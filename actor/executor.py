"""Running one approved action, and undoing nothing by itself.

Each action records the state it changes before changing it, so the audit
log says exactly how to undo it by hand. Names are fixed here rather than
read from anywhere the model could influence; the IAM policy allows exactly
these resources and nothing else.
"""

from __future__ import annotations

from typing import Any

from agent.actions import Action, check
from ops import deployments

PROJECT = "nightshift"
FLAG_PREFIX = f"/{PROJECT}/flags/"
# Alias-qualified: the trigger is attached to fulfillment:live, and listing
# by the bare function name returns nothing.
CONSUMER = f"{PROJECT}-fulfillment:live"
QUEUE = f"{PROJECT}-placed-orders"
DLQ = f"{QUEUE}-dlq"
REASON = "rolled back by the on-call agent, approved by {approver} ({approval})"


class ActionFailed(Exception):
    """The action could not be carried out safely; nothing more was done."""


def run(
    clients: Any, action: Action, *, approver: str, approval: str
) -> dict[str, Any]:
    """Returns {"before": ..., "after": ...}. Re-checks the action first:
    the Actor trusts nothing that came from the model."""
    check(action)
    handlers = {
        "rollback_alias": rollback_alias,
        "set_operational_flag": set_flag,
        "pause_queue_consumer": lambda c, a, **k: consumer(c, enabled=False),
        "resume_queue_consumer": lambda c, a, **k: consumer(c, enabled=True),
        "redrive_dlq": lambda c, a, **k: redrive(c),
    }
    return handlers[action.name](clients, action, approver=approver, approval=approval)


def rollback_alias(
    clients: Any, action: Action, *, approver: str, approval: str
) -> dict:
    """The same rules as scripts/rollback.py: undo the last recorded move,
    and refuse when that would mean guessing."""
    service = action.params["service"]
    current = deployments.current_version(clients.lam, service)
    rows = deployments.history(clients.deployments, service, limit=1)
    try:
        target = deployments.choose_target(current, rows, None)
    except deployments.Refused as refused:
        raise ActionFailed(str(refused)) from None
    deployments.move_alias(clients.lam, service, target)
    deployments.record(
        clients.deployments,
        service=service,
        previous=current,
        new=target,
        kind="rollback",
        actor="nightshift-actor",
        reason=REASON.format(approver=approver, approval=approval),
    )
    return {"before": {"version": current}, "after": {"version": target}}


def set_flag(clients: Any, action: Action, **_: Any) -> dict:
    name = FLAG_PREFIX + action.params["name"]
    before = clients.ssm.get_parameter(Name=name)["Parameter"]["Value"]
    clients.ssm.put_parameter(
        Name=name, Value=action.params["value"], Type="String", Overwrite=True
    )
    return {"before": {"value": before}, "after": {"value": action.params["value"]}}


def mapping_uuid(clients: Any) -> tuple[str, str]:
    mappings = clients.lam.list_event_source_mappings(FunctionName=CONSUMER)[
        "EventSourceMappings"
    ]
    if len(mappings) != 1:
        raise ActionFailed(
            f"expected one queue trigger on {CONSUMER}, found {len(mappings)}"
        )
    return mappings[0]["UUID"], mappings[0]["State"]


def consumer(clients: Any, *, enabled: bool) -> dict:
    uuid, state = mapping_uuid(clients)
    wanted = "Enabled" if enabled else "Disabled"
    if state == wanted:
        raise ActionFailed(f"the queue trigger is already {state}")
    clients.lam.update_event_source_mapping(UUID=uuid, Enabled=enabled)
    return {"before": {"state": state}, "after": {"state": wanted}}


def redrive(clients: Any) -> dict:
    dlq_url = clients.sqs.get_queue_url(QueueName=DLQ)["QueueUrl"]
    waiting = int(
        clients.sqs.get_queue_attributes(
            QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages", "QueueArn"]
        )["Attributes"]["ApproximateNumberOfMessages"]
    )
    if waiting == 0:
        raise ActionFailed("the dead-letter queue is empty")
    arn = clients.sqs.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    # No destination: SQS moves the messages back to the queue they came from.
    task = clients.sqs.start_message_move_task(SourceArn=arn)["TaskHandle"]
    return {"before": {"dlq_messages": waiting}, "after": {"move_task": task}}
