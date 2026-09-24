"""The investigation loop, driven by a scripted fake model.

The properties that matter: it ends (answer or limit, never a hang), every
step is checkpointed, a crash resumes from the last checkpoint without
repeating or losing a step, and the conversation sent on each call stays
under the per-request cap.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.config import AgentConfig
from agent.llm.base import (
    Completion,
    Message,
    ProviderError,
    ToolCall,
    Usage,
)
from agent.loop import Investigator, parse_hypotheses
from agent.state import InvestigationState, Step
from agent.store import MemoryStore
from agent.tools.context import ToolContext
from agent.vocabulary import COMPONENTS, FAULT_CATEGORIES
from agent.window import build, estimate_tokens

TOPOLOGY = {
    "services": {"orders": {"function": "nightshift-orders", "flags": []}},
    "queues": {},
}
FINISH_ARGS = {
    "root_cause_component": "orders",
    "fault_category": "bad_deploy",
    "confidence": 80,
    "summary": "A deploy broke checkout.",
    "evidence_steps": "1",
}


class Ssm:
    def get_parameter(self, Name):
        return {"Parameter": {"Value": json.dumps(TOPOLOGY)}}


class Session:
    def client(self, name):
        assert name == "ssm", f"unexpected AWS client {name}"
        return Ssm()


def reply(*calls: tuple[str, dict], text: str = "", tokens: int = 100) -> Completion:
    return Completion(
        text=text,
        tool_calls=tuple(
            ToolCall(f"c{i}-{name}", name, args) for i, (name, args) in enumerate(calls)
        ),
        usage=Usage(input_tokens=tokens, output_tokens=10),
    )


class Script:
    """Returns the next scripted reply; an Exception in the script is raised."""

    name, model = "fake", "fake-1"

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.seen: list[list[Message]] = []
        self.max_waits: list[float | None] = []

    def complete(self, messages, tools, *, max_wait=None):
        self.seen.append(messages)
        self.max_waits.append(max_wait)
        item = self.replies.pop(0) if self.replies else reply(("get_topology", {}))
        if isinstance(item, Exception):
            raise item
        return item


def investigator(
    script, config=None, store=None, clock=None
) -> tuple[Investigator, MemoryStore]:
    store = store or MemoryStore()
    config = config or AgentConfig()
    kwargs = {"clock": clock} if clock else {}
    return Investigator(
        script, ToolContext(Session(), config), store, config, **kwargs
    ), store


TRIGGER = {"alarm": "nightshift-orders-errors", "state": "ALARM"}


def test_vocabulary_matches_the_scenario_schema():
    from chaos.schema import Component, FaultCategory

    assert FAULT_CATEGORIES == [c.value for c in FaultCategory]
    assert COMPONENTS == [c.value for c in Component]


def test_a_clean_investigation_ends_with_the_answer_and_a_checkpoint_per_turn():
    script = Script(
        reply(("get_topology", {}), text="Look at the layout first."),
        reply(
            (
                "note_hypotheses",
                {"hypotheses": "likely: bad deploy of orders\npossible: cart down"},
            )
        ),
        reply(("finish_investigation", FINISH_ARGS)),
    )
    inv, store = investigator(script)
    state = inv.run(inv.start(TRIGGER, "inv1"))
    assert state.stop_reason == "finished" and state.final == FINISH_ARGS
    assert [s.tool for s in state.steps] == [
        "get_topology",
        "note_hypotheses",
        "finish_investigation",
    ]
    assert [s.number for s in state.steps] == [1, 2, 3]
    assert state.steps[0].reasoning == "Look at the layout first."
    assert state.hypotheses[0] == {"status": "likely", "text": "bad deploy of orders"}
    assert store.saves == 1 + 3  # start, then one per turn
    assert len(state.calls) == 3 and state.tokens_used == 330
    assert store.load("inv1").to_dict() == state.to_dict()


def test_a_crash_resumes_from_the_last_checkpoint_without_repeating_a_step():
    first = Script(
        reply(("get_topology", {})),
        reply(("get_flag_values", {})),
        RuntimeError("Lambda timed out"),  # not a provider error: a real crash
    )
    inv, store = investigator(first)
    with pytest.raises(RuntimeError):
        inv.run(inv.start(TRIGGER, "inv2"))

    second = Script(reply(("finish_investigation", FINISH_ARGS)))
    inv2, _ = investigator(second, store=store)
    state = inv2.run(inv2.resume("inv2"))
    assert [s.tool for s in state.steps] == [
        "get_topology",
        "get_flag_values",
        "finish_investigation",
    ]
    assert [s.number for s in state.steps] == [1, 2, 3]
    # The resumed call saw the work done before the crash.
    sent = second.seen[0]
    assert [m.tool_calls[0].name for m in sent if m.role == "assistant"] == [
        "get_topology",
        "get_flag_values",
    ]


def test_step_limit_stops_it_and_warns_before():
    inv, _ = investigator(Script(), AgentConfig(max_steps=4))
    state = inv.run(inv.start(TRIGGER))
    assert (
        state.stop_reason == "max_steps"
        and state.final is None
        and len(state.steps) == 4
    )
    last_sent = inv.llm.seen[-1][-1]
    assert last_sent.role == "user" and "nearly spent" in last_sent.content


def test_token_limit_stops_it():
    script = Script(*[reply(("get_topology", {}), tokens=5000) for _ in range(5)])
    inv, _ = investigator(script, AgentConfig(max_tokens_per_investigation=12_000))
    state = inv.run(inv.start(TRIGGER))
    assert state.stop_reason == "max_tokens" and len(state.calls) == 3


def test_wall_clock_limit_stops_it_and_caps_rate_limit_waits():
    ticks = iter(range(0, 10_000, 100))
    inv, _ = investigator(
        Script(), AgentConfig(max_wall_seconds=450), clock=lambda: next(ticks)
    )
    state = inv.run(inv.start(TRIGGER))
    assert state.stop_reason == "max_wall_seconds"
    waits = inv.llm.max_waits
    assert waits == sorted(waits, reverse=True) and waits[-1] < 450


def test_a_provider_failure_ends_it_with_the_reason_saved():
    inv, store = investigator(Script(ProviderError("HTTP 429 ... daily quota", 429)))
    state = inv.run(inv.start(TRIGGER, "inv3"))
    assert state.stop_reason.startswith("provider_error: HTTP 429")
    assert store.load("inv3").stop_reason == state.stop_reason


REJECTED = ProviderError(
    "HTTP 400 from https://api.groq.com: Tool call validation failed: parameters for "
    "tool query_logs did not match schema: `/limit`: maximum: got 100, want 50",
    400,
)


def test_a_provider_rejected_tool_call_goes_back_to_the_model():
    script = Script(
        reply(("get_topology", {})),
        REJECTED,
        reply(("finish_investigation", FINISH_ARGS)),
    )
    inv, _ = investigator(script)
    state = inv.run(inv.start(TRIGGER))
    assert state.stop_reason == "finished" and state.rejected_calls == 1
    notice = script.seen[2][-1]
    assert notice.role == "user" and "got 100, want 50" in notice.content


def test_repeated_rejections_end_it():
    inv, _ = investigator(Script(REJECTED, REJECTED))
    assert inv.run(inv.start(TRIGGER)).stop_reason == "tool_calls_rejected"


def test_other_client_errors_still_end_it():
    inv, _ = investigator(Script(ProviderError("HTTP 400: context too long", 400)))
    assert inv.run(inv.start(TRIGGER)).stop_reason.startswith("provider_error")


def test_an_invalid_answer_is_sent_back_not_accepted():
    bad = dict(FINISH_ARGS, fault_category="gremlins")
    script = Script(
        reply(("get_topology", {})),
        reply(("finish_investigation", bad)),
        reply(("finish_investigation", FINISH_ARGS)),
    )
    inv, _ = investigator(script)
    state = inv.run(inv.start(TRIGGER))
    assert "must be one of" in state.steps[1].result
    assert state.final == FINISH_ARGS and len(state.steps) == 3


def test_two_replies_without_a_tool_call_end_it():
    inv, _ = investigator(
        Script(reply(text="Thinking..."), reply(text="Still thinking"))
    )
    state = inv.run(inv.start(TRIGGER))
    assert state.stop_reason == "no_tool_call"
    assert "Reply with a tool call" in inv.llm.seen[1][-1].content


def test_at_most_three_calls_per_reply():
    script = Script(
        reply(*[("get_topology", {})] * 5), reply(("finish_investigation", FINISH_ARGS))
    )
    inv, _ = investigator(script)
    state = inv.run(inv.start(TRIGGER))
    assert [("skipped" in s.result) for s in state.steps[:5]] == [
        False,
        False,
        False,
        True,
        True,
    ]


def test_hypothesis_lines_are_parsed_and_bad_ones_reported():
    found, bad = parse_hypotheses(
        "- Likely: bad deploy\nruled out: cart\nmaybe the moon\npossible:"
    )
    assert found == [
        {"status": "likely", "text": "bad deploy"},
        {"status": "ruled_out", "text": "cart"},
    ]
    assert bad == ["maybe the moon", "possible:"]


# -- the window --------------------------------------------------------------------


def state_with(n: int, result_chars: int) -> InvestigationState:
    state = InvestigationState("w", TRIGGER, "t", "fake", "m")
    for i in range(1, n + 1):
        state.steps.append(
            Step(
                i,
                i,
                "t",
                "get_topology",
                {},
                f"c{i}",
                "R" * result_chars,
                f"summary {i}",
            )
        )
    return state


def tool_contents(messages):
    return [m.content for m in messages if m.role == "tool"]


def test_only_the_most_recent_results_are_kept_in_full():
    messages, _ = build(state_with(5, 100), full_results=2, input_token_cap=100_000)
    assert tool_contents(messages) == [
        "summary 1",
        "summary 2",
        "summary 3",
        "R" * 100,
        "R" * 100,
    ]


def test_the_window_shrinks_to_fit_the_cap():
    state = state_with(6, 4_000)
    messages, estimate = build(state, full_results=3, input_token_cap=4_000)
    assert estimate <= 4_000
    assert tool_contents(messages).count("R" * 4_000) < 3
    assert estimate == estimate_tokens(messages)


def test_no_fault_with_a_component_is_sent_back():
    """The first live Groq run answered no_fault with component orders."""
    wrong = dict(FINISH_ARGS, fault_category="no_fault", evidence_steps="")
    right = dict(wrong, root_cause_component="none")
    script = Script(
        reply(("get_topology", {})),
        reply(("finish_investigation", wrong)),
        reply(("finish_investigation", right)),
    )
    inv, _ = investigator(script)
    state = inv.run(inv.start(TRIGGER))
    assert "root_cause_component must be none" in state.steps[1].result
    assert state.final == right


def test_skipped_calls_and_answer_attempts_do_not_spend_the_step_budget():
    """The M6 live check hit max_steps with a correct answer in hand: four
    skipped calls and two refused answers had counted as steps."""
    script = Script(
        reply(*[("get_topology", {})] * 6),  # 3 run, 3 skipped
        reply(
            ("finish_investigation", dict(FINISH_ARGS, evidence_steps="4"))
        ),  # refused
        reply(("finish_investigation", FINISH_ARGS)),
    )
    inv, _ = investigator(script, AgentConfig(max_steps=4))
    state = inv.run(inv.start(TRIGGER))
    assert state.stop_reason == "finished" and state.final == FINISH_ARGS


def test_answer_attempts_are_capped():
    bad = dict(FINISH_ARGS, evidence_steps="")
    script = Script(
        reply(("get_topology", {})),
        *[reply(("finish_investigation", bad)) for _ in range(5)],
    )
    inv, _ = investigator(script)
    state = inv.run(inv.start(TRIGGER))
    assert state.stop_reason == "answer_rejected" and len(state.steps) == 4
