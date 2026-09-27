"""The two baselines, against fake AWS clients and a scripted model.

The runbook's rules are the baseline, so each one is pinned here; and the
alarm-only baseline must never run a tool it was not offered.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import report
from agent.config import AgentConfig
from agent.llm.base import Completion, ToolCall, Usage
from agent.store import MemoryStore
from agent.tools.context import ToolContext
from baselines import runbook
from baselines.alarm_only import AlarmOnly

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SERVICES = ("orders", "cart", "payments", "fulfillment")
TOPOLOGY = {
    "services": {
        s: {
            "function": f"nightshift-{s}",
            "alias": "live",
            "log_group": f"/aws/lambda/nightshift-{s}",
            "flags": [],
        }
        for s in SERVICES
    },
    "queues": {
        "nightshift-placed-orders": {
            "consumer": "fulfillment",
            "dlq": "nightshift-placed-orders-dlq",
            "max_receive_count": 3,
        }
    },
}


class Fake:
    def __init__(self, **methods) -> None:
        self.methods = methods
        self.calls: list[str] = []

    def __getattr__(self, name):
        def method(**kwargs):
            self.calls.append(name)
            reply = self.methods[name]
            return reply(**kwargs) if callable(reply) else reply

        return method


def alarm(name: str) -> dict:
    return {
        "AlarmName": f"nightshift-{name}",
        "StateValue": "ALARM",
        "StateReason": "Threshold Crossed",
        "StateUpdatedTimestamp": NOW,
        "Namespace": "AWS/Lambda",
        "MetricName": "Errors",
        "Dimensions": [],
        "Statistic": "Sum",
        "Period": 60,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        "Threshold": 1.0,
        "EvaluationPeriods": 1,
    }


def context(
    name: str, moves: list[dict] | None = None, throttles: dict | None = None
) -> ToolContext:
    moves = moves or []
    throttles = throttles or {}

    def query(**kw):
        service = kw["ExpressionAttributeValues"][":s"]["S"]
        return {
            "Items": [
                {k: {"S": v} for k, v in m.items()}
                for m in moves
                if m["service"] == service
            ]
        }

    def metrics(**kw):
        function = kw["Dimensions"][0]["Value"].removeprefix("nightshift-")
        n = throttles.get(function, 0)
        return {"Datapoints": [{"Timestamp": NOW, "Sum": n}] if n else []}

    versions = {
        "1": {"CodeSha256": "a", "Environment": {"Variables": {"T": "x"}}},
        "2": {"CodeSha256": "b", "Environment": {"Variables": {"T": "x"}}},
        "3": {"CodeSha256": "b", "Environment": {"Variables": {"T": "y"}}},
    }
    clients = {
        "ssm": Fake(get_parameter={"Parameter": {"Value": json.dumps(TOPOLOGY)}}),
        "cloudwatch": Fake(
            describe_alarms={"MetricAlarms": [alarm(name)]},
            describe_alarm_history={"AlarmHistoryItems": []},
            get_metric_statistics=metrics,
        ),
        "dynamodb": Fake(query=query),
        "lambda": Fake(
            get_function_configuration=lambda FunctionName, Qualifier: versions[
                Qualifier
            ],
            list_event_source_mappings={"EventSourceMappings": []},
        ),
        "sqs": Fake(
            get_queue_url=lambda QueueName: {"QueueUrl": QueueName},
            get_queue_attributes={
                "Attributes": {
                    "ApproximateNumberOfMessages": "1",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "VisibilityTimeout": "180",
                }
            },
        ),
    }

    class Session:
        def client(self, service):
            return clients[service]

    return ToolContext(Session(), now=lambda: NOW)


def deploy(service: str, old: str, new: str) -> dict:
    return {
        "service": service,
        "deployed_at": "2026-09-26T11:50:00.000+00:00",
        "kind": "deploy",
        "previous": old,
        "new": new,
    }


def answer_of(state) -> report.Report:
    return report.build(state)


def test_a_recent_code_deploy_is_rolled_back():
    state = runbook.investigate(
        context("orders-errors", [deploy("orders", "1", "2")]), "orders-errors"
    )
    r = answer_of(state)
    assert (r.root_cause_component, r.fault_category) == ("orders", "bad_deploy")
    assert r.actions == ["rollback_alias service=orders"]
    assert r.confidence == runbook.CONFIDENCE
    assert [s.tool for s in state.steps] == ["get_alarm", "list_recent_deployments"]


def test_a_settings_only_deploy_is_a_config_regression():
    r = answer_of(
        runbook.investigate(
            context("cart-errors", [deploy("cart", "2", "3")]), "cart-errors"
        )
    )
    assert (r.root_cause_component, r.fault_category) == ("cart", "config_regression")


def test_an_alarm_with_no_service_blames_the_newest_deploy_anywhere():
    """The classic runbook mistake scenario 12 exists to catch."""
    r = answer_of(
        runbook.investigate(
            context("throttles", [deploy("cart", "1", "2")]), "throttles"
        )
    )
    assert (r.root_cause_component, r.fault_category) == ("cart", "bad_deploy")


def test_dlq_depth_without_a_deploy_is_a_poison_message_and_never_redriven():
    r = answer_of(runbook.investigate(context("dlq-depth"), "dlq-depth"))
    assert (r.root_cause_component, r.fault_category) == (
        "placed-orders",
        "poison_message",
    )
    assert r.actions == []


def test_payment_failures_defer_payments():
    r = answer_of(runbook.investigate(context("payment-failures"), "payment-failures"))
    assert (r.root_cause_component, r.fault_category) == ("payments", "slow_dependency")
    assert r.actions == ["set_operational_flag name=payments_degraded_mode value=true"]


def test_throttles_blame_the_most_throttled_function():
    r = answer_of(
        runbook.investigate(
            context("throttles", throttles={"cart": 2, "payments": 9}), "throttles"
        )
    )
    assert (r.root_cause_component, r.fault_category) == ("payments", "throttling")


def test_no_rule_means_insufficient_evidence():
    r = answer_of(runbook.investigate(context("checkout-latency"), "checkout-latency"))
    assert (r.root_cause_component, r.fault_category) == (
        "none",
        "insufficient_evidence",
    )
    assert r.confidence == 0


class Script:
    name, model = "fake", "fake-1"

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.tools_offered: list[list[str]] = []
        self.seen: list = []

    def complete(self, messages, tools, *, max_wait=None):
        self.tools_offered.append([t.name for t in tools])
        self.seen.append(messages)
        return self.replies.pop(0)


def said(*calls) -> Completion:
    return Completion(
        text="",
        tool_calls=tuple(ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)),
        usage=Usage(input_tokens=100, output_tokens=10),
    )


def test_alarm_only_offers_one_tool_and_never_runs_another():
    finish = {
        "root_cause_component": "orders",
        "fault_category": "bad_deploy",
        "confidence": 60,
        "summary": "orders errors after what is probably a deploy.",
        "evidence_steps": "1",
        "hypotheses": "possible: bad deploy of orders",
    }
    script = Script(
        said(("get_metrics", {"namespace": "AWS/Lambda"})),
        said(("finish_investigation", finish)),
    )
    ctx = context("orders-errors")
    config = AgentConfig()
    baseline = AlarmOnly(script, ctx, MemoryStore(), config)
    state = baseline.run(baseline.start_from_alarm("orders-errors"))
    assert script.tools_offered == [["finish_investigation"]] * 2
    # The alarm read is in the page message, never replayed as a call the
    # model did not make (Gemini refuses an unsigned function call).
    first = script.seen[0]
    assert not any(m.tool_calls for m in first)
    assert "nightshift-orders-errors" in first[1].content
    assert [s.tool for s in state.steps] == [
        "get_alarm",
        "get_metrics",
        "finish_investigation",
    ]
    assert "only finish_investigation" in state.steps[1].result
    assert "get_metric_statistics" not in ctx.client("cloudwatch").calls
    r = answer_of(state)
    assert (r.root_cause_component, r.fault_category, r.evidence) == (
        "orders",
        "bad_deploy",
        [1],
    )
