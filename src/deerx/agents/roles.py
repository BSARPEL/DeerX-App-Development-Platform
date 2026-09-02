"""Rol -> ajan fabrikasi.

Her rol yalnizca isini yapmak icin gereken araclari gorur. Genis arac listesi
hem token maliyetini artirir hem de yanlis arac secme olasiligini yukseltir.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Settings
from ..llm import WEB_FETCH_TOOL, WEB_SEARCH_TOOL, LLMClient
from ..logging import EventLog
from ..tools import TOOLSETS, ToolContext, ToolRegistry
from .base import Agent
from .prompts import compose_system

# Sunucu tarafi araclara ihtiyac duyan roller. Digerleri web'e yerel
# `fetch_url` uzerinden erisir (ve yalnizca acikca istendiginde).
SERVER_TOOLS_BY_ROLE: dict[str, list[dict[str, Any]]] = {
    "researcher": [WEB_SEARCH_TOOL, WEB_FETCH_TOOL],
    "architect": [WEB_SEARCH_TOOL],
    "assessor": [WEB_SEARCH_TOOL],
    # Uygulayicilar da guncel API/surum bilgisine ihtiyac duyar.
    "backend": [WEB_SEARCH_TOOL],
    "frontend": [WEB_SEARCH_TOOL],
    "staging": [WEB_SEARCH_TOOL],
}

# Uzun surecek roller icin daha genis iterasyon butcesi.
ITERATION_BUDGET: dict[str, int] = {
    # Danisman bir sohbet turudur, bir faz degil: okur, cevaplar, belki bir
    # kayit degistirir. Genis butce burada bekleme suresine donusur --
    # kullanici cevabini bekliyor.
    "danisman": 12,
    "analyst": 30,
    "researcher": 35,
    "assessor": 30,
    "mockup": 30,
    "architect": 35,
    "planner": 25,
    "backend": 45,
    "frontend": 45,
    "qa": 45,
    "reviewer": 35,
    "staging": 40,
    "live": 30,
}


def build_agent(
    role: str,
    *,
    settings: Settings,
    client: LLMClient,
    registry: ToolRegistry,
    context: ToolContext,
    events: EventLog,
    extra_system: str = "",
    stream: bool = True,
    should_stop: Callable[[], bool] | None = None,
) -> Agent:
    """Verilen rol icin arac kumesi ve prompt'u baglanmis bir ajan uretir."""
    if role not in TOOLSETS:
        raise KeyError(f"Bilinmeyen rol: {role}. Mevcut: {', '.join(sorted(TOOLSETS))}")

    tools = registry.subset(TOOLSETS[role])
    # Sunucu tarafi web araclari Anthropic altyapisinda calisir; yerel bir
    # modelde karsiligi yoktur, orada yerel `web_search` devreye girer.
    server_tools = (
        SERVER_TOOLS_BY_ROLE.get(role, [])
        if settings.enable_web and settings.supports_server_tools
        else []
    )

    return Agent(
        role=role,
        system_prompt=compose_system(role, settings, extra=extra_system),
        registry=tools,
        context=context,
        client=client,
        events=events,
        server_tools=server_tools,
        max_iterations=min(ITERATION_BUDGET.get(role, 30), settings.max_iterations),
        stream=stream,
        should_stop=should_stop,
    )
