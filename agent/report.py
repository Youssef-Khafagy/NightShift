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

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from agent.actions import misfit, parse_block, parse_line
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
    # Allowlisted actions, as canonical lines ("rollback_alias service=orders"),
    # each a candidate for owner approval. Everything else a fix needs is text
    # for a human in proposed_actions.
    actions: list[str] = Field(default_factory=list)
    proposed_actions: list[str]
    stop_reason: str
    # Filled in from the state, not by the model.
    evidence_tools: dict[int, str] = Field(default_factory=dict)
    # Cited steps that were skipped, failed, missing or notes, removed.
    evidence_dropped: list[int] = Field(default_factory=list)

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
            raise ValueError(
                "a fault needs at least one evidence step that returned data"
            )
        if self.fault_category == "no_fault" and self.actions:
            raise ValueError("no_fault means nothing to fix: propose no actions")
        for line in self.actions:
            reason = misfit(
                parse_line(line), self.root_cause_component, self.fault_category
            )
            if reason:
                raise ValueError(f"action {line!r} does not fit the finding: {reason}")
        return self


def returned_nothing(result: str) -> bool:
    """A step that was skipped, rejected or failed. The loop records those as
    {"error": ...}, and a tool that failed returns {"error": ...} under
    untrusted_data; a successful result never has an "error" key. The M5
    live check found answers citing calls the loop had skipped."""
    try:
        data = json.loads(result)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    inner = data.get("untrusted_data")
    return "error" in data or (isinstance(inner, dict) and "error" in inner)


def parse_steps(text: str) -> list[int]:
    return sorted({int(n) for n in re.findall(r"\d+", text or "")})


def from_answer(state: InvestigationState, answer: dict[str, Any]) -> Report:
    """Validate an answer against the state it came from. Raises ValueError
    with every problem, readable by the model."""
    evidence = parse_steps(str(answer.get("evidence_steps", "")))
    tools = {s.number: s.tool for s in state.steps}
    failed = {s.number for s in state.steps if returned_nothing(s.result)}
    problems = []
    # Steps that cannot be evidence are dropped, not fatal: the M6 live check
    # lost a correct diagnosis because it cited two skipped calls beside real
    # ones, and the answer was refused until the step budget ran out. What
    # remains must still be real evidence (a fault needs at least one).
    dropped = [
        n
        for n in evidence
        if n not in tools or tools[n] in CONTROL_TOOLS or n in failed
    ]
    evidence = [n for n in evidence if n not in dropped]
    human = [
        line.strip(" -*\t")
        for line in str(answer.get("proposed_actions", "")).splitlines()
        if line.strip(" -*\t")
    ]
    allowed, action_problems = parse_block(str(answer.get("actions", "")))
    problems += action_problems
    try:
        report = Report(
            investigation_id=state.investigation_id,
            root_cause_component=answer.get("root_cause_component"),
            fault_category=answer.get("fault_category"),
            confidence=answer.get("confidence"),
            summary=answer.get("summary", ""),
            evidence=evidence,
            actions=[a.line() for a in allowed],
            proposed_actions=human,
            stop_reason="finished",
            evidence_tools={n: tools[n] for n in evidence if n in tools},
            evidence_dropped=dropped,
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
