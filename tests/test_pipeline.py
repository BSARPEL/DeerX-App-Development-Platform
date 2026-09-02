"""Durum, konfigurasyon, prompt ve orkestrasyon testleri."""

from __future__ import annotations

from pathlib import Path

import pytest

from deerx.agents.prompts import ROLES, compose_system, load_prompt
from deerx.config import CONFIG_FILENAME, find_workspace, load_settings
from deerx.llm.pricing import Usage, cost_usd, price_for
from deerx.pipeline.models import Gap, Phase, Requirement, Status, Task
from deerx.pipeline.state import ProjectState
from deerx.tools import TOOLSETS


def write_toml(path: Path, *lines: str) -> None:
    """TOML dosyasini satir listesinden yazar."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestPhases:
    def test_order_is_stable(self):
        assert [str(p) for p in Phase.ordered()] == [
            "ingest", "analyze", "research", "assess", "mockup", "design",
            "plan", "implement", "qa", "review", "package", "staging", "live",
        ]
        # Mockup mimariden once gelir: ekrani gormeden veri modeli dogru kurulmaz.
        assert Phase.MOCKUP.index < Phase.DESIGN.index
        # Dogrulama uygulamadan sonra, dagitim dogrulamadan sonra gelir.
        assert Phase.IMPLEMENT.index < Phase.QA.index < Phase.REVIEW.index
        # Teslimat paketi incelemeden sonra, sahneleme oncesinde uretilir.
        assert Phase.REVIEW.index < Phase.PACKAGE.index < Phase.STAGING.index
        assert Phase.STAGING.index < Phase.LIVE.index

    def test_every_phase_has_a_role_or_is_special(self):
        from deerx.pipeline.orchestrator import PHASE_ROLE
        from deerx.tools import TOOLSETS

        for phase in Phase.ordered():
            if phase in (Phase.INGEST, Phase.PACKAGE, Phase.IMPLEMENT):
                # ingest ve package ajansiz (deterministik),
                # implement serit bazli yonlendirilir
                continue
            assert phase in PHASE_ROLE, phase
            assert PHASE_ROLE[phase] in TOOLSETS, phase

    def test_lane_routing_covers_planner_options(self):
        from deerx.tools import LANE_ROLE, TOOLSETS
        from deerx.tools.project import RecordTasks

        schema = RecordTasks().schema["properties"]["items"]["items"]["properties"]
        for lane in schema["lane"]["enum"]:
            assert lane in LANE_ROLE, f"plan '{lane}' seridi uretebilir ama yonlendirme yok"
            assert LANE_ROLE[lane] in TOOLSETS

    def test_every_phase_has_label(self):
        assert all(p.label for p in Phase.ordered())


class TestState:
    def test_requirement_upsert_and_priority_order(self, state):
        state.add_requirement(Requirement(key="REQ-002", title="B", priority="could"))
        state.add_requirement(Requirement(key="REQ-001", title="A", priority="must"))
        state.add_requirement(Requirement(key="REQ-002", title="B guncel", priority="could"))
        keys = [r.key for r in state.list_requirements()]
        assert keys == ["REQ-001", "REQ-002"]
        assert state.list_requirements()[1].title == "B guncel"

    def test_gap_severity_order(self, state):
        state.add_gap(Gap(key="GAP-001", title="dusuk", severity="low"))
        state.add_gap(Gap(key="GAP-002", title="kritik", severity="critical"))
        assert [g.key for g in state.list_gaps()] == ["GAP-002", "GAP-001"]

    def test_ready_and_blocked_tasks(self, state):
        state.add_task(Task(key="T-001", title="ilk"))
        state.add_task(Task(key="T-002", title="ikinci", deps=["T-001"]))
        assert [t.key for t in state.ready_tasks()] == ["T-001"]
        assert [t.key for t in state.blocked_tasks()] == ["T-002"]

        state.update_task("T-001", status=Status.DONE)
        assert [t.key for t in state.ready_tasks()] == ["T-002"]

    def test_dependency_cycle_leaves_everything_blocked(self, state):
        state.add_task(Task(key="T-001", title="a", deps=["T-002"]))
        state.add_task(Task(key="T-002", title="b", deps=["T-001"]))
        assert state.ready_tasks() == []
        assert len(state.blocked_tasks()) == 2

    def test_phase_lifecycle(self, state):
        assert state.phase_status(Phase.ANALYZE).status == Status.PENDING
        state.start_phase(Phase.ANALYZE)
        assert state.phase_status(Phase.ANALYZE).status == Status.RUNNING
        state.finish_phase(Phase.ANALYZE, summary="bitti", cost_usd=0.5)
        result = state.phase_status(Phase.ANALYZE)
        assert result.status == Status.DONE and result.cost_usd == 0.5

    def test_phase_cost_accumulates(self, state):
        state.finish_phase(Phase.ANALYZE, cost_usd=0.25)
        state.finish_phase(Phase.ANALYZE, cost_usd=0.25)
        assert state.phase_status(Phase.ANALYZE).cost_usd == 0.5

    def test_meta_roundtrip(self, state):
        state.set_meta("goal", "saha servis")
        assert state.get_meta("goal") == "saha servis"
        assert state.get_meta("yok", "varsayilan") == "varsayilan"

    def test_snapshot_contains_all_sections(self, state):
        state.add_requirement(Requirement(key="REQ-001", title="Gereksinim A"))
        state.add_gap(Gap(key="GAP-001", title="Bosluk A"))
        state.add_task(Task(key="T-001", title="Gorev A"))
        snapshot = state.snapshot()
        assert "Gereksinim A" in snapshot
        assert "Bosluk A" in snapshot
        assert "Gorev A" in snapshot

    def test_empty_snapshot(self, state):
        assert "kayit yok" in state.snapshot()

    def test_counts(self, state):
        state.add_task(Task(key="T-001", title="a", status=Status.DONE))
        state.add_task(Task(key="T-002", title="b"))
        counts = state.counts()
        assert counts["tasks"] == 2 and counts["tasks_done"] == 1

    def test_shares_database_with_knowledge_base(self, kb, settings, workspace):
        """RAG ve durum ayni SQLite dosyasini paylasir; tablolar carpismaz."""
        kb.ingest_path(workspace / "docs")
        st = ProjectState(settings.db_path)
        st.add_requirement(Requirement(key="REQ-001", title="x"))
        assert len(st.list_requirements()) == 1
        assert kb.stats()["chunks"] > 0
        st.close()


class TestConfig:
    def test_toml_overrides_defaults(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            '[deerx]\nmodel_lead = "claude-sonnet-5"\nmax_iterations = 7\n'
            '\n[deerx.rag]\ntop_k = 3\n',
            encoding="utf-8",
        )
        cfg = load_settings(tmp_path)
        assert cfg.model_lead == "claude-sonnet-5"
        assert cfg.max_iterations == 7
        assert cfg.rag.top_k == 3

    def test_overrides_beat_toml(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            '[deerx]\napproval_mode = "ask"\n', encoding="utf-8"
        )
        assert load_settings(tmp_path, approval_mode="auto").approval_mode == "auto"

    def test_none_override_is_ignored(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            '[deerx]\napproval_mode = "dry-run"\n', encoding="utf-8"
        )
        assert load_settings(tmp_path, approval_mode=None).approval_mode == "dry-run"

    def test_legacy_workspace_is_migrated(self, tmp_path: Path):
        """Praxis adiyla olusturulmus bir calisma alani veri kaybetmeden tasinir."""
        from deerx.config import DATA_DIRNAME, migrate_legacy_workspace

        write_toml(tmp_path / "praxis.toml", "[praxis]", 'language = "tr"')
        legacy = tmp_path / ".praxis"
        (legacy / "artifacts").mkdir(parents=True)
        (legacy / "praxis.db").write_bytes(b"veritabani")
        (legacy / "praxis.db-wal").write_bytes(b"wal")
        (legacy / "artifacts" / "rapor.md").write_text("# Rapor", encoding="utf-8")

        assert migrate_legacy_workspace(tmp_path) is True
        new = tmp_path / DATA_DIRNAME
        assert (new / "deerx.db").read_bytes() == b"veritabani"
        assert (new / "deerx.db-wal").exists()
        assert (new / "artifacts" / "rapor.md").exists()
        assert (tmp_path / CONFIG_FILENAME).is_file()
        assert not legacy.exists()

    def test_legacy_toml_section_is_read(self, tmp_path: Path):
        """Eski dosyalarda kok blok [praxis] adiyla yazilmisti."""
        write_toml(tmp_path / "praxis.toml", "[praxis]", "max_iterations = 9")
        assert load_settings(tmp_path).max_iterations == 9

    def test_migration_does_not_clobber_existing_data(self, tmp_path: Path):
        """Yeni dizin zaten varsa eskiye dokunulmaz — veri ezilmemeli."""
        from deerx.config import DATA_DIRNAME, migrate_legacy_workspace

        (tmp_path / ".praxis").mkdir()
        (tmp_path / ".praxis" / "praxis.db").write_bytes(b"eski")
        (tmp_path / DATA_DIRNAME).mkdir()
        (tmp_path / DATA_DIRNAME / "deerx.db").write_bytes(b"yeni")

        migrate_legacy_workspace(tmp_path)
        assert (tmp_path / DATA_DIRNAME / "deerx.db").read_bytes() == b"yeni"
        assert (tmp_path / ".praxis").exists()

    def test_migration_runs_before_anything_creates_the_directory(self, tmp_path: Path):
        """`load_settings` gecisi ilk is olarak yapmali.

        `EventLog` gibi yardimcilar kendi ust dizinlerini yaratir; gec kalan bir
        gecis "yeni dizin zaten var" deyip veriyi geride birakirdi.
        """
        from deerx.config import DATA_DIRNAME

        write_toml(tmp_path / "praxis.toml", "[praxis]")
        (tmp_path / ".praxis").mkdir()
        (tmp_path / ".praxis" / "praxis.db").write_bytes(b"tasinmali")

        load_settings(tmp_path)
        assert (tmp_path / DATA_DIRNAME / "deerx.db").read_bytes() == b"tasinmali"

    def test_find_workspace_walks_up(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text("[deerx]\n", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_workspace(nested) == tmp_path.resolve()

    def test_role_to_model_mapping(self, settings):
        # Muhakemesi agir roller lead katmaninda
        for role in ("analyst", "architect", "planner", "qa", "reviewer", "live"):
            assert settings.model_for(role) == settings.model_lead, role
        # Uzun ama mekanik roller worker katmaninda
        for role in ("researcher", "mockup", "backend", "frontend", "staging"):
            assert settings.model_for(role) == settings.model_worker, role
        # Bilinmeyen rol icin guvenli taraf: en yetenekli model
        assert settings.model_for("bilinmeyen") == settings.model_lead

    def test_the_budget_is_never_silently_rewritten(self, tmp_path: Path):
        """Kullanicinin verdigi butceye dokunulmaz.

        Onceden yerel saglayicida `max_tokens` sessizce 8K'ya dusuruluyordu;
        ayarlar ekranindan 220K girip kaydeden kullanici degerin neden
        tutmadigini anlayamazdi. Artik deger korunur, tutarsizlik uyarilir.

        Sinanan sey saginin kendisi degil, *saglayiciya gore degismemesi*:
        ayni ayar iki saglayicida da ayni degeri vermeli. (Istek basina
        kirpma ayri bir katman; bkz. TestContextWindow.)
        """
        for value in (8_000, 64_000, 220_000):
            local = load_settings(tmp_path, provider="openai", max_tokens=value)
            remote = load_settings(tmp_path, provider="anthropic", max_tokens=value)
            assert local.max_tokens == remote.max_tokens == value

        # Varsayilan da saglayiciya gore degismemeli.
        assert (
            load_settings(tmp_path, provider="openai").max_tokens
            == load_settings(tmp_path, provider="anthropic").max_tokens
        )

    def test_a_budget_longer_than_the_timeout_is_warned_about(self, tmp_path, caplog):
        """Sessizce yarida kesilen bir istegin sebebi gorunmez."""
        import logging

        with caplog.at_level(logging.WARNING, logger="deerx.config"):
            load_settings(
                tmp_path, provider="openai",
                max_tokens=220_000, request_timeout_seconds=60,
            )
        assert any("yarida" in r.message for r in caplog.records), caplog.text

    def test_a_coherent_budget_is_not_warned_about(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="deerx.config"):
            load_settings(
                tmp_path, provider="openai",
                max_tokens=8_000, request_timeout_seconds=1800,
            )
        assert not [r for r in caplog.records if "yarida" in r.message]

    def test_explicit_max_tokens_is_respected(self, tmp_path: Path):
        (tmp_path / CONFIG_FILENAME).write_text(
            "\n".join(['[deerx]', 'provider = "openai"', "max_tokens = 24000"]),
            encoding="utf-8",
        )
        assert load_settings(tmp_path).max_tokens == 24000

    def test_provider_readiness(self, tmp_path: Path):
        local = load_settings(tmp_path, provider="openai")
        assert local.llm_ready is True          # taban adres varsayilanda tanimli
        assert local.supports_server_tools is False

        remote = load_settings(tmp_path, provider="anthropic")
        remote.anthropic_api_key = None
        assert remote.llm_ready is False
        assert "ANTHROPIC_API_KEY" in remote.llm_hint
        assert remote.supports_server_tools is True

    def test_derived_paths_under_workspace(self, settings):
        assert settings.db_path.parent == settings.data_dir
        assert settings.artifacts_dir.is_relative_to(settings.workspace)


class TestPricing:
    def test_known_model(self):
        assert price_for("claude-opus-5") == (5.00, 25.00)
        assert price_for("claude-sonnet-5") == (2.00, 10.00)

    def test_unknown_model_falls_back_high(self):
        # Bilinmeyen model dusuk degil yuksek tahmin edilmeli.
        assert price_for("claude-gelecek-9") == (5.00, 25.00)

    def test_cache_read_is_cheaper_than_fresh_input(self):
        fresh = cost_usd("claude-opus-5", Usage(input_tokens=1_000_000))
        cached = cost_usd("claude-opus-5", Usage(cache_read_input_tokens=1_000_000))
        assert cached < fresh / 5

    def test_usage_addition(self):
        total = Usage(input_tokens=10, calls=1) + Usage(input_tokens=5, output_tokens=2, calls=1)
        assert total.input_tokens == 15 and total.calls == 2

    def test_usage_from_api_tolerates_missing_fields(self):
        class Partial:
            input_tokens = 5
            output_tokens = 3

        usage = Usage.from_api(Partial())
        assert usage.input_tokens == 5 and usage.cache_read_input_tokens == 0


class TestPrompts:
    def test_every_role_has_a_prompt(self, settings):
        for role in ROLES:
            assert len(load_prompt(role, settings)) > 200

    def test_shared_preamble_is_formatted(self, settings):
        system = compose_system("analyst", settings)
        assert str(settings.workspace.as_posix()) in system
        assert "{workspace}" not in system
        assert "Turkce" in system

    def test_workspace_prompt_overrides_package(self, settings):
        settings.prompts_dir.mkdir(parents=True, exist_ok=True)
        (settings.prompts_dir / "analyst.md").write_text(
            "OZEL PROMPT " + "x" * 300, encoding="utf-8"
        )
        load_prompt.cache_clear() if hasattr(load_prompt, "cache_clear") else None
        assert "OZEL PROMPT" in compose_system("analyst", settings)

    def test_unknown_role(self, settings):
        from deerx.errors import ConfigError

        with pytest.raises(ConfigError):
            load_prompt("yok", settings)

    def test_toolsets_reference_real_tools(self, registry):
        for _role, names in TOOLSETS.items():
            registry.subset(names)  # bilinmeyen arac varsa KeyError firlatir

    def test_every_prompt_role_has_a_toolset(self):
        # `_shared` ortak on sozdur; kalan her rolun arac kumesi olmali.
        assert set(ROLES) <= set(TOOLSETS)


class TestOrchestrator:
    def test_ingest_phase_without_api_key(self, settings, workspace):
        """Ingest fazi model cagrisi yapmaz: API anahtari olmadan calismalidir."""
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        settings.anthropic_api_key = None
        with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as orch:
            result = orch.run_phase(Phase.INGEST, sources=[workspace / "docs"])
            assert result.ok
            assert orch.kb.stats()["chunks"] > 0
            assert orch.state.phase_status(Phase.INGEST).status == Status.DONE

    def test_ingest_fails_loudly_on_empty_corpus(self, settings, tmp_path):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        empty = tmp_path / "bos"
        empty.mkdir()
        with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as orch:
            result = orch.run_phase(Phase.INGEST, sources=[empty])
            assert not result.ok
            assert "bos kaldi" in (result.error or "")

    def test_implement_without_plan_fails(self, settings):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as orch:
            result = orch.run_phase(Phase.IMPLEMENT)
            assert not result.ok
            assert "Plan bos" in (result.error or "")

    def test_completed_phase_is_skipped_without_force(self, settings):
        """Ayni hedef icin tamamlanmis faz tekrar kosmaz.

        Faz olarak `ingest` KULLANILMAZ: onun isi yeni belgeleri fark etmek
        ve bu yuzden hicbir zaman butun olarak atlanmaz (bkz.
        `tests/test_staleness.py`). Kural digerleri icin gecerlidir.
        """
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as orch:
            orch.state.set_meta("goal", "Ayni hedef")
            orch.state.finish_phase(Phase.ANALYZE, status=Status.DONE, summary="bitti")
            report = orch.run([Phase.ANALYZE], goal="Ayni hedef")
            assert report.phases[0].status == Status.SKIPPED


class TestQuestionGate:
    """Faz kapisi: cevaplanmamis bloke edici soru boru hattini durdurur."""

    def _orchestrator(self, settings):
        from deerx.logging import EventLog
        from deerx.pipeline import Orchestrator

        return Orchestrator(settings, events=EventLog(None, echo=False), stream=False)

    def test_blocking_question_halts_pipeline(self, settings, workspace):
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(
                Question(key="Q-001", question="ERP API bicimi?", blocking=True)
            )
            report = orch.run([Phase.INGEST], sources=[workspace / "docs"])

        assert report.needs_input
        assert report.ok, "kapi bir hata degil, bekleme durumudur"
        gate = report.phases[-1]
        assert gate.status == Status.NEEDS_INPUT
        assert gate.phase is None
        assert gate.label == "Bilgi bekleniyor"
        assert report.pending_questions() == ["Q-001"]

    def test_ingest_does_not_run_behind_a_closed_gate(self, settings, workspace):
        """Kapi faz BASLAMADAN once kontrol edilir; is bosa harcanmaz."""
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(Question(key="Q-001", question="?", blocking=True))
            report = orch.run([Phase.INGEST], sources=[workspace / "docs"])
            assert orch.kb.stats()["chunks"] == 0, "faz calismamaliydi"
        assert report.needs_input
        assert len(report.phases) == 1

    def test_non_blocking_question_does_not_halt(self, settings, workspace):
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(
                Question(key="Q-001", question="Renk?", blocking=False, suggestion="mavi")
            )
            report = orch.run([Phase.INGEST], sources=[workspace / "docs"])
            assert orch.kb.stats()["chunks"] > 0
        assert not report.needs_input

    def test_answering_reopens_the_pipeline(self, settings, workspace):
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(Question(key="Q-001", question="Butce?", blocking=True))
            assert orch.run([Phase.INGEST], sources=[workspace / "docs"]).needs_input

            orch.answer_question("Q-001", "250 bin TL")
            report = orch.run([Phase.INGEST], sources=[workspace / "docs"], force=True)
            assert not report.needs_input
            assert orch.kb.stats()["chunks"] > 0

    def test_skipping_also_reopens_the_pipeline(self, settings, workspace):
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(Question(key="Q-001", question="Renk?", blocking=True))
            orch.skip_question("Q-001", "marka mavisi")
            report = orch.run([Phase.INGEST], sources=[workspace / "docs"])
            assert not report.needs_input
            assert orch.state.get_question("Q-001").status == "skipped"

    def test_answers_land_in_the_knowledge_base(self, settings, workspace):
        """Cevap yalnizca hafizada kalmamali; ajanlar arayabilmeli."""
        from deerx.pipeline.answers import ANSWERS_SOURCE
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(
                Question(key="Q-001", question="Hangi ERP kullaniliyor?", blocking=True)
            )
            orch.answer_question("Q-001", "Logo Tiger 3 Enterprise")

            sources = {d["source"] for d in orch.kb.list_documents()}
            assert ANSWERS_SOURCE in sources
            hits = orch.kb.search("Logo Tiger ERP", k=3)
            assert any("Logo Tiger" in h.text for h in hits)

    def test_skipped_assumption_reaches_agents(self, settings):
        from deerx.pipeline.models import Question

        with self._orchestrator(settings) as orch:
            orch.state.add_question(Question(key="Q-001", question="Renk?", blocking=True))
            orch.skip_question("Q-001", "marka mavisi")
            context = orch._phase_context(Phase.ANALYZE)
            assert "marka mavisi" in context

    def test_brief_reaches_agents(self, settings):
        with self._orchestrator(settings) as orch:
            orch.state.set_meta("brief", "Mobil onceligi ver, masaustunu sonraya birak.")
            context = orch._phase_context(Phase.ANALYZE)
            assert "Kullanicinin talimati" in context
            assert "Mobil onceligi ver" in context

    def test_unknown_question(self, settings):
        with self._orchestrator(settings) as orch:
            assert orch.answer_question("Q-999", "x") is None
            assert orch.skip_question("Q-999") is None


class TestCalisanKosuYetimSanilmaz:
    """Ikinci bir surec, CALISAN bir kosuyu yetim sanip kapatiyordu.

    `reclaim_orphaned_runs` / `reclaim_orphaned_tasks` "acilista hicbir sey
    kosmuyor, dolayisiyla `running` goren her kayit yetimdir" varsayimina
    dayaniyordu. O varsayim ayni calisma alanini IKINCI bir surec
    actiginda yanlis -- ve bu desteklenen bir kullanim: README kosuyu
    izlemek icin arayuzu oneriyor, kod tabani da "CLI'dan indekslenen
    dokuman web sunucusunda gorunmuyordu" hatasini bu senaryo icin
    duzeltmis.

    OLCULDU (gercek UAT kosusu): `deerx run` terminalde `research` fazini
    kosarken `deerx serve` acildi. Kosu kaydi aninda `cancelled` oldu ve
    uzerine "Sunucu yeniden baslatildi; kosu yarida kesildi." yazildi --
    oysa kosu devam ediyor ve token harcamaya devam ediyordu. Kullanici
    arayuzde bitmis bir kosu goruyor, gercekte calisan bir kosu var.

    Gorev tarafinda bedel daha agir: o anda uygulanan bir gorev kuyruga
    geri doner ve ikinci bir ajan ayni isi bastan yapabilir.
    """

    def test_a_run_owned_by_a_live_process_is_left_alone(self, tmp_path):
        durum = ProjectState(tmp_path / "d.db")
        durum.start_run("k1", goal="hedef", phases=["analyze"])

        # Ayni surec (testin kendisi) sahibi; yani kosu YASIYOR.
        yetimler = durum.reclaim_orphaned_runs()

        assert yetimler == [], "calisan kosu yetim sayildi"
        kosu = durum.get_run("k1")
        assert kosu["status"] == Status.RUNNING
        assert not kosu["error"]
        durum.close()

    def test_a_run_owned_by_a_dead_process_is_reclaimed(self, tmp_path):
        """Asil is yine yapilmali: gercekten olmus bir kosu kapatilir."""
        durum = ProjectState(tmp_path / "d.db")
        durum.start_run("k1", goal="hedef", phases=["analyze"])
        # Sahipligi, var olmayan bir surece devret.
        durum._conn.execute("UPDATE runs SET pid = ? WHERE id = ?", (_olu_pid(), "k1"))
        durum._conn.commit()

        yetimler = durum.reclaim_orphaned_runs()

        assert yetimler, "olu surecin kosusu geri alinmadi"
        assert durum.get_run("k1")["status"] == Status.CANCELLED
        durum.close()

    def test_only_the_dead_run_is_touched(self, tmp_path):
        """Iki kosu varsa yalnizca olusu kapanmali.

        Eski kod tek bir `UPDATE ... WHERE status='running'` calistiriyordu,
        yani bir tanesi yetim oldugunda HEPSI kapaniyordu.
        """
        durum = ProjectState(tmp_path / "d.db")
        durum.start_run("canli", goal="hedef", phases=["analyze"])
        durum.start_run("olu", goal="hedef", phases=["research"])
        durum._conn.execute("UPDATE runs SET pid = ? WHERE id = ?", (_olu_pid(), "olu"))
        durum._conn.commit()

        durum.reclaim_orphaned_runs()

        assert durum.get_run("canli")["status"] == Status.RUNNING
        assert durum.get_run("olu")["status"] == Status.CANCELLED
        durum.close()

    def test_a_task_being_implemented_is_not_returned_to_the_queue(self, tmp_path):
        durum = ProjectState(tmp_path / "d.db")
        durum.add_task(Task(key="T-001", title="Saglik ucu"))
        durum.update_task("T-001", status=Status.RUNNING)

        geri_alinan = durum.reclaim_orphaned_tasks()

        assert geri_alinan == [], "uygulanan gorev kuyruga geri dondu"
        assert durum.get_task("T-001").status == Status.RUNNING
        durum.close()

    def test_a_task_left_by_a_dead_process_is_returned(self, tmp_path):
        """Asil is yine yapilmali: yoksa plan kilitlenir."""
        durum = ProjectState(tmp_path / "d.db")
        durum.add_task(Task(key="T-001", title="Saglik ucu"))
        durum.update_task("T-001", status=Status.RUNNING)
        durum._conn.execute("UPDATE tasks SET pid = ? WHERE key = ?", (_olu_pid(), "T-001"))
        durum._conn.commit()

        assert durum.reclaim_orphaned_tasks() == ["T-001"]
        assert durum.get_task("T-001").status == Status.PENDING
        durum.close()

    def test_a_record_from_before_the_column_is_still_reclaimed(self, tmp_path):
        """Sutun eklenmeden onceki kayitlarda sahip bilinmiyor (`pid = 0`).
        Orada eski davranis surer: yetim sayilir. Aksi halde gecmisten
        kalan bir kayit sonsuza dek "calisiyor" gorunurdu."""
        durum = ProjectState(tmp_path / "d.db")
        durum.start_run("eski", goal="hedef", phases=["analyze"])
        durum._conn.execute("UPDATE runs SET pid = 0 WHERE id = ?", ("eski",))
        durum._conn.commit()

        assert durum.reclaim_orphaned_runs()
        assert durum.get_run("eski")["status"] == Status.CANCELLED
        durum.close()


def _olu_pid() -> int:
    """Kesinlikle calismayan bir surec kimligi."""
    from deerx.process import process_alive

    for aday in range(600000, 600200):
        if not process_alive(aday):
            return aday
    raise AssertionError("olu bir pid bulunamadi")
