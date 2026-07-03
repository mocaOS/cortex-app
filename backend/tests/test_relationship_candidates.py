"""Tests for targeted Phase B candidate ranking (relationship_candidates.py)."""

from __future__ import annotations

from app.services.relationship_candidates import (
    CandidatePair,
    group_pairs_for_verification,
    merge_and_rank_candidates,
)


class TestMergeAndRank:
    def test_dedup_across_directions_and_sources(self):
        result = merge_and_rank_candidates(
            knn_pairs=[("Alice", "Bob", 0.9), ("Bob", "Alice", 0.85)],
            doc_pairs=[("alice", "bob", 3)],
        )
        assert len(result) == 1
        pair = result[0]
        assert pair.knn_score == 0.9  # max across directions
        assert pair.shared_docs == 3

    def test_both_signals_outrank_single_signal(self):
        result = merge_and_rank_candidates(
            knn_pairs=[("A", "B", 0.9), ("C", "D", 0.9)],
            doc_pairs=[("A", "B", 4)],
        )
        assert {result[0].source, result[0].target} == {"A", "B"}
        assert result[0].score > result[1].score

    def test_self_pairs_dropped(self):
        result = merge_and_rank_candidates(
            knn_pairs=[("A", "a", 0.99)],
            doc_pairs=[("B", "B", 5)],
        )
        assert result == []

    def test_total_cap(self):
        knn = [(f"E{i}", f"F{i}", 0.9) for i in range(100)]
        result = merge_and_rank_candidates(knn, [], total_cap=10)
        assert len(result) == 10

    def test_per_entity_cap(self):
        # Hub entity appears in many high-score pairs; cap must limit it.
        knn = [("Hub", f"E{i}", 0.99 - i * 0.001) for i in range(20)]
        knn.append(("X", "Y", 0.5))
        result = merge_and_rank_candidates(knn, [], per_entity_cap=3)
        hub_pairs = [c for c in result if "Hub" in (c.source, c.target)]
        assert len(hub_pairs) == 3
        # Non-hub pair survives even though it ranks below the dropped hub pairs
        assert any({c.source, c.target} == {"X", "Y"} for c in result)

    def test_score_ordering(self):
        result = merge_and_rank_candidates(
            knn_pairs=[("A", "B", 0.7), ("C", "D", 0.95)],
            doc_pairs=[("E", "F", 2)],
        )
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)


class TestGrouping:
    def test_empty(self):
        assert group_pairs_for_verification([]) == []

    def test_chunk_sizes(self):
        pairs = [CandidatePair(source=f"A{i}", target=f"B{i}") for i in range(95)]
        groups = group_pairs_for_verification(pairs, pairs_per_call=40)
        assert [len(g) for g in groups] == [40, 40, 15]

    def test_shared_entities_cluster_in_same_group(self):
        # Pairs sharing "Alice" should be adjacent after name sort, so a small
        # group size keeps them together regardless of score interleaving.
        pairs = [
            CandidatePair(source="Zed", target="Yara", score=0.9),
            CandidatePair(source="Alice", target="Bob", score=0.5),
            CandidatePair(source="Mia", target="Ned", score=0.7),
            CandidatePair(source="Alice", target="Carol", score=0.1),
        ]
        groups = group_pairs_for_verification(pairs, pairs_per_call=2)
        assert groups[0] == [("Alice", "Bob"), ("Alice", "Carol")]

    def test_all_pairs_preserved(self):
        pairs = [CandidatePair(source=f"A{i}", target=f"B{i}") for i in range(7)]
        groups = group_pairs_for_verification(pairs, pairs_per_call=3)
        flattened = {p for g in groups for p in g}
        assert flattened == {(f"A{i}", f"B{i}") for i in range(7)}
