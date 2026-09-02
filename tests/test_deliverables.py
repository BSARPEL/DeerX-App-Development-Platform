"""Faz sozlesmesi: "bitti" demek bir sey uretmis olmayi gerektirir.

Tam bir boru hatti kosusunda olculdu. `assess` fazi uc turda yalnizca dosya
okudu ve durdu; `mockup` fazi iki turda uc arama yapip durdu. Ikisi de `done`
isaretlendi ve hicbiri tek satir uretmedi -- ne bosluk analizi, ne bir HTML
ekran. Boru hatti eksik girdiyle ilerledi ve mimar "mockup yok, kod tabani
bos" diyerek zorlandi.

Sebep: faz durumu yalnizca modelin konusmayi bitirmesine bakiyordu
(`stop_reason == "end_turn"` -> `done`). Beklenen ciktinin adi yalnizca
yonergelerde yaziliydi ve hicbir sey uygulamiyordu.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from deerx.pipeline.models import Artifact, Phase, Status, Task
from deerx.pipeline.orchestrator import PHASE_DELIVERABLE


@dataclass
class _SahteSonuc:
    """AgentResult'in testin ihtiyaci kadari."""

    ok: bool = True
    text: str = "bitti diyorum ama bir sey uretmedim"
    cost: float = 0.0
    iterations: int = 3
    tool_calls: int = 5
    stop_reason: str = "end_turn"
    error: str | None = None


class TestDeliverableContract:
    def test_every_agent_phase_declares_what_it_must_produce(self):
        """Sozlesmesi olmayan bir faz, sessizce bos donebilir."""
        ajan_fazlari = {
            Phase.ANALYZE, Phase.RESEARCH, Phase.ASSESS, Phase.MOCKUP,
            Phase.DESIGN, Phase.PLAN, Phase.QA, Phase.REVIEW,
            Phase.STAGING, Phase.LIVE,
        }
        assert ajan_fazlari <= set(PHASE_DELIVERABLE)

    def test_phases_without_agents_are_left_alone(self):
        """`ingest`, `implement` ve `package` kendi sonuclarini uretir."""
        for faz in (Phase.INGEST, Phase.IMPLEMENT, Phase.PACKAGE):
            assert faz not in PHASE_DELIVERABLE

    @pytest.mark.parametrize(
        ("faz", "cikti"),
        [
            (Phase.ASSESS, "bosluk-analizi.md"),
            (Phase.MOCKUP, "mockup-giris.html"),
            (Phase.DESIGN, "mimari.md"),
            (Phase.QA, "qa-raporu.md"),
        ],
    )
    def test_the_expected_artifact_satisfies_the_contract(self, orch_factory, faz, cikti):
        with orch_factory() as orch:
            assert orch._missing_deliverable(faz) is not None, "bos projede eksik olmali"
            orch.state.add_artifact(
                Artifact(name=cikti, kind="report", path=f"/tmp/{cikti}", summary="x")
            )
            assert orch._missing_deliverable(faz) is None

    def test_a_wildcard_needs_a_real_match(self, orch_factory):
        """`mockup-*.html` -- adi tutmayan bir cikti fazi tamamlamaz."""
        with orch_factory() as orch:
            orch.state.add_artifact(
                Artifact(name="mockup-notlari.md", kind="report", path="/tmp/x", summary="")
            )
            assert orch._missing_deliverable(Phase.MOCKUP) == "mockup-*.html"
            orch.state.add_artifact(
                Artifact(name="mockup-liste.html", kind="mockup", path="/tmp/y", summary="")
            )
            assert orch._missing_deliverable(Phase.MOCKUP) is None

    def test_a_recorded_plan_counts_even_without_the_report(self, orch_factory):
        """Plan fazinda asil urun gorevlerdir; rapor ikincil."""
        with orch_factory() as orch:
            assert orch._missing_deliverable(Phase.PLAN) is not None
            orch.state.add_task(Task(key="T-001", title="ilk gorev", lane="backend"))
            assert orch._missing_deliverable(Phase.PLAN) is None

    def test_the_nudge_names_the_missing_thing(self, orch_factory):
        """Ajana "bir seyler yap" demek yetmez; ne eksik oldugu yazmali."""
        with orch_factory() as orch:
            metin = orch._nudge(Phase.MOCKUP, "mockup-*.html")
        assert "mockup-*.html" in metin
        assert "save_artifact" in metin


class TestEmptyPhaseFails:
    """Uretmeden biten faz `done` degil `failed` olmali."""

    def test_a_phase_that_produced_nothing_is_not_done(self, orch_factory, monkeypatch):
        with orch_factory() as orch:
            denemeler = []

            class _SahteAjan:
                def run(self, task, context=""):
                    denemeler.append(task)
                    return _SahteSonuc()

            monkeypatch.setattr(
                "deerx.pipeline.orchestrator.build_agent", lambda *a, **k: _SahteAjan()
            )
            sonuc = orch._run_agent_phase(Phase.ASSESS)

        assert sonuc.status == Status.FAILED
        assert "bosluk-analizi.md" in (sonuc.error or "")
        # Bir kez durtulmus olmali: erken durma cogu zaman duzeltilebilir.
        assert len(denemeler) == 2
        assert "save_artifact" in denemeler[1]

    def test_the_nudge_can_rescue_the_phase(self, orch_factory, monkeypatch):
        """Durtulunce uretirse faz basarili sayilir."""
        with orch_factory() as orch:
            durum = {"tur": 0}

            class _SonradanUretenAjan:
                def run(self, task, context=""):
                    durum["tur"] += 1
                    if durum["tur"] == 2:
                        orch.state.add_artifact(
                            Artifact(
                                name="bosluk-analizi.md", kind="report",
                                path="/tmp/b.md", summary="",
                            )
                        )
                    return _SahteSonuc()

            monkeypatch.setattr(
                "deerx.pipeline.orchestrator.build_agent",
                lambda *a, **k: _SonradanUretenAjan(),
            )
            sonuc = orch._run_agent_phase(Phase.ASSESS)

        assert sonuc.status == Status.DONE
        assert durum["tur"] == 2

    def test_a_phase_that_produced_its_artifact_is_not_nudged(self, orch_factory, monkeypatch):
        """Isini yapan ajan ikinci kez cagrilmaz -- bos yere para harcanmasin."""
        with orch_factory() as orch:
            orch.state.add_artifact(
                Artifact(name="mimari.md", kind="architecture", path="/tmp/m.md", summary="")
            )
            sayac = {"n": 0}

            class _CalisanAjan:
                def run(self, task, context=""):
                    sayac["n"] += 1
                    return _SahteSonuc()

            monkeypatch.setattr(
                "deerx.pipeline.orchestrator.build_agent", lambda *a, **k: _CalisanAjan()
            )
            sonuc = orch._run_agent_phase(Phase.DESIGN)

        assert sonuc.status == Status.DONE
        assert sayac["n"] == 1
