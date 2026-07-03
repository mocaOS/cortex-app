"""Candidate-pair generation logic for targeted Phase B relationship discovery.

Pure functions — no Neo4j or LLM access — so ranking/capping behavior is
unit-testable. The graph queries live in `neo4j_service` and the LLM
verification in `document_processor._analyze_relationships_targeted`.

Signals:
- kNN: entity-embedding vector-index similarity (Neo4j cosine index score, 0-1)
- doc co-mention: number of distinct documents mentioning both entities

Score = 0.6 * knn_score + 0.4 * min(1, shared_docs / 4). Pairs surfaced by
both signals naturally outrank single-signal pairs.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CandidatePair:
    """A ranked entity pair proposed for LLM verification."""
    source: str
    target: str
    knn_score: float = 0.0   # vector index score (0 when not a kNN candidate)
    shared_docs: int = 0     # co-mention document count (0 when not a co-mention candidate)
    score: float = 0.0


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    return tuple(sorted((a.lower(), b.lower())))


def merge_and_rank_candidates(
    knn_pairs: Iterable[Tuple[str, str, float]],
    doc_pairs: Iterable[Tuple[str, str, int]],
    per_entity_cap: int = 10,
    total_cap: int = 15000,
) -> List[CandidatePair]:
    """Merge both candidate sources, score, and apply hub/total caps.

    Args:
        knn_pairs: (source, target, similarity_score) tuples from the vector index.
        doc_pairs: (source, target, shared_doc_count) tuples from co-mention.
        per_entity_cap: Max pairs any single entity may appear in (0 = no cap).
        total_cap: Max pairs returned overall (0 = no cap).

    Returns:
        CandidatePairs sorted by score descending, deduplicated across sources
        and directions, with caps applied greedily in rank order.
    """
    merged: Dict[Tuple[str, str], CandidatePair] = {}

    for source, target, sim in knn_pairs:
        if not source or not target or source.lower() == target.lower():
            continue
        key = _pair_key(source, target)
        existing = merged.get(key)
        if existing:
            existing.knn_score = max(existing.knn_score, float(sim))
        else:
            merged[key] = CandidatePair(source=source, target=target, knn_score=float(sim))

    for source, target, shared in doc_pairs:
        if not source or not target or source.lower() == target.lower():
            continue
        key = _pair_key(source, target)
        existing = merged.get(key)
        if existing:
            existing.shared_docs = max(existing.shared_docs, int(shared))
        else:
            merged[key] = CandidatePair(source=source, target=target, shared_docs=int(shared))

    candidates = list(merged.values())
    for c in candidates:
        doc_component = min(1.0, c.shared_docs / 4.0)
        c.score = 0.6 * c.knn_score + 0.4 * doc_component
    candidates.sort(key=lambda c: c.score, reverse=True)

    # Greedy caps in rank order: hub guard first, then total budget.
    selected: List[CandidatePair] = []
    entity_use: Dict[str, int] = {}
    for c in candidates:
        if total_cap > 0 and len(selected) >= total_cap:
            break
        if per_entity_cap > 0:
            s_key, t_key = c.source.lower(), c.target.lower()
            if entity_use.get(s_key, 0) >= per_entity_cap or entity_use.get(t_key, 0) >= per_entity_cap:
                continue
            entity_use[s_key] = entity_use.get(s_key, 0) + 1
            entity_use[t_key] = entity_use.get(t_key, 0) + 1
        selected.append(c)

    dropped = len(candidates) - len(selected)
    if dropped > 0:
        logger.info(
            f"Candidate ranking: kept {len(selected)}/{len(candidates)} pairs "
            f"(per_entity_cap={per_entity_cap}, total_cap={total_cap})"
        )
    return selected


def group_pairs_for_verification(
    pairs: List[CandidatePair],
    pairs_per_call: int = 40,
) -> List[List[Tuple[str, str]]]:
    """Split ranked pairs into LLM verification groups.

    Pairs are sorted by entity name (not score) before chunking so that pairs
    sharing an entity land in the same group — fewer distinct entities per
    prompt and better chunk-context reuse.
    """
    if not pairs:
        return []
    pairs_per_call = max(1, pairs_per_call)
    ordered = sorted(pairs, key=lambda c: (c.source.lower(), c.target.lower()))
    tuples = [(c.source, c.target) for c in ordered]
    return [
        tuples[i:i + pairs_per_call]
        for i in range(0, len(tuples), pairs_per_call)
    ]
