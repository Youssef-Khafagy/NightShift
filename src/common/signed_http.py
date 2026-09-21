"""Calling a Lambda function URL that requires AWS_IAM authentication.

A function URL with AWS_IAM auth rejects anything that is not SigV4-signed,
which is what keeps these endpoints from being open to the internet. Signing
by hand is a few lines with botocore, and botocore is already in the runtime,
so no dependency is added for this.

Why HTTP at all, rather than lambda:InvokeFunction through boto3: the payment
provider is standing in for a third-party API, and the failures worth
rehearsing later are HTTP failures. A read timeout, a 500, a slow response.
Invoking through the SDK would hide all of that behind a different error
surface.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.httpsession import URLLib3Session

from .context import CORRELATION_HEADER

# Built once per execution environment, during init, where the CPU is.
_session = boto3.Session()
_credentials = _session.get_credentials()
_region = os.environ.get("AWS_REGION", "ca-central-1")

# A function URL is signed as the "lambda" service.
SIGNING_SERVICE = "lambda"


class RemoteCallError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    correlation_id: str,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Call an IAM-authenticated function URL and return the decoded response.

    timeout is deliberately a parameter with a short default. An unbounded
    call to a dependency is how one slow service becomes every service's
    problem, and a timeout shorter than the caller's own is what keeps a
    slow dependency visible as a timeout rather than as a caller that hangs.
    """
    body = json.dumps(payload) if payload is not None else ""
    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={
            "content-type": "application/json",
            CORRELATION_HEADER: correlation_id,
        },
    )

    # get_frozen_credentials() each call, because the execution role's
    # credentials rotate and a cached copy would eventually be rejected.
    SigV4Auth(_credentials.get_frozen_credentials(), SIGNING_SERVICE, _region).add_auth(
        request
    )

    session = URLLib3Session(timeout=timeout)
    response = session.send(request.prepare())
    text = response.text

    if response.status_code >= 400:
        raise RemoteCallError(response.status_code, text)

    return json.loads(text) if text else {}


def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
    return request_json("GET", url, None, **kwargs)


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return request_json("POST", url, payload, **kwargs)
