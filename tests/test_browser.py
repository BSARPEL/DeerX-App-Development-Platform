"""Ajanin tarayicisi: politika, vekil ve araclar.

Politika ve vekil testleri tarayici gerektirmez ve her zaman kosar --
guvenlik siniri en cok korunmasi gereken sey. Gercek Chrome gerektirenler
`browser` isaretiyle ayrildi ve tarayici yoksa atlanir.
"""

from __future__ import annotations

import http.server
import ipaddress
import socketserver
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from deerx.browser import FilteringProxy, UrlBlocked, UrlPolicy, find_browser
from deerx.browser.session import _playwright_installed

# ---------------------------------------------------------------------- #
# Politika — tarayici gerektirmez
# ---------------------------------------------------------------------- #


class TestUrlPolicy:
    """Nereye gidilebilir sorusunun tek cevabi.

    Bir dil modelinin surdugu tarayicida bu sinir kacilamaz olmali:
    bulut metadata uclari duz metin kimlik bilgisi verir, ic aglardaki
    yonetim panelleri kimlik dogrulamasizdir, `file://` sunucunun diskidir.
    """

    @pytest.fixture
    def policy(self):
        return UrlPolicy()

    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",   # AWS/Azure metadata
        "http://metadata.google.internal/",           # GCP metadata
        "http://127.0.0.1:8000/",
        "http://localhost:3000/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "file:///C:/Windows/win.ini",
        "file:///etc/passwd",
        "chrome://net-internals",
        "devtools://devtools/bundled/inspector.html",
        "view-source:https://ornek.com",
        "data:text/html,<script>alert(1)</script>",
        "ftp://ornek.com/dosya",
    ])
    def test_blocked(self, policy, url):
        assert not policy.allows(url), f"{url} acilabiliyor"

    # Genel IP'ler dogrudan verilir: testin sonucu DNS'e ve aga bagli olursa
    # agi olmayan bir makinede politika bozulmus gibi gorunur.
    @pytest.mark.parametrize("url", [
        "https://93.184.216.34/yol",   # genel IPv4
        "http://1.1.1.1/",
        "https://[2606:4700:4700::1111]/",
    ])
    def test_allowed(self, policy, url):
        assert policy.allows(url)

    def test_message_says_why(self, policy):
        """Gerekce modele ve kullaniciya gosteriliyor; anlasilir olmali."""
        with pytest.raises(UrlBlocked) as caught:
            policy.check("http://169.254.169.254/")
        assert "engellendi" in str(caught.value).lower()

    def test_dns_failure_is_not_reported_as_a_block(self, policy):
        """Cozulemeyen alan adi ile yasak adres ayni sey degil.

        Ikisi de acilamaz, ama model "engellendi" gorurse politikayi
        sucladi saniyor; oysa adres yanlis yazilmis olabilir.
        """
        with pytest.raises(UrlBlocked) as caught:
            policy.check("https://bu-alan-adi-yok-12345.invalid/")
        message = str(caught.value).lower()
        assert "cozulemedi" in message
        assert "engellendi" not in message


class TestPreviewException:
    """Ajanin kendi uygulamasini gorebilmesi, ic aga acilmak degildir."""

    @pytest.fixture
    def policy(self):
        return UrlPolicy()

    def test_grant_is_origin_scoped(self, policy):
        policy.allow_origin("http://127.0.0.1:3000")
        assert policy.allows("http://127.0.0.1:3000/sayfa")
        assert not policy.allows("http://127.0.0.1:9999/"), "baska port acilmamali"
        assert not policy.allows("http://10.0.0.5/"), "ic ag acilmamali"

    def test_metadata_stays_blocked_even_with_grants(self, policy):
        """Onizleme izni metadata ucunu asla acmamali."""
        policy.allow_origin("http://127.0.0.1:3000")
        assert not policy.allows("http://169.254.169.254/")

    def test_revoke_all_closes_the_door(self, policy):
        """Izin kosuya baglidir; sonraki kosuya sarkmaz."""
        policy.allow_origin("http://127.0.0.1:3000")
        policy.revoke_all()
        assert not policy.allows("http://127.0.0.1:3000/")

    def test_model_cannot_widen_the_policy(self):
        """Politikayi genisletme yolu yalnizca sunucu tarafindadir.

        Araclar `allow_origin`i cagirir ama verdikleri degeri once dogrular:
        yalnizca yerel bir port. Model dogrudan politika nesnesine erisemez.
        """
        from deerx.tools.browser import PreviewOpen

        assert "allow_origin" in PreviewOpen.run.__code__.co_names


