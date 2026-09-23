#!/usr/bin/env python3
"""One live tool call per LLM provider: proves the key, the wire format and
function calling on the free tier, and prints the rate-limit headers.

    python scripts/llm_check.py                    # every provider with a key
    python scripts/llm_check.py --provider mistral
    python scripts/llm_check.py --save-fixtures    # keep the raw replies for tests

Reads keys from the environment, or from .env if they are not set. Never
prints a key. Each check is one request of a few hundred tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.env import load_dotenv
from agent.llm import Message, ProviderError, ToolSpec, make_provider
from agent.llm.factory import KEY_VARIABLES
from agent.tools import tool_specs

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-3.5-flash-lite",
    "mistral": "ministral-14b-latest",
}
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"

TOOL = ToolSpec(
    "get_alarm",
    "Read the current state of one CloudWatch alarm by name.",
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Alarm name."}},
        "required": ["name"],
    },
)
MESSAGES = [
    Message("system", "You investigate alarms. Always start by reading the alarm."),
    Message("user", "The alarm nightshift-orders-errors just fired. Read it."),
]


def check(provider: str, model: str, save: bool, all_tools: bool = False) -> bool:
    llm = make_provider(provider, model)
    try:
        tools = tool_specs() if all_tools else [TOOL]
        reply = llm.complete(MESSAGES, tools)
    except ProviderError as error:
        print(f"{provider:8} {model}: FAILED: {error}")
        return False
    calls = [(c.name, c.arguments) for c in reply.tool_calls]
    ok = any(
        name == "get_alarm" and "orders-errors" in str(args.get("name", ""))
        for name, args in calls
    )
    print(
        f"{provider:8} {model}: {'OK' if ok else 'NO TOOL CALL'} "
        f"calls={calls} text={reply.text[:80]!r} "
        f"tokens in/out/cached={reply.usage.input_tokens}/"
        f"{reply.usage.output_tokens}/{reply.usage.cached_tokens}"
    )
    for name, value in sorted(reply.rate_limit_headers.items()):
        print(f"           {name}: {value}")
    if save and ok:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / f"{provider}.json").write_text(
            json.dumps({"model": model, "reply": reply.raw}, indent=2) + "\n"
        )
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(KEY_VARIABLES))
    parser.add_argument("--model")
    parser.add_argument("--save-fixtures", action="store_true")
    parser.add_argument(
        "--all-tools",
        action="store_true",
        help="offer the agent's full toolset, to prove every schema is accepted",
    )
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")

    providers = [args.provider] if args.provider else sorted(KEY_VARIABLES)
    results = []
    for provider in providers:
        if not os.environ.get(KEY_VARIABLES[provider]):
            print(f"{provider:8} skipped: {KEY_VARIABLES[provider]} not set")
            results.append(False)
            continue
        model = args.model or DEFAULT_MODELS[provider]
        results.append(check(provider, model, args.save_fixtures, args.all_tools))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
