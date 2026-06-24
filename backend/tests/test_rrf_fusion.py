"""Unit tests for Reciprocal Rank Fusion (hybrid search ranking core).

`Neo4jService._reciprocal_rank_fusion` merges multiple ranked result lists into
one weighted ranking. It is pure (no driver use), so we instantiate the service
via object.__new__ to avoid opening a Neo4j connection. Hybrid search was
previously only manually exercised; this pins the fusion math + tie handling.
"""

from __future__ import annotations

import pytest

from app.services.neo4j_service import Neo4jService


@pytest.fixture
def svc():
    # No __init__ -> no DB connection; the method uses no instance state.
    return object.__new__(Neo4jService)


def _r(cid, **extra):
    return {"chunk_id": cid, **extra}


def test_rrf_combines_overlapping_chunk_to_top(svc):
    # c1 appears in both lists -> accumulates score from both -> ranks first.
    vector = [_r("c1"), _r("c2")]
    keyword = [_r("c3"), _r("c1")]
    out = svc._reciprocal_rank_fusion([vector, keyword], [0.6, 0.4])
    assert out[0]["chunk_id"] == "c1"
    assert {r["chunk_id"] for r in out} == {"c1", "c2", "c3"}


def test_rrf_sets_score_fields_and_orders_desc(svc):
    out = svc._reciprocal_rank_fusion([[_r("a"), _r("b"), _r("c")]], [1.0])
    assert [r["chunk_id"] for r in out] == ["a", "b", "c"]  # rank order preserved
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True)
    assert all(r["score"] == r["rrf_score"] for r in out)


def test_rrf_weights_matter(svc):
    # Each list's rank-0 item; the higher-weighted list's item wins.
    out = svc._reciprocal_rank_fusion([[_r("hi")], [_r("lo")]], [0.9, 0.1])
    assert out[0]["chunk_id"] == "hi"


def test_rrf_skips_blank_chunk_ids(svc):
    out = svc._reciprocal_rank_fusion([[_r(""), _r("real")]], [1.0])
    assert [r["chunk_id"] for r in out] == ["real"]


def test_rrf_score_formula_default_k(svc):
    # single list, single item at rank 0: weight / (k + 0 + 1) with k=60
    out = svc._reciprocal_rank_fusion([[_r("x")]], [1.0])
    assert out[0]["score"] == pytest.approx(1.0 / 61)


def test_rrf_empty_input(svc):
    assert svc._reciprocal_rank_fusion([], []) == []
