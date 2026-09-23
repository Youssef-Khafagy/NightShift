"""The shapes every provider speaks, and the one HTTP call they share.

The agent loop only ever sees these dataclasses. Each provider translates
them to and from its own wire format, so the loop cannot depend on one
vendor's quirks.

No SDKs: every provider is one JSON POST over HTTPS with the standard
library, so there is no hidden retry, no hidden logging of prompts, and
nothing to explain beyond this file.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]
USER_AGENT = "nightshift-agent/0.1"


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A call the model asked for.

    `arguments` is the parsed JSON object. If the model sent text that is not
    a JSON object, it is kept under the key "_unparsed" so the loop can tell
    the model its call was malformed instead of crashing.

    `provider_data` is opaque and only echoed back to the provider that made
    the call. Gemini needs this: its models attach a thought signature to a
    function call and reject the next request if it is missing.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    provider_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    """One turn. An assistant turn may carry tool calls; a tool turn answers
    exactly one of them, by `tool_call_id` and `name`."""

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    # Rate-limit headers from the response, kept verbatim for the journal
    # and for checking a provider's published limits against what it sends.
    rate_limit_headers: dict[str, str] = field(default_factory=dict)
    # The provider's reply exactly as parsed from JSON. Not journalled; kept
    # so a live check can save a real reply as a test fixture.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class Provider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_wait: float | None = None,
    ) -> Completion:
        """`max_wait` caps time spent waiting out rate limits in this call,
        so a caller with a deadline is never held past it."""
        ...


class ProviderError(RuntimeError):
    """A call that failed for a reason waiting will not fix, or that kept
    failing after the allowed retries."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class RetryPolicy:
    """How long to wait out 429s and 5xx before giving up.

    `max_total_wait` bounds all waiting in one call, so a daily quota that
    will not reset for hours fails fast instead of holding a Lambda for
    fifteen minutes.
    """

    max_attempts: int = 6
    max_total_wait: float = 120.0
    default_wait: float = 5.0
    sleep: Callable[[float], None] = time.sleep


def retry_after_seconds(headers: dict[str, str], default: float) -> float:
    """Seconds to wait, from a `retry-after` header in seconds. Providers that
    send an HTTP date or nothing get the default."""
    value = headers.get("retry-after", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        return default


def post_json(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    retry: RetryPolicy,
    timeout: float = 60.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[dict[str, Any], dict[str, str]]:
    """POST `body` as JSON. Returns the parsed reply and lower-cased headers.

    429 waits for `retry-after`; 500, 502, 503 and 504 wait with exponential
    backoff. Anything else raises at once, with the provider's error text,
    because a 400 or 401 will not get better by waiting.
    """
    data = json.dumps(body).encode()
    waited = 0.0
    for attempt in range(1, retry.max_attempts + 1):
        request = urllib.request.Request(
            url,
            data=data,
            # A named User-Agent: Groq's Cloudflare front end rejects the
            # default "Python-urllib/3.x" with 403, error code 1010.
            headers={
                "content-type": "application/json",
                "user-agent": USER_AGENT,
                **headers,
            },
            method="POST",
        )
        try:
            with opener(request, timeout=timeout) as response:
                reply_headers = {k.lower(): v for k, v in response.headers.items()}
                return json.loads(response.read()), reply_headers
        except urllib.error.HTTPError as error:
            error_headers = {k.lower(): v for k, v in (error.headers or {}).items()}
            detail = error.read().decode(errors="replace")[:500]
            if error.code == 429:
                wait = retry_after_seconds(error_headers, retry.default_wait)
            elif error.code in (500, 502, 503, 504):
                wait = retry.default_wait * 2 ** (attempt - 1)
            else:
                raise ProviderError(
                    f"HTTP {error.code} from {url}: {detail}", error.code
                ) from None
            if attempt == retry.max_attempts or waited + wait > retry.max_total_wait:
                raise ProviderError(
                    f"HTTP {error.code} from {url} after {attempt} attempts "
                    f"and {waited:.0f}s waiting: {detail}",
                    error.code,
                ) from None
            retry.sleep(wait)
            waited += wait
    raise AssertionError("unreachable")


def bounded(retry: RetryPolicy, max_wait: float | None) -> RetryPolicy:
    if max_wait is None:
        return retry
    return replace(retry, max_total_wait=min(retry.max_total_wait, max(0.0, max_wait)))
