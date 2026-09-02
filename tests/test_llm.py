"""LLM saglayici katmani: bicim cevirisi, gecmis yonetimi, fiyatlandirma."""

from __future__ import annotations

import json

import pytest

from deerx.errors import ConfigError, LLMError
from deerx.llm import (
    LLMResult,
    ToolCall,
    ToolOutcome,
    build_client,
    to_openai_tools,
)
from deerx.llm.anthropic_client import AnthropicClient
from deerx.llm.openai_client import OpenAICompatibleClient
from deerx.llm.pricing import Usage, cost_usd, is_local_model, price_for


def make_result(raw, calls=None) -> LLMResult:
    return LLMResult(
        text="", thinking="", tool_calls=calls or [], stop_reason="tool_use",
        usage=Usage(), cost=0.0, model="test", raw=raw,
    )


class TestToolTranslation:
    def test_anthropic_shape_becomes_openai_function(self):
        specs = [
            {
                "name": "read_file",
                "description": "Dosyayi okur",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]
        converted = to_openai_tools(specs)
        assert converted == [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Dosyayi okur",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

    def test_anthropic_server_tools_are_dropped(self):
        """Sunucu araclari Anthropic altyapisinda calisir; yerel modelde karsiligi yok."""
        specs = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
            {"name": "read_file", "description": "x", "input_schema": {"type": "object"}},
        ]
        converted = to_openai_tools(specs)
        assert [c["function"]["name"] for c in converted] == ["read_file"]


class TestOpenAIHistory:
    def test_tool_results_become_separate_messages(self):
        """OpenAI biciminde her arac sonucu ayri bir mesajdir."""
        messages: list = []
        OpenAICompatibleClient.append_tool_results(
            None,  # type: ignore[arg-type]
            messages,
            [
                ToolOutcome(call_id="a", name="read_file", content="icerik"),
                ToolOutcome(call_id="b", name="grep_files", content="eslesme", is_error=True),
            ],
        )
        assert [m["role"] for m in messages] == ["tool", "tool"]
        assert messages[0]["tool_call_id"] == "a"
        assert messages[1]["content"] == "eslesme"

    def test_assistant_without_content_gets_empty_string(self):
        """Bos icerik gonderen sunucular hata verir; None yerine bos metin konur."""
        messages: list = []
        OpenAICompatibleClient.append_assistant(
            None,  # type: ignore[arg-type]
            messages,
            make_result({"content": None, "tool_calls": []}),
        )
        assert messages[0]["content"] == ""

    def test_assistant_keeps_tool_calls(self):
        messages: list = []
        raw = {"content": None, "tool_calls": [{"id": "a", "type": "function",
                                                "function": {"name": "x", "arguments": "{}"}}]}
        OpenAICompatibleClient.append_assistant(None, messages, make_result(raw))  # type: ignore[arg-type]
        assert messages[0]["tool_calls"] == raw["tool_calls"]

    def test_trim_only_touches_tool_messages(self):
        messages = [{"role": "user", "content": "baslangic"}]
        for index in range(40):
            messages.append({"role": "assistant", "content": "kisa"})
            messages.append(
                {"role": "tool", "tool_call_id": f"t{index}", "content": "y" * 20_000}
            )
        trimmed = OpenAICompatibleClient.trim_history(messages)
        assert trimmed > 0
        assert messages[0]["content"] == "baslangic"
        # Son mesajlar dokunulmadan kalmali.
        assert len(messages[-1]["content"]) == 20_000


class TestAnthropicHistory:
    def test_tool_results_share_one_message(self):
        """Anthropic'te sonuclari bolmek modeli paralel arac kullanmaktan cayirir."""
        messages: list = []
        AnthropicClient.append_tool_results(
            None,  # type: ignore[arg-type]
            messages,
            [
                ToolOutcome(call_id="a", name="x", content="1"),
                ToolOutcome(call_id="b", name="y", content="2"),
            ],
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert {b["tool_use_id"] for b in messages[0]["content"]} == {"a", "b"}


class TestPricing:
    def test_local_models_are_free(self):
        assert is_local_model("qwen3.8 max")
        assert price_for("qwen3.8 max") == (0.0, 0.0)
        assert cost_usd("qwen3.8 max", Usage(input_tokens=10**6, output_tokens=10**6)) == 0.0

    def test_claude_models_are_priced(self):
        assert not is_local_model("claude-opus-5")
        assert cost_usd("claude-opus-5", Usage(output_tokens=1_000_000)) == pytest.approx(25.0)

    def test_unknown_claude_id_falls_back_high(self):
        assert price_for("claude-gelecek-9") == (5.00, 25.00)


class TestFactory:
    def test_builds_openai_client_for_local_provider(self, settings):
        settings.provider = "openai"
        settings.openai_base_url = "http://127.0.0.1:8008/v1"
        assert isinstance(build_client(settings), OpenAICompatibleClient)

    def test_openai_provider_needs_base_url(self, settings):
        settings.provider = "openai"
        settings.openai_base_url = None
        with pytest.raises(ConfigError, match="openai_base_url"):
            build_client(settings)

    def test_unknown_provider(self, settings):
        settings.provider = "uydurma"  # type: ignore[assignment]
        with pytest.raises(ConfigError, match="Bilinmeyen saglayici"):
            build_client(settings)


class TestStopReasonMapping:
    @pytest.mark.parametrize(
        ("finish", "expected"),
        [
            ("tool_calls", "tool_use"),
            ("stop", "end_turn"),
            ("length", "max_tokens"),
            ("content_filter", "refusal"),
        ],
    )
    def test_openai_finish_reasons_map_to_loop_vocabulary(self, finish, expected):
        result = OpenAICompatibleClient._parse(
            {"content": "x", "tool_calls": []}, Usage(), finish, "qwen3.8 max"
        )
        assert result.stop_reason == expected

    def test_malformed_tool_arguments_do_not_crash(self):
        """Model bozuk JSON uretebilir; dongu kirilmasin, arac hatayi bildirsin."""
        message = {
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function",
                 "function": {"name": "read_file", "arguments": "{bozuk json"}}
            ],
        }
        result = OpenAICompatibleClient._parse(message, Usage(), "tool_calls", "qwen3.8 max")
        assert result.tool_calls == [
            ToolCall(id="a", name="read_file", arguments={}, arguments_ok=False)
        ]

    def test_malformed_arguments_are_flagged_not_silently_emptied(self):
        """Bos sozluge dusurmek yetmez; DUSURULDUGU de bilinmeli.

        Eskiden `arguments = {}` yazilip gecilirdi ve ajan dongusu saglam bir
        cagriyla yarim kalan bir cagriyi ayirt edemiyordu. Olculdu (yerel
        vLLM, uctan uca kosunun mockup fazi): model tam 32000 token uretti,
        JSON argumanlari yarida kesildi, saglayici cagriyi yine de dondurdu
        ve arac BOS argumanla kosturuldu. Modele giden tek ipucu
        `SaveArtifact.run() missing 2 required positional arguments` oldu.
        """
        def cozumle(ham):
            mesaj = {"content": None, "tool_calls": [
                {"id": "a", "type": "function",
                 "function": {"name": "read_file", "arguments": ham}}]}
            return OpenAICompatibleClient._parse(
                mesaj, Usage(), "tool_calls", "qwen3.8 max").tool_calls[0]

        assert cozumle('{"path": "a.txt"}').arguments_ok is True
        assert cozumle("{yarim").arguments_ok is False
        # JSON gecerli ama sozluk degil: yine kullanilamaz.
        assert cozumle("[1, 2]").arguments_ok is False


