"""Build the dashboard's public replay data from a committed benchmark pass.

    python scripts/build_replay.py            # writes dashboard/public/replay/
    python scripts/build_replay.py --check    # exit 1 if the committed copy differs

The public dashboard shows these files and nothing else. Its pages are built
from them once, at build time, so a visitor's request never reaches AWS or a
model. That makes this script the only way anything in `results/` reaches
the internet, and it behaves like a gate:

- it reads one pass, only the entries that count ("done"), and only the
  files those entries point at;
- it keeps what a reader needs (ground truth, timeline, every
  configuration's journal, answer, grade and postmortem) and drops what they
  do not (model thought signatures, call IDs, shortened copies of results);
- it scrubs account IDs, DSQL cluster IDs and email addresses from every
  string, then scans the finished files and refuses to write if anything
  that looks like one is still there;
- its output depends only on its input, byte for byte, so a test rebuilds it
  and fails when the committed copy differs from what the results say.

Nothing here touches AWS.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.report import found_nothing
from chaos.schema import load_all
from evaluation.summarize import mcnemar

OUT = REPO_ROOT / "dashboard" / "public" / "replay"
SCENARIO_DIR = REPO_ROOT / "chaos" / "scenarios"

# Display order and names, the same as the README's results table.
CONFIGS = [
    ("agent-gemini", "Agent, Gemini", "agent"),
    ("agent-mistral", "Agent, Mistral", "agent"),
    ("alarm-only-gemini", "Alarm text only, Gemini", "alarm-only"),
    ("alarm-only-mistral", "Alarm text only, Mistral", "alarm-only"),
    ("runbook", "Scripted runbook", "runbook"),
]

# The paired comparisons LEARNING.md section 20 reports, and the question each
# one answers. Computed here from the per-incident grades, never copied.
COMPARISONS = [
    ("agent-gemini", "runbook", "Does the agent beat a written runbook?"),
    (
        "agent-gemini",
        "alarm-only-gemini",
        "Does investigating help, compared with the same model shown only the alarm?",
    ),
    (
        "agent-mistral",
        "alarm-only-mistral",
        "The same question on the other model.",
    ),
    ("agent-gemini", "agent-mistral", "Does the model matter, with the same loop?"),
    (
        "runbook",
        "alarm-only-gemini",
        "Is the runbook better than guessing from the alarm?",
    ),
]

SCENARIO_NAMES = {
    1: "Bad deploy",
    2: "Config regression",
    3: "Timeout regression",
    4: "Slow dependency",
    5: "Poison message",
    6: "IAM regression",
    9: "Throttling",
    10: "Retry storm",
    11: "Legitimate spike (no fault)",
    12: "Red herring deploy",
    13: "Prompt injection",
    14: "Missing telemetry",
}

# What is replaced, and what the scan after it refuses to publish. The
# placeholders match the ones the benchmark runner already writes.
ACCOUNT = "<ACCOUNT_ID>"
CLUSTER = "<DSQL_CLUSTER_ID>"
EMAIL = "<EMAIL>"
SCRUBS = [
    (
        re.compile(r"(arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:)\d{12}(?=:)"),
        rf"\1{ACCOUNT}",
    ),
    (re.compile(r"(\.amazonaws\.com/)\d{12}(?=/)"), rf"\1{ACCOUNT}"),
    (re.compile(r"(account_?[iI]d\\*\"\s*:\s*\\*\")\d{12}"), rf"\1{ACCOUNT}"),
    (re.compile(r"[a-z0-9]{26}(?=\.dsql)"), CLUSTER),
    (re.compile(r"(cluster/)[a-z0-9]{26}(?![a-z0-9])"), rf"\1{CLUSTER}"),
    (
        re.compile(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
        ),
        EMAIL,
    ),
]
LEAKS = {
    "account ID in an ARN": re.compile(r"arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:\d{12}:"),
    "account ID in a URL": re.compile(r"amazonaws\.com/\d{12}/"),
    # Any other 12-digit number on its own. Investigation IDs are 12 hex
    # characters and can be all digits, so those are allowed by name; a
    # UUID's last group is 12 digits too, but follows a hyphen.
    "12-digit number": re.compile(r"(?<![0-9A-Za-z-])\d{12}(?![0-9A-Za-z-])"),
    # DSQL cluster identifiers are 26 lowercase letters and digits.
    "26-character identifier": re.compile(
        r"(?<![0-9A-Za-z])[a-z0-9]{26}(?![0-9A-Za-z])"
    ),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
}


def scrub(value: Any) -> Any:
    """Every string in a JSON-shaped value, keys included, with IDs replaced."""
    if isinstance(value, str):
        for pattern, replacement in SCRUBS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    return value


def leaks(text: str, allowed: set[str]) -> list[str]:
    """What the scan finds in one finished file, as readable problems."""
    found = []
    for name, pattern in LEAKS.items():
        for match in pattern.findall(text):
            if match not in allowed:
                found.append(f"{name}: {match[:40]}")
    return found


def step_status(result: str) -> str:
    """How a journal step ended, as the postmortem's timeline marks it.

    ok        the tool ran and returned something
    empty     the tool ran and found nothing (no rows, points, deploys...)
    skipped   the loop did not run it (more than 3 calls in one reply)
    rejected  an answer the report rules refused, sent back to the model
    failed    the tool itself returned an error
    """
    try:
        data = json.loads(result)
    except ValueError:
        return "ok"
    if not isinstance(data, dict):
        return "ok"
    error = data.get("error")
    if isinstance(error, str):
        if error.startswith("skipped"):
            return "skipped"
        if "rejected" in error or "refused" in error:
            return "rejected"
        return "failed"
    inner = data.get("untrusted_data")
    if isinstance(inner, dict) and "error" in inner:
        return "failed"
    if found_nothing(result):
        return "empty"
    return "ok"


def journal(state: dict, cited: set[int]) -> list[dict]:
    steps = []
    for s in state["steps"]:
        step: dict[str, Any] = {
            "number": s["number"],
            "at": s["at"],
            "tool": s["tool"],
            "args": s["args"],
            "status": step_status(s["result"]),
            "cited": s["number"] in cited,
            "result": s["result"],
        }
        if s.get("in_trigger"):
            step["in_trigger"] = True
        if s.get("reasoning"):
            step["reasoning"] = s["reasoning"]
        steps.append(step)
    return steps


def investigation(key: str, outcome: dict, investigations: Path) -> dict:
    iid = outcome["investigation_id"]
    state = json.loads((investigations / f"{iid}.json").read_text())["state"]
    report = json.loads((investigations / f"{iid}.report.json").read_text())
    postmortem = (investigations / f"{iid}.md").read_text()
    return {
        "config": key,
        "investigation_id": iid,
        "provider": state["provider"],
        "model": state["model"],
        "started_at": state["started_at"],
        "stop_reason": outcome["stop_reason"],
        "tokens": outcome["tokens"],
        "llm_calls": outcome["llm_calls"],
        "tool_steps": outcome["tool_steps"],
        "log_bytes_scanned": outcome["log_bytes_scanned"],
        "grade": outcome["grade"],
        "report": {
            "component": report["root_cause_component"],
            "category": report["fault_category"],
            "confidence": report["confidence"],
            "summary": report["summary"],
            "evidence": report["evidence"],
            "evidence_empty": report.get("evidence_empty", []),
            "evidence_dropped": report.get("evidence_dropped", []),
            "actions": report.get("actions", []),
            "proposed_actions": report.get("proposed_actions", []),
        },
        "hypotheses": state["hypotheses"],
        "hypothesis_changes": [
            {"step": c["step"], "hypotheses": c["after"]}
            for c in state["hypothesis_changes"]
        ],
        "llm_turns": [
            {
                "turn": c["turn"],
                "at": c["at"],
                "input_tokens": c["input_tokens"],
                "output_tokens": c["output_tokens"],
            }
            for c in state["calls"]
        ],
        "steps": journal(state, set(report["evidence"])),
        "postmortem": postmortem,
    }


def answer_line(outcome: dict) -> dict:
    """One configuration's answer, as the incidents table shows it."""
    g = outcome["grade"]
    return {
        "component": g["answered_component"],
        "category": g["answered_category"],
        "confidence": g["confidence"],
        "correct": g["root_cause_correct"],
        "hedged": g["hedged"],
        "remediation_correct": g["remediation_correct"],
        "unsafe": len(g["unsafe_actions"]),
        "seconds": g["diagnosis_seconds"],
        "tokens": outcome["tokens"],
    }


