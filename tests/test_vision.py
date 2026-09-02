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


class TestAnthropicDeGonderir:
    """Anthropic istemcisi goruntuleri HIC islemiyordu.

    `ToolResult.images` "modelin GORMESI gereken dosyalar" diye tanimli.
    OpenAI istemcisi onlari gonderiyordu; `AnthropicClient.append_tool_results`
    alani hic okumuyordu. Yani `provider = "anthropic"` ile kosan bir ajan
    `browser_screenshot` cagirdiginda modele yalnizca "kaydedildi" metni
    gidiyordu -- Claude goruyor olmasina ragmen.

    Bu dosyanin kendisi eksigin bir parcasiydi: butun testler OpenAI
    istemcisine bakiyordu. `architecture.md` tam olarak bu tuzagi tarif
    ediyor, ve sozlesme testi yakalayamaz cunku metodun VARLIGINA bakiyor,
    ne yaptigina degil.
    """

    @staticmethod
    def _ekle(images):
        from deerx.llm.anthropic_client import AnthropicClient

        mesajlar = []
        AnthropicClient.append_tool_results(
            AnthropicClient.__new__(AnthropicClient),
            mesajlar,
            [
                ToolOutcome(
                    call_id="t1",
                    name="browser_screenshot",
                    content="ekran.png kaydedildi",
                    images=images,
                )
            ],
        )
        return mesajlar

    def test_the_image_reaches_the_model(self, tmp_path):
        sonuc = self._ekle([_png(tmp_path)])[0]["content"][0]
        assert sonuc["type"] == "tool_result"
        turler = [b["type"] for b in sonuc["content"]]
        assert "image" in turler, (
            "goruntu tool_result icine konmamis; model ekrani goremez"
        )
        gorsel = next(b for b in sonuc["content"] if b["type"] == "image")
        assert gorsel["source"]["type"] == "base64"
        assert gorsel["source"]["media_type"] == "image/png"
        assert gorsel["source"]["data"]

    def test_the_text_travels_with_it(self, tmp_path):
        """Goruntu metnin YERINE gecmemeli: ajan dosya adini da gorur."""
        sonuc = self._ekle([_png(tmp_path)])[0]["content"][0]
        metinler = [b["text"] for b in sonuc["content"] if b["type"] == "text"]
        assert metinler == ["ekran.png kaydedildi"]

    def test_without_an_image_the_shape_is_unchanged(self):
        """Goruntusuz sonuc duz metin kalmali; her sonucu listeye cevirmek
        gereksiz bir bicim degisikligi olurdu."""
        sonuc = self._ekle([])[0]["content"][0]
        assert sonuc["content"] == "ekran.png kaydedildi"

    def test_an_unreadable_image_does_not_break_the_result(self, tmp_path):
        """Silinmis ya da cok buyuk bir dosya sonucu dusurmemeli."""
        sonuc = self._ekle([tmp_path / "yok.png"])[0]["content"][0]
        assert sonuc["content"] == "ekran.png kaydedildi"


