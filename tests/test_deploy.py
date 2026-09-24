"""The deploy step: moves, records, and rolls back on failure.

A deploy that fails must leave customers on the previous version and leave a
record saying so. These tests drive the whole deploy against a fake Lambda
client and a moto DynamoDB table, with the smoke test replaced by a switch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ops import deployments


@pytest.fixture(scope="module")
def deploy_mod():
    spec = importlib.util.spec_from_file_location(
        "deploy_script", SCRIPTS / "deploy.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeLambda:
    """Aliases in a dict. `fail_on` makes moving that service's alias raise."""

    def __init__(self, versions, fail_on=None):
        self.versions = dict(versions)
        self.fail_on = fail_on

    def get_alias(self, FunctionName, Name):
        return {
            "FunctionVersion": self.versions[FunctionName.removeprefix("nightshift-")]
        }

    def update_alias(self, FunctionName, Name, FunctionVersion):
        service = FunctionName.removeprefix("nightshift-")
        if service == self.fail_on:
            raise RuntimeError(f"cannot move {service}")
        self.versions[service] = FunctionVersion


@pytest.fixture
def table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="ca-central-1")
        yield dynamodb.create_table(
            TableName=deployments.TABLE,
            KeySchema=[
                {"AttributeName": "service", "KeyType": "HASH"},
                {"AttributeName": "deployed_at", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "service", "AttributeType": "S"},
                {"AttributeName": "deployed_at", "AttributeType": "S"},
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
        )


CURRENT = {
    "cart": "10",
    "payments": "5",
    "orders": "16",
    "fulfillment": "5",
    "hello": "9",
    "agent": "1",
    "actor": "1",
}


def rows(table):
    return sorted(table.scan()["Items"], key=lambda r: r["deployed_at"])


def test_plan_moves_only_what_changed_leaves_first(deploy_mod):
    target = {**CURRENT, "orders": "17", "cart": "11"}
    assert deploy_mod.plan_moves(CURRENT, target) == [
        ("cart", "10", "11"),
        ("orders", "16", "17"),
    ]


def test_a_service_with_no_version_is_an_error_not_a_skip(deploy_mod):
    target = {k: v for k, v in CURRENT.items() if k != "payments"}
    with pytest.raises(ValueError, match="payments"):
        deploy_mod.plan_moves(CURRENT, target)


def test_nothing_to_deploy_still_runs_the_smoke_test(deploy_mod, table):
    ran = []
    code = deploy_mod.deploy(
        FakeLambda(CURRENT),
        table,
        dict(CURRENT),
        sha="abc",
        actor="ci",
        smoke=lambda: ran.append(1) or True,
    )
    assert (code, ran, rows(table)) == (0, [1], [])


def test_a_good_deploy_moves_and_records(deploy_mod, table):
    lam = FakeLambda(CURRENT)
    target = {**CURRENT, "orders": "17", "cart": "11"}
    code = deploy_mod.deploy(
        lam, table, target, sha="abc", actor="ci", smoke=lambda: True
    )

    assert code == 0
    assert lam.versions == target
    recorded = rows(table)
    assert [(r["service"], r["previous"], r["new"], r["kind"]) for r in recorded] == [
        ("cart", "10", "11", "deploy"),
        ("orders", "16", "17", "deploy"),
    ]
    assert all(r["git_sha"] == "abc" and r["actor"] == "ci" for r in recorded)


def test_a_failed_smoke_test_puts_every_alias_back_and_says_so(deploy_mod, table):
    lam = FakeLambda(CURRENT)
    target = {**CURRENT, "orders": "17", "cart": "11"}
    code = deploy_mod.deploy(
        lam, table, target, sha="abc", actor="ci", smoke=lambda: False
    )

    assert code == 1
    assert lam.versions == CURRENT, "customers must be back on the previous versions"
    kinds = [(r["service"], r["kind"], r["new"]) for r in rows(table)]
    assert ("orders", "auto-rollback", "16") in kinds
    assert ("cart", "auto-rollback", "10") in kinds
    assert all(
        r.get("reason") == "smoke test failed"
        for r in rows(table)
        if r["kind"] == "auto-rollback"
    )


def test_a_failed_alias_move_rolls_back_the_ones_already_moved(deploy_mod, table):
    """cart moves first, then orders fails: cart must go back, and the smoke
    test must not run against a half-deployed system."""
    lam = FakeLambda(CURRENT, fail_on="orders")
    target = {**CURRENT, "orders": "17", "cart": "11"}
    ran = []
    code = deploy_mod.deploy(
        lam, table, target, sha="abc", actor="ci", smoke=lambda: ran.append(1) or True
    )

    assert code == 1
    assert ran == []
    assert lam.versions == CURRENT
    assert [(r["service"], r["kind"]) for r in rows(table)] == [
        ("cart", "deploy"),
        ("cart", "auto-rollback"),
    ]


def test_a_row_is_never_overwritten(table):
    deployments.record(
        table,
        service="orders",
        previous="1",
        new="2",
        kind="deploy",
        actor="ci",
        at="2026-09-23T00:00:00.000+00:00",
    )
    with pytest.raises(table.meta.client.exceptions.ConditionalCheckFailedException):
        deployments.record(
            table,
            service="orders",
            previous="2",
            new="3",
            kind="deploy",
            actor="ci",
            at="2026-09-23T00:00:00.000+00:00",
        )


def test_history_is_newest_first(table):
    for i, at in enumerate(
        [
            "2026-09-23T01:00:00.000+00:00",
            "2026-09-23T03:00:00.000+00:00",
            "2026-09-23T02:00:00.000+00:00",
        ]
    ):
        deployments.record(
            table,
            service="orders",
            previous=str(i),
            new=str(i + 1),
            kind="deploy",
            actor="ci",
            at=at,
        )
    assert [r["deployed_at"][11:13] for r in deployments.history(table, "orders")] == [
        "03",
        "02",
        "01",
    ]


class FailsGoingBack(FakeLambda):
    """Moves forward fine; refuses to move `service` back to `version`."""

    def __init__(self, versions, service, version):
        super().__init__(versions)
        self.stuck = (service, version)

    def update_alias(self, FunctionName, Name, FunctionVersion):
        service = FunctionName.removeprefix("nightshift-")
        if (service, FunctionVersion) == self.stuck:
            raise RuntimeError(f"cannot move {service} back")
        super().update_alias(FunctionName, Name, FunctionVersion)


def test_one_stuck_rollback_does_not_stop_the_others(deploy_mod, table, capsys):
    """orders cannot go back; cart still must, and the job must say so."""
    lam = FailsGoingBack(CURRENT, "orders", "16")
    target = {**CURRENT, "orders": "17", "cart": "11"}
    code = deploy_mod.deploy(
        lam, table, target, sha="abc", actor="ci", smoke=lambda: False
    )

    assert code == 1
    assert lam.versions["cart"] == "10"
    assert lam.versions["orders"] == "17"
    err = capsys.readouterr().err
    assert "ROLLBACK FAILED orders" in err
    assert "Still on the new version: orders" in err
