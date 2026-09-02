"""Konfigurasyon: ortam degiskenleri (.env) + proje dosyasi (deerx.toml).

Oncelik sirasi (sondaki kazanir):
    varsayilanlar  <  deerx.toml  <  ortam degiskenleri  <  CLI bayraklari
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError
from .i18n import set_language, t
from .logging import get_logger

ApprovalMode = Literal["auto", "ask", "dry-run"]
Provider = Literal["anthropic", "openai"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

log = get_logger("config")

CONFIG_FILENAME = "deerx.toml"
DATA_DIRNAME = ".deerx"

# Proje Praxis adiyla baslamisti; mevcut calisma alanlarini kirmamak icin eski
# adlar okunur ve ilk kullanimda sessizce tasinir.
LEGACY_CONFIG_FILENAME = "praxis.toml"
LEGACY_DATA_DIRNAME = ".praxis"

# Rol -> model eslemesi icin mantiksal katmanlar.
#   lead   : muhakemesi agir isler (analiz, mimari, plan, review)
#   worker : uzun ama daha mekanik isler (arastirma, uygulama)
#   fast   : siniflandirma/ozetleme gibi ucuz isler
ROLE_TIERS: dict[str, str] = {
    # Muhakemesi agir isler
    "analyst": "lead",
    "assessor": "lead",
    "architect": "lead",
    "planner": "lead",
    "qa": "lead",
    "reviewer": "lead",
    "live": "lead",       # uretime dokunur; en dikkatli model
    # Uzun ama daha mekanik isler
    "researcher": "worker",
    "mockup": "worker",
    "backend": "worker",
    "frontend": "worker",
    "staging": "worker",
    # Kullaniciyla konusur ve kayit degistirir; muhakemesi agir sayilir.
    "danisman": "lead",
    # Ucuz isler
    "summarizer": "fast",
}


class ShellPolicy(BaseModel):
    """Kabuk komutu calistirma politikasi."""

    enabled: bool = True
    timeout_seconds: int = 300
    # Bos liste => her sey serbest (yalnizca deny listesi uygulanir).
    allow_prefixes: list[str] = Field(
        default_factory=lambda: [
            "git", "python", "python3", "uv", "pip", "pytest", "ruff", "mypy",
            "node", "npm", "npx", "pnpm", "yarn", "tsc", "jest", "vitest",
            "ls", "cat", "head", "tail", "grep", "find", "wc", "echo", "mkdir",
        # Windows karsiliklari. Hepsi OKUR; ayni yetenek Unix adiyla zaten
        # serbestti ve ajan `findstr` cagirinca reddediliyordu.
        "findstr", "type", "dir", "where", "more",
        # Kosullarin yapitaslari: hicbiri yan etki uretmez.
        "true", "false", "test",
        # Kabuk yerlesikleri. Yalnizca kabugun KENDI durumunu degistirir;
        # tek baslarina hicbir sey yapmazlar ve baslattiklari her komut
        # zaten bu listeden gecer.
        #
        # Bunlar, yeni satirin ayrac sayilmaya baslamasiyla birlikte
        # gerekli oldu: eskiden cok satirli bir betikte yalnizca ILK
        # satirin adi denetleniyordu, artik hepsi deneteniyor. `cd`
        # olmadan `cd src` ile baslayan siradan bir betik reddedilirdi --
        # ve bu kod tabani yanlis alarmin bedelini biliyor: `shutdown`
        # deseninde yedi ornekten dordu yanlis alarmdi.
        "cd", "pwd", "export", "unset", "exit", "set",
        # Metin araclari: grep zaten izinliydi, bunlar ayni sinif.
        "sed", "awk", "sort", "uniq", "cut", "diff", "tr",
            "docker", "make", "go", "cargo",
        ]
    )
    deny_substrings: list[str] = Field(
        default_factory=lambda: [
            "rm -rf /", "mkfs", "shutdown", "reboot", "format ",
            "/dev/sda", "curl | sh", "wget | sh", "chmod 777 /",
            "git push --force", "git reset --hard origin",
        ]
    )


# Web arayuzunun varsayilan portu. Tek yerde durur: CLI, sunucu ve
# yonetim betikleri buradan okur, boylece ucu birbirinden kayamaz.
DEFAULT_PORT = 8791

# Sunucunun BAGLANDIGI adres, tarayicinin GIDEBILECEGI adres degildir.
# `0.0.0.0` "butun arayuzler" demektir ve bir hedef degil: tarayiciya
# `http://0.0.0.0:8791` vermek Firefox'ta dogrudan reddedilir, baska
# yerlerde tesadufen calisir. Bu adreslerle baglanildiginda kullaniciya
# `localhost` gosterilir.
BIND_ANY_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})


def browse_host(host: str) -> str:
    """Baglanma adresini, tarayiciya verilebilir bir ada cevirir."""
    if host in BIND_ANY_HOSTS or host in {"127.0.0.1", "::1", "[::1]"}:
        return "localhost"
    return host


class RagSettings(BaseModel):
    """Bilgi tabani (RAG) ayarlari."""

    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024
    # "fastembed" (yerel ONNX) veya "hash" (cevrimdisi duman testi)
    embedding_provider: Literal["fastembed", "hash"] = "fastembed"
    chunk_tokens: int = 700
    chunk_overlap_tokens: int = 100
    top_k: int = 8
    # Hibrit aramada RRF fuzyon sabiti; kucuk deger ust siralari daha cok odullendirir.
    rrf_k: int = 60
    # MMR cesitlendirmesinde alaka/cesitlilik dengesi (1.0 = saf alaka).
    mmr_lambda: float = 0.6
    include_globs: list[str] = Field(
        default_factory=lambda: [
            "**/*.md", "**/*.txt", "**/*.rst", "**/*.pdf", "**/*.docx",
            "**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx",
            "**/*.go", "**/*.rs", "**/*.java", "**/*.cs", "**/*.sql",
            "**/*.json", "**/*.yaml", "**/*.yml", "**/*.toml", "**/*.html",
        ]
    )
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
            "**/__pycache__/**", "**/dist/**", "**/build/**", "**/.deerx/**",
            "**/*.min.js", "**/*.lock", "**/package-lock.json", "**/uv.lock",
        ]
    )
    max_file_bytes: int = 2_000_000


class Settings(BaseSettings):
    """Uygulama genelinde tek konfigurasyon nesnesi."""

    model_config = SettingsConfigDict(
        env_prefix="DEERX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # --- Saglayici ---
    # "openai" her OpenAI-uyumlu ucu kapsar: vLLM, Ollama, LM Studio, OpenAI.
    provider: Provider = "openai"

    # --- Kimlik ---
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "DEERX_ANTHROPIC_API_KEY"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "DEERX_OPENAI_API_KEY"),
    )
    openai_base_url: str | None = Field(
        default="http://127.0.0.1:8008/v1",
        validation_alias=AliasChoices("OPENAI_BASE_URL", "DEERX_OPENAI_BASE_URL"),
    )

    # --- Modeller ---
    # Varsayilanlar yerel vLLM icindir; Anthropic'e gecerken provider ile birlikte
    # bu uc degeri de degistirin.
    model_lead: str = "qwen3.8 max"
    model_worker: str = "qwen3.8 max"
    model_fast: str = "qwen3.8 max"

    # Yerel modellerde ornekleme kontrolu; None ise sunucunun varsayilani kullanilir.
    temperature: float | None = None
    # Yerel modeller tek bir yaniti dakikalarca uretebilir. Deger token
    # butcesiyle tutarli olmali: 220K token ~70 tok/s ile ~52 dakikadir,
    # daha kisa bir zaman asimi istegi yanit gelmeden keser.
    request_timeout_seconds: int = 1800
    effort_lead: Effort = "high"
    effort_worker: Effort = "high"
    effort_fast: Effort = "low"
    # Tur basina azami uretim.
    #
    # Bu, tek bir yanitin *ust siniri*, hedefi degil: model her turda bu kadar
    # uretmez, deger yalnizca "bu kadarina kadar izin var" der. Girdi + cikti
    # ucun baglam penceresine sigmak zorunda oldugu icin istemci bu degeri
    # istek basina ayrica kirpar (bkz. `context_window`); buradaki sayi tavan,
    # gecerli sinir degil.
    #
    # Eskiden 220_000 idi. 262K pencereli bir ucta girdiye yalnizca ~42K
    # kaliyordu ve uzun bir kosuda devredilen durum bunu asinca uc istegi
    # 400 ile geri cevirdi: "220000 output + 42145 input = 262145 > 262144".
    # Tek bir yanitin 64K token'i (~240 KB metin) asmasi beklenmez; kalan
    # pencere girdiye birakiliyor.
    max_tokens: int = 32_000
    # Ucun toplam baglam penceresi (girdi + cikti), token cinsinden.
    #
    # None ise uca sorulur: vLLM `/v1/models` yanitinda `max_model_len`
    # dondurur. Uc soylemiyorsa kirpma yapilmaz -- uydurma bir sinir koymak,
    # dogru calisan bir kurulumu sebepsiz daraltirdi.
    context_window: int | None = None
    # Adaptif dusunme ozetini akista gormek icin "summarized", gizlemek icin "omitted".
    thinking_display: Literal["summarized", "omitted"] = "summarized"

    # --- Dongu sinirlari ---
    # Butceler token siniriyla birlikte olceklenir: genis pencerede araclara
    # dar bir butce vermek modeli kendi baglamindan mahrum birakir.
    max_iterations: int = 40
    max_tool_output_chars: int = 80_000
    # Bir turdaki TUM arac ciktilarinin toplami. Tek arac siniri yeterli degil:
    # model on araci paralel cagirinca 10 x 80K = 800K karakter tek turda
    # baglama girer ve pencere tasar.
    max_turn_output_chars: int = 240_000
    # Tum kosu icin USD tavani; asilirsa BudgetExceeded firlatilir. 0 => sinirsiz.
    cost_limit_usd: float = 0.0

    # --- Davranis ---
    approval_mode: ApprovalMode = "ask"
    language: str = "tr"
    enable_web: bool = True
    # Yerel `web_search` araci icin arama ucu. Anahtarsiz "duckduckgo" en iyi
    # cabadir; kesintisiz arama icin brave/tavily anahtari tanimlayin.
    # "browser" sunucudaki gercek Chrome'u kullanir ve anahtar istemez.
    # HTTP ile kazima (duckduckgo) kimligimiz yuzunden engelleniyor; brave
    # ve tavily anahtarli API'lerdir ve tarayici gerektirmez.
    # Ajanin komutlari nerede kosacak. Varsayilan konak: bugunku davranis.
    # "docker" secilirse `run_command` ve `start_service` yalitilmis bir
    # konteynerde kosar ve konak makine korunur.
    execution: Literal["host", "docker"] = "host"
    # Tam imaj, `slim` degil: olculdu, `slim` icinde git, curl, gcc ve make
    # YOK -- ajan ilk `pip install` derlemesinde ya da `git init`te duvara
    # carpar. Tam imajda hepsi hazir gelir.
    sandbox_image: str = "python:3.13"
    # Konteyner ILK kurulurken bir kez calisir. node, sqlite3 gibi proje-ozel
    # araclar icin: `apt-get update && apt-get install -y nodejs npm`.
    sandbox_setup: str = ""
    # Yayinlanan port araligi. Docker portlari konteyner kurulurken ayirir,
    # sonradan eklenemez; bu yuzden aralik onceden acilir ve ajanin
    # servisleri buradan secmesi istenir.
    sandbox_port_base: int = 8100
    sandbox_port_count: int = 10
    # Kacak bir ajan konagi yormasin. Sinirsiz birakilirsa bir fork bombasi
    # ya da bellek doldurma konteynerde kalmaz, makineyi dizustu eder.
    sandbox_memory: str = "2g"
    sandbox_cpus: float = 2.0
    sandbox_pids: int = 512
    search_provider: Literal[
        "browser", "duckduckgo", "brave", "tavily", "searxng", "google"
    ] = "browser"
    # Google'in resmi Programmable Search motorunun kimligi. Anahtar
    # `search_api_key` alanini paylasir; bu alan gizli degildir, bir kimlik.
    # Tarayici yoluyla Google kullanilamiyor: olculdu, bot korumasi donuyor.
    google_cse_id: str | None = None
    # Kendi SearXNG orneginin adresi. Anahtar istemez; engellenmez; hangi
    # motorun neden dustugunu bildirir. Genel motorlarin hicbiri durust bir
    # bot kimligiyle calismadigi icin anahtarsiz tek saglam yol budur.
    searxng_url: str = "http://127.0.0.1:8890"
    search_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SEARCH_API_KEY", "DEERX_SEARCH_API_KEY"),
    )
    log_level: str = "INFO"

    # --- Tarayici ---
    # Ajanin tarayicisi sunucuda calisan gercek Chrome'dur. Tembel baslar:
    # tarayici araci cagrilmadan hicbir surec acilmaz.
    browser_channel: Literal["auto", "chrome", "edge", "chromium"] = "auto"
    browser_headless: bool = True
    # Bos kalinca Chrome kendini kapatir; 0 = kapatma.
    browser_idle_seconds: int = 600
    # Ajanin kendi baslattigi yerel uygulamayi acabilmesi. Varsayilan KAPALI:
    # acildiginda bile izin yalnizca ajanin bildirdigi porta ve yalnizca o
    # kosu boyunca verilir, ic aga genel erisim anlamina gelmez.
    browser_allow_preview: bool = True

    # --- Alt bloklar ---
    rag: RagSettings = Field(default_factory=RagSettings)
    shell: ShellPolicy = Field(default_factory=ShellPolicy)

    # --- Yollar ---
    workspace: Path = Field(default_factory=Path.cwd)

    # ------------------------------------------------------------------ #
    # Turetilmis yollar
    # ------------------------------------------------------------------ #
    @property
    def data_dir(self) -> Path:
        return self.workspace / DATA_DIRNAME

    @property
    def db_path(self) -> Path:
        return self.data_dir / "deerx.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def browser_profile_dir(self) -> Path:
        """Ajanin tarayici profili.

        Kullanicinin gercek Chrome profilinden AYRI ve calisma alaninin
        altinda. Gercek profili kullanmak, ajana kullanicinin giris yapmis
        butun hesaplarini acmak demekti.
        """
        return self.data_dir / "browser"

    @property
    def deliveries_dir(self) -> Path:
        """Zip paketleri. Ciktilardan ayri: bunlar ikili ve buyuktur."""
        return self.data_dir / "teslimat"

    @property
    def events_path(self) -> Path:
        return self.data_dir / "events.jsonl"

    @property
    def prompts_dir(self) -> Path:
        """Kullanicinin ezdigi promptlar; yoksa paket icindekiler kullanilir."""
        return self.workspace / "prompts"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.deliveries_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Rol -> model / effort cozumleme
    # ------------------------------------------------------------------ #
    def model_for(self, role: str) -> str:
        tier = ROLE_TIERS.get(role, "lead")
        return {
            "lead": self.model_lead,
            "worker": self.model_worker,
            "fast": self.model_fast,
        }[tier]

    def effort_for(self, role: str) -> Effort:
        tier = ROLE_TIERS.get(role, "lead")
        return {
            "lead": self.effort_lead,
            "worker": self.effort_worker,
            "fast": self.effort_fast,
        }[tier]

    # Bir yanitin uretim hizi (token/sn) kabaca; yerel bir akil yurutme
    # modeli icin gozlemlenen deger. Sure tahmini icin kullanilir.
    LOCAL_TOKENS_PER_SECOND: ClassVar[int] = 70

    @model_validator(mode="after")
    def _sync_language(self) -> Settings:
        """Python tarafinin mesaj katalogunu ayarla ayni dile getirir.

        Dogrulayicida duruyor cunku `Settings` bircok yoldan kuruluyor --
        `load_settings`, dogrudan cagri, arayuzden ayar degisikligi. Tek bir
        yere baglamak, o yollardan birinde dilin sessizce Turkce kalmasi
        demekti; kullanici Ingilizce sectigi halde olay akisi Turkce akardi.
        """
        # Desteklenmeyen bir deger `tr` sayilir; alan da normallestirilir
        # cunku `settings.language` katalogun disinda da kullaniliyor (ajan
        # yonergesindeki dil adi, yonerge klasoru). Alan "de" kalsaydi katalog
        # Turkce, yonerge "de" der, ikisi ayrisirdi.
        self.language = set_language(self.language)
        return self

    @model_validator(mode="after")
    def _check_budget(self) -> Settings:
        """Token butcesiyle zaman asimi arasindaki tutarsizligi bildirir.

        Butcenin kendisine dokunulmaz -- kullanicinin verdigi deger gecerlidir.
        Ama 220K token yerel bir ucta ~52 dakikadir; zaman asimi bundan
        kucukse istek yanit gelmeden kesilir ve sebep gorunmez.
        """
        if self.provider != "openai":
            return self
        seconds = self.max_tokens / self.LOCAL_TOKENS_PER_SECOND
        if seconds > self.request_timeout_seconds:
            log.warning(
                t(
                    "setup.timeout_too_low",
                    tokens=f"{self.max_tokens:,}",
                    minutes=int(seconds / 60),
                    timeout=self.request_timeout_seconds,
                )
            )
        return self

    def require_api_key(self) -> str:
        if not self.anthropic_api_key:
            raise ConfigError(t("setup.no_anthropic_key"))
        return self.anthropic_api_key

    @property
    def llm_ready(self) -> bool:
        """Model cagrisi yapilabilir mi?

        Anthropic icin API anahtari sart; yerel bir OpenAI-uyumlu uc icin taban
        adres yeterlidir (cogu yerel sunucu anahtar istemez).
        """
        if self.provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.openai_base_url)

    @property
    def llm_hint(self) -> str:
        """Model cagrisi yapilamiyorsa kullaniciya gosterilecek neden."""
        if self.llm_ready:
            return ""
        if self.provider == "anthropic":
            return "ANTHROPIC_API_KEY tanimli degil"
        return "openai_base_url tanimli degil"

    @property
    def supports_server_tools(self) -> bool:
        """Sunucu tarafi web_search/web_fetch yalnizca Anthropic'te var."""
        return self.provider == "anthropic"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - kullanici hatasi
        raise ConfigError(t("setup.toml_unreadable", path=path, error=exc)) from exc


