#!/usr/bin/env python3
"""Switch the queue consumer on or off, and queue-age's notifications with it.

    python scripts/consumer.py status
    python scripts/consumer.py on
    python scripts/consumer.py off

The placed-orders trigger polls SQS around the clock while it is enabled,
about two thirds of the free SQS allowance a month for nothing, so it is on
only during a run. From M6 this is a script, not Terraform, so the Actor's
pause_queue_consumer is not undone by the next apply (decision A, like the
aliases). queue-age only notifies while the consumer runs: with it off, the
smoke test's order waits in the queue by design and should not page.
"""

from __future__ import annotations

import argparse
import sys

import boto3

REGION = "ca-central-1"
# The trigger is attached to the alias; listing by the bare function name
# finds nothing.
FUNCTION = "nightshift-fulfillment:live"
QUEUE_AGE_ALARM = "nightshift-queue-age"


def mapping(lam) -> dict:
    found = lam.list_event_source_mappings(FunctionName=FUNCTION)["EventSourceMappings"]
    if len(found) != 1:
        sys.exit(f"expected one trigger on {FUNCTION}, found {len(found)}")
    return found[0]


def switch(lam, cw, on: bool) -> str:
    m = mapping(lam)
    lam.update_event_source_mapping(UUID=m["UUID"], Enabled=on)
    if on:
        cw.enable_alarm_actions(AlarmNames=[QUEUE_AGE_ALARM])
    else:
        cw.disable_alarm_actions(AlarmNames=[QUEUE_AGE_ALARM])
    return m["State"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["status", "on", "off"])
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    lam, cw = session.client("lambda"), session.client("cloudwatch")
    if args.command == "status":
        alarm = cw.describe_alarms(AlarmNames=[QUEUE_AGE_ALARM])["MetricAlarms"][0]
        print(f"queue trigger: {mapping(lam)['State']}")
        print(f"queue-age notifications: {'on' if alarm['ActionsEnabled'] else 'off'}")
        return
    before = switch(lam, cw, args.command == "on")
    print(
        f"queue trigger: {before} -> {'Enabling' if args.command == 'on' else 'Disabling'}"
    )
    print(f"queue-age notifications: {args.command}")


if __name__ == "__main__":
    main()
