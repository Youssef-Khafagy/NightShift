"""Moving live aliases and recording every move. Shared by deploy and rollback.

Every function is invoked through its `live` alias, so what customers get is
decided by one pointer per function. Terraform publishes versions but never
moves that pointer (see `ignore_changes` in the lambda_service module). These
helpers do, and every move writes one row to the deployments table:

    service       orders
    deployed_at   2026-09-23T14:02:11.305+00:00   (sort key; string order is time order)
    previous      14
    new           15
    kind          deploy | rollback | auto-rollback
    git_sha       the commit the new version was built from, when known
    actor         who or what moved it
    reason        why, for rollbacks

The table is what the on-call agent reads to answer "what changed just
before this started", so a move that is not recorded is a move the agent
cannot see.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

PROJECT = "nightshift"
TABLE = f"{PROJECT}-deployments"
ALIAS = "live"

# Leaves first: a service is moved before anything that calls it, so during
# a deploy a new caller never talks to an old dependency for longer than it
# has to. cart and payments call nothing; orders calls cart; fulfillment calls
# payments; hello is independent.
SERVICES = ("cart", "payments", "orders", "fulfillment", "hello")


def function_name(service: str) -> str:
    return f"{PROJECT}-{service}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def current_version(lambda_client: Any, service: str) -> str:
    return lambda_client.get_alias(FunctionName=function_name(service), Name=ALIAS)[
        "FunctionVersion"
    ]


def move_alias(lambda_client: Any, service: str, version: str) -> None:
    lambda_client.update_alias(
        FunctionName=function_name(service), Name=ALIAS, FunctionVersion=version
    )


def record(
    table: Any,
    *,
    service: str,
    previous: str,
    new: str,
    kind: str,
    actor: str,
    git_sha: str | None = None,
    reason: str | None = None,
    at: str | None = None,
) -> dict[str, str]:
    """Write one row. Refuses to overwrite a row with the same key."""
    item = {
        "service": service,
        "deployed_at": at or now_iso(),
        "previous": previous,
        "new": new,
        "kind": kind,
        "actor": actor,
    }
    if git_sha:
        item["git_sha"] = git_sha
    if reason:
        item["reason"] = reason
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(deployed_at)")
    return item


def history(table: Any, service: str, limit: int = 10) -> list[dict[str, Any]]:
    """The most recent rows for one service, newest first."""
    return table.query(
        KeyConditionExpression="service = :s",
        ExpressionAttributeValues={":s": service},
        ScanIndexForward=False,
        Limit=limit,
    )["Items"]
