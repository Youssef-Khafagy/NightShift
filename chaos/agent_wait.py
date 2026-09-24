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


APPROVAL_WAIT = 15 * 60  # an approval expires 15 minutes after the proposal
ACTOR_WAIT = 13 * 60  # the Actor verifies for up to 10 minutes


def wait_for_decisions(
    ddb: Any,
    investigation_id: str,
    count: int,
    *,
    poll: float = 15,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Hold the run while the owner decides on each proposed action. Returns,
    per approval, its final status and the Actor's audit records."""
    items = [f"approval#{n}" for n in range(1, count + 1)]
    deadline = clock() + APPROVAL_WAIT + ACTOR_WAIT
    while True:
        out, settled = [], True
        for item in items:
            raw = fetch(ddb, investigation_id, item) or {}
            status = raw.get("status", {}).get("S", "missing")
            expired = int(raw.get("expires_at", {}).get("N", "0")) < clock()
            audits = [
                json.loads(a["record"]["S"])
                for a in audits_for(ddb, investigation_id, item)
            ]
            done = (
                status == "rejected"
                or (status == "pending" and expired)
                or (status == "used" and any("finished_at" in a for a in audits))
            )
            settled &= done or status == "missing"
            out.append(
                {
                    "item": item,
                    "status": "expired" if status == "pending" and expired else status,
                    "audit": audits,
                }
            )
        if settled or clock() >= deadline:
            return out
        sleep(poll)


def audits_for(ddb: Any, investigation_id: str, item: str) -> list[dict]:
    return ddb.query(
        TableName=TABLE,
        KeyConditionExpression="investigation_id = :i AND begins_with(#t, :a)",
        ExpressionAttributeNames={"#t": "item"},
        ExpressionAttributeValues={
            ":i": {"S": investigation_id},
            ":a": {"S": f"audit#{item}#"},
        },
    )["Items"]
