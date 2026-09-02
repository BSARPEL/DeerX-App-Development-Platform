"""OpenAI-uyumlu istemci — vLLM, Ollama, LM Studio, llama.cpp, OpenAI.

Yerel bir vLLM sunucusu icin yazildi ve ona karsi dogrulandi. Anthropic
istemcisiyle ayni `LLMClient` sozlesmesini uygular; ajan dongusu ikisini
ayirt etmez.

Bicim farklari burada kapatilir:
  * arac tanimi   : {name, description, input_schema} -> {type:"function", function:{...}}
  * arac cagrisi  : tool_calls[].function.arguments JSON *metnidir*, ayristirilir
  * arac sonucu   : ayri bir {"role":"tool", tool_call_id} mesaji
  * dusunme       : vLLM surumune gore `reasoning` veya `reasoning_content`
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from ..config import Settings
from ..errors import BudgetExceeded, ConfigError, LLMError
from ..i18n import t
from ..logging import EventLog, get_logger
from .base import (
    HISTORY_SOFT_LIMIT,
    IMAGE_TOKEN_ESTIMATE,
    KEEP_RECENT_IMAGES,
    KEEP_RECENT_MESSAGES,
    MAX_IMAGE_BYTES,
    TRIM_PLACEHOLDER,
    LLMResult,
    ToolCall,
    ToolOutcome,
    UsageLedger,
    read_image,
)
from .pricing import Usage, cost_usd

log = get_logger("llm.openai")

# vLLM'in reasoning parser'i surumune gore bu alanlardan birini doldurur.
_REASONING_FIELDS = ("reasoning", "reasoning_content")

# Girdi tahmini kasten karamsar. Ucun tokenizer'ini bilmiyoruz; duz metinde
# oran ~3.6 karakter/token, bol noktalama iceren kod ve JSON'da 2.5'e kadar
# duser. Az tahmin etmek istegi 400 ile geri cevirtir; cok tahmin etmek
# yalnizca uretim tavanini biraz kisar, ki o tavan zaten dolmaz.
_CHARS_PER_TOKEN = 2.5
# Sohbet bicimlendirmesi: rol basliklari, ayraclar, arac sema serilestirmesi.
_FORMAT_OVERHEAD_TOKENS = 512
# Bunun altina inen bir uretim tavani anlamsiz; istek zaten sigmiyordur.
_MIN_OUTPUT_TOKENS = 1024

# Ucun baglam sikayeti. Kendi tahminimiz tutmazsa ucun kendi sayilarini
# kullanabilmek icin ayristirilir.
_CONTEXT_ERROR_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?"
    r"contains at least (\d+) input tokens",
    re.S,
)


def to_openai_tools(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic bicimli arac tanimlarini OpenAI islev bicimine cevirir.

    Anthropic'e ozgu sunucu araclari (`{"type": "web_search_..."}` gibi) atlanir:
    onlar Anthropic altyapisinda calisir, yerel bir modelde karsiligi yoktur.
    """
    converted: list[dict[str, Any]] = []
    for spec in specs:
        if "input_schema" not in spec:
            continue  # sunucu araci; bu saglayicida yok
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "parameters": spec["input_schema"],
                },
            }
        )
    return converted

# Akis ortasinda kopan baglanti kac kez tekrar denenir. SDK'nin kendi
# `max_retries` degeri yalnizca istek BASLAMADAN onceki hatalari kapsar.
_STREAM_RETRIES = 2
_RETRY_BACKOFF = 1.5

# Gecici tasima hatalarinin izleri. Tip yerine metne bakiliyor: httpx,
# openai ve altindaki h11 ayni durumu farkli siniflarla bildiriyor ve
# surumden surume degisiyor.
_TRANSIENT_MARKS = (
    "incomplete chunked read",
    "peer closed connection",
    "server disconnected",
    "connection reset",
    "connection aborted",
    "remote protocol error",
    "read timed out",
    "timed out",
    "broken pipe",
)


def _is_transient(exc: Exception) -> bool:
    """Hata, ayni istegi tekrar denemekle gecebilecek turden mi?

    Model reddi, kimlik hatasi ve gecersiz istek tekrar denemekle gecmez;
    onlari tekrarlamak yalnizca zaman ve para harcar.
    """
    metin = f"{type(exc).__name__} {exc}".lower()
    return any(iz in metin for iz in _TRANSIENT_MARKS)


