"""Is akisi danismani: konusma, kapsam ve degisiklik kaydi.

Danisman bir faz degil, bir sohbet: kullanici bir is akisi hakkinda sorar,
danisman cevaplar ve istenirse durumu DEGISTIRIR. Buradaki testler uc seyi
kilitliyor -- konusmanin kalici olmasi, kapsamin modele birakilmamasi ve
yapilan degisikligin kullaniciya gorunmesi.

Hicbiri gercek model cagirmaz; senaryolu sahte istemci kullanilir.
"""

from __future__ import annotations

import pytest

from deerx.errors import DeerXError
from deerx.llm.base import LLMResult, ToolCall
from deerx.llm.pricing import Usage
from deerx.pipeline.models import Question, Status, Task
from deerx.tools import TOOLSETS, build_registry


def yanit(text: str = "", calls: list[ToolCall] | None = None) -> LLMResult:
    return LLMResult(
        text=text,
        thinking="",
        tool_calls=calls or [],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5, calls=1),
        cost=0.0,
        model="sahte",
        raw=[{"type": "text", "text": text}],
    )


class SahteIstemci:
    """Senaryoyu sirayla donen, gonderileni kaydeden istemci."""

    def __init__(self, senaryo: list[LLMResult]) -> None:
        self.senaryo = list(senaryo)
        self.calls: list[dict] = []
        self.total_cost = 0.0

    def complete(self, **kw):
        self.calls.append(kw)
        return self.senaryo.pop(0) if self.senaryo else yanit(text="(bitti)")

    def append_assistant(self, m, r):
        m.append({"role": "assistant", "content": r.raw})

    def append_tool_results(self, m, outs):
        m.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": o.call_id,
                 "content": o.content, "is_error": o.is_error}
                for o in outs
            ],
        })

    def append_note(self, m, text):
        m.append({"role": "user", "content": text})

    def trim_history(self, m) -> int:
        return 0

    def usage_summary(self) -> str:
        return "sahte"


@pytest.fixture
def sohbet(orch_factory):
    """Bir is akisi acilmis, sahte istemcili orkestrator."""
    orch = orch_factory()
    workflow = orch.state.workflow_for_goal("B2B saha servis", brief="ilk talimat")

    def kur(senaryo):
        orch._client = SahteIstemci(senaryo)  # noqa: SLF001 - testin kurdugu istemci
        return orch._client

    yield orch, workflow, kur
    orch.close()


class TestKonusmaKalici:
    def test_both_sides_are_stored(self, sohbet):
        orch, workflow, kur = sohbet
        kur([yanit(text="Kirk iki gorev var.")])

        cevap = orch.chat(workflow["id"], "Kac gorev var?")

        assert cevap.ok and cevap.text == "Kirk iki gorev var."
        gecmis = orch.state.chat_history(workflow["id"])
        assert [(m["role"], m["content"]) for m in gecmis] == [
            ("user", "Kac gorev var?"),
            ("assistant", "Kirk iki gorev var."),
        ]

    def test_the_previous_turn_reaches_the_model(self, sohbet):
        """Ikinci soru, birincinin cevabini bilmeden cevaplanamaz.

        Gecmis modele BAGLAM METNI olarak gidiyor, konusma gecmisi nesnesi
        olarak degil: o nesnenin bicimi saglayiciya ozgu ve tek sahibi
        `LLMClient`. Bu test, katlamanin gercekten yapildigini sabitler.
        """
        orch, workflow, kur = sohbet
        kur([yanit(text="Kirk iki.")])
        orch.chat(workflow["id"], "Kac gorev var?")

        istemci = kur([yanit(text="Evet, oyle demistim.")])
        orch.chat(workflow["id"], "Emin misin?")

        gonderilen = istemci.calls[0]["messages"][0]["content"]
        assert "Kac gorev var?" in gonderilen
        assert "Kirk iki." in gonderilen

    def test_history_can_be_cleared(self, sohbet):
        orch, workflow, kur = sohbet
        kur([yanit(text="tamam")])
        orch.chat(workflow["id"], "merhaba")

        assert orch.state.clear_chat(workflow["id"]) == 2
        assert orch.state.chat_history(workflow["id"]) == []

    def test_an_empty_message_is_refused(self, sohbet):
        orch, workflow, _ = sohbet
        with pytest.raises(DeerXError):
            orch.chat(workflow["id"], "   ")

    def test_an_unknown_workflow_is_refused(self, sohbet):
        orch, _, _ = sohbet
        with pytest.raises(DeerXError):
            orch.chat("boyle-bir-kimlik-yok", "merhaba")