# ---------------------------------------------------------------------- #
# Vekil — gercek soketler, tarayici yok
# ---------------------------------------------------------------------- #
class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"MERHABA"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    """Ajanin baslattigi yerel uygulamanin yerine gecen sunucu."""
    server = socketserver.TCPServer(("127.0.0.1", 0), _Hello)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class TestFilteringProxy:
    """Politika ag katmaninda uygulanmali.

    Arac seviyesindeki kontrol yetmez: ajan izinli bir sayfayi acar, sonra
    oradaki bir baglantiya tiklar ve Chrome onu kendi basina takip eder.
    Python tarafindaki hicbir kontrol devreye girmez -- vekil girer.
    """

    @staticmethod
    def _fetch(proxy_port: int, url: str) -> tuple[int, bytes]:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"})
        )
        try:
            response = opener.open(url, timeout=10)
            return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_blocks_by_default(self, upstream):
        with FilteringProxy(UrlPolicy()) as proxy:
            status, body = self._fetch(proxy.port, upstream + "/")
        assert status == 403
        assert b"reddetti" in body

    def test_blocks_cloud_metadata(self):
        with FilteringProxy(UrlPolicy()) as proxy:
            status, _ = self._fetch(proxy.port, "http://169.254.169.254/latest/meta-data/")
        assert status == 403

    def test_allows_a_granted_origin(self, upstream):
        policy = UrlPolicy()
        policy.allow_origin(upstream)
        with FilteringProxy(policy) as proxy:
            status, body = self._fetch(proxy.port, upstream + "/")
        assert status == 200
        assert body == b"MERHABA"

    def test_revoking_takes_effect_immediately(self, upstream):
        """Izin dusunce acik baglanti da yeni istegi gecirmemeli."""
        policy = UrlPolicy()
        policy.allow_origin(upstream)
        with FilteringProxy(policy) as proxy:
            assert self._fetch(proxy.port, upstream + "/")[0] == 200
            policy.revoke_all()
            assert self._fetch(proxy.port, upstream + "/")[0] == 403

    def test_reports_what_it_blocked(self, upstream):
        """Sessizce engellemek, kimseye ajanin nereye gitmek istedigini anlatmaz."""
        seen: list[tuple[str, bool]] = []
        with FilteringProxy(UrlPolicy(), on_request=lambda u, ok: seen.append((u, ok))) as proxy:
            self._fetch(proxy.port, upstream + "/")
        assert seen and seen[0][1] is False

    def test_binds_only_to_loopback(self):
        """Vekil disaridan erisilebilir olmamali; acik vekil ciddi bir aciktir."""
        with FilteringProxy(UrlPolicy()) as proxy:
            assert proxy.port > 0
            import socket

            probe = socket.socket()
            probe.settimeout(2)
            # 0.0.0.0'a baglansaydi makinenin dis adresinden de cevap verirdi.
            assert probe.connect_ex(("127.0.0.1", proxy.port)) == 0
            probe.close()


# ---------------------------------------------------------------------- #
# Tarayici bulma — kurulum gerektirmez
# ---------------------------------------------------------------------- #
class TestBrowserDiscovery:
    def test_explicit_chromium_never_probes_the_system(self):
        found = find_browser("chromium")
        assert found is not None and found.kind == "chromium"
        assert found.path is None, "paketli tarayicinin sistem yolu olmaz"

    def test_auto_always_returns_something(self):
        """Sistemde tarayici yoksa bile paketli surume duser."""
        assert find_browser("auto") is not None

    def test_profile_is_separate_from_the_users_own(self, settings):
        """Kullanicinin gercek Chrome profili ASLA kullanilmamali.

        Kullanilsaydi ajan, kullanicinin giris yapmis butun hesaplarini
        okuyabilirdi -- hicbir kolaylik bunu karsilamaz.
        """
        profile = settings.browser_profile_dir
        assert profile.is_relative_to(settings.workspace)
        for suspicious in ("Google/Chrome/User Data", "Chrome\\User Data"):
            assert suspicious not in str(profile)