class TestContextWindow:
    """Uretim tavani girdinin buyuklugune gore kirpilmali.

    Yasanan hata: `max_tokens = 220_000` sabitti ve girdi buyudukce toplam
    pencereyi asti. Uc istegi geri cevirdi:

        This model's maximum context length is 262144 tokens. However, you
        requested 220000 output tokens and your prompt contains at least
        42145 input tokens, for a total of at least 262145 tokens.

    Bir token'lik tasma butun kosuyu durduruyordu. `max_tokens` bir tavandir,
    ayrilmis yer degil -- ama uc ikisini toplayip pencereyle karsilastirir.
    """

    WINDOW = 262_144

    @pytest.fixture()
    def client(self, settings):
        settings.provider = "openai"
        settings.openai_base_url = "http://127.0.0.1:8008/v1"
        instance = OpenAICompatibleClient(settings)
        # Ucu aramadan: pencere zaten bilinsin.
        instance._windows["qwen3.8 max"] = self.WINDOW
        return instance

    @staticmethod
    def _payload(chars: int) -> dict:
        return {"messages": [{"role": "user", "content": "a" * chars}], "tools": []}

    def test_settings_value_beats_asking_the_endpoint(self, settings):
        settings.provider = "openai"
        settings.openai_base_url = "http://127.0.0.1:8008/v1"
        settings.context_window = 8192
        instance = OpenAICompatibleClient(settings)
        # Onbellek doldurulmadi; yine de aga cikmadan cevap vermeli.
        assert instance._context_window("qwen3.8 max") == 8192

    def test_small_prompt_keeps_the_ceiling(self, client):
        assert client._fit_output(self._payload(50), 1000, "qwen3.8 max") == 1000

    def test_large_prompt_shrinks_the_ceiling(self, client):
        payload = self._payload(400_000)
        estimated = client._estimate_input(payload)
        fitted = client._fit_output(payload, 220_000, "qwen3.8 max")
        assert fitted < 220_000
        assert fitted == self.WINDOW - estimated

    @pytest.mark.parametrize("chars", [1_000, 100_000, 300_000, 500_000])
    def test_total_never_exceeds_the_window(self, client, chars):
        """Asil sozlesme bu: girdi + cikti pencereye sigmali."""
        payload = self._payload(chars)
        fitted = client._fit_output(payload, 220_000, "qwen3.8 max")
        assert client._estimate_input(payload) + fitted <= self.WINDOW

    def test_unknown_window_leaves_the_request_alone(self, client):
        """Uydurma bir sinir koymak, dogru calisan bir kurulumu daraltirdi."""
        client._windows["gizemli"] = None
        assert client._fit_output(self._payload(400_000), 220_000, "gizemli") == 220_000

    def test_prompt_that_cannot_fit_gets_a_clear_error(self, client):
        with pytest.raises(LLMError, match="baglam penceresine sigmiyor"):
            client._fit_output(self._payload(2_000_000), 220_000, "qwen3.8 max")

    def test_input_estimate_is_pessimistic(self, client):
        """Az tahmin etmek istegi 400'e goturur; cok tahmin etmek yalnizca
        hicbir zaman dolmayan tavani kisar. Yon bilincli secildi."""
        payload = self._payload(36_000)
        # Gercek tokenizer duz metinde ~3.6 karakter/token verir; bizimki
        # bundan daha cok token saymali.
        assert client._estimate_input(payload) > 36_000 / 3.6


