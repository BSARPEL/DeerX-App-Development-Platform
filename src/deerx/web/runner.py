"""Web arayuzu icin kosu yoneticisi.

Orkestrator senkron calisir; web sunucusu asenkron. Arayi bir arka plan thread'i
kapatir. Uc mekanizma burada birlesir:

    olay tamponu : ajanin urettigi olaylar sira numarali bir halkaya yazilir,
                   SSE ucu son gordugu sira numarasindan itibaren okur.
    onay kapisi  : `approval_hook` kosu thread'ini bloke eder, tarayici cevabi
                   gelince serbest birakir. Boylece CLI'daki onay akisi web'de
                   de aynen calisir.
    durdurma     : isbirlikci bayrak; ajan her tur basinda kontrol eder.

Ayni anda tek kosu yurutulur — ikinci bir istek `RunBusy` ile reddedilir.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import DeerXError
from ..i18n import t
from ..logging import Event, get_logger
from ..pipeline import Orchestrator, Phase, Status

log = get_logger("web.runner")

# Tamponda tutulan azami olay sayisi. Uzun kosularda eski olaylar dusurulur;
# tarayici zaten gormustur, kalici kayit `.deerx/events.jsonl` dosyasindadir.
EVENT_BUFFER = 4000

# Tarayicidan cevap gelmezse onay bu sure sonunda reddedilmis sayilir.
APPROVAL_TIMEOUT_SECONDS = 900


class RunBusy(DeerXError):
    """Zaten calisan bir kosu varken yeni kosu istendi."""


@dataclass
class ApprovalRequest:
    """Kosu thread'inin tarayicidan bekledigi tek bir onay."""

    id: str
    action: str
    detail: str
    created_at: float
    granted: bool | None = None
    _gate: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "detail": self.detail,
            "created_at": self.created_at,
            "granted": self.granted,
        }

    def resolve(self, granted: bool) -> None:
        self.granted = granted
        self._gate.set()

    def wait(self, timeout: float) -> bool:
        if not self._gate.wait(timeout):
            self.granted = False
        return bool(self.granted)


@dataclass
class RunInfo:
    """Bir kosunun ozeti."""

    id: str
    phases: list[str]
    goal: str
    started_at: float
    # Kosunun ne oldugu: "T-001 · Saglik ucu", "Plan: Mobil", "Analiz" gibi.
    title: str = ""
    # Ayni basligin cevrilebilir hali. Arayuz once buna bakar; `title`
    # sunucunun o anki dilinde yazilmis metindir ve dil degisince donmez.
    title_key: str = ""
    title_args: dict[str, Any] = field(default_factory=dict)
    # Kullaniciya gosterilen sirali numara (#1, #2 ...).
    seq: int = 0
    finished_at: float | None = None
    status: str = "running"  # running | done | failed | cancelled | needs_input
    error: str | None = None
    cost: float = 0.0
    results: list[dict[str, Any]] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "phases": self.phases,
            "title": self.title,
            "title_key": self.title_key,
            "title_args": self.title_args,
            "goal": self.goal,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "error": self.error,
            "cost": round(self.cost, 4),
            "results": self.results,
            "pending_questions": self.pending_questions,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
        }


