"""Proje hafizasi araclari.

Ajanlar bulgularini serbest metinle degil bu araclarla kaydeder. Sonuc: fazlar
arasi devredilebilen, CLI/MCP uzerinden sorgulanabilen yapisal bir cikti.
Toplu kayit destegi bilerek eklendi — model on gereksinimi tek cagrida yazabilir.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from ..errors import ToolError
from ..i18n import t
from ..pipeline.models import (
    Artifact,
    Decision,
    Gap,
    Question,
    Requirement,
    ResearchNote,
    Task,
)
from .base import Tool, ToolContext, ToolResult, json_block

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d{1,4}$")


def _validate_key(key: str, prefix: str) -> str:
    key = key.strip().upper()
    if not _KEY_RE.match(key):
        raise ToolError(t("tool.bad_key", key=key, prefix=prefix))
    return key


def _required(item: dict[str, Any], field: str, kind: str, index: int) -> str:
    """Zorunlu bir alani okur; yoksa modele ne yapacagini soyler.

    Ham `KeyError: 'title'` hem yigin izi basiyor hem de modele KACINCI
    kayitta ne eksik oldugunu ve hangi alanlari gonderdigini soylemiyordu.
    Olculdu: tam bir boru hatti kosusunda mimar `record_gaps` cagrisinda
    `title` alanini atladi ve arac `KeyError` ile coktu; ajan ne oldugunu
    tahmin ederek bir tur harcadi.
    """
    if not isinstance(item, dict):
        # Model bazen listeye duz metin koyuyor (cogunlukla bozuk JSON'dan).
        # Onceki hali `item.get(...)` cagirip `AttributeError: 'str' object has
        # no attribute 'get'` uretiyordu -- yani alan korumasi eklenmis olmasina
        # ragmen model yine anlamsiz bir hata goruyordu.
        raise ToolError(
            t(
                "record.not_an_object",
                kind=kind, index=index + 1, type=type(item).__name__,
                value=repr(str(item)[:90]), field=field,
            )
        )
    value = item.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        gonderilen = ", ".join(sorted(item)) or "(hic alan yok)"
        raise ToolError(
            t(
                "record.missing_field",
                kind=kind, index=index + 1, field=field, sent=gonderilen,
            )
        )
    return str(value).strip()


class RecordRequirements(Tool):
    name = "record_requirements"
    description = """
    Dokumandan cikarilan gereksinimleri kaydeder (toplu). Her gereksinim
    dokumandaki bir dayanaga (`source_ref`) baglanmalidir; dayanagi olmayan
    cikarimlari `category="assumption"` olarak isaretleyin.

    Anahtar bicimi: REQ-001, REQ-002 …  Ayni anahtar tekrar yazilirsa guncellenir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "REQ-001 bicimi."},
                        "title": {"type": "string", "description": "Tek cumlelik ozet."},
                        "description": {"type": "string", "description": "Detay ve kabul olcutu."},
                        "category": {
                            "type": "string",
                            "enum": ["functional", "nonfunctional", "constraint", "assumption"],
                        },
                        "priority": {"type": "string", "enum": ["must", "should", "could", "wont"]},
                        "source_ref": {
                            "type": "string",
                            "description": "Dokumandaki dayanak (baslik, sayfa, alinti).",
                        },
                    },
                    "required": ["key", "title"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, ctx: ToolContext, items: list[dict[str, Any]]) -> ToolResult:
        state = ctx.require_state()
        saved: list[str] = []
        for i, item in enumerate(items):
            req = Requirement(
                key=_validate_key(_required(item, "key", "Gereksinim", i), "REQ"),
                title=_required(item, "title", "Gereksinim", i),
                description=item.get("description", "").strip(),
                category=item.get("category", "functional"),
                priority=item.get("priority", "should"),
                source_ref=item.get("source_ref", "").strip(),
            )
            state.add_requirement(req)
            saved.append(req.key)
        ctx.events.emit(
            "tool", "state",
            t("tool.recorded_count", count=len(saved), kind=t("kind.requirements")),
        )
        return ToolResult(
            content=t("tool.saved_keys", keys=", ".join(saved), count=len(saved))
        )


