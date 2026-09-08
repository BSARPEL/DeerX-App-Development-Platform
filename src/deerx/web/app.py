"""DeerX web arayuzu — Starlette uzerinde JSON API + SSE canli akis.

FastAPI yerine dogrudan Starlette kullanilir: `starlette`, `uvicorn`,
`sse-starlette` ve `markdown-it-py` zaten bagimlilik agacinda; on bes rotalik
bir API icin ek bir katman tasimaya deger degil.

Guvenlik notu: bu sunucu dosya yazabilir ve kabuk komutu calistirabilir.
Varsayilan olarak yalnizca 127.0.0.1 dinlenir. Disari acmak icin acik bir
`--host` degeri gerekir ve bu durumda uyari basilir.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from markdown_it import MarkdownIt
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import (
    CONFIG_FILENAME,
    DEFAULT_PORT,
    Settings,
    browse_host,
    load_settings,
    platform_home,
    read_toml_table,
    save_settings,
)
from ..errors import ConfigError, DeerXError
from ..i18n import set_language, t
from ..logging import EventLog, get_logger
from ..pipeline import Orchestrator, Phase, Status
from ..rag.loaders import SUPPORTED_SUFFIXES
from .auth import (
    AUDIT_KEEP,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    AuthError,
    AuthStore,
    User,
    migrate_from_project,
)
from .projects import Project, ProjectError, ProjectStore, role_at_least
from .runner import (
    RunBusy,
    RunManager,
    phase_catalog,
    phase_range,
    phase_selection,
    retry_plan,
    run_detail,
    run_steps,
)

log = get_logger("web")

STATIC_DIR = Path(__file__).parent / "static"

# Cikti markdown'i model uretimidir; `html=False` ham HTML enjeksiyonunu keser.
_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": False})
    .enable("table")
    .enable("strikethrough")
)


def _render_fence(self: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
    """Kod bloklarini isler; mermaid bloklarini ayri bir kapsayiciya alir.

    `add_render_rule` fonksiyonu renderer'a bagli bir metoda cevirir; bu yuzden
    ilk parametre renderer ornegidir.
    """
    token = tokens[idx]
    info = (token.info or "").strip().split()
    lang = info[0].lower() if info else ""
    from html import escape

    body = escape(token.content)
    if lang == "mermaid":
        # Mermaid'i cizmek icin harici kutuphane gerekir; kaynagi okunakli goster.
        return (
            '<figure class="diagram">'
            '<figcaption>mermaid diyagrami</figcaption>'
            f"<pre><code>{body}</code></pre>"
            "</figure>\n"
        )
    cls = f' class="language-{escape(lang)}"' if lang else ""
    return f"<pre><code{cls}>{body}</code></pre>\n"


_md.add_render_rule("fence", _render_fence)


def render_markdown(text: str) -> str:
    return _md.render(text)


# ---------------------------------------------------------------------- #
# Yardimcilar
# ---------------------------------------------------------------------- #
def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        if not raw:
            return {}
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise DeerXError(t("api.bad_json")) from None
    except UnicodeDecodeError:
        # OLCULDU: latin-1 kodlanmis bir govde (ornegin yanlis
        # ayarlanmis bir istemci "modülü" gonderdiginde) 500 veriyordu.
        # Bozuk bir istek sunucunun hatasi degil; 400 dogru cevap ve
        # gorunen sey bir yigin izi degil, bir cumle olmali.
        raise DeerXError(t("api.bad_encoding")) from None
    if not isinstance(parsed, dict):
        raise DeerXError(t("api.body_not_object"))
    return parsed


async def event_publisher(
    runner: RunManager,
    cursor: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    poll_seconds: float = 0.25,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[dict[str, str]]:
    """Olay tamponunu SSE olaylarina cevirir.

    Rotanin icine gomulu bir kapanis yerine ayri bir uretec: dongunun kendisi
    (imlec ilerlemesi, nabiz, kopma tespiti) boylece dogrudan sinanabilir.
    """
    last_activity = time.monotonic()
    while True:
        if await is_disconnected():
            return
        fresh, cursor = runner.events_since(cursor)
        for event in fresh:
            yield {"event": "deerx", "data": json.dumps(event, default=str)}
        if fresh:
            last_activity = time.monotonic()
        elif time.monotonic() - last_activity > heartbeat_seconds:
            # Vekil sunucularin bagli akisi kesmemesi icin nabiz.
            last_activity = time.monotonic()
            yield {"event": "ping", "data": json.dumps({"seq": cursor})}
        await asyncio.sleep(poll_seconds)


class NoCacheStatics(StaticFiles):
    """Statik dosyalari her istekte yeniden dogrulatir.

    DeerX yerel bir araç: surum yukseltildiginde tarayicinin onbellekteki eski
    `app.js`/`styles.css` dosyasini servis etmesi, arayuzun API ile uyumsuz
    kalmasina yol acar. Yerel dosya servisinde onbellek kazanci ihmal edilebilir.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# Kapanista kosunun kendiliginden bitmesi icin taninan sure. Adimlar
# isbirlikci durur: devam eden model cagrisi tamamlanir, sonra durulur.
SHUTDOWN_GRACE = 20.0


# Istek boyunca hangi projenin konusuldugu. `ContextVar` secildi cunku
# async gorevlere gore yalitilmis: es zamanli iki istek birbirinin
# projesini gormez. Arka plandaki kosu is parcacigi bunu HIC okumaz --
# kendi orkestratorunu nesne olarak tutuyor.
_AKTIF_PROJE: ContextVar[int] = ContextVar("deerx_aktif_proje", default=0)

# Ayni anda acik tutulan proje sayisi. Her acik proje en az iki SQLite
# baglantisi ve olasi bir tarayici oturumu tutuyor; sinirsiz birakmak
# uzun omurlu bir sunucuda dosya tanimlayicisi biriktirir.
MAX_OPEN_PROJECTS = 8

# Hangi projede calisildigi tarayicida durur. Oturum tablosuna sutun
# eklemek yerine cerez secildi: secim kullaniciya degil SEKMEYE ait bir
# tercih ve sunucuda tutulursa iki pencerede iki proje acamazsin.
# Guvenlik acisindan bedeli yok -- uyelik zaten her istekte dogrulaniyor,
# cerez yalnizca bir tercih tasiyor.
PROJECT_COOKIE = "deerx_project"

# Istegin hangi projeye ait oldugunu tasiyan baslik. Cerezden ONCE
# okunur: hash sekmeye aittir ve iki pencerede iki proje ancak boyle
# acilabilir.
PROJECT_HEADER = "X-DeerX-Project"


class ProjectRuntime:
    """Tek bir projenin calisma zamani.

    Ayarlar, olay gunlugu, orkestrator ve kosu yoneticisi PROJE
    BASINADIR: bunlari paylasmak, bir projedeki kosunun otekinin olay
    akisina dusmesi ve `RunBusy`nin butun platformu kilitlemesi demekti.
    """

    def __init__(self, project: Project, settings: Settings) -> None:
        self.project = project
        self.settings = settings
        # Port dilimi PROJENIN kaydindan gelir, ayar dosyasindan degil:
        # iki proje ayni araligi yayinlamaya calisirsa ikincisinin
        # konteyneri hic kurulamaz. Dilim proje olusturulurken tahsis
        # edilir ve proje silinene kadar degismez.
        if project.port_base:
            settings.sandbox_port_base = project.port_base
            settings.sandbox_port_count = project.port_count or 10
        settings.ensure_dirs()
        self.events = EventLog(settings.events_path, echo=True)
        self.orchestrator = Orchestrator(settings, events=self.events, stream=False)
        self.runner = RunManager(settings, self.orchestrator)
        self.son_kullanim = time.time()

    @property
    def busy(self) -> bool:
        return self.runner.is_running

    def close(self) -> None:
        if self.runner.is_running:
            self.runner.stop()
            self.runner.wait(SHUTDOWN_GRACE)
        self.orchestrator.close()


class AppState:
    """Sunucu omru boyunca paylasilan kaynaklar.

    Hesaplar ve proje kaydi PLATFORM kapsamlidir ve burada durur; kosu
    kaynaklari (ayarlar, olay gunlugu, orkestrator, kosu yoneticisi)
    PROJE kapsamlidir ve `ProjectRuntime` icinde yasar. Hangi projenin
    konusuldugu istek basina `_AKTIF_PROJE` baglaminda tasinir, boylece
    `state.orchestrator` gibi kullanim yerleri degismeden dogru projeye
    bakar.
    """

    def __init__(self, settings: Settings) -> None:
        settings.ensure_dirs()
        self.boot_settings = settings
        self._runtimes: OrderedDict[int, ProjectRuntime] = OrderedDict()
        # Hesaplar PROJELERIN USTUNDE durur. Proje veritabaninin icinde
        # tutulduklarinda ayni kisi her projede ayri bir hesap, ayri bir
        # parola ve bolunmus bir gecmis demekti; oturum cerezi de bir
        # projeden otekine tasinmiyordu.
        settings.platform_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth = AuthStore(settings.platform_db_path)
        # Bir kereye mahsus: eski kurulumlar hesaplarini proje dosyasinda
        # tasiyor. Tasima acik oturumlari korur, yani kullanici hicbir
        # fark gormez.
        migrate_from_project(settings.db_path, self.auth)
        self.auth.purge_expired()

        # Proje kaydi hesaplarla ayni dosyada: "kim hangi projede ne
        # yapabilir" sorusu ikisini birden okumadan cevaplanamaz.
        self.projects = ProjectStore(settings.platform_db_path)
        self.default_project = self._sunulan_projeyi_kaydet()
        # Acilista sunulan proje hazir olsun: ilk istek bir veritabani
        # acilisini beklemesin.
        self._runtimes[self.default_project.id] = ProjectRuntime(
            self.default_project, settings
        )

    # ------------------------------------------------------------------ #
    # Calisma zamani kayit defteri
    # ------------------------------------------------------------------ #
    @property
    def project(self) -> Project:
        """Bu istegin konustugu proje."""
        pid = _AKTIF_PROJE.get() or self.default_project.id
        proje = self.projects.get(pid)
        return proje or self.default_project

    def runtime(self, project: Project | None = None) -> ProjectRuntime:
        """Projenin calisma zamani; yoksa acar.

        Acik proje sayisi sinira ulasinca EN ESKI BOSTAKI kapatilir.
        Kosan bir proje asla kapatilmaz: kapatmak, birinin suren isini
        yarida kesmek olurdu.
        """
        proje = project or self.project
        mevcut = self._runtimes.get(proje.id)
        if mevcut is not None:
            mevcut.son_kullanim = time.time()
            self._runtimes.move_to_end(proje.id)
            return mevcut

        while len(self._runtimes) >= MAX_OPEN_PROJECTS:
            adaylar = [pid for pid, rt in self._runtimes.items() if not rt.busy]
            if not adaylar:
                break
            eski = adaylar[0]
            self._runtimes.pop(eski).close()

        ayar = load_settings(workspace=proje.path)
        yeni = ProjectRuntime(proje, ayar)
        self._runtimes[proje.id] = yeni
        return yeni

    @property
    def settings(self) -> Settings:
        return self.runtime().settings

    @property
    def orchestrator(self) -> Orchestrator:
        return self.runtime().orchestrator

    @property
    def runner(self) -> RunManager:
        return self.runtime().runner

    @property
    def events(self) -> EventLog:
        return self.runtime().events

    def _sunulan_projeyi_kaydet(self) -> Project:
        """`serve --workspace X` ile acilan dizini proje olarak kaydeder.

        Var olan HER kullanici bu projeye uye yazilir: hesap rolu `admin`
        olanlar `owner`, otekiler `developer`. Boylece bugun calisan
        hicbir yetki DARALMIYOR -- daralma yalnizca platform kapsamli
        ayarlarda, ve o zaten ayri bir asamada kapatildi.
        """
        varolan = self.projects.by_path(self.boot_settings.workspace)
        if varolan is not None:
            return varolan

        uyeler = [
            (u.id, "owner" if u.is_admin else "developer")
            for u in self.auth.list_users()
        ]
        sahip = next((uid for uid, rol in uyeler if rol == "owner"), None)
        return self.projects.create(
            self.boot_settings.workspace, owner_id=sahip, members=uyeler,
            port_base=self.boot_settings.sandbox_port_base,
            port_count=self.boot_settings.sandbox_port_count,
        )

    def close(self) -> None:
        # Once kosulari durdur ve bitmelerini bekle. Veritabani, arka
        # plandaki is parcacigi hala yazarken kapatilirsa SQLite serbest
        # birakilmis bir baglantiya dokunur ve surec erisim ihlaliyle
        # coker -- kapanis sirasinda gorulen tam olarak buydu.
        for calisan in list(self._runtimes.values()):
            if not calisan.runner.is_running:
                continue
            calisan.runner.stop()
            calisan.runner.wait(SHUTDOWN_GRACE)
            if calisan.runner.is_running:
                # Adim bir model cagrisinda asili kalmis olabilir.
                # Baglantiyi kapatmiyoruz: coken bir surec yerine sizan
                # bir thread daha iyidir, surec zaten sonlaniyor.
                log.warning(t("api.run_not_stopping", seconds=SHUTDOWN_GRACE))
                return
        for calisan in list(self._runtimes.values()):
            calisan.orchestrator.close()
        self._runtimes.clear()
        self.auth.close()
        self.projects.close()


# ---------------------------------------------------------------------- #
# Ayar alanlari
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class SettingField:
    """Arayuzden degistirilebilen tek bir ayar.

    Tablo tabanli: yeni bir ayar eklemek tek satir, dogrulama tek yerde.
    `secret` isaretli alanlar yalnizca yazilir — degerleri arayuze hicbir
    zaman donmez, yalnizca tanimli olup olmadiklari.
    """

    parse: Callable[[Any], Any]
    secret: bool = False
    # Kimin yazabilecegi. `platform` alanlari butun kullanicilari ve konak
    # makineyi etkiler: modelin ucu, kimlik bilgileri, yalitim, dis erisim.
    # `proje` alanlari yalnizca bu calisma alaninin kosusunu etkiler.
    # `hesap` alanlari yalnizca yazani etkiler.
    scope: str = "proje"