class RunManager:
    """Arka plan kosularini yoneten tekil nesne."""

    def __init__(self, settings: Settings, orchestrator: Orchestrator) -> None:
        self.settings = settings
        self.orchestrator = orchestrator

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

        self._buffer: deque[dict[str, Any]] = deque(maxlen=EVENT_BUFFER)
        self._seq = 0

        self._approvals: dict[str, ApprovalRequest] = {}
        self._current: RunInfo | None = None
        self._last: RunInfo | None = None

        # Orkestratorun olay gunlugunu dinle ve tampona yaz.
        orchestrator.events.subscribe(self._on_event)
        # Onay istekleri artik konsola degil tarayiciya gider.
        orchestrator.ctx.approval_hook = self._request_approval
        orchestrator.should_stop = self._stop_flag.is_set

    # ------------------------------------------------------------------ #
    # Olay tamponu
    # ------------------------------------------------------------------ #
    def _on_event(self, event: Event) -> None:
        with self._lock:
            self._seq += 1
            self._buffer.append(
                {
                    "seq": self._seq,
                    "ts": event.ts,
                    "kind": event.kind,
                    "actor": event.actor,
                    "message": event.message,
                    "data": event.data,
                }
            )

    def events_since(self, seq: int) -> tuple[list[dict[str, Any]], int]:
        """`seq`den sonraki olaylari ve yeni son sira numarasini doner."""
        with self._lock:
            fresh = [e for e in self._buffer if e["seq"] > seq]
            return fresh, self._seq

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def emit(self, kind: str, actor: str, message: str, **data: Any) -> None:
        """Web katmanindan olay yayinlar (kosu disi bilgilendirmeler icin)."""
        self.orchestrator.events.emit(kind, actor, message, **data)

    # ------------------------------------------------------------------ #
    # Onay kapisi
    # ------------------------------------------------------------------ #
    def _request_approval(self, action: str, detail: str) -> bool:
        """Kosu thread'inde cagrilir; tarayici cevaplayana kadar bloke eder."""
        request = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            action=action,
            detail=detail,
            created_at=time.time(),
        )
        with self._lock:
            self._approvals[request.id] = request

        self.orchestrator.events.emit(
            "approval", "onay", action, approval_id=request.id, detail=detail[:2000]
        )
        granted = request.wait(APPROVAL_TIMEOUT_SECONDS)

        with self._lock:
            self._approvals.pop(request.id, None)
        self.orchestrator.events.emit(
            "tool" if granted else "warn",
            "onay",
            f"{'onaylandi' if granted else 'reddedildi'}: {action[:120]}",
            approval_id=request.id,
        )
        return granted

    def pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            return [a.to_dict() for a in self._approvals.values()]

    def resolve_approval(self, approval_id: str, granted: bool) -> bool:
        with self._lock:
            request = self._approvals.get(approval_id)
        if request is None:
            return False
        request.resolve(granted)
        return True

    def _reject_all_approvals(self) -> None:
        """Durdurma sirasinda bekleyen onaylari serbest birak; thread takilmasin."""
        with self._lock:
            pending = list(self._approvals.values())
        for request in pending:
            request.resolve(False)

    # ------------------------------------------------------------------ #
    # Kosu yasam dongusu
    # ------------------------------------------------------------------ #
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            current = self._current.to_dict() if self._current else None
            last = self._last.to_dict() if self._last else None
        return {
            "running": self.is_running,
            "stopping": self._stop_flag.is_set() and self.is_running,
            "current": current,
            "last": last,
            "pending_approvals": self.pending_approvals(),
            "last_seq": self.last_seq,
        }

    def start(
        self,
        phases: list[Phase],
        *,
        goal: str = "",
        brief: str | None = None,
        sources: list[Path] | None = None,
        force: bool = False,
        task_key: str | None = None,
        plan_id: str | None = None,
        title: str = "",
        title_key: str = "",
        title_args: dict[str, Any] | None = None,
    ) -> RunInfo:
        """Fazlari arka planda baslatir."""
        with self._lock:
            if self.is_running:
                raise RunBusy(t("api.run_busy"))
            if not phases:
                raise DeerXError(t("api.no_phase_selected"))

            self._stop_flag.clear()
            info = RunInfo(
                id=uuid.uuid4().hex[:12],
                phases=[str(p) for p in phases],
                goal=goal,
                title=title,
                title_key=title_key,
                title_args=dict(title_args or {}),
                started_at=time.time(),
            )
            # Kosu kaydini burada, is parcacigi baslamadan ac: cagiran taraf
            # yanitla birlikte kosu numarasini alsin. Arka planda acilirsa
            # POST yaniti `seq: 0` doner ve arayuz kosuya baglanti veremez.
            info.seq = self.orchestrator.state.start_run(
                info.id, goal=goal, brief=brief or "",
                phases=info.phases, title=title,
                title_key=title_key, title_args=info.title_args,
                task_key=task_key or "", plan_id=plan_id or "",
            )
            self._current = info

        self._thread = threading.Thread(
            target=self._run,
            args=(info, phases, goal, brief, sources or [], force, task_key, plan_id, title),
            name=f"deerx-run-{info.id}",
            daemon=True,
        )
        self._thread.start()
        return info

    def _run(
        self,
        info: RunInfo,
        phases: list[Phase],
        goal: str,
        brief: str | None,
        sources: list[Path],
        force: bool,
        task_key: str | None,
        plan_id: str | None = None,
        title: str = "",
    ) -> None:
        try:
            # Kimlik burada uretilir ve orkestratore verilir: kalici kosu
            # kaydiyla bellekteki kaydin ayni kimligi tasimasi sart, aksi
            # halde "su an calisan kosu hangisi" sorusu cevapsiz kalir.
            report = self.orchestrator.run(
                phases, goal=goal, brief=brief, sources=sources,
                force=force, task_key=task_key, run_id=info.id, plan_id=plan_id,
                title=title,
            )
            info.seq = report.seq
            info.cost = report.total_cost
            info.results = [
                {
                    "phase": str(r.phase) if r.phase is not None else "gate",
                    "label": r.label,
                    "status": r.status,
                    "cost": round(r.cost, 4),
                    "summary": r.summary,
                    "error": r.error,
                    "details": r.details,
                }
                for r in report.phases
            ]
            if self._stop_flag.is_set():
                info.status = "cancelled"
            elif report.needs_input:
                # Basarisizlik degil: ajan isini yapti, kullanicidan cevap bekliyor.
                info.status = "needs_input"
                info.pending_questions = report.pending_questions()
            elif report.ok:
                info.status = "done"
            else:
                info.status = "failed"
                failed = report.failed_phase()
                info.error = failed.error if failed else "faz basarisiz"
        except DeerXError as exc:
            info.status = "failed"
            info.error = str(exc)
            self.orchestrator.events.emit("error", "run", str(exc))
        except Exception as exc:  # noqa: BLE001 - thread'de kacan hata sessiz kalmasin
            log.exception(t("api.run_crashed"))
            info.status = "failed"
            info.error = f"{type(exc).__name__}: {exc}"
            self.orchestrator.events.emit("error", "run", info.error)
        finally:
            info.finished_at = time.time()
            with self._lock:
                self._current = None
                self._last = info
            self._reject_all_approvals()
            self.orchestrator.events.emit(
                "done" if info.status == "done" else "warn",
                "run",
                t(
                    "run.finished",
                    status=info.status,
                    cost=f"{info.cost:.4f}",
                    seconds=info.to_dict()["elapsed"],
                ),
                run_id=info.id,
                status=info.status,
            )

    def stop(self) -> bool:
        """Isbirlikci durdurma. Devam eden model cagrisi tamamlanir, sonra durulur."""
        if not self.is_running:
            return False
        self._stop_flag.set()
        # Bekleyen onaylar reddedilir; aksi halde thread onay kapisinda asili kalir.
        self._reject_all_approvals()
        self.orchestrator.events.emit(
            "warn", "run", "durdurma istendi; suregelen adim bitince durulacak"
        )
        return True

    def wait(self, timeout: float | None = None) -> None:
        """Testler icin: kosu thread'inin bitmesini bekler."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def phase_selection(names: list[str]) -> list[Phase]:
    """Kullanicinin sectigi fazlari boru hatti sirasina dizer.

    Uc kural var:
      - Sira kullanicinin tiklama sirasi degil, boru hattinin kendi sirasidir;
        `plan`i `analyze`den once kosturmak anlamsizdir.
      - Tekrarlar duser.
      - `ingest` her zaman vardir: bilgi tabani bos ise sonraki her faz
        okuyacak bir sey bulamaz. Zaten tamamlanmissa `run()` onu atlar,
        yani bedeli yoktur.
    """
    chosen: set[Phase] = {Phase.INGEST}
    for name in names:
        try:
            chosen.add(Phase(str(name).strip().lower()))
        except ValueError as exc:
            valid = ", ".join(str(p) for p in Phase)
            raise DeerXError(t("api.unknown_phase", name=name, options=valid)) from exc
    return [p for p in Phase.ordered() if p in chosen]


def phase_range(start: str, end: str) -> list[Phase]:
    """`start`--`end` arasindaki fazlari (dahil) doner."""
    try:
        first, last = Phase(start.lower()), Phase(end.lower())
    except ValueError as exc:
        valid = ", ".join(str(p) for p in Phase)
        raise DeerXError(t("api.unknown_phase_plain", options=valid)) from exc
    if first.index > last.index:
        raise DeerXError(t("api.phase_order", start=start, end=end))
    return [p for p in Phase.ordered() if first.index <= p.index <= last.index]


# Bir adimin "burada bir hata var" sayildigi durumlar.
#
# `needs_input` bilerek disarida: ajan isini yapmis, yalnizca kullanicidan
# cevap bekliyor. Onu hata sayip tekrar kosmak, cevabi bekleyen soruyu
# ikinci kez sormaktan baska bir sey yapmaz.
RETRY_STATUSES = (Status.FAILED, Status.BLOCKED, Status.CANCELLED)


def retry_plan(
    record: dict[str, Any],
    steps: list[dict[str, Any]],
    phase: str = "",
) -> tuple[list[Phase], Phase]:
    """Bir kosuyu belirli bir adimdan itibaren yeniden kurar.

    Doner: (kosulacak fazlar, baslangic fazi).

    `phase` verilmezse ilk sorunlu adim secilir. Verilirse o adim secilir --
    basarili bir adim bile olabilir: "buradan itibaren tekrar yap" mesru bir
    istektir ve hata olmadan da istenir.

    Kosulacak liste, KOSUNUN KENDI faz listesinden alinir; boru hattinin tam
    sirasindan degil. Kullanici [ingest, analyze, plan] secip `analyze`da
    hata aldiysa tekrarin `research` ve `assess`i eklemesi, istemedigi isi
    yaptirmak olurdu.

    Sonraki adimlar da listeye girer. Hatali bir adimin uzerine kurulmus
    ciktilar supheli: `plan`, hic uretilmemis `analyze` ciktisini okuyamaz.
    """
    planlanan = [Phase(p) for p in record.get("phases") or []]
    if not planlanan:
        raise DeerXError(t("api.retry_no_phases", seq=record.get("seq", "?")))

    if phase:
        try:
            baslangic = Phase(str(phase).strip().lower())
        except ValueError as exc:
            valid = ", ".join(str(p) for p in Phase)
            raise DeerXError(t("api.unknown_phase", name=phase, options=valid)) from exc
        if baslangic not in planlanan:
            raise DeerXError(
                t("api.retry_phase_not_in_run",
                  phase=str(baslangic), seq=record.get("seq", "?"),
                  phases=", ".join(str(p) for p in planlanan))
            )
    else:
        sorunlu = [
            Phase(row["phase"]) for row in steps
            if row["status"] in RETRY_STATUSES and row["phase"] in set(record["phases"])
        ]
        if not sorunlu:
            raise DeerXError(t("api.retry_nothing_failed", seq=record.get("seq", "?")))
        # Adim satirlari sira numarasina gore gelir; ilki en erken sorundur.
        # En ERKENi secmek onemli: sonraki hatalar cogu zaman ilkinin
        # sonucudur ve arkadakinden baslamak ayni duvara tekrar carpar.
        baslangic = sorunlu[0]

    return [p for p in planlanan if p.index >= baslangic.index], baslangic


# Diskten okunacak azami olay sayisi. Gunluk on alti megabayta kadar
# buyuyebilir; adim dokumu icin son kosunun olaylari yeter.
DISK_EVENT_LIMIT = 8000


def events_from_disk(path: Path, limit: int = DISK_EVENT_LIMIT) -> list[dict[str, Any]]:
    """Kalici olay gunlugunun sonunu okur.

    Olay tamponu bellektedir: sunucu yeniden baslayinca bosalir ve kosu
    dokumu ayrintisiz kalirdi. `.deerx/events.jsonl` kaliciydi zaten,
    yalnizca okunmuyordu.
    """
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = deque(fh, maxlen=limit)
    except OSError:  # pragma: no cover - gunluk okunamazsa dokum bos kalir
        return []

    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:  # yarim yazilmis son satir
            continue
    return events


def attribute_phases(events: list[dict[str, Any]]) -> None:
    """Etiketsiz olaylari faza baglar (yerinde).

    Olaylar artik uretildikleri fazla etiketleniyor, ama bu alan sonradan
    eklendi: daha once yazilmis gunlukler etiketsiz. Onlari da okunur
    kilmak icin aktor adindan cikarim yapariz — faz adi ya da ajan rolu
    goren yerde gecerli faz degisir, aradaki `artifact`/`rag`/`state` gibi
    yardimci olaylar o fazi devralir.
    """
    from ..pipeline.orchestrator import PHASE_ROLE

    role_to_phase = {role: str(phase) for phase, role in PHASE_ROLE.items()}
    # Uygulama fazinda gorev seride gore yonlendirilir; hepsi ayni faza girer.
    for role in ("backend", "frontend"):
        role_to_phase[role] = str(Phase.IMPLEMENT)
    names = {str(p) for p in Phase.ordered()}

    current: str | None = None
    for event in events:
        if event.get("phase"):
            current = event["phase"]
            continue
        actor = event.get("actor", "")
        if actor in names:
            current = actor
        elif actor in role_to_phase:
            current = role_to_phase[actor]
        elif actor == "run":
            current = None
        event["phase"] = current


def workflow_detail(runner: RunManager, state: Any, workflow_id: str) -> dict[str, Any] | None:
    """Bir is akisi ve adimlari.

    Adimlar kosulardir. Her adimin durumu tek bir kelimeyle anlatilabilmeli
    ki kullanici listeye bakip ne yapmasi gerektigini anlasin: calisiyor mu,
    onay mi bekliyor, cevap mi bekliyor, bitti mi.
    """
    record = state.get_workflow(workflow_id)
    if record is None:
        return None

    live = runner.status()
    current = (live.get("current") or {}).get("id")
    approvals = live.get("pending_approvals") or []
    blocking = [q.key for q in state.open_blocking_questions()]

    steps = []
    for run in state.workflow_runs(workflow_id):
        running = bool(live["running"] and run["id"] == current)
        done_steps = sum(
            1 for s in state.run_step_rows(run["id"]) if s["status"] == Status.DONE
        )
        steps.append(
            {
                **run,
                "live": running,
                "steps_done": done_steps,
                "state": _step_state(run, running, approvals, blocking),
                # Onaylar KOSUYA degil surece aittir; yalnizca calisan adimda
                # gosterilir, cunku bekleyen is parcacigi odur.
                "approvals": approvals if running else [],
                "questions": blocking if running and not approvals else [],
            }
        )

    return {
        "workflow": record,
        "steps": steps,
        "live": any(s["live"] for s in steps),
        "cost": round(sum(float(s.get("cost") or 0) for s in steps), 4),
    }


def _step_state(
    run: dict[str, Any], running: bool, approvals: list[Any], blocking: list[str]
) -> str:
    """Adimin kullaniciya gosterilen tek kelimelik hali.

    Durum alanindan ayri tutuluyor: bir kosu veritabaninda `running` olabilir
    ama gercekte kullanicidan onay bekliyordur. "Calisiyor" demek o an
    yanlis bilgi verir -- kullanici bekler, sistem de onu bekler.
    """
    if running:
        if approvals:
            return "needs_approval"
        if blocking:
            return "needs_input"
        return "running"
    if run["status"] == Status.RUNNING:
        # Kayitta calisiyor. Yukaridaki `running` YALNIZCA "bu sunucunun
        # kosu yoneticisi su an bunu yurutuyor" demek -- "hicbir surec
        # yurutmuyor" demek DEGIL.
        #
        # OLCULDU: `deerx run` terminalde `implement` fazini kosarken
        # acilan `deerx serve`, o adimi "yarida kaldi / sunucu yeniden
        # baslatildi" diye gosterdi. Kosu calisiyordu ve token
        # harciyordu; kullanici bitmis sandi. README kosuyu izlemek icin
        # zaten arayuzu oneriyor, yani bu istisna degil beklenen akis.
        #
        # Ayni yanlis varsayim yetim toplamada da vardi ve orada kaydi
        # BOZUYORDU; burada yalnizca yanlis gosteriyor.
        from ..process import process_alive

        if process_alive(int(run.get("pid") or 0)):
            return "running"
        return "stalled"
    return str(run["status"])


def _workflow_state(
    runs: list[dict[str, Any]], running: list[dict[str, Any]],
    approvals: list[Any], blocking: list[str],
) -> str:
    """Is akisinin hali, ADIMLARINDAN turetilir.

    `workflows.status` sutununa bakilmiyor: onu guncel tutan bir sey yok ve
    saklanip guncellenmeyen bir durum, sorulan soruya yanlis cevap verir --
    butun adimlari bitmis bir is akisi "calisiyor" gorunuyordu. Dogru cevap
    zaten adimlarda duruyor.
    """
    if running:
        return _step_state(running[0], True, approvals, blocking)
    if not runs:
        return Status.PENDING
    if any(r["status"] == Status.RUNNING for r in runs):
        return "stalled"          # surec yok ama kayit calisiyor diyor
    if any(r["status"] == Status.FAILED for r in runs[-1:]):
        return Status.FAILED
    return str(runs[-1]["status"])

def workflow_list(runner: RunManager, state: Any, limit: int = 50) -> dict[str, Any]:
    """Is akislari, en yenisi basta."""
    live = runner.status()
    current = (live.get("current") or {}).get("id")
    approvals = live.get("pending_approvals") or []
    blocking = [q.key for q in state.open_blocking_questions()]

    rows = []
    for workflow in state.list_workflows(limit):
        runs = state.workflow_runs(workflow["id"])
        running = [r for r in runs if live["running"] and r["id"] == current]
        rows.append(
            {
                **workflow,
                "runs": len(runs),
                "runs_done": sum(1 for r in runs if r["status"] == Status.DONE),
                "cost": round(sum(float(r.get("cost") or 0) for r in runs), 4),
                "started_at": runs[0]["started_at"] if runs else workflow["created_at"],
                "last_at": runs[-1]["started_at"] if runs else workflow["created_at"],
                "live": bool(running),
                "state": _workflow_state(runs, running, approvals, blocking),
            }
        )
    return {"workflows": rows, "running": live["running"]}


def run_detail(runner: RunManager, state: Any, run_id: str) -> dict[str, Any] | None:
    """Tek bir kosunun adim adim dokumu.

    Adimlar kosunun kendi kaydindan gelir (`run_steps`), faz durumundan
    degil: faz durumu projeye aittir ve her tekrar kosuda uzerine yazilir,
    yani gecmis bir kosuya bakarken yaniltir.
    """
    record = state.get_run(run_id)
    if record is None:
        return None

    events = [e for e in runner.events_since(0)[0] if e.get("run_id") == run_id]
    if not events:
        disk = events_from_disk(runner.settings.events_path)
        events = [e for e in disk if e.get("run_id") == run_id]
        if not events and state.list_runs(2)[:1] == [record]:
            # Kosu kimligi olay gunlugune sonradan eklendi; en son kosu icin
            # etiketsiz kayitlari fazlara dagitip yine de gosterebiliriz.
            attribute_phases(disk)
            events = [e for e in disk if e.get("phase")]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        key = event.get("phase")
        if key:
            grouped.setdefault(key, []).append(event)

    steps = []
    for row in state.run_step_rows(run_id):
        phase = Phase(row["phase"])
        own = grouped.get(row["phase"], [])
        counts: dict[str, int] = {}
        artifacts: list[str] = []
        for event in own:
            counts[event["kind"]] = counts.get(event["kind"], 0) + 1
            if event["kind"] == "done" and event["actor"] == "artifact":
                name = (event.get("data") or {}).get("name")
                if not name:
                    name = event.get("message", "").split(" yazildi", 1)[0].strip()
                if name and name not in artifacts:
                    artifacts.append(name)
        steps.append(
            {
                **row,
                "label": phase.label,
                "agent": phase.agent_label,
                "produces": phase.produces,
                "stage": phase.stage,
                "index": phase.index,
                "artifacts": artifacts,
                "counts": counts,
                "events": own,
            }
        )

    live = runner.status()
    current = live.get("current") or {}
    return {
        "run": record,
        "live": bool(live["running"] and current.get("id") == run_id),
        "steps": steps,
    }


def run_steps(runner: RunManager, state: Any) -> dict[str, Any]:
    """Son kosuyu adim adim anlatir.

    Faz seridi yalnizca *durumu* gosterir; bu, her adimin altinda ne olup
    bittigini verir: ajanin cagirdigi araclar, modelin metni, uretilen
    ciktilar, hatalar ve gecen sure. Olaylar `phase` etiketinden gruplanir —
    aktor adindan cikarim yapmak kirilgan olurdu, cunku ayni rol birden
    fazla fazda kosabilir.
    """
    status = runner.status()
    info = status["current"] or status["last"] or {}
    planned = list(info.get("phases") or [])

    # Kosuya dahil olmayan ama daha once calistirilmis fazlar da gosterilir;
    # kullanici "en son ne yaptim" derken onceki kosulari da kastediyor olur.
    catalog = {c["phase"]: c for c in phase_catalog(state)}
    shown = [p for p in Phase.ordered() if str(p) in planned
             or catalog[str(p)]["status"] != Status.PENDING]

    # Once bellek tamponu (bu oturumun olaylari), sonra diskteki kalici
    # gunluk. Sunucu yeniden baslatildiginda dokum ayrintisiz kalmasin.
    grouped: dict[str, list[dict[str, Any]]] = {}
    memory = runner.events_since(0)[0]
    for event in memory:
        key = event.get("phase")
        if key:
            grouped.setdefault(key, []).append(event)

    if not grouped:
        disk = events_from_disk(runner.settings.events_path)
        attribute_phases(disk)
        for event in disk:
            key = event.get("phase")
            if key:
                grouped.setdefault(key, []).append(event)

    steps = []
    for phase in shown:
        key = str(phase)
        snapshot = state.phase_status(phase)
        events = grouped.get(key, [])
        counts: dict[str, int] = {}
        artifacts = []
        for event in events:
            counts[event["kind"]] = counts.get(event["kind"], 0) + 1
            if event["kind"] == "done" and event["actor"] == "artifact":
                # `name` alani sonradan eklendi; eski gunluklerde yalnizca
                # "<ad> yazildi (N karakter)" mesaji var.
                name = (event.get("data") or {}).get("name")
                if not name:
                    name = event.get("message", "").split(" yazildi", 1)[0].strip()
                if name and name not in artifacts:
                    artifacts.append(name)

        finished = snapshot.finished_at
        started = snapshot.started_at
        elapsed = None
        if started:
            elapsed = round((finished or time.time()) - started, 1)

        steps.append(
            {
                "phase": key,
                "label": phase.label,
                "agent": phase.agent_label,
                "produces": phase.produces,
                "stage": phase.stage,
                "index": phase.index,
                "status": snapshot.status,
                "summary": snapshot.summary,
                "cost": round(snapshot.cost_usd, 4),
                "started_at": started,
                "finished_at": finished,
                "elapsed": elapsed,
                "in_run": key in planned,
                "artifacts": artifacts,
                "counts": counts,
                "events": events,
            }
        )

    return {"run": status, "goal": state.get_meta("goal", ""), "steps": steps}


def workflow_step_load(state: Any, workflow_id: str) -> list[dict[str, Any]]:
    """Bir is akisinin adimlari ve HER ADIMDA BEKLEYEN IS sayisi.

    "Bekleyen is" fazdan faza ayni sey degil ve bunu gizlemek yaniltici
    olurdu:

    * `implement` GERCEK bir kuyruk tasir -- plandaki gorevler. Sayi
      oradaki `pending` gorev sayisidir ve onlarca olabilir.
    * Oteki fazlar tek ajanlidir; kuyruklari yoktur. Orada bekleyen is
      FAZIN KENDISIDIR: kosulmadiysa 1, kosulduysa 0.

    Boylece ray "nerede yigilma var" sorusunu cevaplar: implement 17,
    qa 1, review 1 -- darbogazin nerede oldugu tek bakista gorunur.

    Durum is akisina gore cozulur, proje geneline gore degil: ayni proje
    icinde birden fazla is akisi yasayabilir ve birinin bitirdigi faz
    otekinde hic kosulmamis olabilir.
    """
    adimlar = state.workflow_runs(workflow_id)
    # Bu is akisinin kosularinda her fazin EN SON durumu.
    durumlar: dict[str, str] = {}
    plan_kimlikleri: set[str] = set()
    for kosu in adimlar:
        if kosu.get("plan_id"):
            plan_kimlikleri.add(kosu["plan_id"])
        for satir in state.run_step_rows(kosu["id"]):
            durumlar[satir["phase"]] = satir["status"]

    # Gorevler: is akisinin planlari varsa onlar, yoksa etkin plan.
    if plan_kimlikleri:
        gorevler = [
            g for pid in plan_kimlikleri for g in state.list_tasks(plan_id=pid)
        ]
    else:
        gorevler = state.list_tasks(plan_id=state.active_plan_id())

    bekleyen_gorev = sum(1 for g in gorevler if g.status == Status.PENDING)
    bloke_gorev = sum(1 for g in gorevler if g.status == Status.BLOCKED)
    kosan_gorev = sum(1 for g in gorevler if g.status == Status.RUNNING)

    katalog = []
    for phase in Phase.ordered():
        durum = durumlar.get(str(phase), Status.PENDING)
        bitti = durum in {Status.DONE, Status.SKIPPED}
        if phase is Phase.IMPLEMENT:
            bekleyen, bloke, kosan = bekleyen_gorev, bloke_gorev, kosan_gorev
        else:
            bekleyen = 0 if bitti else 1
            bloke = 0
            kosan = 1 if durum == Status.RUNNING else 0
        katalog.append(
            {
                "phase": str(phase),
                "label": phase.label,
                "agent": phase.agent_label,
                "produces": phase.produces,
                "stage": phase.stage,
                "index": phase.index,
                "status": durum,
                "terminal": bitti,
                # Bu adimda BEKLEYEN is. `implement` icin gorev sayisi,
                # otekiler icin fazin kendisi (0 ya da 1).
                "waiting": bekleyen,
                "blocked": bloke,
                "running": kosan,
                # Sayinin NEYI saydigi; arayuz ipucunu buna gore yazar.
                "unit": "task" if phase is Phase.IMPLEMENT else "phase",
            }
        )
    return katalog


def phase_catalog(state: Any) -> list[dict[str, Any]]:
    """Arayuzun faz seridi icin faz listesi + guncel durumlari."""
    catalog = []
    for phase in Phase.ordered():
        snapshot = state.phase_status(phase)
        catalog.append(
            {
                "phase": str(phase),
                "label": phase.label,
                "agent": phase.agent_label,
                "produces": phase.produces,
                "stage": phase.stage,
                "index": phase.index,
                "status": snapshot.status,
                "summary": snapshot.summary,
                "cost": round(snapshot.cost_usd, 4),
                "needs_llm": phase is not Phase.INGEST,
                "terminal": snapshot.status in {Status.DONE, Status.SKIPPED},
            }
        )
    return catalog
