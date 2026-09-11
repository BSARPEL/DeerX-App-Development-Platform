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
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ToolError
from .i18n import t
from .logging import get_logger

log = get_logger(__name__)

# Konteyner icindeki calisma alani. Konaktaki yol ne olursa olsun ajan hep
# ayni yeri gorur; uretilen betiklerdeki mutlak yollar makineye baglanmaz.
CALISMA_ALANI = "/workspace"

_OLUSTURMA_ZAMAN_ASIMI = 180

# Derin yoklama gercek bir konteyner kaldirir; buyuk bir imajda ilk
# baslatma bile saniyeler surer. Bir dakika, bilinen olguyu (kopuk bind
# mount'ta takilan `docker run`) kesmeye yeter; ayrintisi `Sandbox.probe`.
_PROBE_ZAMAN_ASIMI = 60

# Bir yoklama sonucunun kac saniye gecerli sayildigi. Ortam ekrani her
# acilista docker'a yeniden sormasin; ama Docker Desktop yeniden
# baslatildiktan yarim dakika sonra bunu da gorsun.
_PROBE_ONBELLEK_TTL = 30.0

# Kabinin kurulamama ya da eksik kalma sebepleri. Sunucu yalnizca anahtar
# ve degerleri doner; arayuz her anahtari kendi dilinde aciklar
# (`env.issue.<key>`). Metin dondurulseydi dil degisince eski dildeki
# metin ekranda kalirdi. Her anahtarin `sandbox.<key>` mesaji da var
# (olay akisi ve ToolError icin); tests/test_i18n_py.py bunu kilitler.
SORUN_ANAHTARLARI = frozenset({
    "no_docker", "daemon_down", "bind_mount_broken", "image_missing",
    "image_pull_failed", "port_busy_host", "name_in_use", "node_missing",
    "setup_failed",
})

# Konteyneri KURAN docker komutlari. Yalnizca bunlarin hatasi "kabin
# kurulamiyor" demektir; `exec` ya da `inspect` hatasi degildir.
_KURAN_KOMUTLAR = frozenset({"run", "start", "create"})

# Docker'in stderr'inde aranan kaliplar; hepsi kucuk harf, ilk eslesen
# kazanir. Kaliplar Docker Desktop (Windows/WSL2) ve Linux daemon'un
# gercek metinlerinden; degistiginde `docker_hatasi_cevir` None doner ve
# eski `sandbox.command_failed` yolu devreye girer -- yani en kotu halde
# bugunku davranis.
_HATA_KALIPLARI: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bind_mount_broken", (
        "mnt/host", "error while creating mount source",
        "invalid mount config", "input/output error",
    )),
    ("daemon_down", (
        "cannot connect to the docker daemon", "pipe/docker_engine",
        "error during connect",
    )),
    ("image_pull_failed", ("pull access denied", "manifest unknown", "not found: manifest")),
    ("image_missing", ("no such image", "--pull never")),
    ("port_busy_host", ("port is already allocated", "address already in use")),
    ("name_in_use", ("name is already in use", "conflict. the container name")),
)

# Yoklama araclari: ajanin yonergesi ve Ortam ekrani bunlarin varligini
# soyler. `node`/`npm` ozellikle: varsayilan imaj `python:3.13` ve icinde
# node yok; package.json tasiyan bir projede ajan ilk `npm install`de
# duvara carpiyordu.
_YOKLANAN_ARACLAR = ("python", "node", "npm", "git")

# Konteyner icinde kosan yoklama betigi. Her satir tek bir olcum: bagli
# dizin var mi, hangi araclar var. `exit 0` sart: son aracin `command -v`
# denemesi dusunce `sh -c` de 1 donerdi ve saglam bir kabin "kurulamiyor"
# sayilirdi. Docker'in kendi hatasi (mount, imaj) zaten 125 ile gelir.
_YOKLAMA_BETIGI = (
    f"test -d {CALISMA_ALANI} && echo MOUNT_OK; "
    f"for t in {' '.join(_YOKLANAN_ARACLAR)}; do "
    "command -v $t >/dev/null 2>&1 && echo TOOL_$t; done; exit 0"
)

# `/proc/net/tcp` dorduncu alanindaki durum kodu: TCP_LISTEN. Kurulmus bir
# baglanti (`01`) ayni portu tasir ama dinlemiyordur; ayirmadan bakmak
# kapanmak uzere olan bir istegi "servis hazir" sayardi.
_DINLEME_DURUMU = "0A"

# `/proc/net/tcp6` icinde `::1`. Adres dort 32 bitlik kelime halinde, her
# kelime KENDI ICINDE ters yazilir (x86 little-endian); bu yuzden son bayt
# (0x01) sondan dorduncu basamak cifti olarak gorunur. Ikinci yazim
# big-endian bir makinede (ya da farkli bir cekirdek surumunde) ayni adresi
# duz sirayla verir -- ikisini de tanimak bedava, tanimamak sessiz bir
# "loopback goremedim" demek.
_IPV6_GERI_DONGU = frozenset({
    "00000000000000000000000001000000",
    "00000000000000000000000000000001",
})


