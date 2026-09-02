"""LLM katmani — saglayicidan bagimsiz arayuz ve fabrikasi."""

from __future__ import annotations

from ..config import Settings
from ..errors import ConfigError
from ..i18n import t
from ..logging import EventLog
from .anthropic_client import (
    SERVER_TOOL_NAMES,
    WEB_FETCH_TOOL,
    WEB_SEARCH_TOOL,
    AnthropicClient,
    supports_adaptive_thinking,
)
from .base import LLMClient, LLMResult, ToolCall, ToolOutcome
from .openai_client import OpenAICompatibleClient, to_openai_tools
from .pricing import Usage, cost_usd, is_local_model, price_for


def build_client(settings: Settings, events: EventLog | None = None) -> LLMClient:
    """Ayarlardaki saglayiciya gore istemciyi kurar."""
    provider = settings.provider
    if provider == "anthropic":
        return AnthropicClient(settings, events=events)
    if provider == "openai":
        return OpenAICompatibleClient(settings, events=events)
    raise ConfigError(t("setup.unknown_provider", provider=provider))


# Geriye donuk ad: eski kod `ClaudeClient` bekliyordu.
ClaudeClient = AnthropicClient

__all__ = [
    "SERVER_TOOL_NAMES",
    "WEB_FETCH_TOOL",
    "WEB_SEARCH_TOOL",
    "AnthropicClient",
    "ClaudeClient",
    "LLMClient",
    "LLMResult",
    "OpenAICompatibleClient",
    "ToolCall",
    "ToolOutcome",
    "Usage",
    "build_client",
    "cost_usd",
    "is_local_model",
    "price_for",
    "supports_adaptive_thinking",
    "to_openai_tools",
]