class TestContextErrorRecovery:
    """Tahmin tutmazsa ucun kendi sayilariyla bir kez tekrar denenir."""

    REAL_ERROR = (
        "Error code: 400 - {'error': {'message': \"This model's maximum context "
        "length is 262144 tokens. However, you requested 220000 output tokens "
        "and your prompt contains at least 42145 input tokens, for a total of "
        "at least 262145 tokens. Please reduce the length of the input prompt "
        "or the number of requested output tokens.\", 'type': 'BadRequestError', "
        "'param': 'input_tokens', 'code': 400}}"
    )

    @pytest.fixture()
    def client(self, settings):
        settings.provider = "openai"
        settings.openai_base_url = "http://127.0.0.1:8008/v1"
        return OpenAICompatibleClient(settings)

    def test_endpoint_numbers_are_extracted(self, client):
        assert client._corrected_output(Exception(self.REAL_ERROR)) == 262_144 - 42_145

    def test_unrelated_errors_are_not_swallowed(self, client):
        """Baska her hata oldugu gibi yukari gitmeli; sessizce tekrar
        denenen bir baglanti hatasi sebebi gizler."""
        for message in ("baglanti koptu", "401 Unauthorized", ""):
            assert client._corrected_output(Exception(message)) is None

    def test_no_retry_when_nothing_would_fit(self, client):
        """Girdi zaten pencereyi dolduruyorsa tekrar denemek anlamsiz."""
        message = (
            "This model's maximum context length is 1000 tokens. However, you "
            "requested 900 output tokens and your prompt contains at least 999 "
            "input tokens, for a total of at least 1899 tokens."
        )
        assert client._corrected_output(Exception(message)) is None