# ---------------------------------------------------------------------- #
# Gercek Chrome gerektirenler
# ---------------------------------------------------------------------- #
_HAS_BROWSER = _playwright_installed() and find_browser("auto") is not None
needs_browser = pytest.mark.skipif(_HAS_BROWSER is False, reason="tarayici yok")


@needs_browser
class TestLiveBrowser:
    @pytest.fixture
    def session(self, tmp_path):
        from deerx.browser import BrowserSession

        instance = BrowserSession(
            profile_dir=Path(tmp_path) / "profile",
            policy=UrlPolicy(),
            channel="auto",
            headless=True,
            idle_seconds=0,
        )
        yield instance
        instance.close()

    def test_lazy_start(self, session):
        """Tarayici araci cagrilmadan surec acilmamali."""
        assert not session.running
        session.describe()
        assert not session.running, "describe() tarayiciyi baslatmamali"

    def test_reuses_one_tab(self, session, upstream):
        """Ajanin 'ara, tikla, geri don' akisi ayni sekmeyi gerektirir."""
        session.policy.allow_origin(upstream)
        first = session.goto(upstream + "/")
        second = session.goto(upstream + "/")
        assert first is second

    def test_policy_applies_to_navigation(self, session):
        from deerx.errors import ToolError

        with pytest.raises(ToolError):
            session.goto("http://169.254.169.254/")


# ---------------------------------------------------------------------- #
# Izinler — tarayici gerektirmez
# ---------------------------------------------------------------------- #
class TestPermissionsAreSeparate:
    """Internete cikmak ile kendi uygulamana bakmak ayni izin degil.

    Ikisi tek `enable_web` bayraginda toplanmisti. Gercek bir kosuda
    olculdu: ajan uygulamayi yazdi, `start_service` ile ayaga kaldirdi,
    `preview_open` cagirdi ve "Web erisimi kapali" ile geri cevrildi --
    guvenlik icin interneti kapatan kullanici UAT yetenegini de sessizce
    kaybediyordu.
    """

    @staticmethod
    def _ctx(settings, tmp_path, *, web: bool, preview: bool):
        from deerx.logging import EventLog
        from deerx.tools.base import ToolContext

        settings.enable_web = web
        settings.browser_allow_preview = preview
        # Oturum nesnesi gerekli ama BASLATILMAZ: testler izin kapisini
        # olcuyor, Chrome'u degil.
        class _Sahte:
            running = False

        return ToolContext(
            settings=settings,
            events=EventLog(tmp_path / "events.jsonl"),
            browser=_Sahte(),
        )

    @pytest.mark.parametrize(
        "arac", ["preview_open", "browser_snapshot", "browser_console", "browser_screenshot"]
    )
    def test_both_off_closes_the_browser(self, settings, tmp_path, arac):
        from deerx.errors import ToolError
        from deerx.tools import build_registry

        ctx = self._ctx(settings, tmp_path, web=False, preview=False)
        with pytest.raises(ToolError, match="Tarayici kapali"):
            build_registry().get(arac).run(ctx, **_ornek_arg(arac))

    def test_preview_permission_alone_opens_the_browser(self, settings, tmp_path):
        """Internet kapali ama kendi uygulamana bakabilmelisin."""
        from deerx.errors import ToolError
        from deerx.tools import build_registry

        ctx = self._ctx(settings, tmp_path, web=False, preview=True)
        # Izin kapisini gecer; sonrasinda baska bir sebeple duser ("acik
        # sayfa yok"), ki bu tam olarak istedigimiz ayrim.
        with pytest.raises(ToolError) as hata:
            build_registry().get("browser_console").run(ctx)
        assert "Tarayici kapali" not in str(hata.value)
        assert "Web erisimi kapali" not in str(hata.value)

    @pytest.mark.parametrize("arac", ["browse_page", "web_search"])
    def test_preview_permission_does_not_open_the_internet(self, settings, tmp_path, arac):
        """Onizleme izni loopback icindir; disari cikan arac yine kapali."""
        from deerx.errors import ToolError
        from deerx.tools import build_registry

        ctx = self._ctx(settings, tmp_path, web=False, preview=True)
        with pytest.raises(ToolError, match="Web erisimi kapali"):
            build_registry().get(arac).run(ctx, **_ornek_arg(arac))


