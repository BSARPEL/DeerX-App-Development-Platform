"""Surec agacini oldurmek.

Tek bir yerde durur: hem `run_command`'in zaman asimi hem de uzun omurlu
servislerin kapatilmasi ayni sorunu cozer -- bir kabugu oldurmek onun
baslattigi torunlari oldurmez. `npm run dev` gibi bir komutta oldurulen
kabuk, gercek sunucuyu arkada yetim birakir ve port dolu kalir.
"""

from __future__ import annotations

import os
import subprocess

if os.name == "nt":  # pragma: no cover - platforma bagli
    import ctypes
    from ctypes import wintypes

    _TH32CS_SNAPPROCESS = 0x0002

    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        )


def _torunlar_nt(pid: int) -> list[int]:
    """Verilen surecin butun torunlari (Windows).

    `taskkill /T` agaci YALNIZCA kok yasiyorsa yurutebilir. Kok olmusse
    "surec bulunamadi" der ve torunlara hic bakmaz.

    OLCULDU: servisler tam olarak bu durumu uretiyor. `shell=True` araya
    bir `cmd.exe` koyar, asil sunucu onun torunudur. Ara kabuk once
    oldugunde `Service.alive` -- yalnizca DOGRUDAN cocuga bakar -- False
    olur, kayit dusurulur ve sunucu artik kimsenin tanimadigi bir surec
    olarak portu tutmaya devam eder. Tek bir test kosusu uc surec birakti;
    bir calisma alaninda yuz on besi birikmisti.

    Surec tablosu ebeveyn kimligini kok oldukten SONRA da tasir, o yuzden
    agac oradan yurunur. Toolhelp32 stdlib ile gelir: `wmic` Windows 11'in
    yeni surumlerinde kaldirildi, her servis kapanisinda PowerShell
    cagirmak da yarim saniye eklerdi.

    PID yeniden kullanimi: olu bir kimlik geri dagitilirsa ilgisiz bir
    surec torun gorunebilir. Risk yeni degil -- `taskkill /T` de ayni
    tabloyu okur -- ama burada acikca yazili duruyor.
    """
    if os.name != "nt":  # pragma: no cover - yalnizca Windows
        return []

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32))
    kernel32.Process32First.restype = wintypes.BOOL
    kernel32.Process32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32))
    kernel32.Process32Next.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    gecersiz = ctypes.c_void_p(-1).value
    anlik = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not anlik or anlik == gecersiz:
        return []

    cocuklar: dict[int, list[int]] = {}
    try:
        kayit = _PROCESSENTRY32()
        kayit.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not kernel32.Process32First(anlik, ctypes.byref(kayit)):
            return []
        while True:
            cocuklar.setdefault(int(kayit.th32ParentProcessID), []).append(
                int(kayit.th32ProcessID)
            )
            if not kernel32.Process32Next(anlik, ctypes.byref(kayit)):
                break
    finally:
        kernel32.CloseHandle(anlik)

    # Genislik oncelikli yuruyus. `gorulen` sart: kendini ebeveyn gosteren
    # bir kayit (0 -> 0 olagan) sonsuz donguye sokardi.
    bulunan: list[int] = []
    gorulen = {pid}
    kuyruk = [pid]
    while kuyruk:
        mevcut = kuyruk.pop()
        for cocuk in cocuklar.get(mevcut, ()):
            if cocuk in gorulen:
                continue
            gorulen.add(cocuk)
            bulunan.append(cocuk)
            kuyruk.append(cocuk)
    return bulunan


