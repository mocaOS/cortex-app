"""Tests for suggest_duplicate_entities (the /deduplicate scan).

The matcher runs rapidfuzz in row blocks capped by a memory budget so peak
memory stays flat on large graphs. These tests lock:
- grouping semantics (typos, Person name-prefix gating, star clustering)
- block-boundary correctness: a tiny block budget must produce the exact
  same groups as one big block
- determinism across runs
- cancellation and progress reporting

The Neo4j driver is mocked; entity rows are fed straight into the scorer.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from app.services.neo4j_service import Neo4jService, DedupScanCancelled


def _make_service(rows):
    """Neo4jService whose driver returns the given entity rows."""
    svc = Neo4jService()
    driver = MagicMock()
    session = MagicMock()
    session.run.return_value = rows
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    svc._driver = driver
    return svc


def _row(name, type_="Concept", mentions=0, rels=0):
    return {
        "name": name,
        "type": type_,
        "description": "",
        "mention_count": mentions,
        "relationship_count": rels,
    }


def _group_sets(groups):
    return sorted(tuple(sorted(e["name"] for e in g["entities"])) for g in groups)


class TestGroupingSemantics:
    def test_typo_variants_group_with_most_connected_canonical(self):
        svc = _make_service([
            _row("Colborn Bell", "Person", mentions=10, rels=8),
            _row("Colborn Bel", "Person", mentions=1, rels=0),
            _row("Ethereum", "Technology"),
        ])
        groups = svc.suggest_duplicate_entities(threshold=0.85)
        assert _group_sets(groups) == [("Colborn Bel", "Colborn Bell")]
        assert groups[0]["suggested_canonical"] == "Colborn Bell"

    def test_person_short_name_matches_full_name_via_prefix(self):
        # ratio("colborn", "colborn bell") < 0.75 — only the Person-gated
        # partial_ratio path can produce this match.
        svc = _make_service([
            _row("Colborn", "Person"),
            _row("Colborn Bell", "Person", rels=5),
        ])
        groups = svc.suggest_duplicate_entities(threshold=0.75)
        assert _group_sets(groups) == [("Colborn", "Colborn Bell")]

    def test_shared_first_name_different_people_not_grouped(self):
        svc = _make_service([
            _row("David Young", "Person"),
            _row("David Hockney", "Person"),
        ])
        groups = svc.suggest_duplicate_entities(threshold=0.75)
        assert groups == []

    def test_single_word_person_never_becomes_hub(self):
        svc = _make_service([
            _row("Andrea", "Person"),
            _row("Andrea Rossi", "Person"),
            _row("Andrea Bianchi", "Person"),
        ])
        groups = svc.suggest_duplicate_entities(threshold=0.75)
        # "Andrea" must not pull both full names into one hub group
        assert all(len(g["entities"]) <= 2 for g in groups)
        andrea_groups = [
            g for g in groups if any(e["name"] == "Andrea" for e in g["entities"])
        ]
        assert len(andrea_groups) == 1

    def test_very_short_names_skipped(self):
        svc = _make_service([_row("AI"), _row("A1")])
        assert svc.suggest_duplicate_entities(threshold=0.75) == []

    def test_fewer_than_two_entities(self):
        svc = _make_service([_row("Solo")])
        assert svc.suggest_duplicate_entities() == []


class TestChunkingAndDeterminism:
    @staticmethod
    def _synthetic_rows(n=700):
        # Deterministic names with duplicates planted across block boundaries
        # (block floor is 256 rows, so n=700 spans 3 blocks).
        rows = []
        for i in range(n):
            rows.append(_row(f"Entity Number {i}", "Concept", mentions=i % 5, rels=i % 3))
        rows[10] = _row("Museum of Crypto Art", "Organization", rels=9)
        rows[300] = _row("Museum of Crytpo Art", "Organization")   # typo, block 2
        rows[650] = _row("Museum of Crypto Artt", "Organization")  # typo, block 3
        rows[20] = _row("Colborn Bell", "Person", rels=7)
        rows[550] = _row("Colborn", "Person")
        return rows

    def test_tiny_blocks_match_single_block_output(self, monkeypatch):
        rows = self._synthetic_rows()
        single = _make_service(rows).suggest_duplicate_entities(threshold=0.8, limit=500)

        monkeypatch.setattr(
            "app.services.neo4j_service._DEDUP_BLOCK_BUDGET_BYTES", 1
        )
        chunked = _make_service(rows).suggest_duplicate_entities(threshold=0.8, limit=500)

        assert _group_sets(single) == _group_sets(chunked)
        assert [g["suggested_canonical"] for g in single] == [
            g["suggested_canonical"] for g in chunked
        ]
        # The planted duplicates were actually found
        found = _group_sets(single)
        assert ("Museum of Crypto Art", "Museum of Crypto Artt", "Museum of Crytpo Art") in found
        assert ("Colborn", "Colborn Bell") in found

    def test_repeat_runs_identical(self):
        rows = self._synthetic_rows()
        svc = _make_service(rows)
        first = svc.suggest_duplicate_entities(threshold=0.8, limit=500)
        second = svc.suggest_duplicate_entities(threshold=0.8, limit=500)
        assert first == second


class TestCancellationAndProgress:
    def test_pre_set_cancel_event_aborts(self):
        svc = _make_service([_row("Alpha One"), _row("Alpha Two")])
        event = threading.Event()
        event.set()
        with pytest.raises(DedupScanCancelled):
            svc.suggest_duplicate_entities(threshold=0.75, cancel_event=event)

    def test_progress_reaches_total(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.neo4j_service._DEDUP_BLOCK_BUDGET_BYTES", 1
        )
        rows = TestChunkingAndDeterminism._synthetic_rows()
        svc = _make_service(rows)
        ticks = []
        svc.suggest_duplicate_entities(
            threshold=0.8, progress_cb=lambda done, total: ticks.append((done, total))
        )
        assert len(ticks) > 1
        dones = [d for d, _ in ticks]
        assert dones == sorted(dones)
        assert ticks[-1][0] == ticks[-1][1]
