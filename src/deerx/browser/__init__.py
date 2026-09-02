"""Sunucudaki gercek Chrome'u ajanin tarayicisi yapan katman.

Uc parca:
  * `policy`  — nereye gidilebilecegine dair tek karar noktasi
  * `proxy`   — o karari ag katmaninda uygulayan filtre vekili
  * `session` — tek, uzun omurlu Chrome oturumu

Araclar `deerx.tools.browser` icinde; bu paket yalnizca altyapidir.
"""

from .policy import UrlBlocked, UrlPolicy
from .proxy import FilteringProxy
from .session import BrowserBinary, BrowserSession, find_browser

__all__ = [
    "BrowserBinary",
    "BrowserSession",
    "FilteringProxy",
    "UrlBlocked",
    "UrlPolicy",
    "find_browser",
]
