"""Operational feature flags, read from SSM Parameter Store.

A flag is a switch an operator (or, from M6, the agent) flips during an
incident to change how a service behaves without deploying anything. There are
exactly two, and each service can read only its own:

- `payments_degraded_mode`, read by fulfillment-worker. When true, the worker
  stops calling the payment provider and leaves orders in `placed` for
  `scripts/replay_placed_orders.py` to republish once the provider recovers.
- `checkout_rate_limit`, read by orders-service. Checkouts per second allowed
  in each execution environment; 0 means no limit.

Three decisions shape this module.

**Cached for 30 seconds.** Reading SSM on every request would put a network
call in front of every checkout. Each execution environment keeps what it read
and asks again only after the TTL. The cost is that a change takes up to 30
seconds to reach every environment, and slightly longer in one that was frozen
by Lambda: time does not pass for a frozen environment's code, but the cache
compares against the clock, so a thawed environment sees an expired entry and
refreshes on its first request. The staleness bound is the TTL either way.

**Failing open.** If SSM cannot be read (throttled, down, or permission
removed), the last value that was read is kept, and if there never was one the
caller's default is used. A flag service outage must not become a checkout
outage. The failure is cached for the same TTL so an outage does not turn into
one extra failing call per request, and it is logged every time it happens so
it is visible.

**Short timeouts.** boto3's defaults are a 60 second read timeout with
retries, which would let a slow SSM hold a checkout for minutes. One second
and two attempts is plenty for a lookup this small.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

TTL_SECONDS = 30.0

# child=True shares the service's logger, so keys appended by the handler
# (the correlation ID above all) appear on these lines too.
logger = Logger(child=True)

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


class Flags:
    """A TTL cache in front of ssm:GetParameter.

    The client and clock are parameters so tests can drive time and failures
    directly instead of sleeping or reaching AWS.
    """

    def __init__(
        self,
        client: Any,
        *,
        ttl: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        # name -> (raw value or None, time it was fetched)
        self._cache: dict[str, tuple[str | None, float]] = {}

    def raw(self, name: str) -> str | None:
        """The parameter's value as a string, or None if it cannot be known."""
        now = self._clock()
        cached = self._cache.get(name)
        if cached is not None and now - cached[1] < self._ttl:
            return cached[0]

        try:
            value = self._client.get_parameter(Name=name)["Parameter"]["Value"]
        except (ClientError, BotoCoreError) as exc:
            code = (
                exc.response.get("Error", {}).get("Code", "ClientError")
                if isinstance(exc, ClientError)
                else type(exc).__name__
            )
            # Keep the last good value if there is one; either way, do not ask
            # again until the TTL has passed.
            value = cached[0] if cached is not None else None
            logger.warning(
                "flag read failed, using fallback",
                extra={"flag": name, "error": code, "fallback": value},
            )

        self._cache[name] = (value, now)
        return value

    def get_bool(self, name: str, default: bool) -> bool:
        value = self.raw(name)
        if value is None:
            return default
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        logger.warning(
            "flag value not a boolean, using default",
            extra={"flag": name, "value": value, "default": default},
        )
        return default

    def get_int(self, name: str, default: int, *, minimum: int = 0) -> int:
        value = self.raw(name)
        if value is None:
            return default
        try:
            number = int(value.strip())
        except ValueError:
            number = None
        if number is None or number < minimum:
            logger.warning(
                "flag value not a valid integer, using default",
                extra={"flag": name, "value": value, "default": default},
            )
            return default
        return number


# Built at module scope, during init, like every other client in this project.
_ssm = boto3.client(
    "ssm",
    config=Config(
        connect_timeout=1,
        read_timeout=1,
        # total_max_attempts, not max_attempts: botocore reads max_attempts
        # as the number of retries, so max_attempts=2 means three calls.
        retries={"total_max_attempts": 2, "mode": "standard"},
    ),
)

flags = Flags(_ssm)
