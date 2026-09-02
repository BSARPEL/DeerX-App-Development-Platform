"""Sistem prompt'larinin yuklenmesi ve birlestirilmesi.

Prompt'lar paket icinde markdown dosyalari olarak durur. Calisma alanindaki
`prompts/<rol>.md` dosyasi varsa paket icindekini ezer — boylece prompt'lari
kod degistirmeden ayarlayabilirsiniz.

Sistem prompt'u bilerek SABIT tutulur (proje durumu buraya konmaz): prompt
onbellegi sistem prefix'ini kapsar, degisken icerik onbellegi her turda gecersiz kilar.
Degisken baglam ilk kullanici mesajina eklenir.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..config import Settings
from ..errors import ConfigError
from ..i18n import t

PACKAGE_PROMPTS = Path(__file__).parent / "prompts"

ROLES = (
    "analyst",
    "researcher",
    "assessor",
    "mockup",
    "architect",
    "planner",
    "backend",
    "frontend",
    "qa",
    "reviewer",
    "staging",
    "live",
)


@lru_cache(maxsize=64)
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_prompt(name: str, settings: Settings | None = None) -> str:
    """Prompt dosyasini okur.

    Sira: calisma alani ezmesi -> pakette secili dil -> pakette Turkce.

    Dil klasoru eksik bir dosyayla Turkce'ye duser. Kismi ceviri boylece
    calisir durumda kalir: bir rolun Ingilizcesi yoksa o rol Turkce
    yonergeyle calisir, digerleri Ingilizce -- hicbir sey cokmez ve
    eksiklik `tests/test_prompts.py` icinde gorunur.
    """
    if settings is not None:
        override = settings.prompts_dir / f"{name}.md"
        if override.is_file():
            return _read(override)

        lang = getattr(settings, "language", "tr")
        if lang and lang != "tr":
            localized = PACKAGE_PROMPTS / lang / f"{name}.md"
            if localized.is_file():
                return _read(localized)

    packaged = PACKAGE_PROMPTS / f"{name}.md"
    if not packaged.is_file():
        raise ConfigError(t("setup.prompt_missing", name=name, path=packaged))
    return _read(packaged)


def compose_system(role: str, settings: Settings, *, extra: str = "") -> str:
    """Ortak on soz + role ozgu prompt + opsiyonel ek."""
    shared = load_prompt("_shared", settings).format(
        workspace=settings.workspace.as_posix(),
        artifacts=settings.artifacts_dir.as_posix(),
        language={"tr": "Turkce", "en": "English"}.get(settings.language, settings.language),
    )
    body = load_prompt(role, settings)
    parts = [shared, f"# Rolun: {role}", body]
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n---\n\n".join(parts)