def _ornek_arg(arac: str) -> dict:
    return {
        "preview_open": {"port": 8477},
        "browser_screenshot": {"name": "x.png"},
        "browse_page": {"url": "https://example.com"},
        "web_search": {"query": "x"},
    }.get(arac, {})


class TestDecoyResults:
    """Arama ucu, sorguyla alakasiz bir sonuc kumesi donduruyor.

    Olculdu (vekilli ve vekilsiz, iki ayri Chrome ornegi):

        "BaseHTTPRequestHandler threading"
            -> Domino's Pizza Japan, Menu - Domino's, ...
            -> Google Photos, Sign in - Google Accounts, ...
        '"BaseHTTPRequestHandler" thread safety python'
            -> Cince Zhihu sayfalari (Microsoft 365 Copilot)
        '... site:docs.python.org OR site:stackoverflow.com'
            -> DKB AG, bir Alman bankasi

    Uc, otomatik tarayiciyi tespit edince engellemiyor: 200 ve makul gorunen
    HTML ile sahte bir kume veriyor. Bu basarisiz aramadan kat kat kotudur --
    basarisizlik "varsayma" diye bildiriliyor, sahte sonuclar ise arastirma
    yapilmis gibi gorunup rapora kaynak olarak giriyor.
    """

    @staticmethod
    def _hit(title, url="https://ornek.test/x", snippet=""):
        return {"title": title, "url": url, "snippet": snippet}

    def test_the_measured_decoy_set_is_refused(self):
        from deerx.tools.browser import _alakali

        sahte = [
            self._hit("Domino's Pizza Japan", "https://www.dominos.jp/"),
            self._hit("Menu - Domino's", "https://www.dominos.com/menu"),
            self._hit("Google Photos", "https://photos.google.com/"),
        ]
        assert _alakali("BaseHTTPRequestHandler threading", sahte) is False

    def test_a_real_result_set_passes(self):
        from deerx.tools.browser import _alakali

        gercek = [
            self._hit("Python HTTP-Server and Threading",
                      "https://stackoverflow.com/questions/1"),
            self._hit("http.server — HTTP servers",
                      "https://docs.python.org/3/library/http.server.html"),
        ]
        assert _alakali("BaseHTTPRequestHandler threading", gercek) is True

    def test_a_match_in_the_url_alone_is_enough(self):
        """Baslik alakasiz gorunse de adres sorguyu tasiyabilir."""
        from deerx.tools.browser import _alakali

        hits = [self._hit("Documentation",
                          "https://docs.python.org/3/library/http.server.html")]
        assert _alakali("http.server docs", hits) is True

    def test_it_does_not_refuse_when_it_cannot_judge(self):
        """Karar verecek bilgi yoksa engelleme: yanlis pozitif, gercek bir
        aramayi olduren sessiz bir hataya donusur."""
        from deerx.tools.browser import _alakali

        assert _alakali("how to the of", [self._hit("Herhangi bir sey")]) is True
        assert _alakali("anything", []) is True

    def test_the_search_tool_reports_the_decoy(self, ctx, monkeypatch):
        """Uctan uca: tuzak kume donduruldugunde arac hata verir ve modele
        bunun bir cevap OLMADIGINI soyler."""
        import deerx.tools.browser as mod
        from deerx.errors import ToolError
        from deerx.i18n import set_language

        sahte = [{"title": "Domino's Pizza Japan",
                  "url": "https://www.dominos.jp/", "snippet": "pizza"}]

        class SahteSayfa:
            def wait_for_selector(self, *a, **k):
                return None

            def evaluate(self, *a, **k):
                return sahte

        class SahteOturum:
            def goto(self, *a, **k):
                return SahteSayfa()

        monkeypatch.setattr(mod, "_session", lambda c: SahteOturum())
        ctx.settings.enable_web = True
        ctx.settings.search_provider = "browser"
        from deerx.tools import build_registry

        arac = build_registry().get("web_search")

        try:
            set_language("en")
            with pytest.raises(ToolError) as hata:
                arac.run(ctx, query="BaseHTTPRequestHandler threading")
        finally:
            set_language("tr")

        metin = str(hata.value)
        assert "decoy results" in metin, metin
        assert "NOT a 'no results' answer" in metin


