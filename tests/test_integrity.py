"""The agent must never be able to learn the answer from the store itself.

Two rules, enforced here the way the metric and alarm budgets are:

1. Nothing under src/ (the code deployed to Lambda) imports the chaos package.
2. Nothing under src/ contains the words chaos, inject, fault or scenario.
   A log line, error message or even a comment with one of those words
   would tell an investigator the incident was staged, and which part.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BANNED = re.compile(r"\b(chaos|inject(ed|ion|s)?|faults?|scenarios?)\b", re.IGNORECASE)


def store_files() -> list[Path]:
    files = sorted((REPO_ROOT / "src").rglob("*.py"))
    assert files, "found no store source files"
    return files


def test_the_store_never_imports_the_chaos_package():
    for path in store_files():
        text = path.read_text()
        assert not re.search(r"^\s*(from|import)\s+chaos\b", text, re.MULTILINE), path


def test_the_store_never_says_the_words():
    hits = []
    for path in store_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if BANNED.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not hits, "banned words in deployed code:\n" + "\n".join(hits)


def test_the_word_check_can_fail():
    """A regex that never matches would pass everything."""
    assert BANNED.search("the payment scenario")
    assert BANNED.search("Injected a FAULT")
    assert not BANNED.search("default injector_free faultless")


def test_the_agent_never_imports_the_chaos_package():
    """The agent reads the store the way an on-call engineer would. Importing
    chaos would put scenario files and ground truth one attribute away."""
    files = sorted((REPO_ROOT / "agent").rglob("*.py"))
    assert files, "found no agent source files"
    for path in files:
        text = path.read_text()
        assert not re.search(
            r"^\s*(from|import)\s+(chaos|results)\b", text, re.MULTILINE
        ), path
        assert "results/chaos" not in text, path


def test_chaos_rollback_reasons_do_not_give_the_game_away():
    """Rollback reasons are written to the deployments table, which the agent
    reads. On 2026-09-23 they named the scenario ("recovery after scenario
    run 02-config-regression-..."), which is the answer key in plain text."""
    from chaos.actions import ROLLBACK_REASON

    assert not BANNED.search(ROLLBACK_REASON)
    assert not re.search(r"\d{8}T\d{6}Z", ROLLBACK_REASON)  # no run IDs
    for path in (REPO_ROOT / "chaos").glob("*.py"):
        text = path.read_text()
        assert "restore(f" not in text, f"{path.name}: a formatted rollback reason"
        assert not re.search(r"restore\(\s*\"", text), f"{path.name}: a literal reason"