class TestStreamRetry:
    """Kopan bir akis butun kosuyu durdurmemeli.

    Tam bir boru hatti kosusunda olculdu: `design` fazi "peer closed
    connection without sending complete message body (incomplete chunked
    read)" ile dustu ve 46 dakikalik kosu altinci fazda kaldi. SDK'nin
    `max_retries` ayari istek BASLAMADAN onceki hatalari kapsar; akisin
    ortasinda kopan baglanti ona gorunmez.
    """

    GERCEK = (
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)"
    )

    @pytest.fixture()
    def client(self, settings, monkeypatch):
        settings.provider = "openai"
        settings.openai_base_url = "http://127.0.0.1:8008/v1"
        instance = OpenAICompatibleClient(settings)
        instance._windows["test-model"] = 100_000
        # Bekleme testte anlamsiz.
        monkeypatch.setattr("deerx.llm.openai_client.time.sleep", lambda _: None)
        return instance

    @staticmethod
    def _cagir(client, model="test-model"):
        return client.complete(
            role="lead", system="s", messages=[{"role": "user", "content": "merhaba"}],
            model=model, thinking=False,
        )

    def test_the_real_error_is_recognised_as_transient(self):
        from deerx.llm.openai_client import _is_transient

        assert _is_transient(Exception(self.GERCEK))

    @pytest.mark.parametrize(
        "mesaj",
        [
            "401 Unauthorized: invalid api key",
            "model 'yok' bulunamadi",
            "invalid_request_error: tool schema gecersiz",
        ],
    )
    def test_permanent_failures_are_not_retried(self, mesaj):
        """Kimlik ve gecersiz istek tekrarlamakla gecmez; yalnizca zaman yakar."""
        from deerx.llm.openai_client import _is_transient

        assert not _is_transient(Exception(mesaj))

    def test_a_broken_stream_is_retried_and_succeeds(self, client, monkeypatch):
        cagri = {"n": 0}

        def _stream(payload, on_text):
            cagri["n"] += 1
            if cagri["n"] == 1:
                raise RuntimeError(self.GERCEK)
            return {"content": "oldu", "tool_calls": []}, Usage(), "stop"

        monkeypatch.setattr(client, "_stream", _stream)
        sonuc = self._cagir(client)
        assert sonuc.text == "oldu"
        assert cagri["n"] == 2, "tekrar denenmedi"

    def test_a_permanent_failure_stops_at_the_first_attempt(self, client, monkeypatch):
        cagri = {"n": 0}

        def _stream(payload, on_text):
            cagri["n"] += 1
            raise RuntimeError("401 Unauthorized")

        monkeypatch.setattr(client, "_stream", _stream)
        with pytest.raises(LLMError, match="401"):
            self._cagir(client)
        assert cagri["n"] == 1, "kalici hata tekrarlandi"

    def test_retries_are_bounded(self, client, monkeypatch):
        """Sonsuza kadar denenmez: uc kapaliysa kosu ilerlemeli, asili kalmamali."""
        from deerx.llm.openai_client import _STREAM_RETRIES

        cagri = {"n": 0}

        def _stream(payload, on_text):
            cagri["n"] += 1
            raise RuntimeError(self.GERCEK)

        monkeypatch.setattr(client, "_stream", _stream)
        with pytest.raises(LLMError):
            self._cagir(client)
        assert cagri["n"] == _STREAM_RETRIES + 1

    def test_the_context_correction_still_works_alongside(self, client, monkeypatch):
        """Iki tekrar sebebi birbirini bozmamali."""
        cagri = {"n": 0}

        def _stream(payload, on_text):
            cagri["n"] += 1
            if cagri["n"] == 1:
                raise RuntimeError(
                    "This model's maximum context length is 100000 tokens. However, "
                    "you requested 99000 output tokens and your prompt contains at "
                    "least 5000 input tokens, for a total of at least 104000 tokens."
                )
            return {"content": "duzeldi", "tool_calls": []}, Usage(), "stop"

        monkeypatch.setattr(client, "_stream", _stream)
        assert self._cagir(client).text == "duzeldi"
        assert cagri["n"] == 2


