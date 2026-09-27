"""The benchmark: every configuration against the same staged incidents.

    python -m evaluation.bench --pass p1 --new --seed 20260926      # write the plan
    python -m evaluation.bench --pass p1 --status
    python -m evaluation.bench --pass p1 --batch 6                  # dry run
    python -m evaluation.bench --pass p1 --batch 6 --run
    python -m evaluation.bench --pass p1 --requeue 7                # after a restore

A pass is every scenario three times, in an order shuffled once from a
recorded seed, so no scenario always follows the same one. For each
incident, chaos.run waits out the quiet gap, warms up, injects, and on the
first alarm this starts all five configurations at once, each as its own
process holding only temporary Investigator credentials:

    agent-mistral, agent-gemini      the full agent
    alarm-only-mistral, -gemini      the same models, shown only the alarm
    runbook                          the scripted runbook, no model

Proposals are graded, never approved: nothing here creates an approval,
and the Actor must not be invoked during a batch (checked from its
Invocations metric). A run whose tool results show anything from before
the previous incident is re-queued once rather than graded.

State lives in results/bench/<pass>/: plan.json (written once) and one
file per entry. An entry left "running" by a crash needs its injection
undone by hand (chaos.run --restore), then --requeue.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation.contamination import leftovers
from evaluation.grade import grade

REGION = "ca-central-1"
BENCH = REPO_ROOT / "results" / "bench"
INVESTIGATIONS = REPO_ROOT / "results" / "investigations"
PYTHON = sys.executable
RUNS_PER_SCENARIO = 3
MAX_ATTEMPTS = 2  # a contaminated run is re-queued once
INVESTIGATION_TIMEOUT = 1_000  # the loop stops itself at 840 s
TRIGGER_RULE = "nightshift-alarm-to-agent"
ACTOR = "nightshift-actor"


@dataclass(frozen=True)
class Config:
    key: str
    kind: str  # agent, alarm-only, runbook
    provider: str | None = None


CONFIGS = (
    Config("agent-mistral", "agent", "mistral"),
    Config("agent-gemini", "agent", "gemini"),
    Config("alarm-only-mistral", "alarm-only", "mistral"),
    Config("alarm-only-gemini", "alarm-only", "gemini"),
    Config("runbook", "runbook"),
)


# ---------------------------------------------------------------------------
# Pure pieces, tested in tests/test_bench.py
# ---------------------------------------------------------------------------


def make_plan(
    scenarios: list[int],
    runs: int,
    seed: int,
    first_runs: dict[int, int] | None = None,
) -> list[dict]:
    """Every scenario `runs` times, in two phases, each shuffled.

    Phase 1 is what gets run first: each scenario `first_runs.get(s, 1)`
    times. Phase 2 is the rest, up to `runs`. Stopping after phase 1 gives a
    complete, reportable benchmark chosen before any result was seen;
    running phase 2 later only adds runs at the same commit. Without
    `first_runs`, everything is phase 1."""
    rng = random.Random(seed)
    first = first_runs if first_runs is not None else {s: runs for s in scenarios}
    phases = [
        [(s, r) for s in sorted(scenarios) for r in range(1, first.get(s, 1) + 1)],
        [
            (s, r)
            for s in sorted(scenarios)
            for r in range(first.get(s, 1) + 1, runs + 1)
        ],
    ]
    plan: list[dict] = []
    for phase, order in enumerate(phases, start=1):
        rng.shuffle(order)
        plan += [
            {
                "entry": len(plan) + i + 1,
                "scenario": s,
                "run": r,
                "attempt": 1,
                "phase": phase,
            }
            for i, (s, r) in enumerate(order)
        ]
    return plan


def command(config: Config, alarm: str, investigation_id: str) -> list[str]:
    common = [
        "--alarm",
        alarm,
        "--investigation-id",
        investigation_id,
        "--profile",
        "ambient",
    ]
    if config.kind == "agent":
        assert config.provider
        return [
            PYTHON,
            "-m",
            "agent.investigate",
            "--provider",
            config.provider,
            "--no-approvals",
            *common,
        ]
    kind = ["--kind", config.kind]
    if config.provider:
        kind += ["--provider", config.provider]
    return [PYTHON, "-m", "baselines.run", *kind, *common]


def isolated_env(creds: dict[str, str], base: dict[str, str]) -> dict[str, str]:
    """The environment for one investigation: the Investigator role's
    temporary credentials and nothing that could reach the owner's login."""
    env = {
        k: v
        for k, v in base.items()
        if not k.startswith("AWS_") and k != "DSQL_ENDPOINT"
    }
    env.update(
        AWS_ACCESS_KEY_ID=creds["AccessKeyId"],
        AWS_SECRET_ACCESS_KEY=creds["SecretAccessKey"],
        AWS_SESSION_TOKEN=creds["SessionToken"],
        AWS_REGION=REGION,
        AWS_DEFAULT_REGION=REGION,
        # No profile or login cache to fall back on.
        AWS_CONFIG_FILE=os.devnull,
        AWS_SHARED_CREDENTIALS_FILE=os.devnull,
    )
    return env