def migrate_legacy_workspace(workspace: Path) -> bool:
    """Praxis adiyla olusturulmus bir calisma alanini DeerX adlarina tasir.

    `load_settings` icinde, HERHANGI bir sey dizin olusturmadan once cagrilir:
    `EventLog` gibi yardimcilar kendi ust dizinlerini kendileri yarattigi icin
    gec kalan bir gecis "yeni dizin zaten var" deyip veriyi geride birakirdi.

    Returns:
        Bir sey tasindiysa True.
    """
    moved = False

    legacy_dir = workspace / LEGACY_DATA_DIRNAME
    new_dir = workspace / DATA_DIRNAME
    if legacy_dir.is_dir() and not new_dir.exists():
        legacy_dir.rename(new_dir)
        # Veritabani dosyasi da yeniden adlandirilir (WAL/SHM yan dosyalariyla).
        for suffix in ("", "-wal", "-shm"):
            old = new_dir / f"praxis.db{suffix}"
            if old.exists():
                old.rename(new_dir / f"deerx.db{suffix}")
        moved = True

    legacy_config = workspace / LEGACY_CONFIG_FILENAME
    new_config = workspace / CONFIG_FILENAME
    if legacy_config.is_file() and not new_config.exists():
        legacy_config.rename(new_config)
        moved = True

    return moved


