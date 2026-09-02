"""Ajanin komutlarini konak makinede degil, bir konteynerde calistirir.

Neden
-----
Bugune kadar `run_command` ve `start_service` DOGRUDAN konakta kosuyordu.
Yasananlar bunun bedelini gosterdi:

* Bir ajan komutu `os.kill(pid, CTRL_BREAK_EVENT)` cagirdi ve sekiz saatlik
  kosuyu oldurdu -- konsol paylasildigi icin.
* Kosular arttikca konakta yuzlerce yetim `http.server` sureci birikti.
* Ajan yanlislikla yarattigi dosyayi silmek istedi ve YAPAMADI: silme araci
  yok, kabuk izin listesinde de `rm` yok. Izin listesi konagi korumak icin
  dar tutulmus, ama ajanin mesru islerini de kisitliyor.

Konteynerde bunlarin hicbiri konagi ilgilendirmez: ajan `rm` de calistirir,
paket de kurar, sureci de oldurur; patlama yaricapi konteynerdir ve kosu
bitince `docker rm` ile silinir.

Iki kisit OLCULDU (Windows, Docker 29.7.2)
------------------------------------------
1. `--network host` konteyner portunu Windows konagina ACMAZ. Konteyner
   icinde 18999'u dinleyen bir sunucuya konaktan baglanilamadi.
2. `-p 127.0.0.1:P:P` ile yayinlanan port konaktan ERISILEBILIYOR.

Bu ikisi tasarimi belirliyor: portlar konteyner kurulurken YAYINLANIR ve
ajanin servisleri yalnizca bu araliktan secebilir. Docker yayinlanan portu
sonradan degistiremedigi icin aralik onceden ayrilir.

Ucuncu kisit da ayni olcumden cikti: yayinlanan bir portun ise yaramasi
icin konteyner icindeki servis `0.0.0.0`a baglanmali, `127.0.0.1`e degil.
`start_service` bunu ajana soyler.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ToolError
from .i18n import t
from .logging import get_logger

log = get_logger(__name__)

# Konteyner icindeki calisma alani. Konaktaki yol ne olursa olsun ajan hep
# ayni yeri gorur; uretilen betiklerdeki mutlak yollar makineye baglanmaz.
CALISMA_ALANI = "/workspace"

_OLUSTURMA_ZAMAN_ASIMI = 180


@dataclass(slots=True)
class SandboxSonuc:
    """Konteynerde calistirilmis bir komutun sonucu."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class Sandbox:
    """Bir kosuya ait konteyner. Kosu bitince silinir.

    Konteyner adi calisma alaninin yolundan turetilir: ayni alan icin ayni
    konteyner yeniden kullanilir, farkli alanlar birbirine karismaz.
    """

    def __init__(
        self,
        workspace: Path,
        image: str,
        port_base: int,
        port_count: int,
        memory: str = "2g",
        cpus: float = 2.0,
        pids_limit: int = 512,
        setup: str = "",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.image = image
        self.port_base = int(port_base)
        self.port_count = max(1, int(port_count))
        self.memory = memory
        self.cpus = float(cpus)
        self.pids_limit = int(pids_limit)
        self.setup = (setup or "").strip()
        ozet = hashlib.sha256(str(self.workspace).encode("utf-8")).hexdigest()[:10]
        self.name = f"deerx-sbx-{ozet}"

    # -- yasam dongusu ------------------------------------------------------

    @property
    def port_range(self) -> range:
        return range(self.port_base, self.port_base + self.port_count)

    def portu_kapsiyor(self, port: int) -> bool:
        return port in self.port_range

    def ensure(self) -> None:
        """Konteyner yoksa kurar, durmussa baslatir."""
        if shutil.which("docker") is None:
            raise ToolError(t("sandbox.no_docker"))
        if self._durum() == "running":
            return
        if self._durum() is not None:
            self._docker(["start", self.name], _OLUSTURMA_ZAMAN_ASIMI)
            return

        son = self.port_base + self.port_count - 1
        argv = [
            "run", "-d", "--name", self.name,
            # Calisma alani baglanir: ajanin yazdigi dosyalar konakta da
            # gorunur, boylece `write_file` gibi konak tarafli araclarla ayni
            # dosyalari paylasirlar.
            "-v", f"{self.workspace}:{CALISMA_ALANI}",
            "-w", CALISMA_ALANI,
            # Portlar YALNIZCA konak geri dongusune acilir; aga cikmaz.
            "-p", f"127.0.0.1:{self.port_base}-{son}:{self.port_base}-{son}",
            # Kacak bir ajan konagi yormasin: bellek, cekirdek ve surec
            # sayisi sinirli. Sinirsiz birakilirsa bir fork bombasi ya da
            # bellek doldurma konteynerde kalmaz, MAKINEYI dizustu eder --
            # yalitimin amaci tam olarak bunu onlemek.
            "--memory", self.memory,
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids_limit),
            # `host.docker.internal` Docker Desktop'in konaga acilan kapisi.
            # Olculdu: konteynerden konaktaki vLLM (8008), SearXNG (8890) ve
            # DeerX'in KENDI arayuzune (8791) ulasilabiliyordu -- ajan
            # sandbox'tan cikip DeerX'i surebilirdi. Adi kendine cevirerek
            # kolay yol kapatiliyor. Tam bir ag yalitimi degil (ag gecidi
            # hala yonlendirilebilir) ama kazara ya da merakla bulunan yol
            # bu.
            "--add-host", "host.docker.internal:127.0.0.1",
            self.image,
            "sleep", "infinity",
        ]
        self._docker(argv, _OLUSTURMA_ZAMAN_ASIMI)
        log.info(t("sandbox.created", name=self.name, image=self.image))
        if self.setup:
            # Yalnizca konteyner ILK kuruldugunda; yeniden baslatmada degil.
            log.info(t("sandbox.setup_running"))
            sonuc = self.run(self.setup, timeout=900)
            if sonuc.returncode != 0:
                raise ToolError(
                    t("sandbox.setup_failed",
                      error=(sonuc.stderr or sonuc.stdout).strip()[:300])
                )

    def close(self) -> None:
        """Konteyneri siler. Icindeki her sey gider; kasit budur."""
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            capture_output=True, text=True, check=False, timeout=60,
        )

    def __enter__(self) -> Sandbox:
        self.ensure()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- calistirma ---------------------------------------------------------

    def run(self, command: str, timeout: float, workdir: Path | None = None) -> SandboxSonuc:
        """Komutu konteynerde calistirir.

        `workdir` konaktaki bir yoldur ve calisma alaninin ALTINDA olmalidir;
        konteyner icindeki karsiligina cevrilir.
        """
        self.ensure()
        ic_dizin = self._ic_yol(workdir) if workdir else CALISMA_ALANI
        argv = [
            "docker", "exec", "-w", ic_dizin, self.name,
            # `sh -lc`: ajanin yazdigi komut bir kabuk satiridir; boru,
            # yonlendirme ve `&&` calissin.
            "sh", "-lc", command,
        ]
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxSonuc(
                returncode=124,
                stdout=(exc.stdout or b"").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr="",
                timed_out=True,
            )
        return SandboxSonuc(p.returncode, p.stdout or "", p.stderr or "", False)

    # -- ic yardimcilar -----------------------------------------------------

    def port_acik(self, port: int, timeout: float = 0.4) -> bool:
        """Port KONTEYNERIN ICINDE dinleniyor mu?

        Konaktan bakmak ise yaramaz: portlar konteyner kurulurken yayinlandigi
        icin Docker konak tarafinda zaten dinliyor. Olculdu -- yayinlanmis
        ama icinde hicbir servis olmayan bir portta konak `port_open` TRUE
        donuyordu. "Dolu mu" denetimi her zaman tetiklenir, daha kotusu
        "hazir mi" denetimi servis hic baslamamisken bile hazir derdi.
        """
        kod = (
            f"import socket,sys;s=socket.socket();s.settimeout({float(timeout)!r});"
            f"sys.exit(0 if s.connect_ex(('127.0.0.1',{int(port)}))==0 else 1)"
        )
        p = subprocess.run(
            ["docker", "exec", self.name, "python", "-c", kod],
            capture_output=True, text=True, check=False, timeout=30,
        )
        return p.returncode == 0

    def ic_oldur(self, pid_yolu: str) -> None:
        """PID dosyasindaki sureci konteyner icinde oldurur.

        Yerel `docker exec` istemcisini oldurmek icerideki sureci OLDURMEZ;
        servis calismaya devam eder ve portu tutar. Olculdu.

        Once `pkill -f` ile bir isaret aranmisti; ise yaramadi cunku `-e` ile
        konan ortam degiskeni surecin komut satirinda GORUNMEZ. PID dosyasi
        belirsizlik birakmiyor.
        """
        kod = (
            f"[ -f {pid_yolu} ] || exit 0; "
            f"p=$(cat {pid_yolu}); "
            "kill -TERM $p 2>/dev/null; sleep 0.4; kill -KILL $p 2>/dev/null; "
            f"rm -f {pid_yolu}; exit 0"
        )
        subprocess.run(
            ["docker", "exec", self.name, "sh", "-lc", kod],
            capture_output=True, text=True, check=False, timeout=30,
        )

    def ic_yol(self, yol: Path) -> str:
        """Konaktaki yolun konteyner icindeki karsiligi."""
        return self._ic_yol(yol)

    def _ic_yol(self, yol: Path) -> str:
        """Konaktaki yolu konteyner icindeki karsiligina cevirir."""
        try:
            bagil = Path(yol).resolve().relative_to(self.workspace)
        except ValueError:
            # Calisma alani disi: konteynerde karsiligi yok.
            return CALISMA_ALANI
        return CALISMA_ALANI if str(bagil) == "." else f"{CALISMA_ALANI}/{bagil.as_posix()}"

    def _durum(self) -> str | None:
        """Konteynerin durumu; yoksa None."""
        p = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", self.name],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if p.returncode != 0:
            return None
        return (p.stdout or "").strip() or None

    def _docker(self, argv: list[str], timeout: int) -> str:
        p = subprocess.run(
            ["docker", *argv], capture_output=True, text=True,
            check=False, timeout=timeout,
        )
        if p.returncode != 0:
            raise ToolError(
                t("sandbox.command_failed",
                  argv=" ".join(argv[:3]),
                  error=(p.stderr or p.stdout or "").strip()[:300])
            )
        return (p.stdout or "").strip()