def kill_tree(pid: int) -> None:
    """Verilen sureci ve butun alt sureclerini oldurur.

    Kok surec ZATEN OLMUS olsa bile torunlar oldurulur; bu, sizintinin
    gerceklestigi tek durumdu.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        # Torunlar kok oldurulmeden ONCE toplanir. Kok olduktan sonra da
        # tabloda dururlar, ama `taskkill /T` koke ulasamayinca agaci hic
        # yurumez; o yuzden hedefler tek tek veriliyor.
        hedefler = [*_torunlar_nt(pid), pid]
        argv = ["taskkill", "/F"]
        for hedef in hedefler:
            argv += ["/PID", str(hedef)]
        subprocess.run(argv, capture_output=True, check=False)
        return

    import signal

    try:
        grup = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        # Kok olmus. `start_new_session=True` ile grup kimligi cocugun
        # kendi pid'i oldugu icin grup yine de hedeflenebilir -- ve grupta
        # yasayan torunlar tam olarak burada oldurulmeden kaliyordu.
        grup = pid
    try:
        os.killpg(grup, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover
        pass


def spawn_flags() -> dict[str, object]:
    """Sureci kendi grubunda VE kendi konsolunda baslatmak icin bayraklar.

    POSIX'te grup olmadan `kill_tree` torunlara ulasamaz: `killpg` surec
    grubuna sinyal gonderir. Windows'ta `taskkill /T` surec tablosundaki
    ebeveyn zincirini yurudugu icin grup sart degil -- ama konsol ayrilmadan
    ajanin komutu ORKESTRATORU oldurebiliyor.

    OLCULDU (Windows 11, gercek bir kosuda yasandi): backend ajani alt
    surece `KeyboardInterrupt` gondermenin yolunu ararken bir betik yazdi
    ve icinde `os.kill(torun_pid, CTRL_BREAK_EVENT)` cagirdi. Sekiz saatlik
    kosu o anda oldu -- ne hata kaydi, ne faz dusmesi, ne de tek satirlik iz.

    Konsol denetim olaylari sureci degil KONSOLU hedefler.
    `CREATE_NEW_PROCESS_GROUP` surec GRUBUNU ayirir, konsolu ayirmaz; olay
    konsolu paylasan herkese -- orkestratore de -- ulasir. Yan yana olcum,
    olduren kombinasyon sabit tutularak (ajan CTRL_BREAK_EVENT'i kendi
    grup lideri olmayan bir toruna gonderir):

        NEW_PROCESS_GROUP                 -> orkestrator OLDU
        NEW_PROCESS_GROUP | NO_WINDOW     -> hayatta
        NEW_PROCESS_GROUP | DETACHED      -> hayatta
        NEW_PROCESS_GROUP | NEW_CONSOLE   -> hayatta

    Uc aday da koruyor; `CREATE_NO_WINDOW` seciliyor: `CREATE_NEW_CONSOLE`
    her komutta ekranda bir pencere caktirir, `DETACHED_PROCESS` ise sureci
    konsolsuz birakir ve konsol bekleyen programlari bozar. `CREATE_NO_WINDOW`
    kendi konsolunu verir ama penceresini gostermez. Boru hatlariyla cikti
    yakalamaya dokunmaz: yonlendirme konsoldan bagimsizdir.
    """
    if os.name == "nt":
        return {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
    return {"start_new_session": True}


# Cocuk surecin cikis kodlamasi. Windows'ta bir Python sureci, ciktisi boruya
# yonlendirildiginde konsol kod sayfasini kullanir (Turkce Windows'ta cp1254)
# ve oraya sigmayan her karakterde `UnicodeEncodeError` ile coker. Biz okurken
# utf-8 cozuyorduk ama YAZAN tarafa bunu hic soylemiyorduk.
#
# Olculdu: tam bir boru hatti kosusunda ajanin calistirdigi
# `python -c "print('Link Kasasi -> http://...')"` komutu, ok isareti (U+2192)
# cp1254'te olmadigi icin cikis kodu 1 ile dustu. Ajan kendi kodunda hata
# aramaya basladi -- oysa sorun bizim baslatma ortamimizdaydi. Turkce bir
# projede bu neredeyse her komutu vurur.
def child_env() -> dict[str, str]:
    """Alt surecin ortami: cikis kodlamasi utf-8'e sabitlenir."""
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
