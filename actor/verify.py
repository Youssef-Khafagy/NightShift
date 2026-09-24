"""Did the action help? Watch the alarms that started the investigation.

Honest outcomes only:
- recovered: every triggering alarm is OK within the window;
- not_recovered: at least one is still in ALARM when the window ends;
- inconclusive: no alarm to watch, or an alarm has no data to judge by.
A recovery that happens for another reason still reads as recovered; the
audit record keeps the timings so a human can tell.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

WINDOW_SECONDS = 600
POLL_SECONDS = 30


def watch(
    cw: Any,
    alarms: list[str],
    *,
    window: int = WINDOW_SECONDS,
    poll: int = POLL_SECONDS,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not alarms:
        return {"outcome": "inconclusive", "reason": "no triggering alarm to watch"}
    start = clock()
    states: dict[str, str] = {}
    while True:
        found = cw.describe_alarms(AlarmNames=alarms)["MetricAlarms"]
        states = {a["AlarmName"]: a["StateValue"] for a in found}
        if states and all(v == "OK" for v in states.values()):
            return {
                "outcome": "recovered",
                "seconds": round(clock() - start),
                "states": states,
            }
        if clock() - start >= window:
            break
        sleep(poll)
    if any(v == "ALARM" for v in states.values()):
        return {
            "outcome": "not_recovered",
            "seconds": round(clock() - start),
            "states": states,
        }
    return {
        "outcome": "inconclusive",
        "reason": "no data to judge by",
        "states": states,
    }
