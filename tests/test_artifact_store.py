"""Blob deposu: cikti baytlari veritabaninda.

Ciktilar diskte `.deerx/artifacts/` altinda duruyordu ve kayit yalnizca
yolu tasiyordu. Tasinmis, baska makineden acilmis ya da `.praxis`ten
`.deerx`e gecmis bir projede yol bos cikiyordu: cikti listede var,
indirilemiyor. Baytlar artik `artifact_blobs` tablosunda; disk yalnizca
yedek okuma yolu (blobsuz eski kayitlar ve blob vermeden kaydeden araclar).

Testler uc soruyu sorar: yazma yarim kalabilir mi, okuma sirasi her
yardimcida ayni mi (DB -> disk -> yok), eski bir veritabani acilista
kendini tamamlar mi ve ikinci acilis bosuna is yapar mi.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import sqlite3
import tracemalloc
import zipfile
from pathlib import Path

import pytest

from deerx.errors import ToolError
from deerx.pipeline import state as state_mod
from deerx.pipeline.models import Artifact
from deerx.pipeline.state import ProjectState

MB = 1024 * 1024


def _ham(db: Path, *sql: str):
    """Ham sqlite ile calistirir ve KAPATIR: Windows'ta acik kalan baglanti
    dosyayi kilitler, tmp_path temizligi duser."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        sonuc = None
        for ifade in sql:
            sonuc = conn.execute(ifade).fetchall()
        conn.commit()
        return sonuc
    finally:
        conn.close()


def _cikti(name: str, yol: Path) -> Artifact:
    return Artifact(name=name, kind="report", path=str(yol))


class _Casus:
    """Havuz baglantisini sarar: SQL metinlerini ve blob yazmalarini kaydeder.

    `sqlite3.Connection` degistirilemez bir C tipi; `blobopen`/`execute`
    dogrudan yamalanamaz. Bu yuzden `ProjectState._conn` ozelligi sarilir.
    """

    def __init__(self, conn, sql: list[str], yazmalar: list[int]) -> None:
        self._c = conn
        self._sql = sql
        self._yazmalar = yazmalar

    def execute(self, sql, *a, **k):
        self._sql.append(sql)
        return self._c.execute(sql, *a, **k)

    def blobopen(self, *a, **k):
        blob = self._c.blobopen(*a, **k)
        yazmalar = self._yazmalar

        class _Blob:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                blob.close()
                return False

            def write(self, veri):
                yazmalar.append(len(veri))
                return blob.write(veri)

            def __getattr__(self, ad):
                return getattr(blob, ad)

        return _Blob()

    def __getattr__(self, ad):
        return getattr(self._c, ad)


def _casusla(monkeypatch) -> tuple[list[str], list[int]]:
    sql: list[str] = []
    yazmalar: list[int] = []
    ozgun = ProjectState._conn
    monkeypatch.setattr(
        ProjectState, "_conn",
        property(lambda self: _Casus(ozgun.fget(self), sql, yazmalar)),
    )
    return sql, yazmalar


