#!/usr/bin/env python3
"""Run a real checkout against the deployed store and fail if it does not work.

Exits non-zero on any failure, so CI can gate on it.

This exists because a change that breaks checkout should never sit on main
unnoticed. `terraform apply` succeeding means the infrastructure matches the
configuration; it says nothing about whether a customer can buy anything. The
gap between those two is where the interesting failures live, and this
milestone produced a long one: an IAM and signing problem that made every
checkout return 502 while every plan and apply stayed green.

Reads the endpoints from Terraform outputs unless CART_SERVICE_URL and
ORDERS_SERVICE_URL are set, so it works the same locally and in CI.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
REGION = os.environ.get("AWS_REGION", "ca-central-1")

# Seeded by scripts/seed_catalogue.py. Fixed IDs so this test does not have to
# discover a product first.
KEYBOARD = "11111111-1111-4111-8111-111111111111"
MOUSE = "22222222-2222-4222-8222-222222222222"
EXPECTED_TOTAL_CENTS = 2 * 12900 + 4900

_lambda = boto3.client("lambda", region_name=REGION)


def function_name(output_name: str, default: str) -> str:
    return os.environ.get(output_name.upper()) or default


def call(target: str, method: str, path: str, payload=None, headers=None):
    """Invoke a service the same way another service would.

    Through the Lambda API rather than the function URL. Requests signed by a
    role were rejected with 403 at the function URL in this account, while the
    identical request signed by an IAM user succeeded, and that was never
    root-caused. Since every internal caller now uses the Lambda API, the
    smoke test does too, which also means it tests the path production
    actually takes.
    """
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers or {},
        "body": json.dumps(payload) if payload is not None else None,
    }
    response = _lambda.invoke(
        FunctionName=target,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode(),
    )
    raw = response["Payload"].read().decode()
    if response.get("FunctionError"):
        return 500, raw
    result = json.loads(raw) if raw else {}
    return int(result.get("statusCode", 500)), result.get("body") or ""


class Failure(Exception):
    pass


def expect(label: str, actual: int, wanted: int, body: str) -> None:
    if actual != wanted:
        raise Failure(f"{label}: expected HTTP {wanted}, got {actual}: {body[:300]}")
    print(f"  ok   {label} -> {actual}")


def main() -> None:
    cart = function_name("cart_function", "nightshift-cart:live")
    orders = function_name("orders_function", "nightshift-orders:live")

    cart_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    correlation_id = f"smoke-{cart_id[:8]}"
    print(f"Smoke checkout, correlation {correlation_id}")

    status, body = call(
        cart,
        "PUT",
        f"/carts/{cart_id}",
        {
            "items": [
                {"product_id": KEYBOARD, "quantity": 2},
                {"product_id": MOUSE, "quantity": 1},
            ]
        },
        {"x-correlation-id": correlation_id},
    )
    expect("store a cart", status, 200, body)

    status, body = call(
        orders,
        "POST",
        "/checkout",
        {"cart_id": cart_id, "customer_id": "smoke-test"},
        {"x-correlation-id": correlation_id, "idempotency-key": idempotency_key},
    )
    expect("checkout", status, 201, body)

    order = json.loads(body)
    if order["total_cents"] != EXPECTED_TOTAL_CENTS:
        raise Failure(
            f"total was {order['total_cents']}, expected {EXPECTED_TOTAL_CENTS}. "
            "Either prices changed or the cart was priced wrongly."
        )
    print(f"  ok   total is {order['total_cents']} cents")

    # The same key must return the original order rather than buying twice.
    status, body = call(
        orders,
        "POST",
        "/checkout",
        {"cart_id": cart_id, "customer_id": "smoke-test"},
        {"x-correlation-id": correlation_id, "idempotency-key": idempotency_key},
    )
    expect("replayed idempotency key", status, 200, body)
    replay = json.loads(body)
    if replay["order_id"] != order["order_id"]:
        raise Failure(
            f"replay returned {replay['order_id']}, expected {order['order_id']}. "
            "Idempotency is not working, which means a retry can double-charge."
        )
    print("  ok   replay returned the original order")

    status, body = call(
        orders,
        "POST",
        "/checkout",
        {"cart_id": cart_id, "customer_id": "smoke-test"},
        {"x-correlation-id": correlation_id},
    )
    expect("checkout without an idempotency key", status, 400, body)

    print(f"\nCheckout works. Order {order['order_id']}.")


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"\nSMOKE TEST FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
