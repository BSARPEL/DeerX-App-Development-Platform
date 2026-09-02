"""Model fiyatlandirmasi ve maliyet hesabi (USD / 1M token)."""

from __future__ import annotations

from dataclasses import dataclass

# (girdi, cikti) USD / 1M token. Kaynak: Anthropic fiyat tablosu.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Onbellek carpanlari: yazma ~1.25x girdi, okuma ~0.1x girdi.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

_FALLBACK_PRICE = (5.00, 25.00)


@dataclass(slots=True)
class Usage:
    """Bir veya daha fazla model cagrisinin toplam token kullanimi."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            calls=self.calls + other.calls,
        )

    @classmethod
    def from_api(cls, usage: object) -> Usage:
        """Anthropic `response.usage` nesnesini guvenli sekilde cevirir."""
        get = lambda name: int(getattr(usage, name, 0) or 0)  # noqa: E731
        return cls(
            input_tokens=get("input_tokens"),
            output_tokens=get("output_tokens"),
            cache_creation_input_tokens=get("cache_creation_input_tokens"),
            cache_read_input_tokens=get("cache_read_input_tokens"),
            calls=1,
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


def is_local_model(model: str) -> bool:
    """Model yerel bir sunucuda mi calisiyor?

    Anthropic model kimlikleri `claude-` ile baslar. Bunun disindaki her sey
    (vLLM'de servis edilen `qwen3.8 max` gibi) yerel kabul edilir ve ucretsiz
    sayilir — token sayimi yine tutulur, yalnizca fiyat sifirdir.
    """
    return not model.startswith("claude-")


def price_for(model: str) -> tuple[float, float]:
    """Model kimligine karsilik (girdi, cikti) fiyatini doner.

    Yerel modeller ucretsizdir. Bilinmeyen bir *Claude* kimligi icin Opus katmani
    fiyati varsayilir; boylece maliyet dusuk degil, yuksek tahmin edilir.
    """
    if model in PRICES:
        return PRICES[model]
    for known, price in PRICES.items():
        if model.startswith(known):
            return price
    if is_local_model(model):
        return (0.0, 0.0)
    return _FALLBACK_PRICE


def cost_usd(model: str, usage: Usage) -> float:
    """Verilen kullanimin yaklasik USD maliyeti."""
    inp, out = price_for(model)
    per_token_in = inp / 1_000_000
    per_token_out = out / 1_000_000
    return (
        usage.input_tokens * per_token_in
        + usage.output_tokens * per_token_out
        + usage.cache_creation_input_tokens * per_token_in * CACHE_WRITE_MULTIPLIER
        + usage.cache_read_input_tokens * per_token_in * CACHE_READ_MULTIPLIER
    )