class TestGoruntuTokenTahmini:
    """Tek bir ekran goruntusu kosuyu olduruyordu.

    `_estimate_input` mesajlari JSON'a cevirip KARAKTER sayiyordu ve
    base64 bir goruntu devasa bir dizedir. Saglayici goruntuyu base64
    uzunluguna gore degil ALANINA gore fiyatlar.

    OLCULDU: 1 MB'lik bir PNG icin tahmin 559.816 token, gercek maliyeti
    ~1.600. 262K pencereli bir uctan `room` negatife dusuyor ve
    `_fit_output` `context_overflow` firlatiyordu -- yani ajanin ILK
    ekran goruntusu kosuyu bitiriyordu, ustelik hata baglamin gercekten
    dolduugunu soyluyordu.
    """

    @staticmethod
    def _goruntulu_payload(ham_bayt):
        import base64

        b64 = base64.b64encode(b"\x00" * ham_bayt).decode()
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ekran goruntusu ekte"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "tools": [],
        }

    def test_a_megabyte_image_does_not_fill_the_window(self):
        tahmin = OpenAICompatibleClient._estimate_input(
            self._goruntulu_payload(1024 * 1024)
        )
        assert tahmin < 10_000, (
            f"{tahmin:,} token tahmin edildi; base64 hala metin olarak "
            "sayiliyor ve tek goruntu 262K'lik pencereyi tasiriyor"
        )

    def test_the_estimate_does_not_grow_with_the_file(self):
        """Goruntunun maliyeti alanina bagli, bayt sayisina degil."""
        kucuk = OpenAICompatibleClient._estimate_input(
            self._goruntulu_payload(200 * 1024)
        )
        buyuk = OpenAICompatibleClient._estimate_input(
            self._goruntulu_payload(2 * 1024 * 1024)
        )
        assert kucuk == buyuk

    def test_text_is_still_counted(self):
        """Duzeltme metni saymayi birakmamali."""
        az = OpenAICompatibleClient._estimate_input(
            {"messages": [{"role": "user", "content": "kisa"}], "tools": []}
        )
        cok = OpenAICompatibleClient._estimate_input(
            {"messages": [{"role": "user", "content": "x" * 100_000}], "tools": []}
        )
        assert cok > az + 20_000

    def test_the_payload_is_not_mutated(self):
        """Tahmin, gonderilecek mesajlara dokunmamali."""
        payload = self._goruntulu_payload(1024)
        OpenAICompatibleClient._estimate_input(payload)
        turler = [b["type"] for b in payload["messages"][0]["content"]]
        assert "image_url" in turler


class TestEskiGoruntulerDusuyor:
    """Goruntuler gecmiste sinirsiz birikiyordu.

    Iki kirpicinin ikisi de goruntulere dokunmuyordu: OpenAI tarafi
    yalnizca `role: "tool"` mesajlarini, Anthropic tarafi yalnizca metin
    icerikli `tool_result` bloklarini kirpiyordu. Bir QA fazi on ekran
    goruntusu alabiliyor ve hepsi kosunun sonuna kadar her turda yeniden
    gonderiliyordu.
    """

    def test_openai_keeps_only_the_recent_ones(self):
        from deerx.llm.base import KEEP_RECENT_IMAGES

        def gorsel_mesaji(n):
            return {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"goruntu {n}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{n}"},
                    },
                ],
            }

        mesajlar = [gorsel_mesaji(i) for i in range(6)]
        OpenAICompatibleClient.trim_history(mesajlar)

        kalan = sum(
            1 for m in mesajlar for b in m["content"] if b["type"] == "image_url"
        )
        assert kalan == KEEP_RECENT_IMAGES
        # Kalanlar EN YENILER olmali.
        assert any(b["type"] == "image_url" for b in mesajlar[-1]["content"])
        # Metin yerinde kalmali; bos icerik istegi bozardi.
        assert all(m["content"] for m in mesajlar)

    def test_anthropic_keeps_only_the_recent_ones(self):
        from deerx.llm.anthropic_client import AnthropicClient
        from deerx.llm.base import KEEP_RECENT_IMAGES

        def sonuc_mesaji(n):
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"t{n}",
                        "content": [
                            {"type": "text", "text": f"ekran{n}.png kaydedildi"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": str(n),
                                },
                            },
                        ],
                    }
                ],
            }

        mesajlar = [sonuc_mesaji(i) for i in range(6)]
        AnthropicClient.trim_history(mesajlar)

        kalan = sum(
            1
            for m in mesajlar
            for blok in m["content"]
            for ic in blok["content"]
            if isinstance(ic, dict) and ic.get("type") == "image"
        )
        assert kalan == KEEP_RECENT_IMAGES
        # `tool_result` bloklari ve `tool_use_id`leri yerinde kalmali:
        # eslesmeyi bozmak istegi kalici olarak dusururdu.
        assert all(
            m["content"][0]["type"] == "tool_result" and m["content"][0]["tool_use_id"]
            for m in mesajlar
        )
