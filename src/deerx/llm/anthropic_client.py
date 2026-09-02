"""Claude API sarmalayicisi.

Ajan dongusunun ihtiyac duydugu tek sey burada: arac tanimlariyla birlikte bir
mesaj gonder, geri gelen `tool_use` bloklarini ve metni ayristirilmis halde al.
Dongunun kendisi `deerx.agents.base` icinde; bu katman durum tutmaz (yalnizca
toplam kullanim/maliyet sayaci disinda).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

import anthropic

from ..config import Settings
from ..errors import BudgetExceeded, LLMError
from ..i18n import t
from ..logging import EventLog, get_logger
from .base import (
    HISTORY_SOFT_LIMIT,
    KEEP_RECENT_IMAGES,
    KEEP_RECENT_MESSAGES,
    TRIM_PLACEHOLDER,
    LLMResult,
    ToolCall,
    ToolOutcome,
    UsageLedger,
    read_image,
)
from .pricing import Usage, cost_usd

log = get_logger("llm")


def _gorsel_blogu(yol: Any) -> dict[str, Any] | None:
    """Dosyayi Anthropic bicimindeki bir `image` bloguna cevirir.

    Okuma ve sinirlar `llm.base.read_image` icinde: iki istemci de ayni
    dosyalari ayni kurallarla okur, yalnizca bicimlendirme farklidir.
    """
    okunan = read_image(yol)
    if okunan is None:
        return None
    tur, veri = okunan
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": tur, "data": veri},
    }


def _eski_gorselleri_dusur(messages: list[dict[str, Any]]) -> int:
    """En yeni birkac goruntu disindakileri gecmisten dusurur.

    Ise yarayan hep son goruntudur: on tur once alinan ekran goruntusu, o
    zamandan beri degistirilmis bir arayuzu gosterir. Eskiler dusmezse
    ayni megabaytlar her turda yeniden gonderilir.

    Yapiyi bozmaz: `tool_result` blogu ve icindeki metin yerinde kalir,
    yalnizca `image` bloklari cikar -- yani `tool_use` / `tool_result`
    eslesmesine dokunulmaz.
    """
    gorulen = 0
    dusen = 0
    for mesaj in reversed(messages):
        icerik = mesaj.get("content")
        if not isinstance(icerik, list):
            continue
        for blok in icerik:
            if not isinstance(blok, dict):
                continue
            govde = blok.get("content")
            if blok.get("type") != "tool_result" or not isinstance(govde, list):
                continue
            kalan: list[Any] = []
            blokdan_dusen = 0
            for ic_blok in govde:
                if not (isinstance(ic_blok, dict) and ic_blok.get("type") == "image"):
                    kalan.append(ic_blok)
                    continue
                gorulen += 1
                if gorulen <= KEEP_RECENT_IMAGES:
                    kalan.append(ic_blok)
                else:
                    blokdan_dusen += 1
            if blokdan_dusen:
                dusen += blokdan_dusen
                blok["content"] = kalan or [
                    {"type": "text", "text": t("llm.screenshot_dropped")}
                ]
    return dusen

# Adaptif dusunme ve `output_config.effort` destekleyen model aileleri.
# Bu listede olmayan modellere (or. Haiku 4.5) bu parametreler gonderilmez.
_ADAPTIVE_FAMILIES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
)

# Sunucu tarafinda calisan araclar: istemci bunlari calistirmaz, sadece bildirir.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 12,
}
WEB_FETCH_TOOL: dict[str, Any] = {
    "type": "web_fetch_20260209",
    "name": "web_fetch",
    "max_uses": 12,
    "max_content_tokens": 40_000,
    "citations": {"enabled": True},
}
SERVER_TOOL_NAMES = {"web_search", "web_fetch", "code_execution"}


def supports_adaptive_thinking(model: str) -> bool:
    return any(model.startswith(fam) for fam in _ADAPTIVE_FAMILIES)


class AnthropicClient:
    """Ince, yeniden denemeli Claude istemcisi.

    - Uzun ciktilarda HTTP zaman asimini onlemek icin daima akis (streaming) kullanir.
    - Sistem promptunu prompt onbellegine alir (arac listesi + sistem prefix'i).
    - SDK surumu bir parametreyi tanimazsa onu `extra_body` icine tasiyip tekrar dener.
    """

    def __init__(self, settings: Settings, events: EventLog | None = None) -> None:
        self.settings = settings
        self.events = events
        self._client = anthropic.Anthropic(
            api_key=settings.require_api_key(),
            max_retries=4,
            timeout=600.0,
        )
        self.ledger = UsageLedger()

    @property
    def total_cost(self) -> float:
        return self.ledger.total_cost

    # ------------------------------------------------------------------ #
    # Genel API
    # ------------------------------------------------------------------ #
    def complete(
        self,
        *,
        role: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: Iterable[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        thinking: bool = True,
        on_text: Callable[[str], None] | None = None,
    ) -> LLMResult:
        """Tek bir model cagrisi yapar ve sonucu ayristirir."""
        model = model or self.settings.model_for(role)
        effort = effort or self.settings.effort_for(role)
        max_tokens = max_tokens or self.settings.max_tokens
        tool_list = list(tools or [])

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # Sistem prompt'u sabit tutulur ve onbellege alinir: arac listesi +
            # sistem prefix'i her turda yeniden faturalanmaz.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
        }
        if tool_list:
            kwargs["tools"] = tool_list
        if supports_adaptive_thinking(model):
            kwargs["output_config"] = {"effort": effort}
            if thinking:
                kwargs["thinking"] = {
                    "type": "adaptive",
                    "display": self.settings.thinking_display,
                }

        started = time.monotonic()
        message = self._create_with_retry(kwargs, on_text=on_text)
        elapsed = time.monotonic() - started

        result = self._parse(message, model)
        self._account(model, result)

        if self.events is not None:
            self.events.emit(
                "cost",
                role,
                f"{model} · {result.usage.output_tokens} cikti tk · "
                f"{elapsed:.1f}s · ${result.cost:.4f} (toplam ${self.total_cost:.3f})",
                model=model,
                cost=result.cost,
                stop_reason=result.stop_reason,
            )
        self._check_budget()
        return result

    # ------------------------------------------------------------------ #
    # Istek gonderimi
    # ------------------------------------------------------------------ #
    def _create_with_retry(
        self,
        kwargs: dict[str, Any],
        on_text: Callable[[str], None] | None,
    ) -> Any:
        """Akisli istek gonderir; SDK bilinmeyen parametreyi reddederse tasir."""
        attempt_kwargs = dict(kwargs)
        for _ in range(3):
            try:
                with self._client.messages.stream(**attempt_kwargs) as stream:
                    if on_text is not None:
                        for chunk in stream.text_stream:
                            on_text(chunk)
                    return stream.get_final_message()
            except TypeError as exc:
                moved = self._demote_unknown_kwarg(attempt_kwargs, exc)
                if not moved:
                    raise LLMError(t("llm.call_setup_failed", error=exc)) from exc
                log.debug(t("llm.moved_kwarg", name=moved))
            except anthropic.APIStatusError as exc:
                # SDK 429/5xx icin zaten yeniden dener; buraya gelen kalicidir.
                raise LLMError(
                    t(
                        "llm.api_error",
                        status=exc.status_code,
                        message=getattr(exc, "message", exc),
                    )
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise LLMError(t("llm.connection_error", error=exc)) from exc
        raise LLMError(t("llm.kwargs_exhausted"))

    @staticmethod
    def _demote_unknown_kwarg(kwargs: dict[str, Any], exc: TypeError) -> str | None:
        """`create()` tanimadigi bir parametreyi `extra_body` icine tasir.

        Eski SDK surumlerinde `output_config` / `thinking` gibi alanlar
        bulunmayabilir; boyle bir durumda istegi bastan reddetmek yerine ham
        govdeye yerlestirip devam ederiz.
        """
        text = str(exc)
        for name in ("output_config", "thinking", "cache_control", "fallbacks"):
            if name in text and name in kwargs:
                extra = kwargs.setdefault("extra_body", {})
                extra[name] = kwargs.pop(name)
                return name
        return None

    # ------------------------------------------------------------------ #
    # Yanit ayristirma
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse(message: Any, model: str) -> LLMResult:
        texts: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []

        for block in message.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(block.text)
            elif btype == "thinking":
                thought = getattr(block, "thinking", "") or ""
                if thought:
                    thoughts.append(thought)
            elif btype == "tool_use":
                # Sunucu araclarinin bloklari `server_tool_use` tipindedir ve
                # buraya dusmez; burada yalnizca istemci araclari toplanir.
                raw = block.input
                args = raw if isinstance(raw, dict) else {}
                calls.append(ToolCall(id=block.id, name=block.name, arguments=args))

        usage = Usage.from_api(message.usage)
        return LLMResult(
            text="\n".join(texts).strip(),
            thinking="\n".join(thoughts).strip(),
            tool_calls=calls,
            stop_reason=getattr(message, "stop_reason", None),
            usage=usage,
            cost=cost_usd(model, usage),
            model=model,
            raw=list(message.content),
        )

    # ------------------------------------------------------------------ #
    # Muhasebe
    # ------------------------------------------------------------------ #
    def _account(self, model: str, result: LLMResult) -> None:
        self.ledger.add(model, result.usage, result.cost)

    def _check_budget(self) -> None:
        limit = self.settings.cost_limit_usd
        if limit and self.ledger.total_cost > limit:
            raise BudgetExceeded(
                t(
                    "llm.budget_exceeded",
                    spent=f"{self.ledger.total_cost:.2f}",
                    limit=f"{limit:.2f}",
                )
            )

    def usage_summary(self) -> str:
        return self.ledger.summary(cost_usd)

    # ------------------------------------------------------------------ #
    # Gecmis yonetimi
    # ------------------------------------------------------------------ #
    def append_assistant(self, messages: list[dict[str, Any]], result: LLMResult) -> None:
        # Icerik bloklari oldugu gibi geri konur: dusunme bloklarinin ayni model
        # uzerinde degistirilmeden dondurulmesi gerekir.
        messages.append({"role": "assistant", "content": result.raw})

    def append_note(self, messages: list[dict[str, Any]], text: str) -> None:
        """Notu son kullanici mesajina katar.

        Anthropic'te roller donusumlu olmali; arac sonuclari zaten bir
        `user` mesajidir ve arkasina ikinci bir `user` mesaji koymak
        istegi gecersiz kilar.
        """
        if messages and messages[-1].get("role") == "user":
            content = messages[-1].get("content")
            if isinstance(content, list):
                content.append({"type": "text", "text": text})
                return
            if isinstance(content, str):
                messages[-1]["content"] = f"{content}\n\n{text}"
                return
        messages.append({"role": "user", "content": text})

    def append_tool_results(
        self, messages: list[dict[str, Any]], outcomes: list[ToolOutcome]
    ) -> None:
        # Sonuclari ayri mesajlara bolmek modeli paralel arac kullanmaktan cayirir;
        # bu yuzden hepsi TEK bir kullanici mesajinda gonderilir.
        #
        # `outcome.images` BU METOTTA HIC ISLENMIYORDU. `ToolResult.images`
        # "modelin GORMESI gereken dosyalar" diye tanimli, OpenAI istemcisi
        # onlari gonderiyordu, burasi sessizce dusuruyordu: `provider =
        # "anthropic"` ile kosan bir ajan `browser_screenshot` cagirdiginda
        # modele yalnizca "kaydedildi" metni gidiyordu. Yani "yaptigini
        # gorebilme" -- README'nin one cikardigi ozellik -- Claude'da
        # calismiyordu, ustelik Claude goruyor.
        #
        # Sozlesme testi bunu yakalayamaz: metodun VARLIGINA bakiyor, ne
        # yaptigina degil. Ortak okuma `llm.base.read_image` icinde durdu ki
        # ayni ayrisma tekrar olmasin.
        content: list[dict[str, Any]] = []
        for outcome in outcomes:
            gorseller = [
                blok
                for blok in (_gorsel_blogu(yol) for yol in outcome.images or [])
                if blok is not None
            ]
            sonuc: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": outcome.call_id,
                "is_error": outcome.is_error,
            }
            # Anthropic `tool_result` icinde metin VE goruntu blogu tasir;
            # OpenAI'daki gibi ayri bir mesaja koymak gerekmiyor.
            sonuc["content"] = (
                [
                    {"type": "text", "text": outcome.content or "(bos)"},
                    *gorseller,
                ]
                if gorseller
                else (outcome.content or "(bos)")
            )
            content.append(sonuc)
        messages.append({"role": "user", "content": content})

    @staticmethod
    def trim_history(messages: list[dict[str, Any]]) -> int:
        """Eski arac ciktilarini yer tutucuyla degistirir.

        Konusmanin yapisi (hangi arac ne zaman cagrildi) korunur; yalnizca
        hacimli gozlem metinleri dusurulur.
        """
        # Goruntuler yumusak sinirdan BAGIMSIZ dusurulur: tek bir base64
        # goruntu siniri kendi basina asar ve kirpma, asil sebep goruntuyken
        # metni budamaya baslardi.
        trimmed = _eski_gorselleri_dusur(messages)

        total = sum(len(str(m.get("content", ""))) for m in messages)
        if total <= HISTORY_SOFT_LIMIT or len(messages) <= KEEP_RECENT_MESSAGES:
            return trimmed

        cutoff = len(messages) - KEEP_RECENT_MESSAGES
        for message in messages[1:cutoff]:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                body = block.get("content", "")
                if isinstance(body, str) and len(body) > 400:
                    block["content"] = TRIM_PLACEHOLDER.format(size=len(body))
                    trimmed += 1
                elif isinstance(body, list):
                    # Goruntu tasiyan sonuc: metin blogu yine kirpilir.
                    # Eskiden bu dal hic yoktu ve goruntulu bir arac ciktisi
                    # ne kadar buyurse buyusun dokunulmadan kalirdi.
                    for ic_blok in body:
                        if (
                            isinstance(ic_blok, dict)
                            and ic_blok.get("type") == "text"
                            and len(ic_blok.get("text", "")) > 400
                        ):
                            uzunluk = len(ic_blok["text"])
                            ic_blok["text"] = TRIM_PLACEHOLDER.format(size=uzunluk)
                            trimmed += 1
        return trimmed
