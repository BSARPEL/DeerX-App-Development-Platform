"""Ekran goruntusu modele GONDERILIYOR mu?

Eskiden `browser_screenshot` PNG'yi diske yazip modele yalnizca
"kaydedildi" diyordu. Goruntu hicbir zaman modele ulasmiyordu: ajan
sayfanin YAPISINI goruyordu (`browser_snapshot`), gorunusunu degil.
Hizalama bozuklugu, ust uste binen kutular, kirpilmis gorsel ve okunmayan
metin onun dongusunun disindaydi.

Olculdu: yerel vLLM ucundaki model goruyor -- ekran goruntusune yazilmis
rastgele bir kodu (`7460-8524`) aynen okudu.
"""

from __future__ import annotations

import pytest

from deerx.llm.base import ToolOutcome
from deerx.llm.openai_client import (
    MAX_GORSEL_BAYT,
    OpenAICompatibleClient,
    _gorsel_blogu,
    _gorsel_reddi_mi,
    _gorselleri_cikar,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


def _png(tmp_path, ad="ekran.png", boyut=200):
    p = tmp_path / ad
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * boyut)
    return p


class TestGorselBlogu:
    def test_a_png_becomes_a_data_url(self, tmp_path):
        blok = _gorsel_blogu(_png(tmp_path))
        assert blok["type"] == "image_url"
        assert blok["image_url"]["url"].startswith("data:image/png;base64,")

    def test_an_unknown_extension_is_skipped(self, tmp_path):
        p = tmp_path / "not.txt"
        p.write_bytes(b"merhaba")
        assert _gorsel_blogu(p) is None

    def test_a_missing_file_is_skipped(self, tmp_path):
        assert _gorsel_blogu(tmp_path / "yok.png") is None

    def test_an_oversized_image_is_skipped_not_sent(self, tmp_path):
        """base64 uc kat buyutur; sinirsiz birakmak baglami birkac
        goruntude doldurur."""
        assert _gorsel_blogu(_png(tmp_path, boyut=MAX_GORSEL_BAYT + 10)) is None


class TestAracSonucununGorseli:
    """OpenAI biciminde `role: "tool"` mesajlari GORSEL TASIYAMAZ."""

    @staticmethod
    def _istemci():
        return OpenAICompatibleClient.__new__(OpenAICompatibleClient)

    def test_the_image_travels_in_a_separate_user_message(self, tmp_path):
        c = self._istemci()
        c.gorsel_gonder = True
        mesajlar: list[dict] = []
        c.append_tool_results(mesajlar, [ToolOutcome(
            call_id="c1", name="browser_screenshot",
            content="kaydedildi", images=[_png(tmp_path)])])

        assert mesajlar[0]["role"] == "tool"
        assert isinstance(mesajlar[0]["content"], str), (
            "arac mesajinin icerigi metin olmali; gorsel oraya konamaz"
        )
        assert mesajlar[-1]["role"] == "user"
        assert [b["type"] for b in mesajlar[-1]["content"]] == ["text", "image_url"]

    def test_no_extra_message_when_there_is_no_image(self, tmp_path):
        c = self._istemci()
        c.gorsel_gonder = True
        mesajlar: list[dict] = []
        c.append_tool_results(mesajlar, [ToolOutcome(
            call_id="c1", name="read_file", content="icerik")])
        assert len(mesajlar) == 1

    def test_nothing_is_sent_once_the_endpoint_refused(self, tmp_path):
        """Uc bir kez reddettiyse israr etmek her turda ayni hatayi uretir."""
        c = self._istemci()
        c.gorsel_gonder = False
        mesajlar: list[dict] = []
        c.append_tool_results(mesajlar, [ToolOutcome(
            call_id="c1", name="browser_screenshot",
            content="kaydedildi", images=[_png(tmp_path)])])
        assert len(mesajlar) == 1


class TestGeriDusme:
    @pytest.mark.parametrize("metin", [
        "This model does not support image input",
        "vision is not supported for this model",
        "invalid content type: image_url",
        "multimodal input rejected",
    ])
    def test_an_image_refusal_is_recognised(self, metin):
        assert _gorsel_reddi_mi(Exception(metin))

    @pytest.mark.parametrize("metin", [
        "rate limit exceeded", "context length exceeded", "connection reset",
    ])
    def test_other_errors_are_not_mistaken_for_it(self, metin):
        """Yanlis pozitif kosu boyunca goruntuyu kapatirdi."""
        assert not _gorsel_reddi_mi(Exception(metin))

    def test_images_are_stripped_from_the_whole_history(self):
        """Yalnizca son istegi temizlemek yetmez: gecmiste kalan bir
        goruntu sonraki HER turda ayni reddi uretir."""
        mesajlar = [
            {"role": "user", "content": [
                {"type": "text", "text": "bak"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
            ]},
            {"role": "assistant", "content": "tamam"},
        ]
        _gorselleri_cikar(mesajlar)
        turler = [b["type"] for b in mesajlar[0]["content"]]
        assert "image_url" not in turler
        assert turler, "bos icerik birakmak istegi bozar"


class TestCagriYerineBagli:
    """Yardimciyi test etmek, aracin onu KULLANDIGINI dogrulamaz.

    Bu oturumda ayni tuzaga iki kez dusuldu (`header_safe`, `_uzanti`).
    """

    def test_the_screenshot_tool_attaches_the_file(self):
        import inspect

        from deerx.tools import browser

        kaynak = inspect.getsource(browser)
        assert "images=[target]" in kaynak, (
            "browser_screenshot goruntuyu sonuca eklemiyor; model yine "
            "yalnizca 'kaydedildi' gorur"
        )
