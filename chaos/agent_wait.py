"""Waiting for the agent's verdict during a chaos run, then grading it.

The control plane may read the agent's output (the investigations table);
the agent may never read the control plane. This is the one direction the
integrity rules allow.

The runner finds the investigation through the incident lock: after an
injection, the first alarm opens a lock whose `opened_at` is later than the
injection time. Its `open_id` is the investigation ID. The report item
appears under that ID when the investigation ends.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

TABLE = "nightshift-investigations"
LOCK_KEY = {"investigation_id": {"S": "incident-lock"}, "item": {"S": "current"}}

# The loop stops itself at 840 s; a Lambda retry after a crash can add more.
REPORT_WAIT = 1_000
# After the alarm window, how long an investigation may take to start.
START_WAIT = 120


def opened_since(ddb: Any, since: float) -> str | None:
    lock = ddb.get_item(TableName=TABLE, Key=LOCK_KEY, ConsistentRead=True).get("Item")
    if lock and int(lock["opened_at"]["N"]) >= int(since):
        return lock["open_id"]["S"]
    return None


def fetch(ddb: Any, investigation_id: str, item: str) -> dict | None:
    return ddb.get_item(
        TableName=TABLE,
        Key={"investigation_id": {"S": investigation_id}, "item": {"S": item}},
        ConsistentRead=True,
    ).get("Item")


def wait_for_report(
    ddb: Any,
    since: float,
    *,
    poll: float = 15,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """{"started": False} if no investigation began, else its ID, report,
    postmortem and final state (or "timed_out")."""
    start_deadline = clock() + START_WAIT
    investigation_id = None
    while investigation_id is None:
        investigation_id = opened_since(ddb, since)
        if investigation_id is None:
            if clock() >= start_deadline:
                return {"started": False}
            sleep(poll)
    report_deadline = clock() + REPORT_WAIT
    while True:
        item = fetch(ddb, investigation_id, "report")
        if item:
            checkpoint = fetch(ddb, investigation_id, "checkpoint")
            return {
                "started": True,
                "investigation_id": investigation_id,
                "report": json.loads(item["report"]["S"]),
                "postmortem": item["postmortem"]["S"],
                "state": json.loads(checkpoint["state"]["S"]) if checkpoint else None,
            }
        if clock() >= report_deadline:
            return {
                "started": True,
                "investigation_id": investigation_id,
                "timed_out": True,
            }
        sleep(poll)