def outcome(result: dict, report: dict | None, state: dict | None) -> dict[str, Any]:
    """One configuration's grade on one incident, and whether it leaked."""
    if report is None or state is None:
        return {"graded": False, "reason": "no report"}
    steps = state.get("steps", [])
    previous = result.get("previous_last_write")
    leaked = leftovers(steps, datetime.fromisoformat(previous)) if previous else []
    calls = state.get("calls", [])
    return {
        "graded": True,
        "grade": asdict(grade(result, report, steps[-1]["at"] if steps else None)),
        "leftover_steps": leaked,
        "tokens": sum(c["input_tokens"] + c["output_tokens"] for c in calls),
        "llm_calls": len(calls),
        "tool_steps": len(steps),
        "stop_reason": state.get("stop_reason"),
        "log_bytes_scanned": state.get("log_bytes_scanned", 0),
    }


def entry_status(result: dict) -> str:
    """done, contaminated or failed. Contamination wins: a leaked run is not
    graded whatever else happened."""
    panel = result.get("investigations", {})
    if any(o.get("leftover_steps") for o in panel.get("configs", {}).values()):
        return "contaminated"
    healthy = all(result.get("health", {}).values()) and result.get("health")
    return "done" if healthy and panel.get("started") else "failed"


def next_entries(plan: list[dict], states: dict[int, dict], limit: int) -> list[dict]:
    """Entries with no state file, or one put back to pending."""
    todo = [
        p
        for p in plan
        if states.get(p["entry"], {}).get("status", "pending") == "pending"
    ]
    return todo[:limit]


def scrub(text: str, secret: str) -> str:
    return text.replace(secret, "<DSQL_CLUSTER_ID>") if secret else text


# ---------------------------------------------------------------------------
# The panel: five investigations beside one incident
# ---------------------------------------------------------------------------


