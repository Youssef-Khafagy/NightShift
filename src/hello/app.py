"""M1 smoke test, now also the shared dependency layer's smoke test.

Two jobs.

The first is the one it has always had: prove the deploy path works, and
carry a correlation ID, because every request in this system needs an ID that
every log line records and the agent can follow across services later.

The second arrived with M2a. The shared layer ships psycopg built for
aarch64 and Amazon Linux, cross-built on an x86 laptop from wheels chosen by
platform tag. Nothing about that is verified until the runtime actually
imports it. This function reports what it can import and what version, so a
wrong wheel shows up here rather than inside the checkout path later.

It also answers a question the layer deliberately left open: the layer does
not bundle boto3 because the runtime provides it, but Aurora DSQL's auth
token methods only exist on a recent enough boto3. Rather than assume, this
reports whether the runtime's boto3 exposes the dsql client at all and
whether that client has the token method DSQL connections need.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
from typing import Any

SERVICE_NAME = os.environ.get("SERVICE_NAME", "hello")

# Lambda's log format is set to JSON on the function itself, so the runtime
# turns each record into a structured log event. No formatter needed here.
logger = logging.getLogger()
logger.setLevel(logging.INFO)

CORRELATION_HEADER = "x-correlation-id"


def _timed(label: str, work: Any, into: dict[str, float]) -> Any:
    """Run work(), record how long it took, and return its result.

    The timings are the point, not decoration. At 128 MB a function gets about
    a twelfth of a vCPU, so imports that are instant on a laptop are not, and
    the first version of this probe hit a 5 second timeout without saying
    which import was responsible.
    """
    started = time.perf_counter()
    try:
        return work()
    finally:
        into[label] = round((time.perf_counter() - started) * 1000, 1)


def _version_of(module_name: str) -> str:
    """Import a module and report its version, or why it could not be imported."""
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - the error is the useful result
        return f"import failed: {type(exc).__name__}: {exc}"
    return str(getattr(module, "__version__", "imported, no __version__"))


def _libpq_implementation() -> str:
    """Force psycopg to bind libpq, which is what actually exercises the wheel.

    Importing psycopg alone does not prove the compiled part loaded. Reading
    psycopg.pq.__impl__ selects and loads an implementation, so a wheel built
    for the wrong architecture fails here rather than at the first query.
    """
    try:
        pq = importlib.import_module("psycopg.pq")
        # __impl__ is the answer that matters. "binary" means the compiled
        # libpq from psycopg-binary loaded. "python" would mean psycopg fell
        # back to a pure-Python implementation, which works but is not what
        # was shipped and would be a silent downgrade.
        return f"{pq.__impl__}, libpq {pq.version()}"
    except Exception as exc:  # noqa: BLE001
        return f"libpq binding failed: {type(exc).__name__}: {exc}"


def _dsql_support() -> dict[str, Any]:
    """Does the runtime's own boto3 know about Aurora DSQL?"""
    try:
        boto3 = importlib.import_module("boto3")
        session = boto3.session.Session()
        available = "dsql" in session.get_available_services()
        if not available:
            return {"dsql_client": False, "token_method": False}
        client = session.client("dsql")
        return {
            "dsql_client": True,
            "token_method": hasattr(client, "generate_db_connect_auth_token"),
            "admin_token_method": hasattr(
                client, "generate_db_connect_admin_auth_token"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def build_layer_report() -> dict[str, Any]:
    ms: dict[str, float] = {}
    report = {
        "aws_lambda_powertools": _timed(
            "powertools_ms", lambda: _version_of("aws_lambda_powertools"), ms
        ),
        "psycopg": _timed("psycopg_ms", lambda: _version_of("psycopg"), ms),
        "psycopg_libpq": _timed("libpq_ms", _libpq_implementation, ms),
        "boto3": _timed("boto3_ms", lambda: _version_of("boto3"), ms),
        "botocore": _version_of("botocore"),
        "dsql": _timed("dsql_client_ms", _dsql_support, ms),
    }
    report["timings_ms"] = ms
    report["imported_during"] = "init"
    return report


# Built once at module import, which is deliberate. Lambda runs module-level
# code during the init phase, and init is given more CPU than the function's
# configured memory would normally buy. Doing this work here rather than on
# the first request is the difference between a slow first request and no
# slow first request.
_LAYER_REPORT = build_layer_report()


def layer_report() -> dict[str, Any]:
    return _LAYER_REPORT


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
    report = layer_report()

    logger.info(
        "handled request",
        extra={
            "service": SERVICE_NAME,
            "correlation_id": correlation_id,
            "function_version": getattr(context, "function_version", "unknown"),
            "layer": report,
        },
    )

    body = {
        "message": "NightShift is awake.",
        "service": SERVICE_NAME,
        "function_version": getattr(context, "function_version", "unknown"),
        "correlation_id": correlation_id,
        "layer": report,
    }

    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            CORRELATION_HEADER: correlation_id,
        },
        "body": json.dumps(body),
    }
