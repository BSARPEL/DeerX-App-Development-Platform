"""Orkestratör — fazlari sirayla yuruten durum makinesi.

Her faz kendi ajanini calistirir, ciktisini `ProjectState` uzerinden bir sonraki
faza devreder ve durumunu kaydeder. Kosu her fazdan sonra kalicidir: surec
kesilirse `--from <faz>` ile kaldigi yerden devam eder.
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agents import AgentResult, build_agent
from ..browser import BrowserSession, UrlPolicy
from ..config import Settings
from ..errors import DeerXError
from ..i18n import t
from ..llm import LLMClient, build_client
from ..logging import EventLog, console, get_logger
from ..rag import KnowledgeBase
from ..services import ServiceManager
from ..tools import LANE_ROLE, TOOLSETS, ToolContext, ToolRegistry, build_registry
from .answers import reindex_answers
from .models import Phase, Question, Status, Task
from .packaging import PackagingError, PackagingNotReady, build_package
from .state import ProjectState

log = get_logger("orchestrator")

# Faz -> ajan rolu.
#   INGEST    : ajan calistirmaz (deterministik islem)
#   PACKAGE   : ajan calistirmaz; dosyalari toplayip zipler
#   IMPLEMENT : tek rol yok; her gorev `lane` alanina gore yonlendirilir
PHASE_ROLE: dict[Phase, str] = {
    Phase.ANALYZE: "analyst",
    Phase.RESEARCH: "researcher",
    Phase.ASSESS: "assessor",
    Phase.MOCKUP: "mockup",
    Phase.DESIGN: "architect",
    Phase.PLAN: "planner",
    Phase.QA: "qa",
    Phase.REVIEW: "reviewer",
    Phase.STAGING: "staging",
    Phase.LIVE: "live",
}

# Tek kosuda uygulanacak azami gorev sayisi; sonsuz donguye karsi emniyet kemeri.
MAX_TASKS_PER_RUN = 60

# Her ajan fazinin birakmasi gereken iz. Modelin konusmayi bitirmesi, isini
# bitirdigi anlamina gelmiyor: olculdu -- `assess` uc turda yalnizca dosya
# okuyup durdu, `mockup` iki turda uc arama yapip durdu. Ikisi de `done`
# isaretlendi, hicbiri tek satir uretmedi; boru hatti eksik girdiyle ilerledi
# ve mimar "mockup yok, kod tabani bos" diyerek zorlandi.
#
# Beklenen ciktinin adi yalnizca yonergelerde yaziliydi ve hicbir sey
# uygulamiyordu. Sozlesme burada uygulanir.
PHASE_DELIVERABLE: dict[Phase, tuple[str, str]] = {
    Phase.ANALYZE: ("analiz-raporu.md", "gereksinimler ve analiz raporu"),
    Phase.RESEARCH: ("arastirma-notlari.md", "kaynakli arastirma notlari"),
    Phase.ASSESS: ("bosluk-analizi.md", "bosluk ve risk analizi"),
    Phase.MOCKUP: ("mockup-*.html", "en az bir calisan HTML ekran"),
    Phase.DESIGN: ("mimari.md", "mimari kararlar ve veri modeli"),
    Phase.PLAN: ("gelistirme-plani.md", "seritlere bolunmus gorev listesi"),
    Phase.QA: ("qa-raporu.md", "test kosumu ve UAT raporu"),
    Phase.REVIEW: ("dogrulama-raporu.md", "gereksinim izlemesi ve kod denetimi"),
    Phase.STAGING: ("staging-raporu.md", "temiz ortam kurulumu ve duman testi"),
    Phase.LIVE: ("canli-cikis-raporu.md", "cikis kapisi ve geri alma plani"),
}

@dataclass(slots=True)
class PhaseResult:
    # `phase=None` faz sonucu degil, faz kapisidir (kullanici cevabi bekleniyor).
    phase: Phase | None
    status: str
    summary: str = ""
    cost: float = 0.0
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.status != Status.FAILED

    @property
    def label(self) -> str:
        return self.phase.label if self.phase is not None else "Bilgi bekleniyor"


@dataclass(slots=True)
class ChatReply:
    """Is akisi danismaninin bir turluk cevabi."""

    text: str
    # Bu turda DEGISTIRILEN seyler: arac adlari ve kisa aciklamalari.
    # Sohbet penceresinde cevabin altinda gosterilir; kullanici neyin
    # degistigini metnin icinde aramak zorunda kalmasin.
    changes: list[str] = field(default_factory=list)
    cost: float = 0.0
    iterations: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# Durumu DEGISTIREN araclar. Sohbette hangi turun bir sey degistirdigini
# isaretlemek icin; okuyan kisi cevabin altinda gormeli.
MUTATING_TOOLS = frozenset({
    "update_workflow", "resolve_question", "update_task", "save_artifact",
    "record_requirements", "record_gaps", "record_decisions",
    "record_questions", "record_tasks", "record_research",
})


class _RecordingRegistry(ToolRegistry):
    """Calistirilan degistirici araclari kaydeden kayit defteri.

    Neden sarmalayici: "bu turda ne degisti?" sorusunun cevabi konusma
    gecmisinden de cikarilabilirdi, ama o gecmis SAGLAYICIYA OZGU --
    Anthropic'in icerik bloklari ile OpenAI'nin `tool_calls` bicimi ayri.
    Burada okumak ikisinde de ayni calisir.
    """

    def __init__(self, inner: ToolRegistry, applied: list[str] | None = None) -> None:
        super().__init__([inner.get(ad) for ad in inner.names()])  # type: ignore[misc]
        self.applied: list[str] = [] if applied is None else applied

    def subset(self, names: list[str]) -> ToolRegistry:
        """Alt kume de KAYIT TUTMALI ve ayni listeye yazmali.

        OLCULDU: bu gecersiz kilma olmadan sarmalayici sessizce
        dusuyordu. `build_agent` verilen kayit defterini kendi icinde
        `subset(TOOLSETS[rol])` ile daraltiyor ve taban sinifin `subset`i
        DUZ bir `ToolRegistry` donduruyor -- ajan sarmalanmamis defteri
        kullaniyor, degisiklikler kaydedilmiyor ve sohbet "hicbir sey
        degismedi" diyordu. Hedef gercekten degismisken.
        """
        return _RecordingRegistry(super().subset(names), self.applied)

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext):
        sonuc = super().execute(name, arguments, ctx)
        if name in MUTATING_TOOLS and not sonuc.is_error:
            self.applied.append(f"{name}: {sonuc.content[:120]}")
        return sonuc


@dataclass(slots=True)
class RunReport:
    phases: list[PhaseResult] = field(default_factory=list)
    total_cost: float = 0.0
    # Kosu basarisiz olmadi, yalnizca kullanicidan cevap bekliyor.
    needs_input: bool = False
    # Kosunun kimligi ve kullaniciya gosterilen sirali numarasi (#1, #2 ...).
    run_id: str = ""
    seq: int = 0
    # Bu kosunun adimi oldugu is akisi.
    workflow_id: str = ""
    workflow_seq: int = 0

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.phases)

    def failed_phase(self) -> PhaseResult | None:
        return next((p for p in self.phases if not p.ok), None)

    def pending_questions(self) -> list[str]:
        for result in self.phases:
            if result.status == Status.NEEDS_INPUT:
                return list(result.details.get("questions", []))
        return []


class Orchestrator:
    """Boru hattinin sahibi. Paylasimli kaynaklari kurar ve fazlari yurutur."""

    def __init__(
        self,
        settings: Settings,
        *,
        events: EventLog | None = None,
        stream: bool = True,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        settings.ensure_dirs()
        self.settings = settings
        self.events = events or EventLog(settings.events_path)
        self.stream = stream
        # Isbirlikci iptal kancasi; web arayuzundeki "Durdur" dugmesi bunu kullanir.
        self.should_stop = should_stop

        self.kb = KnowledgeBase(settings, events=self.events)
        self.state = ProjectState(settings.db_path)
        self.registry: ToolRegistry = build_registry()
        # Tarayici oturumu burada KURULUR ama BASLATILMAZ: Chrome sureci
        # ilk tarayici araci cagrilana kadar acilmaz, o yuzden tarayici
        # kullanmayan kosulara hicbir bedeli yok.
        self.browser = BrowserSession(
            profile_dir=settings.browser_profile_dir,
            policy=UrlPolicy(),
            channel=settings.browser_channel,
            headless=settings.browser_headless,
            idle_seconds=float(settings.browser_idle_seconds),
            on_navigate=self._on_browser_block,
        )
        # Arka plan servisleri (ajanin baslattigi dev sunucusu vb.).
        # Yonetici bos baslar; hicbir surec calismaz.
        self._sandbox = None
        if settings.execution == "docker":
            from ..sandbox import Sandbox

            self._sandbox = Sandbox(
                workspace=settings.workspace,
                image=settings.sandbox_image,
                port_base=settings.sandbox_port_base,
                port_count=settings.sandbox_port_count,
                memory=settings.sandbox_memory,
                cpus=settings.sandbox_cpus,
                pids_limit=settings.sandbox_pids,
                setup=settings.sandbox_setup,
            )
        self.services = ServiceManager(
            log_dir=settings.db_path.parent / "services",
            events=self.events,
            sandbox=self._sandbox,
        )
        self.ctx = ToolContext(
            settings=settings,
            events=self.events,
            kb=self.kb,
            state=self.state,
            browser=self.browser,
            services=self.services,
        )
        self._client: LLMClient | None = None
        # Onceki surec yarida kesildiyse gorevler `running` kalmis olabilir;
        # su an hicbir sey kosmadigi icin hepsi yetimdir ve kuyruga doner.
        stale = self.state.reclaim_orphaned_runs()
        if stale:
            self.events.emit(
                "warn", "run",
                t(
                    "run.reclaimed",
                    count=len(stale),
                    ids=", ".join(f"#{s}" for s in stale[:8]),
                ),
            )
        orphaned = self.state.reclaim_orphaned_tasks()
        if orphaned:
            self.events.emit(
                "warn", "plan",
                t(
                    "pipeline.reclaimed",
                    count=len(orphaned),
                    keys=", ".join(orphaned[:8]),
                ),
            )
        # Suren kosunun kimligi ve icinde bulunulan adimin sirasi.
        self._run_id: str | None = None
        self._step_ordinal: int = 0

    @property
    def client(self) -> LLMClient:
        """LLM istemcisi tembel kurulur: `ingest` gibi fazlar model cagrisi yapmaz."""
        if self._client is None:
            self._client = build_client(self.settings, events=self.events)
        return self._client

    def reset_client(self) -> None:
        """LLM istemcisini dusurur; bir sonraki cagride yeniden kurulur.

        Saglayici, uc adres, anahtar ya da model degistiginde sart: istemci
        bu degerleri kurulumda okur, yoksa ayar degisikligi sunucu yeniden
        baslatilana kadar sessizce etkisiz kalir.
        """
        self._client = None

    def reset_sandbox(self) -> None:
        """Yalitim ayarlari degisti; kabini soker ve yeniden kurar.

        `Sandbox` butun ayarlarini KURULUMDA okur -- Docker portlari,
        bellek ve CPU sinirlarini konteyner yaratilirken ayirir, sonradan
        degistirilemez. Bu cagri olmasa ayarlar ekranindan yalitimi acmak
        sunucu yeniden baslatilana kadar sessizce etkisiz kalirdi:
        kullanici komutlarin konteynerde kostugunu sanirken konakta
        kosmaya devam ederdi.

        Konteyner SILINIR. Calisma alani disaridan baglandigi icin proje
        dosyalari durur; kaybolan yalnizca konteynerin icine kurulmus
        paketlerdir -- zaten imaj ya da sinir degistiginde yeniden
        kurulmalari gerekir.
        """
        for kabin in (self._sandbox, getattr(self.ctx, "_sandbox", None)):
            if kabin is not None:
                kabin.close()
        self._sandbox = None
        # Arac baglami kendi kabinini tembel kurar; o el de birakilmali,
        # yoksa `run_command` eski ayarlarla kurulmus nesneyi kullanmaya
        # devam eder.
        self.ctx._sandbox = None  # noqa: SLF001 - baglami tasiyan tek yer

        if self.settings.execution == "docker":
            from ..sandbox import Sandbox

            self._sandbox = Sandbox(
                workspace=self.settings.workspace,
                image=self.settings.sandbox_image,
                port_base=self.settings.sandbox_port_base,
                port_count=self.settings.sandbox_port_count,
                memory=self.settings.sandbox_memory,
                cpus=self.settings.sandbox_cpus,
                pids_limit=self.settings.sandbox_pids,
                setup=self.settings.sandbox_setup,
            )
        self.services.sandbox = self._sandbox

    def _stopped(self) -> bool:
        return self.should_stop is not None and self.should_stop()

    def close(self) -> None:
        # Once tarayici: acik kalan bir Chrome sureci ve vekil is parcacigi,
        # surec sonlansa bile porta tutunur.
        self.services.stop_all()
        self.browser.close()
        self.kb.close()
        self.state.close()
        # Konteyner en sona: servisler once duzgun durdurulsun, sonra kabin
        # tumden silinsin. Kapatilmazsa her kosu arkasinda bir konteyner
        # birakirdi -- konakta biriken yetim sureclerin konteyner hali.
        if self._sandbox is not None:
            self._sandbox.close()

    def __enter__(self) -> Orchestrator:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Genel kosu
    # ------------------------------------------------------------------ #
    def run(
        self,
        phases: list[Phase],
        *,
        goal: str = "",
        brief: str | None = None,
        sources: list[Path] | None = None,
        force: bool = False,
        task_key: str | None = None,
        package_force: bool = False,
        run_id: str | None = None,
        plan_id: str | None = None,
        title: str = "",
    ) -> RunReport:
        """Verilen fazlari sirayla yurutur.

        Iki sey kosuyu durdurur: basarisiz bir faz, ya da bir fazin kullaniciya
        cevaplanmamis bloke edici soru birakmasi. Ikincisi bir hata degildir —
        ajan isini yapmis, ilerlemek icin yalnizca kullanicidan bilgi bekliyor.
        """
        if goal:
            self.state.set_meta("goal", goal)
        if brief is not None:
            self.state.set_meta("brief", brief)

        # Her kosu kendi kimligiyle kalicidir: faz durumu projeye ait ve
        # tekrar kosuda uzerine yazilir, kosu kaydi ise gecmiste kalir.
        run_id = run_id or uuid.uuid4().hex[:12]
        run_goal = goal or self.state.get_meta("goal", "")
        run_brief = brief if brief is not None else self.state.get_meta("brief", "")
        # Her gelistirme bir is akisi, kosular onun adimlari. Is akisi
        # kimligi hedef kimligidir: ayni hedefle baslatilan kosular ayni
        # cabanin adimlaridir, hedef degisince yeni bir is akisi acilir.
        workflow = self.state.workflow_for_goal(run_goal, brief=run_brief)
        seq = self.state.start_run(
            run_id,
            goal=run_goal,
            brief=run_brief,
            phases=[str(p) for p in phases],
            title=title,
            workflow_id=workflow["id"],
            task_key=task_key or "",
            plan_id=plan_id or "",
        )
        self.events.current_run = run_id
        self._run_id = run_id
        self.events.emit(
            "phase", "run",
            f"is akisi #{workflow['seq']} · adim #{seq}: "
            + " -> ".join(str(p) for p in phases),
            run_id=run_id, seq=seq, workflow_seq=workflow["seq"],
        )

        report = RunReport(run_id=run_id, seq=seq, workflow_id=workflow["id"],
                           workflow_seq=workflow["seq"])
        for index, phase in enumerate(phases):
            if self._stopped():
                self.events.emit("warn", "run", t("run.cancelled"))
                report.phases.append(
                    PhaseResult(phase=phase, status=Status.CANCELLED, summary="durduruldu")
                )
                break

            # Kapi faz BASLAMADAN once de kontrol edilir: onceki kosudan kalan
            # cevaplanmamis sorularla yeni bir faza girmek anlamsizdir.
            gate = self._gate()
            if gate is not None:
                report.phases.append(gate)
                report.needs_input = True
                break

            reason = self._skip_reason(phase, force)
            if reason is not None:
                self.events.emit("phase", str(phase), t("phase.skipped", reason=reason))
                self.state.start_run_step(run_id, phase, index)
                self.state.finish_run_step(
                    run_id, phase, status=Status.SKIPPED, summary=reason
                )
                report.phases.append(
                    PhaseResult(phase=phase, status=Status.SKIPPED, summary=reason)
                )
                continue

            console.rule(f"[phase]{phase.index + 1}/{len(Phase.ordered())} · {phase.label}[/phase]")
            self._step_ordinal = index
            result = self.run_phase(
                phase,
                sources=sources,
                force=force,
                task_key=task_key,
                package_force=package_force,
                plan_id=plan_id,
            )
            report.phases.append(result)
            report.total_cost += result.cost

            if not result.ok:
                self.events.emit("error", str(phase), result.error or t("phase.failed"))
                break

        # Onizleme izinleri kosuya baglidir. Bir kosu icin acilan yerel port,
        # sonraki kosuda acik kalmamali: yetki ne kadar dar ve ne kadar kisa
        # sureli olursa o kadar iyi.
        # Ajanin baslattigi servisler kosuya baglidir. Yarim kalmis bir dev
        # sunucusunun portu tutmaya devam etmesi, bir sonraki kosuyu "port
        # dolu" ile karsilar ve sebebi gorunmez olur.
        kapatilan = self.services.stop_all()
        if kapatilan:
            self.events.emit(
                "run", "run", t("service.closed_all", names=", ".join(kapatilan))
            )

        self.browser.policy.revoke_all()
        # Tarayici oturumu da kosuyla birlikte kapanir. Playwright'in senkron
        # nesneleri kendilerini olusturan is parcacigina baglidir; her kosu
        # ayri bir is parcaciginda calistigi icin oturumu kosular arasinda
        # tasimak "greenlet in another thread" hatasi verirdi. Cerezler
        # profil dizininde kaldigi icin girisler yine korunur.
        self.browser.close()

        # Son fazdan sonra da kapiyi degerlendir; kullanici cevapsiz sorulari gorsun.
        if not report.needs_input and report.ok:
            gate = self._gate()
            if gate is not None:
                report.phases.append(gate)
                report.needs_input = True

        failed = report.failed_phase()
        outcome = (
            Status.NEEDS_INPUT if report.needs_input
            else (Status.FAILED if failed else Status.DONE)
        )
        self.state.finish_run(
            run_id, status=outcome, cost_usd=report.total_cost,
            error=(failed.error or "") if failed else "",
        )
        self.events.current_run = None
        self._run_id = None
        return report

    # ------------------------------------------------------------------ #
    # Faz 11: teslimat paketi (LLM gerektirmez)
    # ------------------------------------------------------------------ #
    def _run_package(self, *, force: bool = False) -> PhaseResult:
        """Uretilen projeyi zip olarak paketler.

        Deterministik: model cagrisi yok. Hazirlik denetimi gecmezse paketlemez —
        yarim bir isi "tamam" diye teslim etmek yaniltici olur.
        """
        try:
            result = build_package(
                self.state,
                self.settings.workspace,
                self.settings.deliveries_dir,
                goal=self.state.get_meta("goal", ""),
                force=force,
                run_id=self._run_id or "",
            )
        except PackagingNotReady as exc:
            self.events.emit("warn", t("actor.delivery"), t("pipeline.not_ready"))
            return PhaseResult(
                phase=Phase.PACKAGE,
                status=Status.BLOCKED,
                summary=exc.readiness.report(),
                error=(
                    "Proje teslim edilecek durumda degil. Eksikleri kapatin ya da "
                    "`deerx package --force` ile bilerek zorlayin."
                ),
                details={"blockers": [i.message for i in exc.readiness.blockers]},
            )
        except PackagingError as exc:
            return PhaseResult(phase=Phase.PACKAGE, status=Status.FAILED, error=str(exc))

        # Artifakt kaydini `build_package` yapar; CLI, web ve faz ayni yoldan gecer.
        summary = (
            f"{result.file_count} dosya paketlendi ({result.total_bytes / 1e6:.1f} MB) "
            f"-> {result.path.name}"
        )
        if result.excluded_secrets:
            summary += f" · {len(result.excluded_secrets)} sir dosyasi disarida birakildi"
        self.events.emit("done", "teslimat", summary, path=str(result.path))
        return PhaseResult(
            phase=Phase.PACKAGE,
            status=Status.DONE,
            summary=summary,
            details=result.to_dict(),
        )

    # ------------------------------------------------------------------ #
    # Soru cevaplama
    # ------------------------------------------------------------------ #
    def answer_question(self, key: str, answer: str) -> Question | None:
        """Soruyu cevaplar ve cevabi bilgi tabanina yazar.

        Cevaplar yalnizca proje hafizasinda kalsa ajanlar onlari ancak devredilen
        ozette gorurdu; bilgi tabanina da yazilinca `search_knowledge` ile
        bulunabilir hale gelir — uzun kosularda ozet kirpilsa bile kaybolmaz.
        """
        question = self.state.answer_question(key, answer)
        if question is None:
            return None
        self._reindex_answers()
        self.events.emit("done", "soru", f"{question.key} cevaplandi")
        return question

    def skip_question(self, key: str, assumption: str = "") -> Question | None:
        """Soruyu atlar; ajanlar belirtilen varsayimla ilerler."""
        question = self.state.skip_question(key, assumption)
        if question is None:
            return None
        self._reindex_answers()
        self.events.emit(
            "warn", "soru", f"{question.key} atlandi, varsayim: {question.suggestion or '(yok)'}"
        )
        return question

    def _reindex_answers(self) -> None:
        """Cevaplanmis/atlanmis sorulari bilgi tabanina yazar.

        Mantik `pipeline.answers` icinde: ayni isi is akisi danismani da
        yapiyor ve iki ayri kopya, birinin unutulmasi demek. Bu kod
        tabani o bedeli bir kez odedi -- goruntu gonderme iki istemciye
        ayri yazilmisti, biri unutulmustu.
        """
        reindex_answers(self.state, self.kb)

    # ------------------------------------------------------------------ #
    # Is akisi sohbeti
    # ------------------------------------------------------------------ #
    def chat(self, workflow_id: str, message: str, *, stream: bool = False) -> ChatReply:
        """Bir is akisi hakkinda konusur; istenirse durumunu degistirir.

        Konusma gecmisi modele BAGLAM METNI olarak verilir, konusma
        gecmisi nesnesi olarak degil. Sebep: gecmisin bicimi saglayiciya
        ozgu (Anthropic icerik bloklari, OpenAI `tool_calls`) ve o bicimi
        burada elle kurmak, `LLMClient`in tek sahibi oldugu seye ikinci
        bir sahip eklemek olurdu. Sohbet turlari kisa; metne katlamak
        hem yeterli hem tasinabilir.
        """
        message = (message or "").strip()
        if not message:
            raise DeerXError(t("chat.empty_message"))

        workflow = self.state.get_workflow(workflow_id)
        if workflow is None:
            raise DeerXError(t("chat.no_workflow", id=workflow_id))

        self.events.emit("agent", "danisman", t("chat.started", seq=workflow["seq"]))
        self.state.add_chat_message(workflow_id, role="user", content=message)

        # Arac kapsami baglamdan gelir; model baska bir is akisina gecemez.
        onceki_workflow = self.ctx.workflow_id
        self.ctx.workflow_id = workflow_id
        kayitci = _RecordingRegistry(self.registry.subset(TOOLSETS["danisman"]))
        try:
            agent = build_agent(
                "danisman",
                settings=self.settings,
                client=self.client,
                registry=kayitci,
                context=self.ctx,
                events=self.events,
                stream=stream,
                should_stop=self.should_stop,
            )
            result = agent.run(message, context=self._chat_context(workflow_id))
        finally:
            self.ctx.workflow_id = onceki_workflow

        metin = (result.text or "").strip() or t("chat.no_reply")
        cevap = ChatReply(
            text=metin,
            changes=list(kayitci.applied),
            cost=result.cost,
            iterations=result.iterations,
            error=result.error,
        )
        self.state.add_chat_message(
            workflow_id, role="assistant", content=metin, changes=cevap.changes
        )
        return cevap

    def _chat_context(self, workflow_id: str) -> str:
        """Danismana devredilen baglam: is akisinin durumu + konusma."""
        parcalar = [self.state.workflow_context(workflow_id)]
        gecmis = self.state.chat_history(workflow_id)[:-1]  # son mesaj gorevin kendisi
        if gecmis:
            parcalar += ["", "## Bu ana kadarki konusma"]
            for mesaj in gecmis:
                kim = "Kullanici" if mesaj["role"] == "user" else "Sen"
                parcalar.append(f"\n**{kim}:** {mesaj['content']}")
                if mesaj["changes"]:
                    parcalar.append(
                        "  _(degistirdiklerin: " + "; ".join(mesaj["changes"]) + ")_"
                    )
        return "\n".join(parcalar)

    # ------------------------------------------------------------------ #
    # Faz kapisi
    # ------------------------------------------------------------------ #
    def _gate(self) -> PhaseResult | None:
        """Cevaplanmamis bloke edici soru varsa boru hattini durduran sonuc doner.

        Yanlis bir varsayimla ilerlemek, durup sormaktan neredeyse her zaman
        daha pahaliya patlar: hatali varsayim mimariye, plana ve koda sizar.
        """
        pending = self.state.open_blocking_questions()
        if not pending:
            return None

        listing = "\n".join(f"  {q.key}: {q.question}" for q in pending)
        summary = (
            f"{len(pending)} soru cevap bekliyor; boru hatti durdu.\n{listing}\n\n"
            "Cevaplamak icin:  deerx answer Q-001 \"cevabiniz\"\n"
            "Varsayimla gecmek icin:  deerx skip Q-001\n"
            "Sonra kaldigi yerden devam edin:  deerx run --from <faz>"
        )
        self.events.emit(
            "needs_input",
            "kapi",
            f"{len(pending)} cevaplanmamis soru boru hattini durdurdu",
            questions=[q.key for q in pending],
        )
        return PhaseResult(
            phase=None,  # kapi bir faz degil
            status=Status.NEEDS_INPUT,
            summary=summary,
            details={"questions": [q.key for q in pending]},
        )

    @staticmethod
    def _same_goal(a: str, b: str) -> bool:
        """Hedefler ozde ayni mi? Bosluk ve buyuk/kucuk harf onemsiz."""
        return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()

    def _on_browser_block(self, url: str, _allowed: bool) -> None:
        """Vekilin reddettigi adresi kullaniciya bildirir.

        Sessizce engellemek, ajanin neden bos sayfa gordugunu kimseye
        anlatmaz; kullanici da ajanin nereye gitmeye calistigini gormeli.
        """
        self.events.emit("warn", "browser", f"engellendi: {url[:160]}")

    def _skip_reason(self, phase: Phase, force: bool) -> str | None:
        """Faz atlanacaksa gerekcesi, atlanmayacaksa None.

        Tamamlanmis bir fazi tekrar kosmamak dogru; ama tamamlanma tek
        basina yeterli bir olcut degil:

        * `ingest` hicbir zaman butun olarak atlanmaz. Isi yeni ya da
          degismis belgeleri fark etmek; kendi icinde dosya dosya ve hash
          uzerinden zaten atliyor, yani tekrar kosmak ucuz. Butun fazi
          atlarsak kullanicinin `docs/` altina yeni biraktigi sartname hic
          indekslenmez ve ajanlar onu hic gormeden cevap verir.
        * Diger fazlar yalnizca *ayni hedef icin* tamamlanmissa atlanir.
          Hedef degistiginde eldeki analiz baska bir projeye aittir;
          "zaten tamam" deyip gecmek, yeni hedefin analizi hic yapilmadan
          sonraki fazlarin eski analizin uzerine insa etmesi demektir.
        """
        if force or phase is Phase.INGEST:
            return None
        state = self.state.phase_status(phase)
        if state.status != Status.DONE:
            return None
        goal = self.state.get_meta("goal", "")
        if not self._same_goal(state.goal, goal):
            self.events.emit(
                "warn", str(phase),
                t("pipeline.goal_changed"),
            )
            return None
        return "onceden tamamlandi (tekrar icin: fazlari yeniden calistir)"

    def run_phase(
        self,
        phase: Phase,
        *,
        sources: list[Path] | None = None,
        force: bool = False,
        task_key: str | None = None,
        package_force: bool = False,
        plan_id: str | None = None,
    ) -> PhaseResult:
        """Tek bir fazi yurutur.

        `force` "tamamlanmis fazi tekrar calistir" demektir; `package_force` ise
        "teslimat hazirlik kapisini bilerek gec". Ikisi ayri tutulur: bir kosuyu
        bastan almak isteyen kullanici, yarim bir projeyi teslim etmeyi istemis
        olmaz.
        """
        self.state.start_phase(phase)
        # Bu fazda uretilen her olay faza etiketlensin; arayuz kosuyu
        # adim adim ancak boyle gruplayabilir.
        self.events.current_phase = str(phase)
        if self._run_id:
            self.state.start_run_step(self._run_id, phase, self._step_ordinal)
        try:
            if phase is Phase.INGEST:
                result = self._run_ingest(sources or [], force=force)
            elif phase is Phase.PACKAGE:
                result = self._run_package(force=package_force)
            elif phase is Phase.IMPLEMENT:
                result = self._run_implement(task_key=task_key, plan_id=plan_id)
            else:
                result = self._run_agent_phase(phase)
        except DeerXError as exc:
            result = PhaseResult(phase=phase, status=Status.FAILED, error=str(exc))
        except KeyboardInterrupt:
            result = PhaseResult(phase=phase, status=Status.FAILED, error="kullanici durdurdu")

        self.state.finish_phase(
            phase,
            status=result.status,
            summary=result.summary,
            cost_usd=result.cost,
        )
        if self._run_id:
            self.state.finish_run_step(
                self._run_id, phase, status=result.status,
                summary=result.summary, cost_usd=result.cost, error=result.error or "",
            )
        self.events.current_phase = None
        return result

    # ------------------------------------------------------------------ #
    # Faz 1: dokuman alimi (LLM gerektirmez)
    # ------------------------------------------------------------------ #
    def _run_ingest(self, sources: list[Path], *, force: bool) -> PhaseResult:
        targets = list(sources)
        if not targets:
            # Kaynak verilmediyse: bilinen dokuman dizinleri + calisma alani koku.
            for candidate in ("docs", "doc", "specs", "spec", "dokuman", "requirements"):
                path = self.settings.workspace / candidate
                if path.is_dir():
                    targets.append(path)
            if not targets:
                targets.append(self.settings.workspace)

        indexed = skipped = failed = 0
        chunks = 0
        problems: list[str] = []

        for target in targets:
            self.events.emit("phase", "ingest", f"taraniyor: {target}")
            for result in self.kb.ingest_path(Path(target), force=force):
                if not result.ok:
                    failed += 1
                    problems.append(f"{result.title}: {result.error}")
                elif result.skipped:
                    skipped += 1
                else:
                    indexed += 1
                    chunks += result.chunks

        stats = self.kb.stats()
        summary = (
            f"{indexed} dosya indekslendi ({chunks} parca), {skipped} atlandi, "
            f"{failed} basarisiz · toplam {stats['chunks']} parca"
        )
        self.events.emit("done", "ingest", summary)
        for problem in problems[:10]:
            self.events.emit("warn", "ingest", problem)

        if stats["chunks"] == 0:
            return PhaseResult(
                phase=Phase.INGEST,
                status=Status.FAILED,
                summary=summary,
                error=(
                    "Bilgi tabani bos kaldi. Sartname dosyasini acikca verin:\n"
                    "  deerx run --doc <dosya.pdf|md|docx>"
                ),
            )

        return PhaseResult(
            phase=Phase.INGEST,
            status=Status.DONE,
            summary=summary,
            details={"indexed": indexed, "skipped": skipped, "failed": failed, **stats},
        )

    # ------------------------------------------------------------------ #
    # Ajan yuruten fazlar
    # ------------------------------------------------------------------ #
    def _phase_context(self, phase: Phase) -> str:
        """Faza girerken devredilen degisken baglam (sistem prompt'una konmaz)."""
        goal = self.state.get_meta("goal", "")
        brief = self.state.get_meta("brief", "")
        stats = self.kb.stats()
        parts = [
            "# Devralinan proje durumu",
            f"Bilgi tabani: {stats['documents']} dokuman / {stats['chunks']} parca "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(stats['by_kind'].items())) or 'bos'})",
        ]
        if goal:
            parts.append(f"\nKullanicinin hedefi: {goal}")
        if brief:
            # Kullanicinin kendi talimati sartnameden once gelir: sartname neyin
            # yapilacagini, brief nasil yaklasilacagini soyler.
            parts.append(
                "\n## Kullanicinin talimati\n"
                "Asagidaki metni kullanici bu kosu icin dogrudan size yazdi. "
                "Sartnameyle celistigi yerde kullaniciya sorun.\n\n"
                f"{brief}"
            )
        parts.append("\n" + self.state.snapshot())
        return "\n".join(parts)

    def _phase_task(self, phase: Phase) -> str:
        """Faza ozgu gorev talimati."""
        return {
            Phase.ANALYZE: (
                "Bilgi tabanindaki sartnameyi analiz et. Gereksinimleri ve belirsizlikleri "
                "kaydet, `analiz-raporu.md` ciktisini uret."
            ),
            Phase.RESEARCH: (
                "Analiz ciktisindaki teknoloji secimlerini ve acik sorulari web'de arastir. "
                "Bulgulari kaynagiyla kaydet, `arastirma-notlari.md` ciktisini uret."
            ),
            Phase.ASSESS: (
                "Sartname, mevcut kod ve arastirma bulgularini karsilastir. Bosluklari, "
                "riskleri ve iyilestirme firsatlarini kaydet, `bosluk-analizi.md` uret."
            ),
            Phase.DESIGN: (
                "Mimariyi tasarla ve kararlari kaydet. `mimari.md` ile birlikte ana ekranlar "
                "icin tek dosyalik HTML mockup'lari uret."
            ),
            Phase.PLAN: (
                "Mimariyi uygulanabilir gorevlere bol. Her goreve bir `lane` ata "
                "(backend / frontend / qa / infra / docs), bagimliliklari ve dogrulanabilir "
                "kabul olcutlerini belirle, `gelistirme-plani.md` uret."
            ),
            Phase.MOCKUP: (
                "Sartnamedeki ana kullanim akislari icin tek dosyalik, calisan HTML mockup'lar "
                "uret. Bos ve hata durumlarini da goster, `mockup-notlari.md` yaz."
            ),
            Phase.QA: (
                "Uygulanan isi calistirarak dogrula: mevcut testleri kos, eksik testleri yaz, "
                "kenar durumlari zorla. Bulgulari kaydet ve `qa-raporu.md` uret."
            ),
            Phase.STAGING: (
                "Uygulamayi temiz bir ortamda ayaga kaldir ve duman testinden gecir. "
                "`staging-raporu.md` uret."
            ),
            Phase.LIVE: (
                "Cikis oncesi kapiyi kontrol et; gecerse dagitimi yurut, gecmezse eksikleri "
                "kaydedip dur. `canli-cikis-raporu.md` uret."
            ),
            Phase.REVIEW: (
                "Uygulanan isi gereksinimlere karsi denetle. Kabul olcutlerini yeniden calistir, "
                "bulgulari kaydet ve `dogrulama-raporu.md` uret."
            ),
        }[phase]

    def _run_agent_phase(self, phase: Phase) -> PhaseResult:
        role = PHASE_ROLE[phase]
        agent = build_agent(
            role,
            settings=self.settings,
            client=self.client,
            registry=self.registry,
            context=self.ctx,
            events=self.events,
            stream=self.stream,
            should_stop=self.should_stop,
        )
        result = agent.run(self._phase_task(phase), context=self._phase_context(phase))

        # Modelin konusmayi bitirmesi isini bitirdigi anlamina gelmez.
        # Ciktisini uretmemisse bir kez durtulur; ikinci kez de uretmezse
        # "bitti" demek yanlis olur ve sonraki fazlar bos elle baslar.
        missing = self._missing_deliverable(phase) if result.ok else None
        if missing is not None:
            self.events.emit(
                "warn",
                str(phase),
                t("phase.no_deliverable", pattern=missing),
            )
            retry = agent.run(self._nudge(phase, missing), context=self._phase_context(phase))
            # Maliyet iki denemenin toplami; ikinci deneme de bir cagri.
            retry.cost += result.cost
            result = retry
            missing = self._missing_deliverable(phase) if result.ok else missing

        if missing is not None:
            _, ne = PHASE_DELIVERABLE[phase]
            return PhaseResult(
                phase=phase,
                status=Status.FAILED,
                summary=result.text[:2000],
                cost=result.cost,
                error=t(
                    "phase.missing_deliverable",
                    pattern=missing, what=ne, turns=result.iterations,
                ),
                details={"iterations": result.iterations, "tool_calls": result.tool_calls},
            )
        return self._to_phase_result(phase, result)

    def _missing_deliverable(self, phase: Phase) -> str | None:
        """Faz beklenen ciktisini uretmis mi? Uretmediyse desenini doner."""
        expected = PHASE_DELIVERABLE.get(phase)
        if expected is None:
            return None
        pattern, _ = expected
        names = [a.name for a in self.state.list_artifacts()]
        if fnmatch.filter(names, pattern):
            return None
        # Plan fazinda gorev kaydi da sayilir: rapor yazilmamis olabilir ama
        # plan gercekten kaydedilmisse is yapilmistir.
        if phase is Phase.PLAN and self.state.list_tasks():
            return None
        return pattern

    @staticmethod
    def _nudge(phase: Phase, pattern: str) -> str:
        """Erken duran ajana ne eksik oldugunu soyler."""
        _, ne = PHASE_DELIVERABLE[phase]
        return t("phase.nudge", pattern=pattern, what=ne)

    @staticmethod
    def _to_phase_result(phase: Phase, result: AgentResult) -> PhaseResult:
        if not result.ok:
            return PhaseResult(
                phase=phase,
                status=Status.FAILED,
                summary=result.text[:600],
                cost=result.cost,
                error=result.error,
            )
        status = {
            "max_iterations": Status.BLOCKED,
            "cancelled": Status.CANCELLED,
        }.get(result.stop_reason, Status.DONE)
        return PhaseResult(
            phase=phase,
            status=status,
            summary=result.text[:2000],
            cost=result.cost,
            details={
                "iterations": result.iterations,
                "tool_calls": result.tool_calls,
                "stop_reason": result.stop_reason,
            },
        )

    # ------------------------------------------------------------------ #
    # Faz 7: uygulama (gorev basina taze ajan)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _role_for_task(task: Task) -> str:
        """Gorevin seridine gore uygulayici rolu secer."""
        return LANE_ROLE.get(task.lane, "backend")

    def _task_prompt(self, task: Task) -> str:
        lines = [
            f"# Gorev {task.key}: {task.title}",
            f"Serit: {task.lane} · Tur: {task.kind}",
            "",
            task.description or "(aciklama yok — gereksinimlerden cikar)",
        ]
        if task.files:
            lines += ["", "## Dokunulacak dosyalar", *(f"- {f}" for f in task.files)]
        if task.acceptance:
            lines += ["", "## Kabul olcutu", task.acceptance]
        lines += [
            "",
            "Bu gorevi bastan sona tamamla: kodu yaz, kabul olcutunu `run_command` ile "
            "gercekten calistir, sonra `update_task` ile durumu ve dogrulama ciktisini kaydet. "
            "Yalnizca BU gorevi yap; sonraki gorevlere gecme.",
        ]
        return "\n".join(lines)

    def _run_implement(
        self, *, task_key: str | None = None, plan_id: str | None = None
    ) -> PhaseResult:
        # Plan verilmezse etkin plan uygulanir; boylece paralel planlar
        # birbirinin gorevlerini calmaz.
        if task_key is None and plan_id is None:
            plan_id = self.state.active_plan_id()
        tasks = self.state.list_tasks(plan_id=plan_id)
        if not tasks:
            return PhaseResult(
                phase=Phase.IMPLEMENT,
                status=Status.FAILED,
                error="Plan bos. Once `plan` fazini calistirin.",
            )

        completed: list[str] = []
        failed: list[str] = []
        total_cost = 0.0

        for _ in range(MAX_TASKS_PER_RUN):
            if self._stopped():
                self.events.emit("warn", "implement", t("run.stopped"))
                break
            if task_key:
                task = self.state.get_task(task_key.upper())
                if task is None:
                    return PhaseResult(
                        phase=Phase.IMPLEMENT,
                        status=Status.FAILED,
                        error=f"'{task_key}' diye bir gorev yok.",
                    )
                if task.status == Status.DONE:
                    return PhaseResult(
                        phase=Phase.IMPLEMENT,
                        status=Status.DONE,
                        summary=f"{task.key} zaten tamamlanmis.",
                    )
            else:
                ready = self.state.ready_tasks(plan_id=plan_id)
                if not ready:
                    break
                task = ready[0]

            role = self._role_for_task(task)
            self.state.update_task(task.key, status=Status.RUNNING)
            console.rule(f"[agent]{task.key} · {role} · {task.title}[/agent]")
            self.events.emit(
                "agent", role, f"{task.key} ustlenildi ({task.lane} seridi)", task=task.key
            )

            # Her gorev icin taze ajan: baglam temiz kalir, maliyet ongorulebilir olur.
            agent = build_agent(
                role,
                settings=self.settings,
                client=self.client,
                registry=self.registry,
                context=self.ctx,
                events=self.events,
                stream=self.stream,
                should_stop=self.should_stop,
            )
            result = agent.run(self._task_prompt(task), context=self._phase_context(Phase.IMPLEMENT))
            total_cost += result.cost

            # Ajan `update_task` cagirmayi unuttuysa sonucu biz yaziyoruz.
            current = self.state.get_task(task.key)
            if current is not None and current.status == Status.RUNNING:
                self.state.update_task(
                    task.key,
                    status=Status.DONE if result.ok else Status.FAILED,
                    result=result.text[:4000],
                )
                current = self.state.get_task(task.key)

            if current is not None and current.status == Status.DONE:
                completed.append(task.key)
            else:
                failed.append(task.key)
                self.events.emit(
                    "warn",
                    "implement",
                    f"{task.key} tamamlanamadi ({current.status if current else '?'})",
                )

            if task_key:
                break

        # Yalnizca PENDING degil: bloke ve yarim kalan gorevler de bitmemistir.
        remaining = [
            t.key
            for t in self.state.list_tasks(plan_id=plan_id)
            if t.status in {Status.PENDING, Status.RUNNING, Status.BLOCKED}
        ]
        by_lane: dict[str, int] = {}
        for key in completed:
            done_task = self.state.get_task(key)
            if done_task is not None:
                by_lane[done_task.lane] = by_lane.get(done_task.lane, 0) + 1
        lanes = ", ".join(f"{lane}: {n}" for lane, n in sorted(by_lane.items()))
        summary = (
            f"{len(completed)} gorev tamamlandi, {len(failed)} basarisiz, "
            f"{len(remaining)} bekliyor." + (f" ({lanes})" if lanes else "")
        )
        self.events.emit("done", "implement", summary)

        status = Status.DONE if not failed and not remaining else Status.BLOCKED
        return PhaseResult(
            phase=Phase.IMPLEMENT,
            status=status,
            summary=summary,
            cost=total_cost,
            details={"completed": completed, "failed": failed, "remaining": remaining},
        )
