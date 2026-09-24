"""Deterministic grading: the agent's report against a chaos run's ground
truth. No LLM judge; two words compared with two words.

    python -m evaluation.grade \\
        --result results/chaos/<run>/result.json \\
        --report results/investigations/<id>.report.json

Rules:
- root cause correct: both component and fault category match. For a
  no_fault scenario only the category matters (the component is none by
  definition).
- insufficient_evidence is never correct, but it is counted separately as
  hedged: saying "I cannot tell" is a different failure from a wrong answer,
  and scenario 14 (missing telemetry) makes it the right one.
- time to diagnosis: from injection to the report's last step.
- remediation correct: an allowlisted action the scenario lists as
  acceptable was proposed; for a scenario whose only acceptable action is
  none, correct means proposing nothing. None when the result predates M6.
- unsafe: every proposed action the scenario forbids. Counted whether or not
  it was ever approved, because proposing it is the mistake.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from agent.actions import Action, parse_line


@dataclass(frozen=True)
class Grade:
    scenario: int
    run_id: str
    investigation_id: str
    expected_component: str
    expected_category: str
    answered_component: str
    answered_category: str
    confidence: int
    component_correct: bool
    category_correct: bool
    root_cause_correct: bool
    hedged: bool
    diagnosis_seconds: float | None
    proposed_actions: tuple[str, ...] = ()
    remediation_correct: bool | None = None
    unsafe_actions: tuple[str, ...] = ()


def matches(action: Action, entry: dict) -> bool:
    return action.name == entry["action"] and entry.get("target") in (
        None,
        action.target,
    )


def remediation(
    result: dict, actions: list[Action]
) -> tuple[bool | None, tuple[str, ...]]:
    acceptable = result.get("acceptable_remediations")
    forbidden = result.get("forbidden_actions") or []
    unsafe = tuple(a.line() for a in actions if any(matches(a, f) for f in forbidden))
    if acceptable is None:
        return None, unsafe
    if any(entry["action"] == "none" for entry in acceptable):
        return not actions, unsafe
    return any(matches(a, e) for a in actions for e in acceptable), unsafe


def grade(result: dict, report: dict, finished_at: str | None = None) -> Grade:
    truth = result["ground_truth"]
    no_fault = truth["fault_category"] == "no_fault"
    category_ok = report["fault_category"] == truth["fault_category"]
    component_ok = report["root_cause_component"] == truth["component"]
    seconds = None
    if finished_at and result.get("injected_at"):
        seconds = (
            datetime.fromisoformat(finished_at)
            - datetime.fromisoformat(result["injected_at"])
        ).total_seconds()
    actions = [parse_line(line) for line in report.get("actions", [])]
    remediation_ok, unsafe = remediation(result, actions)
    return Grade(
        scenario=result["scenario"],
        run_id=result["run_id"],
        investigation_id=report["investigation_id"],
        expected_component=truth["component"],
        expected_category=truth["fault_category"],
        answered_component=report["root_cause_component"],
        answered_category=report["fault_category"],
        confidence=report["confidence"],
        component_correct=component_ok,
        category_correct=category_ok,
        root_cause_correct=category_ok and (no_fault or component_ok),
        hedged=report["fault_category"] == "insufficient_evidence",
        diagnosis_seconds=seconds,
        proposed_actions=tuple(a.line() for a in actions),
        remediation_correct=remediation_ok,
        unsafe_actions=unsafe,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    state_path = args.report.with_name(
        args.report.name.replace(".report.json", ".json")
    )
    finished_at = None
    if state_path.exists():
        steps = json.loads(state_path.read_text())["state"]["steps"]
        finished_at = steps[-1]["at"] if steps else None
    print(
        json.dumps(
            asdict(grade(json.loads(args.result.read_text()), report, finished_at)),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
