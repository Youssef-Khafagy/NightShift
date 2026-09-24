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
5. **With --agent**, wait for the agent's report (the trigger rule must be
   enabled) and grade it against the ground truth.
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
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import deployments

from chaos import agent_wait
from chaos.actions import BUILD, ROLLBACK_REASON, Injector, code_sha256
from chaos.schema import Scenario, load_all
from evaluation.grade import grade

REGION = os.environ.get("AWS_REGION", "ca-central-1")
PROJECT = "nightshift"
SCENARIOS = REPO_ROOT / "chaos" / "scenarios"
RESULTS = REPO_ROOT / "results" / "chaos"
TERRAFORM = ["terraform", f"-chdir={REPO_ROOT / 'terraform'}"]
PYTHON = sys.executable
POLL = 15
RECOVERY_MARGIN = 180
AGENT_TRAFFIC_SECONDS = 600
MAX_LOAD_SECONDS = 1_800
HEALTH_WAIT = 600


# ---------------------------------------------------------------------------
# Pure pieces, tested in tests/test_chaos_run.py
# ---------------------------------------------------------------------------


def alarms_to_wait_for(scenario: Scenario) -> set[str]:
    return {f"{PROJECT}-{name}" for name in scenario.expected_alarms}


def dead_loads(exit_codes: list[int | None]) -> list[int]:
    """Indexes of load generators that have already exited.

    Every load runs longer than the phase that checks it, so an exit here,
    even a clean one, means traffic stopped and the run would measure an
    idle system.
    """
    return [i for i, code in enumerate(exit_codes) if code is not None]


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


def run_vars(with_agent: bool) -> list[str]:
    """The Terraform variables a run sets, so "plan clean" means "nothing
    but what this run turned on differs"."""
    variables = ["-var=queue_consumer_enabled=true"]
    if with_agent:
        variables.append("-var=agent_trigger_enabled=true")
    return variables


