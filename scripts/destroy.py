#!/usr/bin/env python3
"""Remove everything Terraform manages, after saying exactly what that means.

Dry run by default: it writes a real destroy plan, groups what would go by
consequence, and stops. With --apply it asks for the project name typed out
and then applies that saved plan, so what is destroyed is exactly what was
shown.

    scripts/destroy.py            # show what would be destroyed
    scripts/destroy.py --apply    # destroy, after typing nightshift

Runs locally with the owner's credentials, never from CI: the CI apply role
is denied changing the CI roles, and a destroy removes them.

What survives, because Terraform does not manage it: the Terraform state
bucket, the two budgets, the IAM user and group, the raised Lambda
concurrency quota, and CloudTrail event history.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM = ["terraform", f"-chdir={REPO_ROOT / 'terraform'}"]
PLAN = REPO_ROOT / "terraform" / "destroy.tfplan"
CONFIRM = "nightshift"

# What losing each resource type means, in the order that matters most.
CONSEQUENCES = [
    (
        "Data lost for good",
        {"aws_dsql_cluster", "aws_dynamodb_table"},
        "every order, the catalogue, carts and the deployments history",
    ),
    (
        "CI stops working",
        {"aws_iam_openid_connect_provider"},
        "GitHub Actions can no longer reach AWS; rebuilding needs a local apply, like the first bootstrap",
    ),
    (
        "Paging stops",
        {
            "aws_cloudwatch_metric_alarm",
            "aws_sns_topic",
            "aws_sns_topic_subscription",
            "aws_sns_topic_policy",
        },
        "no alarms; re-creating the email subscription needs the confirmation click again",
    ),
    (
        "Service and configuration",
        None,
        "functions, queues, flags, topology, logs and roles",
    ),
]


def group(deleted_types: list[str]) -> dict[str, list[str]]:
    """Resource types by consequence heading. Pure, so it is tested."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for resource_type in deleted_types:
        for heading, types, _ in CONSEQUENCES:
            if types is None or resource_type in types:
                grouped[heading].append(resource_type)
                break
    return grouped


def deleted_resource_types(plan_json: dict) -> list[str]:
    return [
        rc["type"]
        for rc in plan_json.get("resource_changes", [])
        if rc["change"]["actions"] == ["delete"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    subprocess.run(
        [*TERRAFORM, "plan", "-destroy", "-input=false", f"-out={PLAN}"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    shown = subprocess.run(
        [*TERRAFORM, "show", "-json", str(PLAN)],
        check=True,
        capture_output=True,
        text=True,
    )
    types = deleted_resource_types(json.loads(shown.stdout))
    grouped = group(types)

    print(f"A destroy would remove {len(types)} resources.\n")
    for heading, _, meaning in CONSEQUENCES:
        if grouped.get(heading):
            counts = {
                t: grouped[heading].count(t) for t in sorted(set(grouped[heading]))
            }
            print(f"{heading}: {meaning}")
            for t, n in counts.items():
                print(f"  {n:3} {t}")
            print()
    print("Survives: state bucket, budgets, IAM user and group, concurrency quota.")

    if not args.apply:
        PLAN.unlink(missing_ok=True)
        print("\nDry run. Nothing was destroyed. Pass --apply to destroy.")
        return
    if input(f"\nType {CONFIRM} to destroy all of the above: ").strip() != CONFIRM:
        PLAN.unlink(missing_ok=True)
        sys.exit("Not destroyed.")
    subprocess.run([*TERRAFORM, "apply", "-input=false", str(PLAN)], check=True)
    PLAN.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
