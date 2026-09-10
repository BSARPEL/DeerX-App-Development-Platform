"""Yalitilmis calistirma: ajanin komutlari konakta degil konteynerde.

Docker gerektiren testler `docker` yoksa atlanir; gerektirmeyenler her
yerde kosar.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from deerx.config import Settings
from deerx.errors import ToolError
from deerx.logging import EventLog
from deerx.sandbox import (
    CALISMA_ALANI,
    ETIKET,
    ETIKET_ALAN,
    SORUN_ANAHTARLARI,
    Sandbox,
    SandboxUnavailable,
    docker_hatasi_cevir,
)
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


# OLCULDU (bu makine, Docker Desktop + WSL2): calisma alani baglanamayinca
# `docker run` tam bu satirla dusuyordu. Ayirt edici parca uzun yolun
# SONUNDA gelir; testler bunu oldugu gibi tasir ki 300 karakterlik
# kisaltma siniflandirmayi kacirmasin.
_WSL_MOUNT_HATASI = (
    "docker: Error response from daemon: error while creating mount source "
    "path '/run/desktop/mnt/host/c/Users/deneme/Desktop/uzun-bir-proje-adi/"
    "alt-dizin/daha-da-uzun-bir-alt-dizin/calisma-alani': "
    "mkdir /run/desktop/mnt/host/c: file exists."
)


class _SahteDocker:
    """`subprocess.run` yerine gecer; docker komutlarina senaryoya gore cevap.

    Gercek docker'a gitmeden `probe`/`ensure` yollarini olcmek icin: her
    cagrinin argv'si kaydedilir, cevap komut adina gore secilir (`image`
    ikinci sozcugu de alir). Cevap `(rc, stdout, stderr)`, argv alan bir
    islev ya da firlatilacak bir istisna olabilir. Tanimsiz komut basarili
    sayilir: yoklama `rm`/`stop` gibi temizlik cagrilarinda takilmasin.
    """

    def __init__(self, **cevaplar: object) -> None:
        self.cevaplar = cevaplar
        self.cagrilar: list[list[str]] = []

    def __call__(self, argv, **_kw):
        argv = list(argv)
        self.cagrilar.append(argv)
        assert argv[0] == "docker", argv
        anahtar = "image" if argv[1] == "image" else argv[1]
        cevap = self.cevaplar.get(anahtar, (0, "", ""))
        if isinstance(cevap, BaseException):
            raise cevap
        if callable(cevap):
            cevap = cevap(argv)
        rc, out, err = cevap
        return subprocess.CompletedProcess(argv, rc, out, err)

    def sayi(self, komut: str) -> int:
        return sum(1 for a in self.cagrilar if a[1] == komut)

    def ilk(self, komut: str) -> list[str]:
        return next(a for a in self.cagrilar if a[1] == komut)


def _sahte_docker(monkeypatch, **cevaplar: object) -> _SahteDocker:
    """Docker CLI var, daemon cevap veriyor; gerisi senaryoya bagli."""
    import deerx.sandbox as modul

    sahte = _SahteDocker(**cevaplar)
    monkeypatch.setattr(subprocess, "run", sahte)
    monkeypatch.setattr(shutil, "which", lambda _ad: "docker")
    # Onbellek ve kilitler modul/sinif duzeyi; testler birbirine sizmasin.
    monkeypatch.setattr(modul, "_PROBE_ONBELLEK", {})
    monkeypatch.setattr(Sandbox, "_KILITLER", {})
    return sahte


def _anahtarlar(satirlar: list[dict]) -> list[str]:
    return [s["key"] for s in satirlar]


class TestHataCevirisi:
    """Docker'in ham hatasi bir AD almali; metnin kendisi yol gostermez.

    Kullanici olay akisinda "mkdir /run/desktop/mnt/host/c: file exists"
    gorup ne yapacagini bilemiyordu. Anahtar hem sunucu mesajini
    (`sandbox.<key>`) hem arayuz aciklamasini (`env.issue.<key>`) secer.
    """

    def test_the_wsl_mount_failure_gets_a_name(self):
        """Bilinen olgu: Docker Desktop'in WSL2 konak baglantisi kopmus."""
        assert docker_hatasi_cevir(_WSL_MOUNT_HATASI) == ("bind_mount_broken", {})

    @pytest.mark.parametrize(
        ("stderr", "beklenen"),
        [
            ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
             "Is the docker daemon running?", "daemon_down"),
            ('error during connect: Get "http://%2F%2F.%2Fpipe%2Fdocker_engine/'
             'v1.51/containers/json": open //./pipe/docker_engine: The system '
             "cannot find the file specified.", "daemon_down"),
            ("Unable to find image 'deerx/yok:latest' locally\ndocker: Error "
             "response from daemon: pull access denied for deerx/yok, repository "
             "does not exist or may require 'docker login'.", "image_pull_failed"),
            ("docker: Error response from daemon: manifest for python:9.99 not "
             "found: manifest unknown: manifest unknown", "image_pull_failed"),
            ("docker: Error response from daemon: No such image: python:3.13",
             "image_missing"),
            ("docker: Error response from daemon: driver failed programming "
             "external connectivity on endpoint deerx-sbx-abc: Bind for "
             "127.0.0.1:8100 failed: port is already allocated", "port_busy_host"),
            ('docker: Error response from daemon: Conflict. The container name '
             '"/deerx-sbx-abc" is already in use by container "0123abcd". You '
             "have to remove (or rename) that container to be able to reuse "
             "that name.", "name_in_use"),
            ('docker: Error response from daemon: invalid mount config for type '
             '"bind": bind source path does not exist', "bind_mount_broken"),
        ],
    )
    def test_known_docker_messages_get_their_key(self, stderr, beklenen):
        """Kaliplar Docker Desktop ve Linux daemon'un gercek metinlerinden."""
        sonuc = docker_hatasi_cevir(stderr)
        assert sonuc is not None and sonuc[0] == beklenen, sonuc
        assert beklenen in SORUN_ANAHTARLARI

    def test_case_does_not_matter(self):
        """Docker surumleri arasinda buyuk/kucuk harf oynuyor; kalip
        kucuk harfle karsilastirilmali."""
        assert docker_hatasi_cevir("PORT IS ALREADY ALLOCATED") == ("port_busy_host", {})

    def test_an_unknown_error_stays_unnamed(self):
        """Taninmayan hata uydurma bir ad almamali: `None`, eski
        `sandbox.command_failed` yolunu secer ve ham metin gorunur --
        yanlis bir care onermekten iyidir."""
        assert docker_hatasi_cevir("docker: Error response from daemon: OCI runtime "
                                   "create failed: something new") is None
        assert docker_hatasi_cevir("") is None


