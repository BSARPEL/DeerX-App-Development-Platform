"""Ajan dongusu: dusun -> arac cagir -> gozlemle -> tekrarla.

Tool Runner yerine el yazimi dongu kullaniliyor; cunku her adimda onay kapisi,
olay gunlugu, maliyet muhasebesi ve gecmis kirpma gibi kancalara ihtiyac var.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..errors import BudgetExceeded, LLMError
from ..i18n import t
from ..llm import LLMClient, ToolOutcome, Usage
from ..logging import EventLog, console, get_logger
from ..tools import ToolContext, ToolRegistry

log = get_logger("agent")

@dataclass(slots=True)
class AgentResult:
    """Bir ajan kosusunun sonucu."""

    text: str
    role: str
    iterations: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost: float = 0.0
    stop_reason: str = "end_turn"
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


# Yanit uretim tavaninda kesildiginde kac kez "devam et" denir. Sinirsiz
# olmamali: modelin her turda tavani doldurdugu bir durumda dongu sonsuza
# kadar surerdi.
_MAX_TRUNCATIONS = 2

# Tur butcesinin ne kadari harcandiginda modele haber verilir. Erken
# soylemek isini bolerdi; gec soylemek hicbir sey degistirmezdi.
_BUDGET_WARN_AT = 0.7



class Agent:
    """Tek rollu, arac kullanan ajan."""

    def __init__(
        self,
        *,
        role: str,
        system_prompt: str,
        registry: ToolRegistry,
        context: ToolContext,
        client: LLMClient,
        events: EventLog,
        server_tools: list[dict[str, Any]] | None = None,
        max_iterations: int | None = None,
        stream: bool = True,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.role = role
        self.system_prompt = system_prompt
        self.registry = registry
        self.ctx = context
        self.client = client
        self.events = events
        self.server_tools = server_tools or []
        self.settings: Settings = context.settings
        self.max_iterations = max_iterations or self.settings.max_iterations
        self.stream = stream
        # Isbirlikci iptal: her tur basinda sorulur. Model cagrisinin ortasinda
        # kesmek yerine tur sinirinda durmak, konusma gecmisini tutarli birakir.
        self.should_stop = should_stop
        # Butce uyarisinin dusecegi tur. En az iki tur kalmali ki uyari bir
        # ise yarasin.
        self._warn_iteration = max(1, int(self.max_iterations * _BUDGET_WARN_AT))
        if self.max_iterations - self._warn_iteration < 2:
            self._warn_iteration = max(1, self.max_iterations - 2)

    # ------------------------------------------------------------------ #
    # Kosu
    # ------------------------------------------------------------------ #
    def run(self, task: str, *, context: str = "") -> AgentResult:
        """Ajani gorev bitene veya iterasyon siniri dolana kadar calistirir."""
        prompt = task if not context else f"{context}\n\n---\n\n{task}"
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        # Sunucu araclari (web_search/web_fetch) ile yerel araclar ayni listede gider.
        tool_specs = [*self.registry.specs(), *self.server_tools]

        result = AgentResult(text="", role=self.role)
        # Yanit kac kez uretim tavaninda kesildi.
        truncations = 0
        warned = False
        self.events.emit("agent", self.role, t("agent.started"))

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            if self.should_stop is not None and self.should_stop():
                result.stop_reason = "cancelled"
                self.events.emit("warn", self.role, t("agent.cancelled"))
                break
            # Butce bitmeden once haber ver. Harness kac tur kaldigini
            # biliyor, model bilmiyordu: olculdu, QA fazi yirmi dort turun
            # tamamini UAT yaparak harcadi ve raporunu yazamadan durduruldu.
            # Yapilan is kaybolmus olmuyor ama kaydedilmemis olmasi ayni
            # kapiya cikiyor.
            if iteration == self._warn_iteration and not warned:
                warned = True
                self.events.emit(
                    "warn",
                    self.role,
                    t(
                        "agent.budget_warning",
                        used=iteration, total=self.max_iterations,
                    ),
                )
                self.client.append_note(
                    messages,
                    t(
                        "agent.budget_hint",
                        left=self.max_iterations - iteration + 1,
                        total=self.max_iterations,
                    ),
                )
            trimmed = self.client.trim_history(messages)
            if trimmed:
                log.debug("Gecmiste %d eski arac ciktisi kirpildi.", trimmed)

            try:
                completion = self.client.complete(
                    role=self.role,
                    system=self.system_prompt,
                    messages=messages,
                    tools=tool_specs,
                    on_text=self._on_text if self.stream else None,
                )
            except BudgetExceeded as exc:
                result.error = str(exc)
                result.stop_reason = "budget"
                self.events.emit("error", self.role, str(exc))
                break
            except LLMError as exc:
                result.error = str(exc)
                result.stop_reason = "llm_error"
                self.events.emit("error", self.role, str(exc))
                break

            if self.stream:
                self._flush_stream()

            result.usage = result.usage + completion.usage
            result.cost += completion.cost
            if completion.text:
                result.text = completion.text
                if not self.stream:
                    # Akis kapaliyken (web/MCP) modelin metni yalnizca olay
                    # gunlugunden gorunur; acikken zaten stdout'a yazildi.
                    self.events.emit("message", self.role, completion.text)
            self.client.append_assistant(messages, completion)

            if completion.stop_reason == "refusal":
                result.error = t("agent.refusal")
                result.stop_reason = "refusal"
                self.events.emit("error", self.role, result.error)
                break

            if completion.stop_reason == "pause_turn":
                # Sunucu araci uzun surdu; ayni turu devam ettirmek icin yeniden gonder.
                self.events.emit("agent", self.role, t("agent.server_tool_paused"))
                continue

            # Tavana cagrinin ORTASINDA takilmak da kesilmedir. Asagidaki
            # yorumun varsaydigi gibi yarim cagri her zaman DUSMUYOR:
            # olculdu (yerel vLLM), bozuk argumanlarla geliyor ve
            # `tool_calls` dolu oldugu icin bu dal atlaniyordu. Arac o
            # zaman bos sozlukle kosuyor, model de kesildigini ogrenmek
            # yerine imza hatasi aliyordu.
            kesik_cagri = any(
                not cagri.arguments_ok for cagri in completion.tool_calls
            )
            if not completion.tool_calls or kesik_cagri:
                if completion.stop_reason == "max_tokens" or kesik_cagri:
                    # Yanit uretim tavaninda KESILDI. Yarim kalan arac cagrisi
                    # dusuyor ve `tool_calls` bos geliyor -- yani kesilen yanit,
                    # isini bitirmis bir yanittan ayirt edilemiyordu ve dongu
                    # sessizce sona eriyordu.
                    #
                    # Olculdu: on uc fazlik bir kosuda `assess` fazi tam 16000
                    # token uretti (tavanin kendisi), yazmakta oldugu raporu
                    # kaydedemedi ve faz "tamam" gorundu -- uc tur, bes arac,
                    # sifir cikti. `mockup` fazi da ayni sekilde bos gecti.
                    # Tavana iki ayri sebeple takilinir ve tavsiye ayni
                    # degildir. Model butun butceyi DUSUNMEYE harcadiysa
                    # ortada kisaltilacak bir cikti yok; "raporunu kisalt"
                    # demek anlamsizdir. Olculdu (yerel Qwen3): 4000 tokenin
                    # tamami akil yurutmeye gitti, 0 karakter cevap.
                    dusunmede_bitti = bool(completion.thinking) and not completion.text
                    truncations += 1
                    if truncations > _MAX_TRUNCATIONS:
                        result.error = t(
                            "agent.thinking_overrun_giving_up"
                            if dusunmede_bitti
                            else "agent.truncated_giving_up",
                            n=_MAX_TRUNCATIONS + 1,
                        )
                        result.stop_reason = "max_tokens"
                        self.events.emit("error", self.role, result.error)
                        break
                    self.events.emit(
                        "warn", self.role, t("agent.truncated", n=truncations)
                    )
                    messages.append({
                        "role": "user",
                        "content": t(
                            "agent.thinking_overrun"
                            if dusunmede_bitti
                            else "agent.truncated_hint"
                        ),
                    })
                    continue
                result.stop_reason = completion.stop_reason or "end_turn"
                break

            outcomes = self._execute_tools(completion.tool_calls)
            result.tool_calls += len(completion.tool_calls)
            self.client.append_tool_results(messages, outcomes)
        else:
            result.stop_reason = "max_iterations"
            self.events.emit(
                "warn",
                self.role,
                t("agent.max_iterations", limit=self.max_iterations),
            )

        result.messages = messages
        if result.ok:
            self.events.emit(
                "done",
                self.role,
                f"bitti · {result.iterations} tur · {result.tool_calls} arac · ${result.cost:.4f}",
            )
        return result

    # ------------------------------------------------------------------ #
    # Yardimcilar
    # ------------------------------------------------------------------ #
    def _execute_tools(self, calls: list[Any]) -> list[ToolOutcome]:
        """Tum arac cagrilarini calistirir ve notr sonuclari doner.

        Sonuclarin gecmise nasil yazilacagi saglayiciya baglidir; o isi istemci
        yapar (Anthropic hepsini tek mesajda toplar, OpenAI ayri ayri yollar).
        """
        results: list[ToolOutcome] = []
        for call in calls:
            preview = ", ".join(
                f"{k}={str(v)[:60]}" for k, v in list(call.arguments.items())[:3]
            )
            self.events.emit("tool", self.role, f"{call.name}({preview})")

            outcome = self.registry.execute(call.name, call.arguments, self.ctx)
            if outcome.is_error:
                self.events.emit("tool_error", self.role, f"{call.name}: {outcome.content[:180]}")

            results.append(
                ToolOutcome(
                    call_id=call.id,
                    name=call.name,
                    content=outcome.content or "(bos)",
                    is_error=outcome.is_error,
                    images=list(getattr(outcome, "images", []) or []),
                )
            )
        return self._fit_turn_budget(results)

    def _fit_turn_budget(self, outcomes: list[ToolOutcome]) -> list[ToolOutcome]:
        """Bir turdaki toplam arac ciktisini butceye sigdirir.

        Tek arac siniri paralel cagrilarda yetmez: on arac ayni turda tam
        butcesini kullanirsa baglama on kat yuk biner. Butce esit pay olarak
        dagitilir; kucuk ciktilar dokunulmadan gecer, buyukler kirpilir.
        """
        budget = self.settings.max_turn_output_chars
        total = sum(len(o.content) for o in outcomes)
        if not outcomes or total <= budget:
            return outcomes

        share = max(1_000, budget // len(outcomes))
        # Payini kullanmayanlarin artigi buyuklere dagitilir.
        spare = sum(share - len(o.content) for o in outcomes if len(o.content) < share)
        oversized = [o for o in outcomes if len(o.content) > share]
        bonus = spare // len(oversized) if oversized else 0

        for outcome in oversized:
            limit = share + bonus
            dropped = len(outcome.content) - limit
            outcome.content = (
                outcome.content[:limit]
                + f"\n\n…[tur butcesi: {dropped:,} karakter kisaltildi]"
            )
        self.events.emit(
            "warn",
            self.role,
            f"tur arac ciktisi {total:,} karakterdi; {budget:,} butcesine sigdirildi",
        )
        return outcomes

    # ------------------------------------------------------------------ #
    # Akis ciktisi
    # ------------------------------------------------------------------ #
    def _on_text(self, chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()

    @staticmethod
    def _flush_stream() -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()
        console.print()
