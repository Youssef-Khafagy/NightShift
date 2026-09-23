#!/usr/bin/env python3
"""Bring idle usage to near zero, then prove it.

Almost nothing in NightShift costs anything while no one is using it: Lambda,
DynamoDB reads, DSQL and SNS all bill per use, and an idle DSQL cluster
scales to zero. The one exception is the SQS trigger on placed-orders, which
polls the queue around the clock while enabled. So pausing is:

1. Make sure that trigger is disabled. It is changed only through Terraform
   (`queue_consumer_enabled`), so state never drifts; if it is on, this shows
   the plan and asks for the word `pause` before applying.
2. Check that nothing else can run by itself: no EventBridge rules or
   schedules, no provisioned concurrency.
3. Check the last 10 minutes of real usage: Lambda invocations and SQS empty
   receives. With the trigger off and no traffic, both should be zero.

Exits 0 when paused and idle, 1 otherwise. Reads only, except the Terraform
apply in step 1, which needs the confirmation word.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM = ["terraform", f"-chdir={REPO_ROOT / 'terraform'}"]
REGION = "ca-central-1"
PROJECT = "nightshift"
FUNCTIONS = ("cart", "orders", "payments", "fulfillment", "hello")
QUEUE = f"{PROJECT}-placed-orders"
WINDOW = timedelta(minutes=10)


def idle_problems(checks: dict) -> list[str]:
    """Everything that says the store is not idle. Pure, so it is tested."""
    problems = []
    if checks["consumer_states"] and any(
        s != "Disabled" for s in checks["consumer_states"]
    ):
        problems.append(f"queue trigger is {', '.join(checks['consumer_states'])}")
    if checks["eventbridge_rules"]:
        problems.append(f"{checks['eventbridge_rules']} EventBridge rule(s) exist")
    if checks["schedules"]:
        problems.append(
            f"{checks['schedules']} EventBridge Scheduler schedule(s) exist"
        )
    for function, count in checks["provisioned"].items():
        if count:
            problems.append(f"{function} has provisioned concurrency")
    if checks["invocations"]:
        problems.append(
            f"{checks['invocations']:.0f} Lambda invocations in the last 10 minutes"
        )
    if checks["empty_receives"]:
        problems.append(
            f"{checks['empty_receives']:.0f} SQS polls in the last 10 minutes"
        )
    return problems


def consumer_states(lam) -> list[str]:
    mappings = lam.list_event_source_mappings(
        FunctionName=f"{PROJECT}-fulfillment:live"
    )
    return [m["State"] for m in mappings["EventSourceMappings"]]


def disable_consumer_through_terraform() -> None:
    plan = REPO_ROOT / "terraform" / "pause.tfplan"
    subprocess.run(
        [
            *TERRAFORM,
            "plan",
            "-input=false",
            "-var=queue_consumer_enabled=false",
            f"-out={plan}",
        ],
        check=True,
    )
    if input("\nType pause to apply this plan: ").strip() != "pause":
        sys.exit("Not applied.")
    subprocess.run([*TERRAFORM, "apply", "-input=false", str(plan)], check=True)
    plan.unlink(missing_ok=True)


def recent_sum(cw, namespace: str, metric: str, dimensions: list[dict]) -> float:
    now = datetime.now(UTC)
    points = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=dimensions,
        StartTime=now - WINDOW,
        EndTime=now,
        Period=60,
        Statistics=["Sum"],
    )["Datapoints"]
    return sum(p["Sum"] for p in points)


def main() -> None:
    lam = boto3.client("lambda", region_name=REGION)
    if any(s != "Disabled" for s in consumer_states(lam)):
        print("The queue trigger is enabled. Disabling it through Terraform:")
        disable_consumer_through_terraform()

    cw = boto3.client("cloudwatch", region_name=REGION)
    checks = {
        "consumer_states": consumer_states(lam),
        "eventbridge_rules": len(
            boto3.client("events", region_name=REGION).list_rules()["Rules"]
        ),
        "schedules": len(
            boto3.client("scheduler", region_name=REGION).list_schedules()["Schedules"]
        ),
        "provisioned": {
            f: len(
                lam.list_provisioned_concurrency_configs(FunctionName=f"{PROJECT}-{f}")[
                    "ProvisionedConcurrencyConfigs"
                ]
            )
            for f in FUNCTIONS
        },
        "invocations": recent_sum(cw, "AWS/Lambda", "Invocations", []),
        "empty_receives": recent_sum(
            cw,
            "AWS/SQS",
            "NumberOfEmptyReceives",
            [{"Name": "QueueName", "Value": QUEUE}],
        ),
    }

    print(f"queue trigger:            {', '.join(checks['consumer_states'])}")
    print(f"EventBridge rules:        {checks['eventbridge_rules']}")
    print(f"Scheduler schedules:      {checks['schedules']}")
    print(f"provisioned concurrency:  {sum(checks['provisioned'].values())}")
    print(f"invocations, last 10 min: {checks['invocations']:.0f}")
    print(f"SQS polls, last 10 min:   {checks['empty_receives']:.0f}")

    problems = idle_problems(checks)
    if problems:
        print("\nNOT IDLE:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print("\nPaused: nothing is running and nothing will start by itself.")


if __name__ == "__main__":
    main()
