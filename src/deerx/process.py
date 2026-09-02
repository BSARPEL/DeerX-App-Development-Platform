"""Surec agacini oldurmek.

Tek bir yerde durur: hem `run_command`'in zaman asimi hem de uzun omurlu
servislerin kapatilmasi ayni sorunu cozer -- bir kabugu oldurmek onun
baslattigi torunlari oldurmez. `npm run dev` gibi bir komutta oldurulen
kabuk, gercek sunucuyu arkada yetim birakir ve port dolu kalir.
"""

from __future__ import annotations

import os
import subprocess


def kill_tree(pid: int) -> None:
    """Verilen sureci ve butun alt sureclerini oldurur."""
    if pid <= 0:
        return
    if os.name == "nt":
        # `taskkill /T` alt surecleri de kapsar. Windows'ta surec grubuna
        # sinyal gondermek konsol uygulamalarinda guvenilir degil.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return

    import signal

    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
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