class TestKapsamModeleBirakilmaz:
    """Hangi is akisinin degistirilecegini model SECEMEZ.

    Kimligi arac argumani yapmak, modele "hangisini degistireyim?" diye
    sormaktir: kullanici #3'u konusurken model #7'yi degistirebilir ve
    bunun icin kotu niyet gerekmez, yanlis bir sayi uretmesi yeter.
    Kapsam `ToolContext.workflow_id` uzerinden cagirandan gelir.
    """

    def test_the_tools_take_no_workflow_argument(self):
        defter = build_registry()
        for ad in ("read_workflow", "update_workflow", "resolve_question"):
            sema = defter.get(ad).schema.get("properties", {})
            assert "workflow" not in sema and "workflow_id" not in sema, ad

    def test_they_refuse_without_a_workflow_in_context(self, ctx):
        defter = build_registry()
        ctx.workflow_id = ""
        sonuc = defter.execute("read_workflow", {}, ctx)
        assert sonuc.is_error

    def test_the_context_is_restored_after_the_turn(self, sohbet):
        """Sohbet, kosularin kullandigi ayni arac baglamini oduncu alir.
        Geri birakmazsa sonraki faz kendini bir is akisinin icinde bulur.
        """
        orch, workflow, kur = sohbet
        kur([yanit(text="tamam")])
        assert orch.ctx.workflow_id == ""
        orch.chat(workflow["id"], "merhaba")
        assert orch.ctx.workflow_id == ""


class TestDegisiklikGorunur:
    """Yapilan degisiklik cevabin yaninda durmali.

    Kullanici sohbeti geriye donuk okudugunda neyin degistigini metnin
    icinde aramak zorunda kalmamali.
    """

    def test_a_mutating_call_is_recorded(self, sohbet):
        orch, workflow, kur = sohbet
        kur([
            yanit(calls=[ToolCall(id="c1", name="update_workflow",
                                  arguments={"title": "Saha servis v2"})]),
            yanit(text="Basligi degistirdim."),
        ])

        cevap = orch.chat(workflow["id"], "Basligi Saha servis v2 yap")

        assert any("update_workflow" in d for d in cevap.changes), cevap.changes
        assert orch.state.get_workflow(workflow["id"])["title"] == "Saha servis v2"

    def test_the_recorder_survives_the_agents_own_subset(self, sohbet):
        """OLCULDU: `build_agent` verilen kayit defterini kendi icinde
        `subset(TOOLSETS[rol])` ile daraltiyor. Taban sinifin `subset`i
        DUZ bir defter donunce sarmalayici sessizce dusuyordu -- degisiklik
        gercekten yapiliyor ama sohbet "hicbir sey degismedi" diyordu.
        """
        orch, workflow, kur = sohbet
        kur([
            yanit(calls=[ToolCall(id="c1", name="update_workflow",
                                  arguments={"brief": "mobil once"})]),
            yanit(text="tamam"),
        ])
        cevap = orch.chat(workflow["id"], "talimati degistir")
        assert cevap.changes, "sarmalayici dusmus; degisiklik kaydedilmedi"

    def test_a_read_only_turn_records_nothing(self, sohbet):
        orch, workflow, kur = sohbet
        kur([
            yanit(calls=[ToolCall(id="c1", name="read_workflow", arguments={})]),
            yanit(text="Kirk iki gorev."),
        ])
        cevap = orch.chat(workflow["id"], "Kac gorev var?")
        assert cevap.changes == []

    def test_a_failed_call_is_not_recorded_as_a_change(self, sohbet):
        """Dusen bir arac cagrisi degisiklik degildir; oyle gostermek
        kullaniciya olmayan bir sey oldugunu soylerdi."""
        orch, workflow, kur = sohbet
        kur([
            yanit(calls=[ToolCall(id="c1", name="resolve_question",
                                  arguments={"key": "Q-999", "answer": "x"})]),
            yanit(text="Boyle bir soru yok."),
        ])
        cevap = orch.chat(workflow["id"], "Q-999'u cevapla")
        assert cevap.changes == []


