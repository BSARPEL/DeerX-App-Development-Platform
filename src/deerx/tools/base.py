"""Arac altyapisi: baglam, sonuc tipi, kayit defteri ve onay kapisi."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..errors import ApprovalDenied, ToolError, WorkspaceError
from ..i18n import language, t
from ..logging import EventLog, console, get_logger

if TYPE_CHECKING:  # pragma: no cover
    from ..browser import BrowserSession
    from ..pipeline.state import ProjectState
    from ..rag.knowledge import KnowledgeBase
    from ..services import ServiceManager

log = get_logger("tools")


@dataclass(slots=True)
class ToolResult:
    """Bir arac calistirmasinin sonucu.

    `content` modele geri gonderilen metindir. `data` yalnizca Python tarafinda
    kullanilir (or. CLI tablolari); modele gitmez.
    """

    content: str
    is_error: bool = False
    data: Any = None
    # Modelin GORMESI gereken dosyalar. Ekran goruntusu yalnizca "kaydedildi"
    # diye bildirildiginde model kendi urettigi arayuzun nasil GORUNDUGUNU
    # bilemez: hizalama bozuklugu, ust uste binen kutular, okunmayan metin
    # onun dongusunun disinda kalir. Olculdu -- yerel model ekran
    # goruntusundeki rastgele bir kodu dogru okudu, yani gorebiliyor.
    images: list[Path] = field(default_factory=list)

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(content=f"{t('tool.error_prefix')}: {message}", is_error=True)


@dataclass
class ToolContext:
    """Araclarin ihtiyac duydugu tum paylasimli kaynaklar."""

    settings: Settings
    events: EventLog
    kb: KnowledgeBase | None = None
    state: ProjectState | None = None
    # Sunucudaki Chrome oturumu. Tembel kurulur: tarayici araci cagrilmadan
    # hicbir surec baslamaz, o yuzden kullanmayan kurulumlara bedeli yok.
    browser: BrowserSession | None = None
    # Ajanin baslattigi arka plan surecleri (dev sunucusu vb.). Kosuya
    # baglidir: kosu bitince hepsi kapatilir.
    services: ServiceManager | None = None
    # Onay isteme kancasi; None ise `approval_mode` uzerinden karar verilir.
    approval_hook: Callable[[str, str], bool] | None = None
    # Kosu suresince onaylanan tehlikeli islem imzalari (tekrar sormamak icin).
    _granted: set[str] = field(default_factory=set)
    # Kosu boyunca dusen adresler ve kac kez dustukleri. Bir modelin ayni
    # olu adresi on kez denedigi olculdu; harness bunu biliyorsa soylemeli.
    _failed_fetches: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Yol guvenligi
    # ------------------------------------------------------------------ #
    def resolve_path(self, raw: str, *, must_exist: bool = False) -> Path:
        """Yolu calisma alanina gore cozer ve disari cikilmadigini dogrular."""
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.workspace / candidate
        resolved = candidate.resolve()

        workspace = self.settings.workspace.resolve()
        if not resolved.is_relative_to(workspace):
            raise WorkspaceError(
                t("tool.outside_workspace", path=resolved, workspace=workspace)
            )
        if must_exist and not resolved.exists():
            raise ToolError(t("tool.path_missing", path=self.relative(resolved)))
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.settings.workspace.resolve()).as_posix()
        except ValueError:
            return str(path)

    # ------------------------------------------------------------------ #
    # Onay
    # ------------------------------------------------------------------ #
    def approve(self, action: str, detail: str = "", *, signature: str | None = None) -> None:
        """Tehlikeli bir islem icin onay alir; reddedilirse `ApprovalDenied` firlatir."""
        mode = self.settings.approval_mode
        sig = signature or action
        if mode == "auto" or sig in self._granted:
            return
        if mode == "dry-run":
            raise ApprovalDenied(f"dry-run modu: '{action}' uygulanmadi.")

        if self.approval_hook is not None:
            granted = self.approval_hook(action, detail)
        else:
            granted = self._prompt(action, detail)

        if not granted:
            raise ApprovalDenied(t("tool.approval_denied", action=action))
        self._granted.add(sig)

    def note_fetch_failure(self, url: str) -> int:
        """Dusen bir adresi kaydeder ve kacinci kez dustugunu doner."""
        self._failed_fetches[url] = self._failed_fetches.get(url, 0) + 1
        return self._failed_fetches[url]

    @staticmethod
    def _prompt(action: str, detail: str) -> bool:
        from rich.prompt import Confirm

        console.print(f"\n[warn]{t('tool.approval_needed')}[/warn] {action}")
        if detail:
            console.print(f"[dim]{detail[:2000]}[/dim]")
        try:
            return Confirm.ask(t("tool.approval_continue"), default=False)
        except (EOFError, KeyboardInterrupt):
            return False

    def require_kb(self) -> KnowledgeBase:
        if self.kb is None:
            raise ToolError(t("tool.no_kb"))
        return self.kb

    def require_state(self) -> ProjectState:
        if self.state is None:
            raise ToolError(t("tool.no_state"))
        return self.state


class Tool(ABC):
    """Tum araclarin taban sinifi."""

    name: str = ""
    description: str = ""
    schema: dict[str, Any] = {}
    # True ise calistirmadan once `ctx.approve` cagrilmalidir (aracin kendi icinde).
    dangerous: bool = False

    @abstractmethod
    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult | str:
        """Araci calistirir. Kurtarilabilir hatalar icin `ToolError` firlatin."""

    def spec(self) -> dict[str, Any]:
        """Anthropic Messages API'sinin bekledigi arac tanimi.

        Aciklamalar MODELE gidiyor. Ajan yonergeleri Ingilizce secildiginde
        arac aciklamalarinin Turkce kalmasi modele iki dilli bir baglam
        verirdi. Turkce metin aracin kendi sinifinda, kodun belgesi olarak
        duruyor; Ingilizce karsiligi `descriptions_en` icinde ve burada
        uzerine biniyor.
        """
        from .descriptions_en import ENGLISH

        override = ENGLISH.get(self.name, {}) if language() == "en" else {}
        return {
            "name": self.name,
            "description": (override.get("") or self.description).strip(),
            "input_schema": _with_descriptions(self.schema, override),
        }


def _with_descriptions(
    schema: dict[str, Any], override: dict[str, str]
) -> dict[str, Any]:
    """Semanin parametre aciklamalarini cevirisiyle degistirir.

    Sema bir SINIF niteligi; yerinde degistirilseydi ilk cagri butun surec
    icin dili sabitlerdi. O yuzden kopyalanir.
    """
    props = schema.get("properties")
    if not override or not isinstance(props, dict):
        return schema
    yeni_props = {
        ad: ({**alan, "description": override[ad]}
             if isinstance(alan, dict) and ad in override
             else alan)
        for ad, alan in props.items()
    }
    return {**schema, "properties": yeni_props}


class ToolRegistry:
    """Ad -> arac esleme defteri.

    Arac sirasi sabit tutulur: prompt onbellegi arac listesini de kapsadigi icin
    siranin degismesi onbellegi gecersiz kilar.
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"{type(tool).__name__} icin `name` tanimlanmamis.")
        self._tools[tool.name] = tool

    def extend(self, tools: list[Tool]) -> ToolRegistry:
        for tool in tools:
            self.add(tool)
        return self

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [self._tools[name].spec() for name in sorted(self._tools)]

    def subset(self, names: list[str]) -> ToolRegistry:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Bilinmeyen arac(lar): {', '.join(missing)}")
        return ToolRegistry([self._tools[n] for n in names])

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Araci calistirir; hicbir kosulda dongu kirici istisna sizdirmaz."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.error(
                t("tool.unknown", name=name, names=", ".join(self.names()))
            )
        try:
            outcome = tool.run(ctx, **arguments)
        except ApprovalDenied as exc:
            return ToolResult.error(str(exc))
        except (ToolError, WorkspaceError) as exc:
            return ToolResult.error(str(exc))
        except TypeError as exc:
            return ToolResult.error(t("tool.bad_arguments", name=name, error=exc))
        except Exception as exc:  # noqa: BLE001 - modele geri bildirilir, dongu surer
            log.exception(t("tool.unexpected", name=name))
            return ToolResult.error(f"{type(exc).__name__}: {exc}")

        result = outcome if isinstance(outcome, ToolResult) else ToolResult(content=str(outcome))
        limit = ctx.settings.max_tool_output_chars
        if len(result.content) > limit:
            result.content = (
                result.content[:limit]
                + f"\n\n…[cikti {len(result.content) - limit:,} karakter kisaltildi]"
            )
        return result


def json_block(data: Any) -> str:
    """Yapisal veriyi modele okunakli JSON olarak dondurur."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
