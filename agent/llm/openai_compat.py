"""Groq and Mistral: both speak the OpenAI chat-completions format.

Two differences matter here, and both are settings rather than subclasses:
Mistral wants the tool's `name` on a tool-result message, and the two
return cached-token counts in different places (Groq reports them; Mistral
does not say).
"""

from __future__ import annotations

import json
from typing import Any

from agent.llm.base import (
    Completion,
    Message,
    ProviderError,
    RetryPolicy,
    ToolCall,
    ToolSpec,
    Usage,
    post_json,
)

RATE_LIMIT_PREFIXES = ("x-ratelimit-", "ratelimit", "retry-after")


def parse_arguments(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparsed": raw}
    return value if isinstance(value, dict) else {"_unparsed": raw}


def rate_limit_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.startswith(RATE_LIMIT_PREFIXES)}


class OpenAICompatible:
    def __init__(
        self,
        *,
        name: str,
        url: str,
        api_key: str,
        model: str,
        tool_message_has_name: bool,
        temperature: float | None = None,
        max_output_tokens: int = 2048,
        retry: RetryPolicy | None = None,
        opener: Any = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = url
        self._api_key = api_key
        self._tool_message_has_name = tool_message_has_name
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._retry = retry or RetryPolicy()
        self._opener = opener

    def request_body(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message(m) for m in messages],
            "max_tokens": self._max_output_tokens,
        }
        # None means the provider's own default, which some models need.
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        return body

    def _message(self, m: Message) -> dict[str, Any]:
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in m.tool_calls
            ]
        if m.role == "tool":
            out["tool_call_id"] = m.tool_call_id
            if self._tool_message_has_name:
                out["name"] = m.name
        return out

    def parse(self, reply: dict[str, Any], headers: dict[str, str]) -> Completion:
        try:
            message = reply["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"{self.name}: no message in reply: {reply!r:.300}")
        calls = tuple(
            ToolCall(
                id=c["id"],
                name=c["function"]["name"],
                arguments=parse_arguments(c["function"].get("arguments")),
            )
            for c in message.get("tool_calls") or []
        )
        usage = reply.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return Completion(
            text=message.get("content") or "",
            tool_calls=calls,
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                cached_tokens=details.get("cached_tokens", 0) or 0,
            ),
            rate_limit_headers=rate_limit_headers(headers),
            raw=reply,
        )

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        kwargs = {"opener": self._opener} if self._opener else {}
        reply, headers = post_json(
            self._url,
            {"authorization": f"Bearer {self._api_key}"},
            self.request_body(messages, tools),
            retry=self._retry,
            **kwargs,
        )
        return self.parse(reply, headers)
