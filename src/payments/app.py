"""payment-provider: a stand-in for a third-party payment API.

Nothing here is a real payment. It exists so that fulfilment has a dependency
outside its own control, which is the only way to rehearse the failures that
matter: a dependency that gets slow, a dependency that starts returning
errors, and a caller that has to decide what to do about it.

Its behaviour comes from configuration rather than from anything in the code,
so changing how it behaves is an ordinary config change to a deployed
function. That is deliberate: a real third-party API gets slow or flaky
without anyone changing our code, and a special switch in application code
would behave nothing like that.

Defaults are a fast, reliable provider: about 40 to 60 ms and no errors.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from typing import Any

from common.context import CORRELATION_HEADER, correlation_id_from_headers, get_logger

SERVICE = "payments"
logger = get_logger(SERVICE)

BASE_LATENCY_MS = int(os.environ.get("PAYMENT_LATENCY_MS", "40"))
LATENCY_JITTER_MS = int(os.environ.get("PAYMENT_LATENCY_JITTER_MS", "20"))

# Fraction of charges that fail, between 0 and 1.
ERROR_RATE = float(os.environ.get("PAYMENT_ERROR_RATE", "0"))
ERROR_STATUS = int(os.environ.get("PAYMENT_ERROR_STATUS", "500"))


def _response(status: int, body: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            CORRELATION_HEADER: correlation_id,
        },
        "body": json.dumps(body),
    }


def charge(order_id: str, amount_cents: int, correlation_id: str) -> dict[str, Any]:
    latency_ms = BASE_LATENCY_MS + random.uniform(0, LATENCY_JITTER_MS)
    time.sleep(latency_ms / 1000)

    if random.random() < ERROR_RATE:
        logger.warning(
            "charge declined by provider",
            extra={"order_id": order_id, "latency_ms": round(latency_ms, 1)},
        )
        raise RuntimeError("provider declined the charge")

    payment_id = str(uuid.uuid4())
    logger.info(
        "charge approved",
        extra={
            "order_id": order_id,
            "payment_id": payment_id,
            "amount_cents": amount_cents,
            "latency_ms": round(latency_ms, 1),
        },
    )
    return {
        "payment_id": payment_id,
        "status": "approved",
        "amount_cents": amount_cents,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", "local")
    correlation_id = correlation_id_from_headers(event.get("headers"), request_id)
    logger.append_keys(correlation_id=correlation_id)

    path = event.get("rawPath", "")
    if not path.endswith("/charge"):
        return _response(404, {"error": "unknown route", "path": path}, correlation_id)

    payload = json.loads(event.get("body") or "{}")
    order_id = payload.get("order_id")
    amount_cents = int(payload.get("amount_cents", 0))
    if not order_id:
        return _response(400, {"error": "order_id is required"}, correlation_id)

    try:
        return _response(
            200, charge(order_id, amount_cents, correlation_id), correlation_id
        )
    except RuntimeError as exc:
        return _response(
            ERROR_STATUS, {"error": str(exc), "order_id": order_id}, correlation_id
        )