def find_workspace(start: Path | None = None) -> Path:
    """Calisma alanini bulur: once `DEERX_WORKSPACE`, sonra en yakin
    `deerx.toml`, o da yoksa bulunulan dizin.

    Ortam degiskeni EN USTTE: "hangi dizinde olursam olayim bu calisma
    alaninda calis" demenin baska yolu yoktu. `find_workspace` yukari
    dogru yuruyor, yani depo kokunden calistirilan bir komut altindaki
    `demo/` calisma alanini hicbir zaman bulamiyordu.

    MCP sunucusu bu degiskeni zaten kuruyor ve okuyordu (`deerx mcp
    --workspace X` onu ayarliyor); CLI ise yok sayiyordu. Ayni degiskenin
    bir kapida gecerli, otekinde gecersiz olmasi tutarsizlikti.

    ACIKCA verilen `start` degiskeni EZER: `--workspace` bayragi ya da
    kutuphane cagrisi, ortamdan daha belirli bir niyettir.
    """
    if start is None:
        ortam = os.environ.get("DEERX_WORKSPACE")
        if ortam:
            aday = Path(ortam).expanduser()
            # Var olmayan bir yol sessizce kabul edilmez: yazim hatasi
            # yapan biri, komutlarinin bambaska bir yerde calistigini
            # ancak veri kaybettiginde fark ederdi.
            if aday.is_dir():
                return aday.resolve()
            log.warning(t("config.workspace_env_missing", path=aday))

    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        # Eski ad da taninir; ensure_dirs() ilk kullanimda tasir.
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
        if (candidate / LEGACY_CONFIG_FILENAME).is_file():
            return candidate
    return cur


