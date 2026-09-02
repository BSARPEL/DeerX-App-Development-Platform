"""Sunucudaki Chrome'u tek, uzun omurlu bir oturum olarak surer.

Onceki `browse_page` her cagrida tarayiciyi acip kapatiyordu: her sayfa icin
bir-iki saniye ve her seferinde sifirlanan bir durum. Arama motorlari cerez
bekler, dokumantasyon siteleri giris ister, ajanin bir sayfada tiklayip
sonrakine gecmesi ayni sekmeyi gerektirir. Oturum artik kalici.

Profil dizini calisma alaninin altinda, kullanicinin gercek Chrome
profilinden AYRI. Gercek profili kullanmak, ajana kullanicinin giris yapmis
butun hesaplarini acmak demekti; hicbir kolaylik bunu karsilamaz.

Tum ag trafigi `FilteringProxy` uzerinden gecer (bkz. `proxy.py`).
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..i18n import t
from ..logging import get_logger
from .policy import UrlPolicy
from .proxy import FilteringProxy

log = get_logger("browser")

# Chrome'un bulunabilecegi yerler. Sirali: once gercek Chrome, sonra Edge
# (o da Chromium), sonra Playwright'in paketli tarayicisi.
_CHROME_PATHS = {
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
    "linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome",
    ],
}
_EDGE_PATHS = {
    "win32": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    "linux": ["/usr/bin/microsoft-edge"],
}


@dataclass(frozen=True)
class BrowserBinary:
    """Bulunan tarayici."""

    kind: str          # chrome | edge | chromium
    path: str | None   # None ise Playwright'in paketli tarayicisi
    label: str


def find_browser(preferred: str = "auto") -> BrowserBinary | None:
    """Sunucudaki tarayiciyi bulur.

    `preferred` "chrome" | "edge" | "chromium" | "auto" olabilir. "chromium"
    Playwright'in indirdigi tarayicidir; digerleri sistemde kurulu olani
    kullanir ve ayri bir indirme gerektirmez.
    """
    import sys

    platform = sys.platform if sys.platform in _CHROME_PATHS else "linux"

    def first_existing(paths: list[str]) -> str | None:
        for candidate in paths:
            if candidate and Path(candidate).is_file():
                return candidate
        return None

    if preferred == "chromium":
        return BrowserBinary("chromium", None, "Playwright Chromium")

    if preferred in ("auto", "chrome"):
        found = first_existing(_CHROME_PATHS[platform]) or shutil.which("google-chrome")
        if found:
            return BrowserBinary("chrome", found, f"Google Chrome ({found})")
        if preferred == "chrome":
            return None

    if preferred in ("auto", "edge"):
        found = first_existing(_EDGE_PATHS[platform]) or shutil.which("microsoft-edge")
        if found:
            return BrowserBinary("edge", found, f"Microsoft Edge ({found})")
        if preferred == "edge":
            return None

    if preferred == "auto":
        return BrowserBinary("chromium", None, "Playwright Chromium (yedek)")
    return None


class BrowserSession:
    """Paylasilan Chrome oturumu.

    Tembel baslar: ilk sayfa istenene kadar hicbir surec calismaz. Bos
    kalinca kendini kapatir -- bir gun acik duran sunucuda, on dakika once
    bir arama yapildi diye Chrome'un bellekte durmasi gereksiz.
    """

    def __init__(
        self,
        *,
        profile_dir: Path,
        policy: UrlPolicy,
        channel: str = "auto",
        headless: bool = True,
        idle_seconds: float = 600.0,
        max_pages: int = 4,
        on_navigate: Any = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.policy = policy
        self.channel = channel
        self.headless = headless
        self.idle_seconds = idle_seconds
        self.max_pages = max_pages
        self._on_navigate = on_navigate

        self._lock = threading.RLock()
        self._playwright: Any = None
        self._context: Any = None
        self._proxy: FilteringProxy | None = None
        self._last_used = 0.0
        self._binary: BrowserBinary | None = None
        # Sayfanin kendi hatalari: konsol, yakalanmamis istisna, dusen
        # istek, 4xx/5xx yanit. Anlik goruntude her sey yerinde gorunebilir
        # ama konsolda bir istisna varsa uygulama calismiyor demektir --
        # UAT'nin yarisi budur.
        self.messages: list[dict[str, str]] = []
        self._listening: set[int] = set()

    # ------------------------------------------------------------------ #
    # Durum
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._context is not None

    @property
    def binary(self) -> BrowserBinary | None:
        return self._binary

    def describe(self) -> dict[str, Any]:
        """Ayarlar ekraninin gosterdigi tani bilgisi."""
        found = find_browser(self.channel)
        return {
            "available": found is not None and _playwright_installed(),
            "playwright": _playwright_installed(),
            "binary": found.label if found else None,
            "kind": found.kind if found else None,
            "running": self.running,
            "proxy_port": self._proxy.port if self._proxy else None,
            "profile": str(self.profile_dir),
        }

    # ------------------------------------------------------------------ #
    # Yasam dongusu
    # ------------------------------------------------------------------ #
    def _ensure(self) -> Any:
        with self._lock:
            self._reap_if_idle()
            if self._context is not None:
                self._last_used = time.time()
                return self._context

            if not _playwright_installed():
                raise ToolError(t("setup.no_playwright_driver"))

            binary = find_browser(self.channel)
            if binary is None:
                raise ToolError(t("setup.no_browser", channel=self.channel))
            self._binary = binary

            # Vekil her alt kaynak icin de tetiklenir; olay akisina yalnizca
            # ENGELLENEN istekler dusuruluyor. Gezinmeleri araclarin kendisi
            # bildiriyor, cunku anlamli olan onlar.
            def _report(url: str, allowed: bool) -> None:
                if not allowed and self._on_navigate is not None:
                    self._on_navigate(url, False)

            self._proxy = FilteringProxy(self.policy, on_request=_report)
            port = self._proxy.start()

            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self.profile_dir.mkdir(parents=True, exist_ok=True)

            launch: dict[str, Any] = {
                "user_data_dir": str(self.profile_dir),
                "headless": self.headless,
                "proxy": {"server": f"http://127.0.0.1:{port}"},
                "args": _CHROME_ARGS,
                "accept_downloads": False,
            }
            if binary.path:
                launch["executable_path"] = binary.path

            try:
                self._context = self._playwright.chromium.launch_persistent_context(**launch)
            except Exception as exc:  # noqa: BLE001 - playwright kendi tiplerini kullanir
                self._teardown()
                raise ToolError(t("setup.browser_launch_failed", label=binary.label, error=exc)) from exc

            self._context.set_default_timeout(30_000)
            self._last_used = time.time()
            log.info(t("setup.browser_started", label=binary.label, port=port))
            return self._context

    def _reap_if_idle(self) -> None:
        if self._context is None or not self.idle_seconds:
            return
        if time.time() - self._last_used > self.idle_seconds:
            log.info(t("browser.idle_closed", seconds=f"{self.idle_seconds:.0f}"))
            self._teardown()

    def close(self) -> None:
        with self._lock:
            self._teardown()

    def _teardown(self) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._playwright and self._playwright.stop(),
            lambda: self._proxy and self._proxy.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001 - kapanista hata yutulur
                pass
        self._context = None
        self._playwright = None
        self._proxy = None

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Sayfalar
    # ------------------------------------------------------------------ #
    def page(self) -> Any:
        """Etkin sekmeyi doner; yoksa acar.

        Ajan tek bir sekmede gezer: "once ara, sonra ucuncu sonuca tikla,
        sonra geri don" akisinin calismasi icin sekmenin ayni kalmasi
        gerekiyor.
        """
        context = self._ensure()
        pages = [p for p in context.pages if not p.is_closed()]
        if not pages:
            return self._watch(context.new_page())
        # Fazla sekme birikmisse en eskileri kapat.
        while len(pages) > self.max_pages:
            pages.pop(0).close()
        return self._watch(pages[-1])


    # Kayit defterinin ustu: eski satirlar dusulur, bellek sinirsiz buyumez.
    _MESSAGE_LIMIT = 200

    def _watch(self, page: Any) -> Any:
        """Sayfanin hata olaylarini kaydeder.

        Bir kez baglanir: ayni sayfaya her `page()` cagrisinda yeniden
        baglanmak, tek bir hatayi defalarca kaydederdi.
        """
        key = id(page)
        if key in self._listening:
            return page
        self._listening.add(key)

        def kaydet(kind: str, level: str, text: str) -> None:
            self.messages.append({"kind": kind, "level": level, "text": text[:600]})
            if len(self.messages) > self._MESSAGE_LIMIT:
                del self.messages[: len(self.messages) - self._MESSAGE_LIMIT]

        def yanit(response: Any) -> None:
            try:
                if response.status >= 400:
                    kaydet("network", str(response.status), f"{response.status} {response.url}")
            except Exception:  # noqa: BLE001 - sayfa kapanmis olabilir
                pass

        def dusen(request: Any) -> None:
            try:
                kaydet("network", "failed", f"{request.method} {request.url} - {request.failure}")
            except Exception:  # noqa: BLE001
                pass

        try:
            page.on("console", lambda m: kaydet("console", m.type, m.text))
            page.on("pageerror", lambda e: kaydet("pageerror", "error", str(e)))
            page.on("requestfailed", dusen)
            page.on("response", yanit)
        except Exception as exc:  # noqa: BLE001 - dinleyici baglanamadi
            log.debug("sayfa dinleyicileri baglanamadi: %s", exc)
        return page

    def settle(self, timeout: int = 2500) -> None:
        """Bekleyen isteklerin donmesi icin kisa bir sure bekler.

        Sessizce basarisiz olur: uzun yoklama ya da websocket kullanan bir
        sayfada ag hicbir zaman bosalmaz ve bu bir hata degildir.
        """
        try:
            self.page().wait_for_load_state("networkidle", timeout=timeout)
        except Exception:  # noqa: BLE001 - bosalma olmadi; sorun degil
            pass

    @staticmethod
    def collapse(kayitlar: list[dict[str, str]]) -> list[dict[str, str]]:
        """Ard arda gelen ayni kaydi tek satira indirir.

        Chrome ayni kaynak hatasini birden fazla kez bildirebiliyor; uc
        ayni satir, uc ayri sorun gibi okunuyordu. Tekrar sayisi korunur:
        her tiklamada tekrarlayan bir hata, bir kez olan hatadan farklidir.
        """
        toplu: list[dict[str, str]] = []
        for kayit in kayitlar:
            if toplu and all(toplu[-1].get(k) == kayit.get(k) for k in ("kind", "level", "text")):
                toplu[-1]["count"] = str(int(toplu[-1].get("count", "1")) + 1)
                continue
            toplu.append(dict(kayit))
        return toplu

    def problems(self) -> list[dict[str, str]]:
        """Yalnizca sorun bildiren kayitlar."""
        agir = {"error", "warning", "failed"}
        return self.collapse([
            m for m in self.messages
            if m["level"] in agir or m["kind"] == "pageerror"
            or (m["kind"] == "network" and m["level"].isdigit())
        ])

    def goto(self, url: str, *, wait: str = "domcontentloaded", timeout: int = 30_000) -> Any:
        """Adrese gider. Politika hem burada hem vekilde uygulanir.

        Buradaki kontrol modele *anlamli bir hata* dondurmek icin: vekilden
        gelen 403 sayfasi model icin sadece bos bir sayfadir.
        """
        from .policy import UrlBlocked

        try:
            self.policy.check(url)
        except UrlBlocked as exc:
            raise ToolError(str(exc)) from exc

        page = self.page()
        # Yeni sayfa, yeni defter: onceki sayfanin hatalari bu sayfanin
        # hatasi sanilmasin.
        self.messages.clear()
        try:
            page.goto(url, wait_until=wait, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(t("browser.page_failed", error=_short(exc))) from exc
        self._last_used = time.time()
        return page


# Chrome bayraklari. Her biri bir sebep icin burada:
_CHROME_ARGS = [
    # Vekilin atlanmasini engeller. Chrome varsayilan olarak yerel adresler
    # icin vekili atlar; onizleme izni tam olarak orada verildigi icin bu
    # atlamayi kapatmak zorundayiz, yoksa politika devre disi kalir.
    "--proxy-bypass-list=<-loopback>",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    # Asagidakilerin hepsi Chrome'un kendi arka plan istekleri icin: zaman
    # eslemesi, bilesen guncellemesi, telemetri. Ajanin gezinmesiyle ilgisi
    # yok ve vekil kaydini gurultuye bogarlar.
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--disable-breakpad",
    "--no-pings",
    "--no-service-autorun",
    "--disable-default-apps",
    "--metrics-recording-only",
    "--disable-sync",                    # profil buluta gitmesin
    "--disable-extensions",
    "--mute-audio",
    "--disable-features=Translate,MediaRouter,OptimizationHints",
]


def _playwright_installed() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _short(exc: Exception, limit: int = 300) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else exc.__class__.__name__)[:limit]
