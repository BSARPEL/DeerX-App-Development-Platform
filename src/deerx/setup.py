"""Kurulum: projenin ihtiyaci olan her seyi kurar ve dogrular.

`deerx doctor` NE eksik oldugunu soyler; bu modul eksigi KAPATIR. Ayrimi
korumak onemli: doctor hicbir seye dokunmaz, setup dokunur.

Mantik burada, kabuk betiklerinde degil. `deerx.ps1` ve `deerx.sh` ayni
adimlari iki dilde tekrarlasaydi biri digerinden sapardi ve hangisinin
dogru oldugu belirsiz olurdu.

Ne KURULUR ve ne yalnizca BILDIRILIR ayrimi da bilincli:

  kurulur   : bagimliliklar, SearXNG konteyneri, calisma alani, gomme modeli
  bildirilir: vLLM (GPU ve sizin sectiginiz model agirliklari gerekir),
              Chrome (sistem kurulumu), Docker'in kendisi

Sizin adiniza GPU'lu bir konteyner baslatmak ya da tarayici kurmak, bu
betigin verebilecegi karar degil.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import CONFIG_FILENAME, Settings
from .i18n import t
from .process import child_env

Durum = Literal["ok", "kuruldu", "uyari", "eksik"]


@dataclass(slots=True)
class Adim:
    """Tek bir kurulum adiminin sonucu."""

    # Katalog ANAHTARI, cozulmus ad degil. Adimlar bazen dil bilinmeden
    # once uretiliyor (`setup` calisma alanini ayarlardan once kurar);
    # cozumu cizime birakmak, o sirali baglantiyi zararsiz kilar.
    anahtar: str
    durum: Durum
    detay: str = ""
    # Kullanicinin elle calistirmasi gereken komut (varsa).
    komut: str = ""

    @property
    def ad(self) -> str:
        """Etkin dildeki adim adi."""
        return t(self.anahtar)

    @property
    def engel(self) -> bool:
        """Bu adim eksikken DeerX calisir mi?"""
        return self.durum == "eksik"


# ---------------------------------------------------------------------- #
# Yardimcilar
# ---------------------------------------------------------------------- #
def _calistir(argv: list[str], *, timeout: int = 900) -> tuple[int, str]:
    """Komutu calistirir; (cikis kodu, birlestirilmis cikti) doner."""
    try:
        sonuc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env(),
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return sonuc.returncode, (sonuc.stdout or "") + (sonuc.stderr or "")


def _var_mi(program: str) -> bool:
    return shutil.which(program) is not None


# ---------------------------------------------------------------------- #
# Adimlar
# ---------------------------------------------------------------------- #
def python_surumu() -> Adim:
    """Surum bilgisi. Kontrol degil: paket `requires-python = ">=3.11"`
    ile kuruldugu icin bu kodu calistiran yorumlayici zaten uygun."""
    return Adim("step.python", "ok", f"{sys.version_info.major}.{sys.version_info.minor}")


def bagimliliklar(*, kur: bool) -> Adim:
    """Istege bagli eklerin kurulu olup olmadigina bakar, eksikse kurar."""
    eksik = []
    for modul, ek in (("fastembed", "embed"), ("playwright", "browser")):
        try:
            __import__(modul)
        except ImportError:
            eksik.append(ek)

    if not eksik:
        return Adim("step.dependencies", "ok", "embed + browser")

    komut = ["uv", "sync"] + [f"--extra={e}" for e in eksik]
    if not kur:
        return Adim("step.dependencies", "uyari",
                    t("setup.extras_missing", extras=", ".join(eksik)),
                    komut=" ".join(komut))
    if not _var_mi("uv"):
        return Adim("step.dependencies", "eksik", t("setup.no_uv"),
                    komut=" ".join(komut))

    kod, _ = _calistir(komut)
    if kod != 0:
        return Adim("step.dependencies", "eksik",
                    t("setup.extras_failed", extras=", ".join(eksik)),
                    komut=" ".join(komut))
    return Adim("step.dependencies", "kuruldu", ", ".join(eksik))


def tarayici(settings: Settings) -> Adim:
    """Tarayici araclari sistemde kurulu Chrome'u kullanir."""
    from .browser.session import find_browser

    bulunan = find_browser(settings.browser_channel)
    if bulunan is None:
        # Engel degil: tarayici olmadan da analiz/plan fazlari kosar.
        return Adim("step.browser", "uyari", t("setup.browser_absent"))
    return Adim("step.browser", "ok", bulunan.label)


