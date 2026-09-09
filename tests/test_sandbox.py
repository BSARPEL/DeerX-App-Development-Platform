"""Yalitilmis calistirma: ajanin komutlari konakta degil konteynerde.

Docker gerektiren testler `docker` yoksa atlanir; gerektirmeyenler her
yerde kosar.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from deerx.config import Settings
from deerx.errors import ToolError
from deerx.logging import EventLog
from deerx.sandbox import CALISMA_ALANI, ETIKET, ETIKET_ALAN, Sandbox
from deerx.tools import ToolContext, build_registry


def _docker_calisiyor_mu() -> bool:
    """Docker GERCEKTEN kullanilabilir mi?

    `shutil.which("docker")` yalnizca KOMUTUN varligini soyler, arka
    plandaki daemon'un ayakta oldugunu degil. GitHub'in `windows-latest`
    ve `macos-latest` kosuculari docker CLI'yi kurulu getirir ama Docker
    Desktop'i calistirmaz; `ubuntu-latest` calistirir.

    OLCULDU: bu ayrim yapilmadigi icin testler o iki platformda
    atlanmak yerine kosuyor ve "cannot connect to the Docker daemon" ile
    dusuyordu -- CI'nin dort bacagi bu yuzden kirmiziydi, ubuntu ise
    yesildi. Urunun kendisi ayrimi zaten dogru yapiyor
    (`setup.docker()` `docker info` ile daemon'u yokluyor); test
    tarafi geri kalmisti.
    """
    if shutil.which("docker") is None:
        return False
    try:
        sonuc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return sonuc.returncode == 0 and bool(sonuc.stdout.strip())


DOCKER_VAR = _docker_calisiyor_mu()
docker_gerekli = pytest.mark.skipif(
    not DOCKER_VAR, reason="docker calismiyor (kurulu olmayabilir ya da daemon kapali)"
)


# Testler kendi port araligini kullanir. Varsayilan 8100-8109'u kullanmak,
# bu makinede zaten yalitilmis bir DeerX kosusu varken suitin dusmesine yol
# aciyordu: Docker yayinlanan portlari konteyner yaratilirken ayirir ve
# ikinci konteyner "port is already allocated" ile baslamiyordu. Olculdu.
TEST_PORT_BASE = 8710


def _docker_ayarlari(tmp_path: Path, **ek: object) -> Settings:
    """Docker gerektiren testler icin ayar; portlari varsayilandan uzakta."""
    return Settings(
        workspace=tmp_path, execution="docker",
        sandbox_port_base=TEST_PORT_BASE, **ek,
    )


def _sandbox(ws: Path, ayar: Settings) -> Sandbox:
    return Sandbox(
        ws, ayar.sandbox_image, ayar.sandbox_port_base, ayar.sandbox_port_count,
        ayar.sandbox_memory, ayar.sandbox_cpus, ayar.sandbox_pids, ayar.sandbox_setup,
    )


@contextlib.contextmanager
def _acik_kutu(ws: Path, ayar: Settings):
    """Konteyneri kurar ve testin sonunda SILER.

    `close()` yalnizca DURDURUR ve bu kalici bir proje icin dogrudur:
    kurulum komutu yalnizca ilk kurulusta kosuyor, her kapanista silmek her
    acilista `apt-get install` demek olurdu. Testte ise calisma alani her
    seferinde yeni bir tmp dizin; konteyner adi yolun sha256'si oldugu icin
    bir daha ASLA kullanilmayacak. OLCULDU: bu ayrim yapilmadigi icin
    makinede 48 olu konteyner birikmisti.

    `destroy` cokme halinde de kosar: `finally` bloguna baglidir.
    """
    sb = _sandbox(ws, ayar)
    sb.ensure()
    try:
        yield sb
    finally:
        sb.destroy()


class TestYolCevirme:
    """Konaktaki yol konteyner icindeki karsiligina cevrilmeli."""

    def test_workspace_root_maps_to_the_mount_point(self, tmp_path):
        sb = Sandbox(tmp_path, "x", 8100, 10)
        assert sb.ic_yol(tmp_path) == CALISMA_ALANI

    def test_a_subdirectory_keeps_its_relative_path(self, tmp_path):
        sb = Sandbox(tmp_path, "x", 8100, 10)
        alt = tmp_path / "src" / "app"
        alt.mkdir(parents=True)
        assert sb.ic_yol(alt) == f"{CALISMA_ALANI}/src/app"

    def test_paths_outside_the_workspace_fall_back_to_the_root(self, tmp_path):
        """Calisma alani disi bir yolun konteynerde karsiligi YOK.

        Konagin yolunu oldugu gibi vermek `docker exec -w` hatasi verir ve
        ajan hatayi kendi komutunda arar.
        """
        sb = Sandbox(tmp_path / "proje", "x", 8100, 10)
        (tmp_path / "proje").mkdir()
        assert sb.ic_yol(tmp_path / "baska") == CALISMA_ALANI

    def test_the_container_name_follows_the_workspace(self, tmp_path):
        """Ayni alan ayni konteyneri, farkli alanlar farklisini kullanmali."""
        a = Sandbox(tmp_path / "bir", "x", 8100, 10)
        b = Sandbox(tmp_path / "bir", "x", 8100, 10)
        c = Sandbox(tmp_path / "iki", "x", 8100, 10)
        assert a.name == b.name and a.name != c.name


class TestIzinListesi:
    """Konteynerde izin listesi uygulanmaz; yasak kaliplar uygulanir."""

    def test_the_allow_list_does_not_apply_inside_a_container(self):
        """Liste KONAGI korumak icin var.

        Konteynerde koruyacak konak yok; geriye yalnizca ajanin mesru
        islerini engellemesi kaliyor. Olculdu: yalitilmis ortamda bile
        `rm` reddediliyordu -- ajanin yanlislikla yarattigi bir dosyayi
        silememesinin sebebi tam olarak buydu.
        """
        from deerx.tools.shell import check_command

        politika = Settings().shell
        with pytest.raises(ToolError):
            check_command(politika, "rm gecici.txt")
        assert check_command(politika, "rm gecici.txt", yalitilmis=True)

    def test_catastrophic_patterns_are_still_refused(self):
        """Calisma alani konteynere BAGLI: `rm -rf /` kullanicinin
        projesini de siler. Konak korunur, proje korunmaz -- o yuzden
        felaket kaliplari iki kipte de reddedilir."""
        from deerx.tools.shell import check_command

        politika = Settings().shell
        for komut in ("rm -rf /", "mkfs.ext4 /dev/sda", "shutdown /s"):
            with pytest.raises(ToolError):
                check_command(politika, komut, yalitilmis=True)


class TestPortAraligi:
    """Docker portlari konteyner KURULURKEN ayirir; sonradan eklenemez."""

    def test_a_port_outside_the_published_range_is_refused(self, tmp_path):
        ayar = _docker_ayarlari(tmp_path, approval_mode="auto")
        ayar.ensure_dirs()
        ctx = ToolContext(settings=ayar, events=EventLog(None, echo=False))
        sonuc = build_registry().execute(
            "start_service",
            {"command": "python -m http.server 3000", "port": 3000, "name": "x"},
            ctx,
        )
        assert sonuc.is_error
        assert str(ayar.sandbox_port_base) in sonuc.content, sonuc.content

    def test_the_range_matches_the_settings(self, tmp_path):
        ayar = Settings(workspace=tmp_path)
        sb = _sandbox(tmp_path, ayar)
        assert sb.portu_kapsiyor(ayar.sandbox_port_base)
        assert not sb.portu_kapsiyor(ayar.sandbox_port_base - 1)
        assert not sb.portu_kapsiyor(
            ayar.sandbox_port_base + ayar.sandbox_port_count
        )


@docker_gerekli
class TestGercekKonteyner:
    """Docker ile uctan uca. Yavas ama kanit bunlar."""

    @pytest.fixture
    def ortam(self, tmp_path):
        ayar = _docker_ayarlari(tmp_path, approval_mode="auto")
        ayar.ensure_dirs()
        with _acik_kutu(tmp_path, ayar) as sb:
            yield ayar, sb

    @pytest.mark.slow
    def test_commands_run_in_the_container_not_on_the_host(self, ortam):
        _ayar, sb = ortam
        assert sb.run("uname -s", timeout=60).stdout.strip() == "Linux"

    @pytest.mark.slow
    def test_the_workspace_is_shared_both_ways(self, ortam, tmp_path):
        _ayar, sb = ortam
        sb.run("echo konteynerden > paylasim.txt", timeout=60)
        assert (tmp_path / "paylasim.txt").read_text(encoding="utf-8").strip() \
            == "konteynerden"
        (tmp_path / "konaktan.txt").write_text("konaktan\n", encoding="utf-8")
        assert "konaktan" in sb.run("cat konaktan.txt", timeout=60).stdout

    @pytest.mark.slow
    def test_the_agent_can_delete_its_own_mistake(self, ortam, tmp_path):
        """Yalitimin somut kazanimi. Konakta `rm` yasak oldugu icin bir ajan
        yanlislikla yarattigi dosyayi silemedi ve dosya teslimata girdi."""
        _ayar, sb = ortam
        sb.run("echo yanlis > hata.txt", timeout=60)
        assert (tmp_path / "hata.txt").exists()
        sb.run("rm hata.txt", timeout=60)
        assert not (tmp_path / "hata.txt").exists()

    @pytest.mark.slow
    def test_a_service_in_the_container_is_reachable_from_the_host(self, ortam, tmp_path):
        """Yalitim tarayici UAT dongusunu BOZMAMALI.

        Olculdu: `--network host` konteyner portunu Windows konagina acmiyor;
        tek yol yayinlanan aralik. Bu test o kararin dogrulugunu tutuyor --
        konaktaki tarayici ajanin uygulamasina ulasamazsa yalitim, urunun
        en degerli dongusunu oldururdu.
        """
        from deerx.services import ServiceManager

        ayar, sb = ortam
        port = ayar.sandbox_port_base
        m = ServiceManager(
            log_dir=tmp_path / ".deerx" / "services",
            events=EventLog(None, echo=False),
            sandbox=sb,
        )
        m.start(
            name="deneme",
            command=f"python -m http.server {port} --bind 0.0.0.0",
            cwd=tmp_path, port=port, ready_seconds=40,
        )
        try:
            cevap = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10)
            assert cevap.status == 200
        finally:
            m.stop("deneme")
        assert not sb.port_acik(port), (
            "durdurma konteyner icindeki sureci oldurmedi; port dolu kaldi "
            "ve ayni portu tekrar kullanmak imkansiz olurdu"
        )


class TestKaynakSinirlari:
    """Kacak bir ajan konagi yormamali."""

    def test_the_defaults_are_bounded(self):
        """Sinirsiz birakilirsa bir fork bombasi ya da bellek doldurma
        konteynerde kalmaz, MAKINEYI dizustu eder -- yalitimin amaci tam
        olarak bunu onlemek."""
        ayar = Settings()
        assert ayar.sandbox_memory, "bellek siniri bos birakilmamali"
        assert 0 < ayar.sandbox_cpus <= 64
        assert 0 < ayar.sandbox_pids <= 100_000

    def test_the_image_carries_a_toolchain(self):
        """`slim` icinde git, curl, gcc ve make YOK (olculdu) -- ajan ilk
        `pip install` derlemesinde ya da `git init`te duvara carpar."""
        assert not Settings().sandbox_image.endswith("-slim"), (
            "varsayilan imaj gelistirme araclarini icermeli"
        )


@docker_gerekli
class TestKonakYalitimi:
    """Konteyner konaktaki servislere ULASMAMALI."""

    @pytest.mark.slow
    def test_host_services_are_not_reachable(self, tmp_path):
        """Olculdu: kapatilmadan once konteynerden konaktaki vLLM (8008),
        SearXNG (8890) ve DeerX'in KENDI arayuzune (8791) ulasilabiliyordu.
        Ajan sandbox'tan cikip DeerX'i surebilirdi.
        """
        ayar = _docker_ayarlari(tmp_path)
        ayar.ensure_dirs()
        with _acik_kutu(tmp_path, ayar) as sb:
            kod = (
                "import socket,sys;s=socket.socket();s.settimeout(3);"
                "sys.exit(0 if s.connect_ex(('host.docker.internal',8791))==0 else 1)"
            )
            assert sb.run(f"python -c {kod!r}", timeout=60).returncode != 0, (
                "konteynerden DeerX arayuzune ulasilabiliyor"
            )

    @pytest.mark.slow
    def test_the_internet_still_works(self, tmp_path):
        """Isirma karsiti: yalitim paket kurmayi da engelleseydi ajan
        hicbir sey gelistiremezdi."""
        ayar = _docker_ayarlari(tmp_path)
        ayar.ensure_dirs()
        with _acik_kutu(tmp_path, ayar) as sb:
            kod = ("import urllib.request;"
                   "print(urllib.request.urlopen('https://pypi.org',timeout=15).status)")
            assert sb.run(f"python -c {kod!r}", timeout=90).returncode == 0


class TestTestlerKendiKonteynerleriniSiler:
    """Suit makinede artik birakmamali.

    OLCULDU: 48 adet `deerx-sbx-*` konteyneri birikmisti ve `docker
    inspect` ile bakildiginda HEPSI pytest gecici dizinlerine bagliydi --
    yani hepsi testlerden kalmisti. Sebep `Sandbox.close`un DURDURUP
    silmemesi; ki bu kalici bir proje icin DOGRU karar (kurulum komutu
    yalnizca ilk kurulusta kosuyor). Ayrim testte yapilmali, urunde degil.
    """

    def test_the_helper_destroys_and_does_not_merely_stop(self):
        """`close()` durdurur, `destroy()` siler. Test yardimcisi
        SILMELI: testin calisma alani her seferinde yeni bir tmp dizin,
        konteyner adi yolun sha256'si -- durdurulan konteyner bir daha
        asla kullanilmayacak."""
        import inspect as _inspect

        kaynak = _inspect.getsource(_acik_kutu)
        assert "sb.destroy()" in kaynak, "test konteyneri silmiyor, yalnizca durduruyor"
        assert "finally:" in kaynak, "cokme halinde temizlik kosmaz"

    def test_the_product_still_only_stops_on_close(self):
        """Karsi test: urunun kendisi kapanista SILMEYE baslarsa, kalici
        bir projede her sunucu acilisinda `apt-get install` bastan
        kosardi. Testin kolayligi icin urunu bozmayalim."""
        assert Sandbox.close is Sandbox.stop
        assert Sandbox.destroy is not Sandbox.stop

    def test_the_sweeper_only_matches_this_sessions_directory(self, tmp_path):
        """Supurucunun olcutu ADA degil BAGLI DIZINE bakar.

        Ad, calisma alani yolunun sha256'si; ada bakarak bir konteynerin
        teste mi gercek bir projeye mi ait oldugu ANLASILMAZ. Bu test
        olcutun kendisini kilitler: baska bir kok altindaki yol
        eslesmemeli.
        """
        import os

        from conftest import _bu_oturumun_konteynerleri

        assert callable(_bu_oturumun_konteynerleri)

        # Olcutun kendisi: normalize edilmis onek karsilastirmasi.
        benim = tmp_path / "test_x0"
        baskasi = tmp_path.parent / "baska-kok" / "proje"
        gercek = Path.home() / "Desktop" / "GercekProje"
        kok = os.path.normcase(str(tmp_path))
        assert os.path.normcase(str(benim)).startswith(kok)
        assert not os.path.normcase(str(baskasi)).startswith(kok)
        assert not os.path.normcase(str(gercek)).startswith(kok)

    @docker_gerekli
    @pytest.mark.slow
    def test_a_container_is_gone_after_the_helper_exits(self, tmp_path):
        """ASIL KANIT: yardimci cikinca konteyner GERCEKTEN yok."""
        ayar = _docker_ayarlari(tmp_path)
        ayar.ensure_dirs()
        with _acik_kutu(tmp_path, ayar) as sb:
            ad = sb.name
            assert _konteyner_var(ad), "konteyner kurulmadi"
        assert not _konteyner_var(ad), (
            "yardimci cikti ama konteyner duruyor; suit makinede artik birakiyor"
        )

    @docker_gerekli
    @pytest.mark.slow
    def test_the_container_is_gone_even_when_the_test_fails(self, tmp_path):
        """Temizlik `finally`ye bagli: testin patlamasi artik birakmamali."""
        ayar = _docker_ayarlari(tmp_path)
        ayar.ensure_dirs()
        ad = ""
        with pytest.raises(RuntimeError):
            with _acik_kutu(tmp_path, ayar) as sb:
                ad = sb.name
                raise RuntimeError("testin icinde patlama")
        assert ad and not _konteyner_var(ad)


def _konteyner_var(ad: str) -> bool:
    sonuc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{ad}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30,
    )
    return ad in sonuc.stdout.split()


class TestKonteynerKimOldugunuSoyler:
    """Ad yalnizca bir ozet; konteyner nereye ait oldugunu TASIMALI.

    Elinizde `deerx-sbx-3f84682fec` varken hangi projeye ait oldugunu
    ogrenmenin yolu yoktu: ad `sha256(yol)[:10]` ve ozet geri
    cevrilemiyor. Aday yollari tek tek ozetleyip denemek disinda hicbir
    yontem kalmiyordu -- yani yetim bir konteyner sonsuza kadar yetim.
    """

    def test_the_create_command_carries_both_labels(self, tmp_path, monkeypatch):
        """Kurulum komutu OLCULUR: `docker run` satirinda etiketler var mi."""
        yakalanan: list[list[str]] = []

        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        monkeypatch.setattr(sb, "_durum", lambda: None)
        monkeypatch.setattr(
            sb, "_docker", lambda argv, timeout: yakalanan.append(argv) or "")
        monkeypatch.setattr(shutil, "which", lambda _ad: "docker")
        sb.setup = ""  # kurulum komutu kosmasin
        sb.ensure()

        assert yakalanan, "docker run hic cagrilmadi"
        argv = yakalanan[0]
        assert f"{ETIKET}=1" in argv, "DeerX kabini oldugu isaretlenmemis"
        assert f"{ETIKET_ALAN}={tmp_path}" in argv, (
            "konteyner hangi calisma alanina ait oldugunu tasimiyor"
        )

    def test_the_labels_are_not_guessed_in_two_places(self):
        """Etiket adi urunde bir kez tanimli olmali; test tarafi onu
        KOPYALAMAZ, ithal eder. Iki sabit olsaydi biri gunu gelince
        kayar ve supurucu sessizce hicbir sey bulamazdi."""
        from pathlib import Path as _Path

        kaynak = (_Path("tests") / "conftest.py").read_text(encoding="utf-8")
        assert "from deerx.sandbox import ETIKET" in kaynak
        assert '"deerx.sandbox"' not in kaynak, "etiket adi testte kopyalanmis"

    @docker_gerekli
    @pytest.mark.slow
    def test_docker_can_find_the_container_by_its_workspace(self, tmp_path):
        """ASIL KANIT: etiketle sorulunca Docker dogru konteyneri veriyor."""
        ayar = _docker_ayarlari(tmp_path)
        ayar.ensure_dirs()
        with _acik_kutu(tmp_path, ayar) as sb:
            sonuc = subprocess.run(
                ["docker", "ps", "-a",
                 "--filter", f"label={ETIKET_ALAN}={tmp_path}",
                 "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=30,
            )
            assert sb.name in sonuc.stdout.split(), (
                "konteyner kendi calisma alaniyla bulunamiyor"
            )


class TestOrtamiYenidenKurGercektenKurar:
    """"Yeniden kur" dugmesi hicbir sey yeniden kurmuyordu.

    OLCULDU: `reset_sandbox` kabin icin `close()` cagiriyordu ve `close`
    aslinda `stop`. Konteyner adi yalnizca calisma alani yolundan
    turetildigi icin bir sonraki `ensure()` AYNI adi uretiyor, konteyner
    var oldugundan `docker start` ile ESKISINI geri getiriyordu: eski
    imaj, eski bellek/CPU sinirlari, eski yayinlanmis port araligi.

    Ustelik islevin kendi belgesi "Konteyner SILINIR" diyor ve arayuz
    kullaniciya "kabini yeniden kurmak calisan konteyneri siler" uyarisi
    gosteriyordu. Kod, belgesinin ve kullaniciya verdigi sozun tersini
    yapiyordu.
    """

    def test_the_rebuild_path_destroys_instead_of_stopping(self, settings, tmp_path):
        from deerx.pipeline.orchestrator import Orchestrator

        cagrilar: list[str] = []

        class SahteKabin:
            def close(self) -> None:
                cagrilar.append("close")

            def stop(self) -> None:
                cagrilar.append("stop")

            def destroy(self) -> None:
                cagrilar.append("destroy")

        orch = Orchestrator.__new__(Orchestrator)
        orch.settings = settings
        orch.settings.execution = "host"   # yeniden kurmasin, yalnizca biraksin
        orch._sandbox = SahteKabin()       # noqa: SLF001 - testin kurdugu durum
        orch.ctx = type("x", (), {"_sandbox": None})()
        orch.services = type("x", (), {"sandbox": None})()
        orch.reset_sandbox()

        assert cagrilar == ["destroy"], (
            f"kabin silinmedi, cagrilar: {cagrilar} -- durdurulan konteyner ayni "
            "adla geri gelir ve eski ayarlarla kosar"
        )

    def test_the_promise_shown_to_the_user_matches_the_code(self):
        """Kullaniciya gosterilen uyari ile kodun yaptigi ayni sey olmali."""
        from deerx.i18n import CATALOG

        uyari = CATALOG.get("api.sandbox_locked", {})
        metin = " ".join(str(v) for v in uyari.values()).lower()
        if metin:
            assert "sil" in metin or "destroy" in metin, (
                "uyari metni degistiyse bu testin gerekcesi de gozden gecirilmeli"
            )