class LocalPanel:
    def __init__(self, admin: boto3.Session, configs: tuple[Config, ...] = CONFIGS):
        self.admin = admin
        self.configs = configs
        self.procs: dict[str, tuple[str, subprocess.Popen, Any]] = {}
        self.started_at: float | None = None

    def start(self, alarm: str) -> None:
        account = self.admin.client("sts").get_caller_identity()["Account"]
        creds = self.admin.client("sts").assume_role(
            RoleArn=f"arn:aws:iam::{account}:role/nightshift-investigator",
            RoleSessionName="nightshift-bench",
            DurationSeconds=3600,
        )["Credentials"]
        env = isolated_env(creds, dict(os.environ))
        self.started_at = time.time()
        self.alarm = alarm
        INVESTIGATIONS.mkdir(parents=True, exist_ok=True)
        for config in self.configs:
            investigation_id = uuid.uuid4().hex[:12]
            log = (INVESTIGATIONS / f"{investigation_id}.log").open("w")
            proc = subprocess.Popen(
                command(config, alarm, investigation_id),
                cwd=REPO_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            self.procs[config.key] = (investigation_id, proc, log)

    def finish(self, result: dict, run_dir: Path) -> dict:
        self.run_dir = run_dir
        if not self.procs:
            return {"started": False, "reason": "no alarm fired"}
        configs: dict[str, Any] = {}
        deadline = (self.started_at or time.time()) + INVESTIGATION_TIMEOUT
        cluster = os.environ.get("DSQL_ENDPOINT", "").split(".")[0]
        for key, (investigation_id, proc, log) in self.procs.items():
            try:
                code = proc.wait(timeout=max(1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
                code = None
            log.close()
            for path in INVESTIGATIONS.glob(f"{investigation_id}*"):
                path.write_text(scrub(path.read_text(), cluster))
            report = load(INVESTIGATIONS / f"{investigation_id}.report.json")
            saved = load(INVESTIGATIONS / f"{investigation_id}.json")
            configs[key] = {
                "investigation_id": investigation_id,
                "exit_code": code,
                **outcome(result, report, saved["state"] if saved else None),
            }
            print(f"  {key}: {summary_line(configs[key])}", flush=True)
        return {"started": True, "alarm": self.alarm, "configs": configs}


def load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def summary_line(o: dict) -> str:
    if not o.get("graded"):
        return f"not graded ({o.get('reason')}, exit {o.get('exit_code')})"
    g = o["grade"]
    verdict = (
        "correct" if g["root_cause_correct"] else "hedged" if g["hedged"] else "WRONG"
    )
    leak = f", LEFTOVERS in steps {o['leftover_steps']}" if o["leftover_steps"] else ""
    unsafe = f", UNSAFE {g['unsafe_actions']}" if g["unsafe_actions"] else ""
    return (
        f"{g['answered_component']} / {g['answered_category']} ({g['confidence']}) "
        f"{verdict}, {o['tokens']} tokens{unsafe}{leak}"
    )


# ---------------------------------------------------------------------------
# Checks before and after a batch
# ---------------------------------------------------------------------------


def uncommitted() -> list[str]:
    """Tracked or new files outside results/ that differ from the commit.
    Every result records one commit; a batch run from a changed tree would
    be labelled with code it did not run."""
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if not line[3:].startswith("results/")]


def batch_problems(admin: boto3.Session, states: dict[int, dict]) -> list[str]:
    problems = []
    changed = uncommitted()
    if changed:
        problems.append(f"uncommitted changes outside results/: {changed[:3]}")
    running = [n for n, s in states.items() if s.get("status") == "running"]
    if running:
        problems.append(
            f"entries {running} were interrupted: undo each with "
            "python -m chaos.run --restore <run_dir>/state.json --run, then --requeue N"
        )
    rule = admin.client("events").describe_rule(Name=TRIGGER_RULE)
    if rule["State"] != "DISABLED":
        problems.append(
            f"{TRIGGER_RULE} is enabled; the Lambda agent would investigate too"
        )
    if not os.environ.get("DSQL_ENDPOINT"):
        problems.append("DSQL_ENDPOINT is not set")
    # Its exit status is the answer (1 on any ALERT), read on the next line.
    cost = subprocess.run(
        [PYTHON, str(REPO_ROOT / "scripts" / "cost_check.py")], check=False
    )
    if cost.returncode != 0:
        problems.append("scripts/cost_check.py did not pass")
    return problems


def actor_invocations(admin: boto3.Session, since: datetime) -> float:
    points = admin.client("cloudwatch").get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Invocations",
        Dimensions=[{"Name": "FunctionName", "Value": ACTOR}],
        StartTime=since - timedelta(minutes=5),
        EndTime=datetime.now(UTC),
        Period=300,
        Statistics=["Sum"],
    )["Datapoints"]
    return sum(p["Sum"] for p in points)


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def pass_dir(name: str) -> Path:
    return BENCH / name


def read_states(root: Path) -> dict[int, dict]:
    states = {}
    for path in sorted((root / "entries").glob("*.json")):
        data = json.loads(path.read_text())
        states[data["entry"]] = data
    return states


def write_state(root: Path, state: dict) -> None:
    (root / "entries").mkdir(parents=True, exist_ok=True)
    path = root / "entries" / f"{state['entry']:03d}.json"
    path.write_text(json.dumps(state, indent=2) + "\n")


def run_entry(root: Path, item: dict, admin: boto3.Session) -> dict:
    from chaos import quiet
    from chaos.run import SCENARIOS, run
    from chaos.schema import load_all

    scenario = {s.id: s for s in load_all(SCENARIOS)}[item["scenario"]]
    state = {**item, "status": "running", "started": datetime.now(UTC).isoformat()}
    write_state(root, state)
    panel = LocalPanel(admin)
    began = datetime.now(UTC)
    try:
        code = run(scenario, dry_run=False, wait_quiet=True, panel=panel)
    finally:
        quiet.touch_if_started("bench entry ended")
    run_dir: Path | None = getattr(panel, "run_dir", None)
    result = load(run_dir / "result.json") if run_dir else None
    if run_dir is None or result is None:
        # Refused before any write (not quiet, preflight failed): nothing to
        # undo, and the next entry would be refused for the same reason.
        return {**item, "status": "refused", "exit_code": code}
    state.update(
        run_dir=str(run_dir.relative_to(REPO_ROOT)),
        exit_code=code,
        actor_invocations=actor_invocations(admin, began),
        finished=datetime.now(UTC).isoformat(),
        status=entry_status(result),
    )
    if state["actor_invocations"]:
        state["status"] = "failed"
        state["note"] = "the Actor was invoked during a benchmark incident"
    return state


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pass", dest="name", required=True)
    parser.add_argument("--new", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--scenarios", help="comma separated; default every scenario file"
    )
    parser.add_argument("--runs", type=int, default=RUNS_PER_SCENARIO)
    parser.add_argument(
        "--first-runs",
        help="phase 1 runs per scenario, e.g. 1:3,4:3,11:3 (others once); "
        "default: every run is phase 1",
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--batch", type=int)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--requeue", type=int)
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()
    root = pass_dir(args.name)

    if args.new:
        from chaos.run import SCENARIOS
        from chaos.schema import load_all

        if (root / "plan.json").exists():
            sys.exit(f"{root / 'plan.json'} exists; a plan is written once")
        if args.seed is None:
            sys.exit("--new needs --seed, so the order can be reproduced")
        ids = (
            [int(x) for x in args.scenarios.split(",")]
            if args.scenarios
            else sorted(s.id for s in load_all(SCENARIOS))
        )
        first = None
        if args.first_runs:
            first = {s: 1 for s in ids}
            for pair in args.first_runs.split(","):
                s, n = pair.split(":")
                first[int(s)] = int(n)
        plan = make_plan(ids, args.runs, args.seed, first)
        root.mkdir(parents=True, exist_ok=True)
        (root / "plan.json").write_text(
            json.dumps(
                {
                    "seed": args.seed,
                    "scenarios": ids,
                    "runs": args.runs,
                    "first_runs": first,
                    "entries": plan,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"{len(plan)} entries in {root / 'plan.json'}")
        return

    plan = json.loads((root / "plan.json").read_text())["entries"]
    states = read_states(root)

    if args.requeue:
        state = states[args.requeue]
        write_state(
            root,
            {**state, "status": "pending", "requeued": datetime.now(UTC).isoformat()},
        )
        print(f"entry {args.requeue} is pending again")
        return

    if args.status or not args.batch:
        counts: dict[str, int] = {}
        for p in plan:
            s = states.get(p["entry"], {}).get("status", "pending")
            key = f"phase {p.get('phase', 1)} {s}"
            counts[key] = counts.get(key, 0) + 1
        print(f"{len(plan)} entries: {dict(sorted(counts.items()))}")
        return

    todo = next_entries(plan, states, args.batch)
    for item in todo:
        print(f"entry {item['entry']}: scenario {item['scenario']} run {item['run']}")
    if not args.run:
        print("\nDry run. Pass --run to run them.")
        return

    admin = boto3.Session(profile_name=args.profile, region_name=REGION)
    problems = batch_problems(admin, states)
    if problems:
        sys.exit("NOT STARTED:\n  " + "\n  ".join(problems))
    for item in todo:
        state = run_entry(root, item, admin)
        write_state(root, state)
        print(f"entry {item['entry']}: {state['status']}", flush=True)
        if state["status"] == "contaminated" and item["attempt"] < MAX_ATTEMPTS:
            again = {
                **item,
                "entry": max(p["entry"] for p in plan) + 1,
                "attempt": item["attempt"] + 1,
            }
            # Right after the entry it replaces, not at the end: appended, a
            # phase 1 re-run would wait behind all of phase 2.
            plan.insert(plan.index(item) + 1, again)
            data = json.loads((root / "plan.json").read_text())
            data["entries"] = plan
            (root / "plan.json").write_text(json.dumps(data, indent=2) + "\n")
            print(f"  re-queued as entry {again['entry']}")
        if state["status"] == "refused":
            write_state(root, {**item})  # back to pending: nothing ran
            sys.exit(f"entry {item['entry']} was refused before any write; see above")
        if state["status"] == "failed":
            sys.exit(f"entry {item['entry']} failed; stopping the batch to look at it")


if __name__ == "__main__":
    main()
