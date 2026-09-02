"""Cevaplanmis sorularin bilgi tabanina yazilmasi.

Neden ayri bir modul: bu is IKI yerden yapiliyor. Orkestrator kullanici
`deerx answer` dediginde cagiriyor, is akisi danismani da sohbette bir
soru cevaplandiginda. Ikisinin ayri ayri yazilmasi, bu kod tabaninin
daha once odedigi bir bedel -- goruntu gonderme iki istemciye ayri
yazilmis, biri unutulmus ve ozellik sessizce yok olmustu.

Cevap NEDEN bilgi tabanina da yaziliyor: yalnizca proje hafizasinda
kalsa ajanlar onu ancak devredilen ozette gorurdu. Uzun bir kosuda
gecmis kirpilir ve yalnizca gecmiste yasayan bir cevap sessizce yok
olur; bilgi tabanindaki `search_knowledge` ile her zaman bulunur.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..i18n import t

if TYPE_CHECKING:  # pragma: no cover
    from ..rag.knowledge import KnowledgeBase
    from .state import ProjectState

# Kullanici cevaplarinin bilgi tabanindaki kaynak kimligi.
ANSWERS_SOURCE = "deerx://cevaplar"


def reindex_answers(state: ProjectState, kb: KnowledgeBase) -> int:
    """Cevaplanmis/atlanmis sorulari tek bir bilgi tabani dokumaninda toplar.

    Returns:
        Dokumana giren soru sayisi. Sifirsa dokuman tumden dusurulur --
        aksi halde butun cevaplar geri alindiginda bos bir belge aramada
        gorunmeye devam ederdi.
    """
    resolved = [q for q in state.list_questions() if q.status != "open"]
    if not resolved:
        kb.forget(ANSWERS_SOURCE)
        return 0

    lines = ["# Kullanicinin verdigi cevaplar", ""]
    for question in resolved:
        lines.append(f"## {question.key}: {question.question}")
        if question.why:
            lines.append(f"_Neden onemli:_ {question.why}")
        if question.status == "answered":
            lines.append(f"**Cevap:** {question.answer}")
        else:
            lines.append(
                t(
                    "pipeline.skipped_assumption",
                    assumption=(question.suggestion or t("pipeline.own_assumption")),
                )
            )
        lines.append("")

    kb.ingest_text(
        "\n".join(lines),
        source=ANSWERS_SOURCE,
        title="Kullanici cevaplari",
        kind="doc",
    )
    return len(resolved)
