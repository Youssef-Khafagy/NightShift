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
