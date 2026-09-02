"""RAG katmani testleri: parcalama, indeksleme, hibrit arama."""

from __future__ import annotations

import numpy as np
import pytest

from deerx.rag.chunker import chunk_text, estimate_tokens
from deerx.rag.retriever import maximal_marginal_relevance, reciprocal_rank_fusion

MARKDOWN = """\
# Ust Baslik

Giris paragrafi.

## Bolum A

A bolumunun icerigi burada.

### A.1 Alt bolum

Alt bolum detayi.

## Bolum B

B bolumunun icerigi.
"""

CODE = '''\
import os


def alpha(x):
    """Ilk fonksiyon."""
    return x + 1


class Beta:
    def method(self):
        return 2


def gamma():
    return 3
'''


class TestChunker:
    def test_markdown_heading_path(self):
        chunks = chunk_text(MARKDOWN, kind="doc", max_tokens=50, min_tokens=1)
        paths = [c.heading_path for c in chunks]
        assert any("Bolum A" in p for p in paths)
        assert any("A.1 Alt bolum" in p for p in paths)
        # Baslik yolu hiyerarsiyi tasimali, yalnizca son basligi degil.
        nested = next(p for p in paths if "A.1" in p)
        assert nested.startswith("Ust Baslik > Bolum A")

    def test_ordinals_are_sequential(self):
        chunks = chunk_text(MARKDOWN, kind="doc", max_tokens=50, min_tokens=1)
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    def test_code_splits_on_top_level_definitions(self):
        chunks = chunk_text(CODE, kind="code", max_tokens=40, min_tokens=1)
        symbols = " ".join(c.heading_path for c in chunks)
        assert "def alpha" in symbols
        assert "class Beta" in symbols
        # Girintili `method` ust duzey sinir olmamali.
        assert "def method" not in symbols

    def test_headings_inside_code_fence_are_ignored(self):
        text = "# Baslik\n\n```\n# bu bir yorum, baslik degil\n```\n\nGovde."
        chunks = chunk_text(text, kind="doc", max_tokens=500, min_tokens=1)
        assert all("bu bir yorum" not in c.heading_path for c in chunks)

    def test_large_section_is_windowed_with_overlap(self):
        body = "\n".join(f"satir {i} icerik metni burada devam ediyor" for i in range(400))
        chunks = chunk_text(f"# Baslik\n\n{body}", kind="doc", max_tokens=200, overlap_tokens=40)
        assert len(chunks) > 1
        assert all(c.tokens <= 400 for c in chunks)

    def test_contextualized_prefixes_heading(self):
        chunks = chunk_text(MARKDOWN, kind="doc", max_tokens=50, min_tokens=1)
        chunk = next(c for c in chunks if c.heading_path)
        assert chunk.contextualized().startswith(chunk.heading_path)

    def test_empty_input(self):
        assert chunk_text("   \n\n  ", kind="doc") == []

    def test_estimate_tokens_monotonic(self):
        assert estimate_tokens("kisa") < estimate_tokens("kisa" * 100)


class TestFusion:
    def test_rrf_rewards_agreement(self):
        a = [(1, 0.9), (2, 0.8), (3, 0.7)]
        b = [(3, 5.0), (1, 4.0), (9, 3.0)]
        fused = dict(reciprocal_rank_fusion([a, b], k=60))
        # 1 her iki listede de ust siralarda; 9 yalnizca birinde.
        assert fused[1] > fused[9]
        assert fused[3] > fused[2]

    def test_rrf_weights_applied(self):
        a = [(1, 1.0)]
        b = [(2, 1.0)]
        fused = dict(reciprocal_rank_fusion([a, b], k=60, weights=[1.0, 0.5]))
        assert fused[1] > fused[2]

    def test_rrf_rejects_mismatched_weights(self):
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([[(1, 1.0)]], weights=[1.0, 1.0])


class TestMMR:
    def test_uses_supplied_relevance_not_cosine(self):
        # Uc aday: 0 ve 1 birbirinin neredeyse ayni, 2 farkli yonde.
        vectors = np.array(
            [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]], dtype=np.float32
        )
        ids = [10, 11, 12]
        # Fuzyon skoru 12'yi acik ara one koyuyor.
        picked = maximal_marginal_relevance(
            vectors, ids, [0.1, 0.1, 0.9], lambda_=0.9, top_n=1
        )
        assert picked == [12]

    def test_diversity_breaks_near_duplicates(self):
        vectors = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        picked = maximal_marginal_relevance(
            vectors, [1, 2, 3], [1.0, 0.99, 0.5], lambda_=0.5, top_n=2
        )
        # Ikinci secim, birincinin kopyasi olmamali.
        assert picked[0] == 1
        assert picked[1] == 3

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError):
            maximal_marginal_relevance(np.zeros((3, 2), np.float32), [1, 2, 3], [1.0])

    def test_single_candidate(self):
        assert maximal_marginal_relevance(
            np.array([[1.0, 0.0]], np.float32), [7], [1.0]
        ) == [7]


class TestKnowledgeBase:
    def test_ingest_and_search(self, kb, workspace):
        results = kb.ingest_path(workspace / "docs")
        assert len(results) == 1
        assert results[0].ok and results[0].chunks > 0

        hits = kb.search("cevrimdisi calisma", k=3)
        assert hits
        assert any("Cevrimdisi" in h.heading_path for h in hits)

    def test_lexical_finds_rare_token(self, kb, workspace):
        kb.ingest_path(workspace / "docs")
        # "KVKK" nadir bir token: sozcuksel siralama onu bulmali ve
        # fuzyon + MMR bunu ust sirada tutmali.
        hits = kb.search("KVKK", k=2)
        assert any("KVKK" in h.text for h in hits)

    def test_reingest_skips_unchanged(self, kb, workspace):
        kb.ingest_path(workspace / "docs")
        again = kb.ingest_path(workspace / "docs")
        assert all(r.skipped for r in again)

    def test_force_reindexes(self, kb, workspace):
        kb.ingest_path(workspace / "docs")
        again = kb.ingest_path(workspace / "docs", force=True)
        assert all(not r.skipped for r in again)

    def test_kind_filter(self, kb, workspace):
        kb.ingest_path(workspace / "docs")
        kb.ingest_text("def foo(): return 1", source="x.py", title="x.py", kind="code")
        assert all(h.kind == "code" for h in kb.search("foo", k=5, kinds=["code"]))

    def test_empty_query(self, kb):
        assert kb.search("   ") == []

    def test_context_block_cites_sources(self, kb, workspace):
        kb.ingest_path(workspace / "docs")
        block = kb.context_block("is emri yasam dongusu", k=2)
        assert "sartname.md" in block

    def test_forget_removes_document(self, kb, workspace):
        kb.ingest_path(workspace / "docs")
        source = kb.list_documents()[0]["source"]
        assert kb.forget(source) > 0
        assert kb.stats()["chunks"] == 0

    def test_dimension_change_is_detected(self, settings, workspace):
        from deerx.errors import ConfigError
        from deerx.rag import KnowledgeBase

        first = KnowledgeBase(settings)
        first.ingest_path(workspace / "docs")
        first.close()

        settings.rag.embedding_dim = 256  # gomme modeli degistirilmis gibi
        second = KnowledgeBase(settings)
        with pytest.raises(ConfigError, match="Gomme modeli degismis"):
            _ = second.embedder
        second.close()
