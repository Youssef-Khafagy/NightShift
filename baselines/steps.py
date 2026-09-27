"""Recording a baseline's tool calls as journal steps, the way the loop does,
so evidence cites real steps and the report rules apply unchanged."""

from __future__ import annotations

import uuid
from typing import Any

from agent.loop import now_iso, summarise
from agent.state import InvestigationState, Step
from agent.tools import run_tool
from agent.tools.context import ToolContext


def new_state(trigger: dict[str, Any], provider: str, model: str) -> InvestigationState:
    return InvestigationState(
        investigation_id=uuid.uuid4().hex[:12],
        trigger=trigger,
        started_at=now_iso(),
        provider=provider,
        model=model,
    )


def record(
    state: InvestigationState,
    tools: ToolContext,
    tool: str,
    args: dict[str, Any],
    *,
    in_trigger: bool = False,
) -> Step:
    """Run one read-only tool and append it to the journal as its own turn."""
    result = run_tool(tool, args, tools)
    state.turn += 1
    step = Step(
        number=len(state.steps) + 1,
        turn=state.turn,
        at=now_iso(),
        tool=tool,
        args=args,
        call_id=f"baseline-{uuid.uuid4().hex[:8]}",
        result=result,
        summary=summarise(result),
        in_trigger=in_trigger,
    )
    state.steps.append(step)
    return step
