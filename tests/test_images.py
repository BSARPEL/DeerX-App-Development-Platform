"""Gorsel arama ve indirme: sunum ve mockup'lar icin.

Ajan web'de METIN arayabiliyordu ama gorsel bulamiyor, bulsa da
indiremiyordu: `fetch_url` `response.text` kullanir, ikili veri tasimaz.
Sunum uretirken elinde yalnizca CSS ile cizilmis kutular kaliyordu.
"""

from __future__ import annotations

import pytest

from deerx.config import Settings
from deerx.logging import EventLog
from deerx.tools import ToolContext, build_registry
from deerx.tools.images import SERBEST_MOTORLAR, _uzanti


class TestGorselDogrulama:
    """Icerik turune GUVENILMEZ: sunucu yanlis bildirebilir."""

    @pytest.mark.parametrize(
        ("veri", "beklenen"),
        [
            (b"\x89PNG\r\n\x1a\n" + b"x" * 8, ".png"),
            (b"\xff\xd8\xff\xe0" + b"x" * 8, ".jpg"),
            (b"GIF89a" + b"x" * 8, ".gif"),
            (b"RIFF" + b"1234" + b"WEBP", ".webp"),
            (b"<svg xmlns=", ".svg"),
        ],
    )
    def test_real_images_are_recognised_by_their_bytes(self, veri, beklenen):
        assert _uzanti(veri) == beklenen

    @pytest.mark.parametrize(
        "veri",
        [
            b"<!DOCTYPE html><html>",   # hata sayfasi
            b"{\"error\": \"nope\"}",   # JSON hata
            b"RIFF1234AVI ",            # RIFF ama WEBP degil
            b"",
        ],
    )
    def test_what_is_not_an_image_is_refused(self, veri):
        """Sunucu 200 doner ama govde hata sayfasidir; kaydedilirse
        sunumda kirik gorsel olarak gorunur ve sebebi anlasilmaz."""
        assert _uzanti(veri) is None


class TestLisans:
    """Rastgele bir web fotografini teslimata koymak telif riski uretir."""

    def test_the_free_sources_are_the_ones_with_a_known_licence(self):
        for motor in ("openverse", "wikicommons.images", "unsplash", "pexels"):
            assert motor in SERBEST_MOTORLAR
        for motor in ("bing images", "pinterest", "google cse images"):
            assert motor not in SERBEST_MOTORLAR, (
                f"{motor} lisansi belirsiz; serbest sayilmamali"
            )

    def test_a_source_that_refuses_downloads_is_not_offered(self):
        """Kamu mali olmak yetmez; INDIRILEBILIR de olmali.

        Olculdu: Art Institute of Chicago'nun IIIF ucu bes ayri sorguda bes
        kez HTTP 403 dondu, kimlikli istekte bile. Listede kalsaydi
        siralamada uste cikip ajani her seferinde garanti bir cikmaza
        sokardi: gorur, secer, indiremez, turunu harcar.
        """
        assert "artic" not in SERBEST_MOTORLAR

    def test_searching_without_searxng_says_so(self, tmp_path):
        """Sessizce bos donmek, ajanin 'gorsel yok' sanmasina yol acar."""
        ayar = Settings(workspace=tmp_path, approval_mode="auto", searxng_url="")
        ayar.ensure_dirs()
        ctx = ToolContext(settings=ayar, events=EventLog(None, echo=False))
        sonuc = build_registry().execute("find_images", {"query": "x"}, ctx)
        assert sonuc.is_error
        assert "SearXNG" in sonuc.content

    def test_the_web_switch_is_honoured(self, tmp_path):
        ayar = Settings(workspace=tmp_path, approval_mode="auto", enable_web=False)
        ayar.ensure_dirs()
        ctx = ToolContext(settings=ayar, events=EventLog(None, echo=False))
        for arac in ("find_images", "download_image"):
            args = {"query": "x"} if arac == "find_images" else {
                "url": "https://ornek.test/a.jpg", "name": "a"
            }
            assert build_registry().execute(arac, args, ctx).is_error


class TestAraclarKayitli:
    def test_both_tools_exist(self):
        from deerx.tools.images import IMAGE_TOOLS

        adlar = {a.name for a in IMAGE_TOOLS}
        assert adlar == {"find_images", "download_image"}

    def test_the_roles_that_build_slides_can_use_them(self):
        """Mockup rolu sunum uretir; gorsel araci olmadan elinde yalnizca
        CSS kutulari kalir."""
        from deerx.tools import TOOLSETS

        for rol in ("mockup", "researcher"):
            assert "find_images" in TOOLSETS[rol], rol
            assert "download_image" in TOOLSETS[rol], rol

    def test_downloading_is_marked_dangerous(self):
        """Aga cikip calisma alanina dosya yazar: onay kapisindan gecmeli."""
        from deerx.tools.images import DownloadImage

        assert DownloadImage.dangerous is True


class TestDogrulamaCagriYerineBagli:
    """Yardimciyi tek basina test etmek YETMEZ.

    `_uzanti` testleri gecerken `run` icindeki reddi kaldirdim ve hicbiri
    dusmedi: dogrulama hicbir yere bagli olmasaydi da gecerlerdi. Bu
    oturumda ayni tuzaga bir kez daha dusmustum (`header_safe`).
    """

    @staticmethod
    def _cevap(monkeypatch, govde: bytes, content_type: str = "image/jpeg"):
        import httpx

        class SahteCevap:
            content = govde
            headers = {"content-type": content_type}

            def raise_for_status(self):
                return None

        class SahteIstemci:
            def __init__(self, *_a, **_k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_e):
                return False

            def get(self, _url):
                return SahteCevap()

        monkeypatch.setattr(httpx, "Client", SahteIstemci)

    def test_an_error_page_is_not_saved_as_an_image(self, tmp_path, monkeypatch):
        """Sunucu 200 doner ama govde HTML hata sayfasidir. Kaydedilirse
        sunumda kirik gorsel olarak gorunur ve sebebi anlasilmaz."""
        self._cevap(monkeypatch, b"<!DOCTYPE html><html>hata</html>")
        ayar = Settings(workspace=tmp_path, approval_mode="auto")
        ayar.ensure_dirs()
        ctx = ToolContext(settings=ayar, events=EventLog(None, echo=False))
        sonuc = build_registry().execute(
            "download_image",
            {"url": "https://example.com/a.jpg", "name": "kapak"},
            ctx,
        )
        assert sonuc.is_error, "hata sayfasi gorsel olarak kaydedildi"
        assert not list(ayar.artifacts_dir.glob("kapak*")), "dosya yazilmis"

    def test_a_real_image_is_saved(self, tmp_path, monkeypatch):
        """Isirma karsiti: gecerli baytlar kaydedilmeli."""
        self._cevap(monkeypatch, b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        ayar = Settings(workspace=tmp_path, approval_mode="auto")
        ayar.ensure_dirs()
        ctx = ToolContext(settings=ayar, events=EventLog(None, echo=False))
        sonuc = build_registry().execute(
            "download_image",
            {"url": "https://example.com/a", "name": "kapak"},
            ctx,
        )
        assert not sonuc.is_error, sonuc.content
        assert (ayar.artifacts_dir / "kapak.png").is_file(), "uzanti baytlardan gelmeli"
