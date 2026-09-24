"""The investigation loop. No framework: a while loop, a model, ten read-only
tools and two control tools.

Each turn: rebuild the conversation from state, ask the model, run the tool
calls it asked for, save a checkpoint. Stop when the model calls
finish_investigation or a hard limit is reached (steps, tokens, wall clock).
Stopping on a limit is not an error: the investigation ends with no answer
and the reason recorded, which the report (step 5) turns into
insufficient_evidence.

A crash anywhere loses at most the turn in progress: the next run loads the
last checkpoint and carries on from the same step.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent import report
from agent.config import AgentConfig
from agent.llm.base import Provider, ProviderError, ToolSpec
from agent.state import Call, InvestigationState, Step
from agent.store import Store
from agent.tools import run_tool, tool_specs
from agent.tools.args import problems
from agent.tools.context import ToolContext
from agent.vocabulary import COMPONENTS, FAULT_CATEGORIES
from agent.window import build

MAX_CALLS_PER_TURN = 3
MAX_TEXT_ONLY_TURNS = 2
MAX_FINISH_ATTEMPTS = 3
STATUSES = ("likely", "possible", "ruled_out")

NOTE_HYPOTHESES = ToolSpec(
    "note_hypotheses",
    "Replace your list of hypotheses. One per line, as 'status: hypothesis', where "
    "status is likely, possible or ruled_out.",
    {
        "type": "object",
        "properties": {"hypotheses": {"type": "string"}},
        "required": ["hypotheses"],
    },
)
FINISH = ToolSpec(
    "finish_investigation",
    "Give your conclusion and end the investigation.",
    {
        "type": "object",
        "properties": {
            "root_cause_component": {"type": "string", "enum": COMPONENTS},
            "fault_category": {"type": "string", "enum": FAULT_CATEGORIES},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "summary": {
                "type": "string",
                "description": "What happened, in two or three sentences.",
            },
            "evidence_steps": {
                "type": "string",
                "description": "Step numbers you relied on, comma separated, e.g. 2,5,6",
            },
            "actions": {
                "type": "string",
                "description": "Fixes from the allowlist, one per line, each run only if a "
                "human approves it: 'rollback_alias service=<cart|orders|payments|"
                "fulfillment>', 'set_operational_flag name=<payments_degraded_mode|"
                "checkout_rate_limit> value=<v>', 'pause_queue_consumer', "
                "'resume_queue_consumer', 'redrive_dlq'. Empty if none fits.",
            },
            "proposed_actions": {
                "type": "string",
                "description": "Anything else a human should do, one per line.",
            },
        },
        "required": [
            "root_cause_component",
            "fault_category",
            "confidence",
            "summary",
            "evidence_steps",
        ],
    },
)


def tool_steps(state: InvestigationState) -> int:
    """Steps that count toward max_steps: tool calls that actually ran. A
    skipped call, a note or an answer attempt spends no AWS read, and in the
    M6 live check counting them used up the budget before a correct answer
    could be accepted."""
    return sum(
        1
        for s in state.steps
        if s.tool not in (NOTE_HYPOTHESES.name, FINISH.name)
        and not s.result.startswith('{"error": "skipped')
    )


def finish_attempts(state: InvestigationState) -> int:
    return sum(1 for s in state.steps if s.tool == FINISH.name)


def rejected_tool_call(error: ProviderError) -> bool:
    return error.status == 400 and "tool call validation failed" in str(error).lower()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def summarise(result: str, limit: int = 240) -> str:
    """The short form of a result, kept once the full one is too old."""
    try:
        data = json.loads(result).get("untrusted_data", result)
        text = json.dumps(data, separators=(",", ":"), default=str)
    except (ValueError, AttributeError):
        text = result
    if len(text) <= limit:
        return text
    return text[:limit] + "...(older result, shortened)"


def parse_hypotheses(text: str) -> tuple[list[dict[str, str]], list[str]]:
    found, bad = [], []
    for line in filter(None, (raw.strip(" -*\t") for raw in text.splitlines())):
        status, sep, claim = line.partition(":")
        status = status.strip().lower().replace(" ", "_")
        if not sep or status not in STATUSES or not claim.strip():
            bad.append(line)
            continue
        found.append({"status": status, "text": claim.strip()})
    return found, bad


class Investigator:
    def __init__(
        self,
        llm: Provider,
        tools: ToolContext,
        store: Store,
        config: AgentConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.store = store
        self.config = config
        self.clock = clock
        self.specs = [*tool_specs(), NOTE_HYPOTHESES, FINISH]

    def start(
        self, trigger: dict[str, Any], investigation_id: str | None = None
    ) -> InvestigationState:
        state = InvestigationState(
            investigation_id=investigation_id or uuid.uuid4().hex[:12],
            trigger=trigger,
            started_at=now_iso(),
            provider=self.llm.name,
            model=self.llm.model,
        )
        self.store.save(state)
        return state

    def resume(self, investigation_id: str) -> InvestigationState:
        state = self.store.load(investigation_id)
        if state is None:
            raise KeyError(f"no checkpoint for {investigation_id}")
        return state

    # -- the loop --------------------------------------------------------------

    def run(self, state: InvestigationState) -> InvestigationState:
        cfg = self.config
        self.tools.log_bytes_scanned = state.log_bytes_scanned
        began, used_before = self.clock(), state.wall_seconds_used
        text_only = rejected = 0
        self._pending_notice = ""
        while not state.finished:
            elapsed = used_before + self.clock() - began
            limit = self._limit_reached(state, elapsed)
            if limit:
                return self._stop(state, limit, used_before, began)

            messages, estimate = build(
                state,
                full_results=cfg.full_results_kept,
                input_token_cap=cfg.input_token_cap,
                notice=self._pending_notice or self._notice(state, elapsed, text_only),
            )
            self._pending_notice = ""
            try:
                # Waiting out a rate limit must not outlast the wall clock;
                # 30 s are kept back to save the checkpoint and stop cleanly.
                reply = self.llm.complete(
                    messages, self.specs, max_wait=cfg.max_wall_seconds - elapsed - 30
                )
            except ProviderError as error:
                if not rejected_tool_call(error):
                    return self._stop(
                        state, f"provider_error: {error}"[:300], used_before, began
                    )
                # Groq checks tool arguments against the schema itself and
                # answers 400 instead of passing a bad call through. That is
                # the model's mistake and it can fix it, so it gets the
                # message back, like any other invalid call.
                rejected += 1
                state.rejected_calls += 1
                if rejected >= MAX_TEXT_ONLY_TURNS:
                    return self._stop(state, "tool_calls_rejected", used_before, began)
                self._pending_notice = (
                    "Your last reply was rejected before any tool ran: "
                    f"{str(error)[-400:]} Fix the arguments and call again."
                )
                continue
            rejected = 0

            state.turn += 1
            state.calls.append(
                Call(
                    turn=state.turn,
                    at=now_iso(),
                    provider=self.llm.name,
                    model=self.llm.model,
                    input_tokens=reply.usage.input_tokens,
                    output_tokens=reply.usage.output_tokens,
                    cached_tokens=reply.usage.cached_tokens,
                    estimated_request_tokens=estimate,
                    rate_limit_headers=reply.rate_limit_headers,
                )
            )
            if not reply.tool_calls:
                text_only += 1
                if text_only >= MAX_TEXT_ONLY_TURNS:
                    return self._stop(state, "no_tool_call", used_before, began)
            else:
                text_only = 0
                for index, call in enumerate(reply.tool_calls):
                    self._step(state, call, reply.text if index == 0 else "", index)
                    if state.finished:
                        break
            state.log_bytes_scanned = self.tools.log_bytes_scanned
            state.wall_seconds_used = used_before + self.clock() - began
            self.store.save(state)
        return state

    def _step(
        self, state: InvestigationState, call, reasoning: str, index: int
    ) -> None:
        if index >= MAX_CALLS_PER_TURN:
            result = json.dumps(
                {"error": f"skipped: at most {MAX_CALLS_PER_TURN} calls per reply"}
            )
        elif call.name == FINISH.name:
            result = self._finish(state, call.arguments)
        elif call.name == NOTE_HYPOTHESES.name:
            result = self._hypotheses(state, call.arguments)
        else:
            result = run_tool(call.name, call.arguments, self.tools)
        state.steps.append(
            Step(
                number=len(state.steps) + 1,
                turn=state.turn,
                at=now_iso(),
                tool=call.name,
                args=call.arguments,
                call_id=call.id,
                result=result,
                summary=summarise(result),
                reasoning=reasoning[:1000],
                provider_data=call.provider_data,
            )
        )

    def _finish(self, state: InvestigationState, args: dict[str, Any]) -> str:
        found = problems(FINISH.parameters, args)
        if not found:
            # The report's own rules (no_fault means component none, evidence
            # must be real tool steps) are checked here too, so a bad answer
            # goes back to the model instead of into the results.
            try:
                report.from_answer(state, args)
            except ValueError as error:
                found = str(error).split("; ")
        if found:
            return json.dumps(
                {"error": "finish_investigation rejected", "problems": found}
            )
        state.final = args
        state.finished = True
        state.stop_reason = "finished"
        return json.dumps({"ok": "investigation closed"})

    def _hypotheses(self, state: InvestigationState, args: dict[str, Any]) -> str:
        found = problems(NOTE_HYPOTHESES.parameters, args)
        if found:
            return json.dumps({"error": "invalid arguments", "problems": found})
        new, bad = parse_hypotheses(args["hypotheses"])
        if new:
            state.hypothesis_changes.append(
                {"step": len(state.steps) + 1, "before": state.hypotheses, "after": new}
            )
            state.hypotheses = new
        reply: dict[str, Any] = {"ok": f"{len(new)} hypotheses recorded"}
        if bad:
            reply["ignored_lines"] = bad
        return json.dumps(reply)

    # -- limits ----------------------------------------------------------------

    def _limit_reached(self, state: InvestigationState, elapsed: float) -> str:
        cfg = self.config
        if tool_steps(state) >= cfg.max_steps:
            return "max_steps"
        if finish_attempts(state) >= MAX_FINISH_ATTEMPTS:
            return "answer_rejected"
        if state.tokens_used >= cfg.max_tokens_per_investigation:
            return "max_tokens"
        if elapsed >= cfg.max_wall_seconds:
            return "max_wall_seconds"
        return ""

    def _notice(self, state: InvestigationState, elapsed: float, text_only: int) -> str:
        cfg = self.config
        near = (
            tool_steps(state) >= cfg.max_steps - 2
            or state.tokens_used >= 0.8 * cfg.max_tokens_per_investigation
            or elapsed >= 0.8 * cfg.max_wall_seconds
        )
        if near:
            return (
                "Your budget for this investigation is nearly spent. Call "
                "finish_investigation now with your best conclusion; use "
                "insufficient_evidence if you cannot tell."
            )
        if text_only:
            return "Reply with a tool call. To conclude, call finish_investigation."
        return ""

    def _stop(
        self, state: InvestigationState, reason: str, used_before: float, began: float
    ) -> InvestigationState:
        state.finished = True
        state.stop_reason = reason
        state.wall_seconds_used = used_before + self.clock() - began
        self.store.save(state)
        return state
