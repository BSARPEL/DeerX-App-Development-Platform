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
from deerx.services import ServiceManager, port_open
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
