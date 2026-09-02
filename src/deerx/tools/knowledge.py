"""Bilgi tabani (RAG) araclari."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..i18n import t
from .base import Tool, ToolContext, ToolResult, json_block

_KINDS = ["doc", "code", "web", "data"]


class SearchKnowledge(Tool):
    name = "search_knowledge"
    description = """
    Indekslenmis dokumanlarda ve kod tabaninda hibrit arama yapar (anlamsal +
    anahtar sozcuk). Bir seyi VARSAYMADAN ONCE bunu kullanin: gereksinimleri,
    mevcut davranisi ve terminolojiyi buradan dogrulayin.

    Ipucu: tek genis sorgu yerine birkac dar sorgu daha iyi sonuc verir.
    `kinds` ile alani daraltin: doc (sartname), code (mevcut kod), web (arastirma).
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Dogal dilde arama sorgusu."},
            "k": {"type": "integer", "description": "Dondurulecek parca sayisi (varsayilan 8)."},
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": _KINDS},
                "description": "Kaynak turu filtresi.",
            },
        },
        "required": ["query"],
    }

    def run(
        self,
        ctx: ToolContext,
        query: str,
        k: int = 8,
        kinds: list[str] | None = None,
    ) -> ToolResult:
        kb = ctx.require_kb()
        hits = kb.search(query, k=max(1, min(k, 25)), kinds=kinds)
        if not hits:
            stats = kb.stats()
            if stats["chunks"] == 0:
                return ToolResult(content=t("kb.empty"))
            return ToolResult(
                content=t("kb.no_hits", query=query, chunks=stats["chunks"])
            )

        ctx.events.emit(
            "tool", "rag", t("kb.searched", query=query[:70], count=len(hits))
        )
        blocks = [f"### {i}. {h.citation()}  (skor {h.score:.4f})\n{h.text}"
                  for i, h in enumerate(hits, start=1)]
        return ToolResult(content="\n\n---\n\n".join(blocks), data={"count": len(hits)})


class IngestSource(Tool):
    name = "ingest_source"
    description = """
    Bir dosyayi veya dizini bilgi tabanina indeksler. Dizin verilirse
    yapilandirmadaki include/exclude desenleri uygulanir. Icerigi degismemis
    dosyalar atlanir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Dosya veya dizin yolu."},
            "force": {"type": "boolean", "description": "Degismemis olsa da yeniden indeksle."},
        },
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, path: str, force: bool = False) -> ToolResult:
        kb = ctx.require_kb()
        target = ctx.resolve_path(path, must_exist=True)
        results = kb.ingest_path(Path(target), force=force)

        indexed = [r for r in results if r.ok and not r.skipped]
        skipped = [r for r in results if r.skipped]
        failed = [r for r in results if not r.ok]

        lines = [
            f"Indekslenen: {len(indexed)} dosya / {sum(r.chunks for r in indexed)} parca",
            f"Atlanan (degismemis): {len(skipped)}",
        ]
        if failed:
            lines.append(f"Basarisiz: {len(failed)}")
            lines.extend(f"  - {r.title}: {r.error}" for r in failed[:10])
        if indexed:
            lines.append("Dosyalar:")
            lines.extend(f"  - {r.title} ({r.chunks} parca, {r.kind})" for r in indexed[:40])
        return ToolResult(content="\n".join(lines), data={"indexed": len(indexed)})


class ListKnowledge(Tool):
    name = "list_knowledge"
    description = "Bilgi tabanindaki dokumanlari ve istatistikleri listeler."
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext) -> ToolResult:
        kb = ctx.require_kb()
        docs = kb.list_documents()
        stats = kb.stats()
        if not docs:
            return ToolResult(content="Bilgi tabani bos.")
        listing = "\n".join(
            f"- {d['title']} ({d['kind']}, {d['n_chunks']} parca) — {d['source']}"
            for d in docs[:200]
        )
        return ToolResult(content=f"{json_block(stats)}\n\n{listing}")


class ReadDocument(Tool):
    name = "read_document"
    description = """
    Indekslenmis bir dokumanin tamamini (veya bir aralik parcasini) sirayla okur.
    Arama sonuclari yeterli baglam vermediginde, sartnamenin bir bolumunu bastan
    sona okumak icin kullanin.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "`list_knowledge` ciktisindaki tam source degeri veya baslik.",
            },
            "start_chunk": {"type": "integer", "description": "Baslangic parca sirasi (0 tabanli)."},
            "count": {"type": "integer", "description": "Okunacak parca sayisi (varsayilan 6)."},
        },
        "required": ["source"],
    }

    def run(
        self,
        ctx: ToolContext,
        source: str,
        start_chunk: int = 0,
        count: int = 6,
    ) -> ToolResult:
        kb = ctx.require_kb()
        docs = kb.list_documents()
        match = next(
            (d for d in docs if d["source"] == source),
            next((d for d in docs if source.lower() in d["title"].lower()), None),
        )
        if match is None:
            available = ", ".join(d["title"] for d in docs[:20]) or "(bos)"
            raise ToolError(t("tool.source_not_found", source=source, available=available))

        conn = kb.store._conn  # noqa: SLF001 - depo ayni paketin parcasi
        rows = conn.execute(
            "SELECT c.ordinal, c.heading_path, c.text FROM chunks c "
            "JOIN documents d ON d.id = c.doc_id "
            "WHERE d.source = ? AND c.ordinal >= ? ORDER BY c.ordinal LIMIT ?",
            (match["source"], max(0, start_chunk), max(1, min(count, 20))),
        ).fetchall()
        if not rows:
            return ToolResult(
                content=t("kb.no_more_chunks", title=match["title"], start=start_chunk)
            )

        body = "\n\n".join(
            f"[parca {r['ordinal']}{' · ' + r['heading_path'] if r['heading_path'] else ''}]\n"
            f"{r['text']}"
            for r in rows
        )
        last = rows[-1]["ordinal"]
        more = (
            f"\n\n[devam icin start_chunk={last + 1}]"
            if last + 1 < match["n_chunks"]
            else "\n\n[dokuman sonu]"
        )
        return ToolResult(content=f"# {match['title']}\n\n{body}{more}")


KNOWLEDGE_TOOLS: list[Tool] = [
    SearchKnowledge(),
    ReadDocument(),
    IngestSource(),
    ListKnowledge(),
]
