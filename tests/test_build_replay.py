"""The public replay data: what leaves the repo for the internet, and only that.

The dashboard's public pages are built from `dashboard/public/replay/`, which
`scripts/build_replay.py` writes from the committed benchmark results. These
tests hold it to three promises: the IDs are scrubbed, a scan refuses to
publish anything that still looks like one, and the committed files are
exactly what the results build.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_replay.py"
spec = importlib.util.spec_from_file_location("build_replay", SCRIPT)
assert spec and spec.loader
build_replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_replay)

FAKE_ACCOUNT = "123456789012"
FAKE_CLUSTER = "abcdefghij0123456789klmnop"  # 26 characters, like a real one


def test_scrub_replaces_ids_in_every_string_and_key():
    planted = {
        "arn": f"arn:aws:lambda:ca-central-1:{FAKE_ACCOUNT}:function:nightshift-orders",
        "url": f"https://sqs.ca-central-1.amazonaws.com/{FAKE_ACCOUNT}/nightshift-placed-orders",
        # A tool result is JSON inside a string, so its quotes arrive escaped.
        "nested": f'{{\\"accountId\\": \\"{FAKE_ACCOUNT}\\"}}',
        "endpoint": [f"{FAKE_CLUSTER}.dsql.ca-central-1.on.aws"],
        f"arn:aws:dsql:ca-central-1:{FAKE_ACCOUNT}:cluster/{FAKE_CLUSTER}": "key",
        "email": "someone+tag@example.com",
    }
    text = json.dumps(build_replay.scrub(planted))
    assert FAKE_ACCOUNT not in text
    assert FAKE_CLUSTER not in text
    assert "example.com" not in text
    assert text.count("<ACCOUNT_ID>") == 4
    assert text.count("<DSQL_CLUSTER_ID>") == 2
    assert "<EMAIL>" in text


def test_scrub_leaves_ordinary_text_alone():
    ordinary = [
        "fields @timestamp, @message | sort @timestamp desc",
        "product 33333333-3333-4333-8333-333333333333",
        "nightshift-orders-errors fired at 2026-09-30T15:43:29Z",
    ]
    assert build_replay.scrub(ordinary) == ordinary


def test_the_scan_finds_what_scrubbing_would_miss():
    allowed = {"618358364373"}  # an investigation ID that happens to be digits
    assert build_replay.leaks(f"owner {FAKE_ACCOUNT}", allowed) == [
        f"12-digit number: {FAKE_ACCOUNT}"
    ]
    assert build_replay.leaks(f"cluster {FAKE_CLUSTER}", allowed) == [
        f"26-character identifier: {FAKE_CLUSTER}"
    ]
    assert build_replay.leaks("mail a.b@example.org", allowed) == [
        "email address: a.b@example.org"
    ]
    clean = (
        "investigation 618358364373, product 33333333-3333-4333-8333-333333333333, "
        "arn:aws:lambda:ca-central-1:<ACCOUNT_ID>:function:x, <DSQL_CLUSTER_ID>"
    )
    assert build_replay.leaks(clean, allowed) == []


def test_build_refuses_to_publish_when_the_scan_finds_something(monkeypatch):
    """Proves the scan is wired into build(), not only defined: with
    scrubbing replaced by something that plants an ID, nothing is returned."""

    def plant(value):
        return {"leak": f"owner {FAKE_ACCOUNT}", "value": value}

    monkeypatch.setattr(build_replay, "scrub", plant)
    with pytest.raises(SystemExit, match="refusing to publish"):
        build_replay.build(REPO_ROOT / "results", "m7")


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (
            '{"tool": "get_metrics", "truncated": false, "untrusted_data": {"points": [1]}}',
            "ok",
        ),
        (
            '{"tool": "query_logs", "truncated": false, "untrusted_data": {"rows": []}}',
            "empty",
        ),
        ('{"error": "skipped: at most 3 calls per reply"}', "skipped"),
        ('{"error": "finish_investigation rejected", "problems": ["x"]}', "rejected"),
        (
            '{"tool": "get_alarm", "truncated": false, "untrusted_data": {"error": "AccessDenied"}}',
            "failed",
        ),
        ('{"ok": "3 hypotheses recorded"}', "ok"),
        ("not json", "ok"),
    ],
)
def test_each_step_is_marked_as_the_postmortem_marks_it(result, status):
    assert build_replay.step_status(result) == status


def test_the_committed_replay_is_exactly_what_the_results_build():
    """The CI check. If this fails, run `python scripts/build_replay.py` and
    commit the result, or find out why the results changed."""
    files = build_replay.build(REPO_ROOT / "results", "m7")
    assert build_replay.differences(files, build_replay.OUT) == []


def test_the_published_comparisons_are_computed_from_the_incidents():
    index = json.loads(build_replay.build(REPO_ROOT / "results", "m7")["index.json"])
    pairs = {
        (c["first"], c["second"]): (c["only_first"], c["only_second"])
        for c in index["comparisons"]
    }
    # The same splits LEARNING.md section 14 reports, worked out by hand.
    assert pairs[("agent-gemini", "runbook")] == (5, 8)
    assert pairs[("agent-gemini", "alarm-only-gemini")] == (14, 0)
    assert pairs[("agent-gemini", "agent-mistral")] == (15, 2)
    assert pairs[("agent-mistral", "alarm-only-mistral")] == (5, 3)
    assert pairs[("runbook", "alarm-only-gemini")] == (18, 1)
    assert all(c["incidents"] == 36 for c in index["comparisons"])
