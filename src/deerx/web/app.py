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
import shutil
import sqlite3
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from markdown_it import MarkdownIt
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.concurrency import iterate_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
from ..history import UserHistory, project_db, read_only, scan_project, table_columns
from ..i18n import set_language, t
from ..logging import EventLog, get_logger
from ..pipeline import Orchestrator, Phase, Status
from ..pipeline.artifacts import (  # noqa: F401 - uzanti tablolari burada da adlandirilir
    ARCHIVE_SUFFIXES,
    BINARY_SUFFIXES,
    IMAGE_MEDIA_TYPES,
    IMAGE_SUFFIXES,
    artifact_format,
)
from ..pipeline.state import ARTIFACT_MAX_BYTES, BLOB_PARCA
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
        takilan: set[int] = set()
        for pid, calisan in list(self._runtimes.items()):
            if not calisan.runner.is_running:
                continue
            calisan.runner.stop()
            calisan.runner.wait(SHUTDOWN_GRACE)
            if calisan.runner.is_running:
                # Adim bir model cagrisinda asili kalmis olabilir.
                # Baglantiyi kapatmiyoruz: coken bir surec yerine sizan
                # bir thread daha iyidir, surec zaten sonlaniyor.
                log.warning(t("api.run_not_stopping", seconds=SHUTDOWN_GRACE))
                takilan.add(pid)

        # TAKILAN PROJEYI ATLA, otekileri KAPAT. Burada `return` vardi:
        # tek bir projenin asili kalmis kosusu butun projelerin temizligini
        # iptal ediyordu -- hicbir servis durdurulmuyor, hicbir tarayici
        # kapanmiyor, hicbir konteyner durdurulmuyordu. Sunucu oluyor,
        # arkasinda calisan her sey oyle kaliyordu.
        for pid, calisan in list(self._runtimes.items()):
            if pid in takilan:
                continue
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


# Capraz proje taramasindan okunacak kosu sutunlari. Hepsi ISTENIR ama
# yalnizca VAR OLANLAR secilir: goc kosturmadigimiz icin hic acilmamis eski
# bir veritabaninda `started_by` bulunmayabilir (goc `ProjectState.__init__`
# icinde). Sutun yoksa o projedeki her kosu adsizdir -- dogru cevap, sifir
# yazma.
_KOSU_SUTUNLARI = (
    "id", "seq", "workflow_id", "title", "title_key", "title_args",
    "goal", "status", "cost_usd", "started_at", "finished_at", "started_by",
)


# Capraz okuma ilkeleri `deerx.history` modulunde: salt okunur acilis
# (`mode=ro` + `query_only`), goc KOSTURMAMA ve "okunamayan proje listeden
# atilmaz" karari TEK YERDE durur. Burada yalnizca yerel adlar kalir --
# iki kopya olsaydi biri otekinden sessizce ayrilirdi ve ayrilan taraf
# YAZAN taraf olurdu.
_proje_db = project_db
_salt_okunur = read_only
_tabloda_var = table_columns
_proje_tara = scan_project


def _blob_akisi(db: Path, artifact_id: int, *, chunk: int | None = None) -> Iterator[bytes]:
    """Baska bir projenin ciktisini veritabanindan parca parca akitir.

    KENDI baglantisini acar; aktif projeye, `runtime()`a ve goce hic
    dokunmaz -- `_salt_okunur` ile ayni sozlesme (`mode=ro` + `query_only`),
    bir farkla: `check_same_thread=False` ZORUNLU. `iterate_in_threadpool`
    ardisik `next()` cagrilarini FARKLI is parcaciklarinda kosturur
    (OLCULDU: es zamanli alti akisin altisi "SQLite objects created in a
    thread can only be used in that same thread" ile dustu). Uretec ilk
    `next()`te acar, tuketici birakinca `finally` ile blob'u ve baglantiyi
    kapatir; yarida kesilen bir indirme dosyayi kilitli birakmaz.
    """
    boy = BLOB_PARCA if chunk is None else chunk
    conn = sqlite3.connect(
        f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0, check_same_thread=False
    )
    try:
        conn.execute("PRAGMA query_only=ON")
        blob = conn.blobopen("artifact_blobs", "data", artifact_id, readonly=True)
        try:
            while True:
                parca = blob.read(boy)
                if not parca:
                    return
                yield parca
        finally:
            blob.close()
    finally:
        conn.close()


def _ek_basligi(name: str, *, inline: bool = False) -> str:
    """`Content-Disposition` degeri; ASCII disi adlar icin RFC 5987 kopyasi.

    `FileResponse` bunu kendisi kurardi ama veritabanindan akitilan bir
    yanitta dosya yok: basligi bu katman yaziyor. Cift tirnak ve satir
    sonu ADAN ATILIR -- basliga kacislanmadan giren bir tirnak `filename`i
    erkenden bitirir ve tarayiciya baska bir ad okutur.
    """
    tur = "inline" if inline else "attachment"
    temiz = name.replace('"', "").replace("\r", "").replace("\n", "")
    try:
        temiz.encode("ascii")
    except UnicodeEncodeError:
        return f"{tur}; filename*=utf-8''{quote(temiz)}"
    return f'{tur}; filename="{temiz}"'


def _arama_ucu(content: str) -> str:
    """Arama ciktisindan CEVAPLAYAN ucun adini okur.

    `web_search` ilk satiri `# Arama: <sorgu>  (<uc>)` diye yaziyor ve o
    parantez, yapilandirilan saglayicidan farkli olabilir: "browser"
    secildiginde motoru arac seciyor (bing, ddg...), anahtarli bir uc
    dustugunde de tarayiciya dusuluyor. Ayar ekraninda "hangi saglayici
    secili" ile "hangisi cevapladi" ayni sey degil ve kullanici ikincisini
    gormeden bir degisikligin ise yarayip yaramadigini bilemez.

    Bicim tutmazsa bos doner: uydurmak, yanlis bir uc adi yazmak olurdu.
    """
    ilk = (content or "").lstrip().split("\n", 1)[0]
    if not ilk.startswith("#") or not ilk.endswith(")") or "(" not in ilk:
        return ""
    return ilk[ilk.rindex("(") + 1:-1].strip()[:40]


def _dosya_adi(name: str) -> str:
    """Proje adindan indirme dosyasi adi; yalnizca harf, rakam, `-` ve `_`.

    Calisma alani adinda bosluk, Turkce harf ya da isletim sisteminin
    kabul etmedigi bir imge olabilir. Paketleyici ayni kurali kendi
    kokunde uyguluyor (`packaging._safe_name`); kural burada ozel olarak
    tekrarlaniyor cunku web katmani boru hattinin ozel adlarina
    baglanmamali.
    """
    temiz = "".join(ch if ch.isalnum() and ch.isascii() or ch in "-_" else "-"
                    for ch in name)
    return temiz.strip("-") or "proje"


def _zaman_damgasi(path: str) -> float | None:
    """Diskteki dosyanin degistirilme zamani; dosya yoksa None.

    Bozuk ya da cok uzun bir yol icin `OSError`/`ValueError` yutulur:
    kayitli yolun okunamamasi listeyi cokertmemeli, o satir yalnizca
    zamansiz kalmali.
    """
    if not path:
        return None
    try:
        return Path(path).stat().st_mtime
    except (OSError, ValueError):
        return None


