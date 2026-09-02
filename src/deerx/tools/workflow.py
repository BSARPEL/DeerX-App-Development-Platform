"""Is akisi danismaninin araclari.

Bu araclar TEK bir is akisina baglidir ve hangi is akisi oldugunu
`ToolContext.workflow_id` soyler -- arac argumani DEGIL.

Ayrim guvenlik acisindan onemli: kimligi arguman yapmak, modele "hangi
is akisini degistireyim?" sorusunu sormak demektir. Kullanici #3
hakkinda konusurken model #7'yi degistirebilir; ustelik bunu kotu niyet
gerektirmeden, yalnizca yanlis bir sayi ureterek yapar. Kapsam
cagirandan gelir, modelden degil.
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult


def _workflow(ctx: ToolContext) -> tuple[Any, str]:
    """Baglamdaki is akisi ve kimligi; yoksa anlasilir bir hata."""
    state = ctx.require_state()
    if not ctx.workflow_id:
        raise ToolError(t("chat.no_workflow_context"))
    workflow = state.get_workflow(ctx.workflow_id)
    if workflow is None:
        raise ToolError(t("chat.workflow_gone", id=ctx.workflow_id))
    return state, ctx.workflow_id


class ReadWorkflow(Tool):
    name = "read_workflow"
    description = """
    Konusulan is akisinin durumunu okur: hedefi, kullanicinin talimati,
    adimlari (kosular ve fazlari), urettigi ciktilar ve planlari.

    Bir sey iddia etmeden ONCE burayi okuyun. Proje kayitlari
    (gereksinim, bosluk, karar, soru) bu is akisina degil PROJEYE aittir
    ve ciktida ayri bir baslik altinda gosterilir.
    """
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext) -> ToolResult:
        state, workflow_id = _workflow(ctx)
        return ToolResult(content=state.workflow_context(workflow_id))


class UpdateWorkflow(Tool):
    name = "update_workflow"
    description = """
    Konusulan is akisinin basligini, hedefini veya kullanici talimatini
    degistirir. Yalnizca verilen alanlar degisir.

    HEDEFI degistirmek agir bir istir: fazlar "bu faz hangi hedef icin
    tamamlandi?" diye bakarak yeniden kosulup kosulmayacagina karar
    verir, dolayisiyla hedefi degistirmek tamamlanmis fazlari yeniden
    kosulabilir yapar. Kullanici acikca istemediyse dokunmayin.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Yeni baslik."},
            "goal": {"type": "string", "description": "Yeni hedef."},
            "brief": {
                "type": "string",
                "description": "Ajanlara verilecek yeni talimat.",
            },
        },
    }

    def run(
        self,
        ctx: ToolContext,
        title: str | None = None,
        goal: str | None = None,
        brief: str | None = None,
    ) -> ToolResult:
        state, workflow_id = _workflow(ctx)
        if title is None and goal is None and brief is None:
            raise ToolError(t("chat.nothing_to_update"))

        degisen = [
            ad
            for ad, deger in (("title", title), ("goal", goal), ("brief", brief))
            if deger is not None
        ]
        ctx.approve(
            t("chat.approve_workflow", fields=", ".join(degisen)),
            t("chat.approve_workflow_detail", goal=goal or "-", brief=(brief or "-")[:200]),
            signature=f"workflow:{workflow_id}:{','.join(degisen)}",
        )
        guncel = state.update_workflow(
            workflow_id, title=title, goal=goal, brief=brief
        )
        assert guncel is not None
        ctx.events.emit(
            "tool", "workflow", t("chat.workflow_updated", fields=", ".join(degisen))
        )
        return ToolResult(
            content=t("chat.workflow_updated", fields=", ".join(degisen)),
            data={"workflow": guncel, "changed": degisen},
        )


class ResolveQuestion(Tool):
    name = "resolve_question"
    description = """
    Acik bir soruyu kullanicinin verdigi cevapla kapatir; cevap yoksa
    belirtilen varsayimla atlar.

    YALNIZCA kullanici cevabi bu konusmada verdiyse kullanin. Kendi
    tahmininizi cevap diye yazmayin -- bu sorularin var olma sebebi,
    cevabin yalnizca kullanicida olmasi. Emin degilseniz sorun.

    Cevap proje hafizasina VE bilgi tabanina yazilir; boylece sonraki
    fazlar gecmis kirpilsa da onu bulabilir.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Soru anahtari, or. Q-001."},
            "answer": {
                "type": "string",
                "description": "Kullanicinin verdigi cevap.",
            },
            "assumption": {
                "type": "string",
                "description": (
                    "Cevap yerine ilerlenecek varsayim. `answer` verilmisse "
                    "yok sayilir."
                ),
            },
        },
        "required": ["key"],
    }

    def run(
        self,
        ctx: ToolContext,
        key: str,
        answer: str = "",
        assumption: str = "",
    ) -> ToolResult:
        state, _ = _workflow(ctx)
        key = key.strip().upper()
        if not answer.strip() and not assumption.strip():
            raise ToolError(t("chat.answer_or_assumption"))

        ctx.approve(
            t("chat.approve_question", key=key),
            (answer or assumption)[:300],
            signature=f"question:{key}",
        )
        soru = (
            state.answer_question(key, answer.strip())
            if answer.strip()
            else state.skip_question(key, assumption.strip())
        )
        if soru is None:
            raise ToolError(t("chat.no_such_question", key=key))

        # Cevap bilgi tabanina da yazilir; mantik orkestratorle ORTAK.
        if ctx.kb is not None:
            from ..pipeline.answers import reindex_answers

            reindex_answers(state, ctx.kb)

        durum = t("chat.answered" if answer.strip() else "chat.skipped", key=key)
        ctx.events.emit("tool", "workflow", durum)
        return ToolResult(content=durum, data={"question": soru.key})


WORKFLOW_TOOLS: list[Tool] = [
    ReadWorkflow(),
    UpdateWorkflow(),
    ResolveQuestion(),
]
