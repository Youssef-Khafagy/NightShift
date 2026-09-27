"""Did an investigation see anything from before its incident?

The quiet gap (chaos/quiet.py) and the lookback cap (agent/config.py) are
meant to make that impossible. This checks it after the fact instead of
assuming it: any full timestamp in a tool result at or before the previous
incident's last write means something leaked, and the run is re-run rather
than graded.

Every tool shows event times as full timestamps (metric points only as a
time of day, inside a window the cap bounds), and state times older than
the lookback as "more than N minutes ago", so a leak shows up as a date.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# 2026-09-23T15:00:00Z, 2026-09-23T14:00:00.000+00:00, and Logs Insights'
# @timestamp, 2026-09-23 15:00:00.000. All of them are UTC.
TIMESTAMP = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")


def leftovers(steps: list[dict[str, Any]], previous_last_write: datetime) -> list[int]:
    """Numbers of the steps whose result shows a time at or before the
    previous incident's last write."""
    found = []
    for step in steps:
        for day, time in TIMESTAMP.findall(step.get("result", "")):
            seen = datetime.fromisoformat(f"{day}T{time}").replace(tzinfo=UTC)
            if seen <= previous_last_write:
                found.append(step["number"])
                break
    return found
