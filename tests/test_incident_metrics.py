"""scripts/incident_metrics.py: one incident's requests and errors per minute,
every minute of the run present, with silent minutes as zero."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "incident_metrics", REPO_ROOT / "scripts" / "incident_metrics.py"
)
assert spec and spec.loader
incident_metrics = importlib.util.module_from_spec(spec)
sys.modules["incident_metrics"] = incident_metrics
spec.loader.exec_module(incident_metrics)

RESULT = {
    "run_id": "01-bad-deploy-test",
    "started": "2026-09-24T02:30:13+00:00",
    "finished": "2026-09-24T02:33:30+00:00",
}


class CloudWatch:
    def __init__(self):
        self.calls = []

    def get_metric_statistics(self, **kwargs):
        self.calls.append(kwargs)
        minute = lambda m: datetime(2026, 9, 24, 2, m, tzinfo=UTC)
        if kwargs["MetricName"] == "Invocations":
            points = [(minute(30), 38.0), (minute(31), 60.0), (minute(33), 60.0)]
        else:
            points = [(minute(33), 36.0)]
        return {"Datapoints": [{"Timestamp": t, "Sum": v} for t, v in points]}


def test_the_window_covers_the_run_in_whole_minutes():
    start, end = incident_metrics.window(RESULT)
    assert start == datetime(2026, 9, 24, 2, 30, tzinfo=UTC)
    assert end == datetime(2026, 9, 24, 2, 34, tzinfo=UTC)


def test_every_minute_is_listed_and_a_silent_one_is_zero():
    cw = CloudWatch()
    data = incident_metrics.collect(cw, RESULT, "nightshift-orders")
    assert [p["minute"][11:16] for p in data["points"]] == [
        "02:30",
        "02:31",
        "02:32",
        "02:33",
    ]
    assert [p["invocations"] for p in data["points"]] == [38, 60, 0, 60]
    assert [p["errors"] for p in data["points"]] == [0, 0, 0, 36]


def test_it_asks_for_free_per_minute_sums_for_that_function_only():
    cw = CloudWatch()
    incident_metrics.collect(cw, RESULT, "nightshift-orders")
    assert {c["MetricName"] for c in cw.calls} == {"Invocations", "Errors"}
    for call in cw.calls:
        assert call["Period"] == 60
        assert call["Statistics"] == ["Sum"]
        assert call["Dimensions"] == [
            {"Name": "FunctionName", "Value": "nightshift-orders"}
        ]
