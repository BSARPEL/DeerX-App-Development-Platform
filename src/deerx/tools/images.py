"""Gorsel arama ve indirme: sunum ve mockup'lar icin.

Ajan bugune kadar web'de METIN arayabiliyordu ama gorsel bulamiyor, bulsa da
indiremiyordu: `fetch_url` `response.text` kullanir, ikili veriyi tasimaz.
Sunum ve mockup uretirken elinde yalnizca CSS ile cizilmis kutular kaliyordu.

Lisans, bu araclarin en onemli tasarim kisiti
---------------------------------------------
Rastgele bir web fotografini kullanicinin teslimatina koymak telif riski
uretir. SearXNG gorsel aramasi lisansi belli motorlari da doner -- olculdu,
tek bir sorguda: openverse (CC), unsplash, pexels, wikicommons, artic
(kamu mali) ve yaninda bing/google/pinterest gibi lisansi belirsiz olanlar.

Bu yuzden sonuclar SIRALANIR: lisansi bilinen kaynaklar basa gelir ve her
sonuc hangi motordan geldigini soyler. Ajan neyi kullandigini bilir,
kullanici da atif verebilir. Belirsiz kaynaklar gizlenmez -- bazen tek
sonuc odur -- ama "serbest" olanlarla ayni sirada gosterilmez.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult

# Lisansi bilinen ve serbest kullanima acik kaynaklar. Sira onem sirasidir.
SERBEST_MOTORLAR = (
    "openverse",        # Creative Commons arama motoru
    "wikicommons.images",
    # NOT: Art Institute of Chicago (`artic`) BILEREK yok. Eserleri kamu
    # mali ama IIIF ucu otomatik indirmeyi reddediyor -- olculdu, bes ayri
    # sorguda bes kez HTTP 403, kimlikli istekte bile. Listede kalsaydi
    # siralamada uste cikip ajani her seferinde garanti bir cikmaza
    # sokardi: gorur, secer, indiremez, turunu harcar.
    "unsplash",
    "pexels",
)

# Indirilen gorselin ust siniri. Bir sunum icin 15 MB fazlasiyla yeter;
# sinirsiz birakmak calisma alanini sisirir.
MAX_BAYT = 15 * 1024 * 1024

# Gercekten gorsel mi? Icerik turune guvenmek yetmez: sunucu yanlis
# bildirebilir ya da hata sayfasi donebilir.
_IMZALAR = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"RIFF": ".webp",       # RIFF....WEBP
    b"<svg": ".svg",
    b"<?xml": ".svg",
}


def _uzanti(veri: bytes) -> str | None:
    for imza, uzanti in _IMZALAR.items():
        if veri.startswith(imza):
            if imza == b"RIFF" and veri[8:12] != b"WEBP":
                continue
            return uzanti
    return None


class FindImages(Tool):
    name = "find_images"
    description = """
    Web'de gorsel arar; baslik, gorsel adresi, boyut ve KAYNAK doner.

    Sonuclar lisansi bilinen kaynaklar basta olacak sekilde siralanir
    (Openverse, Wikimedia Commons, Unsplash, Pexels, Art Institute).
    Teslimata girecek bir gorsel seciyorsaniz o kaynaklardan birini secin
    ve sunumda atif verin; digerlerinin lisansi belirsizdir.

    Bulduktan sonra `download_image` ile calisma alanina indirin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Ne aranacak (Ingilizce daha iyi sonuc verir)."},
            "max_results": {"type": "integer", "description": "Varsayilan 12, azami 30."},
            "free_only": {
                "type": "boolean",
                "description": "Yalnizca lisansi bilinen kaynaklar (varsayilan evet).",
            },
        },
        "required": ["query"],
    }

    def run(
        self,
        ctx: ToolContext,
        query: str,
        max_results: int = 12,
        free_only: bool = True,
    ) -> ToolResult:
        if not ctx.settings.enable_web:
            raise ToolError(t("browser.web_off"))
        query = (query or "").strip()
        if not query:
            raise ToolError(t("web.empty_query"))
        limit = max(1, min(int(max_results or 12), 30))

        taban = (ctx.settings.searxng_url or "").rstrip("/")
        if not taban:
            raise ToolError(t("images.no_searxng"))

        adres = f"{taban}/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "categories": "images"}
        )
        ctx.events.emit("tool", "web", t("images.searching", query=query[:80]))
        try:
            with urllib.request.urlopen(adres, timeout=30) as cevap:
                veri = json.load(cevap)
        except Exception as exc:  # noqa: BLE001 - uc cesitli hata verebilir
            raise ToolError(t("images.search_failed", error=exc)) from exc

        sonuclar = [r for r in veri.get("results", []) if r.get("img_src")]
        if not sonuclar:
            return ToolResult(content=t("images.no_results", query=query))

        def serbest_mi(r: dict[str, Any]) -> bool:
            return any(m in SERBEST_MOTORLAR for m in (r.get("engines") or []))

        if free_only:
            secilen = [r for r in sonuclar if serbest_mi(r)]
            if not secilen:
                # Sessizce belirsiz kaynaklara dusmek, ajanin lisansli
                # sandigi bir gorseli kullanmasina yol acar.
                return ToolResult(content=t("images.no_free_results", query=query))
            sonuclar = secilen
        else:
            sonuclar.sort(key=lambda r: not serbest_mi(r))

        satirlar = []
        for i, r in enumerate(sonuclar[:limit], start=1):
            motorlar = ", ".join(r.get("engines") or [])
            etiket = t("images.free") if serbest_mi(r) else t("images.unknown_licence")
            satirlar.append(
                f"{i}. {(r.get('title') or r.get('content') or '')[:70]}\n"
                f"   {r['img_src']}\n"
                f"   {r.get('resolution') or '?'} · {motorlar} · {etiket}"
            )
        return ToolResult(
            content="\n".join(satirlar),
            data={"count": len(satirlar)},
        )


