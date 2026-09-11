"""Terminal arayuzu: `deerx artifacts` ve komsulari.

Depoda CLI komutlarini GERCEKTEN kosturan bir test yoktu; hepsi
kutuphane katmanindan olculuyordu. Buradaki testler komutu typer'in
kendi kosucusuyla cagirir, cunku kirilma tam o katmanda oluyordu:
`deerx artifacts <paket>.zip` bir zip'i utf-8 sanip okuyor ve geri
izleme basiyordu.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from deerx.cli import app
from deerx.pipeline.models import Artifact
from deerx.pipeline.state import ProjectState


@pytest.fixture
def kosucu():
    return CliRunner()


@pytest.fixture
def calisma(tmp_path, monkeypatch):
    """Icinde bir DeerX projesi olan calisma alani.

    CLI calisma alanini yukari dogru `deerx.toml` arayarak buluyor;
    `DEERX_WORKSPACE` bunu acikca ayarlayan tek yol ve testin baska bir
    projenin icine dusmesini engeller.
    """
    (tmp_path / "deerx.toml").write_text("[deerx]\n", encoding="utf-8")
    monkeypatch.setenv("DEERX_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("DEERX_LANGUAGE", raising=False)
    return tmp_path


def _cikti(calisma, ad: str, veri: bytes) -> None:
    """Ciktiyi HEM diske HEM veritabanina yazar, sonra dosyayi siler.

    Silme kasitli: testin olctugu sey, kaynagin artik veritabani
    olmasi. Dosya dursaydi eski kod da gecerdi.
    """
    veri_dizin = calisma / ".deerx" / "artifacts"
    veri_dizin.mkdir(parents=True, exist_ok=True)
    yol = veri_dizin / ad
    yol.write_bytes(veri)
    durum = ProjectState(calisma / ".deerx" / "deerx.db")
    try:
        durum.add_artifact(
            Artifact(name=ad, kind="report", path=str(yol), summary="ozet"),
            blob=veri,
        )
    finally:
        durum.close()
    yol.unlink()


class TestCiktiyiOkumak:
    def test_a_text_artifact_is_read_from_the_database(self, kosucu, calisma):
        """Dosya diskten silinmis olsa da icerik gelmeli: kaynak artik
        veritabani, disk yalnizca kopya."""
        _cikti(calisma, "rapor.md", b"# Rapor\nicerik\n")
        sonuc = kosucu.invoke(app, ["artifacts", "rapor.md"])
        assert sonuc.exit_code == 0, sonuc.output
        assert "Rapor" in sonuc.output

    def test_a_binary_artifact_is_summarised_not_dumped(self, kosucu, calisma):
        """`.zip` metin degildir. Eski kod `read_text(encoding="utf-8")`
        cagiriyor ve UnicodeDecodeError ile geri izleme basiyordu;
        `errors="replace"` ise bir ekran dolusu anlamsiz karakter
        uretirdi. Dogru cevap: ad, boyut, saglama ve nasil alinacagi."""
        _cikti(calisma, "paket.zip", b"PK\x03\x04" + bytes(range(256)) * 4)
        sonuc = kosucu.invoke(app, ["artifacts", "paket.zip"])
        assert sonuc.exit_code == 0, sonuc.output
        assert "Traceback" not in sonuc.output
        assert "--export" in sonuc.output
        assert "MB" in sonuc.output

    def test_a_row_without_bytes_anywhere_says_so(self, kosucu, calisma):
        """Ne veritabaninda ne diskte olan bir kayit icin cevap durust
        olmali; bos bir sayfa degil."""
        durum = ProjectState(calisma / ".deerx" / "deerx.db")
        try:
            durum.add_artifact(
                Artifact(name="hayalet.md", kind="report", path=str(calisma / "yok.md"))
            )
        finally:
            durum.close()
        sonuc = kosucu.invoke(app, ["artifacts", "hayalet.md"])
        assert sonuc.exit_code != 0
        assert "hayalet" in sonuc.output or "yok.md" in sonuc.output


class TestDisaAktarmaVeDogrulama:
    def test_export_writes_the_stored_bytes(self, kosucu, calisma, tmp_path):
        """Disk kopyasi silinmis bir ciktiyi geri almanin yolu."""
        veri = b"PK\x03\x04" + b"deerx" * 100
        _cikti(calisma, "teslimat.zip", veri)
        hedef = tmp_path / "disari" / "teslimat.zip"

        sonuc = kosucu.invoke(app, ["artifacts", "teslimat.zip", "--export", str(hedef)])
        assert sonuc.exit_code == 0, sonuc.output
        assert hedef.read_bytes() == veri

    def test_verify_confirms_the_checksum(self, kosucu, calisma):
        _cikti(calisma, "notlar.md", b"# Notlar\n")
        sonuc = kosucu.invoke(app, ["artifacts", "notlar.md", "--verify"])
        assert sonuc.exit_code == 0, sonuc.output
        assert "sha256" in sonuc.output

    def test_verify_fails_loudly_on_corruption(self, kosucu, calisma):
        """Bozulmayi sessizce gecmek, saglamayi hic tutmamaktan kotudur:
        kullanici "dogruladim" der ve bozuk dosyayi teslim eder."""
        import sqlite3

        _cikti(calisma, "bozuk.md", b"# Bozuk\n")
        db = calisma / ".deerx" / "deerx.db"
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE artifact_blobs SET data = X'00' || substr(data, 2)")

        sonuc = kosucu.invoke(app, ["artifacts", "bozuk.md", "--verify"])
        assert sonuc.exit_code == 1, sonuc.output


class TestBakimSecenekleri:
    def test_backfill_takes_rows_left_on_disk(self, kosucu, calisma):
        """Blob'suz eski bir kayit (yalnizca diskte) `--backfill` ile
        veritabanina girer."""
        import sqlite3

        veri_dizin = calisma / ".deerx" / "artifacts"
        veri_dizin.mkdir(parents=True, exist_ok=True)
        yol = veri_dizin / "eski.md"
        yol.write_text("# Eski\n", encoding="utf-8")
        durum = ProjectState(calisma / ".deerx" / "deerx.db")
        try:
            durum.add_artifact(
                Artifact(name="eski.md", kind="report", path=str(yol))
            )
            # Acilistaki otomatik doldurmayi geri al ki `--backfill`in
            # kendisi olculsun.
            durum._conn.execute("DELETE FROM artifact_blobs")  # noqa: SLF001
            durum._conn.execute("UPDATE artifacts SET blob_state = ''")  # noqa: SLF001
            durum._commit()  # noqa: SLF001
        finally:
            durum.close()

        sonuc = kosucu.invoke(app, ["artifacts", "--backfill"])
        assert sonuc.exit_code == 0, sonuc.output
        with sqlite3.connect(calisma / ".deerx" / "deerx.db") as conn:
            sayi = conn.execute("SELECT COUNT(*) FROM artifact_blobs").fetchone()[0]
        assert sayi == 1, "diskte kalmis cikti veritabanina alinmadi"

    def test_checkpoint_empties_the_write_ahead_log(self, kosucu, calisma):
        """Yalnizca `deerx.db`yi kopyalayan bir yedek, `-wal` icindeki son
        ciktilari kaybeder. Bu secenek yedekten once calistirilir."""
        _cikti(calisma, "buyuk.md", b"x" * 200_000)
        wal = calisma / ".deerx" / "deerx.db-wal"

        sonuc = kosucu.invoke(app, ["artifacts", "--checkpoint"])
        assert sonuc.exit_code == 0, sonuc.output
        assert not wal.exists() or wal.stat().st_size == 0, (
            "checkpoint WAL'i bosaltmadi"
        )


class TestPaketKosuKaydiAlir:
    def test_a_cli_package_belongs_to_a_run(self, kosucu, calisma):
        """Belge "elle paketleme tek adimli bir kosu kaydi olusturur"
        diyor ama bu yalnizca web icin dogruydu: CLI'den uretilen paket
        hicbir kosuya ait olmuyor ve Kosular goruntusunden
        erisilemiyordu."""
        (calisma / "app.py").write_text("x = 1\n", encoding="utf-8")

        sonuc = kosucu.invoke(app, ["package", "--force"])
        assert sonuc.exit_code == 0, sonuc.output

        durum = ProjectState(calisma / ".deerx" / "deerx.db")
        try:
            paketler = [a for a in durum.list_artifacts() if a.kind == "package"]
            assert paketler, "paket kaydedilmemis"
            assert paketler[0].run_id, "paket hicbir kosuya bagli degil"
            kosular = durum.list_runs(10)
            assert any(k["id"] == paketler[0].run_id for k in kosular), (
                "paketin kosusu kosu listesinde yok"
            )
        finally:
            durum.close()
