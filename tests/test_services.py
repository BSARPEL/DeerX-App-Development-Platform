"""Arka plan servisleri: ajanin yazdigi uygulamayi ayakta tutabilmesi.

Bu dosyanin varlik sebebi olculmus bir eksiklik: `run_command` bir komutun
BITMESINI bekler ve zaman asiminda surec agacini oldurur, dolayisiyla bir dev
sunucusu iki arac cagrisi arasinda yasayamiyordu. Uc deyim de denenmisti --
duz komut zaman asimina dustu, `python x.py &` Windows'ta komut ayiraci
oldugu icin yine bloke etti, `start /b` izin listesinde olmadigi icin
reddedildi. Oysa `preview_open` "once arka planda baslatin" diyordu.

Buradaki testler sahte degil: gercek surec baslatir, gercek port dinler.
"""

from __future__ import annotations

import socket
import sys
import time

import pytest

from deerx.errors import ToolError
from deerx.i18n import t
from deerx.services import Service, ServiceManager, port_open
from deerx.tools import build_registry
from deerx.tools.base import ToolContext


def bos_port() -> int:
    """Isletim sisteminin verdigi bos bir port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def sunucu_komutu(port: int) -> str:
    """Verilen portu dinleyen en kucuk sunucu."""
    kod = (
        "import http.server,socketserver;"
        "print('hazir',flush=True);"
        f"socketserver.TCPServer(('127.0.0.1',{port}),"
        "http.server.SimpleHTTPRequestHandler).serve_forever()"
    )
    return f'"{sys.executable}" -c "{kod}"'


def bekle(kosul, saniye: float = 6.0) -> bool:
    son = time.time() + saniye
    while time.time() < son:
        if kosul():
            return True
        time.sleep(0.15)
    return False


@pytest.fixture()
def manager(tmp_path):
    m = ServiceManager(log_dir=tmp_path / "services")
    yield m
    m.stop_all()


class TestLifecycle:
    def test_service_outlives_the_call_that_started_it(self, manager, tmp_path):
        """Butun ozelligin sebebi bu tek cumle.

        `run_command` ile baslatilan bir sunucu ya cagriyi bloke ediyor ya da
        zaman asiminda olduruluyordu; ikisi de "uygulamayi acip bakmak"
        imkansiz kiliyordu.
        """
        port = bos_port()
        service = manager.start(
            name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port
        )
        assert service.alive
        # Baslatma cagrisi dondu ve surec hala ayakta: asil garanti bu.
        assert port_open(port)
        assert service.pid > 0

    def test_the_call_returns_only_when_the_port_answers(self, manager, tmp_path):
        """"Baslattim" demek "gercekten hazir" demek olmali."""
        port = bos_port()
        manager.start(name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port)
        # Ayrica beklemeye gerek kalmadan dinleniyor olmali.
        assert port_open(port)

    def test_stop_frees_the_port(self, manager, tmp_path):
        port = bos_port()
        manager.start(name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port)
        manager.stop("web")
        assert bekle(lambda: not port_open(port)), "port bosalmadi"

    def test_stop_all_leaves_nothing_behind(self, manager, tmp_path):
        """Kosu bitince hicbir surec kalmamali.

        Yarim kalmis bir dev sunucusu bir sonraki kosuyu "port dolu" ile
        karsilar ve sebebi gorunmez olur.
        """
        portlar = [bos_port(), bos_port()]
        for i, port in enumerate(portlar):
            manager.start(name=f"s{i}", command=sunucu_komutu(port), cwd=tmp_path, port=port)
        assert len(manager.running()) == 2

        durdurulan = manager.stop_all()
        assert set(durdurulan) == {"s0", "s1"}
        for port in portlar:
            assert bekle(lambda p=port: not port_open(p)), f"{port} bosalmadi"
        assert manager.running() == []


class TestFailureIsVisible:
    def test_a_process_that_dies_reports_its_log(self, manager, tmp_path):
        """Sessizce olen bir servis, calisiyor sanilmaktan iyidir."""
        with pytest.raises(ToolError) as hata:
            manager.start(
                name="olu",
                command=f'"{sys.executable}" -c "import sys;print(\'patladi\');sys.exit(3)"',
                cwd=tmp_path,
            )
        assert "hemen sonlandi" in str(hata.value)
        assert "patladi" in str(hata.value), "gunluk hataya eklenmemis"
        # Olen servis kayitta kalmamali.
        assert manager.running() == []

    def test_a_busy_port_is_refused_before_starting(self, manager, tmp_path):
        """Port doluysa surec ya oldurur ya sessizce baska porta duser."""
        port = bos_port()
        manager.start(name="ilk", command=sunucu_komutu(port), cwd=tmp_path, port=port)
        with pytest.raises(ToolError, match="zaten kullaniliyor"):
            manager.start(name="ikinci", command=sunucu_komutu(port), cwd=tmp_path, port=port)

    def test_the_same_name_twice_is_refused(self, manager, tmp_path):
        port = bos_port()
        manager.start(name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port)
        with pytest.raises(ToolError, match="zaten calisiyor"):
            manager.start(name="web", command=sunucu_komutu(bos_port()), cwd=tmp_path)

    def test_log_is_readable_while_running(self, manager, tmp_path):
        port = bos_port()
        service = manager.start(
            name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port
        )
        assert bekle(lambda: "hazir" in service.tail(20))


class SahteSurec:
    """`Popen` yerine gecen en kucuk nesne: yasiyor ve bir pid'i var."""

    pid = 999_001

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


