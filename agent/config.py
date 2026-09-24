"""Every limit the agent runs under, in one place. Values, not constants
scattered through the code, so a benchmark run can record exactly what it ran
with and a change is a config change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class AgentConfig:
    provider: str = "groq"
    model: str = "openai/gpt-oss-120b"

    # Hard limits per investigation (enforced by the loop from step 4).
    max_tokens_per_investigation: int = 40_000
    max_steps: int = 15
    max_wall_seconds: int = 840  # under Lambda's 900 s ceiling

    # The conversation sent on each call. Groq's free tier allows 8K tokens a
    # minute, which caps a single request, and the reply's max_tokens counts
    # against it too; 6K in leaves room for a 1.5K reply. Raise it per run for
    # providers with larger limits (Gemma 16K, Flash Lite and Mistral far more).
    input_token_cap: int = 6_000
    max_output_tokens: int = 1_500
    full_results_kept: int = 3

    # Tool output. Groq's 8K tokens-per-minute limit caps a single request,
    # so no one tool result may crowd out the rest of the conversation.
    max_tool_output_chars: int = 2_500

    # Logs Insights bills bytes scanned: the budget in COST.md is 20 MB per
    # investigation, and the widest window a query may cover.
    log_scan_cap_bytes: int = 20 * 1024 * 1024
    max_log_query_minutes: int = 60


# Default model and per-request input cap for each provider, from the limits
# measured in COST.md. The cap is what matters on the free tiers: a request
# bigger than the per-minute token limit can never be sent.
class ProviderDefaults(TypedDict):
    model: str
    input_token_cap: int


PROVIDER_DEFAULTS: dict[str, ProviderDefaults] = {
    "groq": {"model": "openai/gpt-oss-120b", "input_token_cap": 6_000},  # 8K TPM
    "gemini": {"model": "gemini-3.5-flash-lite", "input_token_cap": 24_000},  # 250K TPM
    "mistral": {"model": "ministral-14b-latest", "input_token_cap": 24_000},  # 937K TPM
}
GEMMA_INPUT_TOKEN_CAP = 14_000  # Gemma 4 on the Gemini API: 16K TPM


def for_provider(provider: str, model: str | None = None, **overrides) -> AgentConfig:
    defaults = PROVIDER_DEFAULTS[provider]
    chosen = model or defaults["model"]
    cap = (
        GEMMA_INPUT_TOKEN_CAP
        if chosen.startswith("gemma")
        else defaults["input_token_cap"]
    )
    return AgentConfig(
        provider=provider, model=chosen, input_token_cap=cap, **overrides
    )
