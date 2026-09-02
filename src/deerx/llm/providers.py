"""Bilinen model saglayicilari ve uclari.

`provider` alani PROTOKOLU soyler ("anthropic" ya da "openai"); bu liste
ise o protokolu konusan somut hizmetleri. Bugun neredeyse her saglayici
OpenAI-uyumlu bir `/v1` ucu sunuyor, dolayisiyla aralarindaki tek fark
adres, anahtar ve model adlari.

Buradaki her adres olculdu: anahtarsiz bir istek atildi ve ucun var olup
kimlik istedigi dogrulandi (401/403/400 "Authorization eksik"). Yani liste
hafizadan yazilmis bir tahmin degil.

Model adlari BILEREK yazilmadi. Saglayicilar model adlarini sik degistirir
ve burada tutulan bir liste birkac ay icinde yaniltici olur; arayuz bunun
yerine ucun kendi `/models` listesini canli getirir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ProviderPreset:
    """Bir saglayicinin baglanti bilgileri."""

    key: str
    # Marka adi; dilden bagimsizdir ve CEVRILMEZ. "yerel" gibi bir sifat
    # buraya YAZILMAZ -- arayuz onu `local` bayragindan kendi dilinde
    # ekler. Eskiden etiketin icinde Turkce bir parantez vardi ve
    # Ingilizce arayuzde de oyle gorunuyordu.
    label: str
    # Hangi protokolu konusuyor. Anthropic disindaki herkes OpenAI-uyumlu.
    protocol: Literal["openai", "anthropic"]
    base_url: str | None
    # Anahtarin alindigi sayfa; kullaniciyi aramaya birakmamak icin.
    keys_url: str = ""
    # Kendi makinenizde calisir: anahtar gerekmez, internet gerekmez.
    local: bool = False
    # Marka adi olmayan tek secenek ("Diger"); arayuz bunu cevirir.
    label_key: str = ""
    # Serbest metin DEGIL, sozluk anahtari: metnin kendisi burada dursa
    # dil degistiginde sunucuyu yeniden baslatmak gerekirdi.
    note_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "label_key": self.label_key,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "keys_url": self.keys_url,
            "local": self.local,
            "note_key": self.note_key,
        }


# Sira kullaniciya gorunen siradir: once yerel (anahtarsiz denenebilir),
# sonra buyuk saglayicilar, sonra digerleri.
PRESETS: tuple[ProviderPreset, ...] = (
    # --- Yerel ---
    ProviderPreset("vllm", "vLLM", "openai", "http://127.0.0.1:8008/v1",
                   local=True, note_key="provider.noteDockerPort"),
    ProviderPreset("ollama", "Ollama", "openai", "http://127.0.0.1:11434/v1",
                   local=True, keys_url="https://ollama.com/download"),
    ProviderPreset("lmstudio", "LM Studio", "openai", "http://127.0.0.1:1234/v1",
                   local=True, keys_url="https://lmstudio.ai/"),
    ProviderPreset("llamacpp", "llama.cpp", "openai", "http://127.0.0.1:8080/v1",
                   local=True),

    # --- Buyuk saglayicilar ---
    ProviderPreset("anthropic", "Anthropic — Claude", "anthropic", None,
                   keys_url="https://console.anthropic.com/settings/keys",
                   note_key="provider.noteAnthropic"),
    ProviderPreset("openai", "OpenAI", "openai", "https://api.openai.com/v1",
                   keys_url="https://platform.openai.com/api-keys"),
    ProviderPreset("google", "Google — Gemini", "openai",
                   "https://generativelanguage.googleapis.com/v1beta/openai",
                   keys_url="https://aistudio.google.com/apikey",
                   note_key="provider.noteGemini"),
    ProviderPreset("xai", "xAI — Grok", "openai", "https://api.x.ai/v1",
                   keys_url="https://console.x.ai/"),
    ProviderPreset("mistral", "Mistral", "openai", "https://api.mistral.ai/v1",
                   keys_url="https://console.mistral.ai/api-keys/"),
    ProviderPreset("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
                   keys_url="https://platform.deepseek.com/api_keys"),

    # --- Toplayicilar ve hizli cikarim ---
    ProviderPreset("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
                   keys_url="https://openrouter.ai/keys",
                   note_key="provider.noteAggregator"),
    ProviderPreset("groq", "Groq", "openai", "https://api.groq.com/openai/v1",
                   keys_url="https://console.groq.com/keys"),
    ProviderPreset("cerebras", "Cerebras", "openai", "https://api.cerebras.ai/v1",
                   keys_url="https://cloud.cerebras.ai/"),
    ProviderPreset("together", "Together AI", "openai", "https://api.together.xyz/v1",
                   keys_url="https://api.together.ai/settings/api-keys"),
    ProviderPreset("fireworks", "Fireworks AI", "openai",
                   "https://api.fireworks.ai/inference/v1",
                   keys_url="https://fireworks.ai/account/api-keys"),
    ProviderPreset("deepinfra", "DeepInfra", "openai",
                   "https://api.deepinfra.com/v1/openai",
                   keys_url="https://deepinfra.com/dash/api_keys"),
    ProviderPreset("nebius", "Nebius AI Studio", "openai",
                   "https://api.studio.nebius.com/v1",
                   keys_url="https://studio.nebius.com/settings/api-keys"),
    ProviderPreset("sambanova", "SambaNova", "openai", "https://api.sambanova.ai/v1",
                   keys_url="https://cloud.sambanova.ai/apis"),
    ProviderPreset("perplexity", "Perplexity", "openai", "https://api.perplexity.ai",
                   keys_url="https://www.perplexity.ai/settings/api",
                   note_key="provider.noteNoModels"),
    ProviderPreset("moonshot", "Moonshot — Kimi", "openai", "https://api.moonshot.ai/v1",
                   keys_url="https://platform.moonshot.ai/console/api-keys"),
    ProviderPreset("zhipu", "Zhipu — GLM", "openai",
                   "https://open.bigmodel.cn/api/paas/v4",
                   keys_url="https://open.bigmodel.cn/usercenter/apikeys"),

    # --- Elle ---
    ProviderPreset("custom", "Other", "openai", None,
                   # Marka adi olmayan tek secenek; arayuz `label_key`
                   # ile cevirir, `label` yalnizca yedek.
                   label_key="provider.custom",
                   note_key="provider.noteCustom"),
)

BY_KEY: dict[str, ProviderPreset] = {p.key: p for p in PRESETS}

# `/models` ucu olmayan saglayicilar; arayuz bunu onceden soyler ki
# "getir" dugmesi bos donunce kullanici bir sey bozuldu sanmasin.
NO_MODEL_LISTING = frozenset({"perplexity"})


def preset_for(base_url: str | None, protocol: str) -> str:
    """Mevcut ayarlara en cok uyan hazir secenegin anahtari.

    Ayarlar ekrani acildiginda hangi saglayicinin secili gorunecegini
    belirler; kullanici daha once elle bir adres yazmis olabilir.
    """
    if protocol == "anthropic":
        return "anthropic"
    if not base_url:
        return "custom"
    normalized = base_url.rstrip("/").lower()
    for preset in PRESETS:
        if preset.base_url and preset.base_url.rstrip("/").lower() == normalized:
            return preset.key
    return "custom"


def catalog() -> list[dict[str, Any]]:
    return [preset.to_dict() for preset in PRESETS]