class TestHistoryStaysValid:
    """Bozuk bir arac cagrisi konusmayi zehirlememeli.

    `arguments` alani JSON *metnidir*. Model onu bozuk uretebiliyor --
    ozellikle yanit uretim tavaninda kesildiginde JSON yarida kalir.
    `_parse` bunu araci calistirirken yutuyordu ama ham hali gecmise aynen
    geri konuyordu; bir sonraki istekte uc onu cozmeye calisip 400
    donduruyordu ve o kosu bir daha toparlanamiyordu.

    Olculdu: bir boru hatti kosusunda `plan` fazi "Expecting value: line 1
    column 11 (char 10)" ile dustu -- ucun json cozucusu bizim
    gonderdigimiz on birinci karakterde tikanmisti.
    """

    @pytest.fixture()
    def client(self, settings):
        settings.provider = "openai"
        settings.openai_base_url = "http://127.0.0.1:8008/v1"
        return OpenAICompatibleClient(settings)

    @staticmethod
    def _cagri(arguments):
        return {
            "id": "c1",
            "type": "function",
            "function": {"name": "record_tasks", "arguments": arguments},
        }

    @pytest.mark.parametrize(
        "bozuk",
        [
            '{"tasks": [',                    # tavanda kesilmis
            '{"tasks": [{"key": "T-1"',       # daha derinden kesilmis
            "",                                # hic uretilmemis
            "not json at all",
            '{"a": 1,}',                      # fazladan virgul
        ],
    )
    def test_broken_arguments_never_reach_the_history(self, client, bozuk):
        mesajlar = []
        client.append_assistant(
            mesajlar,
            make_result({"content": None, "tool_calls": [self._cagri(bozuk)]}),
        )
        args = mesajlar[0]["tool_calls"][0]["function"]["arguments"]
        # Gecmisteki her sey ucun cozebilecegi bicimde olmali.
        json.loads(args)

    def test_valid_arguments_are_left_alone(self, client):
        """Saglam bir cagri aynen korunmali; icerigi degistirmiyoruz."""
        mesajlar = []
        saglam = '{"tasks": [{"key": "T-001", "title": "ilk"}]}'
        client.append_assistant(
            mesajlar,
            make_result({"content": None, "tool_calls": [self._cagri(saglam)]}),
        )
        args = mesajlar[0]["tool_calls"][0]["function"]["arguments"]
        assert json.loads(args) == json.loads(saglam)

    def test_the_repaired_call_matches_what_was_executed(self, client):
        """Gecmis, gercekte ne calistirildigiyla tutarli kalmali.

        `_parse` bozuk argumani `{}` sayip araci oyle calistiriyor; gecmise
        de ayni sey yazilmali, yoksa model kendi yaptigini yanlis hatirlar.
        """
        sonuc = client._parse(
            {"content": None, "tool_calls": [self._cagri('{"tasks": [')]},
            Usage(), "tool_calls", "test-model",
        )
        assert sonuc.tool_calls[0].arguments == {}

        mesajlar = []
        client.append_assistant(mesajlar, sonuc)
        args = mesajlar[0]["tool_calls"][0]["function"]["arguments"]
        assert json.loads(args) == {}

    def test_non_string_arguments_are_serialised(self, client):
        """Bazi ucler sozluk gonderiyor; gecmis yine metin tasimali."""
        mesajlar = []
        client.append_assistant(
            mesajlar,
            make_result({"content": None, "tool_calls": [self._cagri({"a": 1})]}),
        )
        args = mesajlar[0]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args) == {"a": 1}


