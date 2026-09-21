"""Correlation IDs and logging, shared by every service.

A correlation ID is the thread that ties one customer action to every log
line it produced, across every service it touched. It is the single most
useful thing an on-call engineer can have, and the agent in M5 depends on it
entirely: without it, "what happened to this order" is a search, and with it
it is a filter.

The rule is simple. If the caller sent one, keep it. If not, mint one. Always
put it on the way out, in the response header and in every message published.
"""

from __future__ import annotations

import uuid
from typing import Any

from aws_lambda_powertools import Logger

CORRELATION_HEADER = "x-correlation-id"

# The SQS message attribute carrying the same value. HTTP headers do not
# survive a queue, so the ID has to be re-attached to the message itself,
# otherwise the trail stops at the queue and picks up again as something
# apparently unrelated.
CORRELATION_ATTRIBUTE = "correlationId"


def new_id() -> str:
    return str(uuid.uuid4())


def get_logger(service: str) -> Logger:
    """Powertools Logger, which emits JSON and adds the request ID for free.

    Logger and Metrics only. Powertools Tracer wraps the AWS X-Ray SDK, which
    is unsupported from 2027-02-25, so tracing is handled separately.
    """
    return Logger(service=service)


def correlation_id_from_headers(headers: dict[str, Any] | None, fallback: str) -> str:
    for key, value in (headers or {}).items():
        if key.lower() == CORRELATION_HEADER and value:
            return str(value)
    return fallback


def correlation_id_from_sqs_record(record: dict[str, Any], fallback: str) -> str:
    attributes = record.get("messageAttributes") or {}
    for key, value in attributes.items():
        if key.lower() == CORRELATION_ATTRIBUTE.lower():
            found = value.get("stringValue")
            if found:
                return str(found)
    return fallback
