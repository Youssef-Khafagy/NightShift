"""What a scenario file is allowed to say.

Every scenario is a YAML file in chaos/scenarios/, validated against these
models before anything runs. The vocabularies are closed on purpose: the
benchmark grades the agent by comparing its answer to `ground_truth`
mechanically, which only works if both sides use the same fixed words.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FaultCategory(StrEnum):
    """The answer the agent must name. Fixed for the whole benchmark."""

    BAD_DEPLOY = "bad_deploy"
    CONFIG_REGRESSION = "config_regression"
    TIMEOUT_REGRESSION = "timeout_regression"
    SLOW_DEPENDENCY = "slow_dependency"
    POISON_MESSAGE = "poison_message"
    IAM_REGRESSION = "iam_regression"
    HOT_ROW_CONTENTION = "hot_row_contention"
    MISSING_INDEX = "missing_index"
    THROTTLING = "throttling"
    RETRY_STORM = "retry_storm"
    NO_FAULT = "no_fault"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Component(StrEnum):
    """Where the fault is. Service names match the topology parameter."""

    ORDERS = "orders"
    CART = "cart"
    PAYMENTS = "payments"
    FULFILLMENT = "fulfillment"
    PLACED_ORDERS_QUEUE = "placed-orders"
    DSQL = "dsql"
    CART_TABLE = "cart-table"
    NONE = "none"


class AgentAction(StrEnum):
    """The agent's allowlisted actions (M6), plus doing nothing."""

    ROLLBACK_ALIAS = "rollback_alias"
    SET_OPERATIONAL_FLAG = "set_operational_flag"
    PAUSE_QUEUE_CONSUMER = "pause_queue_consumer"
    RESUME_QUEUE_CONSUMER = "resume_queue_consumer"
    REDRIVE_DLQ = "redrive_dlq"
    NONE = "none"


class Step(BaseModel):
    """One thing the runner does. `do` names a primitive in chaos/actions.py."""

    model_config = ConfigDict(extra="forbid")
    do: Literal[
        "set_env",
        "deploy_patch",
        "send_message",
        "load",
        "wait",
        "restore",
        "drain_dlq_message",
    ]
    args: dict[str, Any] = Field(default_factory=dict)


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: Component
    fault_category: FaultCategory


class Remediation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: AgentAction
    target: str | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, le=14)
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    description: str
    load_rate: float = Field(gt=0, le=5)
    warm_up_seconds: int = Field(ge=0, le=900)
    setup: list[Step] = Field(default_factory=list)
    inject: list[Step]
    expected_alarms: list[str]
    alarm_wait_seconds: int = Field(ge=0, le=1800)
    ground_truth: GroundTruth
    acceptable_remediations: list[Remediation]
    forbidden_actions: list[Remediation]
    recover: list[Step]
    health_check: list[Literal["smoke_test", "alarms_ok", "plan_clean", "dlq_empty"]]

    @model_validator(mode="after")
    def consistent(self) -> Scenario:
        no_fault = self.ground_truth.fault_category == FaultCategory.NO_FAULT
        if no_fault:
            if any(s.do != "load" for s in self.inject):
                raise ValueError("a no_fault scenario may only add load")
            if any(r.action != AgentAction.NONE for r in self.acceptable_remediations):
                raise ValueError("a no_fault scenario's only acceptable action is none")
            if self.ground_truth.component != Component.NONE:
                raise ValueError("a no_fault scenario has component none")
        elif not self.inject:
            raise ValueError("a scenario with a fault must inject it")
        clash = {(r.action, r.target) for r in self.acceptable_remediations} & {
            (r.action, r.target) for r in self.forbidden_actions
        }
        if clash:
            raise ValueError(f"both acceptable and forbidden: {clash}")
        if "plan_clean" not in self.health_check:
            raise ValueError("every scenario must end with terraform plan clean")
        return self


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(path.read_text()))


def load_all(directory: Path) -> list[Scenario]:
    return [load_scenario(p) for p in sorted(directory.glob("*.yaml"))]
