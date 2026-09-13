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
    # Alt ajan olarak cagrilir: uzun metni okur, kisa cevap doner.
    "summarizer",
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


def describe_environment(settings: Settings) -> str:
    """Komutlarin NEREDE kosacagini ajana bir cumleyle soyler.

    Ajan yalitim kipini, yayinlanan port araligini ve isletim sistemini
    hic ogrenmiyordu; ortami deneme-yanilmayla kesfediyordu. OLCULDU:
    QA yonergesindeki `port=3000` ornegi docker kipinde her kosuda ILK
    denemede reddediliyor (`sandbox.port_outside_range`) -- yani her
    kosu, bilinebilir bir bilgiyi ogrenmek icin bir tur yakiyordu.

    Konak kipinde soylenmesi gereken baska: izin listesi. Ajan
    `uvicorn ...` yazip reddedilince hatayi kendi komutunda ariyor;
    listeyi bastan gormek `python -m uvicorn` demesini saglar.
    """
    import platform

    if settings.execution == "docker":
        son = settings.sandbox_port_base + settings.sandbox_port_count - 1
        return t(
            "prompt.env_docker",
            image=settings.sandbox_image,
            first=settings.sandbox_port_base,
            last=son,
        )
    return t(
        "prompt.env_host",
        os=platform.system(),
        prefixes=", ".join(settings.shell.allow_prefixes) or "-",
        timeout=settings.shell.max_timeout_seconds,
    )


def describe_web(role: str, settings: Settings) -> str:
    """Bu ROLUN dis bilgiye nasil ulasacagini bir paragrafla soyler.

    Ayni metni her role vermek iki yonden yanlis: `web_search` aracini
    gormeyen bir role aramayi anlatmak, modeli var olmayan bir cagriya
    davet eder ve o turu yakar; web araci hic olmayan bir role de
    "arastir" demek, uydurmaya acik kapi birakir.

    Uc hal var ve ucu de `TOOLSETS`ten OKUNUR, burada elle listelenmez:
    liste iki yerde tutulsaydi biri degisip oteki kalirdi.
    """
    from ..tools import TOOLSETS

    if not settings.enable_web:
        return t("prompt.web_off")

    araclar = set(TOOLSETS.get(role, ()))
    if {"web_search", "browse_page"} & araclar:
        govde = t("prompt.web_full")
    elif "fetch_url" in araclar:
        govde = t("prompt.web_targeted")
    else:
        # `record_gaps` her rolde yok (ornegin `summarizer` yalnizca okur).
        # Olmayan bir araci onermek, az once kacindigimiz hatanin ta kendisi.
        return t("prompt.web_none" if "record_gaps" in araclar else "prompt.web_none_plain")
    # Enjeksiyon uyarisi yalnizca web'e ULASABILEN role gider: aracı
    # olmayan bir role "okudugun sayfaya guvenme" demek, okumadigi bir
    # sey hakkinda yonerge vermektir.
    return govde + " " + t("prompt.web_untrusted")


def compose_system(role: str, settings: Settings, *, extra: str = "") -> str:
    """Ortak on soz + role ozgu prompt + opsiyonel ek."""
    shared = load_prompt("_shared", settings).format(
        workspace=settings.workspace.as_posix(),
        artifacts=settings.artifacts_dir.as_posix(),
        environment=describe_environment(settings),
        web=describe_web(role, settings),
        language={"tr": "Turkce", "en": "English"}.get(settings.language, settings.language),
    )
    body = load_prompt(role, settings)
    parts = [shared, f"# Rolun: {role}", body]
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n---\n\n".join(parts)
