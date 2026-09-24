#!/usr/bin/env python3
"""Approve or reject an action the agent proposed. The only way one runs.

    python scripts/approve.py list                     # pending approvals
    python scripts/approve.py show   <investigation> <n>
    python scripts/approve.py approve <investigation> <n>
    python scripts/approve.py reject  <investigation> <n>
    python scripts/approve.py status <investigation> <n>

Authenticated by your own AWS login (`aws login --profile nightshift-admin`,
MFA). Approving shows the exact action, asks you to type `approve`, then
invokes the Actor with the hash of the action you were shown: if the record
changed after you read it, the Actor refuses. An approval can be used once,
and only within 15 minutes of the proposal. Approving is always a deliberate
write from here; there is no link to click.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import approvals

REGION = "ca-central-1"
ACTOR = "nightshift-actor:live"


def item_name(n: str) -> str:
    return n if n.startswith(approvals.PREFIX) else f"{approvals.PREFIX}{n}"


def report_line(ddb, investigation_id: str) -> str:
    raw = ddb.get_item(
        TableName=approvals.TABLE, Key=approvals.key(investigation_id, "report")
    ).get("Item")
    if not raw:
        return "(no report)"
    r = json.loads(raw["report"]["S"])
    return (
        f"{r['root_cause_component']} / {r['fault_category']}, confidence "
        f"{r['confidence']}: {r['summary']}"
    )


def describe(record: dict, now: int) -> str:
    left = record["expires_at"] - now
    when = f"expires in {left // 60}m{left % 60:02d}s" if left > 0 else "EXPIRED"
    return f"{record['status']:8} {when:18} {record['action']}"


def cmd_list(ddb, now: int) -> None:
    found: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    while True:
        page = ddb.scan(
            TableName=approvals.TABLE,
            FilterExpression="begins_with(#i, :p) AND #s = :pending",
            ExpressionAttributeNames={"#i": "item", "#s": "status"},
            ExpressionAttributeValues={
                ":p": {"S": approvals.PREFIX},
                ":pending": {"S": "pending"},
            },
            **kwargs,
        )
        found += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if not found:
        print("No pending approvals.")
    for raw in sorted(found, key=lambda i: i["created_at"]["N"]):
        inv, item = raw["investigation_id"]["S"], raw["item"]["S"]
        record = approvals.load(ddb, inv, item)
        if record is None:  # gone between the scan and the read
            continue
        print(f"{inv} {item.removeprefix(approvals.PREFIX)}  {describe(record, now)}")


def cmd_show(ddb, inv: str, item: str, now: int) -> dict:
    record = approvals.load(ddb, inv, item)
    if record is None:
        sys.exit("No such approval.")
    print(f"investigation  {inv}")
    print(f"finding        {report_line(ddb, inv)}")
    print(f"action         {record['action']}")
    print(f"state          {describe(record, now)}")
    print(f"postmortem     results/investigations/{inv}.md, or the table's report item")
    return record


def cmd_status(ddb, inv: str, item: str, now: int) -> None:
    cmd_show(ddb, inv, item, now)
    audits = ddb.query(
        TableName=approvals.TABLE,
        KeyConditionExpression="investigation_id = :i AND begins_with(#t, :a)",
        ExpressionAttributeNames={"#t": "item"},
        ExpressionAttributeValues={":i": {"S": inv}, ":a": {"S": f"audit#{item}#"}},
    )["Items"]
    for raw in audits:
        print(json.dumps(json.loads(raw["record"]["S"]), indent=2))
    if not audits:
        print("No Actor record yet (it can take up to 10 minutes to verify).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=["list", "show", "approve", "reject", "status"]
    )
    parser.add_argument("investigation", nargs="?")
    parser.add_argument("n", nargs="?")
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    ddb = session.client("dynamodb")
    now = int(time.time())
    if args.command == "list":
        return cmd_list(ddb, now)
    if not (args.investigation and args.n):
        parser.error("give the investigation and the approval number")
    inv, item = args.investigation, item_name(args.n)
    who = session.client("sts").get_caller_identity()["Arn"].rsplit("/", 1)[-1]

    if args.command == "show":
        cmd_show(ddb, inv, item, now)
    elif args.command == "status":
        cmd_status(ddb, inv, item, now)
    elif args.command == "reject":
        approvals.reject(ddb, inv, item, who, now)
        print("Rejected.")
    else:
        record = cmd_show(ddb, inv, item, now)
        if record["status"] != "pending" or record["expires_at"] <= now:
            sys.exit("This approval can no longer be used.")
        if input("\nType approve to run exactly this action: ").strip() != "approve":
            sys.exit("Not approved.")
        shown = approvals.action_hash(inv, item, record["action"])
        session.client("lambda").invoke(
            FunctionName=ACTOR,
            InvocationType="Event",
            Payload=json.dumps(
                {
                    "investigation_id": inv,
                    "item": item,
                    "action_hash": shown,
                    "approver": who,
                }
            ).encode(),
        )
        print(
            f"Sent to the Actor. Follow it with: scripts/approve.py status {inv} {args.n}"
        )


if __name__ == "__main__":
    main()