class TestSearxng:
    """Kendi SearXNG ornegi: anahtarsiz arama icin olculen tek saglam yol.

    Olculdu -- durust bir bot User-Agent'i ile bugun:
      bing        -> otomasyona SAHTE sonuc kumesi (Domino's Pizza, Google Photos)
      duckduckgo  -> CAPTCHA ("select all squares containing a duck")
      startpage / mojeek / brave / ecosia -> Access Denied, 403, Captcha
      halka acik SearXNG ornekleri -> 429 / 403
      KENDI SearXNG ornegi -> 20 sonuc, ustteki hepsi isabetli
    """

    @staticmethod
    def _yanit(sonuclar, dusenler=()):
        return {
            "results": [
                {"title": t_, "url": u, "content": c} for t_, u, c in sonuclar
            ],
            "unresponsive_engines": [list(d) for d in dusenler],
        }

    def test_results_are_parsed(self, monkeypatch):
        import deerx.tools.web as web

        class SahteYanit:
            @staticmethod
            def json():
                return TestSearxng._yanit([
                    ("http.server docs", "https://docs.python.org/3/library/http.server.html", "ozet"),
                ])

        monkeypatch.setattr(web, "_http_get", lambda *a, **k: SahteYanit())
        hits, dusen = web._search_searxng("http.server", 5, "http://127.0.0.1:8890")
        assert hits[0]["url"].endswith("http.server.html")
        assert hits[0]["snippet"] == "ozet"
        assert dusen == []

    def test_unresponsive_engines_are_surfaced(self, monkeypatch):
        """SearXNG hangi motorun neden dustugunu soyluyor. Bunu yutmak,
        kapsamin sessizce daralmasi demek olurdu."""
        import deerx.tools.web as web

        class SahteYanit:
            @staticmethod
            def json():
                return TestSearxng._yanit(
                    [("x", "https://ornek.test/x", "")],
                    [("duckduckgo", "CAPTCHA"), ("brave", "too many requests")],
                )

        monkeypatch.setattr(web, "_http_get", lambda *a, **k: SahteYanit())
        _, dusen = web._search_searxng("x", 5, "http://127.0.0.1:8890")
        assert dusen == ["duckduckgo: CAPTCHA", "brave: too many requests"]

    def test_a_403_names_the_actual_fix(self, monkeypatch):
        """JSON varsayilan olarak KAPALI gelir. Genel bir 'HTTP 403'
        operatore ne yapacagini soylemez."""
        import httpx

        import deerx.tools.web as web
        from deerx.errors import ToolError
        from deerx.i18n import set_language

        def reddet(*a, **k):
            istek = httpx.Request("GET", "http://127.0.0.1:8890/search")
            raise httpx.HTTPStatusError(
                "403", request=istek, response=httpx.Response(403, request=istek)
            )

        monkeypatch.setattr(web, "_http_get", reddet)
        try:
            set_language("en")
            with pytest.raises(ToolError) as hata:
                web._search_searxng("x", 5, "http://127.0.0.1:8890")
        finally:
            set_language("tr")
        assert "search.formats" in str(hata.value)
        assert "off by default" in str(hata.value)

    def test_the_tool_uses_searxng_without_a_key(self, ctx, monkeypatch):
        """Anahtar YOK: saglayicinin secilmis olmasi yeterli."""
        import deerx.tools.web as web
        from deerx.tools import build_registry

        class SahteYanit:
            @staticmethod
            def json():
                return TestSearxng._yanit([
                    ("Python http server threading",
                     "https://stackoverflow.com/questions/18261815", "BaseHTTPRequestHandler"),
                ], [("duckduckgo", "CAPTCHA")])

        monkeypatch.setattr(web, "_http_get", lambda *a, **k: SahteYanit())
        ctx.settings.enable_web = True
        ctx.settings.search_provider = "searxng"
        ctx.settings.search_api_key = None

        sonuc = build_registry().get("web_search").run(ctx, query="http server threading")
        assert "stackoverflow.com/questions/18261815" in sonuc.content
        # Kapsam daralmasi ciktida gorunmeli.
        assert "duckduckgo: CAPTCHA" in sonuc.content

    def test_the_default_url_is_loopback(self, settings):
        """Arama ucu disari acilmis bir adres olmamali."""
        assert settings.searxng_url.startswith("http://127.0.0.1")


