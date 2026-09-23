"""The alarm ledger in COST.md must stay inside the free allowance.

CloudWatch gives 10 alarm metrics free, and a metric math alarm is billed for
every metric in its expression. The ledger is where each alarm is budgeted
before Terraform creates it. This checks the ledger itself, and that the
alarms defined in terraform/alarms.tf are exactly the ledger's rows.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FREE_ALARM_METRICS = 10

# | `name` | `Namespace` | `Metric` | dims | stat | count | why | status |
ROW = re.compile(
    r"^\| `([a-z0-9-]+)` \| `([A-Za-z/]+)` \| `(\w+)` \|[^|]*\|[^|]*\| (\d+) \|"
)


def ledger_rows() -> list[tuple[str, str, str, int]]:
    text = (REPO_ROOT / "COST.md").read_text()
    start = text.index("### Alarm ledger")
    section = text[start : text.index("\n### ", start + 1)]
    return [
        (m[1], m[2], m[3], int(m[4]))
        for line in section.splitlines()
        if (m := ROW.match(line))
    ]


def test_the_ledger_parses():
    """A reformatted table must fail here, not become an empty ledger."""
    assert len(ledger_rows()) == 10


def test_the_ledger_fits_the_free_allowance():
    assert sum(count for *_, count in ledger_rows()) <= FREE_ALARM_METRICS


def test_alarm_names_are_unique():
    names = [name for name, *_ in ledger_rows()]
    assert len(names) == len(set(names))


def test_the_allocated_total_matches_the_rows():
    text = (REPO_ROOT / "COST.md").read_text()
    section = text[text.index("### Alarm ledger") :]
    allocated = re.search(r"\| \*\*Allocated\*\* \|(?: \|)* \*\*(\d+)\*\* \|", section)
    assert allocated, "the Allocated row is missing"
    assert int(allocated[1]) == sum(count for *_, count in ledger_rows())


# `"orders-errors" = {` inside the alarms map in terraform/alarms.tf.
TF_ALARM = re.compile(r'^\s+"([a-z0-9-]+)" = \{', re.MULTILINE)


def terraform_alarms() -> set[str]:
    text = (REPO_ROOT / "terraform" / "alarms.tf").read_text()
    block = text[text.index("  alarms = {") :]
    return set(TF_ALARM.findall(block))


def test_terraform_alarms_match_the_ledger():
    """An alarm in Terraform without a ledger row, or a ledger row with no
    alarm, fails here. Adding an alarm means budgeting it first."""
    ledger = {name for name, *_ in ledger_rows()}
    tf = terraform_alarms()
    assert tf, "no alarms parsed from terraform/alarms.tf"
    assert tf - ledger == set(), f"alarms missing from the ledger: {tf - ledger}"
    assert ledger - tf == set(), f"ledger rows with no alarm: {ledger - tf}"
