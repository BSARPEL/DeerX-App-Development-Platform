"""Bilgi tabani deposu: SQLite + FTS5 (sozcuksel) + numpy (anlamsal).

Tek dosyalik, sunucu gerektirmeyen bir depo. Proje olcegindeki (on binlerce
parcaya kadar) korpuslarda kaba kuvvet kosinus aramasi milisaniyeler surer, bu
yuzden harici bir vektor veritabani bagimliligi tasinmaz.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..i18n import t
from ..logging import get_logger
from .chunker import Chunk
from .loaders import LoadedDoc

log = get_logger("rag.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    n_chunks    INTEGER NOT NULL DEFAULT 0,
    meta        TEXT NOT NULL DEFAULT '{}',
    indexed_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY,
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    text         TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    start_line   INTEGER NOT NULL DEFAULT 1,
    end_line     INTEGER NOT NULL DEFAULT 1,
    kind         TEXT NOT NULL DEFAULT 'doc',
    tokens       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    heading_path,
    chunk_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_TOKEN_RE = re.compile(r"[\wÀ-ɏ]{2,}", re.UNICODE)


@dataclass(slots=True)
class ChunkRecord:
    """Aramadan donen, kaynagiyla birlikte zenginlestirilmis parca."""

    id: int
    doc_id: int
    ordinal: int
    text: str
    heading_path: str
    start_line: int
    end_line: int
    kind: str
    source: str
    title: str
    score: float = 0.0

    def citation(self) -> str:
        loc = f":{self.start_line}" if self.start_line > 1 else ""
        head = f" · {self.heading_path}" if self.heading_path else ""
        return f"{self.title}{loc}{head}"

    def render(self, max_chars: int = 4000) -> str:
        body = self.text if len(self.text) <= max_chars else self.text[:max_chars] + "\n…[kesildi]"
        return f"[{self.citation()}]\n{body}"


class VectorStore:
    """Dokuman/parca/vektor kaliciligini yoneten depo."""

    def __init__(self, db_path: Path, dim: int) -> None:
        self.db_path = db_path
        self.dim = dim
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._fts_enabled = True
        self._cache: tuple[np.ndarray, np.ndarray, list[str]] | None = None
        self._cache_rows = -1
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # Kurulum
    # ------------------------------------------------------------------ #
    def _ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.executescript(_FTS_SCHEMA)
        except sqlite3.OperationalError as exc:  # pragma: no cover - eski sqlite
            self._fts_enabled = False
            log.warning(t("setup.no_fts", error=exc))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VectorStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Yazma
    # ------------------------------------------------------------------ #
    def document_hash(self, source: str) -> str | None:
        row = self._conn.execute(
            "SELECT sha256 FROM documents WHERE source = ?", (source,)
        ).fetchone()
        return row["sha256"] if row else None

    def delete_document(self, source: str) -> int:
        row = self._conn.execute("SELECT id FROM documents WHERE source = ?", (source,)).fetchone()
        if row is None:
            return 0
        doc_id = row["id"]
        chunk_ids = [
            r["id"] for r in self._conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))
        ]
        if self._fts_enabled and chunk_ids:
            self._conn.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?", [(cid,) for cid in chunk_ids]
            )
        self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self._conn.commit()
        self._cache = None
        return len(chunk_ids)

    def upsert_document(
        self,
        doc: LoadedDoc,
        chunks: list[Chunk],
        vectors: np.ndarray,
    ) -> int:
        """Dokumani (varsa eskisini silerek) yazar ve parca sayisini doner."""
        if len(chunks) != len(vectors):
            raise ValueError("Parca sayisi ile vektor sayisi uyusmuyor.")

        self.delete_document(doc.source)
        cur = self._conn.execute(
            "INSERT INTO documents (source, title, kind, sha256, n_chunks, meta, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                doc.source,
                doc.title,
                doc.kind,
                doc.sha256,
                len(chunks),
                json.dumps(doc.meta, ensure_ascii=False, default=str),
                time.time(),
            ),
        )
        doc_id = int(cur.lastrowid or 0)

        for chunk, vector in zip(chunks, vectors, strict=True):
            cur = self._conn.execute(
                "INSERT INTO chunks "
                "(doc_id, ordinal, text, heading_path, start_line, end_line, kind, tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    chunk.ordinal,
                    chunk.text,
                    chunk.heading_path,
                    chunk.start_line,
                    chunk.end_line,
                    doc.kind,
                    chunk.tokens,
                ),
            )
            chunk_id = int(cur.lastrowid or 0)
            self._conn.execute(
                "INSERT INTO embeddings (chunk_id, dim, vector) VALUES (?, ?, ?)",
                (chunk_id, int(vector.shape[0]), np.asarray(vector, dtype=np.float32).tobytes()),
            )
            if self._fts_enabled:
                self._conn.execute(
                    "INSERT INTO chunks_fts (text, heading_path, chunk_id) VALUES (?, ?, ?)",
                    (chunk.text, chunk.heading_path, chunk_id),
                )

        self._conn.commit()
        self._cache = None
        return len(chunks)

    # ------------------------------------------------------------------ #
    # Okuma
    # ------------------------------------------------------------------ #
    def _vector_cache(self) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """(vektorler, chunk_id'ler, kind'lar) uclusunu bellege alir.

        Onbellek yalnizca bu ornegin yazmalarinda gecersiz kilinir; ayni
        veritabanini paylasan BASKA bir surec (or. CLI kosarken acik duran web
        sunucusu) yeni parca eklerse bu ornek onlari goremezdi. Satir sayisi
        ucuz bir muhur olarak kullanilir.
        """
        current = int(
            self._conn.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()["n"]
        )
        if self._cache is not None and current == self._cache_rows:
            return self._cache
        self._cache = None
        rows = self._conn.execute(
            "SELECT e.chunk_id, e.vector, c.kind FROM embeddings e "
            "JOIN chunks c ON c.id = e.chunk_id ORDER BY e.chunk_id"
        ).fetchall()
        if not rows:
            self._cache = (
                np.zeros((0, self.dim), dtype=np.float32),
                np.zeros(0, dtype=np.int64),
                [],
            )
            self._cache_rows = 0
            return self._cache
        matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        ids = np.array([r["chunk_id"] for r in rows], dtype=np.int64)
        kinds = [r["kind"] for r in rows]
        self._cache = (matrix, ids, kinds)
        self._cache_rows = len(rows)
        return self._cache

    def search_semantic(
        self,
        query_vector: np.ndarray,
        k: int,
        kinds: Iterable[str] | None = None,
    ) -> list[tuple[int, float]]:
        matrix, ids, row_kinds = self._vector_cache()
        if matrix.shape[0] == 0:
            return []
        if matrix.shape[1] != query_vector.shape[0]:
            log.warning(
                t(
                    "setup.stored_dim_mismatch",
                    stored=matrix.shape[1],
                    query=query_vector.shape[0],
                )
            )
            return []

        scores = matrix @ np.asarray(query_vector, dtype=np.float32)
        if kinds is not None:
            wanted = set(kinds)
            mask = np.array([kind in wanted for kind in row_kinds], dtype=bool)
            scores = np.where(mask, scores, -np.inf)

        top = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(ids[i]), float(scores[i])) for i in top if np.isfinite(scores[i])]

    @staticmethod
    def _fts_query(query: str) -> str:
        """Serbest metni FTS5 icin guvenli bir OR sorgusuna cevirir."""
        tokens = _TOKEN_RE.findall(query.lower())
        if not tokens:
            return ""
        # Her token tirnaklanir; FTS5 operatorlerinin yanlislikla tetiklenmesini onler.
        return " OR ".join(f'"{t}"' for t in tokens[:32])

    def search_lexical(
        self,
        query: str,
        k: int,
        kinds: Iterable[str] | None = None,
    ) -> list[tuple[int, float]]:
        if not self._fts_enabled:
            return self._search_like(query, k, kinds)

        match = self._fts_query(query)
        if not match:
            return []
        sql = (
            "SELECT f.chunk_id AS chunk_id, bm25(chunks_fts) AS rank "
            "FROM chunks_fts f JOIN chunks c ON c.id = f.chunk_id "
            "WHERE chunks_fts MATCH ?"
        )
        params: list[Any] = [match]
        if kinds is not None:
            wanted = list(kinds)
            sql += f" AND c.kind IN ({','.join('?' * len(wanted))})"
            params.extend(wanted)
        sql += " ORDER BY rank LIMIT ?"
        params.append(k)

        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:  # pragma: no cover
            log.debug("FTS sorgusu basarisiz (%s); LIKE'a dusuluyor.", exc)
            return self._search_like(query, k, kinds)
        # bm25 dusuk = daha iyi; isareti cevirerek "yuksek = iyi" yapariz.
        return [(int(r["chunk_id"]), -float(r["rank"])) for r in rows]

    def _search_like(
        self, query: str, k: int, kinds: Iterable[str] | None
    ) -> list[tuple[int, float]]:
        tokens = _TOKEN_RE.findall(query.lower())[:6]
        if not tokens:
            return []
        clauses = " OR ".join("lower(text) LIKE ?" for _ in tokens)
        params: list[Any] = [f"%{t}%" for t in tokens]
        sql = f"SELECT id FROM chunks WHERE ({clauses})"
        if kinds is not None:
            wanted = list(kinds)
            sql += f" AND kind IN ({','.join('?' * len(wanted))})"
            params.extend(wanted)
        sql += " LIMIT ?"
        params.append(k)
        rows = self._conn.execute(sql, params).fetchall()
        return [(int(r["id"]), 1.0) for r in rows]

    def fetch_chunks(self, chunk_ids: Iterable[int]) -> dict[int, ChunkRecord]:
        ids = list(chunk_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            "SELECT c.*, d.source, d.title FROM chunks c "
            f"JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({placeholders})",
            ids,
        ).fetchall()
        return {
            int(r["id"]): ChunkRecord(
                id=int(r["id"]),
                doc_id=int(r["doc_id"]),
                ordinal=int(r["ordinal"]),
                text=r["text"],
                heading_path=r["heading_path"],
                start_line=int(r["start_line"]),
                end_line=int(r["end_line"]),
                kind=r["kind"],
                source=r["source"],
                title=r["title"],
            )
            for r in rows
        }

    def vectors_for(self, chunk_ids: list[int]) -> np.ndarray:
        """MMR cesitlendirmesi icin belirli parcalarin vektorlerini doner."""
        if not chunk_ids:
            return np.zeros((0, self.dim), dtype=np.float32)
        matrix, ids, _ = self._vector_cache()
        index = {int(cid): i for i, cid in enumerate(ids)}
        rows = [matrix[index[cid]] for cid in chunk_ids if cid in index]
        if not rows:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(rows)

    # ------------------------------------------------------------------ #
    # Bilgi
    # ------------------------------------------------------------------ #
    def stored_dim(self) -> int | None:
        """Depodaki vektorlerin boyutu; depo bossa None."""
        row = self._conn.execute("SELECT dim FROM embeddings LIMIT 1").fetchone()
        return int(row["dim"]) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT source, title, kind, n_chunks, indexed_at FROM documents ORDER BY title"
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        docs = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        chunks = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        by_kind = {
            r["kind"]: r["n"]
            for r in self._conn.execute("SELECT kind, COUNT(*) AS n FROM chunks GROUP BY kind")
        }
        return {
            "documents": docs,
            "chunks": chunks,
            "by_kind": by_kind,
            "fts": self._fts_enabled,
            "db": str(self.db_path),
        }
