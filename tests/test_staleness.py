"""Tamamlanmis bir fazi tekrar kosmama karari.

Kullanicinin bildirdigi hata: hedefe "butun isletim sistemlerinde calisacak
bir hesap makinesi" yazip koseyi baslatti, `ingest` ve `analyze` "zaten
tamam" diye atlandi. Calisma alaninda onceki bir projenin (saha servis
sistemi) tamamlanmis analizi duruyordu; boru hatti onu hesap makinesinin
analizi sanip ustune insa etmeye devam etti.

Iki ayri kusur vardi ve ikisi de burada kilitli.
"""

from __future__ import annotations

import pytest

from deerx.logging import EventLog
from deerx.pipeline.models import Phase, Status
from deerx.pipeline.orchestrator import Orchestrator


@pytest.fixture
def orch(settings):
    with Orchestrator(settings, events=EventLog(None, echo=False), stream=False) as o:
        yield o


def _complete(orch: Orchestrator, phase: Phase, goal: str, summary: str = "bitti") -> None:
    """Fazi verilen hedef icin tamamlanmis olarak isaretler."""
    orch.state.set_meta("goal", goal)
    orch.state.finish_phase(phase, status=Status.DONE, summary=summary)


class TestIngestIsNeverWholeSkipped:
    """`ingest` butun olarak atlanamaz.

    Isi yeni ya da degismis belgeleri fark etmek. Kendi icinde dosya dosya,
    hash uzerinden zaten atliyor -- tekrar kosmak ucuz. Butun fazi atlarsak
    kullanicinin `docs/` altina yeni biraktigi sartname hic indekslenmez ve
    ajanlar onu hic gormeden cevap verir; ustelik sessizce.
    """

    def test_new_document_is_indexed_on_a_second_run(self, orch, settings):
        docs = settings.workspace / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "birinci.md").write_text("# Birinci\nilk sartname", encoding="utf-8")

        orch.run([Phase.INGEST], goal="hedef")
        # Mutlak sayi degil ARTIS olculur: calisma alaninda indekslenen baska
        # dosyalar da olabilir, onemli olan yeni belgenin fark edilmesi.
        before = orch.kb.stats()["documents"]

        (docs / "ikinci.md").write_text("# Ikinci\nsonra eklendi", encoding="utf-8")
        report = orch.run([Phase.INGEST], goal="hedef")

        assert orch.kb.stats()["documents"] == before + 1, "yeni belge fark edilmedi"
        assert report.phases[0].status != Status.SKIPPED

    def test_unchanged_files_are_still_skipped_internally(self, orch, settings):
        """Faz kosuyor ama degismeyen dosya yeniden gomulmuyor.

        Butun fazi atlamamak, her seferinde her seyi yeniden islemek
        anlamina gelmemeli; ucuzlugun kaynagi ic hash kontrolu.
        """
        docs = settings.workspace / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "sabit.md").write_text("# Sabit\ndegismeyen", encoding="utf-8")

        orch.run([Phase.INGEST], goal="hedef")
        first = orch.kb.stats()["chunks"]
        orch.run([Phase.INGEST], goal="hedef")

        assert orch.kb.stats()["chunks"] == first, "degismeyen dosya tekrar gomuldu"

    def test_ingest_skip_reason_is_never_completion(self, orch):
        _complete(orch, Phase.INGEST, "hedef")
        assert orch._skip_reason(Phase.INGEST, force=False) is None


