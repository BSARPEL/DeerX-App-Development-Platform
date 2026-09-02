"""KnowledgeBase — RAG katmaninin tek giris noktasi.

Yukleme, parcalama, gomme, saklama ve hibrit arama burada birlesir. Araclar ve
ajanlar yalnizca bu sinifi tanir.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import ConfigError, IngestError
from ..i18n import t
from ..logging import EventLog, get_logger
from .chunker import chunk_text
from .embedder import Embedder, build_embedder
from .loaders import LoadedDoc, iter_files, load_file, load_html_text, load_text
from .retriever import maximal_marginal_relevance, reciprocal_rank_fusion
from .store import ChunkRecord, VectorStore

log = get_logger("rag.kb")


@dataclass(slots=True)
class IngestResult:
    source: str
    title: str
    kind: str
    chunks: int
    skipped: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class KnowledgeBase:
    """Proje bilgi tabani.

    Gomme modeli ilk kullanimda tembel yuklenir: yalnizca arama/indeksleme
    yapilan komutlar ONNX modelini diske indirir.
    """

    def __init__(self, settings: Settings, events: EventLog | None = None) -> None:
        self.settings = settings
        self.events = events
        settings.ensure_dirs()
        self.store = VectorStore(settings.db_path, dim=settings.rag.embedding_dim)
        self._embedder: Embedder | None = None

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            embedder = build_embedder(self.settings.rag)
            stored = self.store.stored_dim()
            if stored is not None and stored != embedder.dim:
                # Sessizce bos sonuc dondurmek yerine acikca duruyoruz: farkli
                # boyuttaki vektorler karsilastirilamaz, arama sessizce bozulurdu.
                raise ConfigError(
                    t(
                        "setup.model_changed",
                        stored=stored,
                        model=embedder.name,
                        dim=embedder.dim,
                    )
                )
            self._embedder = embedder
            self.store.dim = embedder.dim
        return self._embedder

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------ #
    # Indeksleme
    # ------------------------------------------------------------------ #
    def _index(self, doc: LoadedDoc, *, force: bool) -> IngestResult:
        if not force and self.store.document_hash(doc.source) == doc.sha256:
            return IngestResult(doc.source, doc.title, doc.kind, 0, skipped=True)

        rag = self.settings.rag
        chunks = chunk_text(
            doc.text,
            kind=doc.kind,
            max_tokens=rag.chunk_tokens,
            overlap_tokens=rag.chunk_overlap_tokens,
        )
        if not chunks:
            return IngestResult(doc.source, doc.title, doc.kind, 0, error="parca uretilemedi")

        vectors = self.embedder.embed_documents([c.contextualized() for c in chunks])
        count = self.store.upsert_document(doc, chunks, vectors)
        if self.events is not None:
            self.events.emit("tool", "rag", f"indekslendi: {doc.title} ({count} parca)")
        return IngestResult(doc.source, doc.title, doc.kind, count)

    def ingest_file(self, path: Path, *, force: bool = False) -> IngestResult:
        """Tek dosya indeksler.

        Hicbir dosya hatasi disari sizmaz: bir dizin taranirken bozuk tek bir
        dosyanin butun fazi dusurmesi kabul edilemez. Hata sonuca yazilir,
        cagiran raporlar.
        """
        try:
            doc = load_file(path, max_bytes=self.settings.rag.max_file_bytes)
        except IngestError as exc:
            return IngestResult(str(path), path.name, "doc", 0, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - ucuncu parti ayristiricilar
            log.warning(t("setup.unreadable", name=path.name, error=exc))
            return IngestResult(
                str(path), path.name, "doc", 0, error=f"{type(exc).__name__}: {exc}"
            )
        return self._index(doc, force=force)

    def ingest_path(self, path: Path, *, force: bool = False) -> list[IngestResult]:
        """Dosya veya dizin indeksler. Dizinlerde include/exclude glob'lari uygulanir."""
        path = path.resolve()
        if path.is_file():
            return [self.ingest_file(path, force=force)]
        if not path.is_dir():
            return [IngestResult(str(path), path.name, "doc", 0, error="yol bulunamadi")]

        rag = self.settings.rag
        files = iter_files(path, rag.include_globs, rag.exclude_globs)
        results: list[IngestResult] = []
        for file_path in files:
            results.append(self.ingest_file(file_path, force=force))
        return results

    def ingest_text(
        self,
        text: str,
        *,
        source: str,
        title: str,
        kind: str = "doc",
        force: bool = True,
        **meta: Any,
    ) -> IngestResult:
        doc = load_text(text, source=source, title=title, kind=kind, **meta)
        return self._index(doc, force=force)

    def ingest_html(self, html: str, *, source: str, title: str | None = None) -> IngestResult:
        try:
            doc = load_html_text(html, source=source, title=title)
        except IngestError as exc:
            return IngestResult(source, title or source, "web", 0, error=str(exc))
        return self._index(doc, force=True)

    def forget(self, source: str) -> int:
        return self.store.delete_document(source)

    # ------------------------------------------------------------------ #
    # Arama
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        k: int | None = None,
        kinds: Iterable[str] | None = None,
        diversify: bool = True,
    ) -> list[ChunkRecord]:
        """Hibrit arama: anlamsal + sozcuksel siralamalari RRF ile birlestirir."""
        rag = self.settings.rag
        k = k or rag.top_k
        if not query.strip():
            return []

        kind_list = list(kinds) if kinds else None
        # Fuzyon ve MMR icin adaydan daha genis bir havuz cekilir.
        pool = max(k * 4, 24)

        query_vector = self.embedder.embed_query(query)
        semantic = self.store.search_semantic(query_vector, pool, kind_list)
        lexical = self.store.search_lexical(query, pool, kind_list)

        if not semantic and not lexical:
            return []

        fused = reciprocal_rank_fusion(
            [semantic, lexical],
            k=rag.rrf_k,
            # Anlamsal siralamaya biraz daha guvenilir; sozcuksel, ozel isim ve
            # kod tanimlayicilarinda kurtaricidir.
            weights=[1.0, 0.8],
        )
        candidate_ids = [cid for cid, _ in fused[:pool]]
        scores = dict(fused)

        if diversify and len(candidate_ids) > k:
            vectors = self.store.vectors_for(candidate_ids)
            if vectors.shape[0] == len(candidate_ids):
                # Alaka terimi fuzyon skorundan gelir; vektorler yalnizca
                # yakin-kopyalari elemek (fazlalik terimi) icin kullanilir.
                candidate_ids = maximal_marginal_relevance(
                    vectors,
                    candidate_ids,
                    [scores[cid] for cid in candidate_ids],
                    lambda_=rag.mmr_lambda,
                    top_n=k,
                )
        selected = candidate_ids[:k]

        records = self.store.fetch_chunks(selected)
        ordered: list[ChunkRecord] = []
        for cid in selected:
            record = records.get(cid)
            if record is None:
                continue
            record.score = scores.get(cid, 0.0)
            ordered.append(record)
        return ordered

    def context_block(
        self,
        query: str,
        *,
        k: int | None = None,
        kinds: Iterable[str] | None = None,
        max_chars: int = 12_000,
        header: str | None = None,
    ) -> str:
        """Arama sonuclarini prompt'a gomulebilir tek bir metne cevirir."""
        hits = self.search(query, k=k, kinds=kinds)
        if not hits:
            return ""
        parts: list[str] = []
        if header:
            parts.append(header)
        budget = max_chars
        for hit in hits:
            piece = hit.render(max_chars=min(4000, budget))
            if budget - len(piece) < 0:
                break
            parts.append(piece)
            budget -= len(piece)
        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------ #
    # Bilgi
    # ------------------------------------------------------------------ #
    def stats(self) -> dict[str, Any]:
        data = self.store.stats()
        data["embedding_model"] = self.settings.rag.embedding_model
        data["embedding_provider"] = self.settings.rag.embedding_provider
        return data

    def list_documents(self) -> list[dict[str, Any]]:
        return self.store.list_documents()