class TestProbe:
    """`probe` kabini KURMADAN olcer; sig kip ucuz, derin kip istege bagli.

    Ortam ekrani her acilista `docker run` kostursaydi, tam da teshis
    etmesi gereken arizada (kopuk bind mount) bir dakika asili kalirdi.
    """

    def test_probe_without_docker_never_calls_docker(self, tmp_path, monkeypatch):
        """Docker yokken `subprocess` bulunamayan komut icin OSError firlatir;
        onu yakalamak olcmek istedigimiz seyi gizlerdi. Sayac sifir."""
        sahte = _sahte_docker(monkeypatch)
        monkeypatch.setattr(shutil, "which", lambda _ad: None)
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe()

        assert saglik.docker_cli is False
        assert _anahtarlar(saglik.problems) == ["no_docker"]
        assert saglik.status == "unavailable"
        assert sahte.cagrilar == [], "docker yokken docker cagrildi"

    def test_a_silent_daemon_is_a_problem_and_stops_the_probe(self, tmp_path, monkeypatch):
        """`docker info` cevap vermiyorsa gerisini sormanin anlami yok;
        her biri ayni hatayla dusup 30 saniye daha bekletirdi."""
        sahte = _sahte_docker(monkeypatch, info=(1, "", "error during connect: open "
                                                        "//./pipe/docker_engine"))
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True)

        assert saglik.docker_cli is True and saglik.daemon is None
        assert _anahtarlar(saglik.problems) == ["daemon_down"]
        assert sahte.sayi("inspect") == 0 and sahte.sayi("run") == 0

    def test_probe_reports_a_broken_mount(self, tmp_path, monkeypatch):
        """Derin yoklama gecici bir konteynerle olcer ve WSL2 hatasini
        ADIYLA doner; komut satiri imaj cekmez (`--pull never`), arkada
        konteyner birakmaz (`--rm`) ve kosunun konteyneriyle karismaz."""
        sahte = _sahte_docker(
            monkeypatch,
            info=(0, "29.7.2\n", ""),
            inspect=(1, "", "Error: No such object"),
            image=(0, "sha256:abc\n", ""),
            run=(125, "", _WSL_MOUNT_HATASI),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True)

        assert saglik.daemon == "29.7.2"
        assert saglik.image_present is True
        assert saglik.mount_ok is False
        assert _anahtarlar(saglik.problems) == ["bind_mount_broken"]
        assert saglik.status == "unavailable" and saglik.deep is True
        degerler = saglik.problems[0]["args"]
        assert degerler["workspace"] == str(sb.workspace)
        assert "mnt/host" in degerler["error"]

        kosum = sahte.ilk("run")
        assert kosum[kosum.index("--pull") + 1] == "never"
        assert kosum[kosum.index("--name") + 1].startswith("deerx-probe-")
        assert "--rm" in kosum and "-d" not in kosum
        assert f"{sb.workspace}:{CALISMA_ALANI}" in kosum
        assert sb.name not in kosum, "yoklama kosunun konteyner adini kullandi"

    def test_the_probe_is_cached(self, tmp_path, monkeypatch):
        """Ortam ekrani, kosu hazirligi ve ayarlar ayni saniyede ayni
        soruyu sorar; her biri docker'a gitmemeli. `ttl=0` tazeler."""
        sahte = _sahte_docker(monkeypatch, info=(0, "29.7.2\n", ""),
                              inspect=(1, "", ""), image=(0, "sha256:abc\n", ""))
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        ilk = sb.probe()
        sayi = len(sahte.cagrilar)
        assert sayi > 0
        assert sb.probe() is ilk
        assert len(sahte.cagrilar) == sayi, "ikinci yoklama docker'a gitti"

        sb.probe(ttl=0)
        assert len(sahte.cagrilar) > sayi, "ttl=0 tazelemedi"

    def test_a_deep_result_serves_a_shallow_request_but_not_the_reverse(
        self, tmp_path, monkeypatch
    ):
        """Derin sonuc sig sorunun cevabini da icerir; sig sonuc derin
        sorunun (mount, araclar) cevabini icermez."""
        sahte = _sahte_docker(
            monkeypatch, info=(0, "29.7.2\n", ""), inspect=(1, "", ""),
            image=(0, "sha256:abc\n", ""), run=(0, "MOUNT_OK\nTOOL_python\n", ""),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        sig = sb.probe()
        assert sig.deep is False and sahte.sayi("run") == 0
        derin = sb.probe(deep=True)
        assert derin.deep is True and sahte.sayi("run") == 1, "sig sonuc derin istegi karsiladi"
        assert sb.probe() is derin, "derin sonuc sig istegi karsilamadi"
        assert sahte.sayi("run") == 1

    def test_image_missing_is_a_warning_not_a_problem(self, tmp_path, monkeypatch):
        """`ensure()` imaji `docker run` ile kendisi ceker. Sorun sayilsaydi
        taze bir kurulumda `deerx setup` ilk kosudan once "kabin
        kurulamiyor" derdi. Derin yoklama da imaj yokken konteyner
        kaldirmaya kalkmaz: `--pull never` ile zaten dusecekti."""
        sahte = _sahte_docker(monkeypatch, info=(0, "29.7.2\n", ""),
                              inspect=(1, "", ""), image=(1, "", "Error: No such image"))
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True)

        assert saglik.image_present is False
        assert saglik.problems == []
        assert _anahtarlar(saglik.warnings) == ["image_missing"]
        assert saglik.warnings[0]["args"]["image"] == sb.image
        assert saglik.status == "absent"
        assert sahte.sayi("run") == 0

    def test_a_running_container_is_probed_in_place(self, tmp_path, monkeypatch):
        """Konteyner calisiyorsa ikinci bir konteyner kaldirmak yerine
        icine girilir; araclar oradan okunur. `node_gerekli` cagirana
        ozgu: package.json olan proje uyariyi alir, digerleri almaz."""
        sahte = _sahte_docker(
            monkeypatch, info=(0, "29.7.2\n", ""), inspect=(0, "running\n", ""),
            image=(0, "sha256:abc\n", ""),
            exec=(0, "MOUNT_OK\nTOOL_python\nTOOL_git\n", ""),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True)

        assert sahte.sayi("run") == 0 and sb.name in sahte.ilk("exec")
        assert saglik.mount_ok is True
        assert saglik.tools == {"python": True, "node": False, "npm": False, "git": True}
        assert saglik.status == "running" and saglik.warnings == []

        istenen = sb.probe(deep=True, node_gerekli=True)
        assert _anahtarlar(istenen.warnings) == ["node_missing"]
        assert sb.probe(deep=True).warnings == [], "uyari onbellege yazildi"

    def test_a_container_that_died_before_exec_is_not_a_mount_problem(
        self, tmp_path, monkeypatch
    ):
        """`inspect` "running" dedi, `exec` "is not running" ile dustu:
        konteyner arada olmus. Bu bir mount arizasi degil; `bind_mount_broken`
        demek kullaniciya yanlis care ("Docker Desktop'i yeniden baslatin")
        verirdi. Mount ve araclar OLCULMEDI (None / bos) -- betik hic
        kosmadi; bos stdout'u "hic arac yok" saymak docker hatasinin ustune
        bir de `node_missing` bindirirdi. Sorun ham metniyle, ad iddiasiz."""
        sahte = _sahte_docker(
            monkeypatch, info=(0, "29.7.2\n", ""), inspect=(0, "running\n", ""),
            image=(0, "sha256:abc\n", ""),
            exec=(1, "", "Error response from daemon: container deerx-sbx-x is not running"),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True, node_gerekli=True)

        assert _anahtarlar(saglik.problems) == ["unavailable"]
        assert "is not running" in saglik.problems[0]["args"]["error"]
        assert saglik.mount_ok is None and saglik.tools == {}
        assert saglik.warnings == [], "olculmemis arac icin uyari verildi"
        assert sahte.sayi("run") == 0

    def test_a_probe_timeout_removes_the_helper_container(self, tmp_path, monkeypatch):
        """Zaman asimi `docker run` istemcisini oldurur, konteyneri degil;
        `--rm` ancak konteyner bitince temizler. Bilinen olguda takilan
        konteyner makinede kalirdi."""
        sahte = _sahte_docker(
            monkeypatch, info=(0, "29.7.2\n", ""), inspect=(1, "", ""),
            image=(0, "sha256:abc\n", ""),
            run=subprocess.TimeoutExpired(cmd="docker run", timeout=60),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True)

        assert _anahtarlar(saglik.problems) == ["bind_mount_broken"]
        assert saglik.mount_ok is False
        silme = sahte.ilk("rm")
        assert "-f" in silme and silme[-1].startswith("deerx-probe-")

    def test_a_hanging_cleanup_does_not_break_the_probe(self, tmp_path, monkeypatch):
        """Yoklama hicbir halde firlatmamali. Takilan daemon'da `docker run`
        gibi temizligin `docker rm -f`i de takilir; ham `subprocess.run`
        ile ikinci TimeoutExpired disari cikiyor ve Ortam ekrani 500
        aliyordu. Sonuc yine bir saglik kaydi, sorun yine adli."""
        sahte = _sahte_docker(
            monkeypatch, info=(0, "29.7.2\n", ""), inspect=(1, "", ""),
            image=(0, "sha256:abc\n", ""),
            run=subprocess.TimeoutExpired(cmd="docker run", timeout=60),
            rm=subprocess.TimeoutExpired(cmd="docker rm", timeout=30),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe(deep=True)

        assert saglik.deep is True and saglik.status == "unavailable"
        assert _anahtarlar(saglik.problems) == ["bind_mount_broken"]
        assert saglik.mount_ok is False
        assert sahte.sayi("rm") == 1, "temizlik hic denenmedi"

    def test_a_hanging_inspect_does_not_hang_the_probe(self, tmp_path, monkeypatch):
        """`info` yanit verip `inspect` takilirsa yoklama yine bir sonuc
        donmeli; durum "bilinmiyor" (None) olur, ekran asili kalmaz."""
        sahte = _sahte_docker(
            monkeypatch, info=(0, "29.7.2\n", ""),
            inspect=subprocess.TimeoutExpired(cmd="docker inspect", timeout=30),
            image=(0, "sha256:abc\n", ""),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        saglik = sb.probe()

        assert saglik.ok and saglik.container_status is None
        assert saglik.status == "absent"
        assert sahte.sayi("inspect") == 1

    def test_a_healthy_probe_clears_the_broken_mark(self, tmp_path, monkeypatch):
        """Docker Desktop yeniden baslatildiktan sonra kosu yeniden
        denenebilmeli; isaret ancak SORUNSUZ bir yoklamayla kalkar."""
        sahte = _sahte_docker(monkeypatch, info=(0, "29.7.2\n", ""),
                              inspect=(1, "", ""), image=(0, "sha256:abc\n", ""))
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb._kirik = SandboxUnavailable("x", key="bind_mount_broken")  # noqa: SLF001

        sahte.cevaplar["info"] = (1, "", "error during connect")
        sb.probe(ttl=0)
        assert sb._kirik is not None, "sorunlu yoklama isareti kaldirdi"  # noqa: SLF001

        sahte.cevaplar["info"] = (0, "29.7.2\n", "")
        sb.probe(ttl=0)
        assert sb._kirik is None  # noqa: SLF001


class TestKirikKabin:
    """Kabin bir kez kurulamadiysa her arac cagrisi docker'a yeniden gitmemeli.

    Her `run_command` ayni `docker run`u yeniden deniyor, her biri
    saniyeler (kopuk mount'ta bir dakika) bekliyor ve ayni hatayi
    uretiyordu; kosu bir turda dusecegine yarim saat surunuyordu.
    """

    def test_ensure_raises_a_named_error_and_marks_the_sandbox(self, tmp_path, monkeypatch):
        sahte = _sahte_docker(monkeypatch, inspect=(1, "", "No such object"),
                              run=(125, "", _WSL_MOUNT_HATASI))
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = ""

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "bind_mount_broken"
        assert "Docker Desktop" in str(bilgi.value), str(bilgi.value)
        assert bilgi.value.args["workspace"] == str(sb.workspace)
        assert isinstance(bilgi.value, ToolError), "ajan dongusu ToolError bekler"
        assert sb._kirik is not None  # noqa: SLF001
        assert sahte.sayi("run") == 1

        with pytest.raises(SandboxUnavailable) as tekrar:
            sb.run("echo deneme", timeout=5)
        assert tekrar.value.key == "bind_mount_broken"
        assert sahte.sayi("run") == 1, "kirik kabin docker'a yeniden gitti"
        with pytest.raises(SandboxUnavailable):
            sb.port_acik(TEST_PORT_BASE)
        # `ic_oldur` kapanis temizliginin icinde (`services.stop_all` ->
        # `Orchestrator.close`); firlatsaydi temizlik ilk serviste kesilir,
        # tarayici ve SQLite baglantisi acik kalirdi. Sessiz ama docker'siz.
        sb.ic_oldur("/tmp/x.pid")
        assert sahte.sayi("exec") == 0

        sb.destroy()
        assert sb._kirik is None  # noqa: SLF001
        assert sahte.sayi("rm") == 1

    def test_the_message_survives_the_args_attribute(self):
        """OLCULDU: `self.args = {...}` `BaseException.args`i ezer -- sozluk
        anahtarlarinin demetine cevrilir ve `str(exc)` mesaj yerine ilk
        anahtari basar. Kullanici "workspace" yazan bir hata gorurdu."""
        hata = SandboxUnavailable("mesaj", key="daemon_down", args={"workspace": "w"})
        assert str(hata) == "mesaj"
        assert hata.args == {"workspace": "w"}
        assert hata.key == "daemon_down"

    def test_an_unknown_failure_is_still_a_plain_tool_error(self, tmp_path, monkeypatch):
        """Taninmayan hata kabini kirik isaretlemez: yanlis bir ad ve yanlis
        bir care yerine ham metin, ve bir sonraki cagri yeniden dener."""
        _sahte_docker(monkeypatch, inspect=(1, "", ""),
                      run=(125, "", "docker: Error response from daemon: OCI runtime "
                                    "create failed: something new"))
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = ""

        with pytest.raises(ToolError) as bilgi:
            sb.ensure()
        assert not isinstance(bilgi.value, SandboxUnavailable)
        assert sb._kirik is None  # noqa: SLF001

    def test_no_docker_is_the_same_kind_of_stop(self, tmp_path, monkeypatch):
        """Docker hic yoksa da tek durus: orkestrator ayni turu yakalar."""
        sahte = _sahte_docker(monkeypatch)
        monkeypatch.setattr(shutil, "which", lambda _ad: None)
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "no_docker"
        assert sahte.cagrilar == []

    def test_a_failed_setup_command_marks_the_sandbox(self, tmp_path, monkeypatch):
        """Konteyner var ama yarim kurulu; bir sonraki `ensure` onu
        "running" bulup kurulumu bir daha kosmazdi ve ajan eksik ortamda
        calisirdi. Care "Ortami yeniden kur" (`destroy`)."""
        kuruldu = threading.Event()

        def kur(argv):
            kuruldu.set()
            return (0, "0123abcd\n", "")

        sahte = _sahte_docker(
            monkeypatch,
            inspect=lambda _argv: (0, "running\n", "") if kuruldu.is_set() else (1, "", ""),
            run=kur, exec=(1, "", "E: Unable to locate package nodejs"),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = "apt-get install -y nodejs"

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "setup_failed"
        assert "nodejs" in str(bilgi.value)
        assert sb._kirik is not None  # noqa: SLF001
        assert sahte.sayi("run") == 1 and sahte.sayi("exec") == 1

    def test_a_hanging_docker_run_is_named_and_marks_the_sandbox(self, tmp_path, monkeypatch):
        """Kopuk bind mount'ta `docker run` dusmek yerine TAKILABILIR. Ham
        TimeoutExpired disari cikinca `_kirik` yazilmiyor ve her arac
        cagrisi 180 s daha bekliyordu -- isaretin onlemek icin var oldugu
        surunme. Derin yoklama ayni takilmayi `bind_mount_broken` sayiyor;
        `ensure` da oyle saymali ve yarim kalan konteyneri silmeli."""
        sahte = _sahte_docker(
            monkeypatch, inspect=(1, "", ""),
            run=subprocess.TimeoutExpired(cmd="docker run", timeout=180),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = ""

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "bind_mount_broken"
        assert "timeout 180s" in bilgi.value.args["error"]
        assert "Docker Desktop" in str(bilgi.value)
        assert sb._kirik is not None  # noqa: SLF001
        silme = sahte.ilk("rm")
        assert silme[2:] == ["-f", sb.name], silme

        with pytest.raises(SandboxUnavailable):
            sb.run("echo deneme", timeout=5)
        assert sahte.sayi("run") == 1, "kirik kabin takilan docker run'i yeniden denedi"

    def test_a_hanging_cleanup_does_not_unmask_the_timeout(self, tmp_path, monkeypatch):
        """Takilan daemon'da temizlik (`docker rm -f`) de takilir. Ham
        `subprocess.run` ile ikinci TimeoutExpired except govdesinden HAM
        cikiyordu: `_kirik` bos kaliyor, isaret devre disi, her arac
        cagrisi 180+30 s bekliyordu. OLCULDU (sahte docker: run ve rm
        ikisi de zaman asimi)."""
        sahte = _sahte_docker(
            monkeypatch, inspect=(1, "", ""),
            run=subprocess.TimeoutExpired(cmd="docker run", timeout=180),
            rm=subprocess.TimeoutExpired(cmd="docker rm", timeout=30),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = ""

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "bind_mount_broken"
        assert sb._kirik is not None, "takilan temizlik isareti sildi"  # noqa: SLF001
        assert sahte.sayi("rm") == 1, "temizlik hic denenmedi"

        with pytest.raises(SandboxUnavailable):
            sb.run("echo deneme", timeout=5)
        assert sahte.sayi("run") == 1 and sahte.sayi("rm") == 1

    def test_a_hanging_docker_start_keeps_the_existing_container(self, tmp_path, monkeypatch):
        """`start` takilinca konteyner SILINMEZ: o zaten vardi ve icindeki
        kurulum kullanicinin acik "yeniden kur" karari olmadan gitmemeli.
        Hata yine adli ve isaret yine yazilir."""
        sahte = _sahte_docker(
            monkeypatch, inspect=(0, "exited\n", ""),
            start=subprocess.TimeoutExpired(cmd="docker start", timeout=180),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "bind_mount_broken"
        assert sb._kirik is not None  # noqa: SLF001
        assert sahte.sayi("rm") == 0, "var olan konteyner zaman asiminda silindi"

    def test_a_hanging_inspect_is_the_daemon_not_answering(self, tmp_path, monkeypatch):
        """`inspect` 30 s yanit vermiyorsa daemon fiilen kapali; ham
        TimeoutExpired yerine `daemon_down` ve isaret."""
        sahte = _sahte_docker(
            monkeypatch,
            inspect=subprocess.TimeoutExpired(cmd="docker inspect", timeout=30),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))

        with pytest.raises(SandboxUnavailable) as bilgi:
            sb.ensure()
        assert bilgi.value.key == "daemon_down"
        assert sb._kirik is not None  # noqa: SLF001
        assert sahte.sayi("run") == 0


class TestEsZamanliEnsure:
    """Iki is parcacigi ayni konteyneri iki kez kurmaya kalkmamali.

    `run_command` ve `start_service` ayni anda gelince ikisi de "konteyner
    yok" goruyor ve ikisi de `docker run` cagiriyordu; ikincisi "name is
    already in use" ile dusuyordu -- ilk kosunun ilk turunda.
    """

    def test_two_threads_build_one_container(self, tmp_path, monkeypatch):
        kuruldu = threading.Event()

        def kur(_argv):
            time.sleep(0.3)   # kurulum surer; ikinci is parcacigi bu sirada gelir
            kuruldu.set()
            return (0, "0123abcd\n", "")

        sahte = _sahte_docker(
            monkeypatch,
            inspect=lambda _argv: (0, "running\n", "") if kuruldu.is_set() else (1, "", ""),
            run=kur,
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = ""
        hatalar: list[BaseException] = []

        def dene() -> None:
            try:
                sb.ensure()
            except BaseException as exc:  # noqa: BLE001 - testin topladigi hata
                hatalar.append(exc)

        isler = [threading.Thread(target=dene, daemon=True) for _ in range(2)]
        for is_ in isler:
            is_.start()
        for is_ in isler:
            is_.join(timeout=10)

        assert not any(is_.is_alive() for is_ in isler), "ensure asili kaldi"
        assert hatalar == []
        assert sahte.sayi("run") == 1, f"konteyner {sahte.sayi('run')} kez kuruldu"

    def test_the_setup_command_reenters_ensure_without_deadlock(self, tmp_path, monkeypatch):
        """`sandbox_setup` `run()` ile kosar ve `run()` `ensure()`e geri
        doner. OLCULDU: duz `threading.Lock` bu yolda kendi kendini
        kilitliyor (2 s icinde bitmedi), `RLock` aninda bitiyor."""
        kuruldu = threading.Event()

        def kur(_argv):
            kuruldu.set()
            return (0, "0123abcd\n", "")

        sahte = _sahte_docker(
            monkeypatch,
            inspect=lambda _argv: (0, "running\n", "") if kuruldu.is_set() else (1, "", ""),
            run=kur, exec=(0, "kuruldu\n", ""),
        )
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = "echo kurulum"

        is_ = threading.Thread(target=sb.ensure, daemon=True)
        is_.start()
        is_.join(timeout=5)

        assert not is_.is_alive(), "kurulum komutu ensure kilidinde asili kaldi"
        assert sahte.sayi("run") == 1 and sahte.sayi("exec") == 1

    def test_a_waiting_thread_learns_the_failure_without_retrying(self, tmp_path, monkeypatch):
        """Birinci is parcacigi kabini kirik isaretledi; kilidi bekleyen
        ikincisi docker'a bir daha gitmemeli."""
        def kur(_argv):
            time.sleep(0.3)
            return (125, "", _WSL_MOUNT_HATASI)

        sahte = _sahte_docker(monkeypatch, inspect=(1, "", ""), run=kur)
        sb = _sandbox(tmp_path, Settings(workspace=tmp_path))
        sb.setup = ""
        hatalar: list[BaseException] = []

        def dene() -> None:
            try:
                sb.ensure()
            except BaseException as exc:  # noqa: BLE001 - testin topladigi hata
                hatalar.append(exc)

        isler = [threading.Thread(target=dene, daemon=True) for _ in range(2)]
        for is_ in isler:
            is_.start()
        for is_ in isler:
            is_.join(timeout=10)

        assert len(hatalar) == 2
        assert all(isinstance(h, SandboxUnavailable) for h in hatalar)
        assert sahte.sayi("run") == 1, "ikinci is parcacigi docker'a yeniden gitti"


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