def load_settings(workspace: Path | None = None, **overrides: Any) -> Settings:
    """deerx.toml + ortam degiskenlerini birlestirip Settings uretir."""
    ws = (workspace or find_workspace()).resolve()
    migrate_legacy_workspace(ws)
    config_path = ws / CONFIG_FILENAME
    if not config_path.is_file() and (ws / LEGACY_CONFIG_FILENAME).is_file():
        config_path = ws / LEGACY_CONFIG_FILENAME
    file_cfg = _read_toml(config_path)

    # deerx.toml icinde [deerx] kok ayarlar, [deerx.rag] / [deerx.shell] alt bloklar.
    # Eski dosyalarda kok blok [praxis] adiyla yazilmisti.
    root_cfg: dict[str, Any] = dict(file_cfg.get("deerx") or file_cfg.get("praxis") or {})
    merged: dict[str, Any] = {k: v for k, v in root_cfg.items() if not isinstance(v, dict)}
    if isinstance(root_cfg.get("rag"), dict):
        merged["rag"] = RagSettings(**root_cfg["rag"])
    if isinstance(root_cfg.get("shell"), dict):
        merged["shell"] = ShellPolicy(**root_cfg["shell"])

    _warn_misplaced_root_keys(file_cfg, root_cfg, config_path)
    _warn_unknown_keys(root_cfg, config_path)

    merged["workspace"] = ws

    # DEERX_LANGUAGE / DEERX_LANG dosyadaki degeri ezer. CLI yardim metinleri
    # ice aktarma aninda, yani deerx.toml okunmadan once kurulur ve yalnizca
    # bu degiskene bakabilir (`cli._early_language`). Dosya kazansaydi
    # `DEERX_LANGUAGE=en deerx init` yardimi Ingilizce, ciktisi Turkce basardi.
    # Acik `overrides` yine ustundur: onu cagiran kod bilerek veriyor.
    env_language = os.environ.get("DEERX_LANGUAGE") or os.environ.get("DEERX_LANG")
    if env_language:
        merged["language"] = env_language

    merged.update({k: v for k, v in overrides.items() if v is not None})

    # `.env` CALISMA ALANINDAN okunur, gecerli dizinden degil. Aksi halde
    # `deerx serve --workspace X` veya DEERX_WORKSPACE ile calisan MCP sunucusu
    # projenin anahtarini sessizce gormezden gelirdi.
    # pydantic-settings listedeki SON dosyayi ustun tutar; calisma alani
    # sona konur ki gecerli dizindeki bir .env onu ezmesin.
    workspace_env = ws / ".env"
    env_files: list[Path] = []
    cwd_env = Path.cwd() / ".env"
    if cwd_env.resolve() != workspace_env.resolve():
        env_files.append(cwd_env)
    env_files.append(workspace_env)

    return Settings(_env_file=[str(p) for p in env_files], **merged)


