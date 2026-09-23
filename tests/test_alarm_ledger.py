"""The alarm ledger in COST.md must stay inside the free allowance.

CloudWatch gives 10 alarm metrics free, and a metric math alarm is billed for
every metric in its expression. The ledger is where each alarm is budgeted
before Terraform creates it. This checks the ledger itself; M3 step 5 adds
the check that every alarm in Terraform has a row here.
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
    assert len(ledger_rows()) == 9


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
