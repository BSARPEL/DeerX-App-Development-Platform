"""Tarayicinin nereye gidebilecegine dair tek karar noktasi.

Neden ayri bir modul: bu karar iki yerde birden uygulanmak zorunda.

Araclar seviyesinde dogrulama tek basina yetmez. Ajan `browse_page` ile
izinli bir adrese gider, sonra sayfadaki bir baglantiya tiklar ve Chrome o
baglantiyi kendi basina takip eder -- Python tarafindaki hicbir kontrol
devreye girmez. Bu yuzden ayni politika bir de ag katmaninda, Chrome'un
gectigi vekil sunucuda uygulanir (bkz. `proxy.py`).

Engellenenlerin cogu SSRF icin: bulut ortamlarinda `169.254.169.254`
ornek kimlik bilgilerini duz metin olarak verir; ic aglardaki yonetim
panelleri kimlik dogrulamasiz olur; `file://` sunucunun diskini acar.
Bir dil modelinin surdugu tarayicida bunlarin hicbiri erisilebilir olmamali.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Chrome'un kendi ic semalari: `chrome://net-internals` gibi sayfalar
# tarayicinin ayarlarini degistirmeye izin verir.
BLOCKED_SCHEMES = frozenset({
    "file", "chrome", "chrome-extension", "devtools", "view-source",
    "data", "blob", "ftp", "about",
})
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Bulut saglayicilarinin metadata uclari. IP suzgeci bunlari zaten
# yakaliyor (link-local), ama ad uzerinden gelen istekleri de kesiyoruz:
# bazi saglayicilar cozumlemeyi kendi DNS'lerinde yapiyor.
METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})


class UrlBlocked(Exception):
    """Adres politika geregi reddedildi."""


@dataclass
class UrlPolicy:
    """Hangi adreslerin acilabilecegine karar verir.

    `allowed_origins` istisna listesidir ve YALNIZCA sunucu tarafindan
    doldurulur. Ajanin kendi baslattigi onizleme sunucusu buraya girer;
    boylece "yaptigini gorebilme" yetenegi, ic aga serbest erisim anlamina
    gelmez. Modelin bu listeye bir sey ekleme yolu yoktur.
    """

    allowed_origins: set[str] = field(default_factory=set)

    def allow_origin(self, origin: str) -> None:
        self.allowed_origins.add(origin.rstrip("/").lower())

    def revoke_origin(self, origin: str) -> None:
        self.allowed_origins.discard(origin.rstrip("/").lower())

    def revoke_all(self) -> None:
        """Kosu bitince istisnalar dusurulur; kalici izin yoktur."""
        self.allowed_origins.clear()

    # ------------------------------------------------------------------ #

    def check(self, url: str) -> str:
        """Adresi dogrular ve normalize edilmis halini doner.

        Reddedilirse `UrlBlocked` firlatir; mesaj kullaniciya ve modele
        gosterilir, o yuzden nedenini acikca soyler.
        """
        parsed = urlparse(url.strip())
        scheme = (parsed.scheme or "").lower()

        if scheme in BLOCKED_SCHEMES:
            raise UrlBlocked(f"{scheme}: semasi kapali (yerel dosya ve tarayici ic sayfalari).")
        if scheme not in ALLOWED_SCHEMES:
            raise UrlBlocked(f"Yalnizca http/https destekleniyor: {url}")

        host = (parsed.hostname or "").lower()
        if not host:
            raise UrlBlocked(f"Adreste sunucu adi yok: {url}")

        origin = f"{scheme}://{host}"
        if parsed.port:
            origin = f"{origin}:{parsed.port}"

        # Istisna once bakilir: onizleme sunucusu loopback uzerindedir ve
        # asagidaki ic ag kurali onu zaten reddederdi.
        if origin.lower() in self.allowed_origins:
            return url

        if host in METADATA_HOSTS:
            raise UrlBlocked(f"Bulut metadata ucu engellendi: {host}")

        for address in self._resolve(host):
            if not address.is_global:
                raise UrlBlocked(
                    f"Ic ag adresi engellendi: {host} -> {address}. "
                    "Ajanin yerel aga erismesine izin verilmiyor."
                )
        return url

    def allows(self, url: str) -> bool:
        try:
            self.check(url)
        except UrlBlocked:
            return False
        return True

    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        """Adin cozuldugu TUM adresler.

        Hepsine bakilir, ilkine degil: bir ad hem genel hem ic bir adrese
        cozulebilir ve saldirgan hangisinin kullanilacagini secemedigimiz
        icin en kotusunu varsayariz (DNS rebinding'e karsi ilk savunma).
        """
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return [literal]

        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise UrlBlocked(f"Alan adi cozulemedi: {host} ({exc})") from exc

        found = []
        for info in infos:
            try:
                found.append(ipaddress.ip_address(info[4][0]))
            except ValueError:  # pragma: no cover - getaddrinfo tuhaf bir sey dondu
                continue
        if not found:  # pragma: no cover
            raise UrlBlocked(f"Alan adi hicbir adrese cozulmedi: {host}")
        return found