class TestCompletionIsBoundToItsGoal:
    """Tamamlanma, kazanildigi hedefe aittir.

    "Bu faz tamam" tek basina bir sey ifade etmiyor: hangi proje icin
    tamam? Hedef degistiginde eldeki analiz baska bir projeye ait olur.
    """

    def test_changed_goal_reruns_the_phase(self, orch):
        _complete(orch, Phase.ANALYZE, "Saha servis yonetim sistemi")
        orch.state.set_meta("goal", "Hesap makinesi")   # kullanici hedefi degistirdi
        assert orch._skip_reason(Phase.ANALYZE, force=False) is None

    def test_same_goal_still_skips(self, orch):
        _complete(orch, Phase.ANALYZE, "Hesap makinesi")
        assert orch._skip_reason(Phase.ANALYZE, force=False) is not None

    @pytest.mark.parametrize("variant", [
        "  Hesap makinesi  ",       # bastaki/sondaki bosluk
        "Hesap   makinesi",         # ic bosluk
        "hesap MAKINESI",           # buyuk/kucuk harf
        "\nHesap makinesi\n",       # satir sonu
    ])
    def test_cosmetic_goal_differences_do_not_force_a_rerun(self, orch, variant):
        """Bosluk ve harf farki yeni bir hedef degildir.

        Aksi halde kullanici hedefi yeniden yazdiginda her seferinde tum
        boru hatti bastan koser -- pahali ve sinir bozucu.
        """
        _complete(orch, Phase.ANALYZE, "Hesap makinesi")
        orch.state.set_meta("goal", variant)
        assert orch._skip_reason(Phase.ANALYZE, force=False) is not None

    def test_goal_is_recorded_when_the_phase_finishes(self, orch):
        orch.state.set_meta("goal", "Hesap makinesi")
        orch.state.finish_phase(Phase.ANALYZE, status=Status.DONE, summary="x")
        assert orch.state.phase_status(Phase.ANALYZE).goal == "Hesap makinesi"

    def test_unknown_provenance_is_treated_as_stale(self, orch):
        """Bu sutundan onceki kayitlarin hedefi bos.

        Bos bir hedef "bilinmiyor" demek; hangi proje icin yapildigini
        bilmedigimiz bir analize guvenip atlamak, kullanicinin yasadigi
        hatanin ta kendisi. Bir kez tekrar kosulur, sonra kayit duzelir.
        """
        orch.state.set_meta("goal", "Hesap makinesi")
        orch.state._conn.execute(
            "INSERT INTO phase_state (phase, status, summary, goal) VALUES (?, 'done', 'eski', '')",
            (str(Phase.ASSESS),),
        )
        orch.state._conn.commit()
        assert orch._skip_reason(Phase.ASSESS, force=False) is None

    def test_goalless_project_still_skips(self, orch):
        """Hicbir hedef verilmemisse tekrar tekrar kosmaya gerek yok."""
        orch.state.finish_phase(Phase.ANALYZE, status=Status.DONE, summary="x")
        assert orch.state.get_meta("goal", "") == ""
        assert orch._skip_reason(Phase.ANALYZE, force=False) is not None

    def test_the_run_says_why_it_reran(self, orch):
        """Sessizce tekrar kosmak da sessizce atlamak kadar kotu."""
        _complete(orch, Phase.ANALYZE, "Eski proje")
        orch.state.set_meta("goal", "Hesap makinesi")
        seen: list[str] = []
        orch.events.subscribe(lambda ev: seen.append(ev.message))
        orch._skip_reason(Phase.ANALYZE, force=False)
        assert any("hedef degismis" in m for m in seen), seen


class TestSkipInvariants:
    def test_force_never_skips(self, orch):
        _complete(orch, Phase.ANALYZE, "Hesap makinesi")
        assert orch._skip_reason(Phase.ANALYZE, force=True) is None

    def test_unfinished_phases_never_skip(self, orch):
        for status in (Status.PENDING, Status.FAILED, Status.BLOCKED, Status.CANCELLED):
            orch.state.set_meta("goal", "Hesap makinesi")
            orch.state.finish_phase(Phase.QA, status=status, summary="x")
            assert orch._skip_reason(Phase.QA, force=False) is None, status

    def test_skip_reason_tells_the_user_what_to_do(self, orch):
        """Gerekce, tekrar kosmanin yolunu da soylemeli."""
        _complete(orch, Phase.ANALYZE, "Hesap makinesi")
        reason = orch._skip_reason(Phase.ANALYZE, force=False)
        assert "yeniden" in reason.lower(), reason


class TestMigration:
    def test_existing_databases_gain_the_column(self, settings):
        """Sutun eklenmeden once acilmis bir veritabani kirilmamali."""
        import sqlite3

        db = settings.db_path
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE phase_state (phase TEXT PRIMARY KEY, status TEXT NOT NULL "
            "DEFAULT 'pending', summary TEXT NOT NULL DEFAULT '', started_at REAL, "
            "finished_at REAL, cost_usd REAL NOT NULL DEFAULT 0)"
        )
        conn.execute("INSERT INTO phase_state (phase, status) VALUES ('analyze', 'done')")
        conn.commit()
        conn.close()

        from deerx.pipeline.state import ProjectState

        state = ProjectState(db)
        try:
            assert state.phase_status(Phase.ANALYZE).goal == ""
        finally:
            state.close()
