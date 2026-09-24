"""Approval records: how a proposed action waits for a human.

When an investigation ends with allowlisted actions, each becomes one item
in the investigations table:

    investigation_id  <the investigation>
    item              approval#1, approval#2, ...
    action            "rollback_alias service=orders"   (canonical line)
    action_hash       sha256 of investigation, item and action
    status            pending | used | rejected
    created_at, expires_at (15 minutes later)

Creating a record authorises nothing. An action runs only when the owner
invokes the Actor function with the record's key and the hash of the action
they were shown (scripts/approve.py), and the Actor then flips the record
from pending to used in one conditional write that also checks the expiry
and the hash. So an approval is single use, expires, and cannot be
redirected to a different action by editing the record after the owner read
it: the hash would no longer match.

The agent's own role can write this table, so a compromised agent could
create or edit records. What it cannot do is invoke the Actor, which is the
only way anything runs.
"""

from __future__ import annotations

import hashlib
from typing import Any

from botocore.exceptions import ClientError

TABLE = "nightshift-investigations"
TTL_SECONDS = 15 * 60
PREFIX = "approval#"


def action_hash(investigation_id: str, item: str, action: str) -> str:
    return hashlib.sha256(f"{investigation_id}|{item}|{action}".encode()).hexdigest()


def key(investigation_id: str, item: str) -> dict[str, Any]:
    return {"investigation_id": {"S": investigation_id}, "item": {"S": item}}


def create_pending(
    ddb: Any, investigation_id: str, actions: list[str], now: int
) -> list[dict[str, str]]:
    """One pending record per action. Never overwrites an existing record,
    so a resumed investigation cannot reset an approval that was already
    used or rejected."""
    created = []
    for index, action in enumerate(actions, 1):
        item = f"{PREFIX}{index}"
        try:
            ddb.put_item(
                TableName=TABLE,
                Item={
                    **key(investigation_id, item),
                    "action": {"S": action},
                    "action_hash": {"S": action_hash(investigation_id, item, action)},
                    "status": {"S": "pending"},
                    "created_at": {"N": str(now)},
                    "expires_at": {"N": str(now + TTL_SECONDS)},
                },
                ConditionExpression="attribute_not_exists(investigation_id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            continue
        created.append(
            {"investigation_id": investigation_id, "item": item, "action": action}
        )
    return created


def load(ddb: Any, investigation_id: str, item: str) -> dict[str, Any] | None:
    found = ddb.get_item(
        TableName=TABLE, Key=key(investigation_id, item), ConsistentRead=True
    )
    raw = found.get("Item")
    if not raw:
        return None
    return {k: (v.get("S") if "S" in v else int(v["N"])) for k, v in raw.items()}


class ApprovalRefused(Exception):
    """The approval cannot be used: say why, and do nothing."""


def consume(
    ddb: Any, investigation_id: str, item: str, shown_hash: str, approver: str, now: int
) -> str:
    """Flip pending to used, atomically, if and only if it is still pending,
    unexpired, and is the action the approver saw. Returns the action line."""
    record = load(ddb, investigation_id, item)
    if record is None:
        raise ApprovalRefused("no such approval")
    expected = action_hash(investigation_id, item, record["action"])
    if record["action_hash"] != expected:
        raise ApprovalRefused(
            "the record's hash does not match its action; it was edited"
        )
    if shown_hash != expected:
        raise ApprovalRefused(
            "the approved hash is not this action's; it changed after it was shown"
        )
    try:
        ddb.update_item(
            TableName=TABLE,
            Key=key(investigation_id, item),
            UpdateExpression="SET #s = :used, approved_by = :by, approved_at = :now",
            ConditionExpression="#s = :pending AND expires_at > :now AND action_hash = :h",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":used": {"S": "used"},
                ":pending": {"S": "pending"},
                ":by": {"S": approver},
                ":now": {"N": str(now)},
                ":h": {"S": expected},
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        status = record.get("status")
        if status != "pending":
            raise ApprovalRefused(f"already {status}") from None
        raise ApprovalRefused("expired") from None
    return record["action"]


def reject(ddb: Any, investigation_id: str, item: str, by: str, now: int) -> None:
    try:
        ddb.update_item(
            TableName=TABLE,
            Key=key(investigation_id, item),
            UpdateExpression="SET #s = :rejected, rejected_by = :by, rejected_at = :now",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":rejected": {"S": "rejected"},
                ":pending": {"S": "pending"},
                ":by": {"S": by},
                ":now": {"N": str(now)},
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise ApprovalRefused("not pending") from None
