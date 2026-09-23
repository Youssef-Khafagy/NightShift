#!/usr/bin/env python3
"""Run one chaos scenario end to end. Dry run by default.

    python -m chaos.run --scenario 1              # print every step and AWS write
    python -m chaos.run --scenario 1 --run        # do it
    python -m chaos.run --restore results/chaos/<run>/state.json   # undo after a crash

A run:

1. **Preflight.** The queue consumer is on (load needs it), `terraform plan`
   is clean, and for every service the scenario changes, the live $LATEST
   code is byte-identical to Terraform's zip, so restoring it cannot drift.
2. **Warm-up.** Normal traffic from scripts/load.py, so the incident starts
   from a warm, steady system and start-of-run throttles are not mistaken
   for the fault. Traffic keeps running through the incident.
3. **Inject** the scenario's steps. Each change is saved to state.json as
   it happens.
4. **Wait for the expected alarms**, recording when each one fired. A
   no_fault scenario waits the whole window and records any alarm at all.
5. **(M5)** The agent investigates here.
6. **Recover**, then check health: smoke test, alarms back to OK, the DLQ
   empty where it matters, and `terraform plan` clean.

Results, including the ground truth, go to results/chaos/<run>/, which the
agent never reads.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import deployments

from chaos.actions import BUILD, Injector, code_sha256
from chaos.schema import Scenario, load_all

REGION = os.environ.get("AWS_REGION", "ca-central-1")
PROJECT = "nightshift"
SCENARIOS = REPO_ROOT / "chaos" / "scenarios"
RESULTS = REPO_ROOT / "results" / "chaos"
TERRAFORM = ["terraform", f"-chdir={REPO_ROOT / 'terraform'}"]
PYTHON = sys.executable
POLL = 15
RECOVERY_MARGIN = 180
HEALTH_WAIT = 600


# ---------------------------------------------------------------------------
# Pure pieces, tested in tests/test_chaos_run.py
# ---------------------------------------------------------------------------


def alarms_to_wait_for(scenario: Scenario) -> set[str]:
    return {f"{PROJECT}-{name}" for name in scenario.expected_alarms}


def firing(states: dict[str, str]) -> set[str]:
    return {name for name, state in states.items() if state == "ALARM"}


def detection(first_alarm: dict[str, float], injected_at: float) -> dict[str, float]:
    """Seconds from injection to each alarm first firing."""
    return {name: round(t - injected_at, 1) for name, t in sorted(first_alarm.items())}


def services_changed(scenario: Scenario) -> set[str]:
    return {
        s.args["service"]
        for s in scenario.inject
        if s.do in ("set_env", "deploy_patch")
    }


# ---------------------------------------------------------------------------
# Talking to AWS and to the other scripts
# ---------------------------------------------------------------------------


class Clients:
    def __init__(self) -> None:
        self.lam = boto3.client("lambda", region_name=REGION)
        self.sqs = boto3.client("sqs", region_name=REGION)
        self.cw = boto3.client("cloudwatch", region_name=REGION)
        self.table = boto3.resource("dynamodb", region_name=REGION).Table(
            deployments.TABLE
        )


def alarm_states(cw) -> dict[str, str]:
    alarms = cw.describe_alarms(AlarmNamePrefix=f"{PROJECT}-")["MetricAlarms"]
    return {a["AlarmName"]: a["StateValue"] for a in alarms if a["ActionsEnabled"]}


def consumer_on(lam) -> bool:
    mappings = lam.list_event_source_mappings(
        FunctionName=f"{PROJECT}-fulfillment:live"
    )
    return any(m["State"] == "Enabled" for m in mappings["EventSourceMappings"])


def plan_clean() -> bool:
    result = subprocess.run(
        [
            *TERRAFORM,
            "plan",
            "-input=false",
            "-detailed-exitcode",
            "-var=queue_consumer_enabled=true",
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def start_load(rate: float, seconds: int, out: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            PYTHON,
            str(REPO_ROOT / "scripts" / "load.py"),
            "--rate",
            str(rate),
            "--duration",
            str(seconds),
            "--run",
            "--out",
            str(out),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def smoke_test() -> bool:
    return (
        subprocess.run(
            [PYTHON, str(REPO_ROOT / "scripts" / "smoke_checkout.py")],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def dlq_empty(sqs) -> bool:
    url = sqs.get_queue_url(QueueName=f"{PROJECT}-placed-orders-dlq")["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return all(v == "0" for v in attrs.values())


def git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def preflight(scenario: Scenario, c: Clients) -> list[str]:
    problems = []
    if not consumer_on(c.lam):
        problems.append("the queue consumer is off; enable it through Terraform first")
    if not plan_clean():
        problems.append("terraform plan is not clean")
    for service in sorted(services_changed(scenario)):
        function = deployments.function_name(service)
        live = c.lam.get_function_configuration(FunctionName=function)["CodeSha256"]
        if live != code_sha256((BUILD / f"{function}.zip").read_bytes()):
            problems.append(
                f"{function} $LATEST differs from terraform/.build; restore would drift"
            )
    already = firing(alarm_states(c.cw))
    if already:
        problems.append(f"alarms already firing: {', '.join(sorted(already))}")
    return problems


def run_step(step, injector: Injector, loads: list, run_dir: Path, reason: str) -> None:
    a = step.args
    if step.do == "set_env":
        injector.set_env(a["service"], a["name"], a["value"])
    elif step.do == "deploy_patch":
        injector.deploy_patch(a["service"], a["file"], a["find"], a["with"])
    elif step.do == "send_message":
        injector.send_message(a["queue"], json.dumps(a["body"]))
    elif step.do == "load":
        out = run_dir / f"load-{len(loads) + 1}.json"
        print(
            f"  {'WOULD ' if injector.dry_run else ''}start load {a['rate']}/s for {a['seconds']}s"
        )
        if not injector.dry_run:
            loads.append(start_load(a["rate"], a["seconds"], out))
    elif step.do == "wait":
        print(f"  wait {a['seconds']}s")
        if not injector.dry_run:
            time.sleep(a["seconds"])
    elif step.do == "restore":
        injector.restore(reason)
    elif step.do == "drain_dlq_message":
        injector.drain_dlq_message(f"{PROJECT}-placed-orders-dlq")


def run(scenario: Scenario, *, dry_run: bool) -> int:
    run_id = f"{scenario.id:02d}-{scenario.slug}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    run_dir = RESULTS / run_id
    c = Clients()
    injector = Injector(
        c.lam,
        c.sqs,
        c.table,
        actor=os.environ.get("USER", "unknown"),
        git_sha=git_sha(),
        dry_run=dry_run,
        state_path=run_dir / "state.json",
    )
    result: dict = {
        "run_id": run_id,
        "scenario": scenario.id,
        "slug": scenario.slug,
        "ground_truth": scenario.ground_truth.model_dump(mode="json"),
        "commit": git_sha(),
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    print(f"Scenario {scenario.id} ({scenario.slug}){' [dry run]' if dry_run else ''}")

    if not dry_run:
        problems = preflight(scenario, c)
        if problems:
            print("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))
            return 2
        run_dir.mkdir(parents=True, exist_ok=True)

    loads: list[subprocess.Popen] = []
    base_seconds = (
        scenario.warm_up_seconds + scenario.alarm_wait_seconds + RECOVERY_MARGIN
    )
    print(
        f"Warm-up: {scenario.load_rate}/s, {scenario.warm_up_seconds}s, traffic runs {base_seconds}s"
    )
    if not dry_run:
        loads.append(
            start_load(scenario.load_rate, base_seconds, run_dir / "load-base.json")
        )
        time.sleep(scenario.warm_up_seconds)

    for step in scenario.setup:
        run_step(step, injector, loads, run_dir, "setup")
    print("Inject:")
    injected_at = time.time()
    for step in scenario.inject:
        run_step(step, injector, loads, run_dir, "inject")
    result["injected_at"] = datetime.fromtimestamp(injected_at, UTC).isoformat(
        timespec="seconds"
    )

    expected = alarms_to_wait_for(scenario)
    print(
        f"Waiting up to {scenario.alarm_wait_seconds}s for: {', '.join(sorted(expected)) or 'nothing (no fault)'}"
    )
    first_alarm: dict[str, float] = {}
    if not dry_run:
        deadline = injected_at + scenario.alarm_wait_seconds
        while time.time() < deadline:
            for name in firing(alarm_states(c.cw)):
                first_alarm.setdefault(name, time.time())
            if expected and expected <= set(first_alarm):
                break
            time.sleep(POLL)
    result["alarms_fired"] = detection(first_alarm, injected_at)
    result["expected_alarms_fired"] = bool(expected) and expected <= set(first_alarm)
    result["unexpected_alarms"] = sorted(set(first_alarm) - expected)
    print(f"  fired: {result['alarms_fired'] or 'none'}")

    print("Recover:")
    recover_started = time.time()
    for step in scenario.recover:
        run_step(
            step, injector, loads, run_dir, f"recovery after scenario run {run_id}"
        )

    health: dict[str, bool] = {}
    if not dry_run:
        for p in loads:
            p.wait()
        if "alarms_ok" in scenario.health_check:
            deadline = time.time() + HEALTH_WAIT
            while firing(alarm_states(c.cw)) and time.time() < deadline:
                time.sleep(POLL)
            health["alarms_ok"] = not firing(alarm_states(c.cw))
        if "dlq_empty" in scenario.health_check:
            health["dlq_empty"] = dlq_empty(c.sqs)
        if "smoke_test" in scenario.health_check:
            health["smoke_test"] = smoke_test()
        if "plan_clean" in scenario.health_check:
            health["plan_clean"] = plan_clean()
    result["recovery_seconds"] = round(time.time() - recover_started, 1)
    result["health"] = health
    result["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"Health: {health or '(dry run)'}")

    if dry_run:
        print("\nDry run. Nothing was changed. Pass --run to do it.")
        return 0
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Result: {run_dir / 'result.json'}")
    healthy = all(health.values())
    detected = result["expected_alarms_fired"] or not expected
    return 0 if healthy and detected else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", type=int)
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--restore", type=Path, help="state.json from an interrupted run"
    )
    args = parser.parse_args()

    if args.restore:
        c = Clients()
        injector = Injector(
            c.lam,
            c.sqs,
            c.table,
            actor=os.environ.get("USER", "unknown"),
            git_sha=git_sha(),
            dry_run=not args.run,
            state_path=args.restore,
        )
        injector.injections = json.loads(args.restore.read_text())
        injector.restore("recovery after an interrupted run")
        injector.drain_dlq_message(f"{PROJECT}-placed-orders-dlq")
        return

    by_id = {s.id: s for s in load_all(SCENARIOS)}
    if args.scenario not in by_id:
        sys.exit(f"no scenario {args.scenario}; have {sorted(by_id)}")
    sys.exit(run(by_id[args.scenario], dry_run=not args.run))


if __name__ == "__main__":
    main()
