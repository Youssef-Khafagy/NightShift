"""The provider layer: retries, and translation to and from each wire format.

The reply fixtures are written from each provider's documented format. Step 2
replaces the key ones with real replies captured from one live call each.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from email.message import Message as Headers
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.llm import (
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
    make_provider,
)
from agent.llm.base import RetryPolicy, post_json, retry_after_seconds
from agent.llm.gemini import Gemini
from agent.llm.openai_compat import OpenAICompatible

TOOL = ToolSpec(
    "get_alarm",
    "Read one alarm.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)


class Reply:
    def __init__(self, body: dict, headers: dict | None = None) -> None:
        self._body = json.dumps(body).encode()
        self.headers = Headers()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    h = Headers()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://x", code, "err", h, io.BytesIO(b"detail"))


class Opener:
    """Plays back a script of replies and errors, recording each request."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.requests: list[dict] = []

    def __call__(self, request, timeout):
        self.requests.append(json.loads(request.data))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def policy(**kw) -> tuple[RetryPolicy, list[float]]:
    slept: list[float] = []
    return RetryPolicy(sleep=slept.append, **kw), slept


# -- retries -----------------------------------------------------------------


def test_429_waits_for_retry_after_then_succeeds():
    retry, slept = policy()
    opener = Opener(http_error(429, {"retry-after": "7"}), Reply({"ok": 1}))
    body, _ = post_json("https://x", {}, {}, retry=retry, opener=opener)
    assert body == {"ok": 1}
    assert slept == [7.0]


def test_429_gives_up_when_the_wait_would_exceed_the_budget():
    # A daily quota answers with a long retry-after: fail now, not in an hour.
    retry, slept = policy(max_total_wait=60)
    opener = Opener(http_error(429, {"retry-after": "3600"}))
    with pytest.raises(ProviderError) as info:
        post_json("https://x", {}, {}, retry=retry, opener=opener)
    assert info.value.status == 429
    assert slept == []


def test_server_errors_back_off_exponentially():
    retry, slept = policy(default_wait=2)
    opener = Opener(http_error(503), http_error(502), Reply({"ok": 1}))
    post_json("https://x", {}, {}, retry=retry, opener=opener)
    assert slept == [2, 4]


def test_client_errors_are_not_retried():
    retry, slept = policy()
    opener = Opener(http_error(400))
    with pytest.raises(ProviderError) as info:
        post_json("https://x", {}, {}, retry=retry, opener=opener)
    assert info.value.status == 400
    assert "detail" in str(info.value)
    assert slept == []


def test_attempts_are_bounded():
    retry, slept = policy(max_attempts=3, default_wait=1)
    opener = Opener(http_error(500), http_error(500), http_error(500))
    with pytest.raises(ProviderError):
        post_json("https://x", {}, {}, retry=retry, opener=opener)
    assert len(opener.requests) == 3
    assert slept == [1, 2]


def test_unreadable_retry_after_uses_the_default():
    assert retry_after_seconds({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, 5) == 5
    assert retry_after_seconds({}, 5) == 5
    assert retry_after_seconds({"retry-after": "2.5"}, 5) == 2.5


# -- OpenAI-compatible (Groq, Mistral) -----------------------------------------

CONVERSATION = [
    Message("system", "You are on call."),
    Message("user", "orders-errors is firing"),
    Message(
        "assistant",
        "",
        tool_calls=(ToolCall("call_1", "get_alarm", {"name": "orders-errors"}),),
    ),
    Message("tool", '{"state": "ALARM"}', tool_call_id="call_1", name="get_alarm"),
]

OPENAI_REPLY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "get_alarm",
                            "arguments": '{"name": "cart-errors"}',
                        },
                    }
                ],
            }
        }
    ],
    "usage": {
        "prompt_tokens": 120,
        "completion_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": 100},
    },
}


def openai(name="groq", tool_name=False, opener=None, **kw) -> OpenAICompatible:
    return OpenAICompatible(
        name=name,
        url="https://x",
        api_key="k",
        model="m",
        tool_message_has_name=tool_name,
        opener=opener,
        **kw,
    )


