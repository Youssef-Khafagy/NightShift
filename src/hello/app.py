"""M1 smoke-test Lambda.

Deliberately has no dependencies. Powertools arrives in M2 with the store;
here the point is to prove the deploy path, not the application.

The function returns JSON and echoes back a correlation ID, which is the one
idea worth establishing early: every request carries an ID, every log line
records it, and later the agent uses it to follow one request across
services.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

SERVICE_NAME = os.environ.get("SERVICE_NAME", "hello")

# Lambda's log format is set to JSON on the function itself, so the runtime
# turns each record into a structured log event. No formatter needed here.
logger = logging.getLogger()
logger.setLevel(logging.INFO)

CORRELATION_HEADER = "x-correlation-id"


def _correlation_id(event: dict[str, Any], request_id: str) -> str:
    """Use the caller's correlation ID if it sent one, otherwise this request's ID."""
    headers = event.get("headers") or {}
    # Function URL and API Gateway lowercase header names; be defensive anyway.
    for key, value in headers.items():
        if key.lower() == CORRELATION_HEADER and value:
            return str(value)
    return request_id


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", "local")
    correlation_id = _correlation_id(event, request_id)

    logger.info(
        "handled request",
        extra={
            "service": SERVICE_NAME,
            "correlation_id": correlation_id,
            "function_version": getattr(context, "function_version", "unknown"),
        },
    )

    body = {
        "message": "NightShift is awake.",
        "service": SERVICE_NAME,
        "function_version": getattr(context, "function_version", "unknown"),
        "correlation_id": correlation_id,
    }

    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            CORRELATION_HEADER: correlation_id,
        },
        "body": json.dumps(body),
    }