def plan_clean(with_agent: bool = False) -> bool:
    result = subprocess.run(
        [
            *TERRAFORM,
            "plan",
            "-input=false",
            "-detailed-exitcode",
            *run_vars(with_agent),
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def start_load(rate: float, seconds: int, out: Path) -> subprocess.Popen:
    # Output goes to a log beside the result, not to /dev/null: a load that
    # refuses to start must leave its reason somewhere.
    with out.with_suffix(".log").open("w") as log:
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
            stdout=log,
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


def preflight(scenario: Scenario, c: Clients, with_agent: bool = False) -> list[str]:
    problems = []
    if not os.environ.get("DSQL_ENDPOINT"):
        problems.append(
            "DSQL_ENDPOINT is not set; load.py needs it. "
            "export DSQL_ENDPOINT=$(terraform -chdir=terraform output -raw dsql_endpoint)"
        )
    if not consumer_on(c.lam):
        problems.append("the queue consumer is off; enable it through Terraform first")
    if not plan_clean(with_agent):
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


def agent_preflight() -> list[str]:
    problems = []
    rule = boto3.client("events", region_name=REGION).describe_rule(
        Name=f"{PROJECT}-alarm-to-agent"
    )
    if rule["State"] != "ENABLED":
        problems.append(
            "the alarm-to-agent rule is disabled; enable it through Terraform first"
        )
    ddb = boto3.client("dynamodb", region_name=REGION)
    lock = ddb.get_item(TableName=agent_wait.TABLE, Key=agent_wait.LOCK_KEY).get("Item")
    if lock and int(lock["expires_at"]["N"]) > time.time():
        problems.append("an incident window is still open; a new alarm would join it")
    return problems


def agent_verdict(result: dict, injected_at: float, run_dir: Path) -> dict:
    """Wait for the report, keep a copy beside the result, and grade it."""
    ddb = boto3.client("dynamodb", region_name=REGION)
    found = agent_wait.wait_for_report(ddb, injected_at)
    if not found["started"]:
        return {"started": False, "summary": "no investigation started"}
    if found.get("timed_out"):
        return {**found, "summary": "no report within the wait"}
    report, state = found["report"], found["state"] or {}
    (run_dir / "agent-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (run_dir / "agent-postmortem.md").write_text(found["postmortem"])
    steps = state.get("steps", [])
    verdict = grade(result, report, steps[-1]["at"] if steps else None)
    calls = state.get("calls", [])
    return {
        "started": True,
        "investigation_id": found["investigation_id"],
        "grade": asdict(verdict),
        "tokens": sum(c["input_tokens"] + c["output_tokens"] for c in calls),
        "calls": len(calls),
        "steps": len(steps),
        "stop_reason": state.get("stop_reason"),
        "provider": state.get("provider"),
        "model": state.get("model"),
        "summary": (
            f"{report['root_cause_component']} / {report['fault_category']} "
            f"(confidence {report['confidence']}), "
            f"{'correct' if verdict.root_cause_correct else 'WRONG'}; "
            f"actions {list(verdict.proposed_actions) or 'none'}"
            f"{', remediation ok' if verdict.remediation_correct else ''}"
            f"{', UNSAFE ' + str(list(verdict.unsafe_actions)) if verdict.unsafe_actions else ''}"
        ),
    }


def run_step(step, injector: Injector, loads: list, run_dir: Path, reason: str) -> None:
    a = step.args
    if step.do == "set_env":
        injector.set_env(
            a["service"], a["name"], a["value"], a.get("record_deploy", True)
        )
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


def run(scenario: Scenario, *, dry_run: bool, with_agent: bool = False) -> int:
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
        "acceptable_remediations": [
            r.model_dump(mode="json") for r in scenario.acceptable_remediations
        ],
        "forbidden_actions": [
            r.model_dump(mode="json") for r in scenario.forbidden_actions
        ],
        "commit": git_sha(),
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    print(f"Scenario {scenario.id} ({scenario.slug}){' [dry run]' if dry_run else ''}")

    if not dry_run:
        problems = preflight(scenario, c, with_agent)
        if with_agent:
            problems += agent_preflight()
        if problems:
            print("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))
            return 2
        run_dir.mkdir(parents=True, exist_ok=True)

    loads: list[subprocess.Popen] = []
    base_seconds = (
        scenario.warm_up_seconds + scenario.alarm_wait_seconds + RECOVERY_MARGIN
    )
    if with_agent:
        # Keep traffic flowing while the agent investigates, as it would in a
        # real incident, within load.py's 30 minute ceiling.
        base_seconds = min(base_seconds + AGENT_TRAFFIC_SECONDS, MAX_LOAD_SECONDS)
    print(
        f"Warm-up: {scenario.load_rate}/s, {scenario.warm_up_seconds}s, traffic runs {base_seconds}s"
    )
    if not dry_run:
        loads.append(
            start_load(scenario.load_rate, base_seconds, run_dir / "load-base.json")
        )
        time.sleep(scenario.warm_up_seconds)
        dead = dead_loads([p.poll() for p in loads])
        if dead:
            print(
                "WARM-UP TRAFFIC STOPPED before injection; nothing was injected. "
                f"See {run_dir / 'load-base.log'}"
            )
            return 2

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
    if with_agent and dry_run:
        print("WOULD wait for the agent's report and grade it")
    if with_agent and not dry_run:
        print("Waiting for the agent's report:", flush=True)
        result["agent"] = agent_verdict(result, injected_at, run_dir)
        print(f"  agent: {result['agent'].get('summary', result['agent'])}", flush=True)
    result["alarms_fired"] = detection(first_alarm, injected_at)
    result["expected_alarms_fired"] = bool(expected) and expected <= set(first_alarm)
    result["unexpected_alarms"] = sorted(set(first_alarm) - expected)
    print(f"  fired: {result['alarms_fired'] or 'none'}")

    print("Recover:")
    recover_started = time.time()
    for step in scenario.recover:
        run_step(step, injector, loads, run_dir, ROLLBACK_REASON)

    health: dict[str, bool] = {}
    if not dry_run:
        load_codes = [p.wait() for p in loads]
        result["load_exit_codes"] = load_codes
        health["load_ran"] = all(code == 0 for code in load_codes)
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
            health["plan_clean"] = plan_clean(with_agent)
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
        "--agent",
        action="store_true",
        help="wait for the agent's report (trigger rule enabled) and grade it",
    )
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
        injector.restore(ROLLBACK_REASON)
        injector.drain_dlq_message(f"{PROJECT}-placed-orders-dlq")
        return

    by_id = {s.id: s for s in load_all(SCENARIOS)}
    if args.scenario not in by_id:
        sys.exit(f"no scenario {args.scenario}; have {sorted(by_id)}")
    sys.exit(run(by_id[args.scenario], dry_run=not args.run, with_agent=args.agent))


if __name__ == "__main__":
    main()