class SahteKabin:
    """`Sandbox` yerine gecer ve cagri SIRASINI kaydeder.

    Burada olculen sey bir davranis degil bir sira: konteyner
    hazirlanmadan port yoklanirsa yoklama "No such container" ile duser,
    port BOS sanilir ve hemen ardindan `docker exec` ayni hatayla oler.
    Docker'a gerek yok -- sira, docker'in kendisi olmadan da olculur.
    """

    name = "deerx-sbx-sahte"

    def __init__(self, adres: str | None = "all") -> None:
        self.izler: list[str] = []
        self.adres = adres
        self.oldurulenler: list[tuple[str, bool]] = []

    def ensure(self) -> None:
        self.izler.append("ensure")

    def ic_yol(self, yol) -> str:  # noqa: ANN001 - testin sahtesi
        return "/workspace"

    def port_acik(self, port: int) -> bool:
        self.izler.append("port_acik")
        return False

    def dinleme_adresi(self, port: int) -> str | None:
        self.izler.append("dinleme_adresi")
        return self.adres

    def ic_oldur(self, pid_yolu: str, *, grup: bool = False) -> None:
        self.oldurulenler.append((pid_yolu, grup))


@pytest.fixture()
def yalitilmis(tmp_path, monkeypatch):
    """Docker'siz "yalitilmis kip": kabin, `Popen` ve agac oldurme sahte."""
    import subprocess as _subprocess

    import deerx.services as servis_modulu

    kabin = SahteKabin()

    def sahte_popen(*_a, **_kw):
        kabin.izler.append("popen")
        return SahteSurec()

    monkeypatch.setattr(_subprocess, "Popen", sahte_popen)
    # `kill_tree` konakta `taskkill /F /PID` calistirir; sahte pid gercek
    # bir surece denk gelirse testin bedeli makinede odenir.
    monkeypatch.setattr(servis_modulu, "kill_tree", lambda pid: None)
    # Gecis suresi gercekte iki saniye; testin beklemesine gerek yok.
    monkeypatch.setattr(servis_modulu, "_LOOPBACK_BEKLEME", 0.05)
    yonetici = ServiceManager(log_dir=tmp_path / "services", sandbox=kabin)
    yield yonetici, kabin
    yonetici.stop_all()


