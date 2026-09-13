"""Gecmis araclari: kullanicinin ONCEKI projelerinden cikarim yapmak.

Danisman bugune kadar yalnizca ACIK projeyi goruyordu. Kullanicinin
"gecen seferki gibi yapalim" ya da "bunu daha once cozmustuk" demesi
karsisinda elinde hicbir sey yoktu: her proje ayri bir SQLite dosyasi ve
danismanin oralara bakan bir yolu yoktu.

Neden ARAC, neden hepsi bagama konmuyor
---------------------------------------
Gecmisin tamamini her mesaja koymak, asil sorunun uzerine yuz ekran eski
konusma yigmak olurdu -- hem pahali hem dikkat dagitici. Bunun yerine:
kisa bir ozet her zaman baglamda durur (hangi projeler, hangi kararlar),
DERINLIK modelin istegiyle bu aracla gelir.

Kapsam cagirandan gelir
-----------------------
`ctx.history` yoksa arac acikca REDDEDER. Sessizce bos donmek, modelin
"gecmiste hicbir sey yok" diye yanlis bir cikarim yapmasina yol acardi --
oysa dogru cevap "bu baglamda gecmise bakamiyorum".
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult

_TURLER = ("decision", "chat", "workflow", "artifact")


class SearchHistory(Tool):
    name = "search_history"
    description = """
    Kullanicinin DIGER projelerinde arama yapar: alinmis kararlar, gecmis
    is akislari, eski sohbetler ve uretilmis ciktilar.

    Ne zaman: kullanici "daha once", "gecen projede", "hep boyle yapariz"
    gibi bir sey dediginde; bir teknoloji ya da desen secmeden once ayni
    kararin daha once nasil alindigina bakmak icin; tekrar eden bir hatayi
    ikinci kez yapmamak icin.

    Bulduklarin BASKA projelerden gelir: bu projenin gercegi degil, gecmis
    bir baglamin kaydidir. Ona dayanarak oneri yap, ama "su projede boyle
    yapilmisti" diye KAYNAGINI soyle.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Aranacak sozcukler; ozel adlar (teknoloji, "
                               "proje, karar anahtari) en iyi sonucu verir.",
            },
            "type": {
                "type": "string",
                "enum": list(_TURLER),
                "description": "Yalnizca bu turu ara: decision (karar), "
                               "chat (sohbet), workflow (is akisi), "
                               "artifact (cikti).",
            },
            "limit": {"type": "integer", "description": "En fazla kac sonuc (varsayilan 12)."},
        },
        "required": ["query"],
    }

    def run(
        self, ctx: ToolContext, query: str, type: str = "", limit: int = 12  # noqa: A002
    ) -> ToolResult:
        gecmis = getattr(ctx, "history", None)
        if gecmis is None:
            raise ToolError(t("history.unavailable"))
        if type and type not in _TURLER:
            raise ToolError(
                t("history.bad_kind", kind=type, kinds=", ".join(_TURLER))
            )

        bulunan = gecmis.search(query, limit=max(1, min(50, int(limit or 12))), tur=type)
        ctx.events.emit("tool", "history", t("history.searching", query=query[:80]))
        if not bulunan:
            # Okunamayan proje varsa bunu SOYLE: "hicbir sey bulunamadi" ile
            # "bakamadigim yerler vardi" ayri seyler ve model ikincisinde
            # kesin konusmamali.
            okunamayan = gecmis.unreadable()
            return ToolResult(
                content=t(
                    "history.no_match_unreadable",
                    query=query,
                    projects=", ".join(okunamayan),
                ) if okunamayan else t("history.no_match", query=query)
            )

        satirlar = [t("history.found", n=len(bulunan), query=query), ""]
        for kayit in bulunan:
            etiket = t(f"history.kind_{kayit.tur}")
            bas = f"- **{etiket}** · {kayit.project}"
            if kayit.workflow:
                bas += f" · {kayit.workflow}"
            satirlar.append(bas)
            satirlar.append(f"  {kayit.to_line()}")
        return ToolResult(content="\n".join(satirlar))


class ListProjectHistory(Tool):
    name = "list_project_history"
    description = """
    Kullanicinin diger projelerini listeler: adi, hedefi, kac is akisi ve
    kac sohbet satiri oldugu.

    Once bunu cagirin, sonra ilginizi ceken projede `search_history` ile
    derine inin. "Hangi projeler var" sorusunun cevabi burada; "orada ne
    konusuldu" sorusunun cevabi aramada.
    """
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext) -> ToolResult:
        gecmis = getattr(ctx, "history", None)
        if gecmis is None:
            raise ToolError(t("history.unavailable"))

        projeler = [p for p in gecmis.projects() if p.status == "ok" and p.kayitlar]
        if not projeler:
            return ToolResult(content=t("history.no_projects"))

        projeler.sort(key=lambda p: p.last_at, reverse=True)
        satirlar = [t("history.projects", n=len(projeler)), ""]
        for p in projeler:
            bas = f"- **{p.name}**"
            if p.goal:
                bas += f" — {p.goal[:160]}"
            satirlar.append(bas)
            satirlar.append(
                f"  {t('history.counts', workflows=p.workflows, chats=p.chats)}"
            )
        okunamayan = gecmis.unreadable()
        if okunamayan:
            satirlar += ["", t("history.unreadable", projects=", ".join(okunamayan))]
        return ToolResult(content="\n".join(satirlar))


HISTORY_TOOLS: list[Tool] = [SearchHistory(), ListProjectHistory()]
