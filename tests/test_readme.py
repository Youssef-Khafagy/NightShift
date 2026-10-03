"""The README's results table says exactly what the pass's summary says.

The project's rule is that every README number comes from a real run. The
numbers were typed by hand, so this reads them back out of the README and
compares each cell with results/bench/m7/summary.json, formatted the way
evaluation/summarize.py prints them.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = (REPO_ROOT / "README.md").read_text()
SUMMARY = json.loads((REPO_ROOT / "results/bench/m7/summary.json").read_text())

# The README's column order.
CONFIGS = [
    "agent-gemini",
    "agent-mistral",
    "alarm-only-gemini",
    "alarm-only-mistral",
    "runbook",
]


def row(label: str) -> list[str]:
    for line in README.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == label:
            return cells[1:]
    raise AssertionError(f"README has no table row {label!r}")


def metrics(key: str) -> dict:
    return SUMMARY["configs"][key]


def accuracy(m: dict) -> str:
    r = m["root_cause_accuracy"]
    low, high = r["ci95"]
    return (
        f"{r['hits']}/{r['of']}, {r['rate'] * 100:.0f}% "
        f"({low * 100:.0f} to {high * 100:.0f})"
    )


def tokens(m: dict) -> str:
    mean = m["tokens"]["mean"]
    return f"{mean / 1000:.1f}K" if mean else "0"


def test_every_results_cell_matches_the_summary():
    expected = {
        "Root cause right (95% interval)": accuracy,
        "Hedged (insufficient evidence)": lambda m: str(m["hedged"]["hits"]),
        "Action proposed with no fault present": lambda m: (
            f"{m['false_action_on_no_fault']['hits']} of "
            f"{m['false_action_on_no_fault']['of']}"
        ),
        "Unsafe proposals (target 0)": lambda m: str(m["unsafe_proposals"]),
        "Mean tokens per investigation": tokens,
    }
    for label, cell in expected.items():
        assert row(label) == [cell(metrics(k)) for k in CONFIGS], label


def test_the_results_are_labelled_with_the_commit_they_came_from():
    assert SUMMARY["commits"] == ["5641bb7"]
    assert not SUMMARY["mixed_commits"]
    assert "at commit `5641bb7`" in README
