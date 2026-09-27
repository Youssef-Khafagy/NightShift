"""The quiet gap between incidents and the check that it held.

The gap is what keeps one incident's leftovers out of the next
investigation, so each of its three checks must be able to refuse on its
own, and the leak check must be able to find a leak.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from chaos import quiet
from evaluation.contamination import leftovers

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
GAP = timedelta(minutes=quiet.QUIET_GAP_MINUTES)


def test_quiet_when_all_three_agree():
    assert quiet.gap_problems(NOW, NOW - GAP, 0, []) == []
    assert quiet.gap_problems(NOW, None, 0, []) == []


def test_each_check_refuses_on_its_own():
    recent = quiet.gap_problems(NOW, NOW - GAP + timedelta(minutes=10), 0, [])
    assert len(recent) == 1 and "10 more minutes" in recent[0]
    assert "Lambda invocations" in quiet.gap_problems(NOW, None, 3, [])[0]
    assert "write events" in quiet.gap_problems(NOW, None, 0, ["UpdateAlias at x"])[0]


def test_the_marker_round_trips_and_a_refused_run_leaves_it_alone(
    tmp_path, monkeypatch
):
    marker = tmp_path / "last_write.json"
    monkeypatch.setattr(quiet, "_touched", False)
    quiet.touch_if_started("run ended", marker)
    assert quiet.last_write(marker) is None
    quiet.touch("warm-up", marker, now=NOW)
    assert quiet.last_write(marker) == NOW
    quiet.touch_if_started("run ended", marker)
    assert quiet.last_write(marker) > NOW


def test_only_project_writes_count():
    class Trail:
        def lookup_events(self, **kwargs):
            assert kwargs["LookupAttributes"][0]["AttributeValue"] == "false"
            return {
                "Events": [
                    {
                        "EventName": "UpdateAlias",
                        "EventTime": NOW,
                        "Resources": [{"ResourceName": "nightshift-orders"}],
                    },
                    {
                        "EventName": "PutObject",
                        "EventTime": NOW,
                        "Resources": [{"ResourceName": "someone-else"}],
                    },
                ]
            }

    assert quiet.project_writes_since(Trail(), NOW - GAP, NOW) == [
        "UpdateAlias at 12:00:00Z"
    ]


def step(number: int, result: str) -> dict:
    return {"number": number, "result": result}


def test_a_leftover_is_found_in_every_timestamp_format():
    previous = NOW - GAP
    before = previous - timedelta(minutes=1)
    steps = [
        step(1, f'{{"at": "{before:%Y-%m-%dT%H:%M:%SZ}"}}'),
        step(2, f'{{"at": "{before:%Y-%m-%dT%H:%M:%S}.000+00:00"}}'),
        step(3, f'{{"@timestamp": "{before:%Y-%m-%d %H:%M:%S}.000"}}'),
        step(4, f'{{"at": "{NOW:%Y-%m-%dT%H:%M:%SZ}"}}'),
        step(5, '{"since": "more than 30 minutes ago", "points": [["11:59:00Z", 1]]}'),
    ]
    assert leftovers(steps, previous) == [1, 2, 3]
