#!/usr/bin/env python3
"""A live demo of the whole loop: a real fault, the agent, your approval.

    python scripts/demo.py prepare   # at least 45 minutes before showing it
    python scripts/demo.py start     # 15 to 20 minutes, runs in this terminal
    python scripts/demo.py stop      # afterwards: everything back off
    python scripts/demo.py status

What `start` stages is scenario 1, a bad deploy of orders, through the chaos
runner with the agent (`python -m chaos.run --scenario 1 --run --agent
--hold-for-approval`): three minutes of normal traffic, a broken version
shipped through the real deploy path, the orders-errors alarm about a minute
and a half later, the agent investigating in Lambda, and a proposed rollback.
The runner then waits for you: approve it on the dashboard's Live page (or
with scripts/approve.py). The Actor rolls orders back and watches the alarm,
and the runner recovers anything left and checks the store's health.

Why two steps. The runner refuses to inject until the store has been quiet
for 45 minutes (chaos/quiet.py), so that nothing the agent reads is left over
from something else. Switching the queue consumer and the agent trigger on is
itself a configuration write, so `prepare` does that and starts the clock,
and `start` refuses, saying how long is left, until the gap has passed.

Cost, at most: the load's 21-minute cap at one checkout a second is 1,260
checkouts, about 5,900 Lambda invocations, 3,200 SQS requests and 315 DSQL
DPU (the M6 live check sent exactly that), plus about ten model calls. That
is under 1% of each free allowance (COST.md); traffic now stops once the
agent has answered, so a demo uses less. Run `stop` when done: the trigger
would otherwise spend model calls on any later alarm.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import consumer
import trigger

from chaos import quiet

REGION = "ca-central-1"
DASHBOARD = "https://night-shift-tau-amber.vercel.app/live"
SCENARIO = "1"


def ready_at(last: datetime | None) -> datetime | None:
    """When the quiet gap after the last recorded write has passed."""
    return None if last is None else last + timedelta(minutes=quiet.QUIET_GAP_MINUTES)


def dsql_endpoint() -> str:
    return subprocess.run(
        [
            "terraform",
            f"-chdir={REPO_ROOT / 'terraform'}",
            "output",
            "-raw",
            "dsql_endpoint",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def prepare(session: boto3.Session) -> None:
    lam, cw = session.client("lambda"), session.client("cloudwatch")
    consumer.switch(lam, cw, True)
    session.client("events").enable_rule(Name=trigger.RULE)
    quiet.touch("demo prepare")
    when = ready_at(quiet.last_write())
    print("Queue consumer and agent trigger: on.")
    print(
        f"`python scripts/demo.py start` can run from {when:%H:%M} UTC ({quiet.QUIET_GAP_MINUTES} minutes)."
    )


def start(session: boto3.Session, profile: str) -> int:
    problems = quiet.check(session.client("cloudwatch"), session.client("cloudtrail"))
    if problems:
        print("Not quiet long enough yet:")
        for p in problems:
            print(f"  {p}")
        when = ready_at(quiet.last_write())
        if when:
            print(f"Try again after {when:%H:%M} UTC.")
        return 1
    if trigger.state(session.client("events")) != "ENABLED":
        print("The agent trigger is off: run `python scripts/demo.py prepare` first.")
        return 1
    env = {**os.environ, "AWS_PROFILE": profile, "DSQL_ENDPOINT": dsql_endpoint()}
    print("Starting. Open the Live page and watch it go:")
    print(f"  {DASHBOARD}")
    print("When the agent proposes the rollback, approve it there.\n")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "chaos.run",
            "--scenario",
            SCENARIO,
            "--run",
            "--agent",
            "--hold-for-approval",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    print("\nWhen you are done: python scripts/demo.py stop")
    return run.returncode


def stop(session: boto3.Session) -> None:
    session.client("events").disable_rule(Name=trigger.RULE)
    consumer.switch(session.client("lambda"), session.client("cloudwatch"), False)
    quiet.touch("demo stop")
    print("Agent trigger and queue consumer: off.")
    print("In 10 minutes, `python scripts/pause.py` proves the store is idle.")


def status(session: boto3.Session) -> None:
    lam, cw = session.client("lambda"), session.client("cloudwatch")
    print(f"queue consumer: {consumer.mapping(lam)['State']}")
    print(f"agent trigger: {trigger.state(session.client('events'))}")
    firing = cw.describe_alarms(AlarmNamePrefix="nightshift-", StateValue="ALARM")[
        "MetricAlarms"
    ]
    print(f"alarms firing: {', '.join(a['AlarmName'] for a in firing) or 'none'}")
    when = ready_at(quiet.last_write())
    if when and when > datetime.now(UTC):
        print(f"quiet gap ends: {when:%H:%M} UTC")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["prepare", "start", "stop", "status"])
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    if args.command == "prepare":
        prepare(session)
    elif args.command == "start":
        sys.exit(start(session, args.profile))
    elif args.command == "stop":
        stop(session)
    else:
        status(session)


if __name__ == "__main__":
    main()
