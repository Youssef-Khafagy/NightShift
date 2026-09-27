"""No tool may look further back than LOOKBACK_MINUTES.

The benchmark's integrity rests on it: the runner waits longer than the
lookback between incidents (chaos/quiet.py), so if any tool could reach
further, an investigation could read the previous incident's leftovers, as
two of five did in M5 when the deployments tool reached back 7 days. These
tests make widening a window fail CI instead of quietly contaminating results.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.config import LOOKBACK_MINUTES, AgentConfig
from agent.tools import TOOLS, aws_read
from agent.tools.context import ToolContext, ToolError
from chaos.quiet import QUIET_GAP_MINUTES

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def context() -> ToolContext:
    return ToolContext(session=None, now=lambda: NOW)


def test_the_quiet_gap_is_longer_than_the_lookback():
    assert QUIET_GAP_MINUTES > LOOKBACK_MINUTES


def test_every_time_argument_is_minutes_and_capped():
    seen = 0
    for name, tool in TOOLS.items():
        props = tool.spec.parameters["properties"]
        assert not {"hours", "days", "start", "since"} & set(props), name
        if "minutes" in props:
            seen += 1
            assert props["minutes"]["maximum"] <= LOOKBACK_MINUTES, name
    # get_metrics, query_logs, get_traces, list_recent_deployments,
    # lookup_recent_changes. A check that found none would pass everything.
    assert seen == 5


def test_log_queries_are_capped_by_config_too():
    assert AgentConfig().max_log_query_minutes <= LOOKBACK_MINUTES


def test_a_direct_call_cannot_widen_the_window():
    """The scripted baseline calls tools without the schema check."""
    with pytest.raises(ToolError):
        aws_read.window_start(context(), LOOKBACK_MINUTES + 1)
    assert aws_read.window_start(context(), LOOKBACK_MINUTES) == NOW - timedelta(
        minutes=LOOKBACK_MINUTES
    )


def test_old_state_times_are_not_shown_exactly():
    ctx = context()
    old = NOW - timedelta(minutes=LOOKBACK_MINUTES + 1)
    recent = NOW - timedelta(minutes=5)
    assert (
        aws_read.recent_stamp(ctx, old) == f"more than {LOOKBACK_MINUTES} minutes ago"
    )
    assert aws_read.recent_stamp(ctx, recent) == "2026-09-26T11:55:00Z"
    assert (
        aws_read.recent_stamp(ctx, "2026-09-26T09:00:00.000+0000")
        == f"more than {LOOKBACK_MINUTES} minutes ago"
    )