class TestDegisiklikGercektenYapilir:
    def test_the_goal_change_reaches_the_project_meta(self, sohbet):
        """Fazlar "bu faz hangi hedef icin tamamlandi?" diye proje
        hedefine bakar. Is akisinin hedefi ile projeninki ayrisirsa o
        karar yanlis tarafa duser."""
        orch, workflow, _ = sohbet
        orch.state.update_workflow(workflow["id"], goal="yeni hedef")
        assert orch.state.get_workflow(workflow["id"])["goal"] == "yeni hedef"
        assert orch.state.get_meta("goal") == "yeni hedef"

    def test_answering_a_question_reaches_the_knowledge_base(self, sohbet, ctx):
        """Cevap yalnizca hafizada kalsa uzun bir kosuda gecmis kirpilinca
        kaybolurdu; bilgi tabaninda her zaman aranabilir."""
        from deerx.pipeline.answers import ANSWERS_SOURCE

        orch, workflow, _ = sohbet
        orch.state.add_question(
            Question(key="Q-001", question="SLA suresi?", blocking=False)
        )
        ctx.state = orch.state
        ctx.kb = orch.kb
        ctx.workflow_id = workflow["id"]

        sonuc = build_registry().execute(
            "resolve_question", {"key": "Q-001", "answer": "8 saat"}, ctx
        )

        assert not sonuc.is_error, sonuc.content
        assert orch.state.get_question("Q-001").status == "answered"
        assert any(
            d["source"] == ANSWERS_SOURCE for d in orch.kb.list_documents()
        ), "cevap bilgi tabanina yazilmadi"

    def test_a_task_status_can_be_changed_from_the_chat(self, sohbet):
        orch, workflow, kur = sohbet
        orch.state.add_task(Task(key="T-001", title="Saglik ucu"))
        kur([
            yanit(calls=[ToolCall(id="c1", name="update_task",
                                  arguments={"key": "T-001", "status": "done",
                                             "result": "elle dogrulandi"})]),
            yanit(text="T-001 tamam olarak isaretlendi."),
        ])

        cevap = orch.chat(workflow["id"], "T-001 bitti say")

        assert orch.state.get_task("T-001").status == Status.DONE
        assert any("update_task" in d for d in cevap.changes)


class TestDanismanKumesi:
    """Arac kumesi bilerek dar: bu bir sohbet, bir faz degil."""

    @pytest.mark.parametrize("yasak", ["run_command", "write_file", "edit_file",
                                       "start_service", "preview_open",
                                       "browser_click"])
    def test_it_cannot_touch_the_machine(self, yasak):
        assert yasak not in TOOLSETS["danisman"], (
            f"{yasak} sohbette olmamali: kullanicinin bir cumlesiyle komut "
            "calistirilmasi ya da dosya yazilmasi beklenmiyor"
        )

    def test_it_can_read_and_record(self):
        for gerekli in ("read_workflow", "read_project_state", "search_knowledge",
                        "update_workflow", "resolve_question", "record_gaps"):
            assert gerekli in TOOLSETS["danisman"], gerekli

    def test_the_toolset_resolves(self):
        assert len(build_registry().subset(TOOLSETS["danisman"]).names()) == len(
            TOOLSETS["danisman"]
        )


class TestIsAkisiBaglami:
    def test_the_context_separates_workflow_from_project(self, sohbet):
        """Gereksinim ve bosluklar is akisina degil PROJEYE ait; tablolarinda
        is akisi kimligi yok. Baglam bunu acikca soylemeli, yoksa danisman
        "bu is akisinin gereksinimi" diye var olmayan bir sey uydurur."""
        orch, workflow, _ = sohbet
        metin = orch.state.workflow_context(workflow["id"])
        assert "Is akisi #" in metin
        assert "PROJEYE ait" in metin or "projeye ait" in metin.lower()

    def test_an_unknown_workflow_gives_empty_context(self, sohbet):
        orch, _, _ = sohbet
        assert orch.state.workflow_context("yok") == ""
