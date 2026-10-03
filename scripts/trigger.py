#!/usr/bin/env python3
"""Switch the alarm-to-agent trigger on or off.

    python scripts/trigger.py status
    python scripts/trigger.py on
    python scripts/trigger.py off

While it is on, every project alarm that enters ALARM starts an
investigation in the agent Lambda, which spends LLM quota. So it is on only
for a run or a live demo. Terraform creates the rule disabled and then
ignores its state (terraform/agent.tf), like the queue consumer's
(scripts/consumer.py): switching it needs no apply, and the next apply does
not undo it.
"""

from __future__ import annotations

import argparse

import boto3

REGION = "ca-central-1"
RULE = "nightshift-alarm-to-agent"


def state(events) -> str:
    return events.describe_rule(Name=RULE)["State"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["status", "on", "off"])
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    events = boto3.Session(profile_name=args.profile, region_name=REGION).client(
        "events"
    )
    before = state(events)
    if args.command == "on":
        events.enable_rule(Name=RULE)
    elif args.command == "off":
        events.disable_rule(Name=RULE)
    after = state(events)
    print(
        f"alarm-to-agent trigger: {before}"
        + ("" if after == before else f" -> {after}")
    )


if __name__ == "__main__":
    main()
