"""DeerX MCP sunucusu (stdio).

Claude Code, Cline, Cursor gibi istemcilere DeerX'in bilgi tabanini, proje
hafizasini ve boru hattini arac olarak acar. Boylece orkestrasyonu isterse
disaridaki ajan yurutur.

Calisma alani `DEERX_WORKSPACE` ortam degiskeninden, yoksa gecerli dizinden alinir.

Kurulum (Claude Code / Cline `mcpServers` bloguna):

    {
      "mcpServers": {
        "deerx": {
          "command": "uv",
          "args": ["run", "--directory", "C:/path/to/deerx", "deerx-mcp"],
          "env": {
            "DEERX_WORKSPACE": "C:/path/to/your/project",
            "ANTHROPIC_API_KEY": "sk-ant-...",
            "DEERX_APPROVAL_MODE": "auto"
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    # MCP SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from ..config import load_settings
from ..i18n import t
from ..logging import EventLog, setup_logging
from ..pipeline import Orchestrator, Phase, Status

mcp = _Server(
    "deerx",
    instructions=(
        "DeerX: dokuman-gudumlu proje gelistirme ajani. Bir sartnameyi indeksler, "
        "analiz eder, bosluklarini bulur, mimari ve plan uretir, sonra uygular.\n\n"
        "Tipik akis: deerx_ingest -> deerx_search / deerx_status -> "
        "deerx_run_phase('analyze') -> ... -> deerx_tasks.\n"
        "Bir sey iddia etmeden once daima deerx_search ile bilgi tabanindan dogrulayin."
    ),
)

_orchestrator: Orchestrator | None = None


def _get() -> Orchestrator:
    """Paylasimli orkestratoru tembel kurar (surec omru boyunca tek ornek)."""
    global _orchestrator
    if _orchestrator is None:
        workspace = Path(os.environ.get("DEERX_WORKSPACE", Path.cwd())).resolve()
        settings = load_settings(workspace)
        setup_logging(settings.log_level)
        # stdio uzerinden konusuluyor: konsola bir sey yazmak protokolu bozar.
        events = EventLog(settings.events_path, echo=False)
        _orchestrator = Orchestrator(settings, events=events, stream=False)
    return _orchestrator


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------- #
# Bilgi tabani
# ---------------------------------------------------------------------- #
@mcp.tool()
def deerx_ingest(path: str, force: bool = False) -> str:
    """Bir dosyayi veya dizini bilgi tabanina indeksler.

    Args:
        path: Dosya veya dizin yolu (calisma alanina gore ya da mutlak).
        force: Icerigi degismemis dosyalari da yeniden indeksle.
    """
    orch = _get()
    target = Path(path)
    if not target.is_absolute():
        target = orch.settings.workspace / target

    results = orch.kb.ingest_path(target, force=force)
    indexed = [r for r in results if r.ok and not r.skipped]
    failed = [r for r in results if not r.ok]
    return _json(
        {
            "indexed_files": len(indexed),
            "indexed_chunks": sum(r.chunks for r in indexed),
            "skipped": sum(1 for r in results if r.skipped),
            "failed": [{"title": r.title, "error": r.error} for r in failed[:20]],
            "stats": orch.kb.stats(),
        }
    )


@mcp.tool()
def deerx_search(query: str, k: int = 8, kinds: list[str] | None = None) -> str:
    """Bilgi tabaninda hibrit arama yapar (anlamsal + anahtar sozcuk).

    Args:
        query: Dogal dilde sorgu.
        k: Dondurulecek parca sayisi.
        kinds: Kaynak turu filtresi: doc, code, web, data.
    """
    orch = _get()
    hits = orch.kb.search(query, k=max(1, min(k, 25)), kinds=kinds)
    if not hits:
        return f"'{query}' icin sonuc yok. Bilgi tabani: {_json(orch.kb.stats())}"
    return "\n\n---\n\n".join(
        f"### {h.citation()} (skor {h.score:.4f})\n{h.text}" for h in hits
    )


@mcp.tool()
def deerx_documents() -> str:
    """Bilgi tabanindaki dokumanlari ve istatistikleri listeler."""
    orch = _get()
    return _json({"stats": orch.kb.stats(), "documents": orch.kb.list_documents()})


# ---------------------------------------------------------------------- #
# Proje durumu
# ---------------------------------------------------------------------- #
@mcp.tool()
def deerx_status() -> str:
    """Projenin genel durumunu doner: fazlar, sayimlar, bilgi tabani."""
    orch = _get()
    return _json(
        {
            "workspace": str(orch.settings.workspace),
            "goal": orch.state.get_meta("goal", ""),
            "phases": [p.to_dict() for p in orch.state.all_phases()],
            "counts": orch.state.counts(),
            "blocking_questions": [
                {"key": q.key, "question": q.question}
                for q in orch.state.open_blocking_questions()
            ],
            "knowledge_base": orch.kb.stats(),
        }
    )


@mcp.tool()
def deerx_state(section: str = "all") -> str:
    """Proje hafizasini okur.

    Args:
        section: all | requirements | questions | gaps | decisions | research | tasks | artifacts
    """
    orch = _get()
    if section == "all":
        return orch.state.snapshot()
    data = orch.state.to_dict()
    key = {"research": "research_notes"}.get(section, section)
    if key not in data:
        return f"Bilinmeyen bolum: {section}. Secenekler: {', '.join(data)}"
    return _json(data[key])


@mcp.tool()
def deerx_tasks(status: str | None = None) -> str:
    """Gelistirme gorevlerini listeler.

    Args:
        status: Filtre: pending | running | done | blocked | failed | skipped
    """
    orch = _get()
    tasks = orch.state.list_tasks(status)
    ready = {t.key for t in orch.state.ready_tasks()}
    return _json(
        [
            {
                "key": t.key,
                "title": t.title,
                "status": t.status,
                "kind": t.kind,
                "deps": t.deps,
                "files": t.files,
                "acceptance": t.acceptance,
                "ready": t.key in ready,
                "description": t.description,
            }
            for t in tasks
        ]
    )


@mcp.tool()
def deerx_update_task(key: str, status: str, result: str = "") -> str:
    """Bir gorevin durumunu gunceller (disaridaki ajan gorevi uyguladiysa).

    Args:
        key: Gorev anahtari, or. T-003.
        status: pending | running | done | blocked | failed | skipped
        result: Ne yapildi, dogrulama nasil gecti.
    """
    orch = _get()
    key = key.strip().upper()
    if orch.state.get_task(key) is None:
        return f"HATA: '{key}' diye bir gorev yok."
    orch.state.update_task(key, status=status, result=result)
    return f"{key} -> {status}"


@mcp.tool()
def deerx_questions(only_open: bool = True) -> str:
    """Ajanlarin kullaniciya sordugu sorulari listeler.

    Bloke eden cevaplanmamis bir soru varsa boru hatti durur; devam etmeden
    once bunlarin cevaplanmasi (veya atlanmasi) gerekir.

    Args:
        only_open: True ise yalnizca cevap bekleyenler.
    """
    orch = _get()
    items = orch.state.list_questions("open" if only_open else None)
    return _json(
        {
            "items": [
                {
                    "key": q.key,
                    "question": q.question,
                    "why": q.why,
                    "blocking": q.blocking,
                    "status": q.status,
                    "suggestion": q.suggestion,
                    "answer": q.answer,
                }
                for q in items
            ],
            "blocking_open": [q.key for q in orch.state.open_blocking_questions()],
        }
    )


@mcp.tool()
def deerx_answer(key: str, answer: str) -> str:
    """Bir soruyu cevaplar. Cevap bilgi tabanina da yazilir.

    Args:
        key: Soru anahtari, or. Q-001.
        answer: Kullanicinin cevabi.
    """
    orch = _get()
    if not answer.strip():
        return "HATA: bos cevap. Varsayimla gecmek icin deerx_skip_question kullanin."
    question = orch.answer_question(key, answer.strip())
    if question is None:
        return f"HATA: '{key.upper()}' diye bir soru yok."
    remaining = [q.key for q in orch.state.open_blocking_questions()]
    return _json({"answered": question.key, "remaining_blocking": remaining})


@mcp.tool()
def deerx_skip_question(key: str, assumption: str = "") -> str:
    """Soruyu atlar; ajanlar belirtilen varsayimla ilerler.

    Args:
        key: Soru anahtari, or. Q-001.
        assumption: Ilerlenecek varsayim. Bos birakilirsa ajanin onerisi kullanilir.
    """
    orch = _get()
    question = orch.skip_question(key, assumption)
    if question is None:
        return f"HATA: '{key.upper()}' diye bir soru yok."
    remaining = [q.key for q in orch.state.open_blocking_questions()]
    return _json(
        {"skipped": question.key, "assumption": question.suggestion, "remaining_blocking": remaining}
    )


@mcp.tool()
def deerx_package(force: bool = False) -> str:
    """Uretilen projeyi teslim edilebilir bir zip olarak paketler.

    Once hazirlik denetimi yapilir: tamamlanmamis/basarisiz gorev veya
    cevaplanmamis bloke edici soru varsa paketleme durur ve engeller donulur.
    Sirlar (.env, anahtar dosyalari) pakete ASLA dahil edilmez.

    Args:
        force: Hazirlik denetimi engel bulsa da paketle.
    """
    orch = _get()
    from ..pipeline.packaging import PackagingError, PackagingNotReady, build_package

    try:
        result = build_package(
            orch.state,
            orch.settings.workspace,
            orch.settings.deliveries_dir,
            goal=orch.state.get_meta("goal", ""),
            force=force,
        )
    except PackagingNotReady as exc:
        return _json(
            {
                "ready": False,
                "blockers": [i.message for i in exc.readiness.blockers],
                "warnings": [i.message for i in exc.readiness.warnings],
                "message": (
                    "Proje teslim edilecek durumda degil. Eksikleri kapatin veya "
                    "force=true ile yine de paketleyin."
                ),
            }
        )
    except PackagingError as exc:
        return f"HATA: {exc}"

    # Artifakt kaydini `build_package` yapar; CLI, web ve MCP ayni yoldan gecer.
    return _json({"ready": True, **result.to_dict()})


@mcp.tool()
def deerx_artifact(name: str = "") -> str:
    """Uretilen bir ciktiyi okur; ad verilmezse ciktilari listeler.

    Args:
        name: Cikti dosya adi, or. `mimari.md`.
    """
    orch = _get()
    artifacts = orch.state.list_artifacts()
    if not name:
        return _json(
            [{"name": a.name, "kind": a.kind, "summary": a.summary} for a in artifacts]
        )
    match = next((a for a in artifacts if a.name == name), None)
    if match is None:
        available = ", ".join(a.name for a in artifacts) or "(yok)"
        return f"HATA: '{name}' bulunamadi. Mevcut: {available}"
    path = Path(match.path)
    if not path.is_file():
        return f"HATA: dosya diskte yok: {path}"

    if path.suffix.lower() == ".zip":
        # Zip metin degildir; ham baytlari donmek UnicodeDecodeError verirdi.
        # Yerine paketin kapak raporu ve icerik ozeti donulur.
        from ..pipeline.packaging import list_entries, read_manifest

        entries = list_entries(path)
        report = read_manifest(path)
        header = "\n".join(
            [
                f"# {path.name}",
                "",
                f"Teslimat paketi · {len(entries)} dosya · "
                f"{path.stat().st_size / 1e6:.2f} MB",
                f"Yol: {path}",
                "",
                "",
            ]
        )
        return header + (report or "_(pakette teslimat raporu yok)_")

    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return (
            f"HATA: '{name}' metin olarak okunamadi ({exc}). "
            f"Ikili bir cikti olabilir; yol: {path}"
        )


# ---------------------------------------------------------------------- #
# Boru hatti
# ---------------------------------------------------------------------- #
@mcp.tool()
def deerx_run_phase(phase: str, force: bool = False) -> str:
    """Bir boru hatti fazini calistirir. UZUN SURER ve API kredisi harcar.

    Fazlar: ingest, analyze, research, assess, design, plan, implement, review.
    `implement` fazinin dosya yazabilmesi icin DEERX_APPROVAL_MODE=auto olmalidir;
    aksi halde yazma islemleri reddedilir.

    Args:
        phase: Calistirilacak faz adi.
        force: Faz zaten tamamlanmis olsa da tekrar calistir.
    """
    orch = _get()
    try:
        target = Phase(phase.lower())
    except ValueError:
        return t(
            "mcp.unknown_phase", phase=phase, allowed=", ".join(p for p in Phase)
        )

    report = orch.run([target], force=force)
    result = report.phases[0] if report.phases else None
    if result is None:
        return "HATA: faz calistirilamadi."
    if report.needs_input:
        gate = report.phases[-1]
        return _json(
            {
                "status": "needs_input",
                "message": (
                    "Boru hatti durdu: kullanicinin cevaplamasi gereken sorular var. "
                    "deerx_questions ile okuyun, deerx_answer ile cevaplayin."
                ),
                "questions": gate.details.get("questions", []),
                "detail": gate.summary,
            }
        )
    return _json(
        {
            "phase": str(result.phase) if result.phase is not None else "gate",
            "status": result.status,
            "cost_usd": round(result.cost, 4),
            "error": result.error,
            "summary": result.summary,
            "details": result.details,
            "counts": orch.state.counts(),
        }
    )


@mcp.tool()
def deerx_next_task() -> str:
    """Bagimliliklari tamamlanmis bir sonraki gorevi doner (durumu degistirmez)."""
    orch = _get()
    ready = orch.state.ready_tasks()
    if not ready:
        blocked = orch.state.blocked_tasks()
        if blocked:
            return _json(
                {
                    "ready": None,
                    "blocked": [{"key": t.key, "waiting_on": t.deps} for t in blocked],
                }
            )
        pending = orch.state.list_tasks(Status.PENDING)
        return _json({"ready": None, "message": "Yapilacak gorev yok.", "pending": len(pending)})

    task = ready[0]
    return _json(
        {
            "key": task.key,
            "title": task.title,
            "description": task.description,
            "kind": task.kind,
            "files": task.files,
            "acceptance": task.acceptance,
            "deps": task.deps,
            "remaining_ready": len(ready) - 1,
        }
    )


# ---------------------------------------------------------------------- #
# Kaynaklar
# ---------------------------------------------------------------------- #
@mcp.tool()
def deerx_workflow_chat(workflow: str, message: str = "") -> str:
    """Bir is akisi hakkinda konusur; istenirse durumunu degistirir.

    `workflow` sirali numara ("2") ya da kimlik olabilir. `message` bos
    birakilirsa yalnizca konusma gecmisi doner -- once neyin konusuldugunu
    okumak, ustune yazmaktan once gelir.
    """
    orch = _get()
    aday = workflow.strip().lstrip("#")
    kayit = (
        orch.state.get_workflow_by_seq(int(aday))
        if aday.isdigit()
        else orch.state.get_workflow(aday)
    )
    if kayit is None:
        return t("chat.no_workflow", id=workflow)

    if not message.strip():
        return _json(orch.state.chat_history(kayit["id"]))

    cevap = orch.chat(kayit["id"], message)
    return _json(
        {
            "reply": cevap.text,
            "changes": cevap.changes,
            "iterations": cevap.iterations,
            "error": cevap.error,
        }
    )


@mcp.resource("deerx://state")
def state_resource() -> str:
    """Proje hafizasinin markdown ozeti."""
    return _get().state.snapshot()


@mcp.resource("deerx://artifacts/{name}")
def artifact_resource(name: str) -> str:
    """Uretilen bir ciktinin icerigi."""
    return deerx_artifact(name)


def main() -> None:
    """stdio uzerinden MCP sunucusunu calistirir."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
