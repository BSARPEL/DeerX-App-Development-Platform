"""Web araclari.

    web_search   : arama motorunda sorgular (yerel modellerin tek arama yolu)
    fetch_url    : bir sayfayi cekip *bilgi tabanina indeksler* (kalici hale getirir)
    browse_page  : JavaScript ile uretilen sayfalari basliksiz tarayiciyla okur

Anthropic saglayicisinda arama icin Claude'un sunucu tarafi `web_search` araci
kullanilir — alintili sonuc doner ve daha guveniliridir. Yerel bir modelde
(vLLM, Ollama) oyle bir sey yok; oradaki tek arama yolu buradaki `web_search`tir.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult

_USER_AGENT = "Mozilla/5.0 (compatible; DeerXAgent/0.1; +https://example.invalid/deerx)"
_MAX_BYTES = 3_000_000


def _tekrar_notu(ctx: ToolContext, url: str) -> str:
    """Ayni adres ikinci kez dustugunde modele durmasini soyler.

    Hata mesaji adresi zaten soyluyordu, ama "bunu zaten denedin" demiyordu.
    Olculdu: gercek bir kosuda arastirmaci ayni 403 veren adresi on kez
    denedi ve tur butcesinin dortte birini oraya harcadi.
    """
    kac = ctx.note_fetch_failure(url)
    return t("web.already_failed", count=kac) if kac >= 2 else ""


def _guard_url(url: str) -> str:
    """Semayi dogrular ve ic ag adreslerine istek atilmasini engeller (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError(t("web.scheme_only", url=url))
    if not parsed.hostname:
        raise ToolError(t("web.bad_url", url=url))

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ToolError(t("web.dns_failed", host=parsed.hostname, error=exc)) from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local:
            raise ToolError(
                f"Ic ag adresine istek engellendi: {parsed.hostname} -> {address}"
            )
    return url


def _http_get(url: str, **kwargs: Any) -> Any:
    import httpx

    headers = {"User-Agent": _USER_AGENT, **kwargs.pop("headers", {})}
    with httpx.Client(follow_redirects=True, timeout=20.0, headers=headers) as client:
        response = client.get(url, **kwargs)
        response.raise_for_status()
        return response


# DuckDuckGo'nun iki anahtarsiz ucu. `lite` daha sade ve hizli; engellenirse
# `html` ucu genelde yanit vermeye devam eder.
_DDG_ENDPOINTS = (
    ("https://lite.duckduckgo.com/lite/", ("a.result-link", "a[href^=http]")),
    ("https://html.duckduckgo.com/html/", ("a.result__a", "a[href^=http]")),
)

# Sonuc kabuguna ait olmayan baglantilar (DuckDuckGo'nun kendi sayfalari).
_DDG_NOISE = ("duckduckgo.com", "duck.co", "spreadprivacy.com")


class SearchBlocked(ToolError):
    """Saglayici istegi reddetti — "sonuc yok" ile karistirilmamali."""


def _ddg_parse(body: str, selectors: tuple[str, ...], limit: int) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body, "html.parser")
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for selector in selectors:
        for link in soup.select(selector):
            href = link.get("href", "")
            if not href.startswith("http") or any(n in href for n in _DDG_NOISE):
                continue
            if href in seen:
                continue
            title = link.get_text(" ", strip=True)
            if not title:
                continue
            seen.add(href)
            # Sade duzende ozet, baglantinin satirinin bir sonrakindedir;
            # html duzeninde ayni kapsayicinin icindedir.
            row = link.find_parent("tr")
            snippet_node = row.find_next_sibling("tr") if row else link.find_parent("div")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            results.append({"title": title, "url": href, "snippet": snippet[:300]})
            if len(results) >= limit:
                return results
        if results:
            break
    return results