def _text(value: Any) -> str:
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    """Bos dize "temizle" demektir; None saklanir."""
    return _text(value) or None


def _choice(*allowed: str) -> Callable[[Any], str]:
    def parse(value: Any) -> str:
        text = _text(value)
        if text not in allowed:
            # Mesaj `_error()` ile aynen kullaniciya doner.
            raise ValueError(
                t("api.invalid_choice", value=text, allowed=", ".join(allowed))
            )
        return text

    return parse


def _bounded_int(low: int, high: int) -> Callable[[Any], int]:
    def parse(value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("tam sayi olmali") from None
        return max(low, min(number, high))

    return parse


def _bounded_float(low: float, high: float) -> Callable[[Any], float]:
    def parse(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("sayisal olmali") from None
        return max(low, min(number, high))

    return parse


def _optional_float(low: float, high: float) -> Callable[[Any], float | None]:
    def parse(value: Any) -> float | None:
        if value in (None, ""):
            return None  # sunucunun kendi varsayilanini kullan
        return _bounded_float(low, high)(value)

    return parse


def _required_text(value: Any) -> str:
    text = _text(value)
    if not text:
        raise ValueError("bos birakilamaz")
    return text


_EFFORT = _choice("low", "medium", "high", "max")

SETTING_FIELDS: dict[str, SettingField] = {
    # Saglayici ve kimlik
    "provider": SettingField(_choice("openai", "anthropic"), scope="platform"),
    "openai_base_url": SettingField(_optional_text, scope="platform"),
    "openai_api_key": SettingField(_optional_text, secret=True, scope="platform"),
    "anthropic_api_key": SettingField(_optional_text, secret=True, scope="platform"),
    # Modeller
    "model_lead": SettingField(_required_text),
    "model_worker": SettingField(_required_text),
    "model_fast": SettingField(_required_text),
    "effort_lead": SettingField(_EFFORT),
    "effort_worker": SettingField(_EFFORT),
    "effort_fast": SettingField(_EFFORT),
    "temperature": SettingField(_optional_float(0.0, 2.0)),
    "max_tokens": SettingField(_bounded_int(256, 1_000_000)),
    "request_timeout_seconds": SettingField(_bounded_int(10, 7200)),
    "thinking_display": SettingField(_choice("summarized", "omitted")),
    # Dongu sinirlari
    "max_iterations": SettingField(_bounded_int(1, 200)),
    "max_tool_output_chars": SettingField(_bounded_int(1_000, 1_000_000)),
    "max_turn_output_chars": SettingField(_bounded_int(2_000, 4_000_000)),
    "cost_limit_usd": SettingField(_bounded_float(0.0, 10_000.0)),
    # Davranis
    "approval_mode": SettingField(_choice("auto", "ask", "dry-run")),
    # Yalitim. README'nin uc ayirt edici ozelliginden biri, ama ayarlar
    # ekraninda hic yoktu: acmanin tek yolu `deerx.toml` dosyasini elle
    # duzenlemekti.
    "execution": SettingField(_choice("host", "docker"), scope="platform"),
    "sandbox_image": SettingField(_required_text, scope="platform"),
    "sandbox_setup": SettingField(_text, scope="platform"),
    "sandbox_port_base": SettingField(_bounded_int(1024, 65_000), scope="platform"),
    "sandbox_port_count": SettingField(_bounded_int(1, 100), scope="platform"),
    "sandbox_memory": SettingField(_required_text, scope="platform"),
    "sandbox_cpus": SettingField(_bounded_float(0.1, 256.0), scope="platform"),
    "sandbox_pids": SettingField(_bounded_int(16, 100_000), scope="platform"),
    "language": SettingField(_choice("tr", "en"), scope="hesap"),
    "enable_web": SettingField(lambda v: bool(v), scope="platform"),
    "search_provider": SettingField(
        _choice("browser", "duckduckgo", "brave", "tavily", "searxng", "google")
    , scope="platform"),
    "searxng_url": SettingField(_optional_text, scope="platform"),
    # Google'in arama motoru kimligi bir sir degil, bir tanimlayici: gizli
    # isaretlenirse arayuz degerini geri gostermez ve kullanici ne
    # yazdigini goremez.
    "google_cse_id": SettingField(_optional_text, scope="platform"),
    "search_api_key": SettingField(_optional_text, secret=True, scope="platform"),
    # Tarayici
    "browser_channel": SettingField(_choice("auto", "chrome", "edge", "chromium"), scope="platform"),
    "browser_headless": SettingField(lambda v: bool(v), scope="platform"),
    "browser_idle_seconds": SettingField(_bounded_int(0, 86_400), scope="platform"),
    "browser_allow_preview": SettingField(lambda v: bool(v), scope="platform"),
    "log_level": SettingField(_choice("DEBUG", "INFO", "WARNING", "ERROR"), scope="platform"),
}

# Bunlar degisince LLM istemcisi yeniden kurulmali; istemci bu degerleri
# kurulumda okur ve sonradan bakmaz.
MODEL_FIELDS = {
    "provider", "openai_base_url", "openai_api_key", "anthropic_api_key",
    "model_lead", "model_worker", "model_fast",
    "temperature", "max_tokens", "request_timeout_seconds",
}

# Bunlar degisince kabin yeniden kurulmali; Docker portlari ve kaynak
# sinirlarini konteyner YARATILIRKEN ayirir, sonradan degistirilemez.
SANDBOX_FIELDS = {
    "execution", "sandbox_image", "sandbox_setup",
    "sandbox_port_base", "sandbox_port_count",
    "sandbox_memory", "sandbox_cpus", "sandbox_pids",
}


def settings_snapshot(
    settings: Settings, hesap: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Arayuze gonderilen ayar goruntusu. Sirlar deger olarak DONMEZ.

    `hesap`, ISTEGI YAPAN kullanicinin kendi tercihleri. Kapsami "hesap"
    olan alanlar ortak `Settings` nesnesinden degil buradan okunur:
    o nesne sunucuda TEK ve paylasilan, dolayisiyla oradan okumak "en son
    kim kaydettiyse onun dili" demek olurdu.
    """
    view: dict[str, Any] = {}
    hesap = hesap or {}
    for name, spec in SETTING_FIELDS.items():
        if spec.secret:
            view[f"has_{name}"] = bool(getattr(settings, name))
        elif spec.scope == "hesap" and name in hesap:
            view[name] = hesap[name]
        else:
            view[name] = getattr(settings, name)
    view.update(
        {
            # Arayuz yazamayacagi alani kilitli cizsin diye. Liste
            # SUNUCUDAN gelir: alan tablosu tek gercek kaynak, kopyalanan
            # bir liste sessizce ayrilir.
            "platform_fields": sorted(
                ad for ad, spec in SETTING_FIELDS.items() if spec.scope == "platform"
            ),
            # Proje alanlari da kilitlenebilir: bir izleyici onlari GORUR
            # ama yazamaz. Yalnizca platform listesi gonderilirken arayuz
            # onlari acik cizip 403 yiyordu.
            "project_fields": sorted(
                ad for ad, spec in SETTING_FIELDS.items() if spec.scope == "proje"
            ),
            # Alanin HANGI kapsamda oldugu da gidiyor: arayuz bugun bir
            # alanin proje mi hesap mi oldugunu ayirt edemiyor ve her
            # alani ya normal ya kilitli ciziyor. Tek kaynak alan
            # tablosu -- elle kopyalanan bir liste ondan sessizce ayrilir.
            "field_scopes": {ad: spec.scope for ad, spec in SETTING_FIELDS.items()},
            "workspace": str(settings.workspace),
            "has_api_key": settings.llm_ready,
            "llm_hint": settings.llm_hint,
            "embedding_model": settings.rag.embedding_model,
        }
    )
    return view


# ---------------------------------------------------------------------- #
# Rotalar
# ---------------------------------------------------------------------- #
def _resolve_id(raw: str, by_id, by_seq) -> str | None:
    """Yol parcasini gercek kimlige cevirir.

    Adres hem kimligi hem `#3` gibi sira numarasini kabul ediyor. Once
    KIMLIGE bakilir, sonra numaraya: kimlikler onaltilik ve on iki
    karakterlik oldugu icin binde uc-dordu tamamen rakamdan olusuyor
    (or. `387341249535`). Once numaraya bakan bir kontrol boyle bir kosuyu
    var olmayan bir sira numarasi sanip 404 doner -- kayit yerinde
    durdugu halde kullanici ona hicbir zaman ulasamaz.
    """
    # `#3` acikca "sira numarasi" demektir; kimlige hic bakilmaz. Isaretsiz
    # gelen deger once KIMLIK sayilir, bulunamazsa numara olarak denenir.
    if raw.startswith("#"):
        found = by_seq(int(raw[1:])) if raw[1:].isdigit() else None
        return str(found["id"]) if found else None
    if by_id(raw) is not None:
        return raw
    if raw.isdigit():
        found = by_seq(int(raw))
        if found is not None:
            return str(found["id"])
    return None


def build_app(settings: Settings) -> Starlette:
    state = AppState(settings)

    # ---------------------------------------------------------------- #
    # Genel bakis
    # ---------------------------------------------------------------- #
    async def overview(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        orch = state.orchestrator
        counts = orch.state.counts()
        phases = phase_catalog(orch.state)
        total_cost = sum(p["cost"] for p in phases)

        # Ust ray IS AKISI BAZLI. Proje geneli faz durumu, birden fazla is
        # akisi yasadiginda yaniltiyordu: birinin bitirdigi faz otekinde
        # hic kosulmamis olabilir ve ray ikisini tek satirda topluyordu.
        from .runner import workflow_step_load

        akislar = orch.state.list_workflows(limit=1)
        aktif = akislar[0] if akislar else None
        adimlar = workflow_step_load(orch.state, aktif["id"]) if aktif else []
        return _json(
            {
                "workspace": str(state.settings.workspace),
                # Aktif proje ve istegi yapanin ORADAKI rolu. Arayuz
                # yapamayacagi eylemi kilitli cizsin diye rol de gidiyor:
                # bir izleyiciye "Baslat" dugmesini gosterip sonra 403
                # dondurmek, dugmeyi hic gostermemekten kotudur.
                "project": {**state.project.to_dict(), "role": _project_role(request)},
                "goal": orch.state.get_meta("goal", ""),
                "brief": orch.state.get_meta("brief", ""),
                "phases": phases,
                # Ust rayin verisi: is akisina kapsanmis, adim basina
                # bekleyen is sayisiyla.
                "workflow": aktif,
                "workflow_steps": adimlar,
                "counts": counts,
                "knowledge_base": orch.kb.stats(),
                "run": state.runner.status(),
                "blocking_questions": [
                    asdict(q) for q in orch.state.open_blocking_questions()
                ],
                "total_cost": round(total_cost, 4),
                "settings": settings_snapshot(settings, _hesap_ayarlari(request)),
            }
        )

    async def update_settings(request: Request) -> Response:
        """Ayarlari bu oturum icin gunceller.

        Alanlar tablodan surulur: yeni bir ayar eklemek tek satir. Gizli
        degerler (API anahtarlari) yalnizca yazilir, hicbir zaman geri
        donmez.
        """
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        # Model degistirmek suren bir kosunun ortasinda anlamsizdir: ilk
        # yarisi bir modelle, ikinci yarisi baskasiyla kosulmus olur.
        if state.runner.is_running and (set(body) & MODEL_FIELDS):
            return _error(t("api.models_locked"), 409)

        # Yalitimi kosunun ortasinda degistirmek daha da kotu: kabini
        # yeniden kurmak calisan konteyneri siler ve ajanin baslattigi
        # servisler ayaginin altindan cekilir.
        if state.runner.is_running and (set(body) & SANDBOX_FIELDS):
            return _error(t("api.sandbox_locked"), 409)

        # ONCE dogrula, SONRA yaz. Dongu icinde `setattr` cagirmak, bir
        # alan reddedildiginde ondan oncekileri islenmis birakiyordu:
        # kullanici reddedildigini gorur ve hicbir seyin degismedigini
        # sanir. Iki gecis, istegi ya tumuyle uygular ya hic uygulamaz.
        yonetici = _is_admin(request)
        temiz: list[tuple[str, Any, SettingField]] = []
        for name, value in body.items():
            spec = SETTING_FIELDS.get(name)
            if spec is None:
                return _error(t("api.unknown_setting", name=name))
            if spec.scope == "platform" and not yonetici:
                return _error(t("api.setting_admin_only", name=name), 403)
            # Proje ayari da yazma yoludur. Bir izleyici `/api/run`dan
            # 403 aliyor ama onay modunu "auto"ya cekip baskasinin
            # kosusunun konak makinede dosya yazmasini saglayabiliyordu:
            # yazma yolu ucun ADINDA degil, ETKISINDE.
            if spec.scope == "proje":
                denied = _require_role(request, "developer")
                if denied is not None:
                    return denied
            try:
                cleaned = spec.parse(value)
            except (TypeError, ValueError) as exc:
                return _error(f"{name}: {exc}")
            temiz.append((name, cleaned, spec))

        changed: dict[str, Any] = {}
        kisisel = state.auth.is_configured and getattr(request.state, "user", None)
        for name, cleaned, spec in temiz:
            # HESAP ayari ortak nesneye YAZILMAZ. `Settings` sunucuda tek
            # ve paylasilan: oraya yazmak, tercihi hic belirtmemis herkesin
            # ekranini son kaydedenin diline cevirirdi. Ortak deger
            # sunucunun acilistaki degeri olarak kalir ve tam da bunun
            # icin dogru bir yedek olur: "ev varsayilani".
            if not (spec.scope == "hesap" and kisisel):
                setattr(settings, name, cleaned)
                if name == "language":
                    # Atama dogrulayiciyi calistirmaz; Python tarafinin mesaj
                    # katalogunu burada da guncelliyoruz ki arayuz Ingilizceye
                    # gecerken olay akisi Turkce kalmasin.
                    #
                    # KISISEL bir kayitta calismaz: bu katalog SUNUCU
                    # GENELI ve olay akisi projedeki herkese gidiyor.
                    # Kendi dilini secen kisi, baskasinin akisini
                    # cevirmemeli.
                    set_language(str(cleaned))
            changed[name] = t("record.defined") if spec.secret and cleaned else (
                t("record.cleared") if spec.secret else cleaned
            )

        # Saglayici/uc/anahtar/model degistiyse istemci yeniden kurulmali.
        if set(changed) & MODEL_FIELDS:
            state.orchestrator.reset_client()

        if set(changed) & SANDBOX_FIELDS:
            state.orchestrator.reset_sandbox()

        _kalici_yaz(temiz, request)

        if changed:
            # Olay akisina Python sozlugunun `repr`i dusuyordu:
            # "updated: {'language': 'en'}". Akis kullaniciya gosterilen bir
            # yer; kesme isaretleri ve suslu parantezler oraya ait degil.
            visible = [
                f"{k} = {v}" for k, v in changed.items()
                if not SETTING_FIELDS[k].secret
            ]
            gizli = [k for k in changed if SETTING_FIELDS[k].secret]
            state.runner.emit(
                "tool", t("actor.settings"),
                t("api.settings_updated", changed=", ".join(visible + gizli)),
            )
            # Gunluge yalnizca ALAN ADLARI gider. Degerler arasinda API
            # anahtarlari var ve bir denetim gunlugu, sizdirdigi anda
            # korudugu seyin karsisina gecer.
            _audit(request, "settings.change", detail=", ".join(sorted(changed)))
        return _json({"ok": True, "changed": changed})

    async def test_llm(request: Request) -> Response:
        """Model ucuna gercek bir cagri yapar.

        Ayari kaydedip kirk dakikalik bir kosu baslattiktan sonra "model
        adi yanlismis" demekle bunun arasindaki fark, bu dugme.
        """
        if not state.settings.llm_ready:
            return _json({"ok": False, "error": state.settings.llm_hint})

        def probe() -> dict[str, Any]:
            import time as _time

            from ..llm import build_client

            started = _time.time()
            try:
                client = build_client(settings, events=state.events)
                out = client.complete(
                    role="fast",
                    system="Kisa cevap ver.",
                    messages=[{"role": "user", "content": "Sadece OK yaz."}],
                    tools=[],
                )
            except DeerXError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - saglayici her seyi firlatabilir
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            return {
                "ok": True,
                "provider": state.settings.provider,
                "model": state.settings.model_for("fast"),
                "seconds": round(_time.time() - started, 1),
                "text": (out.text or "").strip()[:200],
                "tokens": f"{out.usage.input_tokens} -> {out.usage.output_tokens}",
            }

        return _json(await asyncio.to_thread(probe))

    async def provider_catalog(request: Request) -> Response:
        """Bilinen saglayicilar ve mevcut ayara en uyan secenek."""
        from ..llm.providers import NO_MODEL_LISTING, catalog, preset_for

        return _json({
            "providers": catalog(),
            "current": preset_for(state.settings.openai_base_url, state.settings.provider),
            "no_listing": sorted(NO_MODEL_LISTING),
        })

    async def list_models(request: Request) -> Response:
        """Ucun KENDI model listesini getirir.

        Model adlarini kodda tutmak yaniltici olurdu: saglayicilar onlari
        sik degistirir ve birkac ay eski bir liste, kullaniciya var olmayan
        bir modeli onerir. Dogru kaynak ucun kendisi.
        """
        def probe() -> dict[str, Any]:
            import httpx

            if state.settings.provider == "anthropic":
                base, key, header = (
                    "https://api.anthropic.com/v1",
                    state.settings.anthropic_api_key,
                    {"x-api-key": state.settings.anthropic_api_key or "",
                     "anthropic-version": "2023-06-01"},
                )
            else:
                base = (state.settings.openai_base_url or "").rstrip("/")
                key = state.settings.openai_api_key
                header = {"Authorization": f"Bearer {key}"} if key else {}
            if not base:
                return {"ok": False, "error": "Model ucu tanimli degil."}

            try:
                response = httpx.get(f"{base}/models", headers=header, timeout=20.0)
            except Exception as exc:  # noqa: BLE001 - ag hatalari cesitli
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}

            if response.status_code >= 400:
                hint = ""
                if response.status_code in (401, 403):
                    hint = " Anahtar eksik ya da gecersiz."
                elif response.status_code == 404:
                    hint = " Bu saglayici model listesi sunmuyor; adi elle yazin."
                return {
                    "ok": False,
                    "error": f"HTTP {response.status_code}.{hint}",
                }
            try:
                payload = response.json()
            except ValueError:
                return {"ok": False, "error": "Yanit JSON degil."}

            rows = payload.get("data") if isinstance(payload, dict) else payload
            names = []
            for row in rows or []:
                name = row.get("id") or row.get("name") if isinstance(row, dict) else None
                if name:
                    names.append(str(name))
            return {"ok": True, "models": sorted(set(names)), "base_url": base}

        return _json(await asyncio.to_thread(probe))

    async def test_browser(request: Request) -> Response:
        """Sunucudaki tarayiciyi gercekten acip bir sayfa yukler.

        "Chrome kurulu" demek yetmiyor: surucu eksik olabilir, profil
        yazilamayabilir, vekil port acamayabilir. Tek dogru cevap denemek.

        Kosunun paylasilan oturumu KULLANILMAZ, gecici bir oturum acilir:
        Playwright'in senkron nesneleri kendilerini olusturan is parcacigina
        baglidir ve bu istek baska bir is parcaciginda calisiyor.
        """
        def probe() -> dict[str, Any]:
            import tempfile
            import time as _time

            from ..browser import BrowserSession, UrlPolicy, find_browser

            found = find_browser(state.settings.browser_channel)
            base = {
                "binary": found.label if found else None,
                "kind": found.kind if found else None,
                "channel": state.settings.browser_channel,
            }
            if found is None:
                return {"ok": False, "error": "Sistemde tarayici bulunamadi.", **base}

            started = _time.perf_counter()
            session = BrowserSession(
                profile_dir=Path(tempfile.mkdtemp(prefix="deerx-test-")),
                policy=UrlPolicy(),
                channel=state.settings.browser_channel,
                headless=state.settings.browser_headless,
                idle_seconds=0,
            )
            try:
                page = session.goto("https://example.com/")
                title = page.title()
            except DeerXError as exc:
                return {"ok": False, "error": str(exc)[:400], **base}
            except Exception as exc:  # noqa: BLE001 - playwright kendi tiplerini kullanir
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400], **base}
            finally:
                session.close()
            return {
                "ok": True,
                "title": title,
                "seconds": round(_time.perf_counter() - started, 1),
                **base,
            }

        return _json(await asyncio.to_thread(probe))

    async def test_search(request: Request) -> Response:
        """Web aramasini gercekten deneyip sonucu doner.

        Ayarlari kaydedip kosuyu baslatmadan once calisip calismadigini
        gormek gerekir; anahtarsiz uc sessizce bos donuyordu.
        """
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        query = str(body.get("query") or "DeerX test sorgusu").strip()

        def run_probe() -> dict[str, Any]:
            from ..tools import ToolContext, build_registry

            probe = ToolContext(
                settings=settings, events=state.events,
                kb=state.orchestrator.kb, state=state.orchestrator.state,
            )
            outcome = build_registry().execute(
                "web_search", {"query": query, "max_results": 3}, probe
            )
            return {
                "ok": not outcome.is_error,
                "provider": state.settings.search_provider,
                "result": outcome.content[:1200],
            }

        return _json(await asyncio.to_thread(run_probe))

    # ---------------------------------------------------------------- #
    # Proje hafizasi
    # ---------------------------------------------------------------- #
    async def project_state(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        section = request.path_params["section"]
        data = state.orchestrator.state.to_dict()
        key = {"research": "research_notes"}.get(section, section)
        if section == "all":
            return _json(data)
        if key not in data:
            return _error(t("api.unknown_section", name=section), 404)
        payload = data[key]
        if key == "tasks":
            project = state.orchestrator.state
            wanted = request.query_params.get("plan")
            if wanted:
                payload = [t for t in payload if t.get("plan_id") == wanted]
            ready = {t.key for t in project.ready_tasks()}
            for task in payload:
                task["ready"] = task["key"] in ready
        return _json({"section": section, "items": payload})

    # ---------------------------------------------------------------- #
    # Planlar
    # ---------------------------------------------------------------- #
    async def plans(request: Request) -> Response:
        """Planlar ve hangisinin etkin oldugu."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        project = state.orchestrator.state
        # Once etkin plan: ilk cagride varsayilan plani olusturur ve plansiz
        # eski gorevleri ona devreder. Listeyi once okursak bos doner.
        active = project.active_plan_id()
        return _json({"plans": project.list_plans(), "active": active})

    async def create_plan(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        name = str(body.get("name", "")).strip()
        if not name:
            return _error(t("api.plan_needs_name"))
        plan = state.orchestrator.state.create_plan(
            name, description=str(body.get("description", ""))
        )
        state.runner.emit("tool", "plan", t("api.plan_created", name=name))
        return _json({"ok": True, "plan": plan})

    async def update_plan(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        project = state.orchestrator.state
        plan_id = request.path_params["plan_id"]

        if body.get("active"):
            if not project.set_active_plan(plan_id):
                return _error(t("api.no_such_plan", id=plan_id), 404)
            state.runner.emit("tool", "plan", t("api.plan_active", id=plan_id))

        status = body.get("status")
        if status is not None and status not in {"active", "done", "archived"}:
            return _error(t("api.bad_plan_status", status=status))
        updated = project.update_plan(
            plan_id, name=body.get("name"), status=status
        )
        if updated is None:
            return _error(t("api.no_such_plan", id=plan_id), 404)
        return _json({"ok": True, "plan": updated, "active": project.active_plan_id()})

    async def delete_plan(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        project = state.orchestrator.state
        plan_id = request.path_params["plan_id"]
        if project.get_plan(plan_id) is None:
            return _error(t("api.no_such_plan", id=plan_id), 404)
        if state.runner.is_running:
            return _error(t("api.plan_locked"), 409)
        # Son plani silmek gorevleri sahipsiz birakirdi.
        if len(project.list_plans()) <= 1:
            return _error(t("api.last_plan"), 400)
        removed = project.delete_plan(plan_id)
        state.runner.emit("warn", "plan", t("api.plan_deleted", count=removed))
        return _json({"ok": True, "removed_tasks": removed})


    # ---------------------------------------------------------------- #
    # Denetim gunlugu
    # ---------------------------------------------------------------- #
    def _audit(
        request: Request,
        action: str,
        *,
        detail: str = "",
        detail_key: str = "",
        detail_args: dict[str, Any] | None = None,
        username: str | None = None,
        actor: User | None = None,
        ok: bool = True,
    ) -> None:
        """Istegi yapan kisiyi ve yaptigi seyi gunluge yazar.

        `actor` girisin kendisi icindir: ara katman `request.state.user`i
        istek BASLARKEN cozer, giris ise o istegin icinde olur. Verilmezse
        oturumdaki kullanici yazilir.

        Adres ve tarayici istekten okunur. Bir vekilin arkasindayken
        `request.client` uvicorn tarafindan `X-Forwarded-For`dan duzeltilir
        ve bu baslik YALNIZCA `forwarded_allow_ips` icindeki adresten kabul
        edilir; basligi burada elle okumak, uzaktaki bir istemcinin kendi
        adresini uydurmasina izin vermek olurdu.
        """
        user = actor or getattr(request.state, "user", None)
        state.auth.record(
            action,
            user=user,
            username=username,
            detail=detail,
            detail_key=detail_key,
            detail_args=detail_args,
            ip=request.client.host if request.client else "",
            agent=request.headers.get("user-agent", ""),
            ok=ok,
        )

    # ---------------------------------------------------------------- #
    # Kimlik dogrulama
    # ---------------------------------------------------------------- #
    def _session_cookie(request: Request, response: Response, token: str) -> Response:
        """Oturum cerezini kurar; `Secure` bayragi ISTEGIN semasindan cikar.

        Eskiden karar `--host` degerine bakiyordu ve iki yonde de yanlisti:

        * TLS sonlandiran bir vekilin arkasinda DeerX 127.0.0.1'e baglanir,
          kural "loopback" derdi ve cerez `Secure` ISARETLENMEZDI -- oysa
          baglanti gercekte HTTPS'ti.
        * Duz HTTP ile aga acildiginda `Secure` isaretlenirdi; tarayici
          cerezi `http://` uzerinden ne kaydeder ne gonderir, yani dogru
          parolayla bile giris tamamlanmazdi. `--host` belgelenmis ama
          kullanilamaz bir secenekti.

        `request.url.scheme` ikisini de dogru cozer. Bir vekil arkasindayken
        semayi uvicorn `X-Forwarded-Proto`'dan duzeltir ve bu basligi
        YALNIZCA `forwarded_allow_ips` icindeki adresten kabul eder
        (varsayilan 127.0.0.1): ayni makinedeki vekil guvenilir, uzaktaki
        istemci sahteleyemez.

        Duz HTTP uzerinden oturum acik metin tasinir. Bu bilincli: karar
        kullanicinin, ve `serve` bu durumda uyari basar.
        """
        response.set_cookie(
            SESSION_COOKIE, token,
            httponly=True, samesite="lax", path="/",
            max_age=SESSION_MAX_AGE,
            secure=request.url.scheme == "https",
        )
        return response

    async def auth_status(request: Request) -> Response:
        """Kimin girdigini ve kurulumun gerekip gerekmedigini soyler."""
        user = getattr(request.state, "user", None)
        return _json(
            {
                "configured": state.auth.is_configured,
                "user": user.to_dict() if user else None,
                "required": state.auth.is_configured,
            }
        )

    async def auth_setup(request: Request) -> Response:
        """Ilk yoneticiyi olusturur; sunucunun konsolundaki jetonu ister."""
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        try:
            user = state.auth.create_first_admin(
                str(body.get("token", "")),
                str(body.get("username", "")),
                str(body.get("password", "")),
                str(body.get("display_name", "")),
            )
        except AuthError as exc:
            return _error(str(exc), 403)

        # Ilk yonetici, o ana kadar kaydedilmis projelerin sahibi olur.
        # Sunucu hesap acilmadan once basladiysa proje UYESIZ kaydedilir;
        # yonetici platform rolu sayesinde yine erisir ama uyelik satiri
        # olmadan uye listesi bos gorunur ve rolunu kimseye devredemez.
        for proje in state.projects.all_projects(include_archived=True):
            state.projects.set_member(proje.id, user.id, "owner")

        token = state.auth.open_session(user, request.headers.get("user-agent", ""))
        log.info("Kurulum tamamlandi; yonetici: %s", user.username)
        _audit(request, "setup", actor=user, detail=user.username)
        return _session_cookie(
            request,
            _json({"ok": True, "user": user.to_dict(), "warning": state.auth.last_warning}),
            token,
        )

    async def auth_login(request: Request) -> Response:
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        attempted = str(body.get("username", "")).strip().lower()
        try:
            user = state.auth.authenticate(attempted, str(body.get("password", "")))
        except AuthError as exc:
            # 401: kimlik dogrulanamadi. Mesaj kullanici adi ile parolayi
            # ayirt etmez -- hangisinin yanlis oldugunu soylemek kullanici
            # sayimina yarar. Gunluge ise DENENEN ad yazilir: yoneticinin
            # gormesi gereken tam olarak budur.
            _audit(request, "login.failed", username=attempted[:64], ok=False)
            return _error(str(exc), 401)

        token = state.auth.open_session(user, request.headers.get("user-agent", ""))
        _audit(request, "login", actor=user)
        return _session_cookie(request, _json({"ok": True, "user": user.to_dict()}), token)

    async def auth_logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            state.auth.close_session(token)
            # Cerezsiz gelen bir "cikis" istegi gunluge girmez: kaydi olan
            # her satir gercekten kapanan bir oturum olsun.
            _audit(request, "logout")
        response = _json({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    async def auth_sessions(request: Request) -> Response:
        """Kullanicinin ACIK oturumlari.

        `list_sessions` bugune kadar yalnizca testte cagriliyordu:
        kullanici "su an nerelerden girisim acik" diye soramiyordu -- ve
        bu, calinmis bir cerezi fark etmenin en dogrudan yolu.
        """
        user = getattr(request.state, "user", None)
        if user is None:
            return _error(t("api.login_required"), 401)
        simdiki = request.cookies.get(SESSION_COOKIE, "")
        satirlar = []
        for oturum in state.auth.list_sessions(user.id):
            satirlar.append({**oturum, "current": simdiki.startswith(oturum["id"])})
        return _json({"sessions": satirlar})

    async def auth_session_close(request: Request) -> Response:
        user = getattr(request.state, "user", None)
        if user is None:
            return _error(t("api.login_required"), 401)
        onek = str(request.path_params.get("session_id", ""))
        if not state.auth.close_session_by_prefix(user.id, onek):
            return _error(t("api.unknown_session"), 404)
        _audit(request, "session.close", detail=onek)
        return _json({"ok": True})

    async def auth_password(request: Request) -> Response:
        """Kullanici kendi parolasini degistirir; eskisini bilmesi gerekir."""
        user = request.state.user
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        try:
            state.auth.authenticate(user.username, str(body.get("current", "")))
        except AuthError:
            _audit(request, "password.failed", ok=False)
            return _error(t("api.wrong_current_password"), 403)
        try:
            warning = state.auth.set_password(user.id, str(body.get("password", "")))
        except AuthError as exc:
            return _error(str(exc))
        _audit(request, "password.change")
        # Parola degisince tum oturumlar dustu; bu tarayiciya yenisini ver.
        token = state.auth.open_session(user, request.headers.get("user-agent", ""))
        return _session_cookie(request, _json({"ok": True, "warning": warning}), token)

    # ---------------------------------------------------------------- #
    # Kullanici yonetimi (yalnizca yonetici)
    # ---------------------------------------------------------------- #
    def _sahipsizi_sahiplen(proje: Project, user: User) -> str:
        """SAHIPSIZ PROJEYI ACAN YONETICI SAHIPLENIR; rolunu doner.

        Proje, sunucu acilirken kaydediliyor. Hesaplar sonradan
        olusturulduysa -- web kurulumundan ya da `deerx user add --admin`
        ile -- kimse uye yazilmamis olur. Yonetici platform rolu
        sayesinde zaten erisir, ama uyelik satiri olmadan uye listesi BOS
        gorunur ve rolunu kimseye devredemez: bir projeyi baskasina
        acmanin yolu uye eklemek, ve eklemek icin once orada olmak
        gerekiyor.

        Islem idempotent ve yalnizca gercekten sahipsiz projede calisir.
        """
        rol = state.projects.role_of(proje.id, user.id)
        if rol:
            return rol
        if any(m["role"] == "owner" for m in state.projects.members(proje.id)):
            return ""
        state.projects.set_member(proje.id, user.id, "owner")
        return "owner"

    def _project_role(request: Request) -> str:
        """Istegi yapanin AKTIF projedeki rolu.

        Kimlik dogrulama hic kurulmamissa yerel kurulum tek kisiliktir
        ve o kisi sahiptir. Platform yoneticisi uyeligi olmasa da
        sahiptir: yoksa sahibi ayrilmis bir proje kimsenin ulasamadigi
        bir dizine donusurdu.
        """
        if not state.auth.is_configured:
            return "owner"
        user = getattr(request.state, "user", None)
        if user is None:
            return ""
        if user.is_admin:
            return _sahipsizi_sahiplen(state.project, user) or "owner"
        return state.projects.role_of(state.project.id, user.id)

    def _require_role(request: Request, needed: str) -> Response | None:
        """Proje islemleri icin en az `needed` rolu ister."""
        rol = _project_role(request)
        if not rol:
            return _error(t("project.not_member"), 403)
        if not role_at_least(rol, needed):
            return _error(t("project.needs_role", role=needed), 403)
        return None

    def _kalici_yaz(
        temiz: list[tuple[str, Any, SettingField]], request: Request
    ) -> None:
        """Ayari dosyaya yazar ki sunucu yeniden baslayinca kaybolmasin.

        OLCULDU: depoda toml YAZAN tek satir yoktu (`tomllib` salt okur).
        Ayarlar ekranindan yapilan her degisiklik yalnizca bellekte
        kaliyordu ve sunucu yeniden basladiginda sessizce eski degerine
        donuyordu -- kullanici modeli degistirip birakiyor, ertesi gun
        eski modelle kosuyordu.

        Kapsam dosyayi belirler: proje ayarlari `<proje>/deerx.toml`a,
        platform ayarlari `<DEERX_HOME>/platform.toml`a, hesap ayarlari
        `<DEERX_HOME>/users/<kimlik>.toml`a.

        SIRLAR YAZILMAZ. API anahtarlari `.env` ile ya da elle
        `deerx.toml` ile veriliyor; arayuzden girilen bir anahtari
        kendiliginden diske yazmak, kullanicinin bilmedigi bir yerde bir
        kopya birakmak olurdu. Arayuz zaten "bu oturum icin gecerli"
        diyor -- sirlar icin bu dogru kaliyor.
        """
        proje_alanlari: dict[str, Any] = {}
        platform_alanlari: dict[str, Any] = {}
        hesap_alanlari: dict[str, Any] = {}
        # UC kapsam, UC kova. Once ikisi vardi ("platform degilse proje")
        # ve "hesap" sessizce projeye dusuyordu.
        kovalar = {
            "platform": platform_alanlari,
            "hesap": hesap_alanlari,
            "proje": proje_alanlari,
        }
        for ad, deger, spec in temiz:
            if spec.secret:
                continue
            kovalar.get(spec.scope, proje_alanlari)[ad] = deger

        try:
            if proje_alanlari:
                save_settings(
                    state.settings.workspace / CONFIG_FILENAME, proje_alanlari
                )
            if platform_alanlari:
                save_settings(
                    platform_home() / "platform.toml", platform_alanlari
                )
            if hesap_alanlari:
                save_settings(_hesap_yolu(request), hesap_alanlari)
        except OSError as exc:  # pragma: no cover - disk hatasi
            # Yazma dustuyse ayar YINE DE bu oturumda gecerli; kullaniciya
            # "kaydedilemedi" demek ama degisikligi geri almak, iki kotu
            # secenegin daha kotusu olurdu.
            log.warning(t("api.settings_not_saved", error=exc))

    def _hesap_yolu(request: Request) -> Path:
        """Hesap kapsamli ayarin yazildigi dosya.

        Kimlik dogrulama hic kurulmamis yerel kurulumda KULLANICI YOK ve
        makinede tek kisi var: tercih platform dosyasina duser, cunku
        orada "yalnizca beni etkiler" zaten dogru.
        """
        user = getattr(request.state, "user", None)
        if not state.auth.is_configured or user is None:
            return platform_home() / "platform.toml"
        return platform_home() / "users" / f"{user.id}.toml"

    def _hesap_ayarlari(request: Request) -> dict[str, Any]:
        """Istegi yapan kullanicinin kendi tercihleri."""
        try:
            return read_toml_table(_hesap_yolu(request))
        except (OSError, ConfigError):  # pragma: no cover - bozuk dosya
            return {}

    def _is_admin(request: Request) -> bool:
        """Kimlik dogrulama hic kurulmamissa yerel kurulum tek kisiliktir
        ve o kisi her seyi yapabilir; kurulmussa rol karar verir."""
        if not state.auth.is_configured:
            return True
        user = getattr(request.state, "user", None)
        return bool(user and user.is_admin)

    def _require_admin(request: Request) -> Response | None:
        user = getattr(request.state, "user", None)
        if user is None or not user.is_admin:
            return _error(t("api.admin_only"), 403)
        return None

    # ---------------------------------------------------------------- #
    # Projeler
    # ---------------------------------------------------------------- #
    def _proje_veya_hata(request: Request) -> tuple[Project | None, Response | None]:
        try:
            pid = int(request.path_params["project_id"])
        except (KeyError, TypeError, ValueError):
            return None, _error(t("project.unknown", id="?"), 404)
        proje = state.projects.get(pid)
        if proje is None:
            return None, _error(t("project.unknown", id=pid), 404)
        return proje, None

    def _proje_yonetebilir(request: Request, proje: Project) -> bool:
        """Adi, arsivi ve uyeleri kim degistirebilir.

        Proje SAHIBI ya da platform yoneticisi. Gelistirici projede
        calisir ama kimin girecegine karar vermez.
        """
        if not state.auth.is_configured:
            return True
        user = getattr(request.state, "user", None)
        if user is None:
            return False
        if user.is_admin:
            _sahipsizi_sahiplen(proje, user)
            return True
        return state.projects.role_of(proje.id, user.id) == "owner"

    async def environment(request: Request) -> Response:
        """Bu projenin gelistirme ortami: kabin, portlar, servisler."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        calisan = state.runtime()
        ayar = calisan.settings
        proje = state.project

        kabin: dict[str, Any] = {
            "execution": ayar.execution,
            "image": ayar.sandbox_image,
            "memory": ayar.sandbox_memory,
            "cpus": ayar.sandbox_cpus,
            "status": "off",
            "name": "",
        }
        if ayar.execution == "docker":
            from ..sandbox import Sandbox

            olcek = Sandbox(
                workspace=ayar.workspace, image=ayar.sandbox_image,
                port_base=ayar.sandbox_port_base,
                port_count=ayar.sandbox_port_count,
            )
            kabin["name"] = olcek.name
            # `_durum` docker'a soruyor; docker yoksa None doner ve
            # "kurulu degil" demek dogru cevaptir.
            kabin["status"] = olcek._durum() or "absent"  # noqa: SLF001

        servisler = calisan.orchestrator.services.describe_all()
        return _json({
            "project": proje.to_dict(),
            "ports": {
                "base": ayar.sandbox_port_base,
                "count": ayar.sandbox_port_count,
                "last": ayar.sandbox_port_base + ayar.sandbox_port_count - 1,
            },
            "sandbox": kabin,
            "services": servisler,
        })

    async def environment_rebuild(request: Request) -> Response:
        """Konteyneri siler; bir sonraki komut onu bastan kurar.

        Kurulum betigi yalnizca konteyner ILK kuruldugunda kosuyor, yani
        `sandbox_setup` degistiginde ortamin yenilenmesi icin acik bir
        yol gerekiyor. Sahiplik istenir: ortami yeniden kurmak, o anda
        calisan servisleri de goturur.
        """
        denied = _require_role(request, "owner")
        if denied is not None:
            return denied
        calisan = state.runtime()
        if calisan.runner.is_running:
            return _error(t("api.run_busy"), 409)

        from ..sandbox import Sandbox

        ayar = calisan.settings
        Sandbox(
            workspace=ayar.workspace, image=ayar.sandbox_image,
            port_base=ayar.sandbox_port_base,
            port_count=ayar.sandbox_port_count,
        ).destroy()
        calisan.orchestrator.reset_sandbox()
        _audit(request, "env.rebuild", detail=state.project.name)
        return _json({"ok": True})

    async def projects_list(request: Request) -> Response:
        user = getattr(request.state, "user", None)
        arsiv = request.query_params.get("archived") == "1"
        if not state.auth.is_configured:
            projeler = state.projects.all_projects(include_archived=arsiv)
            projeler = [
                Project(
                    id=x.id, slug=x.slug, name=x.name, path=x.path,
                    owner_id=x.owner_id, created_at=x.created_at,
                    archived=x.archived, role="owner",
                )
                for x in projeler
            ]
        else:
            projeler = state.projects.for_user(
                user.id, is_admin=user.is_admin, include_archived=arsiv
            )
        return _json({
            "projects": [x.to_dict() for x in projeler],
            "active": state.project.to_dict(),
        })

    async def projects_create(request: Request) -> Response:
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        ham = str(body.get("path", "")).strip()
        if not ham:
            return _error(t("api.path_required"))
        try:
            # Proje dizini calisma alaninin ICINDE olmak zorunda DEGIL --
            # projeler kardestir, ic ice degil. Ama yine de mutlak bir
            # yola cozulur ki kayit belirsiz kalmasin.
            yol = Path(ham).expanduser().resolve()
        except OSError as exc:
            return _error(str(exc))

        user = getattr(request.state, "user", None)
        try:
            proje = state.projects.create(
                yol, name=str(body.get("name", "")).strip(),
                owner_id=user.id if user else None,
                port_base=state.boot_settings.sandbox_port_base,
                port_count=state.boot_settings.sandbox_port_count,
            )
        except ProjectError as exc:
            return _error(str(exc))

        _audit(request, "project.create", detail=proje.name)
        return _json({"ok": True, "project": proje.to_dict()})

    async def project_activate(request: Request) -> Response:
        """Bu tarayicinin uzerinde calistigi projeyi degistirir."""
        proje, hata = _proje_veya_hata(request)
        if hata is not None:
            return hata
        assert proje is not None
        if proje.archived:
            return _error(t("project.archived_switch"), 400)

        # Uyelik dogrulanir: cerezi elle yazmak yetmesin.
        if state.auth.is_configured:
            user = getattr(request.state, "user", None)
            if user is None:
                return _error(t("api.login_required"), 401)
            if not user.is_admin and not state.projects.role_of(proje.id, user.id):
                return _error(t("project.not_member"), 403)

        cevap = _json({"ok": True, "project": proje.to_dict()})
        cevap.set_cookie(
            PROJECT_COOKIE, str(proje.id),
            httponly=True, samesite="lax", path="/",
            secure=request.url.scheme == "https",
        )
        _audit(request, "project.activate", detail=proje.name)
        return cevap

    async def projects_update(request: Request) -> Response:
        proje, hata = _proje_veya_hata(request)
        if hata is not None:
            return hata
        assert proje is not None
        if not _proje_yonetebilir(request, proje):
            return _error(t("project.needs_role", role="owner"), 403)
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        try:
            if "name" in body:
                proje = state.projects.rename(proje.id, str(body["name"]))
                _audit(request, "project.rename", detail=proje.name)
            if "archived" in body:
                proje = state.projects.set_archived(proje.id, bool(body["archived"]))
                _audit(
                    request,
                    "project.archive" if proje.archived else "project.unarchive",
                    detail=proje.name,
                )
        except ProjectError as exc:
            return _error(str(exc))
        return _json({"ok": True, "project": proje.to_dict()})

    async def project_members(request: Request) -> Response:
        proje, hata = _proje_veya_hata(request)
        if hata is not None:
            return hata
        assert proje is not None
        if not _proje_yonetebilir(request, proje):
            return _error(t("project.needs_role", role="owner"), 403)

        kisiler = {u.id: u for u in state.auth.list_users()}
        satirlar = []
        for m in state.projects.members(proje.id):
            kisi = kisiler.get(m["user_id"])
            satirlar.append({
                "user_id": m["user_id"],
                "username": kisi.username if kisi else "",
                "display_name": kisi.display_name if kisi else "",
                "role": m["role"],
            })
        return _json({"members": satirlar})

    async def project_member_set(request: Request) -> Response:
        proje, hata = _proje_veya_hata(request)
        if hata is not None:
            return hata
        assert proje is not None
        if not _proje_yonetebilir(request, proje):
            return _error(t("project.needs_role", role="owner"), 403)
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        try:
            uid = int(body.get("user_id", 0))
        except (TypeError, ValueError):
            return _error(t("api.user_required"))
        if state.auth.get_user(uid) is None:
            return _error(t("api.unknown_user"), 404)

        try:
            state.projects.set_member(proje.id, uid, str(body.get("role", "developer")))
        except ProjectError as exc:
            return _error(str(exc))
        _audit(request, "project.member", detail=f"{proje.name}: {uid}")
        return _json({"ok": True})

    async def project_member_delete(request: Request) -> Response:
        proje, hata = _proje_veya_hata(request)
        if hata is not None:
            return hata
        assert proje is not None
        if not _proje_yonetebilir(request, proje):
            return _error(t("project.needs_role", role="owner"), 403)
        try:
            uid = int(request.path_params["user_id"])
        except (KeyError, TypeError, ValueError):
            return _error(t("api.user_required"))
        try:
            state.projects.remove_member(proje.id, uid)
        except ProjectError as exc:
            return _error(str(exc))
        _audit(request, "project.member_remove", detail=f"{proje.name}: {uid}")
        return _json({"ok": True})

    async def users_list(request: Request) -> Response:
        denied = _require_admin(request)
        if denied is not None:
            return denied
        return _json({"users": [u.to_dict() for u in state.auth.list_users()]})

    async def users_create(request: Request) -> Response:
        denied = _require_admin(request)
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        try:
            user = state.auth.create_user(
                str(body.get("username", "")),
                str(body.get("password", "")),
                role=str(body.get("role", "user")),
                display_name=str(body.get("display_name", "")),
            )
        except AuthError as exc:
            return _error(str(exc))
        state.runner.emit(
            "tool", t("actor.user"),
            t("api.user_created", name=user.username, role=user.role),
        )
        _audit(request, "user.create", detail=f"{user.username} · {user.role}")
        return _json(
            {"ok": True, "user": user.to_dict(), "warning": state.auth.last_warning}
        )

    async def users_update(request: Request) -> Response:
        denied = _require_admin(request)
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        user_id = int(request.path_params["user_id"])
        target = state.auth.get_user(user_id)
        if target is None:
            return _error(t("api.user_not_found"), 404)
        # Kendini kapatmak, kendini silmek kadar geri donulmezdir.
        if user_id == request.state.user.id and body.get("active") is False:
            return _error(t("api.cannot_disable_self"), 400)

        try:
            if "active" in body:
                target = state.auth.set_active(user_id, bool(body["active"]))
                state.runner.emit(
                    "warn" if not target.is_active else "tool",
                    t("actor.user"),
                    t(
                        "api.user_activated" if target.is_active
                        else "api.user_deactivated",
                        name=target.username,
                    ),
                )
                _audit(
                    request,
                    "user.enable" if target.is_active else "user.disable",
                    detail=target.username,
                )
            if body.get("role"):
                target = state.auth.set_role(user_id, str(body["role"]))
                _audit(request, "user.role",
                       detail=f"{target.username} · {target.role}")
            if body.get("password"):
                state.auth.set_password(user_id, str(body["password"]))
                state.runner.emit(
                    "warn", t("actor.user"),
                    t("api.password_reset", name=target.username),
                )
                _audit(request, "password.reset", detail=target.username)
            if body.get("logout_all"):
                closed = state.auth.close_all_sessions(user_id)
                state.runner.emit(
                    "warn", t("actor.user"),
                    t("api.sessions_closed", name=target.username, count=closed),
                )
                _audit(request, "user.sessions",
                       detail=f"{target.username} · {closed}")
        except AuthError as exc:
            return _error(str(exc))
        return _json({"ok": True, "user": target.to_dict()})

    async def audit_log(request: Request) -> Response:
        """Kim, ne zaman, ne yapti. Yalnizca yonetici okur.

        Kimlik dogrulama KAPALIYKEN (hic kullanici yokken) acik kalir:
        o kurulumda sunucunun tamami zaten aciktir ve gunlugu tek basina
        kapatmak hicbir sey korumaz, yalnizca yerel kurulumda paneli olu
        birakirdi. Kullanici tanimlandigi anda kapi kapanir.
        """
        if state.auth.is_configured:
            denied = _require_admin(request)
            if denied is not None:
                return denied

        try:
            limit = int(request.query_params.get("limit", "200"))
        except ValueError:
            limit = 200
        return _json(
            {
                "entries": state.auth.list_audit(
                    limit=limit,
                    username=request.query_params.get("user") or None,
                    action=request.query_params.get("action") or None,
                ),
                # Suzgec listeleri gunlukte GERCEKTEN gecenlerden dolar ve
                # suzgeclerin KENDISINDEN etkilenmez: yoksa bir turu secmek
                # kullanici listesini de daraltir ve ikinci bir suzgec
                # secilemezdi.
                "actions": state.auth.audit_actions(),
                "users": state.auth.audit_users(),
                "total": state.auth.audit_count(),
                "kept": AUDIT_KEEP,
            }
        )

    async def users_delete(request: Request) -> Response:
        denied = _require_admin(request)
        if denied is not None:
            return denied
        user_id = int(request.path_params["user_id"])
        if request.state.user.id == user_id:
            return _error(t("api.cannot_delete_self"), 400)
        try:
            target = state.auth.get_user(user_id)
            state.auth.delete_user(user_id)
        except AuthError as exc:
            return _error(str(exc))
        state.runner.emit(
            "warn", t("actor.user"),
            t("api.user_removed", name=target.username if target else user_id),
        )
        # Silinen hesabin GECMISI kalir; bu satir da onun bir parcasi.
        _audit(request, "user.delete",
               detail=target.username if target else str(user_id))
        return _json({"ok": True})

    async def update_task(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        key = request.path_params["key"].upper()
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        project = state.orchestrator.state
        if project.get_task(key) is None:
            return _error(t("api.no_such_task", key=key), 404)
        status = body.get("status")
        valid = {s.value for s in Status}
        if status is not None and status not in valid:
            return _error(
                t("api.bad_status", status=status, options=", ".join(sorted(valid)))
            )
        project.update_task(key, status=status, result=body.get("result"))
        state.runner.emit("tool", t("actor.task"), t("api.task_status", key=key, status=status))
        return _json({"ok": True, "task": asdict(project.get_task(key))})

    # ---------------------------------------------------------------- #
    # Bilgi tabani
    # ---------------------------------------------------------------- #
    async def documents(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        orch = state.orchestrator
        return _json({"stats": orch.kb.stats(), "documents": orch.kb.list_documents()})

    async def search(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        query = str(body.get("query", "")).strip()
        if not query:
            return _error("Arama sorgusu bos.")
        kinds = body.get("kinds") or None
        try:
            limit = max(1, min(int(body.get("k", 8) or 8), 30))
        except (TypeError, ValueError):
            return _error("k sayisal olmali.")

        def run_search() -> list[dict[str, Any]]:
            hits = state.orchestrator.kb.search(query, k=limit, kinds=kinds)
            return [
                {
                    "id": h.id,
                    "title": h.title,
                    "source": h.source,
                    "kind": h.kind,
                    "heading_path": h.heading_path,
                    "citation": h.citation(),
                    "start_line": h.start_line,
                    "score": round(h.score, 5),
                    "text": h.text,
                }
                for h in hits
            ]

        # Gomme modeli ilk cagride yuklenir ve CPU-yogundur; olay dongusunu bloke etme.
        try:
            hits = await asyncio.to_thread(run_search)
        except DeerXError as exc:
            return _error(str(exc), 500)
        return _json({"query": query, "hits": hits})

    def _alan_ici_yol(raw: str) -> Path | None:
        """Yolu calisma alanina gore cozer; disari cikiyorsa None doner.

        OLCULDU: `is_absolute()` denetimi yalnizca GORELI yolu calisma
        alanina bagliyordu; mutlak yol oldugu gibi kabul ediliyordu. Yani
        giris yapmis herhangi bir kullanici konaktaki herhangi bir dizini
        indeksleyip icerigini `/api/search` ile geri okuyabiliyordu --
        ev dizini, ssh anahtarlari, baska bir musterinin projesi.

        `ToolContext.resolve_path` ile ayni disiplin; ajanin uydugu kurala
        HTTP ucunun uymamasi icin bir sebep yok.
        """
        aday = Path(raw).expanduser()
        if not aday.is_absolute():
            aday = state.settings.workspace / aday
        cozulen = aday.resolve()
        if not cozulen.is_relative_to(state.settings.workspace.resolve()):
            return None
        return cozulen

    async def ingest(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        if state.runner.is_running:
            return _error(t("api.ingest_locked"), 409)

        raw = str(body.get("path", "") or "").strip()
        force = bool(body.get("force", False))
        sources: list[Path] = []
        if raw:
            candidate = _alan_ici_yol(raw)
            if candidate is None:
                return _error(t("api.outside_workspace", path=raw), 400)
            if not candidate.exists():
                return _error(t("api.path_not_found", path=raw), 404)
            sources.append(candidate)

        def run_ingest() -> dict[str, Any]:
            result = state.orchestrator.run_phase(Phase.INGEST, sources=sources, force=force)
            return {
                "ok": result.ok,
                "summary": result.summary,
                "error": result.error,
                "stats": state.orchestrator.kb.stats(),
            }

        outcome = await asyncio.to_thread(run_ingest)
        _audit(request, "knowledge.ingest", detail=raw, ok=bool(outcome["ok"]))
        return _json(outcome, status=200 if outcome["ok"] else 400)

    async def forget_document(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        source = str(body.get("source", "")).strip()
        if not source:
            return _error(t("api.source_required"))

        # Uc ayri niyet, uc ayri islem. Onceden tek bir "kaldir" vardi ve
        # o yalnizca dizinden siliyordu: dosya diskte kaldigi icin bir
        # sonraki `ingest` belgeyi SESSIZCE geri getiriyordu.
        mode = str(body.get("mode", "deactivate")).strip().lower()
        if mode not in ("deactivate", "activate", "delete"):
            return _error(t("api.unknown_forget_mode", mode=mode))

        kb = state.orchestrator.kb
        if mode == "delete":
            removed = kb.forget(source, remove_file=True)
            state.runner.emit(
                "tool", "rag", t("api.removed_chunks", source=source, count=removed)
            )
            _audit(request, "knowledge.delete", detail=source)
            return _json({"ok": True, "mode": mode, "removed_chunks": removed})

        aktif = mode == "activate"
        if not kb.deactivate(source, active=aktif):
            return _error(t("api.unknown_document", source=source), 404)
        state.runner.emit(
            "tool", "rag",
            t("api.doc_activated" if aktif else "api.doc_deactivated", source=source),
        )
        _audit(request, "knowledge.activate" if aktif else "knowledge.deactivate",
               detail=source)
        return _json({"ok": True, "mode": mode, "removed_chunks": 0})

    def _uploader(request: Request) -> str:
        """Yukleyenin kullanici adi. Kimlik dogrulama kapaliysa bos kalir --
        ve BOS BIRAKILIR, tahmin edilmez: kim yukledigi bilinmiyorsa
        arayuz onu "-" gosterir; uydurulmus bir ad denetim gunlugunu
        degersiz kilardi."""
        user = getattr(request.state, "user", None)
        return getattr(user, "username", "") or ""

    async def upload(request: Request) -> Response:
        """Sartname dosyasini `docs/` altina yazar ve indeksler.

        Govde ham dosya baytlaridir, dosya adi `name` sorgu parametresinde gelir.
        Multipart yerine bu yol secildi: `python-multipart` bagimliligi eklemeden
        tarayicidan `fetch(url, {body: file})` ile dogrudan gonderilebiliyor.
        """
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        if state.runner.is_running:
            return _error(t("api.upload_locked"), 409)

        raw_name = request.query_params.get("name", "").strip()
        # Yalnizca dosya adi; yol bileseni kabul edilmez.
        name = Path(raw_name.replace("\\", "/")).name
        if not name or name in {".", ".."}:
            return _error(t("api.need_file_name"))

        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return _error(
                t(
                    "api.unsupported_suffix",
                    suffix=suffix or name,
                    supported=", ".join(sorted(SUPPORTED_SUFFIXES)),
                )
            )

        body = await request.body()
        if not body:
            return _error(t("api.empty_file"))
        if len(body) > state.settings.rag.max_file_bytes:
            return _error(
                t(
                    "api.file_too_large",
                    size=f"{len(body):,}",
                    limit=f"{state.settings.rag.max_file_bytes:,}",
                )
            )

        docs_dir = state.settings.workspace / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        target = docs_dir / name
        # Ayni adla ikinci bir yukleme okunamazsa, oncekini silmemeliyiz:
        # bozuk bir dosya yuzunden calisan sartnameyi kaybetmek kabul edilemez.
        backup = target.read_bytes() if target.is_file() else None
        target.write_bytes(body)
        state.runner.emit(
            "tool", t("actor.upload"),
            t("api.upload_received", name=name, size=f"{len(body):,}"),
        )

        def run_ingest() -> dict[str, Any]:
            result = state.orchestrator.kb.ingest_file(
                target, force=True, uploaded_by=_uploader(request)
            )
            if not result.ok:
                # Okunamayan dosyayi calisma alaninda birakma. Ama var olan bir
                # dosyanin ustune yazdiysak eskisini geri koy: bozuk bir yukleme
                # yuzunden calisan sartnameyi kaybetmek kabul edilemez.
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(backup)
                    state.orchestrator.kb.ingest_file(
                        target, force=True, uploaded_by=_uploader(request)
                    )
            return {
                "ok": result.ok,
                "name": name,
                "chunks": result.chunks,
                "error": result.error,
                "restored": not result.ok and backup is not None,
                "stats": state.orchestrator.kb.stats(),
            }

        outcome = await asyncio.to_thread(run_ingest)
        _audit(request, "knowledge.upload", detail=name, ok=bool(outcome["ok"]))
        return _json(outcome, status=200 if outcome["ok"] else 400)

    # ---------------------------------------------------------------- #
    # Teslimat paketi
    # ---------------------------------------------------------------- #
    async def package_status(request: Request) -> Response:
        """Hazirlik denetimi ve mevcut paketler."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        from ..pipeline.packaging import check_readiness

        readiness = check_readiness(state.orchestrator.state)
        packages = sorted(
            state.settings.deliveries_dir.glob("*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Raporu ancak proje hafizasinda kayitli paketler icin gosterebiliriz;
        # dizine elle atilmis bir zip'in artifakt kaydi olmaz.
        known = {a.name for a in state.orchestrator.state.list_artifacts()}
        return _json(
            {
                "ready": readiness.ok,
                "blockers": [i.message for i in readiness.blockers],
                "warnings": [i.message for i in readiness.warnings],
                "packages": [
                    {
                        "name": p.name,
                        "bytes": p.stat().st_size,
                        "created_at": p.stat().st_mtime,
                        "has_report": p.name in known,
                    }
                    for p in packages[:10]
                ],
            }
        )

    async def package_build(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        if state.runner.is_running:
            return _error(t("api.package_locked"), 409)

        from ..pipeline.packaging import PackagingError, PackagingNotReady, build_package

        force = bool(body.get("force", False))

        def run_build() -> dict[str, Any]:
            # Elle paketleme de tek adimli bir kosudur. Kosu kaydi olmadan
            # uretilen paket hicbir kosuya ait olmaz ve Ciktilar'da gorunmez.
            import uuid

            project = state.orchestrator.state
            run_id = uuid.uuid4().hex[:12]
            seq = project.start_run(
                run_id,
                goal=project.get_meta("goal", "") or "Elle paketleme",
                phases=[str(Phase.PACKAGE)],
            )
            project.start_run_step(run_id, Phase.PACKAGE, 0)
            try:
                result = build_package(
                    project,
                    state.settings.workspace,
                    state.settings.deliveries_dir,
                    goal=project.get_meta("goal", ""),
                    force=force,
                    run_id=run_id,
                )
            except Exception as exc:
                project.finish_run_step(
                    run_id, Phase.PACKAGE, status=Status.FAILED, error=str(exc)
                )
                project.finish_run(run_id, status=Status.FAILED, error=str(exc))
                raise

            summary = f"{result.file_count} dosya · {result.total_bytes / 1e6:.1f} MB"
            project.finish_run_step(
                run_id, Phase.PACKAGE, status=Status.DONE, summary=summary
            )
            project.finish_run(run_id, status=Status.DONE)
            # Artifakt kaydini `build_package` yapar.
            return {**result.to_dict(), "run_id": run_id, "seq": seq}

        try:
            outcome = await asyncio.to_thread(run_build)
        except PackagingNotReady as exc:
            return _json(
                {
                    "ready": False,
                    "blockers": [i.message for i in exc.readiness.blockers],
                    "warnings": [i.message for i in exc.readiness.warnings],
                    "error": "Proje teslim edilecek durumda degil.",
                },
                status=409,
            )
        except PackagingError as exc:
            return _error(str(exc), 400)

        state.runner.emit("done", "teslimat", f"paket hazir: {outcome['name']}")
        _audit(request, "package.build", detail=str(outcome["name"]))
        return _json({"ok": True, **outcome})

    async def package_download(request: Request) -> Response:
        """Zip dosyasini indirir."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        # Yalnizca dosya adi kabul edilir; yol bileseni teslimat dizininden
        # cikmaya calisan bir istek olurdu.
        name = Path(request.path_params["name"].replace("\\", "/")).name
        if not name.endswith(".zip"):
            return _error(t("api.zip_only"), 400)

        archive = state.settings.deliveries_dir / name
        if not archive.is_file():
            return _error(t("api.not_found", name=name), 404)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=name,
            headers={"Cache-Control": "no-store"},
        )

    # ---------------------------------------------------------------- #
    # Kullaniciya sorulan sorular
    # ---------------------------------------------------------------- #
    async def questions(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        project = state.orchestrator.state
        return _json(
            {
                "items": [asdict(q) for q in project.list_questions()],
                "blocking": [asdict(q) for q in project.open_blocking_questions()],
            }
        )

    async def resolve_question(request: Request) -> Response:
        # `answer_question` ajana serbest metin veriyor ve o metin bir
        # sonraki fazin GIRDISI. Kosu baslatmaktan daha az yazma degil.
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        key = request.path_params["key"].upper()
        action = str(body.get("action", "answer")).lower()
        text = str(body.get("text", "")).strip()

        if action == "answer":
            if not text:
                return _error(t("api.empty_answer"))
            question = state.orchestrator.answer_question(key, text)
        elif action == "skip":
            question = state.orchestrator.skip_question(key, text)
        else:
            return _error(t("api.unknown_action", action=action))

        if question is None:
            return _error(t("api.no_such_question", key=key), 404)
        remaining = state.orchestrator.state.open_blocking_questions()
        return _json(
            {
                "ok": True,
                "question": asdict(question),
                "remaining_blocking": [q.key for q in remaining],
            }
        )

    # ---------------------------------------------------------------- #
    # Ciktilar
    # ---------------------------------------------------------------- #
    async def artifacts(request: Request) -> Response:
        """Ciktilar, uretildikleri kosuya gore gruplanmis.

        Kosusu bilinmeyen ciktilar listelenmez: her cikti bir kosunun urunudur
        ve kosusuz bir grup basligi kullaniciya hicbir sey anlatmiyordu.
        `?orphans=1` ile kosu kaydindan onceki ciktilar da dahil edilir.
        """
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        project = state.orchestrator.state
        runs = {r["id"]: r for r in project.list_runs(200)}
        # Kosu bir IS AKISININ adimi; cikti da o is akisina aittir. Numara
        # tek tek sorulmuyor: is akislari bir kez okunup eslesme kuruluyor,
        # aksi halde her cikti icin ayri bir sorgu giderdi.
        akislar = {w["id"]: w["seq"] for w in project.list_workflows(200)}
        include_orphans = request.query_params.get("orphans") == "1"

        groups: dict[str, dict[str, Any]] = {}
        orphans = 0
        for artifact in project.list_artifacts():
            path = Path(artifact.path)
            run = runs.get(artifact.run_id)
            if run is None:
                orphans += 1
                if not include_orphans:
                    continue
            key = artifact.run_id if run else ""
            group = groups.setdefault(
                key,
                {
                    "run_id": key,
                    "seq": run["seq"] if run else None,
                    # Hangi is akisina ait. Eski kayitlarda `workflow_id`
                    # bos olabilir; o zaman numara da yok, uydurulmaz.
                    "workflow_id": run["workflow_id"] if run else "",
                    "workflow_seq": (
                        akislar.get(run["workflow_id"]) if run else None
                    ),
                    "title": run["title"] if run else "",
                    # Baslik arayuzde cevrilir; yazilmis metin yedek.
                    "title_key": run["title_key"] if run else "",
                    "title_args": run["title_args"] if run else {},
                    "goal": run["goal"] if run else "",
                    "started_at": run["started_at"] if run else None,
                    "items": [],
                },
            )
            group["items"].append(
                {
                    "name": artifact.name,
                    "kind": artifact.kind,
                    "summary": artifact.summary,
                    "phase": artifact.phase,
                    "phase_label": (
                        Phase(artifact.phase).label if artifact.phase in _PHASE_NAMES else ""
                    ),
                    "run_id": artifact.run_id,
                    "exists": path.is_file(),
                    "bytes": path.stat().st_size if path.is_file() else 0,
                    "format": _artifact_format(artifact.name),
                }
            )

        # En yeni kosu basta; kosusu bilinmeyenler en sonda.
        ordered = sorted(
            groups.values(),
            key=lambda g: (g["seq"] is None, -(g["seq"] or 0)),
        )
        total = sum(len(g["items"]) for g in ordered)
        return _json({"groups": ordered, "total": total, "orphans": orphans})

    async def artifact_detail(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        name = request.path_params["name"]
        match = next(
            (a for a in state.orchestrator.state.list_artifacts() if a.name == name), None
        )
        if match is None:
            return _error(t("api.not_found", name=name), 404)
        path = Path(match.path)
        if not path.is_file():
            return _error(t("api.file_missing", path=path), 404)

        fmt = _artifact_format(name)
        payload: dict[str, Any] = {
            "name": name,
            "kind": match.kind,
            "summary": match.summary,
            "format": fmt,
            "path": str(path),
            "bytes": path.stat().st_size,
        }

        if fmt == "archive":
            # Zip metin degildir: ham icerigi gondermek anlamsiz karakter yigini
            # olur. Yerine indirme baglantisi + icindeki teslimat raporu doner.
            from ..pipeline.packaging import list_entries, read_manifest

            entries = list_entries(path)
            report = read_manifest(path)
            payload.update(
                {
                    "download": f"/api/artifacts/{quote(name)}/download",
                    "entry_count": len(entries),
                    "entries": entries[:200],
                    "report": report,
                    "html": render_markdown(report) if report else "",
                }
            )
            return _json(payload)

        if fmt == "image":
            # `src` tarayicinin dogrudan cizebilecegi adres; `download`
            # dosyayi diske indirir. Ikisi ayni uc, farkli baslik.
            payload["download"] = f"/api/artifacts/{quote(name)}/download"
            payload["src"] = f"/api/artifacts/{quote(name)}/download?inline=1"
            return _json(payload)

        if fmt == "binary":
            payload["download"] = f"/api/artifacts/{quote(name)}/download"
            return _json(payload)

        raw = path.read_text(encoding="utf-8", errors="replace")
        payload["raw"] = raw
        if fmt == "markdown":
            payload["html"] = render_markdown(raw)
        return _json(payload)

    async def artifact_download(request: Request) -> Response:
        """Ciktiyi dosya olarak indirir (zip, gorsel, PDF …)."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        name = request.path_params["name"]
        match = next(
            (a for a in state.orchestrator.state.list_artifacts() if a.name == name), None
        )
        if match is None:
            return _error(t("api.not_found", name=name), 404)
        path = Path(match.path)
        if not path.is_file():
            return _error(t("api.file_missing", path=path), 404)

        # `?inline=1` yalnizca tarama goruntuleri icin gecerli: sayfa onlari
        # `<img>` ile cizer. Baska her sey `octet-stream` olarak iner --
        # ajanin urettigi bir dosyayi tarayiciya "bunu goster" diye vermek
        # onu uygulamanin kaynaginda calistirmak olurdu.
        if request.query_params.get("inline") == "1":
            suffix = path.suffix.lower()
            media = IMAGE_MEDIA_TYPES.get(suffix)
            if media is not None:
                return FileResponse(
                    path,
                    media_type=media,
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Disposition": f'inline; filename="{path.name}"',
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                        "X-Content-Type-Options": "nosniff",
                    },
                )

        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    # ---------------------------------------------------------------- #
    # Kosu
    # ---------------------------------------------------------------- #
    async def run_status(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        return _json(state.runner.status())

    async def run_workflow(request: Request) -> Response:
        """Son kosunun adim adim dokumu."""
        return _json(run_steps(state.runner, state.orchestrator.state))

    async def workflow_list_route(request: Request) -> Response:
        """Is akislari — her gelistirme bir is akisi, kosular onun adimlari."""
        from .runner import workflow_list

        return _json(workflow_list(state.runner, state.orchestrator.state))

    async def workflow_detail_route(request: Request) -> Response:
        """Bir is akisinin adimlari. `#2` gibi sirali numara da kabul edilir."""
        from .runner import workflow_detail

        raw_id = request.path_params["workflow_id"]
        project = state.orchestrator.state
        workflow_id = _resolve_id(raw_id, project.get_workflow, project.get_workflow_by_seq)
        if workflow_id is None:
            return _error(t("api.no_such_workflow", id=raw_id), 404)

        detail = workflow_detail(state.runner, project, workflow_id)
        if detail is None:
            return _error(t("api.no_such_workflow", id=raw_id), 404)
        return _json(detail)

    async def workflow_chat(request: Request) -> Response:
        """Bir is akisi hakkinda konusma: gecmisi oku ya da mesaj gonder.

        Model cagrisi SENKRON ve uzun surebilir; olay dongusunu bloke
        etmemek icin bir is parcacigina alinir. Aksi halde sohbet suren
        her saniye butun arayuz -- canli akis dahil -- donardi.
        """
        # Sohbet bir YAZMA yolu: cevabin `changes` alani is akisinin
        # durumunu degistiriyor ve model cagrisi para harciyor. `#btn-run`
        # dan 403 alan bir izleyici, sohbetten ayni projeyi
        # degistirebiliyordu. GET serbest kalir -- "bu is akisi hakkinda
        # ne konusulmus" izleyicinin gormesi gereken sey.
        if request.method != "GET":
            denied = _require_role(
                request, "owner" if request.method == "DELETE" else "developer"
            )
            if denied is not None:
                return denied
        project = state.orchestrator.state
        raw_id = request.path_params["workflow_id"]
        workflow_id = _resolve_id(raw_id, project.get_workflow, project.get_workflow_by_seq)
        if workflow_id is None:
            return _error(t("api.no_such_workflow", id=raw_id), 404)

        if request.method == "GET":
            return _json({"messages": project.chat_history(workflow_id)})

        if request.method == "DELETE":
            return _json({"ok": True, "deleted": project.clear_chat(workflow_id)})

        body = await _body(request)
        message = str(body.get("message") or "").strip()
        if not message:
            return _error(t("chat.empty_message"))

        # Kosu surerken sohbet acilmaz: ikisi de ayni LLM istemcisini ve
        # ayni arac baglamini kullanir, ve `ToolContext.workflow_id`
        # kosunun altindan kayardi.
        if state.runner.is_running:
            return _error(t("chat.busy"), 409)

        import anyio.to_thread

        cevap = await anyio.to_thread.run_sync(
            lambda: state.orchestrator.chat(workflow_id, message)
        )
        _audit(request, "workflow.chat", detail=message[:120])
        return _json(
            {
                "reply": cevap.text,
                "changes": cevap.changes,
                "iterations": cevap.iterations,
                "error": cevap.error,
                "messages": project.chat_history(workflow_id),
            }
        )

    async def run_list(request: Request) -> Response:
        """Kosu gecmisi — en yenisi basta."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        project = state.orchestrator.state
        live = state.runner.status()
        current = (live.get("current") or {}).get("id")
        runs = project.list_runs(50)
        for run in runs:
            run["live"] = bool(live["running"] and run["id"] == current)
            run["steps_done"] = sum(
                1 for s in project.run_step_rows(run["id"]) if s["status"] == Status.DONE
            )
        return _json({"runs": runs, "running": live["running"]})

    async def run_detail_route(request: Request) -> Response:
        """Tek bir kosunun tum adimlari. `#3` gibi sirali numara da kabul edilir."""
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        raw_id = request.path_params["run_id"]
        project = state.orchestrator.state
        run_id = _resolve_id(raw_id, project.get_run, project.get_run_by_seq)
        if run_id is None:
            return _error(t("api.no_such_run", id=raw_id), 404)

        detail = run_detail(state.runner, project, run_id)
        if detail is None:
            return _error(t("api.no_such_run", id=raw_id), 404)
        return _json(detail)

    async def run_retry(request: Request) -> Response:
        """Basarisiz bir kosuyu, hatanin oldugu adimdan itibaren tekrar kosar.

        Once tek care butun gelistirmeyi bastan baslatmakti: onuncu adimda
        kirilan bir is akisi, onden gecen dokuz adimin model bedelini ikinci
        kez odetiyordu. Kullanicinin istedigi sey hicbir zaman bu degil --
        kirilan yeri tekrar denemek.

        Govde bos gelebilir; o zaman ilk sorunlu adim secilir. `phase`
        verilirse oradan baslanir, basarili bir adim olsa bile.
        """
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        raw_id = request.path_params["run_id"]
        project = state.orchestrator.state
        run_id = _resolve_id(raw_id, project.get_run, project.get_run_by_seq)
        if run_id is None:
            return _error(t("api.no_such_run", id=raw_id), 404)
        record = project.get_run(run_id)
        if record is None:
            return _error(t("api.no_such_run", id=raw_id), 404)

        try:
            body = await _body(request)
            phases, baslangic = retry_plan(
                record, project.run_step_rows(run_id), str(body.get("phase", "") or "")
            )
        except DeerXError as exc:
            return _error(str(exc))

        needs_llm = any(p is not Phase.INGEST for p in phases)
        if needs_llm and not state.settings.llm_ready:
            return _error(f"Model cagrisi yapilamaz: {state.settings.llm_hint}.", 400)

        title = f"#{record['seq']} tekrar · {baslangic.label}"
        title_key = "runs.titleRetry"
        title_args = {"seq": record["seq"], "phase": str(baslangic)}

        try:
            info = state.runner.start(
                phases,
                started_by=_uploader(request),
                goal=record["goal"],
                brief=record["brief"],
                title=title,
                title_key=title_key,
                title_args=title_args,
                # Kullanici bu adimi acikca tekrar istedi; "zaten tamamlandi"
                # deyip atlamak dugmeyi islevsiz birakirdi. Sonraki adimlar da
                # zorlanir: kirilan bir adimin ustune kurulmus ciktilar
                # supheli, tekrar uretilmeleri gerekir.
                force=True,
                task_key=record["task_key"] or None,
                plan_id=record["plan_id"] or None,
            )
        except RunBusy as exc:
            return _error(str(exc), 409)
        except DeerXError as exc:
            return _error(str(exc))

        _audit(request, "run.retry", detail=title,
               detail_key=title_key, detail_args=title_args)
        return _json({"ok": True, "run": info.to_dict(), "from": str(baslangic)})

    async def run_start(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        try:
            if body.get("phases"):
                # Arayuzun ana yolu: kullanici adimlari tek tek secer.
                selection = body["phases"]
                if not isinstance(selection, list):
                    return _error(t("api.phases_must_be_list"))
                phases = phase_selection([str(x) for x in selection])
            elif body.get("phase"):
                phases = [Phase(str(body["phase"]).lower())]
            else:
                phases = phase_range(
                    str(body.get("from", "ingest")), str(body.get("to", "plan"))
                )
        except (DeerXError, ValueError) as exc:
            return _error(str(exc) or "Bilinmeyen faz.")

        needs_llm = any(p is not Phase.INGEST for p in phases)
        if needs_llm and not state.settings.llm_ready:
            return _error(f"Model cagrisi yapilamaz: {state.settings.llm_hint}.", 400)

        # `sources` INDEKSLENECEK yollar; `doc_scope` kosunun OKUYABILECEGI
        # belgeler. Ikisi ayri sey ve ayni istekte birlikte gelebilir:
        # "sunu indeksle, sonra yalnizca sunlari oku".
        doc_scope = [str(x).strip() for x in (body.get("doc_scope") or []) if str(x).strip()]

        sources: list[Path] = []
        for entry in body.get("sources") or []:
            # Ayni delik burada da vardi: `/api/ingest` ile ayni sekilde
            # mutlak bir yol denetimsiz geciyordu.
            candidate = _alan_ici_yol(str(entry))
            if candidate is None:
                return _error(t("api.outside_workspace", path=entry), 400)
            sources.append(candidate)

        # Kosuya anlamli bir baslik ver: liste "hangi kosu neydi" sorusunu
        # cevaplamali. Projenin hedefi her kosuda ayni oldugu icin yetmiyor.
        #
        # Baslik hem YAZILMIS metin hem de ANAHTAR + PARAMETRE olarak
        # saklanir. Yalnizca metin saklandiginda Ingilizce arayuz kosu
        # listesini Turkce gosteriyordu: metin sunucunun o anki diliyle
        # uretiliyor ve bir daha degismiyordu.
        project = state.orchestrator.state
        task_key = str(body["task_key"]).upper() if body.get("task_key") else None
        plan_id = str(body["plan_id"]) if body.get("plan_id") else None
        if task_key:
            task = project.get_task(task_key)
            if task:
                title = f"{task_key} · {task.title}"
                title_key, title_args = "runs.titleTask", {
                    "key": task_key, "title": task.title,
                }
            else:
                title = f"Gorev {task_key}"
                title_key, title_args = "runs.titleTaskOnly", {"key": task_key}
        elif plan_id:
            # `plan_id` yalnizca plan ekranindaki "Baslat" dugmesinden gelir.
            # Faz listesine karsilastirma yapilmaz: secim `ingest` adimini
            # basa ekledigi icin liste hicbir zaman sadece [implement] olmaz.
            plan = project.get_plan(plan_id)
            if plan:
                title = f"Plan: {plan['name']}"
                title_key, title_args = "runs.titlePlan", {"name": plan["name"]}
            else:
                title = "Plan uygulamasi"
                title_key, title_args = "runs.titlePlanOnly", {}
        elif len(phases) == 1:
            title = phases[0].label
            title_key, title_args = "runs.titlePhase", {"phase": str(phases[0])}
        else:
            title = f"{phases[0].label} → {phases[-1].label}"
            title_key, title_args = "runs.titlePhases", {
                "first": str(phases[0]), "last": str(phases[-1]),
            }

        try:
            info = state.runner.start(
                phases,
                started_by=_uploader(request),
                goal=str(body.get("goal", "") or ""),
                title=title,
                title_key=title_key,
                title_args=title_args,
                brief=body.get("brief"),
                sources=sources,
                doc_scope=doc_scope,
                force=bool(body.get("force", False)),
                task_key=task_key,
                plan_id=plan_id,
            )
        except RunBusy as exc:
            return _error(str(exc), 409)
        except DeerXError as exc:
            return _error(str(exc))
        # "Ne calistirmis" sorusunun cevabi. Basligin cevrilebilir hali de
        # yazilir: gunluk, satirin yazildigi gunun dilinde donmasin.
        _audit(request, "run.start", detail=title,
               detail_key=title_key, detail_args=title_args)
        return _json({"ok": True, "run": info.to_dict()})

    async def run_stop(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        stopped = state.runner.stop()
        # Durdurulacak bir sey yoksa satir da yok: bos bir "durdur" istegi
        # gunlugu doldurur ve hicbir sey anlatmaz.
        if stopped:
            _audit(request, "run.stop")
        return _json({"ok": stopped, "running": state.runner.is_running})

    # ---------------------------------------------------------------- #
    # Onaylar
    # ---------------------------------------------------------------- #
    async def approvals(request: Request) -> Response:
        # Bekleyen onay istegi projenin isini durduruyor: uye olmayan
        # biri o listeyi gormemeli, cunku istek metni ajanin calistirmak
        # istedigi KOMUTU tasiyor.
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        return _json({"items": state.runner.pending_approvals()})

    async def resolve_approval(request: Request) -> Response:
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        # ONAYI KOSUYU BASLATAN VERIR. Ajanin calistirmak istedigi
        # tehlikeli komutu, o kosuyu baslatan kisi degerlendirebilir:
        # baskasi icin "evet" demek, onun adina risk almak olur ve
        # gunlukte de o kisinin adi kalir. Platform yoneticisi disarida
        # -- takilmis bir kosuyu cozecek biri her zaman olmali.
        sahip = state.runner.owner
        ben = _uploader(request)
        if sahip and ben and sahip != ben and not _is_admin(request):
            return _error(t("api.approval_not_yours", user=sahip), 403)
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        approval_id = request.path_params["approval_id"]
        granted = bool(body.get("granted", False))
        if not state.runner.resolve_approval(approval_id, granted):
            return _error(t("api.approval_gone"), 404)
        # Bir kabuk komutunu konak makinede calistirma iznini KIMIN
        # verdigi, cok kullanicili bir kurulumda en cok sorulacak denetim
        # sorusu; gunluk onu cevaplayamiyordu.
        _audit(request, "approval.resolve",
               detail=("onaylandi" if granted else "reddedildi") + f" · {approval_id}")
        return _json({"ok": True, "granted": granted})

    # ---------------------------------------------------------------- #
    # Canli olay akisi (SSE)
    # ---------------------------------------------------------------- #
    async def events_history(request: Request) -> Response:
        """Diskteki olay gunlugunun SONUNU doner.

        Canli akis yalnizca bellekteki tampondan besleniyordu; sunucu
        yeniden baslatildiginda ekran bosaliyor, oysa dosya yerinde
        duruyordu. Denetlenebilirlik ekranda bitmezse yoktur.

        Dosya bastan degil SONDAN okunur: 16 MB'lik bir gunlugu belege
        alip son iki yuz satirini vermek, istenen seyi yapmanin en pahali
        yoludur.
        """
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        try:
            limit = int(request.query_params.get("limit", "300"))
        except ValueError:
            limit = 300
        limit = max(1, min(limit, 2000))

        yol = state.settings.events_path
        if not yol.is_file():
            return _json({"events": [], "total": 0, "path": str(yol)})

        satirlar = _tail_lines(yol, limit)
        olaylar: list[dict[str, Any]] = []
        for satir in satirlar:
            try:
                kayit = json.loads(satir)
            except (ValueError, TypeError):
                # Kosu yarida kesildiyse son satir yarim kalmis olabilir;
                # tek bozuk satir butun gecmisi goturmemeli.
                continue
            if isinstance(kayit, dict):
                # `seq` canli tamponun sayacidir; gecmis kayitlarda yok.
                # Bos birakilir ki istemci imlecini geriye kaydirmasin.
                kayit.setdefault("seq", None)
                olaylar.append(kayit)
        return _json({"events": olaylar, "total": len(olaylar), "path": str(yol)})

    async def events_stream(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        try:
            cursor = int(request.query_params.get("since", "0"))
        except ValueError:
            cursor = 0
        # since=-1 => yalnizca bundan sonraki olaylar (gecmisi tekrar gonderme).
        if cursor < 0:
            cursor = state.runner.last_seq

        return EventSourceResponse(
            event_publisher(state.runner, cursor, request.is_disconnected)
        )

    # ---------------------------------------------------------------- #
    # Statik dosyalar
    # ---------------------------------------------------------------- #
    async def index(request: Request) -> Response:
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    routes = [
        Route("/", index),
        Route("/api/auth/status", auth_status),
        Route("/api/auth/setup", auth_setup, methods=["POST"]),
        Route("/api/auth/login", auth_login, methods=["POST"]),
        Route("/api/auth/logout", auth_logout, methods=["POST"]),
        Route("/api/auth/password", auth_password, methods=["POST"]),
        Route("/api/auth/sessions", auth_sessions),
        Route("/api/auth/sessions/{session_id}", auth_session_close,
              methods=["DELETE"]),
        Route("/api/environment", environment),
        Route("/api/environment/rebuild", environment_rebuild, methods=["POST"]),
        Route("/api/projects", projects_list),
        Route("/api/projects", projects_create, methods=["POST"]),
        Route("/api/projects/{project_id}", projects_update, methods=["POST"]),
        Route("/api/projects/{project_id}/activate", project_activate,
              methods=["POST"]),
        Route("/api/projects/{project_id}/members", project_members),
        Route("/api/projects/{project_id}/members", project_member_set,
              methods=["POST"]),
        Route("/api/projects/{project_id}/members/{user_id}",
              project_member_delete, methods=["DELETE"]),
        Route("/api/users", users_list),
        Route("/api/users", users_create, methods=["POST"]),
        Route("/api/users/{user_id}", users_update, methods=["POST"]),
        Route("/api/users/{user_id}", users_delete, methods=["DELETE"]),
        Route("/api/audit", audit_log),
        Route("/api/overview", overview),
        Route("/api/settings", update_settings, methods=["POST"]),
        Route("/api/providers", provider_catalog, methods=["GET"]),
        Route("/api/settings/models", list_models, methods=["POST"]),
        Route("/api/settings/test-browser", test_browser, methods=["POST"]),
        Route("/api/settings/test-search", test_search, methods=["POST"]),
        Route("/api/settings/test-llm", test_llm, methods=["POST"]),
        Route("/api/state/{section}", project_state),
        Route("/api/tasks/{key}", update_task, methods=["POST"]),
        Route("/api/plans", plans),
        Route("/api/plans", create_plan, methods=["POST"]),
        Route("/api/plans/{plan_id}", update_plan, methods=["POST"]),
        Route("/api/plans/{plan_id}", delete_plan, methods=["DELETE"]),
        Route("/api/documents", documents),
        Route("/api/search", search, methods=["POST"]),
        Route("/api/ingest", ingest, methods=["POST"]),
        Route("/api/forget", forget_document, methods=["POST"]),
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/package", package_status),
        Route("/api/package", package_build, methods=["POST"]),
        Route("/api/package/{name}", package_download),
        Route("/api/questions", questions),
        Route("/api/questions/{key}", resolve_question, methods=["POST"]),
        Route("/api/artifacts", artifacts),
        Route("/api/artifacts/{name}", artifact_detail),
        Route("/api/artifacts/{name}/download", artifact_download),
        Route("/api/run", run_status),
        Route("/api/run", run_start, methods=["POST"]),
        Route("/api/run/steps", run_workflow),
        Route("/api/workflows", workflow_list_route),
        Route("/api/workflows/{workflow_id}", workflow_detail_route),
        Route(
            "/api/workflows/{workflow_id}/chat",
            workflow_chat,
            methods=["GET", "POST", "DELETE"],
        ),
        Route("/api/runs", run_list),
        Route("/api/runs/{run_id}", run_detail_route),
        Route("/api/runs/{run_id}/retry", run_retry, methods=["POST"]),
        Route("/api/run/stop", run_stop, methods=["POST"]),
        Route("/api/approvals", approvals),
        Route("/api/approvals/{approval_id}", resolve_approval, methods=["POST"]),
        Route("/api/events", events_stream),
        Route("/api/events/history", events_history),
        Mount("/static", NoCacheStatics(directory=STATIC_DIR), name="static"),
    ]

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            state.close()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(AuthMiddleware, state=state)
    app.state.deerx = state
    return app


ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar")
# Ekranda GOSTERILEBILEN goruntuler. `.svg` bilerek disarida: SVG betik
# tasiyabilir ve dogrudan acildiginda uygulamanin kendi kaynaginda calisir.
# Buradakiler tarama goruntuleridir, betik calistiramazlar.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
}
BINARY_SUFFIXES = (
    ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mp3", ".wasm", ".db",
    ".svg",
)


_PHASE_NAMES = {str(p) for p in Phase.ordered()}


# Kimlik dogrulamasiz gecebilecek yollar. Statik dosyalar arayuzun kabugudur
# ve veri tasimaz; giris ekraninin cizilebilmesi icin acik kalirlar.
PUBLIC_PATHS = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/logout",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Her istegi oturuma baglar ve korumali yollari kapatir.

    Ara katman kullanildi, rota basina dekorator degil: yeni bir rota
    eklendiginde korumayi eklemeyi unutmak mumkun olmasin. Varsayilan
    kapalidir, acik olanlar tek tek sayilir.
    """

    def __init__(self, app: Any, state: AppState) -> None:
        super().__init__(app)
        self.state = state

    def _proje_coz(self, request: Request) -> int:
        """Cerezdeki projeyi DOGRULAR; gecersizse varsayilana duser.

        Dogrulama sart: cerez istemcide duruyor ve elle degistirilebilir.
        Uye olunmayan bir projeye gecmek, o projenin belgelerini ve
        planini okumak demek olurdu.
        """
        # ONCE BASLIK, sonra cerez. Cerez tarayici genelidir: A
        # sekmesinde proje degistiren kisi B sekmesinin sonraki istegini
        # de tasiyordu ve B'deki "Baslat" baska projeyi kosturuyordu.
        # Hash sekmeye aittir; baslik onu sunucuya tasir.
        slug = request.headers.get(PROJECT_HEADER, "").strip()
        if slug and slug != "-":
            proje = self.state.projects.by_slug(slug)
            if proje is None or proje.archived:
                return 0
            return self._uyeyse(request, proje.id)

        ham = request.cookies.get(PROJECT_COOKIE, "")
        if not ham.isdigit():
            return 0
        pid = int(ham)
        proje = self.state.projects.get(pid)
        if proje is None or proje.archived:
            return 0
        return self._uyeyse(request, pid)

    def _uyeyse(self, request: Request, pid: int) -> int:
        """Uyelik dogrulanmis proje kimligi; degilse 0.

        Dogrulama sart: cerez de baslik da ISTEMCIDEN geliyor ve elle
        degistirilebilir. Uye olunmayan bir projeye gecmek, o projenin
        belgelerini ve planini okumak demek olurdu.
        """
        if not self.state.auth.is_configured:
            return pid
        user = getattr(request.state, "user", None)
        if user is None:
            return 0
        if user.is_admin or self.state.projects.role_of(pid, user.id):
            return pid
        return 0

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request.state.user = self.state.auth.resolve_session(
            request.cookies.get(SESSION_COOKIE)
        )
        # Proje baglamI, kimlik cozuldukten SONRA kurulur: dogrulama
        # kullaniciyi bilmek zorunda.
        jeton = _AKTIF_PROJE.set(self._proje_coz(request))
        try:
            return await self._dispatch(request, call_next)
        finally:
            _AKTIF_PROJE.reset(jeton)

    async def _dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path

        # Hic kullanici yoksa kimlik dogrulama kapalidir: yerel tek kullanicili
        # kurulum bugunku gibi calisir. Disari acilan bir sunucuda `serve`
        # kullanici olmadan baslamaz (asagida kontrol edilir).
        if not self.state.auth.is_configured:
            return await call_next(request)

        if (
            request.state.user is not None
            or path in PUBLIC_PATHS
            or path.startswith("/static/")
            or path == "/"          # kabuk; icerigi API'den gelir
        ):
            return await call_next(request)

        return JSONResponse({"error": "Giris gerekli."}, status_code=401)


def _tail_lines(path: Path, count: int, *, block: int = 64 * 1024) -> list[str]:
    """Dosyanin son `count` satirini doner; bastan okumaz.

    Olay gunlugu 16 MB'a kadar buyuyebiliyor. Tamamini belege alip son
    birkac yuz satirini almak, her sayfa yenilemesinde o dosyayi bastan
    sona okumak demekti.
    """
    parcalar: list[bytes] = []
    satir_sayisi = 0
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            kalan = fh.tell()
            while kalan > 0 and satir_sayisi <= count:
                adim = min(block, kalan)
                kalan -= adim
                fh.seek(kalan)
                parca = fh.read(adim)
                parcalar.append(parca)
                satir_sayisi += parca.count(b"\n")
    except OSError:
        return []

    ham = b"".join(reversed(parcalar))
    satirlar = ham.decode("utf-8", "replace").splitlines()
    return [s for s in satirlar[-count:] if s.strip()]


def _artifact_format(name: str) -> str:
    """Ciktinin nasil gosterilecegini belirler.

    `archive` ve `binary` metin olarak *okunmaz*: bir zip'i utf-8 varsayip
    `errors="replace"` ile cozmek, tarayiciya megabaytlarca anlamsiz karakter
    gonderir. Bunlar ek dosya olarak indirilir.
    """
    lowered = name.lower()
    if lowered.endswith(ARCHIVE_SUFFIXES):
        return "archive"
    # Goruntuler ikiliden ONCE bakilir: `browser_screenshot` "kullanici
    # arayuzde gorur" diyor, oysa ekran goruntusu `binary` sayildigi surece
    # yalnizca bir indirme baglantisiydi.
    if lowered.endswith(IMAGE_SUFFIXES):
        return "image"
    if lowered.endswith(BINARY_SUFFIXES):
        return "binary"
    if lowered.endswith((".md", ".markdown")):
        return "markdown"
    if lowered.endswith((".html", ".htm")):
        return "html"
    if lowered.endswith((".json", ".yaml", ".yml", ".toml")):
        return "data"
    return "text"


def serve(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    reload: bool = False,
) -> None:
    """Web sunucusunu baslatir."""
    import uvicorn

    from ..logging import console

    app = build_app(settings)
    state: AppState = app.state.deerx
    loopback = host in {"127.0.0.1", "localhost", "::1"}

    if not loopback and not state.auth.is_configured:
        # Kimliksiz bir sunucuyu aga acmak, dosya yazip kabuk komutu
        # calistirabilen bir ucu herkese acmaktir. Uyarmak yetmez.
        state.close()
        raise ConfigError(t("serve.no_users_remote", host=host))

    if not loopback:
        console.print(
            t("serve.exposed_warning", host=host)
        )

    console.print(
        t(
            "serve.listening",
            url=f"http://{browse_host(host)}:{port}",
        )
    )
    console.print(t("serve.workspace", path=state.settings.workspace))

    if state.auth.is_configured:
        console.print(t("serve.login_required"))
    else:
        # Jeton yalnizca buraya basilir: sunucuya once ulasan biri yonetici
        # hesabini kapamasin.
        token = state.auth.issue_setup_token()
        console.print(t("serve.no_users_local", token=token))

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=state.settings.log_level.lower(),
        access_log=False,
    )
