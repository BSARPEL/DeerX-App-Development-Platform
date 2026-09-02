"""Saglayicidan bagimsiz LLM sozlesmesi.

Ajan dongusu hicbir saglayicinin mesaj bicimini bilmez. Konusma gecmisi, ilgili
istemcinin anladigi bicimde tutulur ve yalnizca istemci ona dokunur:

    client.append_assistant(messages, result)
    client.append_tool_results(messages, outcomes)
    client.trim_history(messages)

Boylece Anthropic'in icerik bloklari ile OpenAI'nin `tool_calls` bicimi arasindaki
fark tek bir dosyada kalir; `deerx.agents.base` ikisini de ayni sekilde surer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .pricing import Usage


@dataclass(slots=True)
class ToolCall:
    """Modelin calistirilmasini istedigi tek bir arac cagrisi."""

    id: str
    name: str
    arguments: dict[str, Any]
    # Argumanlar oldugu gibi cozulebildi mi? Uretim tavaninda cagrinin
    # ORTASINDA kesilen model yarim JSON gonderir; o zaman `arguments` bos
    # sozluge dusurulur ve bu bayrak False olur. Ajan dongusu bunu gormeden
    # araci bos argumanla calistiriyor ve model, kesildigini ogrenmek yerine
    # anlamsiz bir imza hatasi aliyordu.
    arguments_ok: bool = True


@dataclass(slots=True)
class ToolOutcome:
    """Calistirilmis bir aracin sonucu; istemciye geri beslenir."""

    call_id: str
    name: str
    content: str
    is_error: bool = False
    # Modelin GORMESI gereken dosyalar (ekran goruntusu gibi). OpenAI
    # biciminde `role: "tool"` mesajlari gorsel tasiyamaz; istemci bunlari
    # ardindan gelen bir `user` mesajina koyar.
    images: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class LLMResult:
    """Tek bir model cagrisinin saglayicidan bagimsiz sonucu."""

    text: str
    thinking: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    usage: Usage
    cost: float
    model: str
    # Saglayiciya ozgu ham yanit; yalnizca onu ureten istemci yorumlar.
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class LLMClient(Protocol):
    """Ajan dongusunun bagli oldugu arayuz."""

    total_cost: float

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
    ) -> LLMResult: ...

    def append_assistant(self, messages: list[dict[str, Any]], result: LLMResult) -> None:
        """Model yanitini gecmise ekler (saglayicinin bekledigi bicimde)."""

    def append_tool_results(
        self, messages: list[dict[str, Any]], outcomes: list[ToolOutcome]
    ) -> None:
        """Arac sonuclarini gecmise ekler."""

    def append_note(self, messages: list[dict[str, Any]], text: str) -> None:
        """Gecmise bir kullanici notu ekler.

        Saglayiciya birakiliyor cunku bicim farki onemli: Anthropic'te
        ard arda iki `user` mesaji gecersizdir, o yuzden not varsa son
        mesajin icine katilir; OpenAI'de arac sonuclari `tool` rolunde
        oldugu icin ayri bir `user` mesaji eklenebilir.
        """
        messages.append({"role": "user", "content": text})

    def trim_history(self, messages: list[dict[str, Any]]) -> int:
        """Gecmis buyudugunde eski arac ciktilarini kirpar; kirpilan sayiyi doner."""

    def usage_summary(self) -> str: ...


# Gecmis bu esigi asinca eski arac ciktilari kirpilir (karakter).
HISTORY_SOFT_LIMIT = 500_000
KEEP_RECENT_MESSAGES = 12

# Kirpilan bir arac ciktisinin yerine yazilan metin.
TRIM_PLACEHOLDER = "[eski arac ciktisi kirpildi — {size:,} karakter]"

# Gecmiste tutulan azami goruntu sayisi. Ekran goruntusu ajanin kendi
# isini gormesi icin var ve ise yarayan HEP SONUNCUSUDUR: on tur once
# alinan goruntu, o zamandan beri degisen bir arayuzu gosterir. Sinirsiz
# birakmak her turda megabaytlarca base64'u tekrar tekrar gondermek
# demekti.
KEEP_RECENT_IMAGES = 2

# ---------------------------------------------------------------------- #
# Goruntuler
#
# Iki istemci de ayni dosyalari okur ve ayni sinirlara uyar; yalnizca
# bicimlendirme farklidir (OpenAI `image_url`, Anthropic `image` blogu).
# Ortak parca BURADA duruyor cunku ayri ayri yazildiginda tam olarak
# beklenen sey oldu: OpenAI tarafi goruntuleri gonderiyordu, Anthropic
# tarafi hic islemiyordu ve `browser_screenshot` sessizce metne
# donusuyordu -- sozlesme testi metodun VARLIGINA bakiyor, ne yaptigina
# degil.
# ---------------------------------------------------------------------- #

# Base64 bir goruntuyu uc kat sisirir; sinirsiz birakmak baglami tek
# dosyada doldurur.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Bir goruntunun token maliyeti icin karamsar ust sinir.
#
# Saglayicilar goruntuyu BAYT olarak degil ALAN olarak fiyatlar
# (kabaca genislik*yukseklik/750) ve girdiyi ~1,15 megapiksele indirger,
# yani tek bir goruntu bu tavani pratikte asmaz.
#
# OLCULDU: base64 metnini token sayan tahmin, 1 MB'lik tek bir ekran
# goruntusu icin 559.816 token cikariyordu; gercek maliyeti ~1.600.
# 262K pencereli bir ucta bu, ILK ekran goruntusunde kosuyu
# `context_overflow` ile oldurmek demekti -- ve hata mesaji baglamin
# gercekten dolduugunu soyluyordu.
IMAGE_TOKEN_ESTIMATE = 2_000


def read_image(path: Path | str) -> tuple[str, str] | None:
    """Goruntuyu `(media_type, base64)` olarak okur.

    Gonderilemiyorsa None: taninmayan uzanti, olmayan dosya ya da
    sinirin ustunde boyut. Cagiran taraf None'i "bu goruntu yok" diye
    ele alir; hicbir kosulda kosuyu dusurmez.
    """
    import base64

    p = Path(path)
    media_type = IMAGE_MEDIA_TYPES.get(p.suffix.lower())
    if media_type is None or not p.is_file():
        return None
    data = p.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        return None
    return media_type, base64.b64encode(data).decode()


@dataclass
class UsageLedger:
    """Model basina kullanim ve maliyet sayaci (istemciler paylasir)."""

    by_model: dict[str, Usage] = field(default_factory=dict)
    total_cost: float = 0.0

    def add(self, model: str, usage: Usage, cost: float) -> None:
        self.by_model[model] = self.by_model.get(model, Usage()) + usage
        self.total_cost += cost

    def summary(self, price_lookup: Callable[[str, Usage], float]) -> str:
        if not self.by_model:
            return "Model cagrisi yapilmadi."
        lines = []
        for model, usage in sorted(self.by_model.items()):
            cost = price_lookup(model, usage)
            lines.append(
                f"  {model}: {usage.calls} cagri · "
                f"{usage.input_tokens:,} girdi / {usage.output_tokens:,} cikti · "
                + (f"onbellek okuma {usage.cache_read_input_tokens:,} · "
                   if usage.cache_read_input_tokens else "")
                + (f"${cost:.4f}" if cost else "ucretsiz (yerel)")
            )
        total = f"${self.total_cost:.4f}" if self.total_cost else "ucretsiz (yerel model)"
        lines.append(f"  TOPLAM: {total}")
        return "\n".join(lines)