class RecordGaps(Tool):
    name = "record_gaps"
    description = """
    Bosluk, belirsizlik, risk ve iyilestirme firsatlarini kaydeder (toplu).
    `evidence` alanina dayanagi yazin: hangi dokuman/kod bunu gosteriyor?
    `recommendation` alanina somut oneriyi yazin.

    Anahtar bicimi: GAP-001.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "GAP-001 bicimi."},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low"],
                        },
                        "area": {
                            "type": "string",
                            "description": "or. guvenlik, veri modeli, UX, operasyon, performans",
                        },
                        "recommendation": {"type": "string", "description": "Somut cozum onerisi."},
                        "evidence": {"type": "string", "description": "Dayanak/alinti."},
                    },
                    "required": ["key", "title"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, ctx: ToolContext, items: list[dict[str, Any]]) -> ToolResult:
        state = ctx.require_state()
        saved: list[str] = []
        for i, item in enumerate(items):
            gap = Gap(
                key=_validate_key(_required(item, "key", "Bosluk", i), "GAP"),
                title=_required(item, "title", "Bosluk", i),
                description=item.get("description", "").strip(),
                severity=item.get("severity", "medium"),
                area=item.get("area", "genel"),
                recommendation=item.get("recommendation", "").strip(),
                evidence=item.get("evidence", "").strip(),
            )
            state.add_gap(gap)
            saved.append(gap.key)
        ctx.events.emit("tool", "state", f"{len(saved)} bosluk/risk kaydedildi")
        return ToolResult(content=f"Kaydedildi: {', '.join(saved)}.")


class RecordDecisions(Tool):
    name = "record_decisions"
    description = """
    Mimari kararlari kaydeder (toplu, ADR ozeti). Her karar icin degerlendirilen
    alternatifleri ve odunlesmeleri yazin — sonraki fazlar bunlari veri olarak kullanir.

    Anahtar bicimi: ADR-001.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "ADR-001 bicimi."},
                        "title": {"type": "string", "description": "Karar konusu."},
                        "choice": {"type": "string", "description": "Secilen secenek."},
                        "rationale": {"type": "string", "description": "Neden bu secildi."},
                        "alternatives": {"type": "string", "description": "Degerlendirilen digerleri."},
                        "tradeoffs": {"type": "string", "description": "Kabul edilen odunler."},
                    },
                    "required": ["key", "title", "choice"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, ctx: ToolContext, items: list[dict[str, Any]]) -> ToolResult:
        state = ctx.require_state()
        saved: list[str] = []
        for i, item in enumerate(items):
            decision = Decision(
                key=_validate_key(_required(item, "key", "Karar", i), "ADR"),
                title=_required(item, "title", "Karar", i),
                choice=_required(item, "choice", "Karar", i),
                rationale=item.get("rationale", "").strip(),
                alternatives=item.get("alternatives", "").strip(),
                tradeoffs=item.get("tradeoffs", "").strip(),
            )
            state.add_decision(decision)
            saved.append(decision.key)
        ctx.events.emit("tool", "state", f"{len(saved)} mimari karar kaydedildi")
        return ToolResult(content=f"Kaydedildi: {', '.join(saved)}.")


