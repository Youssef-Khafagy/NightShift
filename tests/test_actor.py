"""The Actor and the approvals it consumes, against moto's DynamoDB.

The properties that make acting safe:
- an approval runs at most once, only before it expires, and only for the
  exact action the owner was shown;
- one action at a time, at most three an hour;
- the Actor re-checks the action against the allowlist itself;
- every outcome, refusals included, lands in the audit log.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")

from actor import executor, guard, verify
from agent import approvals
from agent.actions import Action

NOW = 1_800_000_000


@pytest.fixture
def ddb():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ca-central-1")
        client.create_table(
            TableName=approvals.TABLE,
            KeySchema=[
                {"AttributeName": "investigation_id", "KeyType": "HASH"},
                {"AttributeName": "item", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "investigation_id", "AttributeType": "S"},
                {"AttributeName": "item", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def pending(
    ddb, action="set_operational_flag name=checkout_rate_limit value=5", now=NOW
):
    approvals.create_pending(ddb, "inv1", [action], now)
    return approvals.action_hash("inv1", "approval#1", action)


# -- approvals ------------------------------------------------------------------


def test_an_approval_is_used_once(ddb):
    h = pending(ddb)
    assert approvals.consume(
        ddb, "inv1", "approval#1", h, "owner", NOW + 60
    ).startswith("set_")
    with pytest.raises(approvals.ApprovalRefused, match="already used"):
        approvals.consume(ddb, "inv1", "approval#1", h, "owner", NOW + 61)
    assert approvals.load(ddb, "inv1", "approval#1")["approved_by"] == "owner"


def test_an_approval_expires_after_15_minutes(ddb):
    h = pending(ddb)
    with pytest.raises(approvals.ApprovalRefused, match="expired"):
        approvals.consume(
            ddb, "inv1", "approval#1", h, "owner", NOW + approvals.TTL_SECONDS + 1
        )


def test_the_owner_must_approve_what_they_were_shown(ddb):
    pending(ddb)
    shown_for_another_action = approvals.action_hash(
        "inv1", "approval#1", "redrive_dlq"
    )
    with pytest.raises(approvals.ApprovalRefused, match="changed after it was shown"):
        approvals.consume(
            ddb, "inv1", "approval#1", shown_for_another_action, "owner", NOW
        )


def test_editing_the_record_after_it_was_shown_is_caught(ddb):
    h = pending(ddb)
    ddb.update_item(
        TableName=approvals.TABLE,
        Key=approvals.key("inv1", "approval#1"),
        UpdateExpression="SET #a = :x",
        ExpressionAttributeNames={"#a": "action"},
        ExpressionAttributeValues={":x": {"S": "rollback_alias service=cart"}},
    )
    with pytest.raises(approvals.ApprovalRefused, match="edited"):
        approvals.consume(ddb, "inv1", "approval#1", h, "owner", NOW)


def test_a_rejected_approval_cannot_be_used(ddb):
    h = pending(ddb)
    approvals.reject(ddb, "inv1", "approval#1", "owner", NOW)
    with pytest.raises(approvals.ApprovalRefused, match="already rejected"):
        approvals.consume(ddb, "inv1", "approval#1", h, "owner", NOW)


def test_a_resumed_investigation_does_not_reset_an_approval(ddb):
    h = pending(ddb)
    approvals.consume(ddb, "inv1", "approval#1", h, "owner", NOW)
    assert (
        approvals.create_pending(
            ddb, "inv1", ["set_operational_flag name=checkout_rate_limit value=5"], NOW
        )
        == []
    )
    assert approvals.load(ddb, "inv1", "approval#1")["status"] == "used"


# -- guard --------------------------------------------------------------------------


def test_one_action_at_a_time_until_released_or_expired(ddb):
    guard.acquire(ddb, "a", NOW)
    with pytest.raises(guard.Busy):
        guard.acquire(ddb, "b", NOW + 10)
    guard.release(ddb, "b")  # not the holder: no effect
    with pytest.raises(guard.Busy):
        guard.acquire(ddb, "b", NOW + 20)
    guard.release(ddb, "a")
    guard.acquire(ddb, "b", NOW + 30)
    guard.acquire(
        ddb, "c", NOW + 30 + guard.LOCK_SECONDS + 1
    )  # a crashed holder expires


def test_at_most_three_an_hour(ddb):
    for i in range(3):
        guard.spend(ddb, NOW + i)
    with pytest.raises(guard.Busy, match="an hour"):
        guard.spend(ddb, NOW + 100)
    guard.spend(ddb, NOW + 3601)  # a new hour


# -- verification -----------------------------------------------------------------


class Alarms:
    def __init__(self, *states):
        self.states = list(states)

    def describe_alarms(self, AlarmNames):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {
            "MetricAlarms": [{"AlarmName": n, "StateValue": state} for n in AlarmNames]
        }


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_recovered_when_the_alarm_goes_ok():
    c = Clock()
    out = verify.watch(Alarms("ALARM", "ALARM", "OK"), ["a"], clock=c, sleep=c.sleep)
    assert out["outcome"] == "recovered" and out["seconds"] == 60


def test_not_recovered_is_reported_as_such():
    c = Clock()
    out = verify.watch(Alarms("ALARM"), ["a"], window=120, clock=c, sleep=c.sleep)
    assert out["outcome"] == "not_recovered"


def test_no_alarm_or_no_data_is_inconclusive():
    c = Clock()
    assert (
        verify.watch(Alarms("OK"), [], clock=c, sleep=c.sleep)["outcome"]
        == "inconclusive"
    )
    out = verify.watch(
        Alarms("INSUFFICIENT_DATA"), ["a"], window=60, clock=c, sleep=c.sleep
    )
    assert out["outcome"] == "inconclusive"


# -- executor -----------------------------------------------------------------------


class Lam:
    def __init__(self, version="21", mappings=None):
        self.version, self.calls = version, []
        self.mappings = (
            mappings if mappings is not None else [{"UUID": "u1", "State": "Enabled"}]
        )

    def get_alias(self, FunctionName, Name):
        return {"FunctionVersion": self.version}

    def update_alias(self, FunctionName, Name, FunctionVersion):
        self.calls.append(("update_alias", FunctionName, FunctionVersion))
        self.version = FunctionVersion

    def list_event_source_mappings(self, FunctionName):
        return {"EventSourceMappings": self.mappings}

    def update_event_source_mapping(self, UUID, Enabled):
        self.calls.append(("update_mapping", UUID, Enabled))


class Deployments:
    def __init__(self, rows):
        self.rows, self.put = rows, []

    def query(self, **kw):
        return {"Items": self.rows}

    def put_item(self, Item, ConditionExpression):
        self.put.append(Item)


class Clients:
    def __init__(self, lam=None, rows=None):
        self.lam = lam or Lam()
        self.deployments = Deployments(
            rows
            if rows is not None
            else [
                {"service": "orders", "kind": "deploy", "previous": "18", "new": "21"}
            ]
        )


def test_rollback_undoes_the_last_recorded_move_and_records_it():
    c = Clients()
    out = executor.run(
        c,
        Action("rollback_alias", {"service": "orders"}),
        approver="owner",
        approval="inv1/approval#1",
    )
    assert out == {"before": {"version": "21"}, "after": {"version": "18"}}
    row = c.deployments.put[0]
    assert (row["kind"], row["previous"], row["new"], row["actor"]) == (
        "rollback",
        "21",
        "18",
        "nightshift-actor",
    )
    assert "approved by owner" in row["reason"]


def test_rollback_refuses_to_undo_a_rollback():
    c = Clients(
        rows=[{"service": "orders", "kind": "rollback", "previous": "21", "new": "18"}],
        lam=Lam("18"),
    )
    with pytest.raises(executor.ActionFailed, match="already a rollback"):
        executor.run(
            c,
            Action("rollback_alias", {"service": "orders"}),
            approver="o",
            approval="x",
        )
    assert c.lam.calls == []


def test_the_actor_rechecks_the_allowlist_itself():
    with pytest.raises(ValueError, match="must be one of"):
        executor.run(
            Clients(),
            Action("rollback_alias", {"service": "hello"}),
            approver="o",
            approval="x",
        )


def test_pause_refuses_when_already_paused():
    c = Clients(lam=Lam(mappings=[{"UUID": "u1", "State": "Disabled"}]))
    with pytest.raises(executor.ActionFailed, match="already Disabled"):
        executor.run(c, Action("pause_queue_consumer"), approver="o", approval="x")


# -- the handler, end to end ------------------------------------------------------


def test_an_approved_flag_change_runs_once_and_is_audited(ddb, monkeypatch):
    from actor import handler as h

    ssm = boto3.client("ssm", region_name="ca-central-1")
    ssm.put_parameter(
        Name="/nightshift/flags/checkout_rate_limit", Value="0", Type="String"
    )
    monkeypatch.setattr(
        h.verify, "watch", lambda cw, alarms: {"outcome": "recovered", "seconds": 30}
    )
    monkeypatch.setattr(h.time, "time", lambda: NOW + 10)

    class C:
        pass

    c = C()
    c.ddb, c.ssm, c.cw = ddb, ssm, None
    shown = pending(ddb)
    event = {
        "investigation_id": "inv1",
        "item": "approval#1",
        "action_hash": shown,
        "approver": "owner",
    }

    first = h.handler(event, None, clients=c)
    assert first["outcome"] == "done"
    assert first["before"] == {"value": "0"} and first["after"] == {"value": "5"}
    assert (
        ssm.get_parameter(Name="/nightshift/flags/checkout_rate_limit")["Parameter"][
            "Value"
        ]
        == "5"
    )

    replay = h.handler(event, None, clients=c)
    assert replay["outcome"] == "refused" and "already used" in replay["reason"]

    audits = ddb.query(
        TableName=approvals.TABLE,
        KeyConditionExpression="investigation_id = :i AND begins_with(#t, :a)",
        ExpressionAttributeNames={"#t": "item"},
        ExpressionAttributeValues={
            ":i": {"S": "inv1"},
            ":a": {"S": "audit#approval#1#"},
        },
    )["Items"]
    outcomes = sorted(json.loads(a["record"]["S"])["outcome"] for a in audits)
    # Both kept, although they happened in the same second: the refused
    # replay must not overwrite the record of the action that ran.
    assert outcomes == ["done", "refused"]
    # The lock was released both times.
    guard.acquire(ddb, "next", NOW + 20)
