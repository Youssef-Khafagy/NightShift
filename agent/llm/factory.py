"""Build a provider from a name and a model. Keys come from the environment:
`.env` locally, SSM SecureString in Lambda (step 6)."""

from __future__ import annotations

import os

from agent.llm.base import Provider, ProviderError, RetryPolicy
from agent.llm.gemini import Gemini
from agent.llm.openai_compat import OpenAICompatible

KEY_VARIABLES = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
OPENAI_COMPATIBLE = {
    # name: (url, tool message carries the tool's name)
    "groq": ("https://api.groq.com/openai/v1/chat/completions", False),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", True),
}


def api_key(provider: str) -> str:
    variable = KEY_VARIABLES[provider]
    key = os.environ.get(variable, "").strip()
    if not key:
        raise ProviderError(f"{variable} is not set")
    return key


def make_provider(
    provider: str,
    model: str,
    *,
    temperature: float | None = None,
    max_output_tokens: int = 2048,
    retry: RetryPolicy | None = None,
) -> Provider:
    if provider not in KEY_VARIABLES:
        raise ProviderError(
            f"unknown provider {provider!r}; have {sorted(KEY_VARIABLES)}"
        )
    key = api_key(provider)
    if provider == "gemini":
        return Gemini(
            api_key=key,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            retry=retry,
        )
    url, tool_name = OPENAI_COMPATIBLE[provider]
    return OpenAICompatible(
        name=provider,
        url=url,
        api_key=key,
        model=model,
        tool_message_has_name=tool_name,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        retry=retry,
    )