class RecordResearch(Tool):
    name = "record_research"
    description = """
    Web arastirmasindan cikan, kaynagi belli bulgulari kaydeder (toplu).
    Kaynak URL'si olmayan bir bulguyu `confidence="low"` isaretleyin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Arastirma konusu basligi."},
                        "finding": {"type": "string", "description": "Somut bulgu, tek paragraf."},
                        "url": {"type": "string", "description": "Kaynak baglantisi."},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["topic", "finding"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, ctx: ToolContext, items: list[dict[str, Any]]) -> ToolResult:
        state = ctx.require_state()
        for i, item in enumerate(items):
            state.add_research_note(
                ResearchNote(
                    topic=_required(item, "topic", "Arastirma", i),
                    finding=_required(item, "finding", "Arastirma", i),
                    url=item.get("url", "").strip(),
                    confidence=item.get("confidence", "medium"),
                )
            )
        ctx.events.emit("tool", "state", f"{len(items)} arastirma bulgusu kaydedildi")
        return ToolResult(content=f"{len(items)} bulgu kaydedildi.")


class RecordTasks(Tool):
    name = "record_tasks"
    description = """
    Gelistirme gorevlerini kaydeder (toplu). Her gorev:
      * tek oturumda bitirilebilecek buyuklukte olmali,
      * dokunacagi dosyalari (`files`) ongormeli,
      * makine tarafindan dogrulanabilir bir kabul olcutu (`acceptance`) icermeli
        (or. "pytest tests/test_auth.py gecer").
    `deps` alanina onkosul gorevlerin anahtarlarini yazin; sira buradan cikarilir.

    Her goreve bir `lane` atayin; uygulama fazi gorevi o serite ait uzman ajana
    yonlendirir. Bir isi seritlere bolmeyi tercih edin: API ucu backend, formu
    frontend, testi qa seridine ait ayri gorevler olsun.

    Anahtar bicimi: T-001.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "T-001 bicimi."},
                        "title": {"type": "string"},
                        "description": {"type": "string", "description": "Ne yapilacak, nasil."},
                        "kind": {
                            "type": "string",
                            "enum": ["code", "test", "config", "docs", "infra", "research"],
                        },
                        "lane": {
                            "type": "string",
                            "enum": ["backend", "frontend", "qa", "infra", "docs"],
                            "description": (
                                "Gorevi hangi uzman ajan ustlenecek. backend: veri/API/is "
                                "mantigi · frontend: arayuz · qa: test · infra: yapilandirma "
                                "ve derleme · docs: dokumantasyon."
                            ),
                        },
                        "deps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Onkosul gorev anahtarlari.",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Olusturulacak/degistirilecek dosyalar.",
                        },
                        "acceptance": {
                            "type": "string",
                            "description": "Dogrulanabilir kabul olcutu.",
                        },
                        "estimate": {"type": "string", "enum": ["S", "M", "L"]},
                        "order_index": {"type": "integer", "description": "Onerilen sira."},
                    },
                    "required": ["key", "title"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, ctx: ToolContext, items: list[dict[str, Any]]) -> ToolResult:
        state = ctx.require_state()
        saved: list[str] = []
        for index, item in enumerate(items):
            task = Task(
                key=_validate_key(_required(item, "key", "Gorev", index), "T"),
                title=_required(item, "title", "Gorev", index),
                description=item.get("description", "").strip(),
                kind=item.get("kind", "code"),
                lane=item.get("lane", "backend"),
                deps=[d.strip().upper() for d in item.get("deps", []) if d.strip()],
                files=[f.strip() for f in item.get("files", []) if f.strip()],
                acceptance=item.get("acceptance", "").strip(),
                estimate=item.get("estimate", "M"),
                order_index=int(item.get("order_index", index)),
            )
            state.add_task(task)
            saved.append(task.key)

        # Bilinmeyen bagimliliklari modele hemen bildir; plan tutarsizsa duzeltsin.
        known = {t.key for t in state.list_tasks()}
        dangling = sorted(
            {dep for t in state.list_tasks() for dep in t.deps if dep not in known}
        )
        warning = (
            t("tool.dangling_deps", keys=", ".join(dangling)) if dangling else ""
        )
        ctx.events.emit(
            "tool", "state",
            t("tool.recorded_count", count=len(saved), kind=t("kind.tasks")),
        )
        return ToolResult(content=t("tool.saved_tasks", keys=", ".join(saved)) + warning)


