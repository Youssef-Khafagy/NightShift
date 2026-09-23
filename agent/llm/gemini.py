"""Gemini's generateContent API, translated to and from the shared shapes.

Where it differs from the OpenAI format:
- the system prompt is a separate `systemInstruction`, not a message;
- roles are `user` and `model`, and tool results travel in a `user` turn as
  `functionResponse` parts, all results for one assistant turn together;
- a function call part may carry a `thoughtSignature`, which must be sent
  back unchanged on the next request or Gemini rejects it;
- "thinking" tokens are reported separately and count as output here,
  because they count against the same limits.
"""

from __future__ import annotations

from typing import Any

from agent.llm.base import (
    Completion,
    Message,
    ProviderError,
    RetryPolicy,
    ToolCall,
    ToolSpec,
    Usage,
    bounded,
    post_json,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class Gemini:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float | None = None,
        max_output_tokens: int = 2048,
        retry: RetryPolicy | None = None,
        opener: Any = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._retry = retry or RetryPolicy()
        self._opener = opener

    def request_body(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            role, parts = self._parts(m)
            # Gemini wants turns to alternate, so consecutive tool results
            # (one per call) are merged into a single user turn.
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

        config: dict[str, Any] = {"maxOutputTokens": self._max_output_tokens}
        if self._temperature is not None:
            config["temperature"] = self._temperature
        body: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]
        return body

    @staticmethod
    def _parts(m: Message) -> tuple[str, list[dict[str, Any]]]:
        if m.role == "tool":
            response: dict[str, Any] = {
                "name": m.name,
                "response": {"content": m.content},
            }
            if m.tool_call_id and not m.tool_call_id.startswith("gemini-"):
                response["id"] = m.tool_call_id
            return "user", [{"functionResponse": response}]
        if m.role == "assistant":
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"text": m.content})
            for c in m.tool_calls:
                part: dict[str, Any] = {
                    "functionCall": {"name": c.name, "args": c.arguments}
                }
                if "thoughtSignature" in c.provider_data:
                    part["thoughtSignature"] = c.provider_data["thoughtSignature"]
                parts.append(part)
            return "model", parts
        return "user", [{"text": m.content}]

    def parse(self, reply: dict[str, Any], headers: dict[str, str]) -> Completion:
        candidates = reply.get("candidates") or []
        if not candidates:
            feedback = reply.get("promptFeedback", {})
            raise ProviderError(f"gemini: no candidates; promptFeedback={feedback}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for i, part in enumerate(parts):
            if part.get("thought"):
                continue  # a thought summary, not an answer
            if "functionCall" in part:
                fc = part["functionCall"]
                data = {}
                if "thoughtSignature" in part:
                    data["thoughtSignature"] = part["thoughtSignature"]
                calls.append(
                    ToolCall(
                        # Gemini may omit ids; ours start with "gemini-" so
                        # they are never sent back as if Gemini had made them.
                        id=fc.get("id") or f"gemini-{i}",
                        name=fc["name"],
                        arguments=fc.get("args") or {},
                        provider_data=data,
                    )
                )
            elif "text" in part:
                texts.append(part["text"])
        meta = reply.get("usageMetadata") or {}
        return Completion(
            text="".join(texts),
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=meta.get("promptTokenCount", 0),
                output_tokens=meta.get("candidatesTokenCount", 0)
                + meta.get("thoughtsTokenCount", 0),
                cached_tokens=meta.get("cachedContentTokenCount", 0),
            ),
            rate_limit_headers={},
            raw=reply,
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_wait: float | None = None,
    ) -> Completion:
        kwargs = {"opener": self._opener} if self._opener else {}
        reply, headers = post_json(
            f"{BASE_URL}/{self.model}:generateContent",
            {"x-goog-api-key": self._api_key},
            self.request_body(messages, tools),
            retry=bounded(self._retry, max_wait),
            **kwargs,
        )
        return self.parse(reply, headers)
