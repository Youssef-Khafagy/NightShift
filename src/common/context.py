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
import warnings
from typing import Any

from aws_lambda_powertools import Logger, Metrics

CORRELATION_HEADER = "x-correlation-id"

# The SQS message attribute carrying the same value. HTTP headers do not
# survive a queue, so the ID has to be re-attached to the message itself,
# otherwise the trail stops at the queue and picks up again as something
# apparently unrelated.
CORRELATION_ATTRIBUTE = "correlationId"

# Every custom metric in the project lives in this namespace.
METRICS_NAMESPACE = "NightShift"

# Powertools warns on every invocation that ends without a metric. For these
# services that is normal (a replayed checkout, a deferred payment), so the
# warning would only add log bytes.
warnings.filterwarnings("ignore", message="No application metrics to publish")


def new_id() -> str:
    return str(uuid.uuid4())


def get_logger(service: str) -> Logger:
    """Powertools Logger, which emits JSON and adds the request ID for free.

    Logger and Metrics only. Powertools Tracer wraps the AWS X-Ray SDK, which
    is unsupported from 2027-02-25, so tracing is handled separately.
    """
    return Logger(service=service)


def get_metrics(service: str) -> Metrics:
    """Powertools Metrics, which writes metrics as EMF log lines.

    Each metric costs one of the ten free custom metrics per unique set of
    dimensions, so the rules here are strict and enforced by
    tests/test_metrics.py against the ledger in COST.md:

    - `service` is the only dimension. Never add another; a reason, status or
      ID belongs in a log field, not a dimension.
    - Every metric name must be in the ledger before any code emits it.
    - The cold-start metric stays off. It is one more metric per service, and
      the platform report line already carries the init duration.
    """
    return Metrics(namespace=METRICS_NAMESPACE, service=service)


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
