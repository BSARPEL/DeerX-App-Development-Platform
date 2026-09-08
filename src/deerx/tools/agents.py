"""Alt ajan araclari: bir isi bolup uzmanlara dagitmak.

Bir ajan bazen kendi rolunun disina tasan bir isle karsilasir: uygulayici
bir kutuphanenin guncel surumunu bilmiyordur, planlayici mevcut kodun ne
yaptigini okumak ister. Bugune kadar tek yol ya isi kendi dar arac
kumesiyle yapmaya calismak ya da fazin bitmesini beklemekti.

TASARIM KARARLARI

Alt ajan EBEVEYNIN IS PARCACIGINDA, SIRAYLA kosar. Paralellik ayri bir
istir ve bu araclarin onkosulu degildir: es zamanli kosarlarsa tarayici
oturumu (Playwright nesneleri olusturan is parcacigina bagli), kabin
portlari, servis ad alani ve onay kuyrugu ayni anda yedi yerde bolunmek
zorunda kalirdi. Sirayla kosmak bunlarin hicbirini gerektirmiyor.

Ozyineleme siniri BIR: alt ajan alt ajan kosturamaz. Sinirsiz derinlik,
tek bir istegin butun butceyi harcayacagi bir agac uretir ve o agacin
nerede durdugunu kimse goremez.

Kapsam ve onaylar TURETILIR, devralinmaz. Ozellikle onay kumesi: ebeveyn
"su tehlikeli komutu calistir" onayini almissa, alt ajanin ayni onayi
otomatik kullanmasi bir yetki sizintisidir -- kullanici o komuta ONAY
verdi, o role degil.
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult, json_block

# Alt ajan olarak cagrilabilecek roller. Boru hattini yuruten roller
# (analyst, architect, planner ...) bilerek DISARIDA: onlar bir fazin
# sahibi ve kendi ciktilarini uretiyorlar. Alt ajan bir faz kosturmaz,
# bir soruyu cevaplar.
SUBAGENT_ROLES = ("researcher", "qa", "reviewer", "summarizer")

MAX_DEPTH = 1


class PlanSubagents(Tool):
    name = "plan_subagents"
    description = """
    Bir isi alt ajanlara bolmeyi PLANLAR; hicbirini calistirmaz.

    Once bunu kullanin: hangi parcanin kime verilecegini ve her birinden ne
    beklendigini yazmak, isi bolmenin gercekten gerekip gerekmedigini de
    gosterir. Tek bir parca cikiyorsa alt ajana gerek yoktur.

    Her parca icin bir rol, bir gorev ve BEKLENEN TESLIM yazin. Teslim
    beklentisi olmayan bir alt ajan, ne dondurdugu belirsiz bir metin doner.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "Alt ajanlara verilecek parcalar.",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": list(SUBAGENT_ROLES)},
                        "task": {"type": "string", "description": "Ne yapacagi."},
                        "deliverable": {
                            "type": "string",
                            "description": "Ne dondurmesi bekleniyor.",
                        },
                    },
                    "required": ["role", "task", "deliverable"],
                },
            },
        },
        "required": ["tasks"],
    }

    def run(self, ctx: ToolContext, tasks: list[dict[str, Any]]) -> ToolResult:
        if not tasks:
            raise ToolError(t("agent.plan_empty"))
        temiz = []
        for madde in tasks:
            rol = str(madde.get("role", "")).strip()
            if rol not in SUBAGENT_ROLES:
                raise ToolError(t("agent.bad_role", role=rol,
                                  roles=", ".join(SUBAGENT_ROLES)))
            gorev = str(madde.get("task", "")).strip()
            teslim = str(madde.get("deliverable", "")).strip()
            if not gorev or not teslim:
                raise ToolError(t("agent.plan_incomplete"))
            temiz.append({"role": rol, "task": gorev, "deliverable": teslim})

        ctx.events.emit("agent", "plan", t("agent.planned", n=len(temiz)))
        return ToolResult(
            content=t("agent.plan_ready", n=len(temiz)) + "\n" + json_block(temiz)
        )


class RunSubagent(Tool):
    name = "run_subagent"
    description = """
    Tek bir alt ajani calistirir ve DONDURDUGU METNI verir.

    Alt ajan sizin arac kumenizin bir ALT KUMESINI gorur ve kendi onaylarini
    kendisi ister: sizin aldiginiz onay ona gecmez.

    Sirayla kosar -- cagri, alt ajan bitene kadar doner. Uzun bir is
    veriyorsaniz bunu bilerek yapin.

    `deliverable` alanina ne beklediginizi yazin; alt ajan bunu gorur ve
    cevabini ona gore bicimlendirir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": list(SUBAGENT_ROLES)},
            "task": {"type": "string", "description": "Alt ajanin isi."},
            "deliverable": {
                "type": "string",
                "description": "Ne dondurmesi bekleniyor.",
            },
            "context": {
                "type": "string",
                "description": "Bilmesi gereken arka plan (istege bagli).",
            },
        },
        "required": ["role", "task", "deliverable"],
    }

    def run(
        self,
        ctx: ToolContext,
        role: str,
        task: str,
        deliverable: str,
        context: str = "",
    ) -> ToolResult:
        if ctx.spawn is None:
            # Arac kayit defterinde var ama calistiran yok: sohbet ya da
            # test baglami. Sessizce basarili donmek, modelin isini
            # yaptigini sanmasina yol acardi.
            raise ToolError(t("agent.no_spawner"))
        if role not in SUBAGENT_ROLES:
            raise ToolError(t("agent.bad_role", role=role,
                              roles=", ".join(SUBAGENT_ROLES)))
        if ctx.depth >= MAX_DEPTH:
            raise ToolError(t("agent.depth_limit", limit=MAX_DEPTH))
        if not task.strip() or not deliverable.strip():
            raise ToolError(t("agent.plan_incomplete"))

        gorev = f"{task.strip()}\n\n{t('agent.deliverable_line', text=deliverable.strip())}"
        sonuc = ctx.spawn(role, gorev, context)
        if sonuc.error:
            raise ToolError(t("agent.subagent_failed", role=role, error=sonuc.error))

        return ToolResult(
            content=t("agent.subagent_done", role=role, iterations=sonuc.iterations)
            + "\n\n" + (sonuc.text or t("agent.subagent_silent"))
        )


AGENT_TOOLS: list[Tool] = [PlanSubagents(), RunSubagent()]