def test_openai_request_carries_tool_calls_and_results():
    body = openai().request_body(CONVERSATION, [TOOL])
    assert [m["role"] for m in body["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    call = body["messages"][2]["tool_calls"][0]
    assert call["id"] == "call_1"
    assert json.loads(call["function"]["arguments"]) == {"name": "orders-errors"}
    assert body["messages"][3]["tool_call_id"] == "call_1"
    assert "name" not in body["messages"][3]
    assert body["tools"][0]["function"]["name"] == "get_alarm"
    assert "temperature" not in body  # provider default unless configured


def test_mistral_tool_results_carry_the_tool_name():
    body = openai("mistral", tool_name=True).request_body(CONVERSATION, [TOOL])
    assert body["messages"][3]["name"] == "get_alarm"


def test_temperature_is_sent_only_when_configured():
    assert openai(temperature=0.0).request_body(CONVERSATION, [])["temperature"] == 0.0


def test_openai_reply_is_parsed():
    opener = Opener(
        Reply(OPENAI_REPLY, {"x-ratelimit-remaining-tokens": "7000", "date": "x"})
    )
    reply = openai(opener=opener).complete(CONVERSATION, [TOOL])
    assert reply.tool_calls == (
        ToolCall("call_2", "get_alarm", {"name": "cart-errors"}),
    )
    assert (
        reply.usage.input_tokens,
        reply.usage.output_tokens,
        reply.usage.cached_tokens,
    ) == (120, 15, 100)
    assert reply.usage.total == 135
    assert reply.rate_limit_headers == {"x-ratelimit-remaining-tokens": "7000"}


def test_malformed_arguments_are_kept_not_raised():
    bad = json.loads(json.dumps(OPENAI_REPLY))
    bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
        "{name: oops"
    )
    reply = openai().parse(bad, {})
    assert reply.tool_calls[0].arguments == {"_unparsed": "{name: oops"}


def test_a_reply_without_a_message_raises():
    with pytest.raises(ProviderError):
        openai().parse({"error": "x"}, {})


# -- Gemini ----------------------------------------------------------------------

GEMINI_REPLY = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {"text": "thinking out loud", "thought": True},
                    {
                        "functionCall": {
                            "name": "get_alarm",
                            "args": {"name": "cart-errors"},
                        },
                        "thoughtSignature": "sig-abc",
                    },
                ],
            }
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 200,
        "candidatesTokenCount": 20,
        "thoughtsTokenCount": 30,
        "cachedContentTokenCount": 150,
    },
}


def test_gemini_request_shape():
    body = Gemini(api_key="k", model="m").request_body(CONVERSATION, [TOOL])
    assert body["systemInstruction"] == {"parts": [{"text": "You are on call."}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][1]["parts"][0]["functionCall"]["name"] == "get_alarm"
    result = body["contents"][2]["parts"][0]["functionResponse"]
    assert result == {
        "name": "get_alarm",
        "response": {"content": '{"state": "ALARM"}'},
        "id": "call_1",
    }
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "get_alarm"


def test_gemini_merges_consecutive_tool_results_into_one_turn():
    two = [
        Message("user", "go"),
        Message(
            "assistant",
            tool_calls=(ToolCall("gemini-0", "a", {}), ToolCall("gemini-1", "b", {})),
        ),
        Message("tool", "1", tool_call_id="gemini-0", name="a"),
        Message("tool", "2", tool_call_id="gemini-1", name="b"),
    ]
    body = Gemini(api_key="k", model="m").request_body(two, [])
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    responses = [p["functionResponse"] for p in body["contents"][2]["parts"]]
    assert [r["name"] for r in responses] == ["a", "b"]
    # Ids we made up are never sent back as if Gemini had issued them.
    assert all("id" not in r for r in responses)


def test_gemini_thought_signature_round_trips():
    gemini = Gemini(api_key="k", model="m")
    reply = gemini.parse(GEMINI_REPLY, {})
    call = reply.tool_calls[0]
    assert call.provider_data == {"thoughtSignature": "sig-abc"}
    body = gemini.request_body(
        [Message("user", "go"), Message("assistant", tool_calls=(call,))], []
    )
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "sig-abc"


def test_gemini_reply_is_parsed_and_thoughts_count_as_output():
    reply = Gemini(api_key="k", model="m").parse(GEMINI_REPLY, {})
    assert reply.text == ""  # the thought summary is not the answer
    assert reply.tool_calls[0].arguments == {"name": "cart-errors"}
    assert (
        reply.usage.input_tokens,
        reply.usage.output_tokens,
        reply.usage.cached_tokens,
    ) == (200, 50, 150)


def test_gemini_blocked_prompt_raises():
    with pytest.raises(ProviderError, match="promptFeedback"):
        Gemini(api_key="k", model="m").parse(
            {"promptFeedback": {"blockReason": "SAFETY"}}, {}
        )


# -- factory -----------------------------------------------------------------


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="MISTRAL_API_KEY is not set"):
        make_provider("mistral", "mistral-small-latest")


def test_unknown_provider_is_refused(monkeypatch):
    with pytest.raises(ProviderError, match="unknown provider"):
        make_provider("ollama", "x")


def test_each_provider_builds(monkeypatch):
    for variable in ("GROQ_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.setenv(variable, "k")
    assert make_provider("groq", "m").name == "groq"
    assert make_provider("gemini", "m").name == "gemini"
    assert make_provider("mistral", "m").name == "mistral"


# -- real replies ------------------------------------------------------------

FIXTURES = sorted((REPO_ROOT / "tests" / "fixtures" / "llm").glob("*.json"))


@pytest.mark.skipif(not FIXTURES, reason="no live replies saved yet")
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_replies_parse_to_a_tool_call(path):
    """Replies saved by scripts/llm_check.py --save-fixtures, exactly as each
    provider sent them. Guards the parsers against a format drifting from
    what the docs describe."""
    saved = json.loads(path.read_text())
    if path.stem == "gemini":
        reply = Gemini(api_key="k", model=saved["model"]).parse(saved["reply"], {})
    else:
        reply = openai(path.stem).parse(saved["reply"], {})
    assert [c.name for c in reply.tool_calls] == ["get_alarm"]
    assert "orders-errors" in reply.tool_calls[0].arguments["name"]
    assert reply.usage.input_tokens > 0
