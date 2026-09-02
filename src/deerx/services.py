"""Uzun omurlu yerel surecler: ajanin yazdigi uygulamayi ayakta tutmak.

`run_command` bir komutu calistirir ve BITMESINI bekler; zaman asiminda surec
agacini oldurur. Test kosmak icin dogru davranis, bir dev sunucusu icin
yanlis: sunucu hic bitmez, dolayisiyla ya cagriyi bloke eder ya da zaman
asiminda oldurulur.

Olculdu (Windows): `python srv.py` sekiz saniyede oldurulup port kapandi;
`python srv.py &` -- cmd.exe'de `&` arka plan isleci degil komut ayiraci
oldugu icin -- yine bloke edip oldurulldu; `start /b ...` izin listesinde
`start` olmadigi icin reddedildi. Yani ajanin uygulamasini iki arac cagrisi
arasinda ayakta tutmasinin hicbir yolu yoktu, oysa `preview_open` "once
`run_command` ile arka planda baslatin" diyordu.

Buradaki yonetici sureci KOPUK baslatir ve ciktisini bir DOSYAYA yazar.
Dosya onemli: boru kullanilirsa ebeveyn borulari okumak zorunda kalir ve
asil bloke eden sey odur.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ToolError
from .i18n import t
from .logging import get_logger
from .process import child_env, kill_tree, spawn_flags

log = get_logger("services")

# Ayni anda kac servis. Sinir, unutulmus sureclerin birikmesini onler.
MAX_SERVICES = 6
# Port dinlemeye baslayana kadar beklenecek varsayilan sure.
DEFAULT_READY_SECONDS = 90
# Port yoklama araligi.
_POLL = 0.25


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """Port dinleniyor mu?"""
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


@dataclass
class Service:
    """Calisan tek bir arka plan sureci."""

    name: str
    command: str
    cwd: Path
    log_path: Path
    port: int | None = None
    started_at: float = field(default_factory=time.time)
    process: Any = None

    @property
    def pid(self) -> int:
        return int(getattr(self.process, "pid", 0) or 0)

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return None if self.process is None else self.process.poll()

    def tail(self, lines: int = 60) -> str:
        """Gunlugun son satirlari."""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "pid": self.pid,
            "port": self.port,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "uptime": round(time.time() - self.started_at, 1),
            "log": str(self.log_path),
        }


class ServiceManager:
    """Kosu boyunca yasayan arka plan sureclerini tutar ve kapatir.

    Servisler KOSUYA baglidir. Kosu bitince hepsi kapatilir: yarim kalmis
    bir dev sunucusunun portu tutmaya devam etmesi, bir sonraki kosuyu
    "port dolu" ile karsilar ve sebebi gorunmez olur.
    """

    def __init__(self, log_dir: Path, events: Any = None, sandbox: Any = None) -> None:
        self.log_dir = log_dir
        self.events = events
        # Verilirse servisler konakta degil bu konteynerde kosar. `run_command`
        # zaten orada kosuyor; ikisi ayri yerde olsaydi konteynerde kurulan
        # bagimlilik konakta bulunmaz ve ajan neyin nerede oldugunu bilemezdi.
        self.sandbox = sandbox
        self._services: dict[str, Service] = {}

    # ------------------------------------------------------------------ #
    # Yasam dongusu
    # ------------------------------------------------------------------ #
    def _port_acik(self, port: int) -> bool:
        """Yalitilmis kipte port KONTEYNERIN ICINDEN yoklanir.

        Konaktan bakmak yaniltir: yayinlanan portlari Docker zaten dinler.
        """
        if self.sandbox is not None:
            return self.sandbox.port_acik(port)
        return port_open(port)

    def start(
        self,
        *,
        name: str,
        command: str,
        cwd: Path,
        port: int | None = None,
        ready_seconds: int = DEFAULT_READY_SECONDS,
    ) -> Service:
        """Sureci kopuk baslatir; port verilmisse dinlemeye baslamasini bekler."""
        self.reap()

        mevcut = self._services.get(name)
        if mevcut is not None and mevcut.alive:
            raise ToolError(
                t(
                    "service.already_running",
                    name=name,
                    pid=mevcut.pid,
                    port=f", port {mevcut.port}" if mevcut.port else "",
                )
            )

        if len(self.running()) >= MAX_SERVICES:
            raise ToolError(t("service.too_many", limit=MAX_SERVICES))

        # Port zaten doluysa baslatmanin anlami yok: surec ya hemen olur ya
        # da sessizce baska bir porta duser. Ikisi de yaniltir.
        if port is not None and self._port_acik(port):
            raise ToolError(t("service.port_busy", port=port))

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{_safe(name)}.log"
        # Her baslatmada temiz gunluk: onceki kosunun satirlari bu kosunun
        # hatasi sanilmasin.
        handle = log_path.open("w", encoding="utf-8", errors="replace")

        # Yalitilmis kipte surec konteynerde kosar. `docker exec` BAGLI
        # calistirilir: icerideki surec olunce bu da olur, boylece
        # `service.alive` ve gunluk akisi degismeden calisir.
        if self.sandbox is not None:
            # PID dosyasi: `pkill -f` ile isaret aramak ISE YARAMADI --
            # olculdu, `-e` ile konan ortam degiskeni surecin KOMUT
            # SATIRINDA gorunmez, dolayisiyla eslesmiyordu ve servis
            # durdurulduktan sonra portu tutmaya devam ediyordu.
            #
            # `exec` kabugu komutun kendisiyle degistirdigi icin `$$`
            # nihai surecin pid'idir. Dosya baglanan calisma alanina
            # yazilir; hem konteyner hem konak okuyabilir.
            pid_yolu = f"{self.sandbox.ic_yol(self.log_dir)}/{_safe(name)}.pid"
            sarmal = f"echo $$ > {pid_yolu}; exec {command}"
            argv: Any = [
                "docker", "exec",
                "-w", self.sandbox.ic_yol(cwd), self.sandbox.name,
                "sh", "-lc", sarmal,
            ]
            kabuk = False
        else:
            argv = command
            kabuk = True

        try:
            process = subprocess.Popen(  # noqa: S602 - politika + onay kapisindan gecti
                argv,
                shell=kabuk,
                cwd=str(cwd),
                stdout=handle,
                stderr=subprocess.STDOUT,
                # Etkilesimli bir komut girdi beklerse sonsuza kadar asili
                # kalir; okuyacak bir sey olmadigini bastan soyluyoruz.
                stdin=subprocess.DEVNULL,
                env=child_env(),
                **spawn_flags(),  # type: ignore[arg-type]
            )
        except OSError as exc:
            handle.close()
            raise ToolError(t("service.start_failed", error=exc)) from exc
        finally:
            # Dosya tanimlayicisi cocuga gecti; ebeveynin kopyasi kapanabilir.
            handle.close()

        service = Service(
            name=name, command=command, cwd=cwd, log_path=log_path,
            port=port, process=process,
        )
        self._services[name] = service
        self._emit("tool", "service", t("service.started", name=name, command=command[:80]))

        self._await_ready(service, ready_seconds)
        return service

    def _await_ready(self, service: Service, ready_seconds: int) -> None:
        """Servisin gercekten kalktigini dogrular.

        Port verilmisse dinlemeye baslamasini bekler. Port yoksa yalnizca
        surecin ilk saniyeyi atlattigina bakariz: yanlis komut, eksik
        bagimlilik ve sozdizimi hatasi hemen dusen bir surec olarak
        gorunur ve "baslatildi" demek yaniltici olurdu.
        """
        deadline = time.time() + (ready_seconds if service.port else 1.5)
        while time.time() < deadline:
            if not service.alive:
                cikti = service.tail(30)
                self.forget(service.name)
                raise ToolError(
                    t(
                        "service.exited_immediately",
                        code=service.exit_code,
                        log=(
                            f"--- {t('service.log_at', path='')}---\n{cikti}"
                            if cikti.strip()
                            else t("service.log_empty")
                        ),
                    )
                )
            if service.port is None:
                time.sleep(_POLL)
                continue
            if self._port_acik(service.port):
                self._emit(
                    "tool",
                    "service",
                    t("service.ready", name=service.name, port=service.port),
                )
                return
            time.sleep(_POLL)

        if service.port is None:
            self._emit(
                "tool", "service",
                t("service.running", name=service.name, pid=service.pid),
            )
            return

        cikti = service.tail(30)
        raise ToolError(
            t(
                "service.not_listening",
                port=service.port,
                seconds=ready_seconds,
                pid=service.pid,
                log=cikti if cikti.strip() else t("service.log_empty"),
            )
        )

    def stop(self, name: str) -> bool:
        """Servisi ve alt sureclerini durdurur."""
        service = self._services.get(name)
        if service is None:
            return False
        canliydi = service.alive
        # Agac KOSULSUZ oldurulur. `alive` yalnizca DOGRUDAN cocuga bakar ve
        # bu, temizligi atlamak icin yeterli bir gerekce degil: `shell=True`
        # araya bir kabuk koyar, asil sunucu onun torunudur. Kabuk once
        # olunce `alive` False oluyor, temizlik tumden atlaniyor ve sunucu
        # portu tutmaya devam ediyordu -- olculdu, tek test kosusu iki
        # surec birakti.
        kill_tree(service.pid)
        if self.sandbox is not None:
            # Yerel `docker exec` istemcisini oldurmek konteynerdeki
            # sureci OLDURMEZ; yoksa port dolu kalir ve ajan "port
            # kullanimda" hatasini anlamlandiramaz.
            self.sandbox.ic_oldur(
                f"{self.sandbox.ic_yol(self.log_dir)}/{_safe(name)}.pid"
            )
        if canliydi:
            try:
                service.process.wait(timeout=10)
            except Exception:  # noqa: BLE001 - agac zaten olmus olabilir
                pass
            self._emit("tool", "service", t("service.stopped", name=name))
        self._services.pop(name, None)
        return True

    def stop_all(self) -> list[str]:
        """Hepsini durdurur; kosu sonunda cagrilir."""
        durdurulan = []
        for name in list(self._services):
            if self.stop(name):
                durdurulan.append(name)
        return durdurulan

    def forget(self, name: str) -> None:
        """Kaydi siler ve arkada kalmis torunlari oldurur.

        "Surec zaten olmus" tek basina YETERLI bir gerekce degildi:
        `alive` yalnizca DOGRUDAN cocuga bakar. `shell=True` araya bir
        kabuk koyar ve asil sunucu onun torunudur; kabuk once oldugunde
        kayit dusuruluyor, sunucu ise portu tutmaya devam ediyordu ve
        artik onu kimse tanimadigi icin hicbir sey kapatamiyordu.
        """
        service = self._services.pop(name, None)
        if service is not None:
            kill_tree(service.pid)

    def reap(self) -> None:
        """Kendiliginden olmus servisleri kayittan dusurur."""
        for name, service in list(self._services.items()):
            if not service.alive:
                self._emit(
                    "warn",
                    "service",
                    t("service.died", name=name, code=service.exit_code),
                )
                # `forget` uzerinden: kayit dusmeden once agac oldurulur.
                self.forget(name)

    # ------------------------------------------------------------------ #
    # Sorgu
    # ------------------------------------------------------------------ #
    def get(self, name: str | None) -> Service:
        """Adi verilen servis; ad verilmezse tek calisan servis."""
        if name:
            service = self._services.get(name)
            if service is None:
                raise ToolError(
                    t(
                        "service.unknown",
                        name=name,
                        running=", ".join(s.name for s in self.running()) or "-",
                    )
                )
            return service
        calisan = self.running()
        if not calisan:
            raise ToolError(t("service.none"))
        if len(calisan) > 1:
            raise ToolError(
                t("service.ambiguous", running=", ".join(s.name for s in calisan))
            )
        return calisan[0]

    def running(self) -> list[Service]:
        return [s for s in self._services.values() if s.alive]

    def describe_all(self) -> list[dict[str, Any]]:
        return [s.describe() for s in self._services.values()]

    # ------------------------------------------------------------------ #
    def _emit(self, level: str, source: str, message: str) -> None:
        log.info(message)
        if self.events is not None:
            self.events.emit(level, source, message)


def _safe(name: str) -> str:
    """Gunluk dosyasi adi icin guvenli hale getirir."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60] or "service"