def _geri_dongu_mu(adres: str) -> bool:
    """`/proc/net/tcp` onaltilik adresi 127.x.x.x ya da ::1 mi?"""
    ham = adres.upper()
    if len(ham) == 8:
        # IPv4 dort bayti kelime icinde ters yazilir: 127.0.0.1 "0100007F"
        # olur, yani ILK sekizli (0x7F) SONDA durur.
        return ham.endswith("7F")
    if len(ham) == 32:
        if ham in _IPV6_GERI_DONGU:
            return True
        # `::ffff:127.0.0.1` -- IPv4 esleme. Son kelime IPv4 adresidir,
        # ondan onceki kelime `ffff` isaretini tasir.
        return ham[16:24] == "FFFF0000" and ham[24:].endswith("7F")
    return False


def _dinleme_cozumle(metin: str, port: int) -> str | None:
    """Portun konteyner icinde HANGI adrese bagli oldugunu soyler.

    Doner: butun arayuzlere bagliysa `'all'`, yalnizca geri donguye
    bagliysa `'loopback'`, o portu dinleyen yoksa `None`.

    Neden bu sorunun bir cevabi olmali: konteynerde `127.0.0.1`e baglanan
    bir servis calisiyor GORUNUR ama yayinlanan port BOS kalir -- Docker
    yayinlanan portu konteynerin adresine yonlendirir, geri donguye degil.
    Konaktaki tarayici uygulamaya hicbir zaman ulasamaz ve ajan saatlerce
    kendi kodunda hata arar.

    Neden konteynerin ICINDEN: konaktan yoklamak ayrimi hic goremez.
    OLCULDU (`Sandbox.port_acik` docstring'i) -- yayinlanmis ama icinde
    hicbir servis olmayan bir portta konak yoklamasi TRUE donuyordu, cunku
    yayinlanan portu Docker'in kendisi dinler.

    Neden `ss`/`netstat` degil: imajlarin cogunda (python:3.13 dahil)
    ikisi de yok. `/proc/net/tcp` cekirdegin kendi dokumu; `cat` her yerde
    var.

    Saf islev: metni disaridan alir, docker'a hic gitmez -- docker'siz
    test edilebilmesi icin.
    """
    hedef = f"{int(port):04X}"
    geri_dongu = False
    for satir in (metin or "").splitlines():
        alanlar = satir.split()
        # Baslik satiri ("sl local_address ...") burada elenir: dorduncu
        # alani "st" ve durum koduna esit degil.
        if len(alanlar) < 4 or alanlar[3].upper() != _DINLEME_DURUMU:
            continue
        adres, _ayrac, yerel_port = alanlar[1].rpartition(":")
        if yerel_port.upper() != hedef:
            continue
        if not _geri_dongu_mu(adres):
            # Tum arayuzler (0.0.0.0 / ::) ya da konteynerin kendi adresi:
            # ikisinde de yayinlanan port calisir, ayirmaya gerek yok.
            return "all"
        geri_dongu = True
    return "loopback" if geri_dongu else None


def docker_hatasi_cevir(stderr: str) -> tuple[str, dict[str, Any]] | None:
    """Docker'in ham hatasini bir sorun anahtarina cevirir; taninmiyorsa None.

    OLCULDU (bu makine, Docker Desktop + WSL2): calisma alani
    baglanamayinca `docker run` su satirla dusuyordu -- "error while
    creating mount source path '/run/desktop/mnt/host/c/...': mkdir
    /run/desktop/mnt/host/c: file exists". Bu metni oldugu gibi gostermek
    yol gostermez; sebep (Docker Desktop'in konak baglantisi kopmus) ve
    care (yeniden baslat) bir AD ister. Ikinci deger anahtara ozgu ek
    degerlerdir; kaliplarin kendisi deger tasimaz, cagiran doldurur.
    """
    metin = (stderr or "").lower()
    if not metin:
        return None
    # `mkdir ...: file exists` tek bir kalip degil, iki parcanin birlikte
    # gorunmesi; o yuzden tabloda degil burada.
    if "mkdir" in metin and "file exists" in metin:
        return ("bind_mount_broken", {})
    for key, kaliplar in _HATA_KALIPLARI:
        if any(kalip in metin for kalip in kaliplar):
            return (key, {})
    return None


class SandboxUnavailable(ToolError):
    """Kabin kurulamiyor ve bunun bir ADI var.

    Duz `ToolError` ajan dongusunde modele `is_error` olarak doner ve
    dongu surer; oysa docker'a ulasilamiyorsa hicbir komut calismayacak,
    modeli hic cagirmamak gerekir. Orkestrator bu turu yakalar ve kosuyu
    tek bir 'sandbox' olayiyla durdurur. `key` arayuzun cevirdigi sorun
    anahtari (`SORUN_ANAHTARLARI`), `args` o mesajin yer tutuculari.
    """

    def __init__(
        self, message: str, *, key: str = "unavailable",
        args: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.key = key
        self._degerler: dict[str, Any] = dict(args or {})

    @property
    def args(self) -> dict[str, Any]:
        """Yer tutucu degerleri.

        OLCULDU: `self.args = {...}` yazmak `BaseException.args`i (bir
        demet) ezer -- sozluk, anahtarlarinin demetine cevrilir ve
        `str(exc)` mesaj yerine ilk anahtari basar. Ozellik olarak
        tanimlanan `args` yalnizca Python tarafini golgeler; `str`, `repr`
        ve traceback C tarafindaki demeti okumaya devam eder.
        """
        return self._degerler


@dataclass(slots=True)
class SandboxHealth:
    """`Sandbox.probe` sonucu: kabin kurulabilir mi, kurulamiyorsa neden.

    `problems` kosuyu durduran sebepler; `warnings` kosuyu durdurmayan ama
    kullaniciya soylenecekler (imaj henuz cekilmemis, node yok). Ikisi de
    `{"key": ..., "args": {...}}` satirlari tasir; metin yok, arayuz kendi
    dilinde yazar. `tools` yalnizca derin yoklamada dolar; bos sozluk
    "olculmedi" demektir, "yok" degil.
    """

    docker_cli: bool
    daemon: str | None
    container_status: str | None
    image_present: bool | None
    mount_ok: bool | None
    tools: dict[str, bool] = field(default_factory=dict)
    problems: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    checked_at: float = 0.0
    deep: bool = False

    @property
    def status(self) -> str:
        """Ortam ekraninin tek sozcuklu ozeti."""
        if self.problems:
            return "unavailable"
        return self.container_status or "absent"

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "docker_cli": self.docker_cli,
            "daemon": self.daemon,
            "container_status": self.container_status,
            "image_present": self.image_present,
            "mount_ok": self.mount_ok,
            "tools": dict(self.tools),
            "problems": [dict(p) for p in self.problems],
            "warnings": [dict(w) for w in self.warnings],
            "checked_at": self.checked_at,
            "deep": self.deep,
        }


