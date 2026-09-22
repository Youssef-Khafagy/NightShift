"""A token bucket, the rate limiter behind the `checkout_rate_limit` flag.

The bucket holds up to `rate` tokens and refills at `rate` tokens per second.
Each request takes one; a request that finds the bucket empty is rejected.
That allows a short burst (a full bucket's worth) and then holds the average
at `rate` per second.

This limit is per execution environment, not global. Each Lambda environment
has its own bucket and none of them talk to each other, so the real ceiling is
`rate` times the number of environments running. orders-service has reserved
concurrency 2, so a flag value of N caps checkout at 2N per second. That is
load shedding, not an exact quota. An exact global limit needs shared state,
such as a DynamoDB counter, which would cost write capacity and add a new way
for every checkout to fail. The approximation is good enough for the job this
flag has: taking pressure off a struggling dependency during an incident.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._rate = 0.0
        self._tokens = 0.0
        self._updated = clock()

    def allow(self, rate: float) -> bool:
        """Take a token if one is available at this rate. rate <= 0 means off.

        The rate is passed on every call rather than fixed at construction,
        because it comes from a flag that can change at any moment. A changed
        rate takes effect immediately, keeping the tokens already accumulated
        but never more than the new capacity.
        """
        now = self._clock()
        if rate <= 0:
            self._rate = 0.0
            self._updated = now
            return True

        if rate != self._rate:
            if self._rate == 0:
                # Switching the limit on starts with a full bucket, so
                # enabling it never rejects the very next request.
                self._tokens = rate
            self._rate = rate

        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._updated = now

        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False
