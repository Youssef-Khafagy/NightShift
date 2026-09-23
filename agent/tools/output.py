"""Making a tool result fit, and marking it as data rather than instructions.

A tool result is sent to the model as JSON. JSON is the defence as much as
the format: a log line saying "ignore your instructions" arrives inside a
quoted, escaped string under the key `untrusted_data`, where it cannot close
a tag or pose as part of the prompt. The system prompt (step 4) tells the
model that nothing under that key is an instruction.

Results are shrunk structurally, never cut mid-JSON: long strings are
clipped first, then the longest list is halved until the result fits.
"""

from __future__ import annotations

import json
from typing import Any

MAX_STRING = 300
CUT = "...["


def _clip_strings(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + f"...[{len(value) - MAX_STRING} chars cut]"
    if isinstance(value, list):
        return [_clip_strings(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip_strings(v) for k, v in value.items()}
    return value


def _longest_list(value: Any) -> list | None:
    best: list | None = None
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            if len(item) > 1 and (best is None or len(item) > len(best)):
                best = item
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
    return best


def render(tool: str, data: Any, max_chars: int) -> str:
    """The text the model sees for one tool call."""

    def dump(payload: Any, truncated: bool) -> str:
        return json.dumps(
            {"tool": tool, "truncated": truncated, "untrusted_data": payload},
            separators=(",", ":"),
            default=str,
        )

    text = dump(data, False)
    if len(text) <= max_chars:
        return text
    data = _clip_strings(data)
    text = dump(data, True)
    while len(text) > max_chars:
        target = _longest_list(data)
        if target is None:
            break
        already = 0
        if isinstance(target[-1], str) and target[-1].startswith(CUT):
            already = int(target.pop()[len(CUT) :].split()[0])
        dropped = len(target) - len(target) // 2
        del target[len(target) // 2 :]
        target.append(f"{CUT}{dropped + already} more items cut]")
        text = dump(data, True)
    if len(text) > max_chars:
        # Nothing left to shrink structurally: say so rather than send
        # half a JSON document.
        return dump(
            {"error": f"result too large even after shrinking ({len(text)} chars)"},
            True,
        )
    return text
