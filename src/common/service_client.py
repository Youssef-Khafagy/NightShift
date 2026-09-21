"""Calling another NightShift service.

Internal calls go through the Lambda Invoke API rather than the target's
function URL. Function URLs are how the outside world gets in; they are not
how services should talk to each other.

The reason is not ideology. This project spent a long time on an
intermittent, unexplained 403 from a hand-signed SigV4 request to a function
URL, with identical code working from a laptop, IAM's own policy simulator
reporting "allowed", and both identity and resource policies in place.
Handing the signing to boto3 removes that entire class of problem, along with
about sixty lines of security-sensitive code that nobody should be
maintaining by hand for an internal call.

What is preserved is the shape of the dependency. The payload is a function
URL event, so the callee has one handler and does not care who invoked it,
and a read timeout is still enforced by the caller, so a slow dependency
still surfaces as a timeout at the caller rather than as an unbounded wait.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config

from .context import CORRELATION_HEADER


class ServiceCallError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def _client(timeout: float) -> Any:
    return boto3.client(
        "lambda",
        config=Config(
            read_timeout=timeout,
            connect_timeout=min(timeout, 2.0),
            # No SDK-level retries. A retry here would silently double the
            # caller's latency budget and, for anything not idempotent, do
            # the work twice. Retrying is the caller's decision to make.
            retries={"max_attempts": 0},
        ),
    )


def call(
    function_name: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    correlation_id: str,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Invoke another service and return its decoded JSON body.

    The event is shaped like a function URL request so the callee needs only
    one code path whether it was reached from the internet or from here.
    """
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": {CORRELATION_HEADER: correlation_id},
        "body": json.dumps(payload) if payload is not None else None,
    }

    response = _client(timeout).invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode(),
    )

    raw = response["Payload"].read().decode()

    # An unhandled exception in the callee is reported in the response, not
    # as a failed API call, so it has to be checked explicitly. Without this
    # a crashed dependency looks like a successful call returning nonsense.
    if response.get("FunctionError"):
        raise ServiceCallError(502, raw)

    result = json.loads(raw) if raw else {}
    status = int(result.get("statusCode", 502))
    body = result.get("body") or ""

    if status >= 400:
        raise ServiceCallError(status, body)

    return json.loads(body) if body else {}
