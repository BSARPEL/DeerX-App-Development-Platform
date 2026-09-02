"""Boru hattinin ureteci varliklar.

Ajanlar serbest metin yerine bu varliklari *arac cagrisiyla* kaydeder. Boylece
cikti ayristirilabilir, kalici ve fazlar arasi devredilebilir olur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..i18n import t


class Phase(StrEnum):
    """Boru hatti fazlari, calisma sirasiyla."""

    INGEST = "ingest"
    ANALYZE = "analyze"
    RESEARCH = "research"
    ASSESS = "assess"
    MOCKUP = "mockup"
    DESIGN = "design"
    PLAN = "plan"
    IMPLEMENT = "implement"
    QA = "qa"
    REVIEW = "review"
    PACKAGE = "package"
    STAGING = "staging"
    LIVE = "live"

    @classmethod
    def ordered(cls) -> list[Phase]:
        return [
            cls.INGEST,
            cls.ANALYZE,
            cls.RESEARCH,
            cls.ASSESS,
            cls.MOCKUP,
            cls.DESIGN,
            cls.PLAN,
            cls.IMPLEMENT,
            cls.QA,
            cls.REVIEW,
            cls.PACKAGE,
            cls.STAGING,
            cls.LIVE,
        ]

    @property
    def index(self) -> int:
        return Phase.ordered().index(self)

    @property
    def label(self) -> str:
        return t(f"phase.{self.value}")

    @property
    def agent_label(self) -> str:
        """Fazi yuruten ajanin okunakli adi."""
        return t(f"agent.{self.value}")

    @property
    def produces(self) -> str:
        """Fazin ne urettigi -- arayuzde adim secerken gorunur.

        Kullanici "assess" ya da "mockup" adini degil, elde edecegi seyi bilmek
        ister; adim listesi bu satir olmadan anlamsiz bir faz adlari dizisidir.
        """
        return t(f"produces.{self.value}")

    @property
    def stage(self) -> str:
        """Fazin ait oldugu ust asama; adim listesini gruplamak icin.

        Bu bir GORUNTU metni degil, arayuzun anahtar olarak kullandigi bir
        kimlik: `app.js` bunu `t("stage." + stage.name)` diye ariyor. Burada
        cevirmek arayuzde anahtarin kendisini gosterirdi; ceviri istemci
        tarafinda, `static/i18n.js` icinde yapiliyor.
        """
        if self.index <= Phase.ASSESS.index:
            return "Anlama"
        if self.index <= Phase.PLAN.index:
            return "Tasarım"
        if self.index <= Phase.REVIEW.index:
            return "Üretim"
        return "Teslim"


class Status(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    # Faz isini yapti ama devam etmek icin kullanicidan cevap bekliyor.
    NEEDS_INPUT = "needs_input"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


@dataclass(slots=True)
class Requirement:
    """Dokumandan cikarilan tek bir gereksinim."""

    key: str
    title: str
    description: str = ""
    category: str = "functional"  # functional | nonfunctional | constraint | assumption
    priority: str = Priority.SHOULD
    source_ref: str = ""  # dokumandaki dayanak (baslik/sayfa)
    status: str = Status.PENDING
    id: int | None = None

    def to_line(self) -> str:
        return f"[{self.key}] ({self.priority}/{self.category}) {self.title}"


@dataclass(slots=True)
class Gap:
    """Dokumanda veya mevcut kodda tespit edilen eksik/risk."""

    key: str
    title: str
    description: str = ""
    severity: str = Severity.MEDIUM
    area: str = "genel"  # ornek: guvenlik, veri modeli, UX, operasyon
    recommendation: str = ""
    evidence: str = ""
    status: str = Status.PENDING
    id: int | None = None

    def to_line(self) -> str:
        return f"[{self.key}] ({self.severity}/{self.area}) {self.title}"


@dataclass(slots=True)
class Decision:
    """Mimari karar kaydi (ADR ozeti)."""

    key: str
    title: str
    choice: str
    rationale: str = ""
    alternatives: str = ""
    tradeoffs: str = ""
    id: int | None = None

    def to_line(self) -> str:
        return f"[{self.key}] {self.title} -> {self.choice}"


@dataclass(slots=True)
class ResearchNote:
    """Web arastirmasindan elde edilen, kaynagi belli bir bulgu."""

    topic: str
    finding: str
    url: str = ""
    confidence: str = "medium"  # high | medium | low
    id: int | None = None

    def to_line(self) -> str:
        src = f" ({self.url})" if self.url else ""
        return f"- [{self.confidence}] {self.topic}: {self.finding}{src}"


@dataclass(slots=True)
class Question:
    """Ajanin kullaniciya yonelttigi acik soru.

    `Gap`ten farki: bosluk ekibin cozebilecegi bir eksiklik, soru ise yalnizca
    kullanicinin cevaplayabilecegi bir bilgi. `blocking=True` olan cevaplanmamis
    bir soru boru hattini durdurur — yanlis varsayimla ilerlemek, durup sormaktan
    daha pahaliya patlar.
    """

    key: str
    question: str
    why: str = ""  # cevaplanmazsa ne yanlis gider
    asked_by: str = "analyst"
    blocking: bool = True
    suggestion: str = ""  # ajanin onerdigi makul varsayilan
    status: str = "open"  # open | answered | skipped
    answer: str = ""
    id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    def to_line(self) -> str:
        mark = "!" if self.blocking else "?"
        if self.status == "answered":
            return f"[{self.key}] {self.question}\n    -> CEVAP: {self.answer}"
        if self.status == "skipped":
            varsayim = self.suggestion or "belirtilmedi"
            return f"[{self.key}] {self.question}\n    -> ATLANDI, varsayim: {varsayim}"
        return f"[{self.key}]{mark} {self.question}"


@dataclass(slots=True)
class Task:
    """Uygulama planindaki tek bir is birimi."""

    key: str
    title: str
    description: str = ""
    kind: str = "code"  # code | test | config | docs | infra | research
    # Gorevi hangi uzman ajanin ustlenecegi. Uygulama fazi buna gore yonlendirir.
    lane: str = "backend"  # backend | frontend | qa | infra | docs
    deps: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    acceptance: str = ""
    estimate: str = "M"  # S | M | L
    status: str = Status.PENDING
    result: str = ""
    order_index: int = 0
    # Gorevin ait oldugu plan. Ayni projede birden fazla plan yasayabilir
    # (paralel is akislari, alternatif yaklasimlar, yeni surum); bos ise
    # kaydedilirken etkin plana yazilir.
    plan_id: str = ""
    id: int | None = None

    def to_line(self) -> str:
        dep = f" <- {', '.join(self.deps)}" if self.deps else ""
        return f"[{self.key}] ({self.status}/{self.lane}) {self.title}{dep}"


@dataclass(slots=True)
class Artifact:
    """Uretilen dosya (rapor, mimari dokumani, mockup, plan)."""

    name: str
    kind: str  # report | architecture | mockup | plan | diagram | other
    path: str
    summary: str = ""
    # Ciktiyi ureten kosu ve faz. Ciktilar kosu bazli gruplanabilsin diye
    # tutulur; ayni ad tekrar yazilirsa cikti son ureten kosuya gecer.
    run_id: str = ""
    phase: str = ""
    id: int | None = None


@dataclass(slots=True)
class PhaseState:
    phase: str
    status: str = Status.PENDING
    summary: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    cost_usd: float = 0.0
    # Bu fazin HANGI hedef icin tamamlandigi. Tamamlanma tek basina bir sey
    # ifade etmiyor: hedef degistiginde eldeki analiz artik baska bir projeye
    # ait oluyor ve "zaten tamam" diye atlanmasi yanlis sonuc uretiyor.
    goal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "summary": self.summary,
            "cost_usd": self.cost_usd,
        }