def _kisa(exc: Exception, limit: int = 90) -> str:
    metin = str(exc).replace("\n", " ")
    return metin[:limit] + ("..." if len(metin) > limit else "")


def _valid_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Arac cagrilarini gecmise koymadan once gecerli JSON'a cevirir.

    `arguments` alani JSON *metnidir* ve model onu bozuk uretebiliyor --
    ozellikle yanit uretim tavaninda kesildiginde JSON yarida kalir.
    `_parse` bunu araci calistirirken yutuyordu ama HAM hali gecmise
    aynen geri konuyordu; bir sonraki istekte uc onu cozmeye calisip
    400 donduruyor ve konusma KALICI olarak zehirleniyordu.

    Olculdu: bir boru hatti kosusunda `plan` fazi
    "Expecting value: line 1 column 11 (char 10)" ile dustu -- yani ucun
    json cozucusu, bizim gonderdigimiz on birinci karakterde tikandi.
    Kesilen `record_tasks` cagrisinin yarim JSON'uydu.

    Bozuk argumanin yerine `{}` yaziliyor: aracin gercekte oyle
    calistirildigi zaten `_parse` tarafindan kararlastirilmisti, yani
    gecmis yapilanla tutarli kaliyor.
    """
    safe: list[dict[str, Any]] = []
    for call in calls:
        fn = dict(call.get("function") or {})
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = "{}" if args is None else json.dumps(args, ensure_ascii=False)
        try:
            json.loads(args or "{}")
        except (ValueError, TypeError):
            log.warning("Bozuk arac argumani gecmise konmadan duzeltildi: %s", args[:120])
            args = "{}"
        fn["arguments"] = args or "{}"
        safe.append({**call, "function": fn})
    return safe


# Sinir ve tur listesi `llm.base` icinde; iki istemci de ayni kurallara
# uymali. Ad geriye donuk birakiliyor: testler bunu ice aktariyor.
MAX_GORSEL_BAYT = MAX_IMAGE_BYTES


def _gorsel_blogu(yol: Any) -> dict[str, Any] | None:
    """Dosyayi OpenAI biciminde bir `image_url` bloguna cevirir.

    Okuma ve sinirlar `llm.base.read_image` icinde: iki istemci de ayni
    dosyalari ayni kurallarla okumali, yalnizca bicimlendirme farkli.
    """
    okunan = read_image(yol)
    if okunan is None:
        return None
    tur, veri = okunan
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{tur};base64,{veri}"},
    }


def _gorsel_reddi_mi(exc: Exception) -> bool:
    """Hata "bu model goruntu kabul etmiyor" demek mi?

    Uclar bunu ayni sekilde soylemiyor; metinde gecen isaretlere bakilir.
    Yanlis pozitif ucuz (bir kosuda goruntu gonderilmez), yanlis negatif
    pahali (kosu duser).
    """
    metin = str(exc).lower()
    isaretler = ("image", "vision", "multimodal", "image_url", "not supported")
    return any(i in metin for i in isaretler)


def _gorselsiz_kopya(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Goruntu bloklarini dusuren bir KOPYA ve dusurulen goruntu sayisi.

    Kopya sart: bu yalnizca TAHMIN icin kullaniliyor, gonderilecek
    mesajlar dokunulmadan kalmali.
    """
    kopya: list[dict[str, Any]] = []
    sayi = 0
    for mesaj in messages:
        icerik = mesaj.get("content")
        if not isinstance(icerik, list):
            kopya.append(mesaj)
            continue
        kalan = []
        for blok in icerik:
            if isinstance(blok, dict) and blok.get("type") == "image_url":
                sayi += 1
                continue
            kalan.append(blok)
        kopya.append({**mesaj, "content": kalan})
    return kopya, sayi


def _eski_gorselleri_dusur(messages: list[dict[str, Any]]) -> int:
    """En yeni birkac goruntu disindakileri gecmisten dusurur.

    Ekran goruntusu ajanin KENDI isini gormesi icin var ve ise yarayan
    hep sonuncusudur: on tur once alinan goruntu, o zamandan beri
    degistirdigi bir arayuzu gosterir. Eskiler dusmezse ayni megabaytlar
    her turda yeniden gonderilir -- tek bir QA fazi on ekran goruntusu
    alabiliyor ve hepsi kosunun sonuna kadar gecmiste kaliyordu.

    Yapiyi bozmaz: goruntu blogu cikar, mesajin metni yerinde kalir.
    """
    gorulen = 0
    dusen = 0
    for mesaj in reversed(messages):
        icerik = mesaj.get("content")
        if not isinstance(icerik, list):
            continue
        kalan: list[Any] = []
        mesajdan_dusen = 0
        for blok in icerik:
            if not (isinstance(blok, dict) and blok.get("type") == "image_url"):
                kalan.append(blok)
                continue
            gorulen += 1
            if gorulen <= KEEP_RECENT_IMAGES:
                kalan.append(blok)
            else:
                mesajdan_dusen += 1
        if mesajdan_dusen:
            dusen += mesajdan_dusen
            mesaj["content"] = kalan or [
                {"type": "text", "text": t("llm.screenshot_dropped")}
            ]
    return dusen