def docker() -> Adim:
    if not _var_mi("docker"):
        return Adim("step.docker", "uyari", t("setup.no_docker"))
    kod, cikti = _calistir(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
    if kod != 0:
        return Adim("step.docker", "uyari", t("setup.docker_not_running"))
    return Adim("step.docker", "ok", cikti.strip().splitlines()[0] if cikti.strip() else "")


SEARXNG_AYAR = """# SearXNG -- DeerX'in arama ucu. `deerx setup` uretti.
#
# `search.formats` icindeki `json` SART: varsayilan olarak kapalidir ve
# olmadan uc 403 doner.
use_default_settings: true

server:
  secret_key: "{secret}"
  limiter: false            # Kendi ornegimiz; kendimizi hiz sinirlamiyoruz.
  image_proxy: false

search:
  formats:
    - html
    - json
  safe_search: 0
  autocomplete: ""
"""


def _koke_ekle(metin: str, satir: str) -> str:
    """Bir ayari `[deerx]` tablosunun ICINE ekler.

    Dosyanin SONUNA eklemek yanlis: TOML'da bir anahtar, kendinden onceki
    son tablo basligina aittir. `deerx.toml` `[deerx.rag]` ve
    `[deerx.shell]` ile bitiyor, yani sona eklenen `search_provider`
    `[deerx.shell]` icinde kaliyor ve HICBIR SEY yapmiyor -- bu tam olarak
    `config._warn_misplaced_root_keys` fonksiyonunun anlattigi tuzak,
    ve bu kod ilk yazildiginda ona dustu.

    Baslik yoksa dosya zaten okunmuyor demektir; tabloyu da kurariz.
    """
    baslik = re.search(r"^\[deerx\]\s*$", metin, flags=re.MULTILINE)
    if baslik is None:
        return metin.rstrip("\n") + f"\n\n[deerx]\n{satir}\n"
    kesme = baslik.end()
    # Basligin hemen ardina: sonraki alt tablo nerede olursa olsun
    # anahtar dogru tabloda kalir.
    return metin[:kesme] + "\n" + satir + metin[kesme:]


def searxng(
    settings: Settings, *, kur: bool, veri_dizini: Path | None = None
) -> Adim:
    """Kendi SearXNG ornegi: anahtarsiz arama icin olculen tek saglam yol.

    Genel motorlarin durumu olculdu: Bing otomasyona sahte sonuc veriyor,
    DuckDuckGo CAPTCHA cikariyor, digerleri engelliyor. Kendi ornegimizde
    bunlarin hicbiri yok.
    """
    import httpx

    taban = settings.searxng_url.rstrip("/")

    def yasiyor() -> bool:
        try:
            yanit = httpx.get(
                f"{taban}/search",
                params={"q": "deerx kurulum yoklamasi", "format": "json"},
                timeout=25.0,
            )
            return yanit.status_code == 200 and "json" in yanit.headers.get(
                "content-type", ""
            )
        except Exception:  # noqa: BLE001 - ayakta degil ya da JSON kapali
            return False

    if yasiyor():
        return Adim("step.searxng", "ok", taban)
    if not kur:
        return Adim("step.searxng", "uyari", t("setup.searxng_absent", url=taban))
    if not _var_mi("docker"):
        return Adim("step.searxng", "uyari", t("setup.no_docker"))

    dizin = veri_dizini or (Path.home() / ".deerx-searxng")
    dizin.mkdir(parents=True, exist_ok=True)
    ayar = dizin / "settings.yml"
    if not ayar.is_file():
        import secrets as _secrets

        ayar.write_text(
            SEARXNG_AYAR.format(secret=_secrets.token_hex(16)),
            encoding="utf-8",
            newline="\n",
        )

    port = taban.rsplit(":", 1)[-1]
    if not port.isdigit():
        port = "8890"

    _calistir(["docker", "rm", "-f", "deerx-searxng"], timeout=60)
    kod, cikti = _calistir([
        "docker", "run", "-d",
        "--name", "deerx-searxng",
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{port}:8080",
        "-v", f"{dizin.as_posix()}:/etc/searxng:rw",
        "-e", f"SEARXNG_BASE_URL=http://localhost:{port}/",
        "searxng/searxng:latest",
    ], timeout=600)
    if kod != 0:
        return Adim("step.searxng", "uyari",
                    t("setup.searxng_failed", error=cikti.strip()[:160]))

    # Acilmasini bekle: "konteyner basladi" ile "arama calisiyor" ayni sey degil.
    for _ in range(40):
        if yasiyor():
            return Adim("step.searxng", "kuruldu", f"{taban} · {dizin}")
        time.sleep(3)
    return Adim("step.searxng", "uyari", t("setup.searxng_slow", url=taban))


def model_ucu(settings: Settings) -> Adim:
    """Model ucunu yoklar. BASLATMAZ: GPU ve sizin sectiginiz agirliklar gerekir."""
    if settings.provider == "anthropic":
        if settings.anthropic_api_key:
            return Adim("step.endpoint", "ok", "anthropic")
        return Adim("step.endpoint", "eksik", t("setup.no_anthropic_key"),
                    komut="ANTHROPIC_API_KEY=sk-ant-...")

    import httpx

    taban = (settings.openai_base_url or "").rstrip("/")
    if not taban:
        return Adim("step.endpoint", "eksik", t("cli.base_undefined"))
    basliklar = (
        {"Authorization": f"Bearer {settings.openai_api_key}"}
        if settings.openai_api_key else {}
    )
    try:
        yanit = httpx.get(f"{taban}/models", headers=basliklar, timeout=10.0)
        yanit.raise_for_status()
        sunulan = [m.get("id") for m in yanit.json().get("data", [])]
    except Exception as exc:  # noqa: BLE001 - uc kapali ya da anahtar yanlis
        # httpx hatalari bir belge adresiyle devam eder; ilk satir yeter ve
        # cumlenin ortasinda kesilmis bir metin gostermekten iyidir.
        sebep = str(exc).splitlines()[0].strip()
        return Adim("step.endpoint", "eksik",
                    t("setup.endpoint_down", url=taban, error=sebep),
                    komut=t("setup.vllm_command"))

    eksik = [m for m in {settings.model_lead, settings.model_worker} if m not in sunulan]
    if eksik:
        return Adim("step.endpoint", "eksik",
                    t("cli.model_not_served",
                      missing=", ".join(eksik), served=", ".join(sunulan)))
    return Adim("step.endpoint", "ok", f"{taban} · {', '.join(sunulan)}")


def calisma_alani(hedef: Path, *, kur: bool) -> Adim:
    if (hedef / CONFIG_FILENAME).is_file():
        return Adim("step.workspace", "ok", str(hedef))
    if not kur:
        return Adim("step.workspace", "uyari", t("setup.no_workspace"),
                    komut=f"deerx init {hedef}")

    from .config import load_settings

    hedef.mkdir(parents=True, exist_ok=True)
    sablon = Path(__file__).parent / "templates" / "deerx.default.toml"
    (hedef / CONFIG_FILENAME).write_text(
        sablon.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    env = hedef / ".env"
    if not env.exists():
        env.write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8", newline="\n")
    ayarlar = load_settings(hedef)
    ayarlar.ensure_dirs()
    (hedef / "docs").mkdir(exist_ok=True)
    return Adim("step.workspace", "kuruldu", str(hedef))


def gomme_modeli(settings: Settings, *, indir: bool) -> Adim:
    """Gomme modeli ilk indekslemede inerdi; istenirse simdi indirilir.

    ~2,2 GB. Varsayilan olarak indirilmez: uzun bir indirmeyi kullanicinin
    haberi olmadan baslatmak dogru degil.
    """
    if settings.rag.embedding_provider == "hash":
        return Adim("step.embedder", "uyari", t("setup.hash_embedder"))
    if not indir:
        return Adim("step.embedder", "uyari",
                    t("setup.model_on_demand", model=settings.rag.embedding_model),
                    komut="deerx setup --with-embedding-model")
    try:
        from .rag.embedder import build_embedder

        gomucu = build_embedder(settings.rag)
        gomucu.encode(["deerx kurulum yoklamasi"])
    except Exception as exc:  # noqa: BLE001 - indirme cesitli sekilde dusebilir
        return Adim("step.embedder", "uyari", str(exc)[:120])
    return Adim("step.embedder", "kuruldu", settings.rag.embedding_model)


# ---------------------------------------------------------------------- #
# Akis
# ---------------------------------------------------------------------- #
def kur(
    settings: Settings,
    *,
    workspace: Path,
    kur_bagimlilik: bool = True,
    kur_searxng: bool = True,
    indir_model: bool = False,
) -> list[Adim]:
    """Butun adimlari sirayla calistirir ve sonuclarini doner."""
    adimlar = [
        python_surumu(),
        bagimliliklar(kur=kur_bagimlilik),
        calisma_alani(workspace, kur=True),
        docker(),
        searxng(settings, kur=kur_searxng),
        tarayici(settings),
        model_ucu(settings),
        gomme_modeli(settings, indir=indir_model),
    ]
    return adimlar


def searxng_secildi(workspace: Path) -> bool:
    """SearXNG kurulduysa yapilandirmayi ona cevirir.

    Kurmak ama kullanmamak, kullanicinin fark etmeyecegi bir bosluk olurdu
    -- ve tam olarak oldu. OLCULDU (kullanicinin `demo` calisma alani):
    konteyner iki saattir ayaktaydi, `deerx.toml` icinde
    `search_provider` SATIRI HIC YOKTU, bu fonksiyonun `replace`i
    eslesmedi ve sessizce `False` dondu. Arama tarayiciyla Bing'e gitti,
    Bing engelledi, arastirma ajani URL bulamayip TAHMIN etti: bir kosuda
    dokuz HTTP 404 ve dort cozulemeyen alan adi, on dort tur.

    Satir yoksa artik EKLENIR -- ama `[deerx]` tablosunun icine; sona
    eklemek TOML'da onu son alt tabloya (`[deerx.shell]`) sokar ve yine
    hicbir sey yapmaz.

    Yalnizca `browser`dan gecilir. `brave`, `tavily`, `google` ya da
    `duckduckgo` yazan biri SECIM yapmistir; ustune yazmak, ayari
    kullanicinin degil bizim belirledigimiz anlamina gelirdi.

    Doner: dosya degistiyse True. Cagiran taraf bunu kullaniciya soyler.
    """
    yol = workspace / CONFIG_FILENAME
    if not yol.is_file():
        return False
    metin = yol.read_text(encoding="utf-8")

    mevcut = re.search(
        r'^\s*search_provider\s*=\s*"([a-z]+)"', metin, flags=re.MULTILINE
    )
    if mevcut is not None:
        if mevcut.group(1) != "browser":
            return False               # searxng zaten, ya da bilincli baska secim
        yeni = metin[:mevcut.start()] + 'search_provider = "searxng"' + metin[mevcut.end():]
    else:
        yeni = _koke_ekle(metin, 'search_provider = "searxng"')

    yol.write_text(yeni, encoding="utf-8", newline="\n")
    return True


def ozet(adimlar: list[Adim]) -> dict[str, int]:
    sayim = {"ok": 0, "kuruldu": 0, "uyari": 0, "eksik": 0}
    for adim in adimlar:
        sayim[adim.durum] += 1
    return sayim


__all__ = ["Adim", "kur", "ozet", "searxng_secildi"]