# Yoklama sonuclari konteyner adina gore. Nesne uzerinde tutulsaydi hic
# tutmazdi: arayuz her istekte yeni bir `Sandbox` kuruyor. Deger: (tekduze
# saat, sonuc); tazelik tekduze saatle olculur, `checked_at` ise ekrana
# yazilan duvar saati.
_PROBE_ONBELLEK: dict[str, tuple[float, SandboxHealth]] = {}


@dataclass(slots=True)
class SandboxSonuc:
    """Konteynerde calistirilmis bir komutun sonucu."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


# Konteynerin uzerindeki etiketler. Ad tek basina yalnizca bir ozet;
# hangi calisma alanina ait oldugunu ve DeerX'e ait oldugunu bunlar soyler.
ETIKET = "deerx.sandbox"
ETIKET_ALAN = "deerx.workspace"


class Sandbox:
    """Bir kosuya ait konteyner. Kosu bitince silinir.

    Konteyner adi calisma alaninin yolundan turetilir: ayni alan icin ayni
    konteyner yeniden kullanilir, farkli alanlar birbirine karismaz.
    """

    # Konteyner adina gore kilit. Iki is parcacigi (ornegin `run_command`
    # ile `start_service` ayni anda) ayni konteyneri kurmaya kalkarsa
    # ikincisi "name is already in use" ile duserdi; simdi birincinin
    # bitmesini bekler ve konteyneri hazir bulur. RLock sart: kurulum
    # komutu (`sandbox_setup`) `run()` uzerinden `ensure()`e geri doner ve
    # duz bir Lock kendi kendini kilitlerdi.
    _KILITLER: dict[str, threading.RLock] = {}

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
        # Derin yoklamanin gecici konteyneri; kosunun konteyneriyle ayni
        # ozeti tasir ki hangi projeye ait oldugu belli olsun, ama ayni ad
        # olmasin ki `docker run` "name is already in use" demesin.
        self.probe_name = f"deerx-probe-{ozet[:8]}"
        # Kabin bir kez kurulamadiysa sebebi burada durur; `_kirik_mi` her
        # docker yolunun basinda bakar. `destroy()` ve sorunsuz `probe()`
        # sifirlar.
        self._kirik: SandboxUnavailable | None = None

    # -- yasam dongusu ------------------------------------------------------

    @property
    def port_range(self) -> range:
        return range(self.port_base, self.port_base + self.port_count)

    def portu_kapsiyor(self, port: int) -> bool:
        return port in self.port_range

    def ensure(self) -> None:
        """Konteyner yoksa kurar, durmussa baslatir.

        Kabin kurulamazsa (`SandboxUnavailable`) sebebi `_kirik`e yazilir
        ve sonraki her cagri docker'a gitmeden ayni hatayla doner: aksi
        halde her arac cagrisi ayni `docker run`u yeniden dener, her biri
        saniyeler (kopuk mount'ta bir dakika) bekler ve ayni hatayi uretir.
        """
        self._kirik_mi()
        with self._kilit():
            # Kilidi bekleyen ikinci is parcacigi birincinin bulgusunu
            # kilidi alinca ogrenir; docker'a bir daha gitmez.
            self._kirik_mi()
            try:
                degisti = self._kur()
            except SandboxUnavailable as exc:
                self._kirik = exc
                raise
        if degisti:
            # Konteyner kuruldu ya da baslatildi: eski yoklama sonucu
            # (`absent`/`exited`) artik yalan soyler.
            _PROBE_ONBELLEK.pop(self.name, None)

    def _kilit(self) -> threading.RLock:
        # `dict.setdefault` GIL altinda tek adimda calisir; iki is parcacigi
        # ayni ada iki ayri kilit uretemez.
        return self._KILITLER.setdefault(self.name, threading.RLock())

    def _kirik_mi(self) -> None:
        """Kabin kirik isaretliyse ayni hatayi (yeni bir ornekle) firlatir.

        Ayni ornegi yeniden firlatmak traceback'i her seferinde uzatirdi.
        """
        if self._kirik is not None:
            raise SandboxUnavailable(
                str(self._kirik), key=self._kirik.key, args=self._kirik.args,
            )

    def _kur(self) -> bool:
        """`ensure` govdesi; kilit altinda kosar.

        Konteyner kuruldu ya da baslatildiysa True; zaten calisiyorduysa
        False.
        """
        if shutil.which("docker") is None:
            raise SandboxUnavailable(
                t("sandbox.no_docker"), key="no_docker",
                args=self._sorun_degerleri(""),
            )
        if self._durum() == "running":
            return False
        if self._durum() is not None:
            self._docker(["start", self.name], _OLUSTURMA_ZAMAN_ASIMI)
            return True

        son = self.port_base + self.port_count - 1
        argv = [
            "run", "-d", "--name", self.name,
            # Calisma alani baglanir: ajanin yazdigi dosyalar konakta da
            # gorunur, boylece `write_file` gibi konak tarafli araclarla ayni
            # dosyalari paylasirlar.
            "-v", f"{self.workspace}:{CALISMA_ALANI}",
            "-w", CALISMA_ALANI,
            # Konteyner KIM OLDUGUNU tasisin. Ad, calisma alani yolunun
            # sha256'si: elinizde `deerx-sbx-3f84682fec` varken hangi
            # projeye ait oldugunu ogrenmenin yolu YOKTU -- aday yollari
            # tek tek ozetleyip denemek disinda. Etiket bunu kalici
            # olarak cozer ve yetim konteyneri toplamayi mumkun kilar.
            "--label", f"{ETIKET}=1",
            "--label", f"{ETIKET_ALAN}={self.workspace}",
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
                # Konteyner var ama yarim kurulu; bir sonraki `ensure`
                # onu "running" bulup kurulumu bir daha kosmazdi. Kirik
                # isareti kosuyu durdurur; care "Ortami yeniden kur".
                hata = (sonuc.stderr or sonuc.stdout).strip()[:300]
                raise SandboxUnavailable(
                    t("sandbox.setup_failed", error=hata),
                    key="setup_failed", args=self._sorun_degerleri(hata),
                )
        return True

    def stop(self) -> None:
        """Konteyneri DURDURUR; silmez.

        Kurulum (`sandbox_setup`) yalnizca konteyner ilk kuruldugunda
        kosuyor. Kapanista silmek, her sunucu acilisinda `apt-get install
        nodejs npm` gibi bir kurulumun bastan kosmasi demekti: dakikalar
        ve ag trafigi, hicbir sey degismemis olsa bile. Proje kalici bir
        gelistirme ortamiysa ortami da kalici olmali.

        Diskte kalan durdurulmus bir konteyner ihmal edilebilir yer
        tutar; hicbir sey de calistirmaz.
        """
        _PROBE_ONBELLEK.pop(self.name, None)
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "stop", "-t", "5", self.name],
            capture_output=True, text=True, check=False, timeout=60,
        )

    def destroy(self) -> None:
        """Konteyneri SILER. Icindeki her sey gider; kasit budur.

        Yalnizca proje silindiginde ya da kullanici acikca "ortami
        yeniden kur" dediginde cagrilir. Kirik isareti de burada kalkar:
        "yeniden kur" tam olarak yeniden denemek demek.
        """
        self._kirik = None
        _PROBE_ONBELLEK.pop(self.name, None)
        if shutil.which("docker") is None:
            return
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            capture_output=True, text=True, check=False, timeout=60,
        )

    # Eski ad: kapanista SILMEK yerine DURDURMAK dogru davranis.
    close = stop

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

        Zaman asiminda yerel `docker exec` istemcisi oldurulur ama
        ICERIDEKI surec yasamaya devam eder: portu tutar, CPU yakar ve
        kosu bitene kadar onu kimse tanimaz -- `stop_service` bile, cunku
        o yalnizca kayitli servisleri bilir. OLCULDU. Bu yuzden komut bir
        PID dosyasi yazan sarmalla kosar ve zaman asiminda o pid'in SUREC
        GRUBU oldurulur: `npm test` ya da `a | b` asil isi torunlarda
        yapar, yalnizca pid'i oldurmek portu tutani birakirdi.

        Sarmal `echo $$ > pid; exec sh -c "$1"`. `exec` sart: komut
        kabugun COCUGU olsaydi dosya kabugun pid'ini tasirdi ve oldurme
        yanlis surece giderdi. Komut `$1` olarak gecirilir, `--` ayraci
        YOK -- OLCULDU: `sh -c <betik> -- <komut>` ayracin kendisini `$0`
        yapar ve komut `$1`e kayar; `"$0"` okuyan bir sarmal `--`
        calistirir. `setsid` de yok: `docker exec` sureci zaten oturum
        lideri (pid=pgid=sid), araya setsid koymak fork edip erken
        donuyor ve pid dosyasina olu ebeveynin pid'ini yaziyordu.
        """
        self._kirik_mi()
        self.ensure()
        ic_dizin = self._ic_yol(workdir) if workdir else CALISMA_ALANI
        # Her cagriya ozel ad: ayni anda kosan iki komut birbirinin pid
        # dosyasini ezerse zaman asimi yanlis sureci oldururdu.
        pid_yolu = f"/tmp/deerx-{uuid.uuid4().hex[:12]}.pid"
        sarmal = f'echo $$ > {pid_yolu}; exec sh -c "$1"'
        argv = [
            "docker", "exec", "-w", ic_dizin, self.name,
            # `sh -lc`: ajanin yazdigi komut bir kabuk satiridir; boru,
            # yonlendirme ve `&&` calissin.
            "sh", "-lc", sarmal, "deerx", command,
        ]
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self.ic_oldur(pid_yolu, grup=True)
            kismi = (
                (exc.stdout or b"").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            )
            # Neden ciktinin icine: `run_command` zaman asiminda yalnizca
            # kismi ciktiyi gosteriyor. Oldurmenin GERCEKLESTIGINI orada
            # soylemezsek ajan "surec hala kosuyor olabilir" varsayimiyla
            # ayni portu bir daha denemeye kalkar.
            not_ = t("sandbox.timeout_killed", seconds=int(timeout))
            return SandboxSonuc(
                returncode=124,
                stdout=f"{kismi}\n{not_}" if kismi.strip() else not_,
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
        self._kirik_mi()
        # Uc yol sirayla denenir. Ilki calisan imajlarda oteki ikisi hic
        # kosmaz; ama `python`u OLMAYAN bir imajda (node, go, php) o cagri
        # "komut bulunamadi" ile duser ve saglam calisan bir servis
        # sessizce `service.not_listening` diye reddedilirdi.
        deneme = (
            ["python", "-c",
             f"import socket,sys;s=socket.socket();s.settimeout({float(timeout)!r});"
             f"sys.exit(0 if s.connect_ex(('127.0.0.1',{int(port)}))==0 else 1)"],
            # `/dev/tcp` bash'e ozgu; node ve go imajlarinda bash var.
            ["bash", "-c", f"exec 3<>/dev/tcp/127.0.0.1/{int(port)}"],
            # Alpine'da `nc` var ama bash yok.
            ["sh", "-c", f"nc -z 127.0.0.1 {int(port)}"],
        )
        for argv in deneme:
            p = subprocess.run(
                ["docker", "exec", self.name, *argv],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if p.returncode == 0:
                return True
            # Konteyner gitmisse cevap "port kapali" DEGILDIR; adiyla
            # soylenir, yoksa cagiran portu bos sanip servisi baslatmaya
            # calisir ve asil sebep hicbir yerde gorunmez.
            self._konteyner_gitti_mi(p.stderr or "")
            # Arac YOKSA sonraki yola gec; arac var ve port kapaliysa
            # cevap "kapali"dir ve aramaya devam etmek yanlis olurdu.
            hata = (p.stderr or "").lower()
            if not any(x in hata for x in ("not found", "no such file", "executable")):
                return False
        return False

    # Docker'in "bu konteyner yok / calismiyor" cevaplari. Uc surum de ayni
    # seyi soyluyor; hangisinin geldigi docker surumune ve komuta bagli.
    _GITTI_KALIPLARI = ("no such container", "is not running", "no such object")

    def _konteyner_gitti_mi(self, stderr: str) -> None:
        """Konteyner yok ya da durmussa bunu ADIYLA firlatir.

        Sessizce "port kapali" demek olculmus bir yaniltma: `start_service`
        once "port dolu mu" diye sorar, "hayir" cevabini alir, sureci
        baslatir ve o da ayni "No such container" ile aninda oler. Ajan
        gunlukte yalnizca olu bir sureci gorur; konteynerin gittigini
        hicbir satir soylemez.
        """
        hata = (stderr or "").lower()
        if any(kalip in hata for kalip in self._GITTI_KALIPLARI):
            raise ToolError(t("sandbox.container_gone", name=self.name))

    def dinleme_adresi(self, port: int) -> str | None:
        """Port konteyner icinde hangi adrese bagli: 'all' | 'loopback' | None.

        `port_acik` yalnizca "birisi dinliyor mu" der ve 127.0.0.1'e
        baglanan bir servis de dinliyor sayilir -- oysa yayinlanan port
        bos kalir ve konaktaki tarayici uygulamaya ulasamaz. Ayrimi
        cekirdegin kendi dokumu (`/proc/net/tcp`) yapar; cozumleme
        `_dinleme_cozumle` icinde saf bir islev, docker'siz test edilir.
        """
        self._kirik_mi()
        try:
            p = subprocess.run(
                ["docker", "exec", self.name, "sh", "-c",
                 "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null"],
                capture_output=True, text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Hazirlik dongusunun icinde: "bilmiyorum" demek beklemeye
            # devam etmek demektir ve dongunun kendi zaman asimi var.
            return None
        dokum = p.stdout or ""
        if p.returncode != 0 or not dokum.strip():
            self._konteyner_gitti_mi(p.stderr or "")
            # `/proc/net/tcp` okunamiyorsa (kisitlanmis calisma zamani)
            # eski yonteme donulur: yalnizca "dinliyor mu". Yanlis adrese
            # bagli servisi yakalayamaz ama calisan bir kurulumu da
            # "hazir degil" diye reddetmez.
            return "all" if self.port_acik(port) else None
        return _dinleme_cozumle(dokum, port)

    def ic_oldur(self, pid_yolu: str, *, grup: bool = False) -> None:
        """PID dosyasindaki sureci konteyner icinde oldurur.

        Yerel `docker exec` istemcisini oldurmek icerideki sureci OLDURMEZ;
        servis calismaya devam eder ve portu tutar. Olculdu.

        Once `pkill -f` ile bir isaret aranmisti; ise yaramadi cunku `-e` ile
        konan ortam degiskeni surecin komut satirinda GORUNMEZ. PID dosyasi
        belirsizlik birakmiyor.

        Kabin kirik isaretliyse sessizce doner, firlatmaz: bu yol kapanis
        temizliginin icinde (`services.stop_all` -> `Orchestrator.close`)
        ve oradan hicbir zaman hata cikmiyordu. Firlatsaydi ilk serviste
        kesilir, tarayici, bilgi tabani ve SQLite baglantisi acik kalirdi.
        Konteynere zaten ulasilamiyor; oldurulecek surec de PID dosyasi da
        erisilemez, docker'a gitmemek yeter.

        `grup=True` pid'in SUREC GRUBUNU oldurur (`kill -TERM -$p`): bir
        dev sunucusu ya da `npm test` asil isi torunlarda yapar ve
        yalnizca pid'i oldurmek portu tutan sureci arkada birakir.
        `--` ayraci YOK -- OLCULDU: dash'te `kill -TERM -- -$p`
        "Illegal number: --" ile duser ve hicbir sey oldurulmez. Grup yoksa
        (tek surec) ikinci bicim devreye girer; ikisi de basarisiz olursa
        surec zaten olmustur.
        """
        if self._kirik is not None:
            return
        if grup:
            oldur = (
                "kill -TERM -$p 2>/dev/null || kill -TERM $p 2>/dev/null; "
                "sleep 0.4; "
                "kill -KILL -$p 2>/dev/null || kill -KILL $p 2>/dev/null; "
            )
        else:
            oldur = "kill -TERM $p 2>/dev/null; sleep 0.4; kill -KILL $p 2>/dev/null; "
        kod = (
            f"[ -f {pid_yolu} ] || exit 0; "
            f"p=$(cat {pid_yolu}); "
            f"{oldur}"
            f"rm -f {pid_yolu}; exit 0"
        )
        try:
            subprocess.run(
                ["docker", "exec", self.name, "sh", "-lc", kod],
                capture_output=True, text=True, check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Bu yol kapanis temizliginin ve zaman asiminin icinde;
            # takilan bir daemon yuzunden kosunun geri kalan temizligi
            # (tarayici, SQLite, oteki servisler) kesilmemeli.
            return

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

    _DURUM_ARGV = ("inspect", "-f", "{{.State.Status}}")

    def _durum(self) -> str | None:
        """Konteynerin durumu; yoksa None.

        `inspect` 30 s icinde yanit vermezse daemon fiilen kapali sayilir
        ve `ensure` bunu adiyla (`daemon_down`) keser; ham TimeoutExpired
        `_kirik`i yazmaz, sonraki her arac cagrisi 30 s daha beklerdi.
        Yoklama bu yolu kullanmaz (`_yokla` `_sessiz` ile sorar) -- orada
        hicbir sey firlatmamali, ekran asili kalmamali.
        """
        try:
            p = subprocess.run(
                ["docker", *self._DURUM_ARGV, self.name],
                capture_output=True, text=True, check=False, timeout=30,
            )
        except subprocess.TimeoutExpired:
            degerler = self._sorun_degerleri("timeout 30s")
            raise SandboxUnavailable(
                t("sandbox.daemon_down", **degerler), key="daemon_down", args=degerler,
            ) from None
        if p.returncode != 0:
            return None
        return (p.stdout or "").strip() or None

    def _docker(self, argv: list[str], timeout: int) -> str:
        kuran = bool(argv) and argv[0] in _KURAN_KOMUTLAR
        try:
            p = subprocess.run(
                ["docker", *argv], capture_output=True, text=True,
                check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if not kuran:
                raise
            # Kuran komutun takilmasi da bir "kabin kurulamiyor" halidir ve
            # derin yoklamayla ayni karari alir: daemon `inspect`e az once
            # yanit verdi, bilinen takilma sebebi kopuk bind mount. Ham
            # TimeoutExpired'i disari birakmak `_kirik`i yazmaz ve her arac
            # cagrisi ayni `timeout` saniyeyi yeniden beklerdi.
            if argv[0] == "run":
                # Zaman asimi `docker run` istemcisini oldurur, konteyneri
                # degil; yarim kalan konteyner adi tutar ve bir sonraki
                # deneme "name is already in use" ile duserdi. `start`
                # icin silinmez: o konteyner zaten vardi, icindeki kurulum
                # kullanicinin acik "yeniden kur" karari olmadan gitmesin.
                # `_sessiz` sart: takilan daemon'da `rm -f` de takilir ve
                # ham `subprocess.run` ikinci TimeoutExpired'i bu except
                # govdesinden HAM cikarirdi -- `_kirik` bos kalir, her
                # arac cagrisi 180+30 s bekler. OLCULDU (sahte docker).
                self._sessiz(["rm", "-f", self.name], 30)
            degerler = self._sorun_degerleri(f"timeout {timeout}s")
            raise SandboxUnavailable(
                t("sandbox.bind_mount_broken", **degerler),
                key="bind_mount_broken", args=degerler,
            ) from None
        if p.returncode != 0:
            ham = (p.stderr or p.stdout or "").strip()
            # Siniflandirma KISALTILMAMIS metinde: WSL2 mount hatasinin
            # ayirt edici parcasi ("mkdir ...: file exists") uzun yolun
            # sonunda gelir ve 300 karakterin disinda kalabilir.
            cevrilen = docker_hatasi_cevir(ham) if kuran else None
            if cevrilen is not None:
                key, _ek = cevrilen
                degerler = self._sorun_degerleri(ham[:300])
                raise SandboxUnavailable(
                    t(f"sandbox.{key}", **degerler), key=key, args=degerler,
                )
            raise ToolError(
                t("sandbox.command_failed",
                  argv=" ".join(argv[:3]), error=ham[:300])
            )
        return (p.stdout or "").strip()

    def _sorun_degerleri(self, error: str) -> dict[str, Any]:
        """Sorun mesajlarinin yer tutuculari; hepsi bir arada.

        Her `sandbox.<key>` metni bu kumenin bir alt kumesini kullanir
        (tests/test_i18n_py.py bunu kilitler); `t()` fazlasini yok sayar.
        Arayuze giden `args` da bu sozluk: `env.issue.<key>` metinleri ayni
        adlari kullanabilir.
        """
        return {
            "workspace": str(self.workspace),
            "image": self.image,
            "name": self.name,
            "first": self.port_base,
            "last": self.port_base + self.port_count - 1,
            "error": error,
        }

    def _sorun(self, key: str, error: str = "") -> dict[str, Any]:
        """`SandboxHealth.problems`/`warnings` satiri."""
        return {"key": key, "args": self._sorun_degerleri(error)}

    # -- saglik -------------------------------------------------------------

    def probe(
        self, *, deep: bool = False, ttl: float = _PROBE_ONBELLEK_TTL,
        node_gerekli: bool = False,
    ) -> SandboxHealth:
        """Kabin kurulabilir mi? Kurmadan olcer.

        Sig yoklama (varsayilan) yalnizca sorar: docker var mi, daemon
        yanit veriyor mu, konteyner ve imaj ne durumda -- ~100 ms, hicbir
        sey kaldirmaz. Derin yoklama (`deep=True`) gercek bir konteyner
        kaldirip calisma alaninin BAGLANDIGINI ve araclarin var oldugunu
        olcer; bir dakikaya kadar surebilir ve yalnizca kullanici
        istediginde (`?probe=1`, "Docker'i test et", `deerx setup`) kosar.
        Ortam ekrani her acilista derin yoklasaydi, tam da teshis etmesi
        gereken arizada (kopuk bind mount) bir dakika asili kalirdi.

        Sonuc konteyner adina gore `ttl` saniye onbellekte tutulur; derin
        bir sonuc sig istegi de karsilar, tersi gecerli degil. `ttl=0`
        tazeler. Sorunsuz bir yoklama `_kirik` isaretini kaldirir: Docker
        Desktop yeniden baslatildiysa kosu yeniden denenebilir.
        `node_gerekli` cagirana ozgudur (package.json olan proje) ve
        onbellekteki sonuca yazilmaz.
        """
        simdi = time.monotonic()
        onbellek = _PROBE_ONBELLEK.get(self.name)
        if onbellek is not None and ttl > 0:
            olcum_zamani, saglik = onbellek
            if simdi - olcum_zamani < ttl and (saglik.deep or not deep):
                return self._node_uyarisi(saglik, node_gerekli)
        saglik = self._yokla(deep)
        _PROBE_ONBELLEK[self.name] = (simdi, saglik)
        if saglik.ok:
            # Sig yoklama mount'u OLCEMEZ (yalnizca derin yoklama dener);
            # sorunsuz sig sonuc bir dogrulama degil, yeniden deneme
            # IZNIDIR ve bu bilincli: kullaniciya "Docker Desktop'i
            # yeniden baslatin, sonra kosuyu yeniden baslatin" deniyor ve
            # kosu basi sig yoklar -- isaret burada kalksaydi yeniden
            # baslatilan kosu bayat hatayla duserdi. Bedeli sinirli:
            # `ttl` basina en fazla bir `docker run` denemesi, o da
            # siniflandirilip yeniden isaretlenir; isaretin onledigi
            # "her arac cagrisinda bir deneme" surunmesi geri gelmez.
            self._kirik = None
        return self._node_uyarisi(saglik, node_gerekli)

    def _node_uyarisi(self, saglik: SandboxHealth, node_gerekli: bool) -> SandboxHealth:
        # `tools` bos ise olculmemis demektir; olculmemis bir arac icin
        # uyari verilmez, ekrana yalan yazilmaz.
        if not node_gerekli or saglik.tools.get("node", True):
            return saglik
        if any(u["key"] == "node_missing" for u in saglik.warnings):
            return saglik
        return replace(saglik, warnings=[*saglik.warnings, self._sorun("node_missing")])

    def _yokla(self, deep: bool) -> SandboxHealth:
        saglik = SandboxHealth(
            docker_cli=False, daemon=None, container_status=None,
            image_present=None, mount_ok=None, checked_at=time.time(), deep=deep,
        )
        # Docker yoksa docker HIC cagrilmaz: `subprocess` bulunamayan bir
        # komut icin OSError firlatir ve bunu yakalamak, olcmek istedigimiz
        # seyi gizlerdi.
        if shutil.which("docker") is None:
            saglik.problems.append(self._sorun("no_docker"))
            return saglik
        saglik.docker_cli = True
        bilgi = self._sessiz(["info", "--format", "{{.ServerVersion}}"], 30)
        if bilgi is None or bilgi.returncode != 0:
            hata = "" if bilgi is None else (bilgi.stderr or bilgi.stdout or "")
            saglik.problems.append(self._sorun("daemon_down", hata.strip()[:300]))
            return saglik
        saglik.daemon = (bilgi.stdout or "").strip() or None
        # `_durum()` degil: o zaman asiminda firlatir, yoklama ise hicbir
        # halde firlatmamali. Yanit gelmezse durum bilinmiyor (None) ve
        # sig yoklama yine bir sonuc doner; ekran asili kalmaz.
        durum = self._sessiz([*self._DURUM_ARGV, self.name], 30)
        saglik.container_status = (
            None if durum is None or durum.returncode != 0
            else (durum.stdout or "").strip() or None
        )
        imaj = self._sessiz(["image", "inspect", "--format", "{{.Id}}", self.image], 30)
        saglik.image_present = imaj is not None and imaj.returncode == 0
        if not saglik.image_present:
            # Uyari, sorun degil: `ensure()` imaji `docker run` ile kendisi
            # ceker. Sorun sayilsaydi taze bir kurulumda `deerx setup` daha
            # ilk kosudan once "kabin kurulamiyor" derdi.
            saglik.warnings.append(self._sorun("image_missing"))
        if deep and saglik.image_present:
            self._derin_yokla(saglik)
        return saglik

    def _derin_yokla(self, saglik: SandboxHealth) -> None:
        """Gercek bir konteynerde calisma alani ve araclar olculur.

        Konteyner zaten calisiyorsa icine `exec` ile girilir; yoksa gecici
        bir konteyner (`--rm`, ayri ad) kaldirilir ki kosunun konteyneriyle
        karismasin. `--pull never`: yoklama imaj cekmez -- imajin yoklugu
        yukarida uyari olarak isaretlendi ve derin yoklama ona hic girmez.
        """
        gecici = ""
        if saglik.container_status == "running":
            argv = ["exec", self.name, "sh", "-c", _YOKLAMA_BETIGI]
        else:
            gecici = self.probe_name
            argv = [
                "run", "--rm", "--pull", "never", "--name", gecici,
                "-v", f"{self.workspace}:{CALISMA_ALANI}",
                self.image, "sh", "-c", _YOKLAMA_BETIGI,
            ]
        try:
            p = subprocess.run(
                ["docker", *argv], capture_output=True, text=True,
                check=False, timeout=_PROBE_ZAMAN_ASIMI,
            )
        except subprocess.TimeoutExpired:
            if gecici:
                # Zaman asimi `docker run` istemcisini oldurur, konteyneri
                # degil; `--rm` ancak konteyner bitince temizler. `_sessiz`:
                # yoklama hicbir halde firlatmamali, takilan daemon'da
                # `rm -f` de takilir ve ham cagri Ortam ekranina 500
                # verirdi. OLCULDU (sahte docker, run+rm cift zaman asimi).
                self._sessiz(["rm", "-f", gecici], 30)
            # Bilinen takilma sebebi kopuk bind mount (bkz. modul
            # docstring'i); daemon yanit verdigine gore baska aday yok.
            saglik.mount_ok = False
            saglik.problems.append(
                self._sorun("bind_mount_broken", f"timeout {_PROBE_ZAMAN_ASIMI}s")
            )
            return
        except OSError as exc:
            saglik.problems.append(self._sorun("daemon_down", str(exc)[:300]))
            return
        ham = (p.stderr or p.stdout or "").strip()
        if p.returncode != 0:
            # Betik `exit 0` ile biter; sifir disi kod docker'in kendi
            # hatasidir ve betik hic kosmamistir: mount da araclar da
            # OLCULMEMISTIR (None / bos), "yok" degil. Aksi halde bos
            # stdout araclarin hepsini "yok" gosterir ve docker hatasinin
            # ustune bir de `node_missing` uyarisi binerdi.
            cevrilen = docker_hatasi_cevir(ham)
            if cevrilen is not None:
                key = cevrilen[0]
            elif gecici:
                # Daemon yanit verdi, imaj var, ad bos; gecici konteyner
                # yine kalkmadiysa kalan aday bind mount.
                key = "bind_mount_broken"
            else:
                # Calisan konteynere girilemedi: `inspect` ile `exec`
                # arasinda olmus olabilir ("is not running"). Bu bir mount
                # arizasi degil; `bind_mount_broken` demek kullaniciya
                # yanlis care ("Docker Desktop'i yeniden baslatin") verirdi.
                # Ham metin `error` ile tasinir, ad iddiasiz kalir.
                key = "unavailable"
            saglik.mount_ok = False if key == "bind_mount_broken" else None
            saglik.problems.append(self._sorun(key, ham[:300]))
            return
        satirlar = set((p.stdout or "").split())
        saglik.mount_ok = "MOUNT_OK" in satirlar
        saglik.tools = {arac: f"TOOL_{arac}" in satirlar for arac in _YOKLANAN_ARACLAR}
        if not saglik.mount_ok:
            # Betik kostu ama /workspace yok: `-v` istendigi halde
            # baglanmamis. Iki yolda da mount'un kendisi ariza.
            saglik.problems.append(self._sorun("bind_mount_broken", ham[:300]))

    def _sessiz(self, argv: list[str], timeout: int) -> subprocess.CompletedProcess[str] | None:
        """`docker argv`; hata firlatmaz, calistirilamazsa None."""
        try:
            return subprocess.run(
                ["docker", *argv], capture_output=True, text=True,
                check=False, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