class TestDockerHazirlik:
    """Yalitilmis kipte servis baslatmanin sirasi ve reddi.

    Uc olculmus ariza: (1) konteyner hazirlanmadan port yoklaniyordu,
    (2) 127.0.0.1'e baglanan servis "hazir" sayiliyordu, (3) konteyner
    gitmisse yoklama "port bos" diyordu.
    """

    KOMUT = "python -m http.server 8100"

    def test_start_ensures_the_container_first(self, yalitilmis, tmp_path):
        """Port denetimi de surecin kendisi de konteynerin ICINDEN gecer;
        konteyner yoksa ikisi de ayni hatayla duser ve ajan yalnizca olu
        bir surec gorur."""
        yonetici, kabin = yalitilmis

        yonetici.start(
            name="web", command=self.KOMUT, cwd=tmp_path, port=8100, ready_seconds=5
        )

        assert kabin.izler[0] == "ensure", kabin.izler
        assert kabin.izler.index("ensure") < kabin.izler.index("popen")
        # `ensure` kendi kilidini tasiyor; her cagri ucuz ama bir kez yeter.
        assert kabin.izler.count("ensure") == 1

    def test_all_interfaces_is_ready(self, yalitilmis, tmp_path):
        """Dogru baglanan servis reddedilmemeli ve ek bekleme almamali."""
        yonetici, kabin = yalitilmis

        service = yonetici.start(
            name="web", command=self.KOMUT, cwd=tmp_path, port=8100, ready_seconds=5
        )

        assert service.alive and yonetici.running() == [service]
        assert kabin.izler.count("dinleme_adresi") == 1, "gereksiz ikinci bakis"

    def test_a_loopback_only_service_is_refused_with_the_bind_hint(
        self, yalitilmis, tmp_path
    ):
        """Yayinlanan port konteynerin adresine yonlendirilir; geri
        donguye baglanan servis calisir gorunur ama konaktaki tarayici ona
        ulasamaz. "Hazir" demek ajani `preview_open` hatasiyla bas basa
        birakir ve hata uygulamanin kendisindeymis gibi gorunur."""
        yonetici, kabin = yalitilmis
        kabin.adres = "loopback"

        with pytest.raises(ToolError) as bilgi:
            yonetici.start(
                name="web", command=self.KOMUT, cwd=tmp_path, port=8100, ready_seconds=5
            )

        assert "0.0.0.0" in str(bilgi.value), str(bilgi.value)
        assert kabin.izler.count("dinleme_adresi") == 2, (
            "gecis halindeki sunucuya (once gecici soket, sonra asil adres) "
            "ikinci bir bakis taninmadi"
        )
        assert yonetici.running() == [], (
            "reddedilen servis ayakta birakildi; portu tutar ve ajanin "
            "duzeltip yeniden baslatma denemesi 'port dolu' ile karsilanir"
        )
        assert kabin.oldurulenler and kabin.oldurulenler[0][1] is True, (
            "konteyner icindeki surec grubu oldurulmedi"
        )

    def test_a_gone_container_is_named_not_mistaken_for_a_free_port(
        self, yalitilmis, tmp_path
    ):
        """Konteyner silinmisse "port bos" demek sureci baslatir ve o da
        aninda oler; sebep hicbir satirda gorunmez."""
        yonetici, kabin = yalitilmis

        def patla(_port: int) -> bool:
            raise ToolError(t("sandbox.container_gone", name=kabin.name))

        kabin.port_acik = patla

        with pytest.raises(ToolError) as bilgi:
            yonetici.start(
                name="web", command=self.KOMUT, cwd=tmp_path, port=8100, ready_seconds=5
            )

        assert kabin.name in str(bilgi.value), str(bilgi.value)
        assert "popen" not in kabin.izler, "konteyner yokken surec baslatildi"

    def test_the_host_path_is_untouched(self, manager, tmp_path):
        """Konak kipinde ayrim YOK ve olmamali: konaktaki tarayici
        127.0.0.1'e baglanan bir servise de ulasir."""
        port = bos_port()
        service = manager.start(
            name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port
        )
        assert service.alive and port_open(port)


