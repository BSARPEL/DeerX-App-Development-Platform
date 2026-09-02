"""Yalitilmis calistirma: ajanin komutlari konakta degil konteynerde.

Docker gerektiren testler `docker` yoksa atlanir; gerektirmeyenler her
yerde kosar.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from deerx.config import Settings
from deerx.errors import ToolError
from deerx.logging import EventLog
from deerx.sandbox import CALISMA_ALANI, Sandbox
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
        sb = _sandbox(tmp_path, ayar)
        sb.ensure()
        yield ayar, sb
        sb.close()

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
        sb = _sandbox(tmp_path, ayar)
        sb.ensure()
        try:
            kod = (
                "import socket,sys;s=socket.socket();s.settimeout(3);"
                "sys.exit(0 if s.connect_ex(('host.docker.internal',8791))==0 else 1)"
            )
            assert sb.run(f"python -c {kod!r}", timeout=60).returncode != 0, (
                "konteynerden DeerX arayuzune ulasilabiliyor"
            )
        finally:
            sb.close()

    @pytest.mark.slow
    def test_the_internet_still_works(self, tmp_path):
        """Isirma karsiti: yalitim paket kurmayi da engelleseydi ajan
        hicbir sey gelistiremezdi."""
        ayar = _docker_ayarlari(tmp_path)
        ayar.ensure_dirs()
        sb = _sandbox(tmp_path, ayar)
        sb.ensure()
        try:
            kod = ("import urllib.request;"
                   "print(urllib.request.urlopen('https://pypi.org',timeout=15).status)")
            assert sb.run(f"python -c {kod!r}", timeout=90).returncode == 0
        finally:
            sb.close()
