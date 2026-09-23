"""Injections are undone exactly, and a dry run writes nothing.

Recovery is only trustworthy if it puts back what was there, byte for byte:
otherwise Terraform sees drift, or worse, the next Terraform publish ships
the changed $LATEST. These tests drive the injector against fakes that
record every write.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from chaos import actions
from chaos import run as runner


class Waiter:
    def wait(self, **kwargs):
        pass


class FakeLambda:
    def __init__(self):
        self.env = {
            "CART_TABLE_NAME": "nightshift-cart",
            "POWERTOOLS_SERVICE_NAME": "cart",
        }
        self.code = b"original"
        self.alias = {"cart": "13", "orders": "17"}
        self.version = 20
        self.writes = []

    def get_function_configuration(self, FunctionName):
        return {"Environment": {"Variables": dict(self.env)}}

    def update_function_configuration(self, FunctionName, Environment):
        self.writes.append(("config", FunctionName))
        self.env = dict(Environment["Variables"])

    def update_function_code(self, FunctionName, ZipFile):
        self.writes.append(("code", FunctionName))
        self.code = ZipFile

    def publish_version(self, FunctionName):
        self.writes.append(("publish", FunctionName))
        self.version += 1
        return {"Version": str(self.version)}

    def get_alias(self, FunctionName, Name):
        return {"FunctionVersion": self.alias[FunctionName.removeprefix("nightshift-")]}

    def update_alias(self, FunctionName, Name, FunctionVersion):
        self.writes.append(("alias", FunctionName, FunctionVersion))
        self.alias[FunctionName.removeprefix("nightshift-")] = FunctionVersion

    def get_waiter(self, name):
        return Waiter()


class FakeTable:
    def __init__(self):
        self.rows = []

    def put_item(self, Item, ConditionExpression):
        self.rows.append(Item)


class FakeSQS:
    def __init__(self):
        self.sent = []
        self.dlq = []
        self.deleted = []

    def get_queue_url(self, QueueName):
        return {"QueueUrl": f"https://sqs/{QueueName}"}

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append(MessageBody)

    def receive_message(self, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
        return {
            "Messages": [
                {"Body": b, "ReceiptHandle": f"h{i}"} for i, b in enumerate(self.dlq)
            ]
        }

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)


def injector(lam, sqs=None, table=None, dry_run=False, tmp_path=None):
    return actions.Injector(
        lam,
        sqs or FakeSQS(),
        table or FakeTable(),
        actor="tester",
        git_sha="abc1234",
        dry_run=dry_run,
        state_path=tmp_path / "state.json" if tmp_path else None,
    )


def test_set_env_publishes_moves_records_and_restores_exactly(tmp_path):
    lam, table = FakeLambda(), FakeTable()
    before = dict(lam.env)
    inj = injector(lam, table=table, tmp_path=tmp_path)

    inj.set_env("cart", "CART_TABLE_NAME", "nightshift-carts")
    assert lam.env["CART_TABLE_NAME"] == "nightshift-carts"
    assert lam.alias["cart"] == "21"
    assert (table.rows[0]["kind"], table.rows[0]["previous"], table.rows[0]["new"]) == (
        "deploy",
        "13",
        "21",
    )
    assert (
        json.loads((tmp_path / "state.json").read_text())[0]["original_env"] == before
    )

    inj.restore("recovery")
    assert lam.env == before, "the environment must come back exactly"
    assert lam.alias["cart"] == "13"
    assert table.rows[-1]["kind"] == "rollback"


def test_set_env_refuses_a_variable_that_does_not_exist(tmp_path):
    with pytest.raises(ValueError, match="no environment variable"):
        injector(FakeLambda(), tmp_path=tmp_path).set_env("cart", "NOT_THERE", "x")


def test_deploy_patch_ships_the_change_and_restores_terraforms_zip(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(actions, "BUILD", tmp_path)
    (tmp_path / "nightshift-orders.zip").write_bytes(b"terraform's own zip")
    lam = FakeLambda()
    inj = injector(lam, tmp_path=tmp_path)

    inj.deploy_patch(
        "orders",
        "app.py",
        'unit="Count", value=1)\n        logger.info(\n            "checkout complete"',
        'unit="Counts", value=1)\n        logger.info(\n            "checkout complete"',
    )
    shipped = zipfile.ZipFile(io.BytesIO(lam.code))
    assert 'unit="Counts"' in shipped.read("app.py").decode()
    assert lam.alias["orders"] == "21"

    inj.restore("recovery")
    assert lam.code == b"terraform's own zip"
    assert lam.alias["orders"] == "17"


def test_a_patch_that_does_not_match_exactly_once_is_refused():
    with pytest.raises(ValueError, match="exactly once"):
        actions.build_zip("orders", {"file": "app.py", "find": "import", "with": "x"})


def test_build_zip_lays_out_like_the_terraform_module():
    names = zipfile.ZipFile(io.BytesIO(actions.build_zip("cart"))).namelist()
    assert "app.py" in names
    assert "common/dsql.py" in names
    assert not any(n.endswith(".pyc") for n in names)


def test_a_dry_run_makes_no_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "BUILD", tmp_path)
    (tmp_path / "nightshift-cart.zip").write_bytes(b"z")
    lam, sqs, table = FakeLambda(), FakeSQS(), FakeTable()
    inj = injector(lam, sqs, table, dry_run=True, tmp_path=tmp_path)
    inj.set_env("cart", "CART_TABLE_NAME", "wrong")
    inj.send_message("nightshift-placed-orders", "{}")
    inj.restore("recovery")
    assert (lam.writes, sqs.sent, table.rows) == ([], [], [])
    assert not (tmp_path / "state.json").exists()


def test_draining_deletes_only_this_runs_messages(tmp_path):
    sqs = FakeSQS()
    inj = injector(FakeLambda(), sqs, tmp_path=tmp_path)
    inj.send_message("nightshift-placed-orders", '{"orderId": "x"}')
    sqs.dlq = ['{"order_id": "a real order"}', '{"orderId": "x"}']
    assert inj.drain_dlq_message("nightshift-placed-orders-dlq") == 1
    assert sqs.deleted == ["h1"]


def test_detection_times_and_firing():
    assert runner.firing({"a": "ALARM", "b": "OK", "c": "INSUFFICIENT_DATA"}) == {"a"}
    assert runner.detection({"a": 130.0, "b": 100.5}, injected_at=100.0) == {
        "a": 30.0,
        "b": 0.5,
    }


def test_a_third_party_change_leaves_no_deploy_rows(tmp_path):
    """Scenario 4: a real provider's slowdown is not one of our deploys."""
    lam, table = FakeLambda(), FakeTable()
    lam.alias["payments"] = "6"
    lam.env = {"PAYMENT_LATENCY_MS": "40"}
    inj = injector(lam, table=table, tmp_path=tmp_path)
    inj.set_env("payments", "PAYMENT_LATENCY_MS", "5000", record_deploy=False)
    inj.restore("recovery")
    assert table.rows == []
    assert lam.env == {"PAYMENT_LATENCY_MS": "40"}
    assert lam.alias["payments"] == "6"