class TestBindHint:
    """0.0.0.0 kurali ajana SOYLENMELI.

    OLCULDU: `sandbox.bind_all_interfaces` katalogda vardi ve hicbir
    yerden cagrilmiyordu -- ajanin bilmesi gereken tek konteyner kurali
    yalnizca sozlukte duruyordu.
    """

    @staticmethod
    def _sonuc(settings, tmp_path, kip: str) -> str:
        from deerx.logging import EventLog

        settings.approval_mode = "auto"
        settings.execution = kip
        service = Service(
            name="app", command="python -m http.server 8100",
            cwd=tmp_path, log_path=tmp_path / "app.log", port=8100,
        )

        class Yonetici:
            """Gercek baslatma bu testin konusu degil; sonuc metni konusu."""

            def start(self, **_kw) -> Service:
                return service

        ctx = ToolContext(
            settings=settings,
            events=EventLog(tmp_path / "events.jsonl"),
            services=Yonetici(),
        )
        return build_registry().get("start_service").run(
            ctx, command="python -m http.server 8100", port=8100
        ).content

    def test_the_docker_result_tells_the_agent_to_bind_all_interfaces(
        self, settings, tmp_path
    ):
        metin = self._sonuc(settings, tmp_path, "docker")
        assert t("sandbox.bind_all_interfaces") in metin
        assert "0.0.0.0" in metin

    def test_the_host_result_does_not_carry_the_container_rule(self, settings, tmp_path):
        """Konakta boyle bir kisit yok; soylemek ajani yaniltir."""
        metin = self._sonuc(settings, tmp_path, "host")
        assert t("sandbox.bind_all_interfaces") not in metin

    def test_bind_all_interfaces_is_actually_used(self):
        """Cagri yerinin kendisi: anahtar yeniden "yalnizca tanim" haline
        donerse bu test kirmizi olur."""
        import inspect as _inspect

        from deerx.tools import services as arac_modulu

        assert "sandbox.bind_all_interfaces" in _inspect.getsource(arac_modulu)


class TestTools:
    """Araclarin kendisi: politika, onay ve hata bildirimi."""

    @pytest.fixture()
    def ctx(self, settings, tmp_path):
        settings.approval_mode = "auto"
        manager = ServiceManager(log_dir=tmp_path / "services")
        from deerx.logging import EventLog

        context = ToolContext(
            settings=settings,
            events=EventLog(tmp_path / "events.jsonl"),
            services=manager,
        )
        yield context
        manager.stop_all()

    def test_start_service_goes_through_the_shell_policy(self, ctx):
        """Uzun omurlu bir surec, tek seferlik bir komuttan tehlikesiz degil."""
        ctx.settings.shell.allow_prefixes = ["python"]
        with pytest.raises(ToolError, match="Izin listesinde"):
            build_registry().get("start_service").run(ctx, command="npm run dev", port=4321)

    def test_start_service_honours_the_deny_list(self, ctx):
        ctx.settings.shell.deny_substrings = ["rm -rf /"]
        ctx.settings.shell.allow_prefixes = []
        with pytest.raises(ToolError, match="yasakli desen"):
            build_registry().get("start_service").run(ctx, command="rm -rf / --now")

    def test_start_service_refuses_a_disabled_shell(self, ctx):
        ctx.settings.shell.enabled = False
        with pytest.raises(ToolError, match="Kabuk erisimi kapali"):
            build_registry().get("start_service").run(ctx, command="python -V")

    def test_service_log_reports_a_dead_service_as_an_error(self, ctx, tmp_path):
        """Model "calisiyor" varsayimiyla devam etmesin."""
        port = bos_port()
        ctx.services.start(
            name="web", command=sunucu_komutu(port), cwd=tmp_path, port=port
        )
        ctx.services.get("web").process.kill()
        assert bekle(lambda: not ctx.services.get("web").alive)
        sonuc = build_registry().get("service_log").run(ctx, name="web")
        assert sonuc.is_error

    def test_naming_is_required_when_several_run(self, ctx, tmp_path):
        for i in range(2):
            port = bos_port()
            ctx.services.start(
                name=f"s{i}", command=sunucu_komutu(port), cwd=tmp_path, port=port
            )
        with pytest.raises(ToolError, match="Birden fazla servis"):
            ctx.services.get(None)


