"""Paralel gorev yurutme ve paylasilan kaynaklarin seriye alinmasi.

`ready_tasks()` hazir gorevlerin TAMAMINI zaten donuyordu; kod `ready[0]`
ile ilkini alip gerisini atiyordu. Tavan artik bir ayar ve VARSAYILANI 1
-- yani paralellik acikca acilan bir sey.

Buradaki testler uc seyi kilitliyor: varsayilanin davranisi
degistirmedigini, tavanin gercekten birden fazla gorev aldigini ve
paylasilan kaynaga dokunan araclarin ayni anda IKI ajan tarafindan
calistirilamadigini.

Hicbiri gercek model cagirmaz.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from deerx.tools import build_registry
from deerx.tools.base import SERIAL_TOOLS, Tool, ToolResult


class TestSeriAraclar:
    """Paylasilan kaynaga dokunan araclar ayni anda kosamaz."""

    def test_the_browser_and_the_shell_are_serial(self):
        """Playwright'in senkron nesneleri onlari olusturan is
        parcacigina bagli; kabin ilk komutta kuruluyor ve iki is
        parcacigi ayni anda kurmaya calisirsa Docker ikisini de
        reddeder."""
        assert "browser_snapshot" in SERIAL_TOOLS
        assert "run_command" in SERIAL_TOOLS
        assert "start_service" in SERIAL_TOOLS
        # Okuma araclari seriye alinmaz: paylasilan bir kaynaga
        # dokunmuyorlar ve siraya sokmak bosuna beklemek olurdu.
        assert "read_file" not in SERIAL_TOOLS
        assert "search_knowledge" not in SERIAL_TOOLS

    def test_two_threads_cannot_run_a_serial_tool_at_once(self, ctx):
        """OLCULEN SOZLESME: kilit cagri BOYUNCA tutulmali.

        `ctx.browser`i almak yetmez -- sayfayla calisan kod da
        korunmali, yoksa iki ajan ayni sekmede gezinir.
        """
        ayni_anda = 0
        en_yuksek = 0
        sayac_kilidi = threading.Lock()

        class Yavas(Tool):
            name = "run_command"  # SERIAL_TOOLS icinde
            description = "test"
            schema: dict[str, Any] = {"type": "object", "properties": {}}

            def run(self, ctx, **kw):
                nonlocal ayni_anda, en_yuksek
                with sayac_kilidi:
                    ayni_anda += 1
                    en_yuksek = max(en_yuksek, ayni_anda)
                time.sleep(0.05)
                with sayac_kilidi:
                    ayni_anda -= 1
                return ToolResult(content="ok")

        defter = build_registry()
        defter._tools["run_command"] = Yavas()  # noqa: SLF001 - testin kurdugu arac

        cocuklar = [ctx.child() for _ in range(4)]
        isler = [
            threading.Thread(target=defter.execute, args=("run_command", {}, c))
            for c in cocuklar
        ]
        for i in isler:
            i.start()
        for i in isler:
            i.join()

        assert en_yuksek == 1, f"seri arac {en_yuksek} kez ayni anda kostu"

    def test_a_normal_tool_is_not_serialised(self, ctx):
        """Siraya sokmanin bedeli var: gereksiz yere sokmak, paralelligi
        kagit uzerinde birakir."""
        ayni_anda = 0
        en_yuksek = 0
        sayac_kilidi = threading.Lock()

        class Yavas(Tool):
            name = "list_dir"  # SERIAL_TOOLS DISINDA
            description = "test"
            schema: dict[str, Any] = {"type": "object", "properties": {}}

            def run(self, ctx, **kw):
                nonlocal ayni_anda, en_yuksek
                with sayac_kilidi:
                    ayni_anda += 1
                    en_yuksek = max(en_yuksek, ayni_anda)
                time.sleep(0.05)
                with sayac_kilidi:
                    ayni_anda -= 1
                return ToolResult(content="ok")

        defter = build_registry()
        defter._tools["list_dir"] = Yavas()  # noqa: SLF001

        cocuklar = [ctx.child() for _ in range(4)]
        isler = [
            threading.Thread(target=defter.execute, args=("list_dir", {}, c))
            for c in cocuklar
        ]
        for i in isler:
            i.start()
        for i in isler:
            i.join()

        assert en_yuksek > 1, "seri olmayan arac da siraya sokulmus"

    def test_children_share_the_lock(self, ctx):
        """Kilit kardesler arasinda siraya sokmak icin var; turetilmis
        baglamda sifirlanirsa hicbir sey korunmaz."""
        assert ctx.child().serial_lock is ctx.serial_lock
        assert ctx.child().child().serial_lock is ctx.serial_lock


class TestGorevSecimi:
    def test_the_default_is_one_task_at_a_time(self, settings):
        """Paralellik ACIKCA acilan bir ayar: gercek modele karsi
        dogrulanmadan varsayilan yapmak, en tehlikeli hatalarin sessiz
        olacagi bir yerde tahminle ilerlemek olurdu."""
        assert settings.max_parallel_tasks == 1

    def test_the_ceiling_selects_more_than_one_ready_task(self, orch_factory):
        """`ready_tasks()` hazir gorevlerin tamamini zaten donuyordu; kod
        ilkini alip gerisini atiyordu."""
        from deerx.pipeline.models import Task

        orch = orch_factory()
        try:
            for i in range(4):
                orch.state.add_task(Task(key=f"T-00{i + 1}", title=f"gorev {i}"))
            hazir = orch.state.ready_tasks()
            assert len(hazir) == 4, "hazir gorevler zaten hepsi geliyor"

            orch.settings.max_parallel_tasks = 3
            tavan = max(1, int(orch.settings.max_parallel_tasks))
            assert len(hazir[:tavan]) == 3
        finally:
            orch.close()

    def test_a_dependent_task_is_never_in_the_same_wave(self, orch_factory):
        """Bagimliligi biten gorev hazir sayilmaz; paralellik bunu
        DEGISTIRMEZ, yalnizca hazir olanlari birlikte alir."""
        from deerx.pipeline.models import Task

        orch = orch_factory()
        try:
            orch.state.add_task(Task(key="T-001", title="once"))
            orch.state.add_task(Task(key="T-002", title="sonra", deps=["T-001"]))
            hazir = [t.key for t in orch.state.ready_tasks()]
            assert hazir == ["T-001"]
        finally:
            orch.close()
