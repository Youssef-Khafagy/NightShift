"""One investigation per incident, however many alarms it sets off.

A bad deploy of orders can fire orders-errors, checkout-latency and
queue-age within a minute or two. Three investigations would triple the
token bill and give three answers to one question. So the first alarm opens
an incident window, and alarms inside it join that investigation instead of
starting their own.

The mechanism is one DynamoDB item and a conditional write, which is atomic:
of two alarms arriving together, exactly one succeeds in taking the lock.

    PutItem lock IF (no lock) OR (lock expired) OR (lock is mine)
        succeeded: this alarm leads a new investigation
        failed:    ADD this alarm's name to the open lock; join it

"Lock is mine" makes retries safe: EventBridge can deliver an event twice,
and Lambda retries an invocation that timed out. The investigation ID comes
from the event ID, so a redelivered event finds its own lock and resumes
its own investigation from the checkpoint instead of starting another.
"""

from __future__ import annotations

import hashlib
from typing import Any

from botocore.exceptions import ClientError

WINDOW_SECONDS = 600
LOCK_KEY = {"investigation_id": {"S": "incident-lock"}, "item": {"S": "current"}}


def investigation_id_for(event_id: str) -> str:
    return hashlib.sha256(event_id.encode()).hexdigest()[:12]


def open_or_join(
    ddb: Any, table: str, *, alarm: str, event_id: str, now: int
) -> tuple[str, str]:
    """("lead", id) if this alarm starts (or resumes) an investigation,
    ("joined", id) if it belongs to one already open."""
    new_id = investigation_id_for(event_id)
    try:
        ddb.put_item(
            TableName=table,
            Item={
                **LOCK_KEY,
                "open_id": {"S": new_id},
                "opened_at": {"N": str(now)},
                "expires_at": {"N": str(now + WINDOW_SECONDS)},
                "alarms": {"SS": [alarm]},
            },
            ConditionExpression=(
                "attribute_not_exists(investigation_id) OR expires_at < :now OR open_id = :id"
            ),
            ExpressionAttributeValues={":now": {"N": str(now)}, ":id": {"S": new_id}},
        )
        return "lead", new_id
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    joined = ddb.update_item(
        TableName=table,
        Key=LOCK_KEY,
        UpdateExpression="ADD alarms :a",
        ExpressionAttributeValues={":a": {"SS": [alarm]}},
        ReturnValues="ALL_NEW",
    )["Attributes"]
    return "joined", joined["open_id"]["S"]


def trigger_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """The alarm, as EventBridge delivers it, in the shape get_alarm returns."""
    detail = event["detail"]
    state = detail.get("state", {})
    return {
        "name": detail.get("alarmName"),
        "state": state.get("value"),
        "reason": state.get("reason"),
        "since": state.get("timestamp"),
        "previous_state": detail.get("previousState", {}).get("value"),
    }
