"""Ajan dongusu testleri.

Model cagrisi sahte bir istemciyle degistirilir; boylece dongunun kendisi
(arac gonderimi, tool_result bicimi, pause_turn, iterasyon siniri, hata
yayilimi) API anahtari olmadan dogrulanabilir.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from deerx.agents.base import Agent
from deerx.errors import LLMError
from deerx.llm import LLMResult, ToolCall
from deerx.llm.pricing import Usage
from deerx.logging import EventLog
from deerx.pipeline.models import Status, Task
from deerx.tools import build_registry


def make_result(
    *,
    text: str = "",
    calls: list[ToolCall] | None = None,
    stop_reason: str = "end_turn",
) -> LLMResult:
    return LLMResult(
        text=text,
        thinking="",
        tool_calls=calls or [],
        stop_reason=stop_reason,
        usage=Usage(input_tokens=10, output_tokens=5, calls=1),
        cost=0.001,
        model="claude-opus-5",
        raw=[{"type": "text", "text": text}],
    )


class FakeClient:
    """Onceden yazilmis yanitlari sirayla donen sahte Claude istemcisi."""

    def __init__(self, script: list[LLMResult] | list[Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.total_cost = 0.0

    def complete(self, **kwargs: Any) -> LLMResult:
        # Ajan `messages` listesini yerinde buyutur; referans saklamak her cagriyi
        # ayni son duruma isaret ettirirdi. Anlik goruntu al.
        snapshot = dict(kwargs)
        snapshot["messages"] = copy.deepcopy(kwargs.get("messages", []))
        self.calls.append(snapshot)
        if not self.script:
            return make_result(text="(senaryo bitti)")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        self.total_cost += item.cost
        return item

    @property
    def last_messages(self) -> list[dict[str, Any]]:
        return self.calls[-1]["messages"]

    # --- LLMClient sozlesmesinin gecmis bolumu (Anthropic bicimi) -------- #
    def append_assistant(self, messages, result):
        messages.append({"role": "assistant", "content": result.raw})

    def append_tool_results(self, messages, outcomes):
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": o.call_id,
                        "content": o.content,
                        "is_error": o.is_error,
                    }
                    for o in outcomes
                ],
            }
        )

    def append_note(self, messages, text):
        messages.append({"role": "user", "content": text})

    def trim_history(self, messages) -> int:
        from deerx.llm.anthropic_client import AnthropicClient

        return AnthropicClient.trim_history(messages)

    def usage_summary(self) -> str:
        return "sahte istemci"


@pytest.fixture
def agent_factory(ctx):
    def build(script, *, role="analyst", tool_names=None, max_iterations=10, server_tools=None):
        registry = build_registry()
        names = tool_names or ["read_project_state", "record_requirements", "search_knowledge"]
        return (
            Agent(
                role=role,
                system_prompt="TEST SISTEM PROMPTU",
                registry=registry.subset(names),
                context=ctx,
                client=FakeClient(script),
                events=EventLog(None, echo=False),
                server_tools=server_tools or [],
                max_iterations=max_iterations,
                stream=False,
            ),
        )[0]

    return build


class TestAgentLoop:
    def test_returns_text_on_end_turn(self, agent_factory):
        agent = agent_factory([make_result(text="Analiz tamamlandi.")])
        result = agent.run("gorev")
        assert result.ok
        assert result.text == "Analiz tamamlandi."
        assert result.iterations == 1
        assert result.tool_calls == 0
        assert result.stop_reason == "end_turn"

    def test_context_is_prepended_to_first_user_message(self, agent_factory):
        agent = agent_factory([make_result(text="ok")])
        agent.run("GOREV METNI", context="DEVRALINAN BAGLAM")
        first = agent.client.calls[0]["messages"][0]["content"]
        assert "DEVRALINAN BAGLAM" in first
        assert "GOREV METNI" in first
        # Degisken baglam sistem prompt'una girmemeli (onbellek prefix'i bozulur).
        assert "DEVRALINAN BAGLAM" not in agent.client.calls[0]["system"]

    def test_executes_tool_and_feeds_result_back(self, agent_factory, state):
        agent = agent_factory(
            [
                make_result(
                    calls=[
                        ToolCall(
                            id="tu_1",
                            name="record_requirements",
                            arguments={"items": [{"key": "REQ-001", "title": "Giris yap"}]},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                make_result(text="Kaydedildi."),
            ]
        )
        result = agent.run("gorev")
        assert result.ok and result.tool_calls == 1
        assert [r.key for r in state.list_requirements()] == ["REQ-001"]

        # Ikinci cagrida gecmis: user, assistant, user(tool_result)
        messages = agent.client.calls[1]["messages"]
        assert len(messages) == 3
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        block = messages[2]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_1"
        assert block["is_error"] is False

    def test_parallel_tool_results_share_one_user_message(self, agent_factory):
        agent = agent_factory(
            [
                make_result(
                    calls=[
                        ToolCall(id="a", name="read_project_state", arguments={}),
                        ToolCall(id="b", name="search_knowledge", arguments={"query": "test"}),
                    ],
                    stop_reason="tool_use",
                ),
                make_result(text="bitti"),
            ]
        )
        agent.run("gorev")
        messages = agent.client.calls[1]["messages"]
        # Sonuclari ayri mesajlara bolmek modeli paralel arac kullanmaktan cayirir.
        assert len(messages) == 3
        assert len(messages[2]["content"]) == 2
        assert {b["tool_use_id"] for b in messages[2]["content"]} == {"a", "b"}

    def test_tool_error_is_reported_and_loop_continues(self, agent_factory):
        agent = agent_factory(
            [
                make_result(
                    calls=[
                        ToolCall(
                            id="tu_1",
                            name="record_requirements",
                            arguments={"items": [{"key": "GECERSIZ", "title": "x"}]},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                make_result(text="Anahtari duzeltiyorum."),
            ]
        )
        result = agent.run("gorev")
        assert result.ok  # arac hatasi dongunun sonu degildir
        block = agent.client.calls[1]["messages"][2]["content"][0]
        assert block["is_error"] is True
        assert "Gecersiz anahtar" in block["content"]

    def test_unknown_tool_does_not_break_loop(self, agent_factory):
        agent = agent_factory(
            [
                make_result(
                    calls=[ToolCall(id="x", name="olmayan_arac", arguments={})],
                    stop_reason="tool_use",
                ),
                make_result(text="tamam"),
            ]
        )
        assert agent.run("gorev").ok

    def test_pause_turn_resends_without_new_user_message(self, agent_factory):
        agent = agent_factory(
            [
                make_result(text="kismi", stop_reason="pause_turn"),
                make_result(text="tamamlandi"),
            ]
        )
        result = agent.run("gorev")
        assert result.ok and result.text == "tamamlandi"
        # Duraklama sonrasi: user + assistant (yeni kullanici mesaji eklenmez)
        messages = agent.client.calls[1]["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]

    def test_max_iterations_is_enforced(self, agent_factory):
        endless = [
            make_result(
                calls=[ToolCall(id=f"t{i}", name="read_project_state", arguments={})],
                stop_reason="tool_use",
            )
            for i in range(20)
        ]
        agent = agent_factory(endless, max_iterations=3)
        result = agent.run("gorev")
        assert result.stop_reason == "max_iterations"
        assert result.iterations == 3

    def test_refusal_stops_with_error(self, agent_factory):
        agent = agent_factory([make_result(text="", stop_reason="refusal")])
        result = agent.run("gorev")
        assert not result.ok
        assert result.stop_reason == "refusal"

    def test_llm_error_is_captured_not_raised(self, agent_factory):
        agent = agent_factory([LLMError("ag hatasi")])
        result = agent.run("gorev")
        assert not result.ok
        assert "ag hatasi" in (result.error or "")
        assert result.stop_reason == "llm_error"

    def test_cost_and_usage_accumulate(self, agent_factory):
        agent = agent_factory(
            [
                make_result(
                    calls=[ToolCall(id="a", name="read_project_state", arguments={})],
                    stop_reason="tool_use",
                ),
                make_result(text="bitti"),
            ]
        )
        result = agent.run("gorev")
        assert result.cost == pytest.approx(0.002)
        assert result.usage.calls == 2
        assert result.usage.output_tokens == 10

    def test_tool_specs_include_server_tools(self, agent_factory):
        from deerx.llm import WEB_SEARCH_TOOL

        agent = agent_factory([make_result(text="ok")], server_tools=[WEB_SEARCH_TOOL])
        agent.run("gorev")
        tools = agent.client.calls[0]["tools"]
        assert any(t.get("name") == "web_search" for t in tools)
        assert any(t.get("name") == "read_project_state" for t in tools)

    def test_history_trimming_keeps_structure(self, agent_factory):
        from deerx.llm.anthropic_client import AnthropicClient

        messages = [{"role": "user", "content": "baslangic"}]
        for i in range(40):
            messages.append({"role": "assistant", "content": [{"type": "text", "text": "x"}]})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"t{i}",
                            "content": "y" * 20_000,
                            "is_error": False,
                        }
                    ],
                }
            )
        before = len(messages)
        AnthropicClient.trim_history(messages)
        assert len(messages) == before  # yapi korunur, mesaj silinmez

        trimmed = [
            b
            for m in messages
            if isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict) and "kirpildi" in str(b.get("content", ""))
        ]
        assert trimmed, "eski arac ciktilari kirpilmali"
        # Son mesajlar dokunulmadan kalmali.
        assert len(messages[-1]["content"][0]["content"]) == 20_000


class TestRoleWiring:
    def test_each_role_gets_only_its_tools(self, settings, ctx):
        from deerx.agents import build_agent
        from deerx.tools import TOOLSETS

        for role in TOOLSETS:
            agent = build_agent(
                role,
                settings=settings,
                client=FakeClient([]),
                registry=build_registry(),
                context=ctx,
                events=EventLog(None, echo=False),
                stream=False,
            )
            assert agent.registry.names() == sorted(TOOLSETS[role])

    def test_researcher_gets_server_tools_on_anthropic(self, settings, ctx):
        from deerx.agents import build_agent

        settings.provider = "anthropic"
        agent = build_agent(
            "researcher",
            settings=settings,
            client=FakeClient([]),
            registry=build_registry(),
            context=ctx,
            events=EventLog(None, echo=False),
            stream=False,
        )
        assert {t["name"] for t in agent.server_tools} == {"web_search", "web_fetch"}

    def test_local_provider_gets_no_server_tools(self, settings, ctx):
        """Sunucu tarafi web araclari Anthropic altyapisinda calisir.

        Yerel bir modelde karsiligi yoktur; gonderilirse model tanimadigi bir
        arac gorur. Yerine yerel `web_search` araci devreye girer.
        """
        from deerx.agents import build_agent

        settings.provider = "openai"
        agent = build_agent(
            "researcher",
            settings=settings,
            client=FakeClient([]),
            registry=build_registry(),
            context=ctx,
            events=EventLog(None, echo=False),
            stream=False,
        )
        assert agent.server_tools == []

    def test_web_disabled_removes_server_tools(self, settings, ctx):
        from deerx.agents import build_agent

        settings.enable_web = False
        agent = build_agent(
            "researcher",
            settings=settings,
            client=FakeClient([]),
            registry=build_registry(),
            context=ctx,
            events=EventLog(None, echo=False),
            stream=False,
        )
        assert agent.server_tools == []

    @pytest.mark.parametrize("role", ["backend", "frontend", "qa"])
    def test_implementers_cannot_define_requirements(self, settings, ctx, role):
        from deerx.agents import build_agent

        agent = build_agent(
            role,
            settings=settings,
            client=FakeClient([]),
            registry=build_registry(),
            context=ctx,
            events=EventLog(None, echo=False),
            stream=False,
        )
        # Uygulayicilar gereksinim/karar tanimlamaz; yalnizca uygular.
        assert "record_requirements" not in agent.registry.names()
        assert "record_decisions" not in agent.registry.names()
        assert "run_command" in agent.registry.names()

    def test_live_agent_cannot_write_files(self, settings, ctx):
        """Canli ajan incelenmis olani dagitir; kod yazmaz."""
        from deerx.agents import build_agent

        agent = build_agent(
            "live",
            settings=settings,
            client=FakeClient([]),
            registry=build_registry(),
            context=ctx,
            events=EventLog(None, echo=False),
            stream=False,
        )
        assert "write_file" not in agent.registry.names()
        assert "edit_file" not in agent.registry.names()
        assert "run_command" in agent.registry.names()

    def test_unknown_role(self, settings, ctx):
        from deerx.agents import build_agent

        with pytest.raises(KeyError):
            build_agent(
                "yok",
                settings=settings,
                client=FakeClient([]),
                registry=build_registry(),
                context=ctx,
                events=EventLog(None, echo=False),
            )


class TestImplementPhase:
    """Uygulama fazi: gorev basina taze ajan, unutulan update_task telafisi."""

    def _orchestrator(self, settings, monkeypatch, script_per_task):
        from deerx.logging import EventLog as EL
        from deerx.pipeline import Orchestrator

        orch = Orchestrator(settings, events=EL(None, echo=False), stream=False)
        made: list[FakeClient] = []

        def fake_build_agent(role, **kwargs):
            client = FakeClient(list(script_per_task))
            made.append(client)
            kwargs["client"] = client
            from deerx.agents.roles import build_agent as real

            return real(role, **kwargs)

        monkeypatch.setattr("deerx.pipeline.orchestrator.build_agent", fake_build_agent)
        orch._client = FakeClient([])  # gercek istemci kurulmasin
        return orch, made

    def test_routes_tasks_to_lane_agents(self, settings, monkeypatch):
        """Her gorev, seridine karsilik gelen uzman ajana gitmeli."""
        from deerx.pipeline.models import Phase

        orch, made = self._orchestrator(settings, monkeypatch, [make_result(text="ok")])
        roles: list[str] = []
        original = orch._role_for_task
        orch.state.add_task(Task(key="T-001", title="API", lane="backend", order_index=0))
        orch.state.add_task(Task(key="T-002", title="Form", lane="frontend", order_index=1))
        orch.state.add_task(Task(key="T-003", title="Test", lane="qa", order_index=2))
        orch.state.add_task(Task(key="T-004", title="CI", lane="infra", order_index=3))

        import deerx.pipeline.orchestrator as module
        real_build = module.build_agent

        def spy(role, **kwargs):
            roles.append(role)
            return real_build(role, **kwargs)

        monkeypatch.setattr(module, "build_agent", spy)
        orch.run_phase(Phase.IMPLEMENT)
        assert roles == ["backend", "frontend", "qa", "backend"]
        assert original  # kullanildi
        orch.close()

    def test_runs_tasks_in_dependency_order(self, settings, monkeypatch):
        from deerx.pipeline.models import Phase

        orch, made = self._orchestrator(settings, monkeypatch, [make_result(text="yapildi")])
        orch.state.add_task(Task(key="T-001", title="ilk", order_index=0))
        orch.state.add_task(Task(key="T-002", title="ikinci", deps=["T-001"], order_index=1))

        result = orch.run_phase(Phase.IMPLEMENT)
        assert result.details["completed"] == ["T-001", "T-002"]
        assert len(made) == 2  # her gorev icin taze ajan
        orch.close()

    def test_missing_update_task_is_backfilled(self, settings, monkeypatch):
        """Ajan `update_task` cagirmayi unutursa orkestrator sonucu kendisi yazar."""
        from deerx.pipeline.models import Phase

        orch, _ = self._orchestrator(settings, monkeypatch, [make_result(text="bitti ama kaydetmedim")])
        orch.state.add_task(Task(key="T-001", title="tek"))
        orch.run_phase(Phase.IMPLEMENT)
        task = orch.state.get_task("T-001")
        assert task.status == Status.DONE
        assert "kaydetmedim" in task.result
        orch.close()

    def test_single_task_mode(self, settings, monkeypatch):
        from deerx.pipeline.models import Phase

        orch, made = self._orchestrator(settings, monkeypatch, [make_result(text="ok")])
        orch.state.add_task(Task(key="T-001", title="a", order_index=0))
        orch.state.add_task(Task(key="T-002", title="b", order_index=1))
        orch.run_phase(Phase.IMPLEMENT, task_key="T-002")
        assert len(made) == 1
        assert orch.state.get_task("T-002").status == Status.DONE
        assert orch.state.get_task("T-001").status == Status.PENDING
        orch.close()

    def test_failed_agent_marks_task_failed(self, settings, monkeypatch):
        from deerx.pipeline.models import Phase

        orch, _ = self._orchestrator(settings, monkeypatch, [LLMError("model coktu")])
        orch.state.add_task(Task(key="T-001", title="a"))
        result = orch.run_phase(Phase.IMPLEMENT)
        assert orch.state.get_task("T-001").status == Status.FAILED
        assert result.details["failed"] == ["T-001"]
        orch.close()


class TestTruncatedResponse:
    """Uretim tavaninda kesilen yanit, bitmis yanit sanilmamali.

    Olculdu: on uc fazlik bir boru hatti kosusunda `assess` fazi ucuncu
    turda TAM 16000 token uretti -- yapilandirmadaki `max_tokens` degerinin
    kendisi. Yazmakta oldugu raporu kaydedemedi, arac cagrisi yarida kaldi
    ve dusuruldu; `tool_calls` bos geldigi icin dongu sessizce sona erdi ve
    faz "tamam" gorundu. Uc tur, bes arac, sifir cikti. `mockup` fazi da
    ayni sekilde bos gecti ve mimar "mockup yok" diyerek zorlandi.
    """

    def test_a_cut_off_answer_is_asked_to_continue(self, agent_factory):
        """Kesilen yanit "bitti" degil "devam et" demektir."""
        agent = agent_factory([
            make_result(text="rapor yazmaya basliyorum...", stop_reason="max_tokens"),
            make_result(text="tamamladim", stop_reason="end_turn"),
        ])
        sonuc = agent.run("gorev")

        assert sonuc.ok
        assert sonuc.stop_reason == "end_turn"
        assert sonuc.iterations == 2, "kesilen yanittan sonra devam edilmedi"
        # Modele ne oldugu soylenmis olmali.
        son = agent.client.last_messages[-1]
        assert "KESILDI" in str(son.get("content"))

    def test_the_hint_tells_the_model_how_to_recover(self, agent_factory):
        """"Devam et" yetmez: ayni uzunlukta tekrar denerse ayni yerde kesilir."""
        agent = agent_factory([
            make_result(text="...", stop_reason="max_tokens"),
            make_result(text="bitti"),
        ])
        agent.run("gorev")
        metin = str(agent.client.last_messages[-1]["content"])
        assert "save_artifact" in metin
        assert "kisa" in metin.lower() or "parca" in metin.lower()

    def test_repeated_truncation_is_reported_not_hidden(self, agent_factory):
        """Her turda tavani dolduran bir model sonsuza kadar denenmez."""
        from deerx.agents.base import _MAX_TRUNCATIONS

        agent = agent_factory(
            [make_result(text="...", stop_reason="max_tokens") for _ in range(6)],
            max_iterations=10,
        )
        sonuc = agent.run("gorev")

        assert not sonuc.ok
        assert sonuc.stop_reason == "max_tokens"
        assert "max_tokens" in (sonuc.error or "")
        assert sonuc.iterations == _MAX_TRUNCATIONS + 1

    def test_a_normal_ending_is_untouched(self, agent_factory):
        """Duzgun biten tur ekstra bir tura zorlanmamali."""
        agent = agent_factory([make_result(text="bitti", stop_reason="end_turn")])
        sonuc = agent.run("gorev")
        assert sonuc.iterations == 1
        assert sonuc.stop_reason == "end_turn"

    def test_truncation_with_usable_tool_calls_still_runs_them(self, agent_factory):
        """Kesilme bazi arac cagrilarini saglam birakabilir; onlar calismali."""
        agent = agent_factory([
            make_result(
                text="okuyorum",
                stop_reason="max_tokens",
                calls=[ToolCall(id="c1", name="read_project_state", arguments={})],
            ),
            make_result(text="bitti"),
        ])
        sonuc = agent.run("gorev")
        assert sonuc.tool_calls == 1
        assert sonuc.ok

    def test_a_call_truncated_mid_arguments_is_not_executed(self, agent_factory):
        """Cagrinin ORTASINDA kesilmek de kesilmedir.

        Ustteki dalin yorumu "yarim kalan arac cagrisi dusuyor ve
        `tool_calls` bos geliyor" diyordu. OLCULDU (yerel vLLM, uctan uca
        kosunun mockup fazi): dusmuyor. Model tam 32000 token uretti
        (tavanin kendisi), JSON argumanlari yarida kesildi, saglayici
        cagriyi yine de dondurdu. `tool_calls` dolu oldugu icin kesilme
        dali atlandi, arac BOS sozlukle kosturuldu ve modele su gitti:

            SaveArtifact.run() missing 2 required positional arguments

        Model kendi cagrisinda hata aramaya basladi. Oysa harness kesildigini
        biliyordu: `finish_reason == "length"` ayni yerde okunuyor.
        """
        agent = agent_factory([
            make_result(
                text="mockup yaziyorum",
                stop_reason="max_tokens",
                calls=[ToolCall(id="c1", name="save_artifact", arguments={},
                                arguments_ok=False)],
            ),
            make_result(text="daha kisa bir cikti yazdim"),
        ])
        sonuc = agent.run("gorev")

        assert sonuc.tool_calls == 0, (
            "yarim kalan cagri calistirilmamali: bos argumanla kosmak modele "
            "kesildigini degil imza hatasi oldugunu soyluyordu"
        )
        assert sonuc.ok, "model uyarilip devam edebilmeli"


class TestTurnBudgetWarning:
    """Ajan tur butcesinin bittigini bilmeli.

    Olculdu: on uc fazlik bir kosuda QA fazi yirmi dort turun tamamini
    uygulamayi acip UAT yaparak harcadi -- ekran goruntuleri de aldi -- ama
    `qa-raporu.md` yazamadan tur siniri doldu ve durduruldu. Harness kac tur
    kaldigini biliyordu, model bilmiyordu. Yapilan is kayboldu: kaydedilmemis
    bir inceleme hic yapilmamis sayilir.
    """

    def test_the_model_is_told_before_the_budget_runs_out(self, agent_factory):
        agent = agent_factory(
            [
                make_result(
                    text=f"tur {i}",
                    calls=[ToolCall(id=f"c{i}", name="read_project_state", arguments={})],
                )
                for i in range(10)
            ],
            max_iterations=10,
        )
        agent.run("gorev")
        notlar = [
            str(m.get("content"))
            for cagri in agent.client.calls
            for m in cagri["messages"]
            if m.get("role") == "user" and "TUR BUTCESI" in str(m.get("content"))
        ]
        assert notlar, "butce uyarisi hic verilmedi"
        assert "save_artifact" in notlar[0], "ne yapmasi gerektigi yazmali"

    def test_the_warning_lands_with_turns_to_spare(self, agent_factory):
        """Son turda uyarmak hicbir sey degistirmez."""
        agent = agent_factory([], max_iterations=10)
        assert agent._warn_iteration <= 8
        assert agent._warn_iteration >= 1

    def test_a_short_budget_still_leaves_room(self, agent_factory):
        """Kucuk butcelerde de uyari bir ise yaramali."""
        for sinir in (3, 4, 5):
            agent = agent_factory([], max_iterations=sinir)
            assert sinir - agent._warn_iteration >= 2, sinir

    def test_the_warning_is_given_once(self, agent_factory):
        """Her turda tekrarlanan uyari gecmisi doldurur."""
        agent = agent_factory(
            [
                make_result(
                    text="x",
                    calls=[ToolCall(id=f"d{i}", name="read_project_state", arguments={})],
                )
                for i in range(10)
            ],
            max_iterations=10,
        )
        agent.run("gorev")
        son = agent.client.last_messages
        kac = sum(1 for m in son if "TUR BUTCESI" in str(m.get("content")))
        assert kac == 1, f"{kac} kez uyarilmis"

    def test_a_short_run_is_never_warned(self, agent_factory):
        """Isini iki turda bitiren ajan gereksiz yere durtulmemeli."""
        agent = agent_factory([make_result(text="bitti")], max_iterations=20)
        agent.run("gorev")
        for cagri in agent.client.calls:
            for m in cagri["messages"]:
                assert "TUR BUTCESI" not in str(m.get("content"))


class TestThinkingOverrun:
    """Uretim tavanina iki ayri sebeple takilinir.

    Olculdu (yerel Qwen3, `max_tokens=4000`): model butcenin TAMAMINI akil
    yurutmeye harcadi -- 15.354 karakter reasoning, 0 karakter cevap,
    0 arac cagrisi, `finish_reason: length`. O ana kadar modele "raporunu
    kisalt, parca parca yaz" deniyordu; ortada rapor yokken bu tavsiye
    anlamsizdir ve modeli hicbir yere goturmez.
    """

    @staticmethod
    def _kesilen(*, thinking: str = "", text: str = "") -> LLMResult:
        """Uretim tavaninda kesilmis bir yanit."""
        return LLMResult(
            text=text,
            thinking=thinking,
            tool_calls=[],
            stop_reason="max_tokens",
            usage=Usage(input_tokens=10, output_tokens=16000, calls=1),
            cost=0.0,
            model="sahte",
            raw=[],
        )

    @staticmethod
    def _notlar(ajan) -> str:
        """Ajanin gecmise ekledigi kullanici notlari."""
        return "\n".join(
            str(m.get("content"))
            for m in ajan.client.last_messages
            if m.get("role") == "user"
        )

    def test_thinking_overrun_gets_its_own_advice(self, agent_factory):
        from deerx.i18n import set_language

        ajan = agent_factory([
            self._kesilen(thinking="uzun uzun dusunuyorum..."),
            make_result(text="tamam"),
        ])
        try:
            set_language("en")
            ajan.run("degerlendir")
            notlar = self._notlar(ajan)
        finally:
            set_language("tr")

        assert "CALL A TOOL first" in notlar, notlar[-300:]
        assert "keep it shorter" not in notlar

    def test_a_truncated_answer_still_gets_the_old_advice(self, agent_factory):
        """Gercekten uzun bir CEVAP kesildiyse tavsiye degismemeli."""
        from deerx.i18n import set_language

        ajan = agent_factory([
            self._kesilen(text="# Rapor\nBolum 1..."),
            make_result(text="tamam"),
        ])
        try:
            set_language("en")
            ajan.run("degerlendir")
            notlar = self._notlar(ajan)
        finally:
            set_language("tr")

        assert "keep it shorter" in notlar
        assert "CALL A TOOL first" not in notlar

    def test_giving_up_names_the_real_cause(self, agent_factory):
        """Operatore `max_tokens`'in DUSUNMEYI de kapsadigi soylenmeli --
        yoksa tavani yukseltmenin ise yarayacagini bilemez."""
        from deerx.i18n import set_language

        ajan = agent_factory(
            [self._kesilen(thinking="dusunuyorum") for _ in range(5)],
            max_iterations=6,
        )
        try:
            set_language("en")
            sonuc = ajan.run("degerlendir")
        finally:
            set_language("tr")

        assert "covers the thinking too" in (sonuc.error or ""), sonuc.error

    def test_both_messages_exist_in_both_languages(self):
        from deerx.i18n import set_language, t

        try:
            for dil, beklenen in (("en", "CALL A TOOL"), ("tr", "ARAC CAGIR")):
                set_language(dil)
                assert beklenen in t("agent.thinking_overrun")
                assert t("agent.thinking_overrun_giving_up", n=3) != \
                    "agent.thinking_overrun_giving_up"
        finally:
            set_language("tr")