class TestClientContract:
    """Her istemci, sozlesmenin TAMAMINI uygulamali.

    `LLMClient` bir `Protocol`; govdeleri calisma zamaninda miras
    ALINMAZ. Sozlesmeye `append_note` eklenip yalnizca Anthropic'e
    yazildiginda OpenAI istemcisi eksik kaldi ve alti saatlik bir
    dogrulama kosusu `AttributeError: 'OpenAICompatibleClient' object has
    no attribute 'append_note'` ile ucuncu fazda coktu.

    Testler de kacirmisti: sahte istemciye metodu elle eklemistim, yani
    gercek istemcinin eksikligi hic sinanmadi. Bu test o boslugu kapatir --
    sozlesmeye eklenen her yeni metot burada kendini gosterir.
    """

    CLIENTS = [AnthropicClient, OpenAICompatibleClient]

    @staticmethod
    def _contract() -> set[str]:
        from deerx.llm.base import LLMClient

        return {
            ad for ad in dir(LLMClient)
            if not ad.startswith("_") and callable(getattr(LLMClient, ad, None))
        }

    @pytest.mark.parametrize("client", CLIENTS, ids=lambda c: c.__name__)
    def test_every_contract_method_is_implemented(self, client):
        eksik = sorted(self._contract() - set(dir(client)))
        assert not eksik, f"{client.__name__} eksik: {eksik}"

    @pytest.mark.parametrize("client", CLIENTS, ids=lambda c: c.__name__)
    def test_no_method_is_only_inherited_from_the_protocol(self, client):
        """Protocol govdeleri calismaz; her metot sinifin kendisinde olmali."""
        from deerx.llm.base import LLMClient

        for ad in self._contract():
            assert ad in client.__dict__, f"{client.__name__}.{ad} kendi tanimi degil"
            assert getattr(client, ad) is not getattr(LLMClient, ad, None)

    def test_the_fake_client_matches_the_contract_too(self):
        """Sahte istemci gercekten geride kalirsa testler yaniltir."""
        from tests.test_agent import FakeClient

        eksik = sorted(self._contract() - set(dir(FakeClient)))
        assert not eksik, f"FakeClient eksik: {eksik}"

    @pytest.mark.parametrize("client", CLIENTS, ids=lambda c: c.__name__)
    def test_the_note_reaches_the_model(self, client):
        mesajlar: list = [{"role": "user", "content": "gorev"}]
        client.append_note(None, mesajlar, "NOT")  # type: ignore[arg-type]
        assert "NOT" in str(mesajlar[-1]["content"])

    def test_anthropic_never_leaves_two_user_messages_in_a_row(self):
        """Anthropic'te roller donusumlu olmali; ikinci `user` istegi gecersiz kilar."""
        mesajlar: list = [
            {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}
        ]
        AnthropicClient.append_note(None, mesajlar, "NOT")  # type: ignore[arg-type]
        assert len(mesajlar) == 1, "yeni bir user mesaji eklenmis"
        assert any("NOT" in str(b) for b in mesajlar[0]["content"])

    def test_anthropic_merges_into_plain_text_content_too(self):
        mesajlar: list = [{"role": "user", "content": "gorev"}]
        AnthropicClient.append_note(None, mesajlar, "NOT")  # type: ignore[arg-type]
        assert len(mesajlar) == 1
        assert "NOT" in mesajlar[0]["content"]

    def test_openai_adds_a_separate_message_after_tool_results(self):
        """OpenAI'de arac sonuclari `tool` rolunde; ayri bir `user` mesaji gecerli."""
        mesajlar: list = [{"role": "tool", "tool_call_id": "a", "content": "x"}]
        OpenAICompatibleClient.append_note(None, mesajlar, "NOT")  # type: ignore[arg-type]
        assert mesajlar[-1]["role"] == "user"
        assert mesajlar[-1]["content"] == "NOT"


class TestAuthHint:
    """401/403'te caresi soylenmeli.

    OLCULDU: ana calisma alaninda ayarlar ekrani modeli "hazir" gosteriyordu
    ama her cagri `Error code: 401 - {'error': 'Unauthorized'}` ile dusuyordu.
    Sebep `Settings.llm_ready`: yerel bir OpenAI-uyumlu uc icin taban adresi
    yeterli sayar, "cogu yerel sunucu anahtar istemez" varsayimiyla. Bu uc
    istiyordu. Karari harness vermisti; karar yanlis cikinca ciplak kodu
    gosterip susuyor ve kullaniciyi kendi yapilandirmasinda hata aramaya
    gonderiyordu.
    """

    @staticmethod
    def _ayar(anahtar):
        from deerx.config import Settings

        return Settings(openai_base_url="http://127.0.0.1:9/v1", openai_api_key=anahtar)

    def test_401_without_a_key_names_the_remedy(self):
        from deerx.llm.openai_client import _yetki_notu

        not_ = _yetki_notu(Exception("Error code: 401 - {'error': 'Unauthorized'}"),
                           self._ayar(None))
        assert "OPENAI_API_KEY" in not_, not_

    def test_401_with_a_key_says_the_key_was_rejected(self):
        """Anahtar varken 'anahtar ekleyin' demek yanlis yere gonderirdi."""
        from deerx.llm.openai_client import _yetki_notu

        not_ = _yetki_notu(Exception("Error code: 403"), self._ayar("bir-anahtar"))
        assert "OPENAI_API_KEY" not in not_
        assert not_.strip(), "403'te de bir sey soylenmeli"

    def test_other_errors_get_no_invented_advice(self):
        """Uydurma tavsiye, hicbir tavsiyeden kotudur."""
        from deerx.llm.openai_client import _yetki_notu

        for metin in ("Error code: 500", "connection reset", "model not found"):
            assert _yetki_notu(Exception(metin), self._ayar(None)) == ""

    def test_the_hint_reaches_the_raised_error(self):
        """Yardimciyi tek basina test etmek yeterli degil: cagri yerine
        bagli olmasaydi da gecerdi."""
        import inspect

        from deerx.llm import openai_client

        kaynak = inspect.getsource(openai_client)
        assert "_yetki_notu(exc, self.settings)" in kaynak, (
            "not, LLMError metnine eklenmeli"
        )
