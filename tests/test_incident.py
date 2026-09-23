"""Incident correlation: one investigation per incident, safe under retries.

The fake table evaluates the lock's condition the way DynamoDB would, so the
tests exercise the real decision: lead, join, expire, or resume.
"""

from __future__ import annotations

import sys
from pathlib import Path

from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.incident import (
    WINDOW_SECONDS,
    investigation_id_for,
    open_or_join,
    trigger_from_event,
)


class LockTable:
    def __init__(self) -> None:
        self.lock: dict | None = None

    def put_item(self, TableName, Item, ConditionExpression, ExpressionAttributeValues):
        now = int(ExpressionAttributeValues[":now"]["N"])
        mine = ExpressionAttributeValues[":id"]["S"]
        ok = (
            self.lock is None
            or int(self.lock["expires_at"]["N"]) < now
            or self.lock["open_id"]["S"] == mine
        )
        if not ok:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
            )
        self.lock = {k: v for k, v in Item.items()}

    def update_item(
        self, TableName, Key, UpdateExpression, ExpressionAttributeValues, ReturnValues
    ):
        assert UpdateExpression == "ADD alarms :a"
        alarms = set(self.lock["alarms"]["SS"]) | set(
            ExpressionAttributeValues[":a"]["SS"]
        )
        self.lock["alarms"] = {"SS": sorted(alarms)}
        return {"Attributes": self.lock}


def test_first_alarm_leads_and_related_alarms_join():
    t = LockTable()
    assert open_or_join(t, "x", alarm="orders-errors", event_id="e1", now=1000) == (
        "lead",
        investigation_id_for("e1"),
    )
    assert open_or_join(t, "x", alarm="checkout-latency", event_id="e2", now=1060) == (
        "joined",
        investigation_id_for("e1"),
    )
    assert (
        open_or_join(t, "x", alarm="queue-age", event_id="e3", now=1100)[0] == "joined"
    )
    assert t.lock["alarms"]["SS"] == ["checkout-latency", "orders-errors", "queue-age"]


def test_an_alarm_after_the_window_starts_a_new_investigation():
    t = LockTable()
    open_or_join(t, "x", alarm="orders-errors", event_id="e1", now=1000)
    role, inv = open_or_join(
        t, "x", alarm="cart-errors", event_id="e9", now=1000 + WINDOW_SECONDS + 1
    )
    assert (role, inv) == ("lead", investigation_id_for("e9"))


def test_a_redelivered_event_resumes_its_own_investigation():
    t = LockTable()
    first = open_or_join(t, "x", alarm="orders-errors", event_id="e1", now=1000)
    again = open_or_join(t, "x", alarm="orders-errors", event_id="e1", now=1300)
    assert first == again == ("lead", investigation_id_for("e1"))


def test_joining_twice_does_not_duplicate_the_alarm():
    t = LockTable()
    open_or_join(t, "x", alarm="orders-errors", event_id="e1", now=1000)
    open_or_join(t, "x", alarm="queue-age", event_id="e2", now=1010)
    open_or_join(t, "x", alarm="queue-age", event_id="e2", now=1020)
    assert t.lock["alarms"]["SS"] == ["orders-errors", "queue-age"]


def test_other_dynamodb_errors_are_not_swallowed():
    class Broken(LockTable):
        def put_item(self, **kw):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem"
            )

    try:
        open_or_join(Broken(), "x", alarm="a", event_id="e", now=1)
    except ClientError as error:
        assert (
            error.response["Error"]["Code"] == "ProvisionedThroughputExceededException"
        )
    else:
        raise AssertionError("expected the error to propagate")


def test_the_eventbridge_event_becomes_a_trigger():
    event = {
        "id": "e1",
        "detail": {
            "alarmName": "nightshift-orders-errors",
            "state": {
                "value": "ALARM",
                "reason": "Threshold Crossed",
                "timestamp": "2026-09-23T14:39:00.000+0000",
            },
            "previousState": {"value": "OK"},
        },
    }
    assert trigger_from_event(event) == {
        "name": "nightshift-orders-errors",
        "state": "ALARM",
        "reason": "Threshold Crossed",
        "since": "2026-09-23T14:39:00.000+0000",
        "previous_state": "OK",
    }
