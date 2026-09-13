"""Arac altyapisi: baglam, sonuc tipi, kayit defteri ve onay kapisi."""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..config import Settings
from ..errors import ApprovalDenied, ToolError, WorkspaceError
from ..i18n import language, t
from ..logging import EventLog, console, get_logger
from ..sandbox import SandboxUnavailable

if TYPE_CHECKING:  # pragma: no cover
    from ..browser import BrowserSession
    from ..pipeline.state import ProjectState
    from ..rag.knowledge import KnowledgeBase
    from ..services import ServiceManager


# `child()`in "devral" varsayilani. `None` kullanilamaz: `doc_scope`
# icin `None` artik gecerli bir DEGER ("kapsam yok") ve iki anlami
# tek isarete yuklemek, tam da bu dosyada duzeltilen hatanin kendisi.
_DEVRAL = object()

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


# Paylasilan bir kaynaga dokunan ve bu yuzden ayni anda YALNIZCA BIR
# ajan tarafindan calistirilabilen araclar.
#
# Tarayici: Playwright'in senkron nesneleri onlari olusturan is
# parcacigina bagli; ikinci bir is parcacigindan dokunmak tanimsiz
# davranis. Servisler: ad alani ve port secimi paylasilan durum. Kabin:
# ilk komut konteyneri kuruyor ve iki is parcacigi ayni anda kurmaya
# calisirsa Docker ikisini de reddeder.
SERIAL_TOOLS = frozenset({
    "browse_page", "browser_snapshot", "browser_click", "browser_type",
    "browser_back", "browser_screenshot", "preview_open", "find_images",
    "download_image", "web_search", "fetch_url",
    "start_service", "stop_service", "service_log",
    "run_command",
})


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
    # Hakkinda konusulan is akisi. Danisman araclari kapsamlarini
    # BURADAN alir, arac argumanindan degil: kimligi modele sormak,
    # kullanici #3'u konusurken modelin #7'yi degistirmesine kapi acar.
    # Bos dize "is akisi baglami yok" demektir ve o araclar reddeder.
    workflow_id: str = ""
    # Kullanicinin ONCEKI projeleri; salt okunur (`deerx.history`).
    # Kapsami CAGIRAN kurar -- web katmani kullanicinin gorebildigi
    # projeleri verir. Burada "butun projeler" gibi bir kisayol olsaydi
    # yetki iki yerde iki kez tanimlanmis olurdu.
    #
    # `None` "bu baglamda gecmise bakilamaz" demektir ve gecmis araclari
    # acikca REDDEDER. Sessizce bos donmek, modelin "gecmiste hicbir sey
    # yok" diye yanlis bir cikarim yapmasina yol acardi.
    history: Any = None
    # Bu kosunun okuyabilecegi belgeler. `workflow_id` ile ayni gerekce:
    # kapsam CAGIRANDAN gelir, arac argumanindan degil. Modele "hangi
    # belgelere bakayim?" diye sormak, kullanicinin bilerek disarida
    # biraktigi eski bir sartnameyi modelin geri getirmesine kapi acar --
    # ve bunun icin kotu niyet gerekmez, bir liste uydurmasi yeter.
    # `None` "kapsam yok = tum korpus"; BOS DEMET "hicbir belge".
    #
    # Ikisi ayri seyler ve RAG katmani bu ayrimi zaten kuruyor
    # (`test_an_empty_scope_means_no_document_not_every_document`). Once
    # ikisi de `()` idi: Gelistirme ekraninda butun belgelerin secimini
    # kaldiran kullaniciya "ajanlar belge okumadan calisacak" deniyor ve
    # ajanlar tum korpusu okuyordu.
    doc_scope: tuple[str, ...] | None = None
    # Onay isteme kancasi; None ise `approval_mode` uzerinden karar verilir.
    approval_hook: Callable[[str, str], bool] | None = None
    # Kosu suresince onaylanan tehlikeli islem imzalari (tekrar sormamak icin).
    _granted: set[str] = field(default_factory=set)
    # Kosu boyunca dusen adresler ve kac kez dustukleri. Bir modelin ayni
    # olu adresi on kez denedigi olculdu; harness bunu biliyorsa soylemeli.
    _failed_fetches: dict[str, int] = field(default_factory=dict)
    # Alt ajan calistirici. Orkestrator baglar; sohbet ve test
    # baglamlarinda `None` kalir ve `run_subagent` acikca reddeder --
    # sessizce basarili donmek, modelin isini yaptigini sanmasina yol
    # acardi.
    spawn: Callable[[str, str, str], Any] | None = None
    # Ozyineleme derinligi. Alt ajan alt ajan kosturamaz: sinirsiz
    # derinlik, tek bir istegin butun butceyi harcayacagi ve nerede
    # durdugunu kimsenin goremeyecegi bir agac uretir.
    depth: int = 0
    # Paylasilan kaynaklara erisimi seriye alan kilit. Alt ajanlar ve
    # paralel gorevler AYNI kilidi paylasir -- `child()` bunu sifirlamaz,
    # cunku amaci tam olarak kardesler arasinda siraya sokmak.
    serial_lock: Any = field(default_factory=threading.RLock)
    # Kosuya ait konteyner; `run_command` ilk yalitilmis komutta kurar.
    # Alan BURADA tanimli olmali: `shell.py` ve orkestrator ona disaridan
    # yaziyordu ve bu yalnizca bu veri sinifinda `slots` KAPALI oldugu icin
    # calisiyordu. Dosyadaki oteki veri siniflari `slots=True` kullaniyor;
    # birinin bunu da eklediği gun `execution = "docker"` calisma
    # zamaninda `AttributeError` ile kirilirdi.
    _sandbox: Any = None

    # ------------------------------------------------------------------ #
    # Turetme
    # ------------------------------------------------------------------ #
    def child(
        self,
        *,
        doc_scope: tuple[str, ...] | None | object = _DEVRAL,
        workflow_id: str | None = None,
    ) -> ToolContext:
        """Alt ajan icin turetilmis baglam.

        Paylasilan kaynaklar (bilgi tabani, durum, tarayici, servisler,
        kabin) OLDUGU GIBI gecer: alt ajan ayni projede calisiyor ve
        ikinci bir tarayici acmak ya da ikinci bir konteyner kurmak
        anlamsiz olurdu.

        Devralinmayan iki sey var ve ikisi de bilincli:

        * `_granted` -- ebeveynin aldigi ONAYLAR. Kullanici "su tehlikeli
          komutu calistir" dediginde o komuta onay verdi, bir role degil;
          onayi alt ajana tasimak bir yetki sizintisidir.
        * `_failed_fetches` -- dusen adres sayaci. Ebeveynin denedigi bir
          adres alt ajan icin de olu olabilir ama sayaci devralmak, alt
          ajanin hic denemedigi bir adresi "cok denedin" diye
          reddetmesine yol acardi.
        """
        return ToolContext(
            settings=self.settings,
            events=self.events,
            kb=self.kb,
            state=self.state,
            browser=self.browser,
            services=self.services,
            workflow_id=self.workflow_id if workflow_id is None else workflow_id,
            # Nobetci gerekli: `None` artik gecerli bir DEGER ("kapsam
            # yok"), yani "devralma" anlamini tasiyamaz.
            doc_scope=(
                self.doc_scope if doc_scope is _DEVRAL
                else cast("tuple[str, ...] | None", doc_scope)
            ),
            approval_hook=self.approval_hook,
            spawn=self.spawn,
            depth=self.depth + 1,
            # Kilit PAYLASILIR: kardesleri siraya sokmasi gerekiyor.
            serial_lock=self.serial_lock,
            _sandbox=self._sandbox,
        )

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
        # Paylasilan kaynaga dokunan araclar SERIYE alinir. Kilit cagri
        # BOYUNCA tutulmali: `ctx.browser`i almak yetmez, sayfayla
        # calisan kod da korunmali.
        kilit = ctx.serial_lock if name in SERIAL_TOOLS else None
        try:
            if kilit is not None:
                with kilit:
                    outcome = tool.run(ctx, **arguments)
            else:
                outcome = tool.run(ctx, **arguments)
        except ApprovalDenied as exc:
            return ToolResult.error(str(exc))
        except SandboxUnavailable as exc:
            # Kabin koptu. Modele bu da bir arac hatasi olarak doner (o
            # turu tamamlamak icin gerekli) ama YALNIZCA oyle donmesi
            # yetmiyordu: hicbir komut calismayacagi halde model ayni
            # duvara tur butcesi bitene kadar tosluyor, kirk tur
            # "izin listesi", "yol yanlis" diye kendi komutunu duzeltmeye
            # calisiyordu. Isaret ajan dongusunu kestirir; sebep
            # metninde zaten yaziyor.
            return ToolResult(
                content=f"{t('tool.error_prefix')}: {exc}",
                is_error=True,
                data={"sandbox_down": True},
            )
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
