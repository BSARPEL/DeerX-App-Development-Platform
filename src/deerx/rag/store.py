"""Bilgi tabani deposu: SQLite + FTS5 (sozcuksel) + numpy (anlamsal).

Tek dosyalik, sunucu gerektirmeyen bir depo. Proje olcegindeki (on binlerce
parcaya kadar) korpuslarda kaba kuvvet kosinus aramasi milisaniyeler surer, bu
yuzden harici bir vektor veritabani bagimliligi tasinmaz.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..i18n import t
from ..logging import get_logger
from .chunker import Chunk
from .loaders import LoadedDoc

log = get_logger("rag.store")

# (vektorler, chunk_id'ler, kind'lar, doc_id'ler). Dorduncu eleman belge
# daraltmasi icin zorunlu: skorlar parca duzeyinde uretiliyor ve parcanin
# hangi belgeye ait oldugu yalnizca burada bilinebilir.
_Onbellek = tuple["np.ndarray", "np.ndarray", list[str], "np.ndarray"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    n_chunks    INTEGER NOT NULL DEFAULT 0,
    meta        TEXT NOT NULL DEFAULT '{}',
    indexed_at  REAL NOT NULL,
    uploaded_by TEXT NOT NULL DEFAULT '',
    uploaded_at REAL NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1
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
    # Belgesi ETKIN mi. Ajan yolunda hep True (pasifler dislaniyor);
    # arayuzun tani aramasinda False olabilir ve ekran bunu soyler.
    is_active: bool = True

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
        # Baglanti PAYLASILMAZ, is parcacigi basina acilir: tek bir
        # `sqlite3.Connection`i iki is parcacigindan kullanmak CPython'da
        # uc ayri sekilde duser ve ucu de olculdu.
        self._yerel = threading.local()
        self._baglanti_kilidi = threading.Lock()
        self._baglantilar: list[sqlite3.Connection] = []
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._fts_enabled = True
        self._cache: _Onbellek | None = None
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
        self._migrate()
        self._commit()

    def _migrate(self) -> None:
        """Var olan bir veritabanini yeni sutunlarla yukseltir.

        `CREATE TABLE IF NOT EXISTS` var olan bir tabloyu DEGISTIRMEZ:
        gocmen olmadan yeni sutunlar yalnizca bos bir veritabaninda
        olusur ve mevcut her kurulum "no such column" ile coker.
        `ProjectState._migrate` ile ayni desen.
        """
        mevcut = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(documents)")
        }
        for ad, tanim in (
            ("uploaded_by", "TEXT NOT NULL DEFAULT ''"),
            ("uploaded_at", "REAL NOT NULL DEFAULT 0"),
            ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if ad not in mevcut:
                self._conn.execute(f"ALTER TABLE documents ADD COLUMN {ad} {tanim}")

    # ------------------------------------------------------------------ #
    # Baglanti: is parcacigi basina bir tane
    # ------------------------------------------------------------------ #
    @property
    def _conn(self) -> sqlite3.Connection:
        """Bu is parcacigina ait baglanti; yoksa acilir.

        Baglantiyi paylasmak yerine cogaltmak, SQLite'in tasarladigi
        yoldur: WAL kipinde okuyucular yaziciyi engellemez ve her
        baglantinin kendi islem durumu olur.
        """
        conn = getattr(self._yerel, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # Yazici kilidi tutuyorsa bekle. Bunsuz es zamanli bir yazma
            # aninda `database is locked` ile duserdi.
            conn.execute("PRAGMA busy_timeout=5000")
            self._yerel.conn = conn
            with self._baglanti_kilidi:
                self._baglantilar.append(conn)
        return conn

    def _islem_derinligi_al(self) -> int:
        return getattr(self._yerel, "derinlik", 0)

    def _islem_derinligi_yaz(self, deger: int) -> None:
        self._yerel.derinlik = deger

    @contextmanager
    def _islem(self) -> Iterator[None]:
        """Cok ifadeli bir yazmayi tek isleme alir; IC ICE GECISI TANIR.

        Ic metotlar kendi `_commit()`lerini cagiriyor. Sarmalayici bunu
        bilmezse ic cagri disaridaki islemi bitirir ve disaridaki `COMMIT`
        "no transaction is active" ile duser -- olculdu.
        """
        if self._islem_derinligi_al():
            self._islem_derinligi_yaz(self._islem_derinligi_al() + 1)
            try:
                yield
            finally:
                self._islem_derinligi_yaz(self._islem_derinligi_al() - 1)
            return

        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        self._islem_derinligi_yaz(1)
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            self._islem_derinligi_yaz(0)

    def _commit(self) -> None:
        """Acik bir islemin icindeyken hicbir sey yapmaz."""
        if not self._islem_derinligi_al():
            self._conn.commit()

    def close(self) -> None:
        """Butun is parcaciklarinin baglantilarini kapatir."""
        with self._baglanti_kilidi:
            for conn in self._baglantilar:
                try:
                    conn.close()
                except sqlite3.Error:  # pragma: no cover - kapanista onemsiz
                    pass
            self._baglantilar.clear()
        self._yerel = threading.local()

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
        self._commit()
        self._cache = None
        return len(chunk_ids)

    def upsert_document(
        self,
        doc: LoadedDoc,
        chunks: list[Chunk],
        vectors: np.ndarray,
        uploaded_by: str = "",
    ) -> int:
        """Dokumani (varsa eskisini silerek) yazar ve parca sayisini doner."""
        if len(chunks) != len(vectors):
            raise ValueError("Parca sayisi ile vektor sayisi uyusmuyor.")

        # Yeniden indeksleme kaydi SILIP yeniden yaziyor: yukleyen ve
        # yuklenme zamani onceden okunmazsa her yeniden indekslemede
        # kaybolurdu -- ve bir belgenin kim tarafindan getirildigi, tam
        # da o belge degistiginde onemli hale gelir.
        onceki = self._conn.execute(
            "SELECT uploaded_by, uploaded_at FROM documents WHERE source = ?",
            (doc.source,),
        ).fetchone()
        kim = uploaded_by or (onceki["uploaded_by"] if onceki else "")
        ne_zaman = (onceki["uploaded_at"] if onceki and onceki["uploaded_at"] else 0.0)

        # Sil-ve-yaz TEK bir islem olmali: arada kesilirse belge yok
        # olur. Otomatik islem modunda bu butunluk kendiliginden gelmiyor,
        # acikca aliniyor.
        with self._islem():
            return self._upsert_govde(doc, chunks, vectors, kim, ne_zaman)

    def _upsert_govde(
        self,
        doc: LoadedDoc,
        chunks: list[Chunk],
        vectors: np.ndarray,
        kim: str,
        ne_zaman: float,
    ) -> int:
        self.delete_document(doc.source)
        cur = self._conn.execute(
            "INSERT INTO documents "
            "(source, title, kind, sha256, n_chunks, meta, indexed_at, "
            " uploaded_by, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc.source,
                doc.title,
                doc.kind,
                doc.sha256,
                len(chunks),
                json.dumps(doc.meta, ensure_ascii=False, default=str),
                time.time(),
                kim,
                ne_zaman or (time.time() if kim else 0.0),
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

        self._commit()
        self._cache = None
        return len(chunks)

    # ------------------------------------------------------------------ #
    # Okuma
    # ------------------------------------------------------------------ #
    def _vector_cache(self) -> _Onbellek:
        """(vektorler, chunk_id'ler, kind'lar, doc_id'ler) dortlusu.

        `doc_ids` olmadan anlamsal tarafta belge daraltmasi yapilamaz:
        skorlar parca duzeyinde uretiliyor ve parcanin hangi belgeye ait
        oldugu yalnizca burada bilinebilir.

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
            "SELECT e.chunk_id, e.vector, c.kind, c.doc_id FROM embeddings e "
            "JOIN chunks c ON c.id = e.chunk_id ORDER BY e.chunk_id"
        ).fetchall()
        if not rows:
            self._cache = (
                np.zeros((0, self.dim), dtype=np.float32),
                np.zeros(0, dtype=np.int64),
                [],
                np.zeros(0, dtype=np.int64),
            )
            self._cache_rows = 0
            return self._cache
        matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        ids = np.array([r["chunk_id"] for r in rows], dtype=np.int64)
        kinds = [r["kind"] for r in rows]
        docs = np.array([r["doc_id"] for r in rows], dtype=np.int64)
        self._cache = (matrix, ids, kinds, docs)
        self._cache_rows = len(rows)
        return self._cache

    def search_semantic(
        self,
        query_vector: np.ndarray,
        k: int,
        kinds: Iterable[str] | None = None,
        doc_ids: Iterable[int] | None = None,
        exclude_doc_ids: Iterable[int] | None = None,
    ) -> list[tuple[int, float]]:
        matrix, ids, row_kinds, row_docs = self._vector_cache()
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
        if doc_ids is not None:
            izin = np.asarray(sorted(set(doc_ids)), dtype=np.int64)
            # Bos kapsam "hicbir belge" demektir, "kapsam yok" degil:
            # caginin bos liste vermesi ile hic vermemesi ayri seyler.
            scores = np.where(np.isin(row_docs, izin), scores, -np.inf)
        if exclude_doc_ids:
            # Pasiflik kapsamdan SONRA uygulanir: bir kosu kapsami bir
            # DARALTMADIR, kullanicinin dislama kararini geri alamaz.
            haric = np.asarray(sorted(set(exclude_doc_ids)), dtype=np.int64)
            scores = np.where(np.isin(row_docs, haric), -np.inf, scores)

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
        doc_ids: Iterable[int] | None = None,
        exclude_doc_ids: Iterable[int] | None = None,
    ) -> list[tuple[int, float]]:
        if not self._fts_enabled:
            return self._search_like(query, k, kinds, doc_ids, exclude_doc_ids)

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
        if doc_ids is not None:
            izin = sorted(set(doc_ids))
            sql += f" AND c.doc_id IN ({','.join('?' * len(izin)) or 'NULL'})"
            params.extend(izin)
        if exclude_doc_ids:
            haric = sorted(set(exclude_doc_ids))
            sql += f" AND c.doc_id NOT IN ({','.join('?' * len(haric))})"
            params.extend(haric)
        sql += " ORDER BY rank LIMIT ?"
        params.append(k)

        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:  # pragma: no cover
            log.debug("FTS sorgusu basarisiz (%s); LIKE'a dusuluyor.", exc)
            return self._search_like(query, k, kinds, doc_ids, exclude_doc_ids)
        # bm25 dusuk = daha iyi; isareti cevirerek "yuksek = iyi" yapariz.
        return [(int(r["chunk_id"]), -float(r["rank"])) for r in rows]

    def _search_like(
        self,
        query: str,
        k: int,
        kinds: Iterable[str] | None,
        doc_ids: Iterable[int] | None = None,
        exclude_doc_ids: Iterable[int] | None = None,
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
        if doc_ids is not None:
            izin = sorted(set(doc_ids))
            sql += f" AND doc_id IN ({','.join('?' * len(izin)) or 'NULL'})"
            params.extend(izin)
        if exclude_doc_ids:
            haric = sorted(set(exclude_doc_ids))
            sql += f" AND doc_id NOT IN ({','.join('?' * len(haric))})"
            params.extend(haric)
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
            "SELECT c.*, d.source, d.title, d.is_active FROM chunks c "
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
                # Isabet, belgesinin pasif olup olmadigini TASIR. Ajan
                # yolunda hep True (pasifler zaten dislanmis); arayuzun
                # tani aramasinda False olabilir ve ekran bunu soyler.
                is_active=bool(r["is_active"]),
            )
            for r in rows
        }

    def vectors_for(self, chunk_ids: list[int]) -> np.ndarray:
        """MMR cesitlendirmesi icin belirli parcalarin vektorlerini doner."""
        if not chunk_ids:
            return np.zeros((0, self.dim), dtype=np.float32)
        matrix, ids, _, _ = self._vector_cache()
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
            "SELECT id, source, title, kind, n_chunks, indexed_at, "
            "uploaded_by, uploaded_at, is_active FROM documents "
            "ORDER BY indexed_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def doc_ids_for(self, sources: Iterable[str]) -> list[int]:
        """Kaynak yollarini belge kimliklerine cevirir.

        Bilinmeyen bir kaynak SESSIZCE atlanir: kapsam bir suzgectir,
        bir iddia degil -- silinmis bir belgeye atif yuzunden kosunun
        dusmesi, kapsamin hic olmamasindan kotudur.
        """
        istenen = [str(x) for x in sources]
        if not istenen:
            return []
        isaret = ",".join("?" * len(istenen))
        rows = self._conn.execute(
            f"SELECT id FROM documents WHERE source IN ({isaret})", istenen
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def active_doc_ids(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT id FROM documents WHERE is_active = 1"
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def inactive_doc_ids(self) -> list[int]:
        """Kullanicinin "artik buna bakma" dedigi belgeler.

        Arama bunlari DISLAR. Etkin olanlari saymak yerine pasif olanlari
        saymanin iki sebebi var: liste normalde cok daha kucuk (bir
        korpusta genelde birkac belge pasiflestirilir) ve bos oldugunda
        suzgec tamamen atlanabilir -- yani hicbir sey pasif degilken
        arama bugunku kadar hizli kalir.
        """
        rows = self._conn.execute(
            "SELECT id FROM documents WHERE is_active = 0"
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def is_inactive(self, source: str) -> bool:
        """Belge bilerek pasiflestirilmis mi.

        Yeniden indeksleme bunu sormak ZORUNDA: dosya diskte durdugu
        icin `docs/` her tarandiginda geri gelirdi ve kullanicinin
        "artik buna bakma" karari sessizce iptal olurdu.
        """
        row = self._conn.execute(
            "SELECT is_active FROM documents WHERE source = ?", (str(source),)
        ).fetchone()
        return row is not None and not int(row["is_active"])

    def set_active(self, source: str, active: bool) -> bool:
        """Belgeyi aramadan ve yeniden indekslemeden cikarir/geri alir.

        Silmekten farki: parcalar ve vektorler DURUR, yani geri almak
        yeniden indeksleme gerektirmez. Bir sartnamenin eski surumunu
        "artik buna bakma" diye isaretlemek, silmekten daha sik istenen
        seydir ve geri donusu olmali.
        """
        cur = self._conn.execute(
            "UPDATE documents SET is_active = ? WHERE source = ?",
            (1 if active else 0, str(source)),
        )
        self._commit()
        return cur.rowcount > 0

    def stats(self) -> dict[str, Any]:
        docs = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        # ETKIN sayisi ayrica. Toplam envanterdir; ajanlarin gordugu
        # sayi ise budur ve ekran ikisini karistirirsa uc belgeyi
        # pasiflestiren kullaniciya yine "27 belge" der.
        active = self._conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE is_active = 1"
        ).fetchone()["n"]
        chunks = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        # Parcalar da ayni ayrimi tasir: 799 parcanin 640'i aranabilir
        # olabilir ve "799" o farki gizler.
        active_chunks = self._conn.execute(
            "SELECT COUNT(*) AS n FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.is_active = 1"
        ).fetchone()["n"]
        by_kind = {
            r["kind"]: r["n"]
            for r in self._conn.execute("SELECT kind, COUNT(*) AS n FROM chunks GROUP BY kind")
        }
        return {
            "documents": docs,
            "active_documents": active,
            "chunks": chunks,
            "active_chunks": active_chunks,
            "by_kind": by_kind,
            "fts": self._fts_enabled,
            "db": str(self.db_path),
        }
