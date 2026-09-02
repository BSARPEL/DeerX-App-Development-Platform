"""Arka plan servisleri: yazdigin uygulamayi calistir, ac, bak.

`run_command` biten komutlar icindir (test, derleme, kurulum). Bir dev
sunucusu bitmez; onu `run_command` ile baslatmak ya cagriyi bloke eder ya da
zaman asiminda oldurur. Bu araclar sureci kopuk baslatir ve kosu boyunca
ayakta tutar.
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..i18n import t
from ..services import DEFAULT_READY_SECONDS, ServiceManager
from .base import Tool, ToolContext, ToolResult
from .shell import check_command


def _manager(ctx: ToolContext) -> ServiceManager:
    if ctx.services is None:
        raise ToolError(t("service.no_manager"))
    return ctx.services


class StartService(Tool):
    name = "start_service"
    description = """
    Uygulamayi arka planda baslatir ve kosu boyunca ayakta tutar.

    `run_command`'dan farki: bitmesini beklemez. Dev sunucusu, API, worker --
    bitmeyen her sey buradan baslatilir.

    `port` verirseniz o port dinlemeye baslayana kadar beklenir; boylece
    "baslatildi" demek "gercekten hazir" demek olur. Surec hemen olurse
    gunlugun sonu hata olarak doner.

    Sonra: `preview_open` ile acin, `browser_snapshot` ile gezin,
    `browser_console` ile hata var mi bakin, `browser_screenshot` ile kanit
    birakin. Yaptiginizi gormeden bittigini soylemeyin.
    """
    dangerous = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Baslatma komutu, or. `npm run dev`."},
            "port": {
                "type": "integer",
                "description": "Dinlemesi beklenen yerel port. Web uygulamalari icin verin.",
            },
            "name": {
                "type": "string",
                "description": "Servis adi (varsayilan: `app`). Birden fazla servis icin ayirt eder.",
            },
            "cwd": {"type": "string", "description": "Calisma dizini (calisma alanina gore)."},
            "ready_seconds": {
                "type": "integer",
                "description": f"Portun acilmasi icin beklenecek sure (varsayilan {DEFAULT_READY_SECONDS}).",
            },
        },
        "required": ["command"],
    }

    def run(
        self,
        ctx: ToolContext,
        command: str,
        port: int | None = None,
        name: str = "app",
        cwd: str = ".",
        ready_seconds: int = DEFAULT_READY_SECONDS,
    ) -> ToolResult:
        # Uzun omurlu bir surec baslatmak, tek seferlik bir komuttan daha az
        # tehlikeli degil: ayni politika kapisindan gecer.
        command = check_command(
            ctx.settings.shell, command,
            yalitilmis=ctx.settings.execution == "docker",
        )

        if port is not None:
            try:
                port = int(port)
            except (TypeError, ValueError):
                raise ToolError(t("browser.bad_port", port=port)) from None
            if not 1 <= port <= 65535:
                raise ToolError(t("browser.port_range", port=port))
            if ctx.settings.execution == "docker":
                # Docker portlari konteyner KURULURKEN ayirir; sonradan
                # eklenemez. Yayinlanmamis bir port secilirse servis
                # konteynerde calisir ama konaktaki tarayici ona hicbir
                # zaman ulasamaz -- ve bunu kimse soylemezse ajan uygulamayi
                # bozuk saniyor. Olculdu: `--network host` bu makinede
                # konaga acilmiyor, tek yol yayinlanan aralik.
                ilk = ctx.settings.sandbox_port_base
                son = ilk + ctx.settings.sandbox_port_count - 1
                if not ilk <= port <= son:
                    raise ToolError(
                        t("sandbox.port_outside_range", port=port, first=ilk, last=son)
                    )

        workdir = ctx.resolve_path(cwd, must_exist=True)
        if not workdir.is_dir():
            raise ToolError(t("fs.not_a_dir", path=cwd))

        ctx.approve(
            t("shell.approve_service", command=command[:160]),
            t(
                "shell.approve_service_detail",
                cwd=ctx.relative(workdir),
                port=f"\nPort: {port}" if port else "",
            ),
            signature=f"service:{command}",
        )

        service = _manager(ctx).start(
            name=name or "app",
            command=command,
            cwd=workdir,
            port=port,
            ready_seconds=int(ready_seconds or DEFAULT_READY_SECONDS),
        )

        satirlar = [
            t("service.start_ok", name=service.name, pid=service.pid),
            t("service.log_at", path=ctx.relative(service.log_path)),
        ]
        if service.port:
            satirlar.append(
                t("service.address", url=f"http://127.0.0.1:{service.port}")
            )
            satirlar.append(t("service.open_hint", port=service.port))
        cikti = service.tail(15)
        if cikti.strip():
            satirlar.append(t("service.first_lines", log=cikti))
        return ToolResult(content="\n".join(satirlar), data=service.describe())


class ServiceLog(Tool):
    name = "service_log"
    description = """
    Calisan bir servisin ciktisini okur.

    Sayfa bos geldiginde, istek 500 dondugunde ya da derleme sessizce
    basarisiz oldugunda sebep buradadir. Tarayicida gorduklerinizle
    sunucunun soyledigini birlikte okuyun.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Servis adi; tek servis varsa gerekmez."},
            "lines": {"type": "integer", "description": "Son kac satir (varsayilan 80)."},
        },
    }

    def run(
        self, ctx: ToolContext, name: str | None = None, lines: int = 80
    ) -> ToolResult:
        service = _manager(ctx).get(name)
        lines = max(1, min(int(lines or 80), 500))
        cikti = service.tail(lines)
        durum = (
            t("service.state_running")
            if service.alive
            else t("service.state_exited", code=service.exit_code)
        )
        basli = t(
            "service.log_header",
            name=service.name, pid=service.pid, state=durum, lines=lines,
        )
        return ToolResult(
            content=basli + (cikti or t("service.log_empty")),
            # Surec olmusse bu bir hatadir: model "calisiyor" varsayimiyla
            # devam etmesin.
            is_error=not service.alive,
            data=service.describe(),
        )


class StopService(Tool):
    name = "stop_service"
    description = """
    Arka plan servisini durdurur (alt surecleriyle birlikte).

    Isi bitince durdurun: portu bosaltir ve bir sonraki baslatma
    "port dolu" ile karsilasmaz. Kosu bitince hepsi zaten kapanir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Servis adi; tek servis varsa gerekmez."},
        },
    }

    def run(self, ctx: ToolContext, name: str | None = None) -> ToolResult:
        manager = _manager(ctx)
        service = manager.get(name)
        manager.stop(service.name)
        return ToolResult(content=t("service.stopped_ok", name=service.name))


class ListServices(Tool):
    name = "list_services"
    description = "Calisan arka plan servislerini listeler."
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext) -> ToolResult:
        manager = _manager(ctx)
        manager.reap()
        hepsi = manager.describe_all()
        if not hepsi:
            return ToolResult(content=t("service.list_empty"))
        satirlar = [
            f"{s['name']}: PID {s['pid']}"
            + (f" · port {s['port']}" if s["port"] else "")
            + f" · {s['uptime']}s · {s['command'][:60]}"
            for s in hepsi
        ]
        return ToolResult(content="\n".join(satirlar), data=hepsi)


SERVICE_TOOLS: list[Tool] = [StartService(), ServiceLog(), StopService(), ListServices()]