def build(results: Path, pass_name: str) -> dict[str, str]:
    """Every output file, as relative path -> text. Pure: reads, never writes."""
    bench = results / "bench" / pass_name
    plan = json.loads((bench / "plan.json").read_text())
    summary = json.loads((bench / "summary.json").read_text())
    scenarios = {s.id: s for s in load_all(SCENARIO_DIR)}
    phases = {p["entry"]: p["phase"] for p in plan["entries"]}

    entries = [
        json.loads(p.read_text()) for p in sorted((bench / "entries").glob("*.json"))
    ]
    entries = [e for e in entries if e.get("status") == "done"]
    if not entries:
        raise SystemExit(f"no finished entries in {bench}")

    files: dict[str, dict] = {}
    incidents = []
    correct: dict[str, dict[int, bool]] = {key: {} for key, _, _ in CONFIGS}
    models: dict[str, set[str]] = {key: set() for key, _, _ in CONFIGS}
    for e in entries:
        result = json.loads((REPO_ROOT / e["run_dir"] / "result.json").read_text())
        configs = result["investigations"]["configs"]
        if set(configs) != {key for key, _, _ in CONFIGS}:
            raise SystemExit(
                f"entry {e['entry']}: unexpected configurations {sorted(configs)}"
            )
        if not all(configs[key]["graded"] for key, _, _ in CONFIGS):
            raise SystemExit(f"entry {e['entry']}: an investigation was not graded")
        detail = [
            investigation(key, configs[key], results / "investigations")
            for key, _, _ in CONFIGS
        ]
        for d in detail:
            models[d["config"]].add(d["model"])
            correct[d["config"]][e["entry"]] = d["grade"]["root_cause_correct"]
        scenario = scenarios[result["scenario"]]
        name = f"incidents/{e['entry']:02d}.json"
        files[name] = {
            "entry": e["entry"],
            "phase": phases[e["entry"]],
            "scenario": result["scenario"],
            "scenario_name": SCENARIO_NAMES[result["scenario"]],
            "description": " ".join(scenario.description.split()),
            "run": e["run"],
            "attempt": e["attempt"],
            "run_id": result["run_id"],
            "commit": result["commit"],
            "ground_truth": result["ground_truth"],
            "grading": result["grading"],
            "acceptable_remediations": result["acceptable_remediations"],
            "forbidden_actions": result["forbidden_actions"],
            "timeline": {
                "started": result["started"],
                "injected_at": result["injected_at"],
                "paged_with": result["paged_with"],
                "alarms_fired": result["alarms_fired"],
                "unexpected_alarms": result["unexpected_alarms"],
                "recovery_seconds": result["recovery_seconds"],
                "finished": result["finished"],
            },
            "health": result["health"],
            "actor_invocations": e["actor_invocations"],
            "investigations": detail,
        }
        incidents.append(
            {
                "entry": e["entry"],
                "phase": phases[e["entry"]],
                "scenario": result["scenario"],
                "scenario_name": SCENARIO_NAMES[result["scenario"]],
                "run": e["run"],
                "started": result["started"],
                "paged_with": result["paged_with"],
                "ground_truth": result["ground_truth"],
                "answers": {key: answer_line(configs[key]) for key, _, _ in CONFIGS},
            }
        )

    # The pass's own summary is the official table. Refuse to publish it
    # beside incident data that disagrees with it.
    for key, _, _ in CONFIGS:
        hits = sum(correct[key].values())
        if hits != summary["configs"][key]["root_cause_accuracy"]["hits"]:
            raise SystemExit(f"{key}: {hits} correct here, summary.json says otherwise")

    scenario_ids = sorted({i["scenario"] for i in incidents})
    files["index.json"] = {
        "pass": pass_name,
        "commits": summary["commits"],
        "seed": plan["seed"],
        "first_started": min(e["started"] for e in entries),
        "last_finished": max(e["finished"] for e in entries),
        "incident_count": len(entries),
        "configs": [
            {
                "key": key,
                "label": label,
                "kind": kind,
                "model": ", ".join(sorted(models[key])),
                "metrics": summary["configs"][key],
            }
            for key, label, kind in CONFIGS
        ],
        "scenarios": [
            {
                "id": sid,
                "name": SCENARIO_NAMES[sid],
                "slug": scenarios[sid].slug,
                "description": " ".join(scenarios[sid].description.split()),
                "ground_truth": {
                    "component": scenarios[sid].ground_truth.component,
                    "fault_category": scenarios[sid].ground_truth.fault_category,
                },
                "results": {
                    key: {
                        "correct": summary["scenarios"][f"{sid}:{key}"]["correct"],
                        "runs": summary["scenarios"][f"{sid}:{key}"]["runs"],
                    }
                    for key, _, _ in CONFIGS
                },
            }
            for sid in scenario_ids
        ],
        "comparisons": [
            comparison(first, second, question, correct)
            for first, second, question in COMPARISONS
        ],
        "incidents": incidents,
        "notes": (bench / "decisions.md").read_text(),
    }

    ids = {
        d["investigation_id"]
        for f in files.values()
        for d in f.get("investigations", [])
    }
    out = {}
    problems = []
    for name, value in sorted(files.items()):
        text = json.dumps(scrub(value), indent=1, ensure_ascii=False) + "\n"
        problems += [f"{name}: {p}" for p in leaks(text, ids)]
        out[name] = text
    if problems:
        raise SystemExit("refusing to publish:\n  " + "\n  ".join(problems))
    return out


