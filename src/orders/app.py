"""orders-service: checkout.

One request, one database transaction: validate the cart against stock, take
the stock, write the order and its lines, and record the idempotency key.
Either all of that happens or none of it does.

The transaction is retried on SQLSTATE 40001. Aurora DSQL uses optimistic
concurrency control, so two checkouts touching the same inventory row do not
queue behind each other, they both proceed and the loser fails at commit.
Retrying is not an optimisation, it is the contract. Every retry is logged,
because a rising retry count is the earliest visible symptom of hot-row
contention.

Publishing to SQS happens after the commit, deliberately. Publishing inside
the transaction would mean a message for an order that might still roll back.
Publishing after means a commit followed by a crash loses the message, which
is the lesser of the two problems and is recoverable by replaying orders left
in `placed`.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import boto3
import psycopg

from common import dsql, service_client
from common.context import (
    CORRELATION_ATTRIBUTE,
    CORRELATION_HEADER,
    correlation_id_from_headers,
    get_logger,
    get_metrics,
)
from common.flags import flags
from common.ratelimit import TokenBucket

SERVICE = "orders"
logger = get_logger(SERVICE)
# Three metrics, all in the COST.md ledger: CheckoutsPlaced, CheckoutsRejected
# and SerializationRetries.
metrics = get_metrics(SERVICE)

CART_FUNCTION_NAME = os.environ["CART_FUNCTION_NAME"]
PLACED_ORDERS_QUEUE_URL = os.environ["PLACED_ORDERS_QUEUE_URL"]
DB_ROLE = os.environ.get("DSQL_ROLE", "orders_service")
CART_TIMEOUT_SECONDS = float(os.environ.get("CART_TIMEOUT_SECONDS", "2.0"))
# The full SSM parameter name. This function's IAM policy allows reading this
# one parameter and no other.
RATE_LIMIT_PARAMETER = os.environ["CHECKOUT_RATE_LIMIT_PARAMETER"]

IDEMPOTENCY_HEADER = "idempotency-key"
NOTE_MAX_CHARS = 500
UNIQUE_VIOLATION = "23505"

# Built during init, where Lambda gives more CPU than the configured memory
# would otherwise buy.
_sqs = boto3.client("sqs")

# One bucket per execution environment. See common/ratelimit.py for why the
# limit is per environment rather than global.
_bucket = TokenBucket()


def _response(
    status: int,
    body: dict[str, Any],
    correlation_id: str,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            CORRELATION_HEADER: correlation_id,
            **(extra_headers or {}),
        },
        "body": json.dumps(body),
    }


def _header(headers: dict[str, Any] | None, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if key.lower() == name:
            return str(value)
    return None


def fetch_cart(cart_id: str, correlation_id: str) -> list[dict[str, Any]]:
    """Ask cart-service for the cart rather than reading its table directly.

    Reading another service's table would be faster and is a common shortcut.
    It also means the cart's storage can never change without changing this
    service, and it hides the dependency from every trace and service map.
    """
    result = service_client.call(
        CART_FUNCTION_NAME,
        "GET",
        f"/carts/{cart_id}",
        correlation_id=correlation_id,
        timeout=CART_TIMEOUT_SECONDS,
    )
    cart = result.get("cart")
    if not cart:
        raise LookupError(f"cart {cart_id} not found")
    return cart["items"]


def existing_order(conn: psycopg.Connection, idempotency_key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT order_id FROM idempotency_keys WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def place_order(
    conn: psycopg.Connection,
    *,
    order_id: str,
    customer_id: str,
    idempotency_key: str,
    items: list[dict[str, Any]],
) -> int:
    """The whole checkout, in one transaction. Returns the order total.

    The transaction is explicit because the connection runs with autocommit on,
    so without this block each statement below would commit on its own and a
    failure halfway through would leave inventory decremented for an order that
    was never written. Leaving the block commits; raising out of it rolls back.
    """
    product_ids = [i["product_id"] for i in items]

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.product_id, p.price_cents, i.quantity
            FROM products p
            JOIN inventory i ON i.product_id = p.product_id
            WHERE p.product_id = ANY(%s)
            """,
            (product_ids,),
        )
        catalogue = {
            str(r[0]): {"price_cents": r[1], "quantity": r[2]} for r in cur.fetchall()
        }

        missing = [pid for pid in product_ids if pid not in catalogue]
        if missing:
            raise LookupError(f"unknown products: {', '.join(missing)}")

        total = 0
        for item in items:
            pid, wanted = item["product_id"], int(item["quantity"])
            if catalogue[pid]["quantity"] < wanted:
                raise ValueError(
                    f"insufficient stock for {pid}: "
                    f"wanted {wanted}, have {catalogue[pid]['quantity']}"
                )
            total += catalogue[pid]["price_cents"] * wanted

        for item in items:
            pid, wanted = item["product_id"], int(item["quantity"])
            # The quantity check is repeated in the WHERE clause on purpose.
            # The SELECT above ran at the start of the transaction and under
            # Repeatable Read cannot see a concurrent change, so this is what
            # actually prevents overselling.
            cur.execute(
                """
                UPDATE inventory
                SET quantity = quantity - %s, updated_at = now()
                WHERE product_id = %s AND quantity >= %s
                """,
                (wanted, pid, wanted),
            )
            if cur.rowcount != 1:
                raise ValueError(f"insufficient stock for {pid}")

        cur.execute(
            """
            INSERT INTO orders (order_id, customer_id, status, total_cents)
            VALUES (%s, %s, 'placed', %s)
            """,
            (order_id, customer_id, total),
        )

        cur.executemany(
            """
            INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (
                    order_id,
                    i["product_id"],
                    int(i["quantity"]),
                    catalogue[i["product_id"]]["price_cents"],
                )
                for i in items
            ],
        )

        # Last, so that a duplicate request collides here after everything
        # else has been staged, and the whole transaction rolls back.
        cur.execute(
            "INSERT INTO idempotency_keys (idempotency_key, order_id) VALUES (%s, %s)",
            (idempotency_key, order_id),
        )

    return total


def publish_placed(order_id: str, correlation_id: str) -> None:
    _sqs.send_message(
        QueueUrl=PLACED_ORDERS_QUEUE_URL,
        MessageBody=json.dumps({"order_id": order_id}),
        # The correlation ID has to ride on the message. HTTP headers do not
        # survive a queue, and without this the fulfilment half of the story
        # looks like an unrelated event.
        MessageAttributes={
            CORRELATION_ATTRIBUTE: {"DataType": "String", "StringValue": correlation_id}
        },
    )


def checkout(
    payload: dict[str, Any], idempotency_key: str, correlation_id: str
) -> dict[str, Any]:
    cart_id = payload.get("cart_id")
    customer_id = payload.get("customer_id")
    if not cart_id or not customer_id:
        raise ValueError("cart_id and customer_id are required")

    items = fetch_cart(str(cart_id), correlation_id)
    order_id = str(uuid.uuid4())

    def attempt() -> dict[str, Any]:
        conn = dsql.shared_connection(role=DB_ROLE)
        try:
            total = place_order(
                conn,
                order_id=order_id,
                customer_id=str(customer_id),
                idempotency_key=idempotency_key,
                items=items,
            )
            return {"order_id": order_id, "total_cents": total, "replayed": False}
        except psycopg.errors.UniqueViolation:
            # No rollback here. Raising out of place_order's transaction block
            # already rolled it back, and this read is its own transaction that
            # ends the moment it returns. An earlier version rolled back and
            # then left this SELECT open, which cost 315 DPU every time the
            # execution environment froze afterwards.
            replayed = existing_order(conn, idempotency_key)
            logger.info(
                "duplicate checkout", extra={"idempotency_key": idempotency_key}
            )
            return {"order_id": replayed, "replayed": True}

    def on_retry(attempt_number: int, delay: float, exc: BaseException) -> None:
        # The earliest visible symptom of hot-row contention, so it is worth a
        # metric of its own.
        metrics.add_metric(name="SerializationRetries", unit="Count", value=1)
        logger.warning(
            "serialization conflict, retrying",
            extra={
                "attempt": attempt_number,
                "delay_ms": round(delay * 1000, 1),
                "sqlstate": getattr(exc, "sqlstate", None),
            },
        )

    try:
        result = dsql.retry_on_conflict(attempt, on_retry=on_retry)
    except psycopg.OperationalError:
        # A connection kept between invocations can be closed underneath us,
        # by DSQL's 60 minute cap or by the environment being frozen. One
        # reconnect, then give up.
        logger.warning("database connection lost, reconnecting")
        dsql.reset_connection()
        result = dsql.retry_on_conflict(attempt, on_retry=on_retry)

    if not result["replayed"]:
        publish_placed(result["order_id"], correlation_id)

    return result


def _rejected() -> None:
    # One metric for every kind of rejection. Which kind goes in the log
    # line's `reason`, never in a dimension: a dimension per reason would be
    # a billed metric per reason.
    metrics.add_metric(name="CheckoutsRejected", unit="Count", value=1)


@metrics.log_metrics
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", "local")
    headers = event.get("headers")

    correlation_id = correlation_id_from_headers(headers, request_id)
    logger.append_keys(correlation_id=correlation_id)

    # First, before the cart call or the database, so a shed request costs
    # almost nothing. Shedding load is only useful if it is cheap.
    limit = flags.get_int(RATE_LIMIT_PARAMETER, 0)
    if not _bucket.allow(limit):
        _rejected()
        logger.warning(
            "checkout rejected",
            extra={"reason": "rate_limited", "limit_per_environment": limit},
        )
        return _response(
            429,
            {"error": "too many checkouts, retry shortly"},
            correlation_id,
            {"retry-after": "1"},
        )

    idempotency_key = _header(headers, IDEMPOTENCY_HEADER)
    if not idempotency_key:
        _rejected()
        logger.warning("checkout rejected", extra={"reason": "missing_idempotency_key"})
        return _response(
            400,
            {"error": f"{IDEMPOTENCY_HEADER} header is required"},
            correlation_id,
        )

    try:
        payload = json.loads(event.get("body") or "{}")
        result = checkout(payload, idempotency_key, correlation_id)
        if not result["replayed"]:
            # A replayed key returns an order placed earlier, so it is not a
            # new placement.
            metrics.add_metric(name="CheckoutsPlaced", unit="Count", value=1)
        # A free-text note from the customer (a gift message, delivery
        # instructions). Only logged, never acted on. It is also the most
        # realistic way for a stranger's text to reach the on-call agent,
        # which reads these logs, so it is capped and kept a plain string.
        note = payload.get("note")
        extra = {"order_id": result["order_id"], "replayed": result["replayed"]}
        if isinstance(note, str) and note:
            extra["note"] = note[:NOTE_MAX_CHARS]
        logger.info("checkout complete", extra=extra)
        return _response(200 if result["replayed"] else 201, result, correlation_id)

    except LookupError as exc:
        _rejected()
        logger.warning("checkout rejected", extra={"reason": str(exc)})
        return _response(404, {"error": str(exc)}, correlation_id)
    except ValueError as exc:
        _rejected()
        logger.warning("checkout rejected", extra={"reason": str(exc)})
        return _response(409, {"error": str(exc)}, correlation_id)
    except service_client.ServiceCallError as exc:
        # The body matters as much as the status. A 403 from a signature
        # mismatch and a 403 from an IAM denial are the same number and
        # entirely different problems.
        logger.error(
            "cart-service call failed",
            extra={"status": exc.status, "body": exc.body[:300]},
        )
        return _response(502, {"error": "cart-service unavailable"}, correlation_id)
