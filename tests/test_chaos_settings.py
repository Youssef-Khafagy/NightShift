"""The M7 injection primitives: each change is recorded before it is made,
undone exactly, and the IAM undo is confirmed by the policy simulator.
Also the scenario files' own promises: every code patch applies, every
note reads like a customer rather than a test."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from chaos import actions
from chaos import run as runner
from chaos.schema import load_all
from evaluation.grade import grade
from tests.test_integrity import BANNED

SCENARIOS = load_all(REPO_ROOT / "chaos" / "scenarios")


class Recorder:
    def __init__(self, **replies):
        self.replies = replies
        self.writes: list[tuple] = []

    def __getattr__(self, name):
        def method(**kwargs):
            self.writes.append((name, kwargs))
            reply = self.replies.get(name, {})
            return reply(**kwargs) if callable(reply) else reply

        return method

    def get_waiter(self, name):
        class W:
            def wait(self, **kwargs):
                pass

        return W()


def injector(tmp_path, lam=None, sqs=None, iam=None, dry_run=False):
    return actions.Injector(
        lam or Recorder(),
        sqs or Recorder(),
        None,
        actor="t",
        git_sha="abc",
        dry_run=dry_run,
        state_path=tmp_path / "state.json",
        iam=iam,
    )


def test_concurrency_is_restored_to_what_it_was(tmp_path):
    lam = Recorder(get_function_concurrency={"ReservedConcurrentExecutions": 5})
    inj = injector(tmp_path, lam=lam)
    inj.set_concurrency("cart", 1)
    inj.restore("r")
    puts = [
        w[1]["ReservedConcurrentExecutions"]
        for w in lam.writes
        if w[0] == "put_function_concurrency"
    ]
    assert puts == [1, 5]
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved[0]["original"] == 5 and saved[0]["restored"]


def test_a_queue_attribute_is_restored(tmp_path):
    sqs = Recorder(
        get_queue_url={"QueueUrl": "q"},
        get_queue_attributes={"Attributes": {"VisibilityTimeout": "180"}},
    )
    inj = injector(tmp_path, sqs=sqs)
    inj.set_queue_attribute("nightshift-placed-orders", "VisibilityTimeout", "0")
    inj.restore("r")
    sets = [w[1]["Attributes"] for w in sqs.writes if w[0] == "set_queue_attributes"]
    assert sets == [{"VisibilityTimeout": "0"}, {"VisibilityTimeout": "180"}]


def test_logging_is_restored_whole(tmp_path):
    original = {
        "LogFormat": "JSON",
        "ApplicationLogLevel": "INFO",
        "SystemLogLevel": "INFO",
    }
    lam = Recorder(get_function_configuration={"LoggingConfig": original})
    inj = injector(tmp_path, lam=lam)
    inj.set_log_level("orders", "FATAL")
    inj.restore("r")
    configs = [
        w[1]["LoggingConfig"]
        for w in lam.writes
        if w[0] == "update_function_configuration"
    ]
    assert configs == [{**original, "ApplicationLogLevel": "FATAL"}, original]


POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ConnectToDsql",
            "Effect": "Allow",
            "Action": "dsql:DbConnect",
            "Resource": "c",
        },
        {
            "Sid": "PublishPlacedOrders",
            "Effect": "Allow",
            "Action": "sqs:SendMessage",
            "Resource": "q",
        },
    ],
}


def iam(decision="allowed"):
    return Recorder(
        get_role_policy={"PolicyDocument": POLICY},
        get_role={"Role": {"Arn": "arn:role"}},
        simulate_principal_policy={
            "EvaluationResults": [
                {"EvalActionName": "sqs:SendMessage", "EvalDecision": decision}
            ]
        },
    )


def test_a_policy_statement_is_removed_then_restored_and_checked(tmp_path):
    fake = iam()
    inj = injector(tmp_path, iam=fake)
    inj.remove_policy_statement("orders", "PublishPlacedOrders")
    inj.restore("r")
    puts = [
        json.loads(w[1]["PolicyDocument"])
        for w in fake.writes
        if w[0] == "put_role_policy"
    ]
    assert [s["Sid"] for s in puts[0]["Statement"]] == ["ConnectToDsql"]
    assert puts[1] == POLICY
    assert all(
        w[1]["RoleName"] == "nightshift-orders-exec"
        for w in fake.writes
        if w[0] == "put_role_policy"
    )
    sim = next(w[1] for w in fake.writes if w[0] == "simulate_principal_policy")
    assert sim["ActionNames"] == ["sqs:SendMessage"] and sim["ResourceArns"] == ["q"]


def test_a_restore_the_simulator_still_denies_fails_loudly(tmp_path):
    inj = injector(tmp_path, iam=iam("implicitDeny"))
    inj.remove_policy_statement("orders", "PublishPlacedOrders")
    with pytest.raises(RuntimeError, match="still denied"):
        inj.restore("r")


def test_an_unknown_statement_is_refused_before_any_write(tmp_path):
    fake = iam()
    with pytest.raises(ValueError):
        injector(tmp_path, iam=fake).remove_policy_statement("orders", "Nope")
    assert not [w for w in fake.writes if w[0] == "put_role_policy"]


def test_an_empty_dlq_is_not_redriven(tmp_path):
    sqs = Recorder(
        get_queue_url={"QueueUrl": "dlq"},
        get_queue_attributes={
            "Attributes": {"QueueArn": "arn", "ApproximateNumberOfMessages": "0"}
        },
    )
    injector(tmp_path, sqs=sqs).redrive_dlq("nightshift-placed-orders-dlq")
    assert not [w for w in sqs.writes if w[0] == "start_message_move_task"]


def test_every_code_patch_in_every_scenario_applies_exactly_once():
    patches = [
        s.args
        for scenario in SCENARIOS
        for s in scenario.setup + scenario.inject
        if s.do == "deploy_patch"
    ]
    assert len(patches) >= 3
    for a in patches:
        actions.build_zip(
            a["service"], {"file": a["file"], "find": a["find"], "with": a["with"]}
        )


def test_notes_and_patches_never_give_the_game_away():
    """A note or a patched line is evidence the agent reads."""
    for scenario in SCENARIOS:
        for step in scenario.setup + scenario.inject:
            for key in ("note", "with"):
                if key in step.args:
                    assert not BANNED.search(step.args[key]), (scenario.id, key)


def test_load_steps_become_load_py_flags():
    assert runner.load_options({"rate": 2, "hot_product": 0}) == ["--hot-product", "0"]
    assert runner.load_options({"note": "hi"}) == ["--note", "hi"]
    assert runner.load_options({"rate": 1}) == []


def test_a_hot_product_is_in_every_cart_once():
    from load import CATALOGUE, plan_carts

    hot = CATALOGUE[0][0]
    carts = plan_carts(200, seed=3, hot_product=0)
    assert all(c[0] == {"product_id": hot, "quantity": 1} for c in carts)
    assert all(sum(i["product_id"] == hot for i in c) == 1 for c in carts)
    assert plan_carts(50, seed=3) == plan_carts(50, seed=3, hot_product=None)


def result(grading: str) -> dict:
    return {
        "scenario": 14,
        "run_id": "r",
        "grading": grading,
        "ground_truth": {"component": "orders", "fault_category": "bad_deploy"},
    }


def report(category: str, component: str, confidence: int) -> dict:
    return {
        "investigation_id": "i",
        "root_cause_component": component,
        "fault_category": category,
        "confidence": confidence,
        "actions": [],
    }


@pytest.mark.parametrize(
    "answer, correct",
    [
        (report("insufficient_evidence", "none", 0), True),
        (report("bad_deploy", "orders", 70), True),
        (report("bad_deploy", "orders", 95), False),
        (report("config_regression", "orders", 50), False),
    ],
)
def test_hedged_grading(answer, correct):
    assert grade(result("hedged"), answer).root_cause_correct is correct


def test_exact_grading_is_unchanged():
    assert grade(result("exact"), report("bad_deploy", "orders", 95)).root_cause_correct
    assert not grade(
        result("exact"), report("insufficient_evidence", "none", 0)
    ).root_cause_correct


def test_the_page_goes_to_the_alarm_that_changed_first_not_the_first_name():
    from datetime import UTC, datetime

    class Cw:
        def describe_alarms(self, AlarmNames):
            at = {
                "nightshift-orders-errors": datetime(2026, 9, 27, 3, 0, 10, tzinfo=UTC),
                "nightshift-throttles": datetime(2026, 9, 27, 3, 0, 2, tzinfo=UTC),
            }
            return {
                "MetricAlarms": [
                    {"AlarmName": n, "StateUpdatedTimestamp": at[n]} for n in AlarmNames
                ]
            }

    both = {"nightshift-orders-errors", "nightshift-throttles"}
    assert runner.first_to_fire(Cw(), both) == "nightshift-throttles"
