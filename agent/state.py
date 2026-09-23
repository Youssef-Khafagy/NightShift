"""Everything an investigation knows, in a form that survives a crash.

The conversation sent to the model is never stored. It is rebuilt from this
state before every call (agent/window.py), which is what makes resuming
trivial: load the last checkpoint and carry on, with nothing half-written.

A step is one tool call. Steps made in the same model reply share a `turn`,
because the provider needs them back as one assistant message.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Step:
    number: int
    turn: int
    at: str
    tool: str
    args: dict[str, Any]
    call_id: str
    result: str  # exactly what the model saw
    summary: str  # a short form, used once the full result is too old to keep
    reasoning: str = ""  # the model's text in the turn that made this call
    provider_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Call:
    """One request to the LLM: what it cost and what the provider said about
    its limits. This is the per-run token log the owner asked for."""

    turn: int
    at: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_request_tokens: int
    rate_limit_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class InvestigationState:
    investigation_id: str
    trigger: dict[str, Any]  # the alarm (or alarms) that started it
    started_at: str
    provider: str
    model: str
    turn: int = 0
    steps: list[Step] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    hypotheses: list[dict[str, str]] = field(default_factory=list)
    hypothesis_changes: list[dict[str, Any]] = field(default_factory=list)
    log_bytes_scanned: int = 0
    rejected_calls: int = 0  # replies a provider refused for bad tool arguments
    wall_seconds_used: float = 0.0
    finished: bool = False
    stop_reason: str = ""
    final: dict[str, Any] | None = None  # finish_investigation's arguments

    @property
    def tokens_used(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvestigationState:
        data = dict(data)
        data["steps"] = [Step(**s) for s in data.get("steps", [])]
        data["calls"] = [Call(**c) for c in data.get("calls", [])]
        return cls(**data)