class UpdateTask(Tool):
    name = "update_task"
    description = """
    Bir gorevin durumunu ve sonucunu gunceller. Uygulama fazinda her gorev icin
    bitiminde cagirin. `result` alanina ne yapildigini ve dogrulamanin nasil
    gectigini yazin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Gorev anahtari, or. T-003."},
            "status": {
                "type": "string",
                "enum": ["pending", "running", "done", "blocked", "failed", "skipped"],
            },
            "result": {"type": "string", "description": "Sonuc ozeti / dogrulama kaniti."},
        },
        "required": ["key", "status"],
    }

    def run(self, ctx: ToolContext, key: str, status: str, result: str = "") -> ToolResult:
        state = ctx.require_state()
        key = _validate_key(key, "T")
        if state.get_task(key) is None:
            raise ToolError(t("tool.no_such_task", key=key))
        state.update_task(key, status=status, result=result)
        ctx.events.emit("tool", "state", f"{key} -> {status}")
        return ToolResult(content=t("tool.task_updated", key=key, status=status))


class SaveArtifact(Tool):
    name = "save_artifact"
    description = """
    Uretilen bir ciktiyi (analiz raporu, mimari dokumani, mockup, plan) diske
    yazar ve proje hafizasina kaydeder. Ciktilar `.deerx/artifacts/` altinda toplanir.

    Mockup'lar icin tek dosyalik, harici bagimliligi olmayan HTML yazin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Dosya adi, or. `analiz-raporu.md` veya `mockup-dashboard.html`.",
            },
            "kind": {
                "type": "string",
                "enum": ["report", "architecture", "mockup", "plan", "diagram", "other"],
            },
            "content": {"type": "string", "description": "Dosyanin tam icerigi."},
            "summary": {"type": "string", "description": "Bir cumlelik ozet."},
        },
        "required": ["name", "content"],
    }

    def run(
        self,
        ctx: ToolContext,
        name: str,
        content: str,
        kind: str = "report",
        summary: str = "",
    ) -> ToolResult:
        state = ctx.require_state()
        safe_name = name.strip().replace("\\", "/").split("/")[-1]
        if not safe_name or safe_name in {".", ".."}:
            raise ToolError(t("tool.bad_artifact_name", name=name))

        ctx.settings.ensure_dirs()
        path = ctx.settings.artifacts_dir / safe_name
        path.write_text(content, encoding="utf-8")
        # Cikti uretildigi kosuya baglanir; Ciktilar gorunumu kosu bazli
        # gruplayabilsin diye. Kosu disinda uretilirse alanlar bos kalir.
        state.add_artifact(
            Artifact(name=safe_name, kind=kind, path=str(path), summary=summary.strip()),
            run_id=ctx.events.current_run or "",
            phase=ctx.events.current_phase or "",
        )

        # Ciktiyi bilgi tabanina da ekle: sonraki fazlar bunu arayabilsin.
        if ctx.kb is not None and safe_name.endswith((".md", ".txt")):
            ctx.kb.ingest_text(
                content, source=f"artifact://{safe_name}", title=safe_name, kind="doc"
            )

        ctx.events.emit(
            "done", "artifact",
            t("tool.artifact_written", name=safe_name, size=f"{len(content):,}"),
            name=safe_name, artifact_kind=kind,
        )
        return ToolResult(
            content=f"Kaydedildi: {path}",
            data={"path": str(path)},
        )


