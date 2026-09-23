"""Scenario files: every one validates, and the rules bite when broken."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from chaos.schema import Scenario


def valid(**overrides):
    base = {
        "id": 1,
        "slug": "x",
        "description": "d",
        "load_rate": 1,
        "warm_up_seconds": 60,
        "inject": [{"do": "set_env", "args": {}}],
        "expected_alarms": ["orders-errors"],
        "alarm_wait_seconds": 300,
        "ground_truth": {"component": "orders", "fault_category": "bad_deploy"},
        "acceptable_remediations": [{"action": "rollback_alias", "target": "orders"}],
        "forbidden_actions": [{"action": "redrive_dlq"}],
        "recover": [{"do": "restore"}],
        "health_check": ["smoke_test", "alarms_ok", "plan_clean"],
    }
    base.update(overrides)
    return base


def test_a_valid_scenario_passes():
    Scenario.model_validate(valid())


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        (
            {"ground_truth": {"component": "orders", "fault_category": "made_up"}},
            "fault_category",
        ),
        ({"inject": []}, "must inject"),
        ({"health_check": ["smoke_test"]}, "plan clean"),
        (
            {"forbidden_actions": [{"action": "rollback_alias", "target": "orders"}]},
            "both acceptable and forbidden",
        ),
        ({"unexpected": 1}, "Extra inputs"),
        ({"inject": [{"do": "rm_rf", "args": {}}]}, "do"),
    ],
)
def test_rules_bite(overrides, fragment):
    with pytest.raises(ValidationError, match=fragment):
        Scenario.model_validate(valid(**overrides))


def test_no_fault_rules():
    ok = valid(
        inject=[{"do": "load", "args": {"rate": 3}}],
        ground_truth={"component": "none", "fault_category": "no_fault"},
        acceptable_remediations=[{"action": "none"}],
        expected_alarms=[],
    )
    Scenario.model_validate(ok)
    with pytest.raises(ValidationError, match="only add load"):
        Scenario.model_validate({**ok, "inject": [{"do": "set_env", "args": {}}]})
    with pytest.raises(ValidationError, match="only acceptable action is none"):
        Scenario.model_validate(
            {
                **ok,
                "acceptable_remediations": [
                    {"action": "rollback_alias", "target": "orders"}
                ],
            }
        )