class DownloadImage(Tool):
    name = "download_image"
    description = """
    Bir gorseli calisma alanina indirir ve cikti olarak kaydeder.

    Sunum ve mockup HTML dosyalari indirilen gorseli ayni klasorden
    goreceli adresle kullanabilir: `<img src="manzara.jpg">`.

    Kaynak adresi cikti ozetine yazilir; atif vermek icin gereklidir.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Gorselin dogrudan adresi."},
            "name": {"type": "string", "description": "Dosya adi, or. kapak.jpg"},
        },
        "required": ["url", "name"],
    }

    def run(self, ctx: ToolContext, url: str, name: str) -> ToolResult:
        if not ctx.settings.enable_web:
            raise ToolError(t("browser.web_off"))

        import httpx

        from .web import _USER_AGENT, _guard_url

        _guard_url(url)
        guvenli = name.replace("/", "_").replace("\\", "_").replace("..", "_").strip()
        if not guvenli:
            raise ToolError(t("images.bad_name"))

        ctx.approve(
            t("images.approve", url=url[:120]),
            t("images.approve_detail", name=guvenli),
            signature=f"image:{url}",
        )
        ctx.events.emit("tool", "web", t("images.downloading", url=url[:80]))
        try:
            # Kimliksiz istek engelleniyor: olculdu, artic.edu 403 dondu.
            # `fetch_url` ayni basligi gonderiyor; ikisi ayni davranmali.
            with httpx.Client(
                follow_redirects=True, timeout=45.0,
                headers={"User-Agent": _USER_AGENT},
            ) as istemci:
                cevap = istemci.get(url)
                cevap.raise_for_status()
                veri = cevap.content
        except httpx.HTTPError as exc:
            raise ToolError(t("images.download_failed", error=exc)) from exc

        if len(veri) > MAX_BAYT:
            raise ToolError(t("images.too_big", limit=MAX_BAYT // (1024 * 1024)))

        # Icerik turune GUVENMIYORUZ: sunucu yanlis bildirebilir ya da hata
        # sayfasi donebilir. Baytlarin kendisine bakiyoruz.
        uzanti = _uzanti(veri[:16])
        if uzanti is None:
            raise ToolError(
                t("images.not_an_image",
                  content_type=cevap.headers.get("content-type", "?"))
            )
        if "." not in guvenli:
            guvenli += uzanti

        hedef = ctx.settings.artifacts_dir / guvenli
        hedef.parent.mkdir(parents=True, exist_ok=True)
        hedef.write_bytes(veri)

        if ctx.state is not None:
            from ..pipeline.models import Artifact

            ctx.state.add_artifact(
                Artifact(
                    name=guvenli,
                    kind="image",
                    path=str(hedef),
                    # Kaynak ozete yazilir: atif vermek icin gerekli ve
                    # sonradan "bu nereden geldi" sorusunun tek cevabi.
                    summary=t("images.artifact_summary", url=url, kb=len(veri) // 1024),
                )
            )
        return ToolResult(
            content=t("images.saved", name=guvenli, kb=len(veri) // 1024, url=url)
        )


IMAGE_TOOLS: list[Tool] = [FindImages(), DownloadImage()]
