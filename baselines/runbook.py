"""The scripted runbook: what a written on-call runbook would do, with no model.

    1. Read the alarm.
    2. Was the alarmed service (or, for an alarm with no service, any
       service) deployed in the last 30 minutes? Then that deploy is the
       cause: code changed means bad_deploy, only settings changed means
       config_regression, and the fix is to roll it back.
    3. Otherwise, one rule per alarm (RULES below).
    4. No rule: insufficient_evidence.

It is written from the alarm list in terraform/alarms.tf and the fixed
fault categories, never from a scenario's answer, and it reads nothing at
run time but the same read-only tools the agent has. Its author knew the
scenarios, which makes it a stronger baseline than a runbook written blind:
if the agent beats it, that is not because the runbook was naive.

It always answers with confidence 70: a runbook has no way to know how sure
it should be.
"""

from __future__ import annotations

import json
from typing import Any

from agent import report
from agent.loop import parse_hypotheses
from agent.state import InvestigationState, Step
from agent.tools.aws_read import get_alarm
from agent.tools.context import ToolContext
from baselines.steps import new_state, record

CONFIDENCE = 70
DEPLOY_WINDOW_MINUTES = 30
FUNCTIONS = ("orders", "cart", "payments", "fulfillment")

# The service each alarm watches, from its dimensions in terraform/alarms.tf.
ALARM_SERVICE = {
    "orders-errors": "orders",
    "cart-errors": "cart",
    "payments-errors": "payments",
    "fulfillment-errors": "fulfillment",
    "checkout-latency": "orders",
    "serialization-retries": "orders",
    "payment-failures": "payments",
}


def data(step: Step) -> Any:
    return json.loads(step.result).get("untrusted_data", {})


def short_name(alarm: str) -> str:
    return alarm.removeprefix("nightshift-")


def answer(
    component: str,
    category: str,
    summary: str,
    evidence: list[Step],
    actions: tuple[str, ...] = (),
) -> dict[str, Any]:
    known = category != "insufficient_evidence"
    return {
        "root_cause_component": component,
        "fault_category": category,
        "confidence": CONFIDENCE if known else 0,
        "summary": summary,
        "evidence_steps": ",".join(str(s.number) for s in evidence),
        "hypotheses": f"{'likely' if known else 'possible'}: {summary}",
        "actions": "\n".join(actions),
        "proposed_actions": "",
    }


def from_deploy(
    state: InvestigationState, tools: ToolContext, alarm: str
) -> dict | None:
    service = ALARM_SERVICE.get(alarm)
    args: dict[str, Any] = {"minutes": DEPLOY_WINDOW_MINUTES}
    if service:
        args["service"] = service
    step = record(state, tools, "list_recent_deployments", args)
    moves = [m for m in data(step).get("moves", []) if m.get("kind") == "deploy"]
    if not moves:
        return None
    move = moves[0]  # newest first
    changed = move.get("changed", {})
    code = changed.get("code_changed", True)  # unknown counts as code
    category = "bad_deploy" if code else "config_regression"
    what = (
        "code"
        if code
        else f"settings ({', '.join(changed.get('settings_changed', []))})"
    )
    return answer(
        move["service"],
        category,
        f"{move['service']} was deployed at {move['at']} ({what} changed) before "
        f"{alarm} fired. Roll it back.",
        [state.steps[0], step],
        (f"rollback_alias service={move['service']}",),
    )


def most_throttled(
    state: InvestigationState, tools: ToolContext
) -> tuple[str, Step] | None:
    best: tuple[float, str, Step] | None = None
    for service in FUNCTIONS:
        step = record(
            state,
            tools,
            "get_metrics",
            {
                "namespace": "AWS/Lambda",
                "metric": "Throttles",
                "statistic": "Sum",
                "dimensions": f"FunctionName=nightshift-{service}",
                "minutes": 15,
            },
        )
        total = sum(v for _, v in data(step).get("points", []))
        if total and (best is None or total > best[0]):
            best = (total, service, step)
    return (best[1], best[2]) if best else None


def by_alarm(state: InvestigationState, tools: ToolContext, alarm: str) -> dict:
    first = state.steps[0]
    if alarm == "dlq-depth":
        step = record(state, tools, "get_queue_stats", {})
        return answer(
            "placed-orders",
            "poison_message",
            "Messages reached the dead-letter queue: a message the consumer cannot "
            "process. Do not redrive until the producer is fixed.",
            [first, step],
        )
    if alarm in ("payment-failures", "payments-errors"):
        return answer(
            "payments",
            "slow_dependency",
            "The payment provider is failing. Defer payments until it recovers.",
            [first],
            ("set_operational_flag name=payments_degraded_mode value=true",),
        )
    if alarm == "serialization-retries":
        return answer(
            "dsql",
            "hot_row_contention",
            "Checkout transactions are conflicting on the same rows.",
            [first],
        )
    if alarm == "throttles":
        found = most_throttled(state, tools)
        if found:
            service, step = found
            return answer(
                service,
                "throttling",
                f"{service} is being throttled at its concurrency limit.",
                [first, step],
            )
    return answer(
        "none",
        "insufficient_evidence",
        f"No runbook rule explains {alarm} without a recent deploy.",
        [],
    )


def investigate(tools: ToolContext, alarm_name: str) -> InvestigationState:
    trigger = get_alarm(tools, alarm_name)
    state = new_state(trigger, provider="runbook", model="scripted")
    record(state, tools, "get_alarm", {"name": short_name(trigger["name"])})
    alarm = short_name(trigger["name"])
    final = from_deploy(state, tools, alarm) or by_alarm(state, tools, alarm)
    # The same validation the agent's answer gets. A rule that produced an
    # answer the report rules refuse is a bug in the runbook: fail loudly.
    report.from_answer(state, final)
    state.hypotheses, _ = parse_hypotheses(final["hypotheses"])
    state.final = final
    state.finished = True
    state.stop_reason = "finished"
    return state