def _warn_misplaced_root_keys(
    file_cfg: dict[str, Any], root_cfg: dict[str, Any], config_path: Path
) -> None:
    """Ayarlar `[deerx]` disina yazilmissa soyle; sessizce yutma.

    OLCULDU: basligi olmayan bir dosyadaki `search_provider = "searxng"`
    ve `approval_mode = "auto"` hicbir sey yapmadi ve hicbir uyari cikmadi.
    Ayarlar `[deerx]` tablosunun altinda beklenir, kok duzey okunmaz --
    ve `_warn_unknown_keys` bu durumda BOS sozlukle calistigi icin yazim
    hatasi denetimi de susar. Dosyanin tamami yok sayilirken susmak, tek
    bir yazim hatasinda konusmaktan daha kotudur.
    """
    if root_cfg:
        return
    bilinen = set(Settings.model_fields) | {"rag", "shell"}
    yanlis_yerde = sorted(k for k in file_cfg if k in bilinen)
    if not yanlis_yerde:
        return
    log.warning(
        t("config.missing_deerx_table", path=config_path,
          keys=", ".join(yanlis_yerde[:6]))
    )


def _warn_unknown_keys(root_cfg: dict[str, Any], config_path: Path) -> None:
    """deerx.toml icindeki taninmayan anahtarlari bildirir.

    `extra="ignore"` bir yazim hatasini (or. `aproval_mode`) sessizce yutar ve
    kullanici ayarin neden ise yaramadigini anlamaz.
    """
    known = set(Settings.model_fields) | {"rag", "shell"}
    unknown = sorted(set(root_cfg) - known)
    if not unknown:
        return

    import difflib

    hints = []
    for key in unknown:
        close = difflib.get_close_matches(key, known, n=1, cutoff=0.7)
        hints.append(
            f"{key}"
            + (t("setup.did_you_mean", suggestion=close[0]) if close else "")
        )
    log.warning(
        t("setup.unknown_settings", file=config_path.name, keys=", ".join(hints))
    )