class TestBlobYazma:
    def test_add_artifact_with_blob_stores_bytes_and_sha256(self, tmp_path):
        """Tek yazma girisi `add_artifact(..., blob=)`: baytlar, boyut ve
        saglama ayni satirda. Saglama yazma aninda hesaplanir ki sonradan
        `--verify` ile bozulma yakalanabilsin."""
        durum = ProjectState(tmp_path / "d.db")
        icerik = b"# Rapor\n" * 100
        a = durum.add_artifact(_cikti("rapor.md", tmp_path / "rapor.md"), blob=icerik)

        satir = _ham(
            tmp_path / "d.db",
            "SELECT bytes, sha256, length(data) AS n FROM artifact_blobs "
            f"WHERE artifact_id = {a.id}",
        )[0]
        assert satir["bytes"] == len(icerik) == satir["n"]
        assert satir["sha256"] == hashlib.sha256(icerik).hexdigest()

        bilgi = durum.artifact_info("rapor.md")
        assert bilgi.stored and bilgi.bytes == len(icerik)
        assert bilgi.blob_state == "stored"
        assert bilgi.sha256 == satir["sha256"]
        durum.close()

    def test_a_large_source_is_written_in_chunks(self, tmp_path, monkeypatch):
        """250 MB'lik bir paketi `bytes` olarak okuyup INSERT etmek 250 MB RAM
        demekti. Kopya `blobopen` ile parca parca akmali: hicbir yazma
        BLOB_PARCA'dan buyuk degil, sayisi tam parca sayisi ve tepe bellek
        dosyanin cok altinda."""
        kaynak = tmp_path / "paket.zip"
        boyut = 40 * MB
        with kaynak.open("wb") as fp:
            for _ in range(boyut // MB):
                fp.write(b"\x5a" * MB)
        durum = ProjectState(tmp_path / "d.db")
        _, yazmalar = _casusla(monkeypatch)

        tracemalloc.start()
        try:
            a = durum.add_artifact(_cikti("paket.zip", kaynak), blob=kaynak)
            _, tepe = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert yazmalar, "blobopen uzerinden hic yazma olmadi"
        assert max(yazmalar) <= state_mod.BLOB_PARCA
        assert len(yazmalar) == math.ceil(boyut / state_mod.BLOB_PARCA)
        assert sum(yazmalar) == boyut
        assert tepe < 16 * MB, f"tepe bellek {tepe / MB:.1f} MB"
        assert durum.artifact_size(a) == boyut
        durum.close()

    def test_a_failed_copy_leaves_no_half_row(self, tmp_path, monkeypatch):
        """Kopya ikinci parcada duserse ne yarim blob ne de blobsuz kayit
        kalmali: kayit ve kopya TEK islemdir. Aksi halde liste "var" der,
        indirme 404 verir ve kimse sebebini anlamaz."""
        monkeypatch.setattr(state_mod, "BLOB_PARCA", 8)
        kaynak = tmp_path / "kirik.bin"
        kaynak.write_bytes(b"\x01" * 24)
        durum = ProjectState(tmp_path / "d.db")

        ozgun_open = Path.open

        def sahte_open(self, *a, **k):
            fp = ozgun_open(self, *a, **k)
            if self.name != "kirik.bin":
                return fp

            class _Kirik:
                sayac = 0

                def read(self, n=-1):
                    self.sayac += 1
                    if self.sayac >= 2:
                        raise OSError("disk unplugged")
                    return fp.read(n)

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    fp.close()
                    return False

            return _Kirik()

        monkeypatch.setattr(Path, "open", sahte_open)
        with pytest.raises(OSError):
            durum.add_artifact(_cikti("kirik.bin", kaynak), blob=kaynak)
        monkeypatch.undo()

        db = tmp_path / "d.db"
        assert _ham(db, "SELECT COUNT(*) AS n FROM artifact_blobs")[0]["n"] == 0
        assert _ham(db, "SELECT COUNT(*) AS n FROM artifacts WHERE name = 'kirik.bin'")[0]["n"] == 0
        # Islem geri alindi, durum hala yazilabilir.
        durum.add_artifact(_cikti("saglam.bin", kaynak), blob=b"ok")
        assert durum.artifact_bytes(Artifact(name="saglam.bin", kind="report", path="")) == b"ok"
        durum.close()

    def test_rewriting_the_same_name_replaces_the_blob(self, tmp_path):
        """UPSERT id'yi korur; blob o id'ye baglidir ve CASCADE hic
        tetiklenmez. Yenileme ACIKCA yapilmali: yoksa yeni kosunun ciktisi
        eski kosunun baytlariyla inerdi."""
        durum = ProjectState(tmp_path / "d.db")
        yol = tmp_path / "plan.md"
        ilk = durum.add_artifact(_cikti("plan.md", yol), blob=b"eski plan")
        son = durum.add_artifact(_cikti("plan.md", yol), blob=b"yeni ve daha uzun plan")

        assert son.id == ilk.id
        assert durum.artifact_bytes(son) == b"yeni ve daha uzun plan"
        satirlar = _ham(tmp_path / "d.db", "SELECT artifact_id, sha256 FROM artifact_blobs")
        assert len(satirlar) == 1
        assert satirlar[0]["sha256"] == hashlib.sha256(b"yeni ve daha uzun plan").hexdigest()
        durum.close()

    def test_rewriting_without_a_blob_drops_the_old_blob(self, tmp_path):
        """Blobsuz yeniden kayit, diskteki dosyayi yeni gercek ilan eder:
        eski kopya dusmeli ve `blob_state` '' kalmali ki acilis onu diskten
        yeniden alsin. Bayat blob kalsaydi indirme eski icerigi verirdi."""
        durum = ProjectState(tmp_path / "d.db")
        yol = tmp_path / "rapor.md"
        yol.write_bytes(b"diskteki yeni icerik")
        durum.add_artifact(_cikti("rapor.md", yol), blob=b"eski kopya")
        durum.add_artifact(_cikti("rapor.md", yol))

        bilgi = durum.artifact_info("rapor.md")
        assert not bilgi.stored
        assert bilgi.blob_state == ""
        assert bilgi.on_disk is True
        assert _ham(tmp_path / "d.db", "SELECT COUNT(*) AS n FROM artifact_blobs")[0]["n"] == 0
        assert durum.artifact_bytes(bilgi) == b"diskteki yeni icerik"
        durum.close()

    def test_oversized_sources_are_refused(self, tmp_path, monkeypatch):
        """Sinirin ustu blob'a girmez ve kayit da yazilmaz: hata ToolError'dir
        (ajan dongusu onu modele dondurur, cokmez) ve adi soyler."""
        monkeypatch.setattr(state_mod, "ARTIFACT_MAX_BYTES", 1024)
        durum = ProjectState(tmp_path / "d.db")
        buyuk = tmp_path / "buyuk.bin"
        buyuk.write_bytes(b"\x00" * 2048)

        with pytest.raises(ToolError, match="buyuk.bin"):
            durum.add_artifact(_cikti("buyuk.bin", buyuk), blob=buyuk)
        with pytest.raises(ToolError, match="bellek.bin"):
            durum.add_artifact(_cikti("bellek.bin", buyuk), blob=b"\x00" * 2048)

        assert durum.list_artifacts() == []
        # Sinirin altindaki ayni yoldan yaziliyor: sinir tek sebep.
        durum.add_artifact(_cikti("kucuk.bin", buyuk), blob=b"\x00" * 1024)
        assert durum.artifact_info("kucuk.bin").stored
        durum.close()


class TestBlobOkuma:
    def test_db_first_then_disk_then_none(self, tmp_path):
        """Okuma sirasi HER yardimcida ayni: once veritabani, sonra disk,
        sonra yok. Blobsuz eski kayitlar (ve blob vermeyen araclar) diskten
        sunulmaya devam etmeli; ikisi de yoksa None -- 404'un kaynagi."""
        durum = ProjectState(tmp_path / "d.db")
        diskte = tmp_path / "ikisi.md"
        diskte.write_bytes(b"diskteki")
        ikisi = durum.add_artifact(_cikti("ikisi.md", diskte), blob=b"veritabanindaki")
        yalniz_disk = durum.add_artifact(_cikti("disk.md", diskte))
        hicbiri = durum.add_artifact(_cikti("yok.md", tmp_path / "silinmis.md"))

        assert durum.artifact_bytes(ikisi) == b"veritabanindaki"
        assert durum.artifact_size(ikisi) == len(b"veritabanindaki")
        with durum.open_artifact(ikisi) as fp:
            assert fp.read() == b"veritabanindaki"

        assert durum.artifact_bytes(yalniz_disk) == b"diskteki"
        assert durum.artifact_size(yalniz_disk) == len(b"diskteki")
        fp = durum.open_artifact(yalniz_disk)
        assert fp.read() == b"diskteki"
        fp.close()

        assert durum.artifact_bytes(hicbiri) is None
        assert durum.artifact_size(hicbiri) is None
        assert durum.open_artifact(hicbiri) is None
        assert list(durum.iter_artifact_bytes(hicbiri)) == []

        bilgiler = {b.name: b for b in durum.list_artifact_infos()}
        assert (bilgiler["ikisi.md"].stored, bilgiler["ikisi.md"].on_disk) == (True, True)
        assert (bilgiler["disk.md"].stored, bilgiler["disk.md"].on_disk) == (False, True)
        assert (bilgiler["yok.md"].stored, bilgiler["yok.md"].on_disk) == (False, False)
        durum.close()

    def test_list_never_selects_the_blob_column(self, tmp_path, monkeypatch):
        """Liste ve meta sorgulari `data` sutununa HIC dokunmamali: yuzlerce
        ciktiyi listelerken megabaytlarca blob tasimak liste istegini
        dakikalara cikarirdi. `SELECT *` yalnizca `artifacts` uzerinde."""
        durum = ProjectState(tmp_path / "d.db")
        a = durum.add_artifact(_cikti("a.md", tmp_path / "a.md"), blob=b"a" * 1000)
        durum.add_artifact(_cikti("b.md", tmp_path / "b.md"), blob=b"b" * 1000)
        sql, _ = _casusla(monkeypatch)

        durum.list_artifact_infos()
        durum.artifact_info("a.md")
        durum.list_artifacts()
        durum.artifact_size(a)
        durum.artifact_blob_sizes()

        assert len(sql) >= 5
        dokunanlar = [s for s in sql if re.search(r"\bdata\b", s, re.I)]
        assert not dokunanlar, dokunanlar
        durum.close()

    def test_streaming_survives_state_close(self, tmp_path):
        """Akitilan yanit `ProjectState.close()`tan sonra da akmali: uretec
        havuz baglantisina degil kendi baglantisina dayanir. Havuza bagli
        olsaydi ilk parcadan sonra kapanan durum akisi ortasindan keserdi."""
        durum = ProjectState(tmp_path / "d.db")
        icerik = bytes(range(20))
        a = durum.add_artifact(_cikti("akis.bin", tmp_path / "akis.bin"), blob=icerik)

        uretec = durum.iter_artifact_bytes(a, chunk=8)
        ilk = next(uretec)
        durum.close()
        kalan = b"".join(uretec)

        assert ilk == icerik[:8]
        assert ilk + kalan == icerik
        # Kapanmis durumdan sonra dosya kilitli kalmadi: yeniden acilabiliyor.
        yeniden = ProjectState(tmp_path / "d.db")
        assert yeniden.artifact_bytes(a) == icerik
        yeniden.close()

    def test_a_relative_db_path_still_streams(self, tmp_path, monkeypatch):
        """Akitma baglantisi `file:` URI ile acilir ve `Path.as_uri` goreli
        yolu reddeder: goreli `db_path` ile kurulan durum yazabiliyor ama ilk
        `open_artifact`ta "relative path can't be expressed as a file URI"
        veriyordu (OLCULDU). Hata kurucuda degil okumada patladigi icin
        kimse fark etmezdi; yol URI'ye cevrilmeden mutlaklanmali."""
        monkeypatch.chdir(tmp_path)
        durum = ProjectState(Path("goreli") / "d.db")
        a = durum.add_artifact(_cikti("r.md", tmp_path / "r.md"), blob=b"# goreli\n")

        with durum.open_artifact(a) as fp:
            assert fp.read() == b"# goreli\n"
        assert b"".join(durum.iter_artifact_bytes(a, chunk=4)) == b"# goreli\n"
        assert durum.db_path == Path("goreli") / "d.db"
        durum.close()

    def test_open_artifact_serves_a_zip_to_zipfile(self, tmp_path):
        """`read_manifest` blob'u dogrudan `zipfile`a verir. OLCULDU:
        `sqlite3.Blob` read/seek/tell saglar ama `seekable` yok;
        `namelist()` calisirken `read()` AttributeError veriyordu. Sarmal
        konumlanabilir bir dosya gibi davranmali."""
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("proje/MANIFEST.md", "# Icerik\n")
            zf.writestr("proje/dolgu.txt", "x" * 5000)
        durum = ProjectState(tmp_path / "d.db")
        a = durum.add_artifact(
            _cikti("paket.zip", tmp_path / "paket.zip"), blob=tampon.getvalue()
        )

        fp = durum.open_artifact(a)
        assert fp.seekable() and fp.readable()
        with zipfile.ZipFile(fp) as zf:
            assert zf.read("proje/MANIFEST.md") == b"# Icerik\n"
            assert len(zf.read("proje/dolgu.txt")) == 5000
        fp.close()
        durum.close()

    def test_journal_size_limit_is_set(self, tmp_path):
        """OLCULDU: 250 MB'lik bir blob'dan sonra -wal dosyasi 250 MB olarak
        kaliciydi; checkpoint icerigi tasir ama dosyayi kucultmez. Sinir
        olmadan her buyuk paket diskte ikinci bir kopya birakirdi."""
        durum = ProjectState(tmp_path / "d.db")
        assert durum._conn.execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * MB
        durum.close()

    def test_verify_detects_corruption(self, tmp_path, caplog):
        """Saglama yazma aninda alinir ki bozulma sonradan yakalanabilsin:
        tek bayti degisen bir blob `verify_artifact`ta False vermeli ve
        gunlukte adiyla gorunmeli."""
        durum = ProjectState(tmp_path / "d.db")
        icerik = b"\x01" + b"saglam icerik" * 10
        durum.add_artifact(_cikti("v.bin", tmp_path / "v.bin"), blob=icerik)
        assert durum.verify_artifact("v.bin") is True
        assert durum.verify_artifact("olmayan.bin") is False

        _ham(
            tmp_path / "d.db",
            "UPDATE artifact_blobs SET data = CAST(X'00' || substr(data, 2) AS BLOB)",
        )
        with caplog.at_level("WARNING", logger="deerx.state"):
            assert durum.verify_artifact("v.bin") is False
        assert "v.bin" in caplog.text and "sha256" in caplog.text
        durum.close()


class TestGeriDoldurma:
    def _uc_cikti(self, tmp_path: Path) -> tuple[Path, Path]:
        """Iki dosya + bir silinmis; hepsi blobsuz kayit (eski veritabani)."""
        db = tmp_path / ".deerx" / "deerx.db"
        dizin = tmp_path / ".deerx" / "artifacts"
        dizin.mkdir(parents=True)
        durum = ProjectState(db)
        for ad in ("a.md", "b.md", "silinmis.md"):
            yol = dizin / ad
            yol.write_bytes(f"# {ad}\n".encode())
            durum.add_artifact(_cikti(ad, yol))
        durum.close()
        (dizin / "silinmis.md").unlink()
        return db, dizin

    def test_an_old_database_gains_blobs_on_open(self, tmp_path):
        """Blob tablosundan onceki bir veritabani acilista kendini
        tamamlamali: diskteki dosyalar 'stored', kaybolan 'missing' olur
        ama listede kalir. Kullanicinin elle bir gocu kosturmasi beklenmez."""
        db, dizin = self._uc_cikti(tmp_path)
        # Bugunun semasi kurulup tablo DUSURULEREK dune donulur: elle yazilan
        # bir "eski" sema gercek eskiden sessizce ayrilirdi.
        _ham(db, "DROP TABLE artifact_blobs", "UPDATE artifacts SET blob_state = ''")

        durum = ProjectState(db)
        bilgi = {b.name: b for b in durum.list_artifact_infos()}
        assert (bilgi["a.md"].stored, bilgi["a.md"].blob_state) == (True, "stored")
        assert (bilgi["b.md"].stored, bilgi["b.md"].blob_state) == (True, "stored")
        assert (bilgi["silinmis.md"].stored, bilgi["silinmis.md"].blob_state) == (False, "missing")
        assert len(durum.list_artifacts()) == 3
        assert durum.artifact_bytes(bilgi["a.md"]) == b"# a.md\n"
        assert durum.artifact_bytes(bilgi["silinmis.md"]) is None
        durum.close()

    def test_the_second_open_does_not_touch_the_file(self, tmp_path, monkeypatch):
        """Ikinci acilis sifir stat, sifir yazma: bakilmis satirlar
        ('stored'/'missing') sorguya girmez. Her aciliste diski taramak,
        yuzlerce ciktili bir projede sunucunun acilisini uzatirdi ve
        `.db` dosyasinin degismesi her acilista yedegi eskitirdi."""
        db, _ = self._uc_cikti(tmp_path)
        ProjectState(db).close()  # geri doldurma burada kosar
        _ham(db, "PRAGMA wal_checkpoint(TRUNCATE)")
        once = db.stat()

        sayac = {"n": 0}
        ozgun = Path.is_file

        def sayan(self):
            sayac["n"] += 1
            return ozgun(self)

        monkeypatch.setattr(Path, "is_file", sayan)
        durum = ProjectState(db)
        # Kopyalar ilk acilistan kaliyor; `check_disk=False` sayaci kirletmesin.
        assert durum.artifact_info("a.md", check_disk=False).blob_state == "stored"
        assert durum.artifact_info("silinmis.md", check_disk=False).blob_state == "missing"
        durum.close()

        assert sayac["n"] == 0
        sonra = db.stat()
        assert (sonra.st_size, sonra.st_mtime_ns) == (once.st_size, once.st_mtime_ns)

    def test_backfill_is_per_row_transactional(self, tmp_path, monkeypatch, caplog):
        """Bir dosyanin kopyasi duserse yalnizca o satir '' kalir; onceki
        satir durur, acilis cokmez ve bir sonraki acilis eksigi tamamlar.
        Tek buyuk islem olsaydi bir bozuk dosya butun projeyi blobsuz
        birakirdi."""
        db, _ = self._uc_cikti(tmp_path)
        kirik = {"b.md"}
        ozgun_open = Path.open

        def sahte_open(self, *a, **k):
            if self.name in kirik:
                raise OSError("read error")
            return ozgun_open(self, *a, **k)

        monkeypatch.setattr(Path, "open", sahte_open)
        with caplog.at_level("WARNING", logger="deerx.state"):
            durum = ProjectState(db)
        bilgi = {b.name: b for b in durum.list_artifact_infos()}
        assert bilgi["a.md"].blob_state == "stored"
        assert (bilgi["b.md"].stored, bilgi["b.md"].blob_state) == (False, "")
        assert "b.md" in caplog.text
        durum.close()

        kirik.clear()
        durum = ProjectState(db)
        assert durum.artifact_info("b.md").blob_state == "stored"
        assert durum.artifact_bytes(durum.artifact_info("b.md")) == b"# b.md\n"
        durum.close()

    def test_a_source_that_grows_after_stat_does_not_crash_the_open(
        self, tmp_path, monkeypatch, caplog
    ):
        """zeroblob stat'in soyledigi kadar yer acar; dosya stat ile kopya
        arasinda buyurse EOF'a kadar okumak `sqlite3.Blob.write`i ValueError
        ile dusururdu ve o tur hicbir yerde yakalanmiyordu -- proje
        acilamiyordu (OLCULDU). Okuma boyutla sinirli: satir '' kalir, uyari
        loglanir, yarim blob kalmaz ve sonraki acilis buyumus dosyayi alir."""
        db, _ = self._uc_cikti(tmp_path)
        buyuyen = {"b.md"}
        ek = b"# kopya sirasinda eklenen satir\n"
        ozgun_open = Path.open

        def buyuten_open(self, *a, **k):
            # stat alindi, kopya icin aciliyor: tam o anda dosya buyuyor.
            if self.name in buyuyen:
                with ozgun_open(self, "ab") as fp:
                    fp.write(ek)
            return ozgun_open(self, *a, **k)

        monkeypatch.setattr(Path, "open", buyuten_open)
        with caplog.at_level("WARNING", logger="deerx.state"):
            durum = ProjectState(db)
        bilgi = {b.name: b for b in durum.list_artifact_infos()}
        assert bilgi["a.md"].blob_state == "stored"
        assert (bilgi["b.md"].stored, bilgi["b.md"].blob_state) == (False, "")
        # Sinirli okuma + yoklama yakaladi; ValueError'a hic gelinmedi.
        assert "b.md" in caplog.text and "source grew" in caplog.text
        assert _ham(db, "SELECT COUNT(*) AS n FROM artifact_blobs")[0]["n"] == 1
        durum.close()

        buyuyen.clear()
        durum = ProjectState(db)
        bilgi = durum.artifact_info("b.md")
        assert (bilgi.blob_state, bilgi.bytes) == ("stored", len(b"# b.md\n" + ek))
        assert durum.artifact_bytes(bilgi) == b"# b.md\n" + ek
        durum.close()

    def test_large_files_are_deferred_not_copied_on_open(self, tmp_path, monkeypatch):
        """Acilis dakikalarca kopya yapmamali: esigin ustu 'deferred' kalir
        ve yalnizca acik komut (`deerx artifacts --backfill`) alir. Kucuk
        olan ayni aciliste 'stored' olur -- esik tek fark."""
        monkeypatch.setattr(state_mod, "BACKFILL_AUTO_MAX", 1024)
        db = tmp_path / "d.db"
        durum = ProjectState(db)
        buyuk = tmp_path / "buyuk.bin"
        buyuk.write_bytes(b"\x00" * 2000)
        kucuk = tmp_path / "kucuk.bin"
        kucuk.write_bytes(b"\x00" * 100)
        durum.add_artifact(_cikti("buyuk.bin", buyuk))
        durum.add_artifact(_cikti("kucuk.bin", kucuk))
        durum.close()

        durum = ProjectState(db)
        assert durum.artifact_info("kucuk.bin").blob_state == "stored"
        bilgi = durum.artifact_info("buyuk.bin")
        assert (bilgi.stored, bilgi.blob_state) == (False, "deferred")
        assert _ham(db, "SELECT COUNT(*) AS n FROM artifact_blobs")[0]["n"] == 1

        assert durum.backfill_artifacts() == 1
        bilgi = durum.artifact_info("buyuk.bin")
        assert (bilgi.stored, bilgi.blob_state, bilgi.bytes) == (True, "stored", 2000)
        durum.close()

    def test_a_legacy_praxis_path_is_recovered_from_the_artifacts_dir(self, tmp_path):
        """`.praxis` -> `.deerx` tasimasi: kayit eski dizin adini tasir ama
        dosya yeni dizinde durur. Aday yol veritabaninin kendi
        `artifacts/` dizini (Settings.artifacts_dir turetmesiyle ayni); kayitli
        `path` degistirilmez, yalnizca kopya alinir."""
        db = tmp_path / ".deerx" / "deerx.db"
        dizin = tmp_path / ".deerx" / "artifacts"
        dizin.mkdir(parents=True)
        (dizin / "eski.md").write_bytes(b"# eski rapor\n")
        eski_yol = tmp_path / ".praxis" / "artifacts" / "eski.md"
        durum = ProjectState(db)
        durum.add_artifact(_cikti("eski.md", eski_yol))
        durum.close()

        durum = ProjectState(db)
        bilgi = durum.artifact_info("eski.md")
        assert (bilgi.stored, bilgi.blob_state) == (True, "stored")
        assert bilgi.path == str(eski_yol)
        assert bilgi.on_disk is False
        assert durum.artifact_bytes(bilgi) == b"# eski rapor\n"
        durum.close()
