"""One action at a time, at most three an hour, across the account.

Both are conditional writes on single items in the investigations table,
so two approvals arriving together cannot both pass.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

TABLE = "nightshift-investigations"
LOCK = {"investigation_id": {"S": "actor-lock"}, "item": {"S": "current"}}
RATE = {"investigation_id": {"S": "actor-rate"}, "item": {"S": "current"}}
LOCK_SECONDS = 900  # longer than the Actor can run, so a crash cannot wedge it
MAX_PER_HOUR = 3


class Busy(Exception):
    pass


def _conditional(call, *args, **kwargs) -> bool:
    try:
        call(*args, **kwargs)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def acquire(ddb: Any, approval: str, now: int) -> None:
    ok = _conditional(
        ddb.put_item,
        TableName=TABLE,
        Item={
            **LOCK,
            "holder": {"S": approval},
            "expires_at": {"N": str(now + LOCK_SECONDS)},
        },
        ConditionExpression="attribute_not_exists(investigation_id) OR expires_at < :now",
        ExpressionAttributeValues={":now": {"N": str(now)}},
    )
    if not ok:
        raise Busy("another action is running")


def release(ddb: Any, approval: str) -> None:
    """Expire the lock rather than delete it: the Actor holds no delete
    permission anywhere."""
    _conditional(
        ddb.update_item,
        TableName=TABLE,
        Key=LOCK,
        UpdateExpression="SET expires_at = :zero",
        ConditionExpression="holder = :me",
        ExpressionAttributeValues={":me": {"S": approval}, ":zero": {"N": "0"}},
    )


def spend(ddb: Any, now: int) -> None:
    """Count one action against the hourly budget, or refuse."""
    # Inside the current hour and under the limit: add one.
    if _conditional(
        ddb.update_item,
        TableName=TABLE,
        Key=RATE,
        UpdateExpression="ADD used :one",
        ConditionExpression="window_start > :hour_ago AND used < :max",
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":hour_ago": {"N": str(now - 3600)},
            ":max": {"N": str(MAX_PER_HOUR)},
        },
    ):
        return
    # No window yet, or the last one is over: start a new one at 1.
    if _conditional(
        ddb.put_item,
        TableName=TABLE,
        Item={**RATE, "window_start": {"N": str(now)}, "used": {"N": "1"}},
        ConditionExpression="attribute_not_exists(investigation_id) OR window_start <= :hour_ago",
        ExpressionAttributeValues={":hour_ago": {"N": str(now - 3600)}},
    ):
        return
    raise Busy(f"at most {MAX_PER_HOUR} actions an hour")
