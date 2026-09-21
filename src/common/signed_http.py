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
import logging
import os
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.httpsession import URLLib3Session

from .context import CORRELATION_HEADER

_session = boto3.Session()
_region = os.environ.get("AWS_REGION", "ca-central-1")


def frozen_credentials():
    """Resolve credentials per request, not once per execution environment.

    This looks like a missed optimisation and is not. Lambda supplies the
    execution role's credentials in environment variables and refreshes those
    variables when the credentials rotate. A Credentials object captured at
    import keeps the values it was built with, so once an execution
    environment lives long enough to see a rotation, every request it signs
    is rejected with AccessDeniedException.

    The symptom is why this was hard to find: alternating success and
    failure, because Lambda spreads requests across several warm environments
    and only the older ones hold stale credentials. Every deploy appeared to
    fix it, because deploying replaces all of them.

    Reading the environment each time costs nothing and is always current.
    The boto3 fallback is for running outside Lambda, where these variables
    are not set and credentials come from a profile.
    """
    return _session.get_credentials().get_frozen_credentials()


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
    # Only sign what is actually sent. A GET carries no body, so it gets no
    # content-type: SigV4 signs the headers it is given, and any header an
    # HTTP client adds, drops or rewrites afterwards invalidates the
    # signature. Sending content-type on a bodyless request is exactly the
    # kind of header a client feels free to tidy up.
    headers = {CORRELATION_HEADER: correlation_id}
    body = None
    if payload is not None:
        body = json.dumps(payload)
        headers["content-type"] = "application/json"

    request = AWSRequest(method=method, url=url, data=body, headers=headers)

    SigV4Auth(frozen_credentials(), SIGNING_SERVICE, _region).add_auth(request)

    prepared = request.prepare()

    if os.environ.get("SIGNED_HTTP_DEBUG") == "1":
        frozen = frozen_credentials()
        logging.getLogger().warning(
            "signed request debug",
            extra={
                "url": url,
                "method": method,
                "region": _region,
                "access_key_prefix": frozen.access_key[:5],
                "has_session_token": bool(frozen.token),
                "header_names": sorted(prepared.headers.keys()),
                "auth_prefix": str(prepared.headers.get("Authorization", ""))[:80],
            },
        )

    session = URLLib3Session(timeout=timeout)
    response = session.send(prepared)
    text = response.text

    if response.status_code >= 400:
        if os.environ.get("SIGNED_HTTP_DEBUG") == "1":
            logging.getLogger().warning(
                "signed request rejected",
                extra={
                    "status": response.status_code,
                    "response_headers": dict(response.headers),
                    "signed_headers": sorted(prepared.headers.keys()),
                    "host": prepared.url,
                },
            )
        raise RemoteCallError(response.status_code, text)

    return json.loads(text) if text else {}


def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
    return request_json("GET", url, None, **kwargs)


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return request_json("POST", url, payload, **kwargs)
