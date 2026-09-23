"""The investigation's answer, validated.

Pydantic checks the shape (fixed vocabularies, confidence 0 to 100) and the
rules that make an answer mean something:

- no_fault means nothing is broken, so the component must be none;
- component none means no fault was found, so the category must be
  no_fault or insufficient_evidence;
- a fault must cite at least one journal step as evidence, and every cited
  step must exist and be a real tool call, not a note to itself.

The loop runs the same validation when the model calls finish_investigation,
so a bad answer goes back to the model to fix instead of into the results.
An investigation that stopped without an answer (a limit, a provider
failure) gets insufficient_evidence with confidence 0, and says why.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from agent.state import InvestigationState
from agent.vocabulary import COMPONENTS, FAULT_CATEGORIES

Component = Literal[tuple(COMPONENTS)]  # type: ignore[valid-type]
FaultCategory = Literal[tuple(FAULT_CATEGORIES)]  # type: ignore[valid-type]
CONTROL_TOOLS = {"note_hypotheses", "finish_investigation"}
NO_ANSWER = {"no_fault", "insufficient_evidence"}


class Report(BaseModel):
    investigation_id: str
    root_cause_component: Component
    fault_category: FaultCategory
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[int]
    proposed_actions: list[str]
    stop_reason: str
    # Filled in from the state, not by the model.
    evidence_tools: dict[int, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self) -> Report:
        if self.fault_category == "no_fault" and self.root_cause_component != "none":
            raise ValueError(
                "no_fault means nothing is broken: root_cause_component must be none"
            )
        if self.root_cause_component == "none" and self.fault_category not in NO_ANSWER:
            raise ValueError(
                "root_cause_component none means no fault found: use no_fault or insufficient_evidence"
            )
        if self.fault_category not in NO_ANSWER and not self.evidence:
            raise ValueError("a fault needs at least one evidence step")
        return self


def parse_steps(text: str) -> list[int]:
    return sorted({int(n) for n in re.findall(r"\d+", text or "")})


def from_answer(state: InvestigationState, answer: dict[str, Any]) -> Report:
    """Validate an answer against the state it came from. Raises ValueError
    with every problem, readable by the model."""
    evidence = parse_steps(str(answer.get("evidence_steps", "")))
    tools = {s.number: s.tool for s in state.steps}
    problems = []
    for n in evidence:
        if n not in tools:
            problems.append(
                f"evidence step {n} does not exist (steps 1 to {len(tools)})"
            )
        elif tools[n] in CONTROL_TOOLS:
            problems.append(f"evidence step {n} is a {tools[n]} note, not evidence")
    actions = [
        line.strip(" -*\t")
        for line in str(answer.get("proposed_actions", "")).splitlines()
        if line.strip(" -*\t")
    ]
    try:
        report = Report(
            investigation_id=state.investigation_id,
            root_cause_component=answer.get("root_cause_component"),
            fault_category=answer.get("fault_category"),
            confidence=answer.get("confidence"),
            summary=answer.get("summary", ""),
            evidence=evidence,
            proposed_actions=actions,
            stop_reason="finished",
            evidence_tools={n: tools[n] for n in evidence if n in tools},
        )
    except ValidationError as error:
        problems += [e["msg"].removeprefix("Value error, ") for e in error.errors()]
        report = None
    if problems:
        raise ValueError("; ".join(problems))
    assert report is not None
    return report


def build(state: InvestigationState) -> Report:
    """The final report for a finished or stopped investigation."""
    if state.final is not None:
        return from_answer(state, state.final)
    return Report(
        investigation_id=state.investigation_id,
        root_cause_component="none",
        fault_category="insufficient_evidence",
        confidence=0,
        summary=f"The investigation stopped before reaching a conclusion ({state.stop_reason}).",
        evidence=[],
        proposed_actions=[],
        stop_reason=state.stop_reason or "unknown",
    )
