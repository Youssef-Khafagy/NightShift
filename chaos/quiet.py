"""The quiet gap between incidents.

Every agent tool looks back at most LOOKBACK_MINUTES (agent/config.py). If
the next incident starts at least QUIET_GAP_MINUTES after the last write of
the previous one, nothing the previous incident left behind (a deploy row,
a CloudTrail event, an alarm change, a metric spike, a log line) can appear
in the next investigation. In M5, two of five wrong answers were read from
exactly those leftovers.

Three independent checks, because each one alone has a hole:

1. **The marker.** The runner writes the time of its last write to
   results/chaos/last_write.json before traffic starts and when a run ends,
   however it ends. Writes the runner did not make are invisible to it.
2. **Lambda invocations.** Zero across the account over the whole gap, read
   with GetMetricStatistics. Catches traffic, smoke tests and deploy checks
   from anywhere, but not configuration changes.
3. **CloudTrail.** No write events on project resources over the whole gap.
   Catches configuration changes from anywhere, but events arrive about
   5 minutes late, which the marker and the 15-minute margin cover.

The margin also covers the time between this check and the injection,
which is always later, so the gap at injection is never shorter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.config import LOOKBACK_MINUTES

QUIET_GAP_MINUTES = LOOKBACK_MINUTES + 15
MARKER = (
    Path(__file__).resolve().parent.parent / "results" / "chaos" / "last_write.json"
)
PROJECT = "nightshift"

_touched = False


def touch(reason: str, path: Path = MARKER, now: datetime | None = None) -> None:
    global _touched
    _touched = True
    path.parent.mkdir(parents=True, exist_ok=True)
    at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    path.write_text(json.dumps({"at": at, "reason": reason}) + "\n")


def touch_if_started(reason: str, path: Path = MARKER) -> None:
    """At the end of a run, however it ended, but only if it got as far as
    writing: a run refused before any write must not restart the clock."""
    if _touched:
        touch(reason, path)


def last_write(path: Path = MARKER) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromisoformat(json.loads(path.read_text())["at"])


def gap_problems(
    now: datetime,
    marker: datetime | None,
    invocations: float,
    changes: list[str],
) -> list[str]:
    gap = timedelta(minutes=QUIET_GAP_MINUTES)
    problems = []
    if marker is not None and now - marker < gap:
        left = gap - (now - marker)
        problems.append(
            f"the last run wrote at {marker:%H:%M:%SZ}; "
            f"{-(-int(left.total_seconds()) // 60)} more minutes of quiet needed"
        )
    if invocations:
        problems.append(
            f"{invocations:.0f} Lambda invocations in the last {QUIET_GAP_MINUTES} minutes"
        )
    if changes:
        problems.append(
            f"{len(changes)} write events on project resources in the last "
            f"{QUIET_GAP_MINUTES} minutes, e.g. {changes[0]}"
        )
    return problems


def invocations_since(cw: Any, start: datetime, end: datetime) -> float:
    points = cw.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Invocations",
        Dimensions=[],
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Sum"],
    )["Datapoints"]
    return sum(p["Sum"] for p in points)


def project_writes_since(trail: Any, start: datetime, end: datetime) -> list[str]:
    kwargs: dict[str, Any] = {
        "LookupAttributes": [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
        "StartTime": start,
        "EndTime": end,
        "MaxResults": 50,
    }
    found = []
    for _ in range(10):
        page = trail.lookup_events(**kwargs)
        for e in page.get("Events", []):
            names = [r.get("ResourceName", "") for r in e.get("Resources", [])]
            if any(PROJECT in n for n in names):
                at = e["EventTime"].astimezone(UTC)
                found.append(f"{e['EventName']} at {at:%H:%M:%SZ}")
        if not page.get("NextToken"):
            break
        kwargs["NextToken"] = page["NextToken"]
    return found


def check(cw: Any, trail: Any, path: Path = MARKER) -> list[str]:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=QUIET_GAP_MINUTES)
    return gap_problems(
        now,
        last_write(path),
        invocations_since(cw, start, now),
        project_writes_since(trail, start, now),
    )
