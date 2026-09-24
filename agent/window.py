"""Building the conversation for each model call from the saved state.

The constraint is per request, not per investigation: Groq's free tier allows
8K tokens a minute, so a single request larger than that can never be sent,
however long we wait. Gemma's limit is 16K. So the window keeps:

- the system prompt and the alarm, always;
- every step's tool call, always (it is short, and the model must know what
  it already tried);
- the full result of only the most recent steps, and the one-line summary for
  older ones;

and if that is still over the cap, it drops full results one by one, oldest
first, until only summaries remain. Tokens are estimated from characters,
conservatively, before sending; the provider's own count is recorded after.
"""

from __future__ import annotations

import json

from agent.llm.base import Message, ToolCall
from agent.prompt import system_prompt, trigger_message
from agent.state import InvestigationState

CHARS_PER_TOKEN = 3.5  # below the usual 4 for English, because JSON is denser
TOOL_SPEC_TOKENS = 1_300  # measured: the ten tool definitions cost 900 to 1,300


def estimate_tokens(messages: list[Message]) -> int:
    chars = 0
    for m in messages:
        chars += len(m.content)
        for c in m.tool_calls:
            chars += len(c.name) + len(json.dumps(c.arguments))
    return int(chars / CHARS_PER_TOKEN) + TOOL_SPEC_TOKENS


def build(
    state: InvestigationState,
    *,
    full_results: int,
    input_token_cap: int,
    notice: str = "",
) -> tuple[list[Message], int]:
    """The messages to send, and their estimated token count. `notice` is a
    note from the loop itself (a budget nearly spent), sent last."""
    keep_full = full_results
    while True:
        messages = _render(state, keep_full, notice)
        estimate = estimate_tokens(messages)
        if estimate <= input_token_cap or keep_full == 0:
            return messages, estimate
        keep_full -= 1


def _render(state: InvestigationState, keep_full: int, notice: str) -> list[Message]:
    messages = [
        Message("system", system_prompt()),
        Message("user", trigger_message(state.trigger)),
    ]
    full_from = len(state.steps) - keep_full
    turns: dict[int, list] = {}
    for step in state.steps:
        turns.setdefault(step.turn, []).append(step)
    for turn in sorted(turns):
        steps = turns[turn]
        messages.append(
            Message(
                "assistant",
                steps[0].reasoning,
                tool_calls=tuple(
                    ToolCall(s.call_id, s.tool, s.args, s.provider_data) for s in steps
                ),
            )
        )
        for s in steps:
            # The step number leads every result so the model can cite it.
            # Without it the model counted for itself and got it wrong: an
            # M5 answer cited steps 8 and 9 of 6, and the M6 live check cited
            # 1 to 8 for a diagnosis resting on steps 9 and 14.
            body = s.result if s.number > full_from else s.summary
            content = f"Step {s.number}. {body}"
            messages.append(
                Message("tool", content, tool_call_id=s.call_id, name=s.tool)
            )
    if state.hypotheses:
        lines = "\n".join(f"- {h['status']}: {h['text']}" for h in state.hypotheses)
        messages.append(Message("user", f"Your current hypotheses:\n{lines}"))
    if notice:
        messages.append(Message("user", notice))
    return messages