def _gorselleri_cikar(messages: list[dict[str, Any]]) -> None:
    """Gecmisteki goruntu bloklarini metne indirger.

    Yalnizca son istegi temizlemek yetmez: gecmiste kalan bir goruntu
    sonraki her turda ayni reddi uretir.
    """
    for m in messages:
        icerik = m.get("content")
        if not isinstance(icerik, list):
            continue
        kalan = [b for b in icerik if b.get("type") != "image_url"]
        m["content"] = kalan or [{"type": "text", "text": t("llm.screenshot_dropped")}]


def _yetki_notu(exc: Exception, settings: Any) -> str:
    """401/403 icin caresi olan bir not; digerlerinde bos dize.

    OLCULDU: ana calisma alaninda her cagri
    `Error code: 401 - {'error': 'Unauthorized'}` ile dusuyordu ve ayarlar
    ekrani modeli "hazir" gosteriyordu. `Settings.llm_ready` yerel bir
    OpenAI-uyumlu uc icin taban adresi yeterli sayar -- "cogu yerel sunucu
    anahtar istemez". Bu uc istiyordu.

    Yani karari harness vermisti; karar yanlis cikinca caresini de o biliyor.
    Ciplak 401 kullaniciyi kendi yapilandirmasinda hata aramaya gonderiyordu.
    """
    metin = str(exc)
    if "401" not in metin and "403" not in metin:
        return ""
    anahtar = getattr(settings, "openai_api_key", None)
    return t("llm.auth_key_rejected" if anahtar else "llm.auth_needs_key")


