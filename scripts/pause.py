#!/usr/bin/env python3
"""Bring idle usage to near zero, then prove it.

Almost nothing in NightShift costs anything while no one is using it: Lambda,
DynamoDB reads, DSQL and SNS all bill per use, and an idle DSQL cluster
scales to zero. The one exception is the SQS trigger on placed-orders, which
polls the queue around the clock while enabled. So pausing is:

1. Make sure that trigger is disabled. From M6 it is switched by
   scripts/consumer.py, not Terraform (decision A: an Actor pause must not
   be undone by the next apply); if it is on, this asks for the word `pause`
   and switches it off, with queue-age's notifications.
2. Check that nothing else can run by itself: no enabled EventBridge rules or
   schedules, no provisioned concurrency.
3. Check the last 10 minutes of real usage: Lambda invocations and SQS empty
   receives. With the trigger off and no traffic, both should be zero.

Exits 0 when paused and idle, 1 otherwise. Reads only, except the switch in
step 1, which needs the confirmation word.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import consumer

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
        problems.append(f"{checks['eventbridge_rules']} enabled EventBridge rule(s)")
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


def disable_consumer() -> None:
    if input("Type pause to switch the queue trigger off: ").strip() != "pause":
        sys.exit("Not switched.")
    consumer.switch(
        lam_client(), boto3.client("cloudwatch", region_name=REGION), on=False
    )


def lam_client():
    return boto3.client("lambda", region_name=REGION)


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
        print("The queue trigger is enabled.")
        disable_consumer()

    cw = boto3.client("cloudwatch", region_name=REGION)
    checks: dict[str, Any] = {
        "consumer_states": consumer_states(lam),
        # A disabled rule starts nothing: the alarm-to-agent rule exists
        # permanently and is only enabled for a run.
        "eventbridge_rules": sum(
            1
            for rule in boto3.client("events", region_name=REGION).list_rules()["Rules"]
            if rule.get("State") == "ENABLED"
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
    print(f"enabled EventBridge rules: {checks['eventbridge_rules']}")
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
