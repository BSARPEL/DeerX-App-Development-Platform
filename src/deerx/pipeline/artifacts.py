"""Cikti bicimi ve ortam turu: ad uzantisindan turetilen kararlar.

Bu sabitler web katmaninda yasiyordu; blob deposu (`state._blob_yaz`)
kaydederken ortam turune ihtiyac duyunca boru hattinin web'e bagimli olmasi
gerekirdi -- ters yonde bir bagimlilik. Bu yuzden buraya tasindi: yaprak
modul, yalnizca `mimetypes` kullanir, hem web hem durum katmani ithal eder.
"""

from __future__ import annotations

import mimetypes

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar")
# Ekranda GOSTERILEBILEN goruntuler. `.svg` bilerek disarida: SVG betik
# tasiyabilir ve dogrudan acildiginda uygulamanin kendi kaynaginda calisir.
# Buradakiler tarama goruntuleridir, betik calistiramazlar.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
}
BINARY_SUFFIXES = (
    ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mp3", ".wasm", ".db",
    ".svg",
)

OCTET_STREAM = "application/octet-stream"


def artifact_format(name: str) -> str:
    """Ciktinin nasil gosterilecegini belirler.

    `archive` ve `binary` metin olarak *okunmaz*: bir zip'i utf-8 varsayip
    `errors="replace"` ile cozmek, tarayiciya megabaytlarca anlamsiz karakter
    gonderir. Bunlar ek dosya olarak indirilir.
    """
    lowered = name.lower()
    if lowered.endswith(ARCHIVE_SUFFIXES):
        return "archive"
    # Goruntuler ikiliden ONCE bakilir: `browser_screenshot` "kullanici
    # arayuzde gorur" diyor, oysa ekran goruntusu `binary` sayildigi surece
    # yalnizca bir indirme baglantisiydi.
    if lowered.endswith(IMAGE_SUFFIXES):
        return "image"
    if lowered.endswith(BINARY_SUFFIXES):
        return "binary"
    if lowered.endswith((".md", ".markdown")):
        return "markdown"
    if lowered.endswith((".html", ".htm")):
        return "html"
    if lowered.endswith((".json", ".yaml", ".yml", ".toml")):
        return "data"
    return "text"


def media_type_for(name: str) -> str:
    """Blob satirina yazilan ortam turu.

    Once kendi goruntu tablosu: `mimetypes` isletim sisteminin kayit
    defterine bakar ve ayni uzanti icin makineden makineye farkli cevap
    verebilir; ekranda cizilen goruntulerin turu makineye gore degismemeli.
    Tanimadigi her sey `octet-stream` olarak iner -- tarayiciya "bunu
    goster" diye verilmez.
    """
    lowered = name.lower()
    for suffix, media in IMAGE_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return media
    guessed, _ = mimetypes.guess_type(name)
    return guessed or OCTET_STREAM
