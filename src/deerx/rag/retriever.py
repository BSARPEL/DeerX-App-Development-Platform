"""Siralama fuzyonu ve cesitlendirme.

Hibrit arama iki bagimsiz siralama uretir (anlamsal + sozcuksel). Skorlari
dogrudan toplamak yerine RRF kullaniriz: skor olcekleri karsilastirilabilir
olmadigi icin sira bazli fuzyon daha kararlidir. Ardindan MMR ile birbirinin
neredeyse ayni olan parcalar elenir; boylece baglam penceresi tekrarla dolmaz.
"""

from __future__ import annotations

import numpy as np


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int, float]]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[int, float]]:
    """Birden fazla siralamayi RRF ile birlestirir.

    Args:
        rankings: Her biri (id, skor) ciftlerinden olusan, en iyiden kotuye sirali listeler.
        k: RRF sabiti. Buyudukce ust siralarin avantaji azalir.
        weights: Siralama basina agirlik; verilmezse hepsi 1.0.

    Returns:
        (id, fuzyon_skoru) ciftleri, azalan skora gore sirali.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights ve rankings uzunluklari esit olmali.")

    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, (item_id, _score) in enumerate(ranking, start=1):
            fused[item_id] = fused.get(item_id, 0.0) + weight / (k + rank)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


def _minmax(values: np.ndarray) -> np.ndarray:
    """Skorlari [0, 1] araligina tasir; sabit dizide hepsi 1.0 olur."""
    low, high = float(values.min()), float(values.max())
    if high - low < 1e-12:
        return np.ones_like(values, dtype=np.float32)
    return ((values - low) / (high - low)).astype(np.float32)


def maximal_marginal_relevance(
    candidate_vectors: np.ndarray,
    candidate_ids: list[int],
    relevance_scores: list[float],
    *,
    lambda_: float = 0.6,
    top_n: int = 8,
) -> list[int]:
    """MMR ile alaka/cesitlilik dengesi kurar.

    `relevance_scores` fuzyon (RRF) skorlaridir — ham kosinus degil. Bu onemli:
    alaka terimini yeniden kosinusten hesaplamak, sozcuksel siralamanin katkisini
    tamamen atar ve hibrit aramayi saf anlamsal aramaya dusurur. Vektorler burada
    yalnizca *fazlalik* (redundancy) terimi icin kullanilir.

    lambda_ = 1.0 saf alaka, 0.0 saf cesitlilik demektir.
    """
    if candidate_vectors.shape[0] == 0:
        return []
    if candidate_vectors.shape[0] <= 1:
        return candidate_ids[:top_n]
    if len(relevance_scores) != len(candidate_ids):
        raise ValueError("relevance_scores ve candidate_ids uzunluklari esit olmali.")

    relevance = _minmax(np.asarray(relevance_scores, dtype=np.float32))
    similarity = candidate_vectors @ candidate_vectors.T

    selected: list[int] = []
    remaining = list(range(len(candidate_ids)))

    while remaining and len(selected) < top_n:
        if not selected:
            best = max(remaining, key=lambda i: relevance[i])
        else:
            def mmr_score(i: int) -> float:
                redundancy = max(similarity[i][j] for j in selected)
                return lambda_ * float(relevance[i]) - (1 - lambda_) * float(redundancy)

            best = max(remaining, key=mmr_score)
        selected.append(best)
        remaining.remove(best)

    return [candidate_ids[i] for i in selected]
