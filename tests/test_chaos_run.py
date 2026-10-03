"""The chaos runner refuses to inject into a system with no traffic.

On 2026-09-23 a scenario 1 run injected a bad deploy while its warm-up load
had already exited (DSQL_ENDPOINT was unset). With no checkouts, the broken
code never ran, no alarm fired, and the run looked like a detection
failure. These tests pin the check that stops that before injection.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from chaos.run import dead_loads


def test_running_loads_are_alive():
    assert dead_loads([None, None]) == []


def test_a_failed_load_is_dead():
    assert dead_loads([None, 1]) == [1]


def test_a_clean_early_exit_is_still_dead():
    # Every load outlives the phase that checks it, so even exit 0 means
    # traffic stopped too soon.
    assert dead_loads([0]) == [0]


def test_no_loads_is_not_an_error():
    assert dead_loads([]) == []


def test_switching_the_trigger_never_makes_the_plan_dirty():
    """On 2026-09-23 the first agent run refused to start: the plan check
    knew the consumer was on but not the trigger. Since M8 Terraform ignores
    the trigger's state, as it does the consumer's, so a run with the agent
    plans with no extra variables, and the rule says so in Terraform."""
    from chaos.run import run_vars

    assert run_vars(False) == []
    assert run_vars(True) == []
    agent_tf = (
        Path(__file__).resolve().parent.parent / "terraform" / "agent.tf"
    ).read_text()
    rule = agent_tf[
        agent_tf.index('resource "aws_cloudwatch_event_rule" "alarm_to_agent"') :
    ]
    rule = rule[: rule.index("\n}\n")]
    assert "ignore_changes = [state]" in rule
