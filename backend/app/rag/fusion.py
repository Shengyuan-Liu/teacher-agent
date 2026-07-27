"""Reciprocal Rank Fusion.

Dense and BM25 scores are not comparable — cosine distance and term-frequency
weights live on different scales — so combine positions rather than values.
Each list contributes 1/(k + rank); k dampens how much the very top of one list
can dominate the other.
"""

import uuid
from collections.abc import Sequence

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[uuid.UUID]], k: int = RRF_K
) -> list[tuple[uuid.UUID, float]]:
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
