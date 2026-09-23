"""cart-service: the shopping cart, stored in DynamoDB.

A cart is one document, read and written whole by its key, which is what
DynamoDB is cheapest and simplest at. Its failure modes look nothing like a
SQL database's: throttling rather than lock contention, and a missing table
rather than a failing query.

Routes, over a function URL with AWS_IAM auth:

    PUT  /carts/{cart_id}   replace the cart's items
    GET  /carts/{cart_id}   read it back
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from typing import Any

import boto3

from common.context import CORRELATION_HEADER, correlation_id_from_headers, get_logger

SERVICE = "cart"
logger = get_logger(SERVICE)

TABLE_NAME = os.environ["CART_TABLE_NAME"]
CART_TTL_SECONDS = int(os.environ.get("CART_TTL_SECONDS", "86400"))

# Built at import, during the init phase. Measured in step 3: the same client
# construction costs 1,661 ms inside the handler and 158 ms here.
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _json_default(value: Any) -> Any:
    """DynamoDB returns every number as a Decimal, which json cannot encode.

    Quantities are whole numbers, so they come back as ints. Anything else
    falls back to float rather than raising, because a serialisation error in
    a response is a worse failure than a slightly lossy number.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def _response(status: int, body: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            CORRELATION_HEADER: correlation_id,
        },
        "body": json.dumps(body, default=_json_default),
    }


def _cart_id_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) == 2 and parts[0] == "carts":
        return parts[1]
    return None


def put_cart(cart_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")

    for item in items:
        if not item.get("product_id") or int(item.get("quantity", 0)) < 1:
            raise ValueError(
                "each item needs a product_id and a quantity of at least 1"
            )

    record = {
        "cart_id": cart_id,
        "items": [
            {"product_id": str(i["product_id"]), "quantity": int(i["quantity"])}
            for i in items
        ],
        # DynamoDB TTL wants epoch seconds. Expired carts are deleted by
        # DynamoDB itself, which costs no write capacity.
        "expires_at": int(time.time()) + CART_TTL_SECONDS,
    }
    _table.put_item(Item=record)
    return record


def get_cart(cart_id: str) -> dict[str, Any] | None:
    result = _table.get_item(Key={"cart_id": cart_id})
    return result.get("Item")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", "local")
    correlation_id = correlation_id_from_headers(event.get("headers"), request_id)
    logger.append_keys(correlation_id=correlation_id)

    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")
    path = event.get("rawPath", "")
    cart_id = _cart_id_from_path(path)

    if not cart_id:
        return _response(404, {"error": "unknown route", "path": path}, correlation_id)

    try:
        if method == "PUT":
            payload = json.loads(event.get("body") or "{}")
            record = put_cart(cart_id, payload)
            logger.info(
                "cart stored", extra={"cart_id": cart_id, "items": len(record["items"])}
            )
            return _response(200, {"cart": record}, correlation_id)

        if method == "GET":
            record = get_cart(cart_id)
            if record is None:
                logger.info("cart not found", extra={"cart_id": cart_id})
                return _response(404, {"error": "cart not found"}, correlation_id)
            logger.info("cart read", extra={"cart_id": cart_id})
            return _response(200, {"cart": record}, correlation_id)

        return _response(405, {"error": f"method {method} not allowed"}, correlation_id)

    except ValueError as exc:
        logger.warning("bad request", extra={"cart_id": cart_id, "reason": str(exc)})
        return _response(400, {"error": str(exc)}, correlation_id)
