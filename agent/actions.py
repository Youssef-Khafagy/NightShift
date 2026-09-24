"""The five actions the agent may propose, and nothing else.

A proposal is one line of text, because every provider accepts a string
argument and not every one accepts nested objects:

    rollback_alias service=orders
    set_operational_flag name=payments_degraded_mode value=true
    pause_queue_consumer
    resume_queue_consumer
    redrive_dlq

This module is the single definition of what is allowed. The agent checks a
proposal here before its answer is accepted, and the Actor (M6 step 3)
checks the approved action here again before running it, because the Actor
treats everything that came from the model as untrusted.

Anything else a fix needs (IAM, schema, code, capacity) is written as a
proposal for a human in `proposed_actions`, never as an action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ROLLBACK_SERVICES = ("cart", "orders", "payments", "fulfillment")
FLAGS = {
    # The same patterns SSM enforces on write (terraform/flags.tf).
    "payments_degraded_mode": re.compile(r"^(true|false)$"),
    "checkout_rate_limit": re.compile(r"^[0-9]{1,4}$"),
}
PARAMETERS: dict[str, tuple[str, ...]] = {
    "rollback_alias": ("service",),
    "set_operational_flag": ("name", "value"),
    "pause_queue_consumer": (),
    "resume_queue_consumer": (),
    "redrive_dlq": (),
}
MAX_ACTIONS = 3


@dataclass(frozen=True)
class Action:
    name: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def target(self) -> str | None:
        """What the scenario files call the target: the service for a
        rollback, the flag for a flag, nothing for the queue actions."""
        return self.params.get("service") or self.params.get("name")

    def line(self) -> str:
        return " ".join(
            [self.name, *(f"{k}={self.params[k]}" for k in sorted(self.params))]
        )


def parse_line(line: str) -> Action:
    """One proposal line to an Action. Raises ValueError saying what is wrong."""
    words = line.split()
    if not words:
        raise ValueError("empty action")
    name, pairs = words[0], words[1:]
    if name not in PARAMETERS:
        raise ValueError(
            f"{name!r} is not an allowed action; allowed: {', '.join(PARAMETERS)}"
        )
    params: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key or not value:
            raise ValueError(f"{pair!r} is not key=value")
        if key in params:
            raise ValueError(f"{key!r} given twice")
        params[key] = value
    expected = set(PARAMETERS[name])
    if set(params) != expected:
        want = ", ".join(f"{k}=..." for k in PARAMETERS[name]) or "no parameters"
        raise ValueError(f"{name} takes {want}")
    action = Action(name, params)
    check(action)
    return action


def check(action: Action) -> None:
    """The values, not just the shape. Raises ValueError."""
    if (
        action.name == "rollback_alias"
        and action.params["service"] not in ROLLBACK_SERVICES
    ):
        raise ValueError(
            f"rollback_alias service must be one of {', '.join(ROLLBACK_SERVICES)}"
        )
    if action.name == "set_operational_flag":
        flag, value = action.params["name"], action.params["value"]
        if flag not in FLAGS:
            raise ValueError(f"flag must be one of {', '.join(FLAGS)}")
        if not FLAGS[flag].match(value):
            raise ValueError(f"{value!r} is not a valid value for {flag}")


def parse_block(text: str) -> tuple[list[Action], list[str]]:
    """Every non-empty line of `text`, parsed. Returns the actions and one
    problem per bad line, so the model can fix them all at once."""
    actions, problems = [], []
    for raw in (text or "").splitlines():
        line = raw.strip(" -*\t")
        if not line:
            continue
        try:
            actions.append(parse_line(line))
        except ValueError as error:
            problems.append(f"action {line!r}: {error}")
    if len({a.line() for a in actions}) != len(actions):
        problems.append("the same action is proposed twice")
    if len(actions) > MAX_ACTIONS:
        problems.append(f"at most {MAX_ACTIONS} actions")
    return actions, problems


# -- does the action fit the finding? ---------------------------------------------
#
# An injected instruction ("roll back cart") has to get past more than the
# allowlist: the action must also follow from the agent's own diagnosis. A
# rollback targets the service named as the root cause; queue actions need a
# cause on the queue side; any action needs an actual diagnosis. The agent's
# report is checked against this, and the Actor checks it again against the
# saved report before acting.

QUEUE_SIDE = ("placed-orders", "fulfillment", "payments")
FLAG_COMPONENTS = {
    "payments_degraded_mode": QUEUE_SIDE,
    "checkout_rate_limit": ("orders", "cart", "dsql", "cart-table"),
}
NOT_A_DIAGNOSIS = ("no_fault", "insufficient_evidence")


def misfit(action: Action, component: str, category: str) -> str | None:
    """Why this action does not follow from the finding, or None if it does."""
    if category in NOT_A_DIAGNOSIS:
        return f"{category} is not a diagnosis to act on"
    if action.name == "rollback_alias" and action.params["service"] != component:
        return (
            f"rollback_alias targets {action.params['service']} but the root cause "
            f"is {component}"
        )
    if action.name == "set_operational_flag":
        allowed = FLAG_COMPONENTS[action.params["name"]]
        if component not in allowed:
            return f"{action.params['name']} does not address a fault in {component}"
    queue_actions = ("pause_queue_consumer", "resume_queue_consumer", "redrive_dlq")
    if action.name in queue_actions and component not in QUEUE_SIDE:
        return f"{action.name} does not address a fault in {component}"
    return None
