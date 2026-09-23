"""pause.py's idle check and destroy.py's grouping, without AWS."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_script", SCRIPTS / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pause():
    return load("pause")


@pytest.fixture(scope="module")
def destroy():
    return load("destroy")


IDLE = {
    "consumer_states": ["Disabled"],
    "eventbridge_rules": 0,
    "schedules": 0,
    "provisioned": {"orders": 0, "cart": 0},
    "invocations": 0.0,
    "empty_receives": 0.0,
}


def test_an_idle_store_is_paused(pause):
    assert pause.idle_problems(IDLE) == []


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"consumer_states": ["Enabled"]}, "queue trigger is Enabled"),
        ({"consumer_states": ["Disabling"]}, "queue trigger is Disabling"),
        ({"eventbridge_rules": 1}, "EventBridge rule"),
        ({"schedules": 2}, "Scheduler"),
        ({"provisioned": {"orders": 1}}, "orders has provisioned concurrency"),
        ({"invocations": 3.0}, "3 Lambda invocations"),
        ({"empty_receives": 42.0}, "42 SQS polls"),
    ],
)
def test_anything_that_runs_by_itself_is_reported(pause, override, fragment):
    problems = pause.idle_problems({**IDLE, **override})
    assert any(fragment in p for p in problems), problems


def test_destroy_groups_by_consequence(destroy):
    grouped = destroy.group(
        [
            "aws_dsql_cluster",
            "aws_dynamodb_table",
            "aws_iam_openid_connect_provider",
            "aws_cloudwatch_metric_alarm",
            "aws_lambda_function",
            "aws_iam_role",
        ]
    )
    assert grouped["Data lost for good"] == ["aws_dsql_cluster", "aws_dynamodb_table"]
    assert grouped["CI stops working"] == ["aws_iam_openid_connect_provider"]
    assert grouped["Paging stops"] == ["aws_cloudwatch_metric_alarm"]
    assert grouped["Service and configuration"] == [
        "aws_lambda_function",
        "aws_iam_role",
    ]


def test_only_deletions_are_counted(destroy):
    plan = {
        "resource_changes": [
            {"type": "aws_sqs_queue", "change": {"actions": ["delete"]}},
            {"type": "aws_sqs_queue", "change": {"actions": ["no-op"]}},
            {
                "type": "aws_lambda_function",
                "change": {"actions": ["delete", "create"]},
            },
        ]
    }
    assert destroy.deleted_resource_types(plan) == ["aws_sqs_queue"]
