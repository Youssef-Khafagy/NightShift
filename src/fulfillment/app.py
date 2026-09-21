"""fulfillment-worker: charge placed orders and mark them paid.

Reads the placed-orders queue, calls the payment provider, and moves the
order from `placed` to `paid`.

Two things make this more than a loop.

Partial batch failure reporting. Lambda hands the worker up to ten messages
at once. Without `batchItemFailures`, one bad message fails the whole batch
and all ten are redelivered, so nine messages that already succeeded are
processed again. Returning the identifiers of only the failures means SQS
redelivers only those, which is what makes the DLQ's maxReceiveCount mean
what it says.

Idempotency. A message can be delivered more than once, so marking an order
paid is conditional on it still being `placed`. A redelivery updates nothing
and succeeds, rather than charging twice or failing loudly.
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg

from common import dsql, service_client
from common.context import correlation_id_from_sqs_record, get_logger

SERVICE = "fulfillment"
logger = get_logger(SERVICE)

PAYMENTS_FUNCTION_NAME = os.environ["PAYMENTS_FUNCTION_NAME"]
DB_ROLE = os.environ.get("DSQL_ROLE", "fulfillment_service")
PAYMENT_TIMEOUT_SECONDS = float(os.environ.get("PAYMENT_TIMEOUT_SECONDS", "3.0"))


def order_total(conn: psycopg.Connection, order_id: str) -> int | None:
    """Read the order's total, or None if it is gone, or -1 if it is settled.

    One statement on an autocommit connection, so the transaction is over
    before this returns. That matters because the caller then invokes the
    payment provider, which is slow on purpose in one of the chaos scenarios.
    Holding this read open across that call would bill the payment provider's
    latency as DSQL compute time.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT total_cents, status FROM orders WHERE order_id = %s", (order_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    return row[0] if row[1] == "placed" else -1


def mark_paid(conn: psycopg.Connection, order_id: str) -> bool:
    """Returns True if this call is what moved the order to paid.

    No explicit transaction block: this is a single statement on an autocommit
    connection, so it is already atomic and already committed when execute
    returns. Wrapping one statement in `with conn.transaction():` would only
    add round trips and widen the window DSQL bills for.
    """

    def attempt() -> bool:
        with conn.cursor() as cur:
            # The status predicate is what makes redelivery safe. A second
            # delivery matches no rows and changes nothing.
            cur.execute(
                """
                UPDATE orders SET status = 'paid', updated_at = now()
                WHERE order_id = %s AND status = 'placed'
                """,
                (order_id,),
            )
            return cur.rowcount == 1

    def on_retry(attempt_number: int, delay: float, exc: BaseException) -> None:
        logger.warning(
            "serialization conflict, retrying",
            extra={"attempt": attempt_number, "delay_ms": round(delay * 1000, 1)},
        )

    return dsql.retry_on_conflict(attempt, on_retry=on_retry)


def process(record: dict[str, Any]) -> None:
    correlation_id = correlation_id_from_sqs_record(record, record["messageId"])
    logger.append_keys(correlation_id=correlation_id)

    body = json.loads(record["body"])
    order_id = body["order_id"]

    conn = dsql.shared_connection(role=DB_ROLE)
    total = order_total(conn, order_id)

    if total is None:
        # The order does not exist. Retrying will not make it appear, so this
        # is not raised: it would cycle to the DLQ three deliveries later
        # having learned nothing.
        logger.error("order not found", extra={"order_id": order_id})
        return

    if total == -1:
        logger.info("order already settled", extra={"order_id": order_id})
        return

    payment = service_client.call(
        PAYMENTS_FUNCTION_NAME,
        "POST",
        "/charge",
        {"order_id": order_id, "amount_cents": total},
        correlation_id=correlation_id,
        timeout=PAYMENT_TIMEOUT_SECONDS,
    )

    if mark_paid(conn, order_id):
        logger.info(
            "order paid",
            extra={"order_id": order_id, "payment_id": payment.get("payment_id")},
        )
    else:
        logger.info(
            "order already paid by another delivery", extra={"order_id": order_id}
        )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        try:
            process(record)
        except psycopg.OperationalError:
            logger.warning("database connection lost, reconnecting")
            dsql.reset_connection()
            failures.append({"itemIdentifier": record["messageId"]})
        except Exception as exc:
            logger.exception(
                "message failed",
                extra={"message_id": record["messageId"], "reason": str(exc)},
            )
            failures.append({"itemIdentifier": record["messageId"]})

    if failures:
        logger.warning(
            "batch had failures",
            extra={"failed": len(failures), "total": len(event.get("Records", []))},
        )

    # Only these are redelivered. Everything else is deleted from the queue.
    return {"batchItemFailures": failures}
