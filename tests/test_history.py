"""Kullanicinin gecmisi: onceki projelerden cikarim.

Danisman bugune kadar yalnizca ACIK projeyi goruyordu. "Gecen seferki
gibi yapalim" diyen bir kullanici karsisinda elinde hicbir sey yoktu.
Buradaki testler o kopruyu kurar ve KOPRUNUN SINIRLARINI kilitler:
okuma salt okunurdur, goc kosturmaz, kapsami cagiran belirler.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deerx.history import UserHistory, project_db, read_only, scan_project
from deerx.pipeline.models import Artifact, Decision
from deerx.pipeline.state import ProjectState


def _proje(kok: Path, *, hedef: str = "", kararlar=(), sohbet=(), ciktilar=()) -> Path:
    """Gercek bir proje veritabani kurar ve kapatir."""
    (kok / ".deerx").mkdir(parents=True, exist_ok=True)
    db = kok / ".deerx" / "deerx.db"
    durum = ProjectState(db)
    try:
        if hedef:
            durum.set_meta("goal", hedef)
        akis = durum.workflow_for_goal(hedef or "is")
        for anahtar, baslik, secim, gerekce in kararlar:
            durum.add_decision(
                Decision(key=anahtar, title=baslik, choice=secim, rationale=gerekce)
            )
        for rol, metin in sohbet:
            durum.add_chat_message(akis["id"], role=rol, content=metin)
        for ad in ciktilar:
            durum.add_artifact(
                Artifact(name=ad, kind="report", path=str(kok / ad), summary="ozet"),
                blob=b"x",
            )
    finally:
        durum.close()
    return db


class TestSaltOkunurOkuma:
    """Capraz okumanin uc kurali: yazma yok, goc yok, proje dusurulmez."""

    def test_the_reader_cannot_write(self, tmp_path):
        db = _proje(tmp_path / "a", hedef="A projesi")
        conn = read_only(db)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO project (key, value) VALUES ('x', 'y')")
        finally:
            conn.close()

    def test_reading_does_not_migrate_the_schema(self, tmp_path):
        """EN KESKIN OLCUM. `ProjectState` uzerinden gecen bir okuyucu goc
        kosturur ve eksik sutun BELIRIR; bu test o an duser."""
        kok = tmp_path / "eski"
        (kok / ".deerx").mkdir(parents=True)
        db = kok / ".deerx" / "deerx.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE decisions (id INTEGER PRIMARY KEY, key TEXT NOT NULL"
                " UNIQUE, title TEXT NOT NULL, choice TEXT NOT NULL DEFAULT '',"
                " created_at REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO decisions (key, title, choice, created_at)"
                " VALUES ('ADR-001', 'Veritabani', 'PostgreSQL', 100.0)"
            )

        gecmis = UserHistory([("Eski", "eski", kok)])
        assert gecmis.search("PostgreSQL"), "eski semadan okunamadi"

        with sqlite3.connect(db) as conn:
            sutunlar = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
        assert "rationale" not in sutunlar, "okuma sema gocu kosturdu"

    def test_the_file_is_not_touched(self, tmp_path):
        """Boyut ve degistirilme zamani ayni kalmali (`-wal`/`-shm` haric:
        okuyucunun paylasilan bellege yazmasi mesru)."""
        db = _proje(tmp_path / "a", hedef="A", kararlar=[("ADR-1", "X", "Y", "Z")])
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        once = (db.stat().st_size, db.stat().st_mtime_ns)

        UserHistory([("A", "a", tmp_path / "a")]).summary()
        assert (db.stat().st_size, db.stat().st_mtime_ns) == once

    def test_a_corrupt_project_is_named_not_dropped(self, tmp_path):
        """Sessizce atlamak, eksik bir gecmisle konusuldugunu kullanicidan
        gizlemek olurdu."""
        kok = tmp_path / "bozuk"
        (kok / ".deerx").mkdir(parents=True)
        (kok / ".deerx" / "deerx.db").write_bytes(b"bu bir sqlite dosyasi degil")

        gecmis = UserHistory([("Bozuk", "bozuk", kok)])
        assert gecmis.unreadable() == ["Bozuk"]
        assert gecmis.search("herhangi") == []

    def test_a_project_without_a_database_is_empty_not_an_error(self, tmp_path):
        kok = tmp_path / "hic-kosulmamis"
        kok.mkdir()
        gecmis = UserHistory([("Bos", "bos", kok)])
        assert [p.status for p in gecmis.projects()] == ["empty"]
        assert gecmis.unreadable() == []

    def test_the_legacy_directory_is_read_not_moved(self, tmp_path):
        """`.praxis` -> `.deerx` tasimasi ayar YAZAN bir yolda kosuyor;
        okuma oraya girmez."""
        kok = tmp_path / "eski-ad"
        (kok / ".praxis").mkdir(parents=True)
        with sqlite3.connect(kok / ".praxis" / "praxis.db") as conn:
            conn.execute("CREATE TABLE project (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO project VALUES ('goal', 'Eski hedef')")

        assert project_db(kok) == kok / ".praxis" / "praxis.db"
        assert not (kok / ".deerx").exists(), "okuma dizini tasidi"


class TestKapsamCagirandanGelir:
    def test_only_the_given_projects_are_read(self, tmp_path):
        """Yetki burada ikinci kez tanimlanmaz: verilmeyen proje okunmaz."""
        _proje(tmp_path / "benim", hedef="Benim", kararlar=[("ADR-1", "Kuyruk", "Redis", "")])
        _proje(tmp_path / "baskasinin", hedef="Gizli", kararlar=[("ADR-9", "Gizli", "Sir", "")])

        gecmis = UserHistory([("Benim", "benim", tmp_path / "benim")])
        assert gecmis.search("Redis")
        assert not gecmis.search("Sir"), "verilmeyen proje okundu"

    def test_the_current_project_is_left_out(self, tmp_path):
        """Acik projenin durumu ve sohbeti danismana zaten TAM haliyle
        gidiyor; ikinci kez kirpilmis olarak koymak baglami sisirirdi."""
        _proje(tmp_path / "acik", hedef="Acik", kararlar=[("ADR-1", "Onbellek", "Redis", "")])
        _proje(tmp_path / "oteki", hedef="Oteki", kararlar=[("ADR-2", "Kuyruk", "Kafka", "")])

        gecmis = UserHistory(
            [("Acik", "acik", tmp_path / "acik"), ("Oteki", "oteki", tmp_path / "oteki")],
            simdiki_slug="acik",
        )
        assert [p.name for p in gecmis.projects()] == ["Oteki"]
        assert not gecmis.search("Onbellek")
        assert gecmis.search("Kafka")


class TestArama:
    def test_a_decision_is_found_by_its_content(self, tmp_path):
        _proje(
            tmp_path / "a", hedef="Saha servis",
            kararlar=[("ADR-014", "Veritabani secimi", "PostgreSQL 18",
                       "Coklu kiracı icin satir duzeyi guvenlik")],
        )
        bulunan = UserHistory([("Saha", "saha", tmp_path / "a")]).search("PostgreSQL")
        assert bulunan
        assert bulunan[0].tur == "decision"
        assert "ADR-014" in bulunan[0].title
        assert "PostgreSQL 18" in bulunan[0].body

    def test_a_chat_line_is_found_and_carries_its_workflow(self, tmp_path):
        """Bir cumlenin HANGI isin ortasinda soylendigini bilmeden eski bir
        konusma yaniltici olur."""
        _proje(
            tmp_path / "a", hedef="Mobil uygulama",
            sohbet=[("user", "Bildirimler icin Firebase kullanalim mi?")],
        )
        bulunan = UserHistory([("Mobil", "mobil", tmp_path / "a")]).search("Firebase")
        assert bulunan and bulunan[0].tur == "chat"
        assert bulunan[0].workflow, "sohbet satiri hangi ise ait, yazmiyor"

    def test_decisions_outrank_chatter(self, tmp_path):
        """Kayda gecmis bir karar, laf arasinda gecen bir cumleden agir
        basmali: "gecmiste ne yaptik" sorusunun cevabi once kararlardir."""
        _proje(
            tmp_path / "a", hedef="X",
            kararlar=[("ADR-3", "Kuyruk", "RabbitMQ", "")],
            sohbet=[("user", "RabbitMQ mu olsa acaba")],
        )
        bulunan = UserHistory([("X", "x", tmp_path / "a")]).search("RabbitMQ")
        assert bulunan[0].tur == "decision"

    def test_a_kind_filter_narrows_the_search(self, tmp_path):
        _proje(
            tmp_path / "a", hedef="X",
            kararlar=[("ADR-3", "Kuyruk", "RabbitMQ", "")],
            sohbet=[("user", "RabbitMQ mu olsa")],
        )
        gecmis = UserHistory([("X", "x", tmp_path / "a")])
        assert all(k.tur == "chat" for k in gecmis.search("RabbitMQ", tur="chat"))

    def test_an_empty_query_finds_nothing(self, tmp_path):
        """Bos sorgu "her sey" demek DEGIL: butun gecmisi dokmek, modelin
        istemedigi yuz ekrani baglama koymak olurdu."""
        _proje(tmp_path / "a", hedef="X", kararlar=[("ADR-1", "Y", "Z", "")])
        assert UserHistory([("X", "x", tmp_path / "a")]).search("") == []
        assert UserHistory([("X", "x", tmp_path / "a")]).search("ve bir") == []


class TestOzet:
    def test_the_summary_names_projects_and_decisions(self, tmp_path):
        _proje(
            tmp_path / "a", hedef="Saha servis yonetimi",
            kararlar=[("ADR-014", "Veritabani", "PostgreSQL 18", "Satir duzeyi guvenlik")],
            sohbet=[("user", "merhaba")],
        )
        ozet = UserHistory([("Saha", "saha", tmp_path / "a")]).summary()
        assert "Saha" in ozet
        assert "Saha servis yonetimi" in ozet
        assert "ADR-014" in ozet
        assert "PostgreSQL 18" in ozet
        assert "sohbet satiri" in ozet, "derinlige giden yol soylenmiyor"

    def test_the_summary_is_bounded(self, tmp_path):
        """Gecmisin TAMAMINI her mesaja koymak, asil sorunun uzerine yuz
        ekran eski konusma yigmak olurdu."""
        projeler = []
        for i in range(20):
            kok = tmp_path / f"p{i}"
            _proje(
                kok, hedef=f"Proje {i}",
                kararlar=[(f"ADR-{j}", f"Karar {j}", "secim", "x" * 500) for j in range(20)],
            )
            projeler.append((f"Proje {i}", f"p{i}", kok))

        ozet = UserHistory(projeler).summary()
        assert ozet.count("### ") <= 8, "ozet proje sayisinda sinirsiz"
        assert len(ozet) < 12000, f"ozet cok uzun: {len(ozet)}"

    def test_an_empty_history_produces_no_block(self, tmp_path):
        """Hicbir gecmis yokken bosluk basmak, modele anlamsiz bir baslik
        gostermek olurdu."""
        kok = tmp_path / "bos"
        kok.mkdir()
        gecmis = UserHistory([("Bos", "bos", kok)])
        assert gecmis.summary() == ""
        assert gecmis.is_empty()


class TestTekSeferOkur:
    def test_the_scan_happens_once(self, tmp_path, monkeypatch):
        """Uzun bir sohbette her tur N dosya acmak bosa maliyet."""
        _proje(tmp_path / "a", hedef="X", kararlar=[("ADR-1", "Y", "Z", "")])
        import deerx.history as modul

        sayac = []
        gercek = modul.scan_project

        def sayan(db, okuyucu):
            sayac.append(db)
            return gercek(db, okuyucu)

        monkeypatch.setattr(modul, "scan_project", sayan)
        gecmis = UserHistory([("X", "x", tmp_path / "a")])
        gecmis.summary()
        gecmis.search("Y")
        gecmis.projects()
        assert len(sayac) == 1, f"{len(sayac)} kez tarandi"


class TestAraclar:
    """Danismanin gecmise ulasan iki araci."""

    def test_the_tools_refuse_when_there_is_no_history(self, ctx, registry):
        """Sessizce bos donmek, modelin "gecmiste hicbir sey yok" diye
        YANLIS bir cikarim yapmasina yol acardi. Dogru cevap "bu baglamda
        gecmise bakamiyorum"."""
        assert ctx.history is None
        for ad, arg in (("search_history", {"query": "x"}), ("list_project_history", {})):
            sonuc = registry.execute(ad, arg, ctx)
            assert sonuc.is_error, ad
            assert "gecmis" in sonuc.content.lower() or "sohbet" in sonuc.content.lower()

    def test_search_reports_what_it_found(self, ctx, registry, tmp_path):
        _proje(
            tmp_path / "a", hedef="Saha servis",
            kararlar=[("ADR-014", "Veritabani", "PostgreSQL 18", "cok kiracili")],
        )
        ctx.history = UserHistory([("Saha", "saha", tmp_path / "a")])
        sonuc = registry.execute("search_history", {"query": "PostgreSQL"}, ctx)
        assert not sonuc.is_error, sonuc.content
        assert "Saha" in sonuc.content
        assert "ADR-014" in sonuc.content

    def test_search_distinguishes_nothing_found_from_could_not_look(
        self, ctx, registry, tmp_path
    ):
        """Ikisi ayri seyler ve ikincisinde model kesin konusmamali."""
        kok = tmp_path / "bozuk"
        (kok / ".deerx").mkdir(parents=True)
        (kok / ".deerx" / "deerx.db").write_bytes(b"sqlite degil")
        ctx.history = UserHistory([("Bozuk", "bozuk", kok)])

        sonuc = registry.execute("search_history", {"query": "redis"}, ctx)
        assert not sonuc.is_error
        assert "Bozuk" in sonuc.content, "okunamayan proje soylenmiyor"

    def test_listing_names_the_projects(self, ctx, registry, tmp_path):
        _proje(tmp_path / "a", hedef="Birinci", kararlar=[("ADR-1", "X", "Y", "")])
        _proje(tmp_path / "b", hedef="Ikinci", sohbet=[("user", "selam")])
        ctx.history = UserHistory(
            [("Bir", "bir", tmp_path / "a"), ("Iki", "iki", tmp_path / "b")]
        )
        sonuc = registry.execute("list_project_history", {}, ctx)
        assert not sonuc.is_error
        assert "Bir" in sonuc.content and "Iki" in sonuc.content
        assert "Birinci" in sonuc.content

    def test_an_unknown_kind_is_refused(self, ctx, registry, tmp_path):
        kok = tmp_path / "a"
        _proje(kok, hedef="X")
        ctx.history = UserHistory([("X", "x", kok)])
        sonuc = registry.execute(
            "search_history", {"query": "x", "type": "sifre"}, ctx
        )
        assert sonuc.is_error

    def test_only_the_advisor_gets_them(self):
        """Faz ajanlari kendi fazinin isini yapar; baska projelerin
        sohbetini okumak onlarin kapsami degil ve her faza acmak her kosuya
        N dosya okuma maliyeti bindirirdi."""
        from deerx.tools import TOOLSETS

        for rol, araclar in TOOLSETS.items():
            gecmis_var = "search_history" in araclar
            assert gecmis_var == (rol == "danisman"), rol


class TestScanProjectHataYutmaz:
    def test_a_reader_error_is_classified_not_raised(self, tmp_path):
        db = _proje(tmp_path / "a", hedef="X")

        def patlayan(conn):
            raise sqlite3.DatabaseError("bozuk")

        sonuc, durum = scan_project(db, patlayan)
        assert (sonuc, durum) == (None, "unreadable")

    def test_the_connection_is_closed_even_on_error(self, tmp_path):
        """Yarida kalan bir okuma dosyayi kilitli birakmamali."""
        db = _proje(tmp_path / "a", hedef="X")
        tutulan = []

        def yakalayan(conn):
            tutulan.append(conn)
            raise sqlite3.DatabaseError("bozuk")

        scan_project(db, yakalayan)
        with pytest.raises(sqlite3.ProgrammingError):
            tutulan[0].execute("SELECT 1")
