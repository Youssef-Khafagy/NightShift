"""The Actor: runs one owner-approved action, verifies it, audits it.

Invoked only by scripts/approve.py (and, in M8, the dashboard's approve
button), with:

    {"investigation_id": "...", "item": "approval#1",
     "action_hash": "<the hash of the action the owner was shown>",
     "approver": "<who approved>"}

It never talks to a model and trusts nothing that came from one: the action
is re-parsed and re-checked against the allowlist, and the approval must
still be pending, unexpired and exactly what the owner saw. The order:

    lock (one at a time) -> hourly budget -> consume the approval (single
    use) -> check the action fits the saved report -> record before -> act
    -> watch the alarms -> audit -> unlock

Every outcome, including a refusal, is written to the audit log.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import boto3

from actor import executor, guard, verify
from agent import approvals
from agent.actions import misfit, parse_line

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = approvals.TABLE


class Clients:
    def __init__(self) -> None:
        self.ddb = boto3.client("dynamodb")
        self.lam = boto3.client("lambda")
        self.ssm = boto3.client("ssm")
        self.sqs = boto3.client("sqs")
        self.cw = boto3.client("cloudwatch")
        self.deployments = boto3.resource("dynamodb").Table("nightshift-deployments")


CLIENTS = Clients()


def triggering_alarms(ddb: Any, investigation_id: str) -> list[str]:
    item = ddb.get_item(
        TableName=TABLE,
        Key=approvals.key(investigation_id, "checkpoint"),
        ConsistentRead=True,
    ).get("Item")
    if not item:
        return []
    name = json.loads(item["state"]["S"]).get("trigger", {}).get("name")
    return [name] if name else []


def saved_finding(ddb: Any, investigation_id: str) -> dict[str, Any]:
    """The report the approval came from. Without one there is nothing to
    check the action against, so the Actor refuses."""
    item = ddb.get_item(
        TableName=TABLE,
        Key=approvals.key(investigation_id, "report"),
        ConsistentRead=True,
    ).get("Item")
    if not item:
        raise approvals.ApprovalRefused("no saved report to check the action against")
    return json.loads(item["report"]["S"])


def audit(ddb: Any, investigation_id: str, item: str, record: dict[str, Any]) -> None:
    """Append-only: a random suffix and a no-overwrite condition, so two
    records in the same second (a replayed approval, refused, right after the
    real one) can never replace each other."""
    ddb.put_item(
        TableName=TABLE,
        Item={
            **approvals.key(
                investigation_id, f"audit#{item}#{record['at']}#{uuid.uuid4().hex[:8]}"
            ),
            "record": {"S": json.dumps(record, default=str)},
        },
        ConditionExpression="attribute_not_exists(investigation_id)",
    )
    log.info("actor", extra=record)


def handler(
    event: dict[str, Any], context: object, clients: Any = None
) -> dict[str, Any]:
    c = clients or CLIENTS
    investigation_id, item = event["investigation_id"], event["item"]
    approver = str(event.get("approver", "unknown"))[:200]
    approval_ref = f"{investigation_id}/{item}"
    now = int(time.time())
    record: dict[str, Any] = {"at": now, "approval": approval_ref, "approver": approver}

    try:
        guard.acquire(c.ddb, approval_ref, now)
    except guard.Busy as busy:
        record.update(outcome="refused", reason=str(busy))
        audit(c.ddb, investigation_id, item, record)
        return record
    try:
        guard.spend(c.ddb, now)
        line = approvals.consume(
            c.ddb,
            investigation_id,
            item,
            str(event.get("action_hash", "")),
            approver,
            now,
        )
        action = parse_line(line)
        record["action"] = action.line()
        finding = saved_finding(c.ddb, investigation_id)
        reason = misfit(
            action, finding["root_cause_component"], finding["fault_category"]
        )
        if reason:
            raise approvals.ApprovalRefused(
                f"the action does not fit the report: {reason}"
            )
        record.update(executor.run(c, action, approver=approver, approval=approval_ref))
        record["acted_at"] = int(time.time())
        record["verification"] = verify.watch(
            c.cw, triggering_alarms(c.ddb, investigation_id)
        )
        record["outcome"] = "done"
    except (guard.Busy, approvals.ApprovalRefused, ValueError) as refused:
        record.update(outcome="refused", reason=str(refused))
    except executor.ActionFailed as failed:
        record.update(outcome="failed", reason=str(failed))
    finally:
        guard.release(c.ddb, approval_ref)
    record["finished_at"] = int(time.time())
    audit(c.ddb, investigation_id, item, record)
    return record