class OpenAICompatibleClient:
    """Chat Completions API'si konusan her sunucu icin istemci."""

    def __init__(self, settings: Settings, events: EventLog | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(t("setup.no_openai_package")) from exc

        base_url = settings.openai_base_url
        if not base_url:
            raise ConfigError(t("setup.no_base_url"))

        self.settings = settings
        self.events = events
        self.ledger = UsageLedger()
        # model -> baglam penceresi. Bir kez sorulur; None "uc soylemedi"
        # demektir ve tekrar sorulmaz.
        self._windows: dict[str, int | None] = {}
        # Kirpma notu kosu basina bir kez basilir; her turda tekrarlanirsa
        # olay akisini doldurur.
        self._clamp_noted = False
        # Uc goruntu kabul ediyor mu? Her model gormez. Bir kez reddederse
        # bu bayrak duser, kosu boyunca goruntu gonderilmez ve istek
        # yeniden denenir -- ajan hicbir sey fark etmez, yalnizca
        # "goremiyor" durumuna doner.
        self.gorsel_gonder = True
        self._client = OpenAI(
            base_url=base_url,
            # Yerel sunucular cogu zaman anahtar istemez; SDK bos anahtari reddettigi
            # icin yer tutucu gonderilir.
            api_key=settings.openai_api_key or "not-needed",
            timeout=float(settings.request_timeout_seconds),
            max_retries=3,
        )

    @property
    def total_cost(self) -> float:
        return self.ledger.total_cost

    # ------------------------------------------------------------------ #
    # Cagri
    # ------------------------------------------------------------------ #
    def complete(
        self,
        *,
        role: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        thinking: bool = True,
        on_text: Callable[[str], None] | None = None,
    ) -> LLMResult:
        model = model or self.settings.model_for(role)
        max_tokens = max_tokens or self.settings.max_tokens

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        converted = to_openai_tools(list(tools or []))
        if converted:
            payload["tools"] = converted
            payload["tool_choice"] = "auto"
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature
        # Tavan en son konulur: kirpma girdinin tamamini gormeli.
        payload["max_tokens"] = self._fit_output(payload, max_tokens, model)

        # Iki ayri tekrar sebebi var ve ikisi de bir kosuyu kurtarir:
        #   * baglam tasmasi  -> uc kendi sayilarini soyler, tavan duzeltilir
        #   * kopan akis      -> gecici tasima hatasi, aynen tekrar denenir
        # Ikincisi olculdu: tam bir boru hatti kosusunda `design` fazi
        # "peer closed connection without sending complete message body"
        # ile dustu ve 46 dakikalik kosu altinci fazda durdu. SDK'nin kendi
        # `max_retries` ayari akis BASLAMADAN onceki hatalari kapsar; akisin
        # ortasinda kopan baglanti ona gorunmez.
        duzeltildi = False
        deneme = 0
        while True:
            try:
                message, usage, finish_reason = self._stream(payload, on_text)
                break
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - SDK cesitli hata tipleri firlatir
                corrected = None if duzeltildi else self._corrected_output(exc)
                if corrected is not None:
                    duzeltildi = True
                    self._note(
                        f"uc {corrected:,} token'lik uretime izin veriyor; istek "
                        "kendi sinirlariyla tekrarlaniyor"
                    )
                    payload["max_tokens"] = corrected
                    continue
                if deneme < _STREAM_RETRIES and _is_transient(exc):
                    deneme += 1
                    self._note(
                        f"akis yarida koptu ({_kisa(exc)}); {deneme}. tekrar deneme"
                    )
                    time.sleep(_RETRY_BACKOFF * deneme)
                    continue
                if self.gorsel_gonder and _gorsel_reddi_mi(exc):
                    # Uc goruntuyu kabul etmiyor. Ajanin hatasi degil ve
                    # ajanin duzeltebilecegi bir sey de degil: sessizce
                    # metne donup devam ederiz.
                    self.gorsel_gonder = False
                    self._note(t("llm.vision_unsupported"))
                    _gorselleri_cikar(messages)
                    payload["messages"] = messages
                    continue
                raise LLMError(
                    f"{model} cagrisi basarisiz: {exc}{_yetki_notu(exc, self.settings)}"
                ) from exc

        result = self._parse(message, usage, finish_reason, model)
        self.ledger.add(model, result.usage, result.cost)

        if self.events is not None:
            self.events.emit(
                "cost",
                role,
                f"{model} · {result.usage.output_tokens} cikti tk"
                + (f" · ${result.cost:.4f}" if result.cost else " · yerel"),
                model=model,
                cost=result.cost,
                stop_reason=result.stop_reason,
            )
        self._check_budget()
        return result


    # ------------------------------------------------------------------ #
    # Baglam penceresi
    # ------------------------------------------------------------------ #
    def _context_window(self, model: str) -> int | None:
        """Ucun toplam baglam penceresi; bilinmiyorsa None.

        Ayarlarda verilmisse o gecerlidir. Verilmemisse uca sorulur: vLLM
        `/v1/models` yanitinda `max_model_len` dondurur. Sonuc -- None dahil --
        onbelleklenir; her turda model listesi cekilmez.
        """
        if self.settings.context_window:
            return self.settings.context_window
        if model in self._windows:
            return self._windows[model]

        window: int | None = None
        try:
            for entry in self._client.models.list().data:
                length = getattr(entry, "max_model_len", None)
                if entry.id == model and isinstance(length, int) and length > 0:
                    window = length
                    break
        except Exception as exc:  # noqa: BLE001 - uc listeleme sunmayabilir
            log.debug("baglam penceresi ogrenilemedi: %s", exc)
        self._windows[model] = window
        return window

    @staticmethod
    def _estimate_input(payload: dict[str, Any]) -> int:
        """Istegin girdi tarafi icin karamsar token tahmini.

        Goruntuler METIN OLARAK SAYILMAZ. Saglayici bir goruntuyu base64
        uzunluguna gore degil alanina gore fiyatlar; base64'u metin
        saymak yuzlerce kat sismis bir tahmin uretiyordu.

        OLCULDU: 1 MB'lik tek bir ekran goruntusu 559.816 token tahmin
        ettiriyordu, gercek maliyeti ~1.600. 262K pencereli bir uctan
        `room` negatife dusuyor ve `_fit_output` kosuyu
        `context_overflow` ile olduruyordu -- yani ajanin ILK ekran
        goruntusu kosuyu bitiriyor, ustelik hata baglamin gercekten
        dolduugunu soyluyordu. Tam da "yaptigini gorebilme" ozelliginin
        en cok ise yaradigi anda.
        """
        gorselsiz, gorsel_sayisi = _gorselsiz_kopya(payload.get("messages", []))
        text = json.dumps(
            [gorselsiz, payload.get("tools", [])],
            ensure_ascii=False,
        )
        return (
            int(len(text) / _CHARS_PER_TOKEN)
            + gorsel_sayisi * IMAGE_TOKEN_ESTIMATE
            + _FORMAT_OVERHEAD_TOKENS
        )

    def _fit_output(self, payload: dict[str, Any], ceiling: int, model: str) -> int:
        """Uretim tavanini pencereye sigdirir.

        `max_tokens` bir tavandir, ayrilmis yer degil -- ama uc ikisini toplayip
        pencereyle karsilastirir. Girdi buyudukce tavanin kucultulmesi gerekir;
        yoksa uzun bir kosunun ortasinda istek 400 ile geri doner.
        """
        window = self._context_window(model)
        if not window:
            return ceiling

        room = window - self._estimate_input(payload)
        if room < _MIN_OUTPUT_TOKENS:
            raise LLMError(
                t(
                    "llm.context_overflow",
                    model=model,
                    window=f"{window:,}",
                    input=f"{window - room:,}",
                )
            )
        if ceiling <= room:
            return ceiling

        self._note(
            f"uretim tavani {ceiling:,} -> {room:,} token'a cekildi "
            f"(pencere {window:,}, girdi buyudu)"
        )
        return room

    def _corrected_output(self, exc: Exception) -> int | None:
        """Ucun baglam sikayetinden gecerli bir uretim tavani cikarir.

        Yalnizca uc hem pencereyi hem girdiyi sayiyla soyluyorsa bir sey
        dondurur; baska her hata oldugu gibi yukari gider.
        """
        match = _CONTEXT_ERROR_RE.search(str(exc))
        if match is None:
            return None
        window, used = int(match.group(1)), int(match.group(2))
        room = window - used
        return room if room >= _MIN_OUTPUT_TOKENS else None

    def _note(self, message: str) -> None:
        log.info(message)
        if self._clamp_noted:
            return
        self._clamp_noted = True
        if self.events is not None:
            self.events.emit("warn", "model", message)

    def _stream(
        self, payload: dict[str, Any], on_text: Callable[[str], None] | None
    ) -> tuple[dict[str, Any], Usage, str | None]:
        """Akisli istek; metin ve arac cagrisi parcalarini birlestirir.

        Akis, uzun ciktilarda HTTP zaman asimini onler — yerel modellerde tek bir
        yanit dakikalar surebilir.
        """
        content: list[str] = []
        reasoning: list[str] = []
        # index -> {id, name, arguments}: OpenAI arac cagrilarini parca parca yollar.
        partial_calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage = Usage(calls=1)

        stream = self._client.chat.completions.create(
            **payload, stream=True, stream_options={"include_usage": True}
        )
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = Usage(
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                    calls=1,
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                content.append(delta.content)
                if on_text is not None:
                    on_text(delta.content)
            for field in _REASONING_FIELDS:
                piece = getattr(delta, field, None)
                if piece:
                    reasoning.append(piece)
                    break
            for call in delta.tool_calls or []:
                slot = partial_calls.setdefault(
                    call.index, {"id": "", "name": "", "arguments": ""}
                )
                if call.id:
                    slot["id"] = call.id
                if call.function and call.function.name:
                    slot["name"] = call.function.name
                if call.function and call.function.arguments:
                    slot["arguments"] += call.function.arguments

        message = {
            "role": "assistant",
            "content": "".join(content) or None,
            "reasoning": "".join(reasoning),
            "tool_calls": [
                {
                    "id": slot["id"] or f"call_{index}",
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for index, slot in sorted(partial_calls.items())
            ],
        }
        return message, usage, finish_reason

    @staticmethod
    def _parse(
        message: dict[str, Any], usage: Usage, finish_reason: str | None, model: str
    ) -> LLMResult:
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw["function"]
            saglam = True
            try:
                arguments = json.loads(fn["arguments"] or "{}")
            except json.JSONDecodeError:
                # Model bozuk JSON uretebilir; dongu kirilmasin, arac hatayi bildirsin.
                log.warning("Arac argumanlari ayristirilamadi: %s", fn["arguments"][:200])
                arguments = {}
                saglam = False
            if not isinstance(arguments, dict):
                arguments = {}
                saglam = False
            calls.append(ToolCall(id=raw["id"], name=fn["name"], arguments=arguments,
                                  arguments_ok=saglam))

        # OpenAI'nin finish_reason'unu ajan dongusunun bekledigi sozluge cevir.
        stop_reason = {
            "tool_calls": "tool_use",
            "stop": "end_turn",
            "length": "max_tokens",
            "content_filter": "refusal",
        }.get(finish_reason or "", finish_reason or "end_turn")

        return LLMResult(
            text=(message.get("content") or "").strip(),
            thinking=(message.get("reasoning") or "").strip(),
            tool_calls=calls,
            stop_reason=stop_reason,
            usage=usage,
            cost=cost_usd(model, usage),
            model=model,
            raw=message,
        )

    # ------------------------------------------------------------------ #
    # Gecmis yonetimi
    # ------------------------------------------------------------------ #
    def append_assistant(self, messages: list[dict[str, Any]], result: LLMResult) -> None:
        raw = result.raw or {}
        entry: dict[str, Any] = {"role": "assistant", "content": raw.get("content")}
        if raw.get("tool_calls"):
            entry["tool_calls"] = _valid_tool_calls(raw["tool_calls"])
        elif entry["content"] is None:
            # Ne metin ne arac cagrisi: bos icerik gonderen sunucular hata verir.
            entry["content"] = ""
        messages.append(entry)

    def append_note(self, messages: list[dict[str, Any]], text: str) -> None:
        """Gecmise bir kullanici notu ekler.

        OpenAI biciminde arac sonuclari `tool` rolundedir, dolayisiyla
        arkasina ayri bir `user` mesaji koymak gecerlidir. (Anthropic'te
        oyle degil; orada not son kullanici mesajina katilir.)
        """
        messages.append({"role": "user", "content": text})

    def append_tool_results(
        self, messages: list[dict[str, Any]], outcomes: list[ToolOutcome]
    ) -> None:
        # OpenAI biciminde her arac sonucu AYRI bir mesajdir; Anthropic'te hepsi
        # tek bir kullanici mesajinda toplanir.
        for outcome in outcomes:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": outcome.call_id,
                    "content": outcome.content or "(bos)",
                }
            )

        # Gorseller AYRI bir `user` mesajinda gider: OpenAI biciminde
        # `role: "tool"` mesajlari gorsel tasiyamaz. Uc goruntuyu bir kez
        # reddettiyse hic gondermeyiz.
        # `getattr`: bu metot yalnizca mesaj bicimlendirir ve kurulmus bir
        # istemci olmadan da cagrilabilir (testler boyle yapiyor).
        if getattr(self, "gorsel_gonder", True):
            bloklar = []
            for outcome in outcomes:
                for yol in outcome.images or []:
                    blok = _gorsel_blogu(yol)
                    if blok is not None:
                        bloklar.append(blok)
            if bloklar:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": t("llm.screenshot_attached")},
                        *bloklar,
                    ],
                })

    @staticmethod
    def trim_history(messages: list[dict[str, Any]]) -> int:
        # Goruntuler yumusak sinirdan BAGIMSIZ dusurulur. Sinir karakter
        # sayar ve tek bir base64 goruntu onu tek basina asar; o zaman
        # kirpma, asil sebep goruntuyken metni budamaya baslardi.
        trimmed = _eski_gorselleri_dusur(messages)

        total = sum(len(str(m.get("content", "") or "")) for m in messages)
        if total <= HISTORY_SOFT_LIMIT or len(messages) <= KEEP_RECENT_MESSAGES:
            return trimmed

        cutoff = len(messages) - KEEP_RECENT_MESSAGES
        for message in messages[1:cutoff]:
            if message.get("role") != "tool":
                continue
            body = message.get("content") or ""
            if isinstance(body, str) and len(body) > 400:
                message["content"] = TRIM_PLACEHOLDER.format(size=len(body))
                trimmed += 1
        return trimmed

    # ------------------------------------------------------------------ #
    # Muhasebe
    # ------------------------------------------------------------------ #
    def _check_budget(self) -> None:
        limit = self.settings.cost_limit_usd
        if limit and self.ledger.total_cost > limit:
            raise BudgetExceeded(
                f"Maliyet tavani asildi: ${self.ledger.total_cost:.2f} > ${limit:.2f}."
            )

    def usage_summary(self) -> str:
        return self.ledger.summary(cost_usd)