class ReadProjectState(Tool):
    name = "read_project_state"
    description = """
    Proje hafizasini okur: gereksinimler, bosluklar, kararlar, arastirma
    bulgulari, gorevler ve ciktilar. Bir fazi bir onceki fazin sonucuna
    dayandirmak icin kullanin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": [
                    "all", "requirements", "questions", "gaps", "decisions",
                    "research", "tasks", "artifacts", "counts",
                ],
                "description": "Varsayilan: all (ozet).",
            }
        },
    }

    def run(self, ctx: ToolContext, section: str = "all") -> ToolResult:
        state = ctx.require_state()
        if section == "all":
            return ToolResult(content=state.snapshot())
        if section == "counts":
            return ToolResult(content=json_block(state.counts()))

        data = {
            "requirements": lambda: [asdict(r) for r in state.list_requirements()],
            "questions": lambda: [asdict(q) for q in state.list_questions()],
            "gaps": lambda: [asdict(g) for g in state.list_gaps()],
            "decisions": lambda: [asdict(d) for d in state.list_decisions()],
            "research": lambda: [asdict(n) for n in state.list_research_notes()],
            "tasks": lambda: [asdict(t) for t in state.list_tasks()],
            "artifacts": lambda: [asdict(a) for a in state.list_artifacts()],
        }[section]()
        if not data:
            return ToolResult(content=f"'{section}' bolumu bos.")
        return ToolResult(content=json_block(data))


class RecordQuestions(Tool):
    name = "record_questions"
    description = """
    YALNIZCA kullanicinin cevaplayabilecegi acik sorulari kaydeder.

    `record_gaps` ile farki onemli:
      * `record_gaps` — ekibin kendi cozebilecegi eksiklik veya risk.
      * `record_questions` — dokumanda olmayan, arastirmayla da bulunamayacak,
        yalnizca kullanicinin bildigi bilgi. Ornek: "ERP'nin API dokumanini
        alabilir miyiz?", "Hangi musteri segmenti oncelikli?", "Butce siniri nedir?"

    `blocking=true` isaretlenen cevaplanmamis bir soru BORU HATTINI DURDURUR ve
    kullanicidan cevap istenir. Bunu yalnizca cevapsiz ilerlemek isin buyuk bir
    kismini bosa cikaracaksa kullanin. Cevapsiz da makul bir varsayimla
    ilerlenebiliyorsa `blocking=false` yapin ve `suggestion` alanina varsayiminizi yazin.

    Once `read_project_state(section="questions")` ile bakin: ayni soru zaten
    sorulmus ve cevaplanmis olabilir.

    Anahtar bicimi: Q-001.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Q-001 bicimi."},
                        "question": {
                            "type": "string",
                            "description": "Kullaniciya sorulacak soru, tek cumle, net.",
                        },
                        "why": {
                            "type": "string",
                            "description": "Cevaplanmazsa ne yanlis gider veya ne yapilamaz.",
                        },
                        "blocking": {
                            "type": "boolean",
                            "description": (
                                "true ise boru hatti durur ve cevap beklenir. "
                                "Yalnizca gercekten ilerlenemiyorsa true yapin."
                            ),
                        },
                        "suggestion": {
                            "type": "string",
                            "description": (
                                "Cevap gelmezse kullanilacak makul varsayim. "
                                "blocking=false icin zorunlu sayilir."
                            ),
                        },
                    },
                    "required": ["key", "question"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, ctx: ToolContext, items: list[dict[str, Any]]) -> ToolResult:
        state = ctx.require_state()
        saved: list[str] = []
        already_answered: list[str] = []

        for i, item in enumerate(items):
            key = _validate_key(_required(item, "key", "Soru", i), "Q")
            existing = state.get_question(key)
            if existing is not None and existing.status != "open":
                already_answered.append(f"{key} ({existing.status})")
                continue
            question = Question(
                key=key,
                question=_required(item, "question", "Soru", i),
                why=item.get("why", "").strip(),
                asked_by=item.get("asked_by", "").strip() or "agent",
                blocking=bool(item.get("blocking", True)),
                suggestion=item.get("suggestion", "").strip(),
            )
            state.add_question(question)
            saved.append(key)

        blocking = len(state.open_blocking_questions())
        lines = []
        if saved:
            lines.append(f"Kaydedildi: {', '.join(saved)}.")
        if already_answered:
            lines.append(
                "Zaten cevaplanmis, tekrar acilmadi: " + ", ".join(already_answered) + ". "
                "Cevaplari `read_project_state(section=\"questions\")` ile okuyun."
            )
        if blocking:
            lines.append(
                f"{blocking} bloke eden acik soru var; faz bitince boru hatti durup "
                "kullanicidan cevap isteyecek."
            )
        ctx.events.emit("tool", "state", f"{len(saved)} soru kaydedildi")
        return ToolResult(content=" ".join(lines) or "Yeni soru yok.")


PROJECT_TOOLS: list[Tool] = [
    RecordRequirements(),
    RecordQuestions(),
    RecordGaps(),
    RecordDecisions(),
    RecordResearch(),
    RecordTasks(),
    UpdateTask(),
    SaveArtifact(),
    ReadProjectState(),
]