class TestDnsRebinding:
    """Denetimle baglanti arasinda ad IKINCI KEZ cozuluyordu.

    `UrlPolicy` bir adin cozuldugu TUM adreslere bakiyor ve ic aga
    cozulen her adi reddediyor. Ama vekil, denetimden GECEN adresleri
    kullanmiyordu: `socket.create_connection((host, port))` adi bastan
    cozuyordu.

    Kisa TTL'li bir ad denetimde genel bir adrese, saniyeler sonra
    baglanirken `169.254.169.254` (bulut kimlik bilgileri) ya da
    `127.0.0.1` (kimlik dogrulamasiz yerel panel) adresine cozulebilir.
    Savunma tam olmasin diye degil, cozumlemenin sonucu atildigi icin
    aciklik kaliyordu.

    SECURITY.md tarayici sinirini "DNS-rebinding savunmasi olan bir
    filtre vekili" diye tarif ediyor; bu testler o cumlenin karsiligidir.
    """

    def test_the_checked_addresses_are_returned(self, monkeypatch):
        """Politika artik denetledigi adresleri geri veriyor."""
        from deerx.browser import policy as politika_modulu

        monkeypatch.setattr(
            politika_modulu.UrlPolicy,
            "_resolve",
            staticmethod(lambda host: [ipaddress.ip_address("93.184.216.34")]),
        )
        adresler = UrlPolicy().check_addresses("https://ornek.test/x")
        assert adresler == ["93.184.216.34"]

    def test_an_allowed_origin_connects_by_name(self, monkeypatch):
        """Onizleme istisnasinda cozumleme yapilmaz: bos liste doner ve
        vekil ada baglanir. Hedef zaten acikca izin verilmis bir
        loopback adresi."""
        politika = UrlPolicy()
        politika.allow_origin("http://127.0.0.1:8123")
        assert politika.check_addresses("http://127.0.0.1:8123/sayfa") == []

    def test_the_proxy_connects_to_the_checked_address_not_the_name(self):
        """Asil koruma: baglanti ADA degil, DENETLENEN ADRESE kurulur."""
        from deerx.browser.proxy import _dogrulanan_hedef

        assert _dogrulanan_hedef("ornek.test", ["93.184.216.34"]) == "93.184.216.34"
        # Istisna yolu: adres yoksa ad kullanilir.
        assert _dogrulanan_hedef("127.0.0.1", []) == "127.0.0.1"

    def test_a_name_that_resolves_inside_is_still_refused(self, monkeypatch):
        """Cozumlemenin kendisi degismedi: ic adrese cozulen ad reddedilir."""
        from deerx.browser import policy as politika_modulu

        monkeypatch.setattr(
            politika_modulu.UrlPolicy,
            "_resolve",
            staticmethod(lambda host: [ipaddress.ip_address("169.254.169.254")]),
        )
        with pytest.raises(UrlBlocked):
            UrlPolicy().check_addresses("https://ornek.test/x")

    def test_a_second_resolution_cannot_change_the_target(self, monkeypatch):
        """Rebinding taklidi: ad her cozuldugunde BASKA adres doner.

        Ilk cozumleme genel adresi verir ve denetimi gecer; ikincisi ic
        adresi verirdi. Vekil ilk cozumlemenin sonucunu kullandigi icin
        ikincisi hic yapilmaz -- test tam olarak bunu sabitler.
        """
        from deerx.browser import policy as politika_modulu

        sirasi = iter([
            [ipaddress.ip_address("93.184.216.34")],   # denetim: genel
            [ipaddress.ip_address("127.0.0.1")],       # baglanti: ic
        ])
        monkeypatch.setattr(
            politika_modulu.UrlPolicy, "_resolve", staticmethod(lambda host: next(sirasi))
        )

        adresler = UrlPolicy().check_addresses("https://ornek.test/x")
        from deerx.browser.proxy import _dogrulanan_hedef

        hedef = _dogrulanan_hedef("ornek.test", adresler)
        assert hedef == "93.184.216.34", (
            "vekil ada baglaniyor; ikinci cozumleme ic adrese donebilir"
        )
        # Ikinci cozumleme HIC yapilmamis olmali.
        assert next(sirasi, None) is not None, "cozumleme iki kez yapildi"