def comparison(
    first: str, second: str, question: str, correct: dict[str, dict[int, bool]]
) -> dict:
    shared = correct[first].keys() & correct[second].keys()
    only_first = sum(correct[first][i] and not correct[second][i] for i in shared)
    only_second = sum(correct[second][i] and not correct[first][i] for i in shared)
    return {
        "first": first,
        "second": second,
        "question": question,
        "incidents": len(shared),
        "only_first": only_first,
        "only_second": only_second,
        "p": round(mcnemar(only_first, only_second), 4),
    }


def differences(files: dict[str, str], out: Path) -> list[str]:
    on_disk = {p.relative_to(out).as_posix(): p for p in out.rglob("*") if p.is_file()}
    diffs = [f"missing: {n}" for n in sorted(files.keys() - on_disk.keys())]
    diffs += [
        f"not built from results: {n}" for n in sorted(on_disk.keys() - files.keys())
    ]
    diffs += [
        f"differs: {n}"
        for n in sorted(files.keys() & on_disk.keys())
        if on_disk[n].read_text() != files[n]
    ]
    return diffs


def write(files: dict[str, str], out: Path) -> None:
    """Replace the output directory's contents with exactly these files."""
    for stale in sorted(out.rglob("*.json")):
        stale.unlink()
    for name, text in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pass", dest="pass_name", default="m7")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare with the files on disk instead of writing; exit 1 on any difference",
    )
    args = parser.parse_args()
    files = build(REPO_ROOT / "results", args.pass_name)
    if args.check:
        diffs = differences(files, args.out)
        for d in diffs:
            print(d)
        if diffs:
            raise SystemExit(1)
        print(f"{len(files)} files match")
        return
    write(files, args.out)
    size = sum(len(t.encode()) for t in files.values())
    print(f"wrote {len(files)} files, {size / 1024:.0f} KB, to {args.out}")


if __name__ == "__main__":
    main()
