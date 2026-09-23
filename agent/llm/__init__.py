"""One interface, three free-tier providers. Swapping models is a config change.

from agent.llm import make_provider
llm = make_provider("groq", "openai/gpt-oss-120b")
reply = llm.complete(messages, tools)
"""

from agent.llm.base import (
    Completion,
    Message,
    Provider,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)
from agent.llm.factory import make_provider

__all__ = [
    "Completion",
    "Message",
    "Provider",
    "ProviderError",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "make_provider",
]