def _search_duckduckgo(query: str, limit: int) -> list[dict[str, str]]:
    """Anahtarsiz arama — DuckDuckGo'nun sade uclari.

    En iyi caba. DuckDuckGo kendini tanitan istemcileri sik sik engeller ve
    birkac istekten sonra oran siniri uygular; bu yuzden iki uc sirayla
    denenir ve engellenme "sonuc yok"tan ayri raporlanir. Kesintisiz arama
    gerekiyorsa `search_provider` ile anahtarli bir uca gecin.
    """
    import httpx

    last_error: Exception | None = None
    blocked = False
    for url, selectors in _DDG_ENDPOINTS:
        try:
            response = _http_get(url, params={"q": query})
        except httpx.HTTPError as exc:
            last_error = exc
            continue

        results = _ddg_parse(response.text, selectors, limit)
        if results:
            return results
        # Sonuc kabugu hic yoksa istek engellenmis demektir; bos bir sonuc
        # sayfasi en azindan arama formunu icerir.
        if "anomaly" in response.text or "challenge" in response.text:
            blocked = True

    if last_error is not None and not blocked:
        raise ToolError(f"Arama basarisiz: {last_error}") from last_error
    if blocked:
        raise SearchBlocked(
            "DuckDuckGo istegi reddetti (oran siniri veya bot tespiti). "
            "Kesintisiz arama icin deerx.toml icinde search_provider = \"brave\" "
            "ya da \"tavily\" ve search_api_key tanimlayin; Ayarlar bolumunden "
            "de girebilirsiniz."
        )
    return []


def _search_searxng(query: str, limit: int, base_url: str) -> tuple[list[dict], list[str]]:
    """Kendi SearXNG orneginde arar.

    `(sonuclar, dusen_motorlar)` doner. Ikinci deger onemli: SearXNG hangi
    motorun neden cevap vermedigini soyluyor (CAPTCHA, hiz siniri) ve bunu
    yutmak, kapsamin sessizce daralmasi demek olurdu.
    """
    import httpx

    try:
        response = _http_get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
        )
    except httpx.HTTPStatusError as exc:
        # 403 neredeyse her zaman tek bir seydir: ornekte JSON kapali.
        # Genel bir "HTTP 403" mesaji operatore ne yapacagini soylemez.
        if exc.response.status_code == 403:
            raise ToolError(t("web.searxng_no_json", url=base_url)) from exc
        raise
    data = response.json()

    hits = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:300],
        }
        for item in (data.get("results") or [])
        if item.get("url")
    ][:limit]

    dusenler = [
        f"{ad}: {sebep}"
        for ad, sebep in (data.get("unresponsive_engines") or [])
    ]
    return hits, dusenler

def _search_brave(query: str, limit: int, api_key: str) -> list[dict[str, str]]:
    import httpx

    try:
        response = _http_get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Brave arama basarisiz: {exc}") from exc

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("description") or "")[:300],
        }
        for item in response.json().get("web", {}).get("results", [])[:limit]
    ]


def _search_google(
    query: str, limit: int, api_key: str | None, cse_id: str | None
) -> list[dict[str, str]]:
    """Google Programmable Search JSON API.

    Tarayici yoluyla Google kullanilamiyor: olculdu, arama adresi gercek
    Chrome'da bile bot korumasi donuyor ("unusual traffic"). CAPTCHA asmak
    yapmadigimiz bir sey, bu yuzden Google yalnizca resmi ucla gelir.

    Iki sey ister ve ikisi de eksikse SESSIZCE bos donmek yerine ne
    eksik oldugunu soyler: anahtar tek basina yetmez, arama motoru
    kimligi (`cx`) olmadan uc 400 doner ve mesaji kullaniciya bir sey
    anlatmaz.
    """
    import httpx

    eksik = [
        ad
        for ad, deger in (("search_api_key", api_key), ("google_cse_id", cse_id))
        if not deger
    ]
    if eksik:
        raise ToolError(t("web.google_missing", fields=", ".join(eksik)))

    try:
        response = _http_get(
            "https://www.googleapis.com/customsearch/v1",
            params={"q": query, "key": api_key, "cx": cse_id,
                    # Uc tek istekte en fazla on sonuc verir.
                    "num": min(limit, 10)},
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Google arama basarisiz: {exc}") from exc

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": (item.get("snippet") or "")[:300],
        }
        for item in response.json().get("items", [])[:limit]
    ]


def _search_tavily(query: str, limit: int, api_key: str) -> list[dict[str, str]]:
    import httpx

    try:
        with httpx.Client(timeout=25.0) as client:
            response = client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": limit},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(f"Tavily arama basarisiz: {exc}") from exc

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:300],
        }
        for item in response.json().get("results", [])[:limit]
    ]


