"""The chaos runner's wait for the agent: find the investigation an
injection started, wait for its report, give up on time."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from chaos import agent_wait


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Table:
    """Items appear at given clock times, like a Lambda writing them."""

    def __init__(
        self, clock: Clock, items: dict[tuple[str, str], tuple[float, dict]]
    ) -> None:
        self.clock, self.items = clock, items

    def get_item(self, TableName, Key, ConsistentRead=False):
        key = (Key["investigation_id"]["S"], Key["item"]["S"])
        at, item = self.items.get(key, (None, None))
        return {"Item": item} if at is not None and self.clock.now >= at else {}


def lock(open_id: str, opened_at: int) -> dict:
    return {"open_id": {"S": open_id}, "opened_at": {"N": str(opened_at)}}


def wait(table: Table, clock: Clock, since: float) -> dict:
    return agent_wait.wait_for_report(
        table, since, poll=15, clock=clock, sleep=clock.sleep
    )


def test_finds_the_investigation_opened_after_injection_and_waits_for_its_report():
    clock = Clock()
    state = {"steps": [{"at": "t"}], "calls": []}
    table = Table(
        clock,
        {
            ("incident-lock", "current"): (1_030, lock("inv1", 1_020)),
            ("inv1", "report"): (
                1_300,
                {
                    "report": {"S": json.dumps({"fault_category": "bad_deploy"})},
                    "postmortem": {"S": "# pm"},
                },
            ),
            ("inv1", "checkpoint"): (1_000, {"state": {"S": json.dumps(state)}}),
        },
    )
    found = wait(table, clock, since=1_000)
    assert found["investigation_id"] == "inv1"
    assert found["report"] == {"fault_category": "bad_deploy"}
    assert found["state"] == state and clock.now >= 1_300


def test_an_older_lock_is_not_this_runs_investigation():
    clock = Clock()
    table = Table(clock, {("incident-lock", "current"): (0, lock("old", 500))})
    assert wait(table, clock, since=1_000) == {"started": False}
    assert clock.now >= 1_000 + agent_wait.START_WAIT


def test_a_report_that_never_comes_times_out():
    clock = Clock()
    table = Table(clock, {("incident-lock", "current"): (0, lock("inv2", 1_000))})
    found = wait(table, clock, since=1_000)
    assert found == {"started": True, "investigation_id": "inv2", "timed_out": True}
    assert clock.now >= 1_000 + agent_wait.REPORT_WAIT


class DecisionTable(Table):
    def query(
        self,
        TableName,
        KeyConditionExpression,
        ExpressionAttributeNames,
        ExpressionAttributeValues,
    ):
        prefix = ExpressionAttributeValues[":a"]["S"]
        inv = ExpressionAttributeValues[":i"]["S"]
        return {
            "Items": [
                item
                for (i, name), (at, item) in self.items.items()
                if i == inv and name.startswith(prefix) and self.clock.now >= at
            ]
        }


def approval(status: str, expires_at: int) -> dict:
    return {"status": {"S": status}, "expires_at": {"N": str(expires_at)}}


def test_the_hold_ends_when_the_actor_has_finished():
    clock = Clock()
    table = DecisionTable(
        clock,
        {
            ("inv", "approval#1"): (0, approval("pending", 1_900)),
            ("inv", "audit#approval#1#1200#ab"): (
                1_400,
                {
                    "record": {
                        "S": json.dumps({"outcome": "done", "finished_at": 1_400})
                    }
                },
            ),
        },
    )
    # The owner approves at 1,200: status flips, then the audit lands at 1,400.
    table.items[("inv", "approval#1")] = (0, approval("pending", 1_900))

    def approve_later(seconds):
        clock.sleep(seconds)
        if clock.now >= 1_200:
            table.items[("inv", "approval#1")] = (0, approval("used", 1_900))

    out = agent_wait.wait_for_decisions(
        table, "inv", 1, poll=15, clock=clock, sleep=approve_later
    )
    assert out[0]["status"] == "used" and out[0]["audit"][0]["outcome"] == "done"
    assert clock.now >= 1_400


def test_an_unanswered_approval_ends_the_hold_when_it_expires():
    clock = Clock()
    table = DecisionTable(
        clock, {("inv", "approval#1"): (0, approval("pending", 1_300))}
    )
    out = agent_wait.wait_for_decisions(
        table, "inv", 1, poll=15, clock=clock, sleep=clock.sleep
    )
    assert out == [{"item": "approval#1", "status": "expired", "audit": []}]
    assert 1_300 <= clock.now < 1_400
