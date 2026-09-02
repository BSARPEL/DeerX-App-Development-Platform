"""Gomme (embedding) saglayicilari.

Varsayilan `fastembed` ONNX uzerinden yerel calisir: veri makineden cikmaz ve
gomme maliyeti sifirdir. `hash` saglayicisi model indirmeden calisan, cevrimdisi
duman testleri icin deterministik bir yedektir.
"""

from __future__ import annotations

import hashlib
import re
import warnings
from typing import Protocol, runtime_checkable

import numpy as np

from ..config import RagSettings
from ..errors import ConfigError
from ..i18n import t
from ..logging import get_logger

log = get_logger("rag.embedder")

# E5 ailesi asimetrik egitilmistir: sorgu ve pasaj farkli on ek ister.
_E5_PATTERN = re.compile(r"e5", re.IGNORECASE)


def _supported_multilingual() -> str:
    """Hata mesajlarinda gosterilmek uzere cok dilli model listesi."""
    try:
        from fastembed import TextEmbedding

        rows = [
            f"  - {m['model']}  (dim={m['dim']}, ~{m.get('size_in_GB', '?')} GB)"
            for m in TextEmbedding.list_supported_models()
            if any(tag in m["model"].lower() for tag in ("multilingual", "labse", "e5"))
        ]
        return "\n".join(rows) if rows else "  (liste alinamadi)"
    except Exception:  # noqa: BLE001 - yalnizca yardim metni
        return "  (liste alinamadi)"


@runtime_checkable
class Embedder(Protocol):
    """Gomme saglayicisi arayuzu."""

    dim: int
    name: str

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """Satir bazli L2 normalizasyon; kosinus benzerligini nokta carpimina indirger."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class FastEmbedEmbedder:
    """Yerel ONNX gomme modeli (varsayilan)."""

    def __init__(self, model_name: str, dim: int) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                t("setup.no_fastembed")
            ) from exc

        self.name = model_name
        self.dim = dim
        self._is_e5 = bool(_E5_PATTERN.search(model_name))
        log.debug("fastembed modeli yukleniyor: %s", model_name)
        try:
            with warnings.catch_warnings():
                # fastembed >= 0.6 bazi modellerde CLS yerine ortalama havuzlama
                # kullandigini her yuklemede uyarir. Varsayilan modelimizin geri
                # getirme kalitesi bu havuzlamayla olculdu; uyari gurultuden ibaret.
                warnings.filterwarnings("ignore", message=".*mean pooling.*")
                self._model = TextEmbedding(model_name=model_name)
        except ValueError as exc:
            raise ConfigError(
                t(
                    "setup.model_unsupported",
                    model=model_name,
                    options=_supported_multilingual(),
                )
            ) from exc

    def _encode(self, texts: list[str]) -> np.ndarray:
        vectors = np.array(list(self._model.embed(texts)), dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        if vectors.shape[1] != self.dim:
            # Konfigurasyondaki boyut modelle uyusmuyorsa modele guven.
            log.warning(
                t(
                    "setup.dim_mismatch",
                    actual=vectors.shape[1],
                    configured=self.dim,
                )
            )
            self.dim = int(vectors.shape[1])
        return _normalize(vectors)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        prepared = [f"passage: {t}" for t in texts] if self._is_e5 else texts
        return self._encode(prepared)

    def embed_query(self, text: str) -> np.ndarray:
        prepared = f"query: {text}" if self._is_e5 else text
        return self._encode([prepared])[0]


class HashEmbedder:
    """Model indirmeden calisan deterministik yedek.

    Karakter 3-gram'larini sabit boyutlu bir uzaya hash'ler. Anlamsal benzerlik
    yakalamaz; yalnizca yuzeysel ortakligi olcer. Uretimde kullanilmamalidir.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.name = "hash-3gram"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        cleaned = re.sub(r"\s+", " ", text.lower())
        for i in range(max(1, len(cleaned) - 2)):
            gram = cleaned[i : i + 3]
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalize(np.vstack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vector(text).reshape(1, -1))[0]


def build_embedder(rag: RagSettings) -> Embedder:
    """Ayarlara gore gomme saglayicisini kurar."""
    if rag.embedding_provider == "hash":
        return HashEmbedder(dim=rag.embedding_dim)
    return FastEmbedEmbedder(model_name=rag.embedding_model, dim=rag.embedding_dim)