class FetchUrl(Tool):
    name = "fetch_url"
    description = """
    Bir web sayfasini indirir, metnini cikarir ve bilgi tabanina indeksler.
    Sunucu tarafi `web_fetch` aracindan farki: icerik KALICI olur, sonraki
    fazlarda `search_knowledge` ile tekrar bulunabilir.

    Referans dokumantasyon, API sayfalari ve teknik kilavuzlar icin kullanin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http/https adresi."},
            "index": {
                "type": "boolean",
                "description": "Bilgi tabanina eklensin mi (varsayilan true).",
            },
        },
        "required": ["url"],
    }

    def run(self, ctx: ToolContext, url: str, index: bool = True) -> ToolResult:
        if not ctx.settings.enable_web:
            raise ToolError("Web erisimi kapali (deerx.toml -> enable_web = false).")

        import httpx

        _guard_url(url)
        ctx.events.emit("tool", "web", f"cekiliyor: {url}")
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=30.0,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                raw = response.text[:_MAX_BYTES]
        except httpx.HTTPStatusError as exc:
            raise ToolError(
                f"HTTP {exc.response.status_code}: {url}" + _tekrar_notu(ctx, url)
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                t("web.fetch_failed", error=exc) + _tekrar_notu(ctx, url)
            ) from exc

        content_type = response.headers.get("content-type", "")
        if "html" in content_type or raw.lstrip().startswith("<"):
            from ..rag.loaders import load_html_text

            doc = load_html_text(raw, source=url, title=url)
            text = doc.text
        else:
            text = raw

        if index and ctx.kb is not None:
            result = ctx.kb.ingest_text(text, source=url, title=url, kind="web")
            note = f"\n\n[bilgi tabanina eklendi: {result.chunks} parca]"
        else:
            note = ""

        preview = text[:8000] + ("\n…[kesildi]" if len(text) > 8000 else "")
        return ToolResult(content=f"# {url}\n\n{preview}{note}")


class BrowsePage(Tool):
    name = "browse_page"
    description = """
    Sayfayi basliksiz (headless) tarayicida acar, JavaScript'in calismasini
    bekler ve olusan metni doner. Yalnizca `fetch_url` bos/eksik icerik
    dondurdugunde kullanin — daha yavas ve daha pahalidir.

    Gereksinim: `uv add playwright && playwright install chromium`
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "wait_selector": {
                "type": "string",
                "description": "Beklenecek CSS secici (opsiyonel).",
            },
            "index": {"type": "boolean", "description": "Bilgi tabanina eklensin mi."},
        },
        "required": ["url"],
    }

    def run(
        self,
        ctx: ToolContext,
        url: str,
        wait_selector: str | None = None,
        index: bool = True,
    ) -> ToolResult:
        if not ctx.settings.enable_web:
            raise ToolError(t("web.disabled"))
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ToolError(t("web.no_playwright")) from exc

        _guard_url(url)
        ctx.events.emit("tool", "web", f"tarayici: {url}")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(user_agent=_USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=45_000)
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=15_000)
                html = page.content()
                browser.close()
        except Exception as exc:  # noqa: BLE001 - playwright kendi hata tiplerini kullanir
            raise ToolError(t("browser.page_failed", error=exc)) from exc

        from ..rag.loaders import load_html_text

        doc = load_html_text(html, source=url, title=url)
        note = ""
        if index and ctx.kb is not None:
            result = ctx.kb.ingest_text(doc.text, source=url, title=url, kind="web")
            note = f"\n\n[bilgi tabanina eklendi: {result.chunks} parca]"

        preview = doc.text[:8000] + ("\n…[kesildi]" if len(doc.text) > 8000 else "")
        return ToolResult(content=f"# {url}\n\n{preview}{note}")


# `web_search` ve `browse_page` artik `tools/browser.py` icinde, gercek
# Chrome uzerinde. Buradaki HTTP surumleri yardimci olarak kaliyor:
# anahtarli saglayicilar (brave/tavily) tarayici gerektirmiyor ve ayarlar
# ekranindaki arama testi bunlari kullaniyor. `fetch_url` ise tarayici
# calismadiginda da ise yarayan ucuz okuyucu.
WEB_TOOLS: list[Tool] = [FetchUrl()]