def _dosya_akisi(fp: Any, boy: int = 1024 * 1024) -> Iterator[bytes]:
    """Acik bir dosyayi parca parca akitir ve SONUNDA kapatir.

    Tuketici yarida birakirsa `finally` yine kosar: gecici zip dosyasi
    (`SpooledTemporaryFile`) diskte artik olarak kalmaz.
    """
    try:
        while True:
            parca = fp.read(boy)
            if not parca:
                return
            yield parca
    finally:
        fp.close()


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
            # Ucuz bir `which`: yalitimi secen kisi Docker'in bu makinede
            # olmadigini KAYDETMEDEN gormeli. Daemon'a sorulmaz -- o,
            # "Docker'i test et" dugmesinin isi ve saniyeler surebilir.
            "docker_found": shutil.which("docker") is not None,
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

        # Gorev sayilari ETKIN PLANA kapsanir. `counts()` proje genelini
        # sayiyor ve o ajan baglami icin dogru; ama rayda bir sayi
        # gostermek, ona tiklayinca gorulecek seyi soylemektir. Plan
        # ekrani acilista etkin plani gosteriyor: iki plani olan bir
        # projede ray "8" derken ekranda 3 gorev cikiyordu -- Ciktilar'da
        # 11/1 olarak bildirilen hatanin ayni sinifi.
        aktif_plan = orch.state.active_plan_id()
        plan_gorevleri = orch.state.list_tasks(plan_id=aktif_plan)
        counts = {
            **counts,
            "tasks": len(plan_gorevleri),
            "tasks_done": sum(1 for g in plan_gorevleri if g.status == Status.DONE),
            # Projenin tamami da lazim: plan sekmeleri bunu gosteriyor ve
            # "bu planda 3, projede 8" cumlesi kurulabilmeli.
            "tasks_all": counts["tasks"],
        }
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
        # Kullanicinin kendi dosyasi: `hesap` alanlarinin dogru "onceki"si
        # ortak `Settings` nesnesi DEGIL, bu tablodur.
        hesap_onceki = _hesap_ayarlari(request) if kisisel else {}
        # Gercekten degisenler. `temiz` her zaman kapsamin BUTUN alanlarini
        # tasir (istemci hepsini gonderiyor); diske de olay akisina da
        # yalnizca bu liste gider.
        degisen: list[tuple[str, Any, SettingField]] = []
        for name, cleaned, spec in temiz:
            # DEGISMEYEN ALAN DEGISMIS SAYILMAZ.
            #
            # Once her gelen alan `changed`e yaziliyordu: tek bir ayari
            # degistiren kullaniciya "30 alan kaydedildi" deniyor, denetim
            # gunlugune otuz ad dusuyor ve `set(changed) & MODEL_FIELDS`
            # her zaman dolu oldugu icin LLM istemcisi bos yere yeniden
            # kuruluyordu. Sirlar geri okunmadigi icin karsilastirilamaz:
            # gonderilen bir sir her zaman degisiklik sayilir.
            if not spec.secret:
                onceki = (
                    hesap_onceki.get(name, getattr(settings, name))
                    if spec.scope == "hesap" and kisisel
                    else getattr(settings, name)
                )
                if onceki == cleaned:
                    continue
            degisen.append((name, cleaned, spec))
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

        _kalici_yaz(degisen, request)

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

        # Yalitim aciliyor ama Docker yok: ayar KAYDEDILIR (kullanici
        # Docker'i sonra kurabilir; reddetmek, makineye gore degisen bir
        # ayar ekrani demektir) ve uyari yanitla birlikte doner. Sessizce
        # kaydetmek, kullaniciya "yalitilmis" rozetini gosterip kosunun
        # neden basliamadigini soylememek olurdu.
        uyarilar = []
        if changed.get("execution") == "docker" and shutil.which("docker") is None:
            uyarilar.append("sandbox.no_docker")
        return _json({"ok": True, "changed": changed, "warnings": uyarilar})

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
            profil = Path(tempfile.mkdtemp(prefix="deerx-test-"))
            session = BrowserSession(
                profile_dir=profil,
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
                # Tiklama basina bir Chrome profili birakmak, ayar
                # ekranini gecici klasor uretecine cevirir.
                shutil.rmtree(profil, ignore_errors=True)
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

        SINAMA ARACA TARAYICI VERIR. Olculdu: varsayilan saglayici
        "browser" ve `web_search` o kipte `ctx.browser` istiyor; uc bunu
        vermedigi icin dugme arama CALISIRKEN bile "Tarayici oturumu bu
        baglamda kullanilamiyor" donuyordu. Yani kurulumun calisip
        calismadigini soylemesi gereken tek yer yanlis cevap veriyordu.
        Ayni sorgu gercek bir oturumla 2,8 saniyede Bing'den uc sonuc
        donuyor.

        Kosunun paylasilan oturumu KULLANILMAZ, gecici bir oturum acilir:
        Playwright'in senkron nesneleri kendilerini olusturan is
        parcacigina baglidir ve bu istek baska bir parcacikta kosar --
        `test_browser` ayni gerekceyle ayni seyi yapiyor.

        Anahtarli bir saglayici secilmis olsa bile tarayici acilir: arac
        anahtarli uc dustugunde tarayiciya DUSUYOR ve sinama kosunun
        gercekte yapacagi seyi denemeli, yapacagini umdugumuz seyi degil.
        """
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))
        query = str(body.get("query") or "DeerX test sorgusu").strip()

        def run_probe() -> dict[str, Any]:
            import tempfile
            import time as _time

            from ..browser import BrowserSession, UrlPolicy, find_browser
            from ..tools import ToolContext, build_registry

            oturum: BrowserSession | None = None
            tarayici_notu = ""
            # Her tiklama bir profil dizini birakiyordu: sinama dugmesi
            # gecici klasoru cop olarak birakan bir dugme olamaz.
            profil = Path(tempfile.mkdtemp(prefix="deerx-arama-"))
            if find_browser(state.settings.browser_channel) is not None:
                try:
                    oturum = BrowserSession(
                        profile_dir=profil,
                        policy=UrlPolicy(),
                        channel=state.settings.browser_channel,
                        headless=state.settings.browser_headless,
                        idle_seconds=0,
                    )
                except Exception as exc:  # noqa: BLE001 - playwright kendi tiplerini kullanir
                    tarayici_notu = f"{type(exc).__name__}: {exc}"[:200]
            else:
                tarayici_notu = t("api.search_no_browser")

            basladi = _time.perf_counter()
            try:
                probe = ToolContext(
                    settings=settings, events=state.events,
                    kb=state.orchestrator.kb, state=state.orchestrator.state,
                    browser=oturum,
                )
                outcome = build_registry().execute(
                    "web_search", {"query": query, "max_results": 3}, probe
                )
            finally:
                # Her tiklamada bir Chrome birakmak, ayar ekranini surec
                # sizintisina cevirir.
                if oturum is not None:
                    try:
                        oturum.close()
                    except Exception:  # noqa: BLE001 - kapanis hatasi sinamayi dusurmesin
                        pass
                shutil.rmtree(profil, ignore_errors=True)

            return {
                "ok": not outcome.is_error,
                "provider": state.settings.search_provider,
                # Yapilandirilan saglayici ile CEVAPLAYAN ayni olmayabilir:
                # "browser" secildiginde motoru arac seciyor, anahtarli uc
                # dustugunde tarayiciya dusuluyor. Kullanici neyin
                # calistigini gormeden ayari degistirip test edemez.
                "answered_by": _arama_ucu(outcome.content),
                "seconds": round(_time.perf_counter() - basladi, 1),
                "browser_note": tarayici_notu,
                "result": outcome.content[:1200],
            }

        return _json(await asyncio.to_thread(run_probe))

    async def test_sandbox(request: Request) -> Response:
        """Kabini GERCEKTEN kurmayi dener: imaj, calisma alani, araclar.

        "Docker kurulu" demek yetmiyor: daemon yanit verse bile calisma
        alani konteynere baglanamayabilir (OLCULDU: Docker Desktop'in
        konak baglantisi koptugunda `docker info` sorunsuz, her `docker
        run` "mkdir /run/desktop/mnt/host/c: file exists"). Ayari kaydedip
        kirk dakikalik bir kosu baslattiktan sonra bunu ogrenmekle bu
        dugmeye basmak arasindaki fark, bir kosu.

        YONETICI kapisi var -- oteki `test-*` uclarindan farki bu: govdeden
        gelen imaj adiyla bir konteyner KALDIRIYOR. Kapisiz birakmak,
        izleyici rolundeki birine calisma alani bagli olarak istedigi
        imaji kosturma izni vermek olurdu. Kimlik dogrulama hic
        kurulmamissa yerel kurulum tek kisiliktir ve o kisi yoneticidir.
        """
        if not _is_admin(request):
            return _error(t("api.admin_only"), 403)
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        ayar = state.settings
        # Kaydedilmemis form degerleri de denenebilir: kullanici imaji
        # degistirip once TEST etmek ister, kaydedip kosu baslatmak degil.
        imaj = str(body.get("image") or ayar.sandbox_image).strip() or ayar.sandbox_image
        try:
            taban = int(body.get("port_base") or ayar.sandbox_port_base)
            sayi = int(body.get("port_count") or ayar.sandbox_port_count)
        except (TypeError, ValueError):
            taban, sayi = ayar.sandbox_port_base, ayar.sandbox_port_count

        from ..sandbox import Sandbox

        kabin = Sandbox(
            workspace=ayar.workspace, image=imaj,
            port_base=taban, port_count=sayi,
        )

        def probe() -> dict[str, Any]:
            import time as _time

            basladi = _time.perf_counter()
            saglik = kabin.probe(
                deep=True,
                ttl=0.0,
                node_gerekli=(ayar.workspace / "package.json").is_file(),
            )
            return {
                "ok": saglik.ok,
                "image": imaj,
                "seconds": round(_time.perf_counter() - basladi, 1),
                **saglik.to_dict(),
            }

        return _json(await asyncio.to_thread(probe))

    # ---------------------------------------------------------------- #
    # Proje hafizasi
    # ---------------------------------------------------------------- #
    # ---------------------------------------------------------------- #
    # Capraz proje etkinligi
    # ---------------------------------------------------------------- #
    def _gorulebilen_projeler(request: Request) -> list[Project]:
        """Taramanin kapsami.

        `_proje_uyesi` ve `_project_role` KULLANILMAZ: ikisi de
        `_sahipsizi_sahiplen` cagiriyor ve bir yonetici sahipsiz projeye
        dokundugunda kendini `owner` olarak YAZIYOR. Bir listeyi cizmek,
        dokundugu her projeye uyelik satiri eklemek olamaz.

        Arsivlenenler DAHIL: arsiv kaydi silmez ve orada yapilmis is hala
        kullanicinindir. Disarida birakmak toplami sessizce yanlis yapardi.
        """
        if not state.auth.is_configured:
            return state.projects.all_projects(include_archived=True)
        user = getattr(request.state, "user", None)
        if user is None:
            return []
        return state.projects.for_user(
            user.id, is_admin=bool(user.is_admin), include_archived=True
        )

    def _kim_cozumle(request: Request) -> tuple[str, bool, Response | None]:
        """`?who=` -- uc kip, tek parametre. (kim, hepsi_mi, hata) doner.

        Yonetici olmayan `all` ya da baskasinin adini yollarsa 403; sessizce
        `me`ye DUSURULMEZ: bir yoneticinin paylastigi baglanti, alan kisiye
        kendi verisini baskasinin adiyla gostermemeli.
        """
        yonetici = _is_admin(request)
        ham = (request.query_params.get("who") or "me").strip()

        if not state.auth.is_configured:
            # Makinede tek kisi var ve her sey onun; `me` sifir satir
            # donerdi cunku `_uploader` orada bos donuyor.
            return "", True, None

        me = getattr(request.state, "user", None)
        benim_adim = getattr(me, "username", "")

        if ham in ("", "me"):
            return benim_adim, False, None
        if ham == "all":
            if not yonetici:
                return "", False, _error(t("api.activity_admin_only"), 403)
            return "", True, None
        if ham == benim_adim:
            return benim_adim, False, None
        if not yonetici:
            return "", False, _error(t("api.activity_admin_only"), 403)
        return ham, False, None

    def _kosu_okuyucu(kim: str, hepsi: bool, before: float, limit: int):
        """Tek projeden kosu satirlari + ozet okuyan kapanis."""

        def oku(conn: sqlite3.Connection) -> dict[str, Any]:
            var = _tabloda_var(conn, "runs")
            if not var:
                return {"runs": [], "ozet": None}
            sutunlar = [c for c in _KOSU_SUTUNLARI if c in var]
            adli = "started_by" in var
            cikti_var = bool(_tabloda_var(conn, "artifacts"))

            secim = ", ".join(f"r.{c}" for c in sutunlar)
            if cikti_var:
                secim += (
                    ", (SELECT COUNT(*) FROM artifacts a WHERE a.run_id = r.id)"
                    " AS cikti_sayisi"
                )
            kosul = ["r.started_at < ?"]
            param: list[Any] = [before]
            if not hepsi:
                if not adli:
                    # Sutun yok: bu projede kimse atfedilemez.
                    return {"runs": [], "ozet": _ozet(conn, var, kim, cikti_var)}
                kosul.append("r.started_by = ?")
                param.append(kim)
            param.append(limit)

            satirlar = conn.execute(
                f"SELECT {secim} FROM runs r WHERE {' AND '.join(kosul)} "
                f"ORDER BY r.started_at DESC LIMIT ?",
                param,
            ).fetchall()
            return {
                "runs": [dict(r) for r in satirlar],
                "ozet": _ozet(conn, var, kim, cikti_var),
            }

        def _ozet(conn, var, kim, cikti_var) -> dict[str, Any]:
            adli = "started_by" in var
            if adli:
                satir = conn.execute(
                    "SELECT COUNT(*) AS toplam,"
                    " SUM(CASE WHEN started_by = '' THEN 1 ELSE 0 END) AS adsiz,"
                    " SUM(CASE WHEN started_by = ? THEN 1 ELSE 0 END) AS benim,"
                    " SUM(CASE WHEN started_by = ? THEN cost_usd ELSE 0 END)"
                    "   AS benim_maliyet,"
                    " MAX(started_at) AS son FROM runs",
                    (kim, kim),
                ).fetchone()
            else:
                satir = conn.execute(
                    "SELECT COUNT(*) AS toplam, COUNT(*) AS adsiz, 0 AS benim,"
                    " 0 AS benim_maliyet, MAX(started_at) AS son FROM runs"
                ).fetchone()
            return {
                "total": int(satir["toplam"] or 0),
                "unattributed": int(satir["adsiz"] or 0),
                "mine": int(satir["benim"] or 0),
                "cost_mine": round(float(satir["benim_maliyet"] or 0.0), 4),
                "last_at": satir["son"],
            }

        return oku

    def _capraz_tara(request: Request, okuyucu) -> tuple[list, list, int]:
        """Gorulebilen her projeyi salt okunur tarar.

        (proje_ozetleri, ham_sonuclar, okunamayan) doner. Yaniti hicbir
        hata dusurmez.
        """
        ozetler: list[dict[str, Any]] = []
        sonuclar: list[tuple[Project, Any]] = []
        okunamayan = 0

        for proje in _gorulebilen_projeler(request):
            temel = {
                "id": proje.id, "slug": proje.slug, "name": proje.name,
                "archived": bool(proje.archived), "role": proje.role,
            }
            db = _proje_db(proje.path)
            if db is None:
                ozetler.append({**temel, "status": "empty", "total": 0,
                                "mine": 0, "unattributed": 0, "cost_mine": 0.0,
                                "last_at": None})
                continue

            sonuc, durum = _proje_tara(db, okuyucu)
            if durum != "ok" or sonuc is None:
                okunamayan += 1
                ozetler.append({**temel, "status": "unreadable", "total": 0,
                                "mine": 0, "unattributed": 0, "cost_mine": 0.0,
                                "last_at": None})
                continue

            ozet = sonuc.get("ozet") or {
                "total": 0, "mine": 0, "unattributed": 0,
                "cost_mine": 0.0, "last_at": None,
            }
            ozetler.append({**temel, "status": "ok", **ozet})
            sonuclar.append((proje, sonuc))

        return ozetler, sonuclar, okunamayan

    async def activity_runs(request: Request) -> Response:
        """Kullanicinin (ya da herkesin) BUTUN projelerdeki kosulari.

        Sayfalama ZAMAN DAMGASI IMLECIYLE: N bagimsiz sirali listede ofset
        yanlistir -- sayfa degistikce satir atlar ve tekrarlar. Her
        projeden `LIMIT` alinip birlestirilir; kuresel ilk N'e girecek bir
        satir kendi projesinin ilk N'inde olmak zorundadir.
        """
        kim, hepsi, hata = _kim_cozumle(request)
        if hata is not None:
            return hata

        try:
            limit = max(1, min(200, int(request.query_params.get("limit", 50))))
        except ValueError:
            limit = 50
        try:
            before = float(request.query_params.get("before", "") or time.time() + 1)
        except ValueError:
            before = time.time() + 1

        okuyucu = _kosu_okuyucu(kim, hepsi, before, limit)
        ozetler, sonuclar, okunamayan = await asyncio.to_thread(
            _capraz_tara, request, okuyucu
        )

        satirlar: list[dict[str, Any]] = []
        for proje, sonuc in sonuclar:
            for ham in sonuc["runs"]:
                basladi = float(ham.get("started_at") or 0.0)
                bitti = ham.get("finished_at")
                satirlar.append({
                    "project_id": proje.id,
                    "project_slug": proje.slug,
                    "project_name": proje.name,
                    "project_archived": bool(proje.archived),
                    "id": ham.get("id", ""),
                    "seq": ham.get("seq"),
                    "workflow_id": ham.get("workflow_id", ""),
                    "title": ham.get("title", ""),
                    "title_key": ham.get("title_key", ""),
                    "title_args": json.loads(ham.get("title_args") or "{}"),
                    "goal": ham.get("goal", ""),
                    "status": ham.get("status", ""),
                    "started_by": ham.get("started_by", ""),
                    "cost": round(float(ham.get("cost_usd") or 0.0), 4),
                    "started_at": basladi,
                    "finished_at": bitti,
                    "elapsed": round((bitti or time.time()) - basladi, 1),
                    "artifacts": int(ham.get("cikti_sayisi") or 0),
                })

        satirlar.sort(key=lambda r: r["started_at"], reverse=True)
        kirpilmis = satirlar[:limit]
        return _json({
            "who": "all" if hepsi else kim,
            "can_see_everyone": _is_admin(request),
            "runs": kirpilmis,
            "projects": ozetler,
            "next_before": kirpilmis[-1]["started_at"] if len(satirlar) > limit else None,
            "unreadable": okunamayan,
        })

    async def activity_artifacts(request: Request) -> Response:
        """Ayni tarama, cikti yuku.

        `exists` DONMEZ ve HICBIR `stat()` cagrilmaz: proje ici islerici
        cikti basina diske bakiyor ve N projede bu binlerce dosya sistemi
        cagrisi demek -- erisilemeyen bir yolda takilir. `bytes` artik
        DONER cunku kaynagi disk degil, `artifact_blobs` satiri: boyut
        SQL'in icinden gelir ve hicbir seye dokunmaz.

        `stored` ayni sorgudan cikar ve ekranin dogru soylemesini saglar:
        blob'u olmayan bir cikti listede GORUNUR ama indirme baglantisi
        tasimaz -- "yok" demek yerine "projeye gecince" demek.
        """
        kim, hepsi, hata = _kim_cozumle(request)
        if hata is not None:
            return hata

        def oku(conn: sqlite3.Connection) -> dict[str, Any]:
            var_runs = _tabloda_var(conn, "runs")
            var_art = _tabloda_var(conn, "artifacts")
            if not var_art:
                return {"gruplar": [], "ozet": None, "toplam": 0}
            adli = "started_by" in var_runs
            if not hepsi and not adli:
                return {"gruplar": [], "ozet": None, "toplam": 0}
            # Blob tablosu bu projede henuz olmayabilir (hic acilmamis eski
            # bir veritabani). JOIN'i kosulsuz yazmak "no such table" ile
            # butun projeyi "okunamadi" yapardi; yoklugu bir sutun degeri.
            var_blob = bool(_tabloda_var(conn, "artifact_blobs"))
            # Blob tablosu yoksa bu projede ALINABILIR cikti da yok:
            # gorunurluk kurali baytlari sart kosuyor. Bos donmek, olmayan
            # bir tabloya JOIN atip butun projeyi "okunamadi" yapmaktan
            # dogru.
            if not var_blob or not _tabloda_var(conn, "workflows"):
                return {"gruplar": [], "ozet": None, "toplam": 0}
            blob_secim = (
                " b.bytes AS blob_bytes, b.sha256 AS blob_sha256"
                if var_blob else " NULL AS blob_bytes, '' AS blob_sha256"
            )
            blob_join = (
                " LEFT JOIN artifact_blobs b ON b.artifact_id = a.id" if var_blob else ""
            )

            # Gorunurluk kurali proje ici listeyle AYNI: cikti bir IS
            # AKISINA bagli olmali (INNER JOIN runs + workflows) ve
            # baytlarina ulasilabilmeli. Capraz listede "alinabilir"
            # yalnizca blob demektir -- baska bir projenin diskine
            # dokunmuyoruz ve orada dosya var mi bilemeyiz.
            #
            # Bu ayni zamanda kosusuz ciktilar sorusunu da kapatir: akisi
            # olmayan satir hicbir kipte gorunmez, dolayisiyla "kim
            # baslatti" cevapsizken kimseye atfedilmis olmaz.
            kosul = "" if hepsi else " AND r.started_by = ?"
            param = () if hepsi else (kim,)
            satirlar = conn.execute(
                "SELECT a.id AS artifact_id, a.name, a.kind, a.phase, a.run_id,"
                " r.seq, r.title, r.title_key, r.title_args, r.goal,"
                " r.started_at, " + ("r.started_by" if adli else "'' AS started_by") +
                "," + blob_secim +
                " FROM artifacts a JOIN runs r ON r.id = a.run_id"
                " JOIN workflows w ON w.id = r.workflow_id" + blob_join +
                " WHERE b.artifact_id IS NOT NULL" + kosul +
                " ORDER BY r.started_at DESC, a.name",
                param,
            ).fetchall()

            gruplar: dict[str, dict[str, Any]] = {}
            for r in satirlar:
                anahtar = r["run_id"] if r["seq"] is not None else ""
                g = gruplar.setdefault(anahtar, {
                    "run_id": anahtar, "seq": r["seq"],
                    "title": r["title"] or "", "title_key": r["title_key"] or "",
                    "title_args": json.loads(r["title_args"] or "{}"),
                    "goal": r["goal"] or "",
                    "started_at": float(r["started_at"] or 0.0),
                    "started_by": r["started_by"] or "",
                    "items": [],
                })
                saklandi = r["blob_bytes"] is not None
                g["items"].append({
                    "name": r["name"], "kind": r["kind"], "phase": r["phase"] or "",
                    "format": artifact_format(r["name"]),
                    "stored": saklandi,
                    "bytes": int(r["blob_bytes"]) if saklandi else 0,
                    "sha256": r["blob_sha256"] if saklandi else "",
                })
            return {
                "gruplar": list(gruplar.values()),
                "ozet": None,
                "toplam": len(satirlar),
            }

        ozetler, sonuclar, okunamayan = await asyncio.to_thread(
            _capraz_tara, request, oku
        )

        projeler = []
        toplam = 0
        for proje, sonuc in sonuclar:
            if not sonuc["gruplar"]:
                continue
            toplam += sonuc["toplam"]
            # Indirme adresi ancak burada kurulabilir: proje kimligini
            # okuyucu bilmiyor (salt okunur baglanti yalnizca o projenin
            # dosyasini goruyor). Blob'u olmayan satirda adres BOS kalir --
            # tiklanabilir ama 404 donen bir baglanti vermek, olmayan bir
            # sey vaat etmektir.
            for grup in sonuc["gruplar"]:
                for oge in grup["items"]:
                    oge["download"] = (
                        f"/api/activity/artifacts/{quote(str(proje.id))}"
                        f"/{quote(oge['name'])}/download"
                        if oge["stored"] else ""
                    )
            projeler.append({
                "id": proje.id, "slug": proje.slug, "name": proje.name,
                "archived": bool(proje.archived),
                "total": sonuc["toplam"], "runs": sonuc["gruplar"],
            })

        return _json({
            "who": "all" if hepsi else kim,
            "can_see_everyone": _is_admin(request),
            "projects": projeler,
            "total": toplam,
            "unreadable": okunamayan,
        })

    async def activity_artifact_download(request: Request) -> Response:
        """Baska bir projenin ciktisini SALT OKUNUR baglantiyla indirir.

        Uc kural burada birlikte duruyor:

        1. Proje `_gorulebilen_projeler` icinde degilse 404 -- 403 DEGIL:
           "yetkin yok" demek, projenin VAR OLDUGUNU soylemektir. Uye
           olmayan biri baska bir ekibin proje kimliklerini tek tek
           deneyerek varlik listesi cikaramamali.
        2. `?who=` kapsami listeyle AYNI: adli kipte satirin kosusu
           isteyene ait olmali. Aksi halde liste gostermedigi bir ciktiyi
           adres tahmin ederek indirmek mumkun olurdu.
        3. Yalnizca veritabani. Disk yolu HIC acilmaz: baska projenin
           yolu erisilemez olabilir (agdaki surucu, cikarilmis disk) ve
           orada takilan bir istek bu sunucunun is parcacigini tutar.
        """
        kim, hepsi, hata = _kim_cozumle(request)
        if hata is not None:
            return hata

        proje_id = request.path_params["project_id"]
        name = request.path_params["name"]
        proje = next(
            (p for p in _gorulebilen_projeler(request) if str(p.id) == str(proje_id)),
            None,
        )
        if proje is None:
            return _error(t("api.not_found", name=name), 404)
        db = _proje_db(proje.path)
        if db is None:
            return _error(t("api.artifact_not_stored", name=name), 404)

        def ara() -> dict[str, Any] | None:
            def oku(conn: sqlite3.Connection) -> dict[str, Any] | None:
                if not _tabloda_var(conn, "artifact_blobs"):
                    return None
                adli = "started_by" in _tabloda_var(conn, "runs")
                # Gorunurluk kurali LISTEYLE ayni: is akisina bagli ve
                # blob'u olan. Aksi halde listede hic gorunmeyen bir cikti
                # adresi tahmin edilerek indirilebilirdi -- eleme kurali
                # yalnizca cizime uygulanmis, yetkiye uygulanmamis olurdu.
                row = conn.execute(
                    "SELECT b.artifact_id, b.bytes, b.sha256, b.media_type, "
                    + ("r.started_by" if adli else "'' AS started_by") +
                    " FROM artifacts a JOIN artifact_blobs b ON b.artifact_id = a.id"
                    " JOIN runs r ON r.id = a.run_id"
                    " JOIN workflows w ON w.id = r.workflow_id"
                    " WHERE a.name = ?",
                    (name,),
                ).fetchone()
                if row is None:
                    return None
                if not hepsi and (row["started_by"] or "") != kim:
                    return None
                return {
                    "artifact_id": int(row["artifact_id"]),
                    "bytes": int(row["bytes"]),
                    "sha256": row["sha256"] or "",
                    "media_type": row["media_type"] or "application/octet-stream",
                }

            sonuc, _durum = _proje_tara(db, oku)
            return sonuc

        kayit = await asyncio.to_thread(ara)
        if kayit is None:
            return _error(t("api.artifact_not_stored", name=name), 404)

        return StreamingResponse(
            iterate_in_threadpool(_blob_akisi(db, kayit["artifact_id"])),
            # Capraz kipte satir ici gosterim YOK: baska bir projenin
            # uretmis oldugu bir dosyayi bu uygulamanin kaynaginda cizmek
            # yeni bir yetki yuzeyi acardi. Her sey ek dosya olarak iner.
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(kayit["bytes"]),
                "Content-Disposition": _ek_basligi(name),
                "Cache-Control": "no-store",
                "ETag": f'"{kayit["sha256"]}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

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
            # Bagimlilik cozumu PROJE CAPINDA yapilir: bir planin gorevi
            # baska bir planin gorevini bekleyebilir. Istemcinin elinde
            # yalnizca secili planin gorevleri var, o yuzden "neyi
            # bekliyor" burada hesaplanir.
            tum = {t.key: t for t in project.list_tasks()}
            biten = {k for k, t in tum.items() if t.status == Status.DONE}
            for task in payload:
                task["ready"] = task["key"] in ready
                bekleyen = [d for d in task.get("deps", []) if d not in biten]
                task["waiting_on"] = bekleyen
                # "T-014 bekliyor" ile "T-014 YOK" ayni sey degil: silinen
                # bir plan, baska planlardaki gorevlerin `deps` listesinde
                # olu anahtarlar birakabiliyordu ve o gorevler sessizce
                # sonsuza dek hazir olmuyordu.
                task["missing_deps"] = [d for d in bekleyen if d not in tum]
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
        removed, cleaned = project.delete_plan(plan_id)
        state.runner.emit("warn", "plan", t("api.plan_deleted", count=removed))
        if cleaned:
            # Sessiz bir veri degisikligi olmasin: baska planlardaki olu
            # bagimlilik anahtarlari temizlendi ve kac gorevi etkiledigi
            # soylenir.
            state.runner.emit(
                "warn", "plan", t("api.plan_deps_cleaned", count=cleaned)
            )
        return _json(
            {"ok": True, "removed_tasks": removed, "cleaned_deps": cleaned}
        )


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

    def _proje_uyesi(request: Request, proje: Project) -> bool:
        """Projeyi kim OKUYABILIR: uyesi ya da platform yoneticisi.

        Sahipsiz projeyi acan yonetici burada da sahiplenir; bu cagri
        eskiden `_proje_yonetebilir` icinde gizliydi ve uye listesini
        okumak onu tetikliyordu. Yer degistirdi, davranis degismedi:
        yoneticinin uyelik satiri olmadan liste BOS gorunur ve rolunu
        kimseye devredemez.
        """
        if not state.auth.is_configured:
            return True
        user = getattr(request.state, "user", None)
        if user is None:
            return False
        if user.is_admin:
            _sahipsizi_sahiplen(proje, user)
            return True
        return bool(state.projects.role_of(proje.id, user.id))

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
            # Konak kipinde kabin YOKTUR; "kurulmamis" demek sanki bir
            # eksiklik varmis gibi okunuyordu. Kendi sozcugu var.
            "status": "host",
            "name": "",
            "problems": [],
            "warnings": [],
            "tools": {},
            "deep": False,
            "checked_at": 0.0,
        }
        if ayar.execution == "docker":
            from ..sandbox import Sandbox

            olcek = Sandbox(
                workspace=ayar.workspace, image=ayar.sandbox_image,
                port_base=ayar.sandbox_port_base,
                port_count=ayar.sandbox_port_count,
            )
            kabin["name"] = olcek.name
            # Ekran eskiden yalnizca `docker inspect` durumunu yaziyordu:
            # "kurulmamis" hem "docker yok" hem "henuz kurulmadi" hem de
            # "calisma alani baglanamiyor" icin ayni cevapti ve kullanici
            # sebebi ancak kosunun ilk komutunda, olay akisinin ortasinda
            # goruyordu. Yoklama sebebi ADLANDIRIYOR.
            #
            # Sig yoklama varsayilan (~100 ms). Derin yoklama gercek bir
            # konteyner kaldirir ve YALNIZCA `?probe=1` ile kosar: her
            # ekran acilisinda kosaydi, tam da teshis etmesi gereken
            # arizada (kopuk baglanti) bir dakika asili kalirdi.
            derin = request.query_params.get("probe") == "1"
            node_gerekli = (ayar.workspace / "package.json").is_file()
            if derin:
                # Kullanici "Sagligi olc" dedi: onbellek atlanir, yoksa
                # dugme az once olculmus bir sonucu geri gosterir ve
                # hicbir sey yapmamis gibi gorunur.
                saglik = await asyncio.to_thread(
                    olcek.probe, deep=True, ttl=0.0, node_gerekli=node_gerekli
                )
            else:
                saglik = await asyncio.to_thread(
                    olcek.probe, node_gerekli=node_gerekli
                )
            kabin.update(saglik.to_dict())

        servisler = calisan.orchestrator.services.describe_all()
        return _json({
            "project": proje.to_dict(),
            "ports": {
                "base": ayar.sandbox_port_base,
                "count": ayar.sandbox_port_count,
                "last": ayar.sandbox_port_base + ayar.sandbox_port_count - 1,
                # Aralik YALNIZCA yalitilmis kipte uygulanir. Konak
                # kipinde de gostermek "portlarim kisitli" izlenimi
                # veriyordu; ekran bunu artik kelimeyle ayiriyor.
                "enforced": ayar.execution == "docker",
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
        # OKUMAK uyelik ister, yonetmek sahiplik. Kapali oldugu surece
        # "kosuyu ayse baslatti" satirindaki ayse'nin kim oldugunu
        # okuyabilecegi hicbir yer yoktu; uyelik bir sir degil.
        #
        # UYELIK YINE DE SORULUR: `_proje_veya_hata` yalnizca projenin
        # var olup olmadigina bakiyor. Bu satir olmadan uye OLMAYAN biri
        # de listeyi okurdu -- kapiyi genisletmek onu acmak degil.
        if not _proje_uyesi(request, proje):
            return _error(t("project.needs_role", role="viewer"), 403)

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
        # `or None` YOK: bos liste "hicbir tur" demek. Dort tur cipinin
        # dordunu de kapatan kullanici bos liste gonderiyor ve suzgec
        # dusuyordu -- kapattigi turler dahil her sey donuyordu.
        ham_kinds = body.get("kinds")
        kinds = None if ham_kinds is None else list(ham_kinds)
        try:
            limit = max(1, min(int(body.get("k", 8) or 8), 30))
        except (TypeError, ValueError):
            return _error("k sayisal olmali.")

        def run_search() -> list[dict[str, Any]]:
            # TANI KIPI. Ekrandaki arama kutusuna cogu zaman "bu neden
            # bulunmuyor?" diye gelinir ve sifir sonuc bu soruya cevap
            # vermez. Pasif belgeler de doner, ISARETLI olarak: ekran
            # onlari soluk gosterip sebebini soyler.
            #
            # Ajan yolu (`tools/knowledge.py`) bunu ACMAZ: orada pasif
            # belge hic gorunmez.
            hits = state.orchestrator.kb.search(
                query, k=limit, kinds=kinds, include_inactive=True)
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
                    "is_active": h.is_active,
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
            removed, dosya_silindi = kb.forget(source, remove_file=True)
            state.runner.emit(
                "tool", "rag", t("api.removed_chunks", source=source, count=removed)
            )
            # Dosya alan disindaysa yerinde birakildi ve bu SOYLENIR:
            # sessizce birakmak, "Kalici sil" diyen bir dugmenin ikinci
            # bir yalani olurdu.
            if not dosya_silindi:
                state.runner.emit(
                    "warn", "rag", t("api.file_kept_outside", source=source)
                )
            _audit(request, "knowledge.delete", detail=source)
            return _json({"ok": True, "mode": mode, "removed_chunks": removed,
                          "file_removed": dosya_silindi})

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

    async def ingest_docs(request: Request) -> Response:
        """`docs/` altinda ZATEN duran dosyalari indeksler.

        Arayuzde belge eklemenin tek yolu isletim sisteminin dosya
        penceresiydi ve o pencere tek arizali kapiydi: konak makinede
        takildiginda (agdaki bir surucu, bulut kabuk eklentisi, pencerenin
        arkada acilmasi) kullanicinin sartnameyi indeksleyecek baska
        hicbir yolu kalmiyordu -- `deerx ingest` icin terminale gitmek
        disinda.

        Ustelik o pencere cogu zaman ayni dosyayi ayni yere geri
        yaziyordu: yukleme hedefi `<calisma alani>/docs/` ve kullanicinin
        sectigi dosya cogunlukla zaten oradaydi. Bu uc, yazmayi hic
        yapmadan yalnizca indeksler.

        `force` verilmezse degismemis dosyalar atlanir (`_index` icerik
        ozetine bakar); ikinci bir tiklama bos is yapmaz.
        """
        denied = _require_role(request, "developer")
        if denied is not None:
            return denied
        if state.runner.is_running:
            return _error(t("api.upload_locked"), 409)
        try:
            body = await _body(request)
        except DeerXError as exc:
            return _error(str(exc))

        docs_dir = state.settings.workspace / "docs"
        if not docs_dir.is_dir():
            return _error(t("api.no_docs_dir", path=docs_dir), 404)
        force = bool(body.get("force", False))
        kim = _uploader(request)

        def tara() -> dict[str, Any]:
            kb = state.orchestrator.kb
            sonuclar = []
            for yol in sorted(docs_dir.rglob("*")):
                if not yol.is_file() or yol.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                sonuc = kb.ingest_file(yol, force=force, uploaded_by=kim)
                sonuclar.append({
                    "name": yol.name,
                    "ok": sonuc.ok,
                    "chunks": sonuc.chunks,
                    "error": sonuc.error,
                })
            return {
                "ok": True,
                "files": sonuclar,
                "indexed": sum(1 for s in sonuclar if s["ok"] and s["chunks"]),
                "skipped": sum(1 for s in sonuclar if s["ok"] and not s["chunks"]),
                "failed": [s["name"] for s in sonuclar if not s["ok"]],
                "stats": kb.stats(),
            }

        outcome = await asyncio.to_thread(tara)
        _audit(
            request, "knowledge.ingest_docs",
            detail=f"{outcome['indexed']}/{len(outcome['files'])}",
        )
        state.runner.emit(
            "tool", t("actor.upload"),
            t("api.docs_indexed", indexed=outcome["indexed"],
              total=len(outcome["files"])),
        )
        return _json(outcome)

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

        project = state.orchestrator.state
        readiness = check_readiness(project)
        # Liste artik `deliveries/` dizininden DEGIL proje hafizasindan
        # kurulur. Dizini taramanin iki yanlisi vardi: dizine elle atilmis
        # bir zip paket gibi listeleniyor (raporu yok, indirilince rastgele
        # bir dosya iniyor), `--output` ile baska bir yere yazilan gercek
        # paket ise hic gorunmuyordu. Kayit tek gercek kaynak; siralama da
        # dosya zaman damgasindan degil kayit sirasindan gelir.
        infos = [i for i in project.list_artifact_infos(check_disk=False)
                 if i.kind == "package"]
        satirlar = []
        for info in reversed(infos[-10:]):
            boyut = info.bytes if info.stored else project.artifact_size(info)
            satirlar.append(
                {
                    "name": info.name,
                    "bytes": boyut or 0,
                    # Zaman damgasi yalnizca diskteki dosyadan okunabiliyor;
                    # veritabaninda duran bir paket icin uydurulmaz.
                    "created_at": _zaman_damgasi(info.path),
                    # Raporu okuyabilmek icin baytlara ulasabilmek gerekir:
                    # kaydi olup baytlari gitmis bir paketin raporu yok.
                    "has_report": boyut is not None,
                }
            )
        return _json(
            {
                "ready": readiness.ok,
                "blockers": [i.message for i in readiness.blockers],
                "warnings": [i.message for i in readiness.warnings],
                "packages": satirlar,
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

        from ..pipeline.packaging import (
            PackagingError,
            PackagingNotReady,
            package_with_run,
        )

        force = bool(body.get("force", False))

        def run_build() -> dict[str, Any]:
            # Elle paketleme de tek adimli bir kosudur; kaydi `package_with_run`
            # aciyor. Web, CLI ve MCP ayni yardimciyi cagirir, yoksa "elle
            # paketleme kosu kaydi olusturur" cumlesi yalnizca web icin
            # dogru kalirdi. Artifakt kaydini `build_package` yapar.
            project = state.orchestrator.state
            result, run_id, seq = package_with_run(
                project,
                state.settings.workspace,
                state.settings.deliveries_dir,
                goal=project.get_meta("goal", ""),
                force=force,
            )
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

        Iki kosulu saglayan gorunur: baytlarina bir yerden ULASILABILIR
        olmali ve bagli oldugu kosunun bir IS AKISI numarasi olmali.
        Alinamayan bir satir yalnizca bir addir ve tiklayinca 404 verir;
        numarasi olmayan bir cikti "bu nereden cikti" sorusunu
        cevaplayamaz.

        Gizlemenin bir bedeli var ve bu depo onu bir kez odedi: kosusuz
        ciktilar gizlendiginde rozet "11" derken ekranda tek bir cikti
        goruluyordu ve sahibi bunu ariza olarak bildirdi. Bu yuzden sayan
        ve listeleyen AYNI kurali kullanir (`visible_artifact_infos`) ve
        `hidden` alani kac satirin elendigini SOYLER -- gizlemek sessizce
        yok saymak degildir.
        """
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied

        def topla() -> dict[str, Any]:
            project = state.orchestrator.state
            runs = {r["id"]: r for r in project.list_runs(200)}
            # Kosu bir IS AKISININ adimi; cikti da o is akisina aittir. Numara
            # tek tek sorulmuyor: is akislari bir kez okunup eslesme kuruluyor,
            # aksi halde her cikti icin ayri bir sorgu giderdi.
            akislar = {w["id"]: w["seq"] for w in project.list_workflows(200)}
            groups: dict[str, dict[str, Any]] = {}
            # Gorunurluk kurali TEK YERDE: `visible_artifact_infos` hem
            # alinabilirligi hem is akisi bagini uygular ve diske yalnizca
            # blob'u olmayan satirlar icin iner. Elenenler sayilir; sayiyi
            # ekrana tasimak, gizlemenin sessiz olmamasi icin.
            gorunur = project.visible_artifact_infos()
            hidden = project.artifact_count() - len(gorunur)
            for info in gorunur:
                boyut = info.bytes if info.stored else project.artifact_size(info)
                run = runs.get(info.run_id)
                group = groups.setdefault(
                    info.run_id,
                    {
                        "run_id": info.run_id,
                        "seq": run["seq"] if run else None,
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
                        "name": info.name,
                        "kind": info.kind,
                        "summary": info.summary,
                        "phase": info.phase,
                        "phase_label": (
                            Phase(info.phase).label if info.phase in _PHASE_NAMES else ""
                        ),
                        "run_id": info.run_id,
                        # `exists` artik "diskte duruyor mu" degil "bir yerden
                        # INDIRILEBILIR mi": baytlari veritabaninda olan bir
                        # cikti, diskteki dosyasi silinmis olsa da vardir.
                        "exists": boyut is not None,
                        "stored": info.stored,
                        "bytes": boyut or 0,
                        "sha256": info.sha256,
                        # Indirme adresi satirda GELIR: arayuz onu kurmak icin
                        # ad kacislama kurallarini ikinci kez bilmek zorunda
                        # kalmasin.
                        "download": f"/api/artifacts/{quote(info.name)}/download",
                        "format": _artifact_format(info.name),
                    }
                )

            # En yeni kosu basta.
            ordered = sorted(
                groups.values(),
                key=lambda g: (g["seq"] is None, -(g["seq"] or 0)),
            )
            total = sum(len(g["items"]) for g in ordered)
            return {"groups": ordered, "total": total, "hidden": hidden}

        # Liste bir dizi SQLite sorgusu; olay dongusunu bloklamasin diye
        # is parcaciginda kosar. `ProjectState` baglantiyi is parcacigi
        # basina acar, paylasmaz -- bu cagri o yuzden guvenli.
        return _json(await asyncio.to_thread(topla))

    async def artifact_detail(request: Request) -> Response:
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        name = request.path_params["name"]
        project = state.orchestrator.state
        info = project.artifact_info(name, check_disk=False)
        if info is None:
            return _error(t("api.not_found", name=name), 404)
        # Okuma sirasi: once veritabani, sonra disk, sonra yok. Baytlarina
        # hicbir yerden ulasilamayan bir cikti "listede ama indirilemez"
        # olur ve detay 404 verir -- kayit yok demek DEGIL, icerik yok demek.
        boyut = info.bytes if info.stored else project.artifact_size(info)
        if boyut is None:
            return _error(t("api.file_missing", path=info.path), 404)

        fmt = _artifact_format(name)
        indirme = f"/api/artifacts/{quote(name)}/download"
        payload: dict[str, Any] = {
            "name": name,
            "kind": info.kind,
            "summary": info.summary,
            "format": fmt,
            # `path` KALIR: disk yolunu okuyan harici tuketiciler var ve
            # blob'a gecmek onlari kirmamali. Yaninda artik icerigin nerede
            # durdugu da soyleniyor.
            "path": info.path,
            "bytes": boyut,
            "stored": info.stored,
            "sha256": info.sha256,
            # Indirme baglantisi HER bicimde doner. Metin ciktilarinda yoktu
            # ve bir raporu dosya olarak almanin hicbir yolu bulunmuyordu.
            "download": indirme,
        }

        if fmt == "archive":
            # Zip metin degildir: ham icerigi gondermek anlamsiz karakter yigini
            # olur. Yerine indirme baglantisi + icindeki teslimat raporu doner.
            from ..pipeline.packaging import list_entries, read_manifest

            kaynak = project.open_artifact(info)
            if kaynak is None:
                return _error(t("api.file_missing", path=info.path), 404)
            # Zip diske acilmadan, acik dosya uzerinden okunur: veritabanindaki
            # kopya icin gecici dosya yazmak 250 MB'lik bir paketi diske iki
            # kez dolasmak olurdu. Ikinci okuma icin basa sarilir.
            with kaynak:
                entries = list_entries(kaynak)
                kaynak.seek(0)
                report = read_manifest(kaynak)
            payload.update(
                {
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
            payload["src"] = f"{indirme}?inline=1"
            return _json(payload)

        if fmt == "binary":
            return _json(payload)

        ham = project.artifact_bytes(info)
        if ham is None:
            return _error(t("api.file_missing", path=info.path), 404)
        raw = ham.decode("utf-8", errors="replace")
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
        project = state.orchestrator.state
        info = project.artifact_info(name, check_disk=False)
        if info is None:
            return _error(t("api.not_found", name=name), 404)

        # `?inline=1` yalnizca tarama goruntuleri icin gecerli: sayfa onlari
        # `<img>` ile cizer. Baska her sey `octet-stream` olarak iner --
        # ajanin urettigi bir dosyayi tarayiciya "bunu goster" diye vermek
        # onu uygulamanin kaynaginda calistirmak olurdu. Karar ADIN
        # uzantisindan verilir, diskteki dosyadan degil: blob'dan sunulan
        # bir goruntunun diskte karsiligi olmayabilir.
        media = None
        if request.query_params.get("inline") == "1":
            media = IMAGE_MEDIA_TYPES.get(Path(name).suffix.lower())

        if info.stored:
            # Sunum veritabanindan. `ETag` saglamanin kendisi: ayni adla
            # yeniden yazilan bir cikti yeni bir saglama alir, tarayicinin
            # elindeki kopya kendiliginden gecersizlesir.
            etiket = f'"{info.sha256}"'
            if request.headers.get("if-none-match") == etiket:
                return Response(
                    status_code=304,
                    headers={"ETag": etiket, "Cache-Control": "no-store"},
                )
            basliklar = {
                "Content-Length": str(info.bytes),
                "Cache-Control": "no-store",
                "ETag": etiket,
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": _ek_basligi(name, inline=media is not None),
            }
            if media is not None:
                basliklar["Content-Security-Policy"] = "default-src 'none'; sandbox"
            # Akitma: 250 MB'lik bir paket icin butun baytlari bellege almak
            # yerine parca parca gecer. Uretec `iterate_in_threadpool` ile
            # kosar ve kendi salt-okunur baglantisini tasir.
            return StreamingResponse(
                iterate_in_threadpool(project.iter_artifact_bytes(info)),
                media_type=media or "application/octet-stream",
                headers=basliklar,
            )

        # Blob yok: eski kayit, yalnizca diskten sunulabilir.
        path = Path(info.path)
        if not path.is_file():
            return _error(t("api.file_missing", path=path), 404)
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

    async def artifacts_zip(request: Request) -> Response:
        """Butun ciktilari tek bir zip olarak indirir; `?run_id=` suzer.

        Tek tek indirmek bir kosunun on ciktisi icin on tiklama demekti ve
        ekranda toplu indirmenin hicbir karsiligi yoktu. Paket BELLEGE
        kurulmaz: `SpooledTemporaryFile` kucuk toplamlari bellekte tutar,
        esigi asan toplami diske tasir -- yuz megabaytlik bir teslimatin
        sunucuyu sismesi boyle onlenir.
        """
        denied = _require_role(request, "viewer")
        if denied is not None:
            return denied
        run_id = request.query_params.get("run_id") or None

        def paketle() -> tuple[str, Any, int, str]:
            import shutil
            import tempfile
            import zipfile

            project = state.orchestrator.state
            secili = []
            toplam = 0
            for info in project.list_artifact_infos(run_id=run_id, check_disk=False):
                boyut = info.bytes if info.stored else project.artifact_size(info)
                if boyut is None:
                    # Baytlari hicbir yerde olmayan cikti pakete GIRMEZ;
                    # icinde sifir baytlik bir dosya cikan bir zip,
                    # kullaniciya eksigi gizlemek olurdu.
                    continue
                toplam += boyut
                secili.append(info)
            if not secili:
                return "empty", None, 0, ""
            if toplam > ARTIFACT_MAX_BYTES:
                return "too_big", None, toplam, ""

            # Ad kosuyla birlikte anlam kazanir: ayni projeden inen iki zip
            # ayni adi tasirsa kullanici hangisinin hangi kosu oldugunu
            # indirme klasorunde ayirt edemez.
            seq = None
            if run_id is not None:
                seq = next(
                    (r["seq"] for r in project.list_runs(200) if r["id"] == run_id), None
                )
            kok = _dosya_adi(state.settings.workspace.name)
            ad = f"{kok}-ciktilar{'' if seq is None else f'-{seq}'}.zip"

            fp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
            with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED) as zf:
                for info in secili:
                    kaynak = project.open_artifact(info)
                    if kaynak is None:
                        continue
                    with kaynak, zf.open(info.name, "w") as hedef:
                        shutil.copyfileobj(kaynak, hedef, BLOB_PARCA)
            fp.seek(0, os.SEEK_END)
            boyut = fp.tell()
            fp.seek(0)
            return "ok", fp, boyut, ad

        durum, fp, boyut, ad = await asyncio.to_thread(paketle)
        if durum == "empty":
            return _error(t("api.no_artifacts"), 404)
        if durum == "too_big":
            return _error(
                t("api.artifacts_zip_too_big", limit_mb=ARTIFACT_MAX_BYTES // (1024 * 1024)),
                413,
            )
        return StreamingResponse(
            iterate_in_threadpool(_dosya_akisi(fp)),
            media_type="application/zip",
            headers={
                "Content-Length": str(boyut),
                "Cache-Control": "no-store",
                "Content-Disposition": _ek_basligi(ad),
            },
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

        # Danisman bu kullanicinin ONCEKI projelerini de gorur. Kapsam
        # BURADA kurulur: `_gorulebilen_projeler` zaten "kullanicinin
        # gordugu" sorusunun tek cevabi ve orkestrator platform
        # veritabanini bilmiyor. Yetkiyi orada ikinci kez tanimlamak, iki
        # yerde iki kural demekti.
        #
        # Simdiki proje disarida birakilir: durumu ve sohbeti danismana
        # zaten tam haliyle gidiyor, ikinci kez kirpilmis olarak koymak
        # baglami sisirirdi.
        gecmis = UserHistory(
            [(p.name, p.slug, p.path) for p in _gorulebilen_projeler(request)],
            simdiki_slug=state.project.slug,
        )
        cevap = await anyio.to_thread.run_sync(
            lambda: state.orchestrator.chat(workflow_id, message, history=gecmis)
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

        # KAPSAM. `workflow` bir is akisinin butun kosularini kapsar;
        # `run` tek bir kosuyu. Ikisi de yoksa kapsam yok = her sey.
        #
        # Suzgec ISTEMCIDE olamazdi: gecmis sondan okunuyor ve ucuncu is
        # akisinin olaylari son 400 satirin cok gerisinde olabilir.
        wf = (request.query_params.get("workflow") or "").strip()
        tek_kosu = (request.query_params.get("run") or "").strip()
        kosular: set[str] | None = None
        if tek_kosu:
            kosular = {tek_kosu}
        elif wf:
            kosular = {
                str(r["id"]) for r in state.orchestrator.state.workflow_runs(wf)
            }
            if not kosular:
                # Is akisi var ama hic kosusu yok: bos kume "hicbir kosu"
                # demek, "kapsam yok" degil -- yoksa tek kosusu olmayan
                # bir is akisi butun projeyi gosterirdi.
                return _json({
                    "events": [], "total": 0, "path": str(state.settings.events_path),
                    "scope": {"workflow": wf, "runs": []}, "truncated": False,
                })

        yol = state.settings.events_path
        if not yol.is_file():
            return _json({"events": [], "total": 0, "path": str(yol),
                          "truncated": False})

        def kabul(kayit: dict[str, Any]) -> bool:
            if kosular is None:
                return True
            return str(kayit.get("run_id") or "") in kosular

        olaylar, kesildi = _tail_records(yol, limit, kabul=kabul)
        for kayit in olaylar:
            # `seq` canli tamponun sayacidir; gecmis kayitlarda yok.
            # Bos birakilir ki istemci imlecini geriye kaydirmasin.
            kayit.setdefault("seq", None)
        return _json({
            "events": olaylar,
            "total": len(olaylar),
            "path": str(yol),
            # "Daha eskisi taranmadi" ile "hic olay yok" ayni sey degil.
            "truncated": kesildi,
            "scope": ({"workflow": wf, "run": tek_kosu,
                       "runs": sorted(kosular)} if kosular is not None else None),
        })

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
        Route("/api/activity/runs", activity_runs),
        Route("/api/activity/artifacts", activity_artifacts),
        Route(
            "/api/activity/artifacts/{project_id}/{name}/download",
            activity_artifact_download,
        ),
        Route("/api/overview", overview),
        Route("/api/settings", update_settings, methods=["POST"]),
        Route("/api/providers", provider_catalog, methods=["GET"]),
        Route("/api/settings/models", list_models, methods=["POST"]),
        Route("/api/settings/test-browser", test_browser, methods=["POST"]),
        Route("/api/settings/test-search", test_search, methods=["POST"]),
        Route("/api/settings/test-llm", test_llm, methods=["POST"]),
        Route("/api/settings/test-sandbox", test_sandbox, methods=["POST"]),
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
        Route("/api/ingest-docs", ingest_docs, methods=["POST"]),
        Route("/api/package", package_status),
        Route("/api/package", package_build, methods=["POST"]),
        Route("/api/package/{name}", package_download),
        Route("/api/questions", questions),
        Route("/api/questions/{key}", resolve_question, methods=["POST"]),
        Route("/api/artifacts", artifacts),
        # `.zip` ucu `{name}` yakalayicisindan ONCE: Starlette rotalari
        # sirayla dener ve tersi sirada "artifacts.zip" adli bir cikti
        # aranirdi.
        Route("/api/artifacts.zip", artifacts_zip),
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


# Uzanti tablolari (ARCHIVE/IMAGE/BINARY_SUFFIXES, IMAGE_MEDIA_TYPES) artik
# `pipeline.artifacts`ta: blob deposu kaydederken ortam turune ihtiyac duyar
# ve boru hatti web katmanina bagimli olamaz. Adlar ithalle burada yasamaya
# devam eder (bkz. ustteki ithal blogu); tuketiciler degismedi.


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
        # BASLIK GONDEREMEYEN istemci icin sorgu parametresi. Tek gercek
        # ornegi `EventSource`: web standardi ona baslik koymaya izin
        # vermiyor ve akis, uygulamada baslik disiplininden muaf kalan
        # tek istek oluyordu -- yani iki sekme iki projede acikken ikisi
        # de son etkinlestirilen projenin olaylarini aliyordu.
        #
        # Yeni bir yetki yuzeyi DEGIL: asagidaki `_uyeyse` yine uyelik
        # ariyor. Parametre baslikla ayni bilgiyi tasiyor, daha fazlasini
        # degil.
        if not slug:
            slug = request.query_params.get("project", "").strip()
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


def _tail_records(
    path: Path,
    count: int,
    *,
    kabul: Callable[[dict[str, Any]], bool] | None = None,
    butce: int = 20000,
) -> tuple[list[dict[str, Any]], bool]:
    """Sondan geriye dogru okuyup `count` KABUL EDILEN kayit toplar.

    `_tail_lines` son N satiri verir; suzgec varken bu yetmez. Ucuncu
    is akisinin olaylari son 400 satirin cok gerisinde olabilir ve
    istemci tarafinda suzmek onlari hic goremezdi.

    `butce` taranan SATIR sayisinin tavani. Gunluk 16 MB'a kadar
    buyuyor ve hic olayi olmayan bir is akisi icin butun dosyayi
    taramak, bir ekran suzgecinin odemesi gereken bedel degil. Butce
    dolarsa ikinci deger `True` doner ve ekran "daha eskisi taranmadi"
    der -- "hic olay yok" DEMEZ, cunku o yalan olurdu.

    Doner: (eskiden yeniye sirali kayitlar, tarama kesildi mi)
    """
    kayitlar: list[dict[str, Any]] = []
    tarandi = 0
    kesildi = False
    artik = b""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            kalan = fh.tell()
            while kalan > 0 and len(kayitlar) < count and not kesildi:
                adim = min(64 * 1024, kalan)
                kalan -= adim
                fh.seek(kalan)
                blok = fh.read(adim) + artik
                satirlar = blok.split(b"\n")
                # Ilk parca yarim bir satir olabilir: bir sonraki (daha
                # erken) bloga eklenmek uzere saklanir. Basa vardigimizda
                # artik yarim degildir.
                artik = satirlar[0] if kalan > 0 else b""
                govde = satirlar[1:] if kalan > 0 else satirlar
                for ham in reversed(govde):
                    if not ham.strip():
                        continue
                    tarandi += 1
                    if tarandi > butce:
                        kesildi = True
                        break
                    try:
                        kayit = json.loads(ham.decode("utf-8", "replace"))
                    except (ValueError, TypeError):
                        # Kosu yarida kesildiyse son satir yarim kalmis
                        # olabilir; tek bozuk satir gecmisi goturmemeli.
                        continue
                    if not isinstance(kayit, dict):
                        continue
                    if kabul is not None and not kabul(kayit):
                        continue
                    kayitlar.append(kayit)
                    if len(kayitlar) >= count:
                        break
    except OSError:
        return [], False
    kayitlar.reverse()
    return kayitlar, kesildi


# Bicim karari `pipeline.artifacts.artifact_format`a tasindi; eski ad,
# cagiran yerler degismesin diye takma ad olarak kalir.
_artifact_format = artifact_format


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
