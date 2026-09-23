"""Every limit the agent runs under, in one place. Values, not constants
scattered through the code, so a benchmark run can record exactly what it ran
with and a change is a config change."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    provider: str = "groq"
    model: str = "openai/gpt-oss-120b"

    # Hard limits per investigation (enforced by the loop from step 4).
    max_tokens_per_investigation: int = 40_000
    max_steps: int = 15
    max_wall_seconds: int = 840  # under Lambda's 900 s ceiling

    # Tool output. Groq's 8K tokens-per-minute limit caps a single request,
    # so no one tool result may crowd out the rest of the conversation.
    max_tool_output_chars: int = 2_500

    # Logs Insights bills bytes scanned: the budget in COST.md is 20 MB per
    # investigation, and the widest window a query may cover.
    log_scan_cap_bytes: int = 20 * 1024 * 1024
    max_log_query_minutes: int = 60