class TestToolContract:
    """Araclarin birbirine isaret ettigi yer dogru olmali."""

    def test_preview_open_points_at_start_service(self):
        """`preview_open` bir sure `run_command` ile baslatmayi soyluyordu;
        o yolla baslatilan bir sunucu zaman asiminda olduruluyordu."""
        from deerx.tools import build_registry

        metin = build_registry().get("preview_open").description
        assert "start_service" in metin
        assert "run_command" in metin, "farkin neden onemli oldugu anlatilmali"

    @pytest.mark.parametrize("rol", ["qa", "frontend", "staging", "backend"])
    def test_building_roles_can_run_what_they_write(self, rol):
        from deerx.tools import TOOLSETS

        assert "start_service" in TOOLSETS[rol]
        assert "service_log" in TOOLSETS[rol]

    @pytest.mark.parametrize("rol", ["qa", "frontend"])
    def test_roles_that_look_at_pages_can_see_page_errors(self, rol):
        """Anlik goruntu sayfanin gorunusunu verir, calistigini degil."""
        from deerx.tools import TOOLSETS

        assert "browser_console" in TOOLSETS[rol]
        assert "browser_screenshot" in TOOLSETS[rol]

    def test_the_researcher_still_cannot_start_processes(self):
        """Okudugu web sayfasi "su sunucuyu baslat" yazabilir."""
        from deerx.tools import TOOLSETS

        for arac in ("start_service", "run_command", "write_file"):
            assert arac not in TOOLSETS["researcher"]


class TestQaPromptDemandsUat:
    """Arac vermek yetmez; ajana kullanmasi soylenmeli."""

    @staticmethod
    def _prompt() -> str:
        from deerx.agents.prompts import PACKAGE_PROMPTS

        return (PACKAGE_PROMPTS / "qa.md").read_text(encoding="utf-8")

    def test_uat_is_part_of_the_job(self):
        metin = self._prompt()
        assert "UAT" in metin
        for arac in ("start_service", "preview_open", "browser_console", "browser_screenshot"):
            assert arac in metin, f"{arac} yonergede gecmiyor"

    def test_evidence_is_required_to_finish(self):
        """Ekran goruntusu olmadan "calisiyor" denmemeli."""
        kabul = self._prompt().split("## Kabul ölçütü", 1)[1]
        assert "ekran görüntüsü" in kabul.lower()


class TestChildEncoding:
    """Alt surec Turkce yazabilmeli.

    Olculdu: tam bir boru hatti kosusunda ajanin calistirdigi
    `python -c "print('Link Kasasi -> ...')"` komutu, ok isareti (U+2192)
    Windows konsol kod sayfasinda (cp1254) olmadigi icin `UnicodeEncodeError`
    ile dustu. Biz okurken utf-8 cozuyorduk ama YAZAN tarafa bunu hic
    soylemiyorduk; ajan kendi kodunda hata aramaya basladi.
    """

    YAZI = "Link Kasası → ölçüm ✓"

    def test_run_command_survives_non_ascii_output(self, settings, tmp_path):
        from deerx.logging import EventLog

        settings.approval_mode = "auto"
        ctx = ToolContext(settings=settings, events=EventLog(tmp_path / "e.jsonl"))
        betik = settings.workspace / "yaz.py"
        betik.write_text(f"print({self.YAZI!r})\n", encoding="utf-8")

        sonuc = build_registry().get("run_command").run(ctx, command=f'"{sys.executable}" yaz.py')
        assert not sonuc.is_error, sonuc.content
        assert "Kasası" in sonuc.content
        assert "→" in sonuc.content

    def test_a_service_can_log_non_ascii(self, manager, tmp_path):
        """Servis gunlugu de ayni sorundan etkileniyordu."""
        betik = tmp_path / "srv.py"
        port = bos_port()
        betik.write_text(
            f"print({self.YAZI!r}, flush=True)\n"
            "import http.server,socketserver\n"
            f"socketserver.TCPServer(('127.0.0.1',{port}),"
            " http.server.SimpleHTTPRequestHandler).serve_forever()\n",
            encoding="utf-8",
        )
        service = manager.start(
            name="tr", command=f'"{sys.executable}" srv.py', cwd=tmp_path, port=port
        )
        assert bekle(lambda: "Kasası" in service.tail(10)), service.tail(10)

    def test_the_environment_pins_utf8(self):
        from deerx.process import child_env

        env = child_env()
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["PYTHONUTF8"] == "1"
