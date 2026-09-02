"""Unit tests for the retrieval-layer helpers added with the agentic-retrieval
recall fixes (hermetic — the Neo4j driver is replaced by a scripted fake):

- lucene_or_terms: parse-safe keyword queries (the fulltext leg used to drop
  silently on `/`, `:`, unbalanced parens, stray operators)
- scoped_ann_k: vector-index over-fetch when a collection/filter scope applies
- _per_query_candidate_k: rerank pool sized ~2× what rerank keeps
- resolve_query_entity_names: exact / case-insensitive+alias / fulltext-on-name
  passes, each only for still-unresolved names; never raises
- vector_search / fulltext_search / hybrid_search_rrf wiring of the above
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.neo4j_service import (
    Neo4jService,
    lucene_or_terms,
    query_entity_name_tokens,
    scoped_ann_k,
)
from app.services.researcher_agent import _per_query_candidate_k


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("x402 payment flow", "x402 OR payment OR flow"),
        ("what is docs/setup.md?", "what OR is OR docs OR setup OR md"),
        ("Note: (unbalanced", "Note OR unbalanced"),
        ("-negated NOT this AND that OR", "negated OR this OR that"),
        ("Müller straße", "Müller OR straße"),
        ("", None),
        (None, None),
        ("/// :: ()", None),
        ("OR AND NOT", None),
    ],
)
def test_lucene_or_terms(text, expected):
    assert lucene_or_terms(text) == expected


def test_lucene_or_terms_caps_term_count():
    q = lucene_or_terms(" ".join(f"t{i}" for i in range(100)), max_terms=4)
    assert q == "t0 OR t1 OR t2 OR t3"


def test_query_entity_name_tokens():
    assert query_entity_name_tokens("Polygon Network") == {"polygon", "network"}
    assert query_entity_name_tokens("") == set()
    assert query_entity_name_tokens(None) == set()


def test_scoped_ann_k():
    assert scoped_ann_k(15, scoped=True, factor=1) == 15  # VECTOR_SCOPED_OVERFETCH=1 → off
    assert scoped_ann_k(15, scoped=True, factor=3) == 45
    assert scoped_ann_k(15, scoped=False) == 15
    assert scoped_ann_k(15, scoped=True) == 150
    assert scoped_ann_k(30, scoped=True) == 200  # capped
    assert scoped_ann_k(500, scoped=True) == 500  # never below top_k


def test_per_query_candidate_k():
    assert _per_query_candidate_k(15, 3) == 10  # pool 30
    assert _per_query_candidate_k(15, 2) == 12  # capped
    assert _per_query_candidate_k(15, 1) == 12
    assert _per_query_candidate_k(5, 3) == 5  # floor keeps the legacy depth
    assert _per_query_candidate_k(15, 0) == 12  # defensive: no queries → n=1


# ---------------------------------------------------------------------------
# Scripted Neo4j fake
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []  # (query, params)

    def run(self, query, **params):
        self.calls.append((query, params))
        return list(self._handler(query, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, handler):
        self.sessions = []
        self._handler = handler

    def session(self):
        s = _FakeSession(self._handler)
        self.sessions.append(s)
        return s

    @property
    def calls(self):
        return [c for s in self.sessions for c in s.calls]


def _service(handler, **overrides):
    svc = Neo4jService.__new__(Neo4jService)
    svc._driver = _FakeDriver(handler)
    svc._vector_search_failures = 0
    svc._vector_search_failure_warned = False
    svc._query_entity_cache = {}
    cfg = {
        "enable_query_entity_resolution": True,
        "vector_scoped_overfetch": 10,
        "enable_parallel_search_legs": True,
        "enable_ranked_graph_traversal": True,
    }
    cfg.update(overrides)
    svc.settings = SimpleNamespace(**cfg)
    return svc


# ---------------------------------------------------------------------------
# resolve_query_entity_names
# ---------------------------------------------------------------------------


def _graph_handler(entities):
    """entities: list of {"name": str, "aliases": [..]} — emulates the 3 passes."""

    def handler(query, params):
        if "MATCH (e:Entity {name: q})" in query:
            names = {e["name"] for e in entities}
            return [{"q": q, "name": q} for q in params["names"] if q in names]
        if "toLower(e.name) IN $lowers" in query:
            lowers = set(params["lowers"])
            out = []
            for e in entities:
                al = [a.lower() for a in e.get("aliases", [])]
                if e["name"].lower() in lowers or any(a in lowers for a in al):
                    out.append(
                        {"name": e["name"], "name_lower": e["name"].lower(), "alias_lowers": al}
                    )
            return out
        if "entity_name_fulltext" in query:
            # Emulate a name-field OR query: any shared token is a hit.
            terms = {
                t.lower()
                for t in params["q"][len("name:(") : -1].split(" OR ")
            }
            return [
                {"name": e["name"]}
                for e in entities
                if terms & query_entity_name_tokens(e["name"])
            ]
        raise AssertionError(f"unexpected query: {query}")

    return handler


def test_resolve_exact_hit_skips_later_passes():
    svc = _service(_graph_handler([{"name": "Polygon"}]))
    assert svc.resolve_query_entity_names(["Polygon"]) == ["Polygon"]
    # Only pass 1 ran (everything resolved).
    assert len(svc._driver.calls) == 1


def test_resolve_case_insensitive_and_alias():
    svc = _service(
        _graph_handler(
            [
                {"name": "Polygon Network", "aliases": ["Matic", "polygon"]},
                {"name": "Ethereum"},
            ]
        )
    )
    out = svc.resolve_query_entity_names(["ethereum", "matic"])
    # Raw names kept (harmless), canonical names appended.
    assert out == ["ethereum", "Ethereum", "matic", "Polygon Network"]
    assert len(svc._driver.calls) == 2  # pass 1 + one scan, no fulltext


def test_resolve_fulltext_requires_token_containment():
    svc = _service(
        _graph_handler(
            [
                {"name": "Polygon Network"},  # contains all tokens of "Polygon" → accept
                {"name": "Ethereum"},  # does NOT contain "foundation" → reject
                {"name": "Bitcoin Cash"},
            ]
        )
    )
    out = svc.resolve_query_entity_names(["Polygon", "Ethereum Foundation"])
    assert "Polygon Network" in out
    assert "Ethereum" not in out
    assert "Bitcoin Cash" not in out
    assert out[0] == "Polygon"  # input order preserved


def test_resolve_dedups_caps_and_ignores_junk():
    svc = _service(_graph_handler([{"name": "A"}]))
    out = svc.resolve_query_entity_names(["A", " A ", "", None, 42])  # type: ignore[list-item]
    assert out == ["A"]
    assert svc.resolve_query_entity_names([]) == []
    assert svc.resolve_query_entity_names(None) == []  # type: ignore[arg-type]


def test_resolve_never_raises_falls_back_to_raw():
    def boom(query, params):
        raise RuntimeError("bolt down")

    svc = _service(boom)
    assert svc.resolve_query_entity_names(["Polygon", "x"]) == ["Polygon", "x"]


# ---------------------------------------------------------------------------
# vector_search / fulltext_search wiring
# ---------------------------------------------------------------------------


def test_vector_search_overfetches_only_when_scoped():
    svc = _service(lambda q, p: [])
    svc.vector_search([0.1], top_k=15)
    q, p = svc._driver.calls[-1]
    assert "$ann_k" in q and p["ann_k"] == 15 and p["top_k"] == 15

    svc.vector_search([0.1], top_k=15, collection_id="col")
    _, p = svc._driver.calls[-1]
    assert p["ann_k"] == 150 and p["top_k"] == 15

    svc.vector_search([0.1], top_k=15, allowed_collection_ids=["a", "b"])
    _, p = svc._driver.calls[-1]
    assert p["ann_k"] == 150

    svc.vector_search([0.1], top_k=15, filters={"file_type": "pdf"})
    _, p = svc._driver.calls[-1]
    assert p["ann_k"] == 150


def test_fulltext_search_sends_sanitized_query_and_short_circuits_empty():
    svc = _service(lambda q, p: [])
    svc.fulltext_search("what about docs/setup.md: (x402?)", top_k=9)
    _, p = svc._driver.calls[-1]
    assert p["search_text"] == "what OR about OR docs OR setup OR md OR x402"

    n_before = len(svc._driver.calls)
    assert svc.fulltext_search("/// ::", top_k=9) == []
    assert len(svc._driver.calls) == n_before  # no round-trip for an empty query


def test_community_search_sends_sanitized_query():
    svc = _service(lambda q, p: [])
    svc.search_communities_by_content("themes: governance/voting", limit=3)
    _, p = svc._driver.calls[-1]
    assert p["search_query"] == "themes OR governance OR voting"


# ---------------------------------------------------------------------------
# hybrid_search_rrf: parallel legs + resolution feeding traversal
# ---------------------------------------------------------------------------


def test_hybrid_search_rrf_runs_legs_and_resolves_entities_for_traversal():
    svc = _service(lambda q, p: [])
    seen = {}

    def vector_search(embedding, top_k, **kw):
        seen["vector"] = (top_k, kw)
        return [{"chunk_id": "v1", "content": "a", "score": 0.9}]

    def fulltext_search(text, top_k, **kw):
        seen["keyword"] = (top_k, kw)
        return [{"chunk_id": "v1", "content": "a", "score": 3.0}, {"chunk_id": "k2", "content": "b", "score": 1.0}]

    def resolve(names, max_per_name=2):
        seen["resolve_in"] = list(names)
        return list(names) + ["Polygon Network"]

    def traverse(names, max_hops, **kw):
        seen["traverse_in"] = (list(names), kw)
        return {
            "entities": [{"name": "Polygon Network"}],
            "relationships": [],
            "chunks": [{"chunk_id": "g3", "content": "c", "document_id": "d", "filename": "f"}],
        }

    svc.vector_search = vector_search
    svc.fulltext_search = fulltext_search
    svc.resolve_query_entity_names = resolve
    svc.traverse_from_entities = traverse
    svc.traverse_for_retrieval = traverse  # default flag routes here

    out = svc.hybrid_search_rrf(
        query_embedding=[0.1], query_text="polygon", entity_names=["polygon"],
        top_k=5, collection_id="col",
    )

    assert seen["vector"][0] == 15 and seen["vector"][1]["collection_id"] == "col"
    assert seen["keyword"][0] == 15
    assert seen["resolve_in"] == ["polygon"]
    assert seen["traverse_in"][0] == ["polygon", "Polygon Network"]
    assert seen["traverse_in"][1]["collection_id"] == "col"

    ids = [r["chunk_id"] for r in out["results"]]
    assert ids[0] == "v1"  # in two legs → highest fused score
    assert set(ids) == {"v1", "k2", "g3"}
    assert out["vector_count"] == 1 and out["keyword_count"] == 2 and out["graph_chunk_count"] == 1
    assert out["query_entity_count"] == 1 and out["resolved_entity_count"] == 2


def test_hybrid_search_rrf_propagates_leg_exception():
    svc = _service(lambda q, p: [])

    def vector_search(*a, **kw):
        raise RuntimeError("vector index missing")

    svc.vector_search = vector_search
    svc.fulltext_search = lambda *a, **kw: []
    svc.resolve_query_entity_names = lambda names, max_per_name=2: list(names)
    svc.traverse_from_entities = lambda *a, **kw: {"entities": [], "relationships": [], "chunks": []}

    with pytest.raises(RuntimeError, match="vector index missing"):
        svc.hybrid_search_rrf([0.1], "q", [], top_k=5)


# ---------------------------------------------------------------------------
# Revert switches + resolution cache
# ---------------------------------------------------------------------------


def test_resolution_cache_reuses_result_within_ttl():
    svc = _service(_graph_handler([{"name": "Ethereum"}]))
    first = svc.resolve_query_entity_names(["ethereum"])
    n_calls = len(svc._driver.calls)
    assert n_calls > 0
    second = svc.resolve_query_entity_names(["ethereum"])
    assert second == first == ["ethereum", "Ethereum"]
    assert len(svc._driver.calls) == n_calls, "second call must be served from cache"
    assert second is not first  # caller gets a copy, cache stays immutable


def test_overfetch_disabled_via_setting():
    svc = _service(lambda q, p: [], vector_scoped_overfetch=1)
    svc.vector_search([0.1], top_k=15, collection_id="col")
    _, p = svc._driver.calls[-1]
    assert p["ann_k"] == 15


def _wire_legs(svc, seen):
    svc.vector_search = lambda e, k, **kw: seen.setdefault("order", []).append("vector") or []
    svc.fulltext_search = lambda t, k, **kw: seen.setdefault("order", []).append("keyword") or []
    svc.resolve_query_entity_names = lambda names, max_per_name=2: (
        seen.setdefault("order", []).append("resolve") or list(names) + ["Canonical"]
    )
    svc.traverse_from_entities = lambda names, hops, **kw: (
        seen.__setitem__("traverse_in", list(names))
        or {"entities": [], "relationships": [], "chunks": []}
    )
    svc.traverse_for_retrieval = svc.traverse_from_entities


def test_resolution_disabled_passes_raw_names_to_traversal():
    seen = {}
    svc = _service(lambda q, p: [], enable_query_entity_resolution=False)
    _wire_legs(svc, seen)
    out = svc.hybrid_search_rrf([0.1], "q", ["polygon"], top_k=5)
    assert seen["traverse_in"] == ["polygon"]
    assert "resolve" not in seen.get("order", [])
    assert out["resolved_entity_count"] == 1  # raw names, unresolved


def test_sequential_legs_when_parallel_disabled():
    seen = {}
    svc = _service(lambda q, p: [], enable_parallel_search_legs=False)
    _wire_legs(svc, seen)
    svc.hybrid_search_rrf([0.1], "q", ["polygon"], top_k=5)
    assert seen["order"] == ["vector", "keyword", "resolve"]
    assert seen["traverse_in"] == ["polygon", "Canonical"]


# ---------------------------------------------------------------------------
# traverse_for_retrieval (ranked graph leg) + hybrid wiring
# ---------------------------------------------------------------------------


def _traversal_handler(query, params):
    if "CALL {" in query and "n1 AS related" in query:
        # Neighbor rings: one row per start entity.
        assert params["limit2"] == max(1, params["limit"] // 2)
        return [
            {
                "start_name": n, "start_type": "Person", "start_description": f"desc {n}",
                "related": [
                    {"name": "Odrade", "type": "Person", "description": "d", "hop": 1},
                    {"name": "Far", "type": "Concept", "description": "d", "hop": 2},
                ],
            }
            for n in params["names"] if n != "Ghost"
        ]
    if "rank_score" in query:
        # Ranked chunks, already ordered by the (fake) database.
        assert "d.processing_status = 'completed'" in query
        assert set(params["start_names"]) <= set(params["all_names"])
        return [
            {"chunk_id": "c-both", "content": "x", "chunk_index": 42, "document_id": "d1", "filename": "f", "score": 6},
            {"chunk_id": "c-one", "content": "y", "chunk_index": 7, "document_id": "d1", "filename": "f", "score": 3},
        ][: params["chunk_limit"]]
    if "relationship_type" in query:
        return [{"source": "Murbella", "target": "Odrade", "relationship_type": "RELATED_TO",
                 "description": "d", "sub_type": "MENTORED_BY"}]
    raise AssertionError(f"unexpected query: {query}")


def test_traverse_for_retrieval_shape_and_ordering():
    svc = _service(_traversal_handler)
    ctx = svc.traverse_for_retrieval(["Murbella", "Duncan Idaho", "Ghost"], max_hops=2, chunk_limit=20)
    names = [e["name"] for e in ctx["entities"]]
    # Starts first (Ghost has no node → dropped), then neighbors nearest ring first, deduped.
    assert names == ["Murbella", "Duncan Idaho", "Odrade", "Far"]
    assert [c["chunk_id"] for c in ctx["chunks"]] == ["c-both", "c-one"]
    assert ctx["chunks"][0]["chunk_index"] == 42 and ctx["chunks"][0]["score"] == 6
    assert ctx["relationships"] == [
        {"source": "Murbella", "target": "Odrade", "type": "MENTORED_BY", "description": "d"}
    ]
    # Scope + limits reach the chunk query.
    _, p = [c for c in svc._driver.calls if "rank_score" in c[0]][0]
    assert p["chunk_limit"] == 20 and p["start_names"] == ["Murbella", "Duncan Idaho"]


def test_traverse_for_retrieval_scoped_and_empty():
    svc = _service(_traversal_handler)
    svc.traverse_for_retrieval(["Murbella"], collection_id="col-1")
    q, p = [c for c in svc._driver.calls if "rank_score" in c[0]][0]
    assert "Collection {id: $collection_id}" in q and p["collection_id"] == "col-1"

    svc2 = _service(_traversal_handler)
    assert svc2.traverse_for_retrieval([]) == {"entities": [], "relationships": [], "chunks": []}
    assert svc2._driver.calls == []  # no round-trip for nothing
    assert svc2.traverse_for_retrieval(["Ghost"]) == {"entities": [], "relationships": [], "chunks": []}
    assert len(svc2._driver.calls) == 1  # neighbor query only; no chunk/relationship queries


def test_hybrid_uses_ranked_traversal_with_chunk_limit_and_scores():
    svc = _service(lambda q, p: [])
    seen = {}
    svc.vector_search = lambda *a, **kw: []
    svc.fulltext_search = lambda *a, **kw: []
    svc.resolve_query_entity_names = lambda names, max_per_name=2: list(names)

    def ranked(names, hops, **kw):
        seen["ranked"] = (list(names), hops, kw)
        return {"entities": [], "relationships": [], "chunks": [
            {"chunk_id": "g1", "content": "a", "chunk_index": 42, "document_id": "d", "filename": "f", "score": 6},
            {"chunk_id": "g2", "content": "b", "chunk_index": 7, "document_id": "d", "filename": "f", "score": 3},
        ]}

    svc.traverse_for_retrieval = ranked
    svc.traverse_from_entities = lambda *a, **kw: seen.setdefault("legacy", True) and {"entities": [], "relationships": [], "chunks": []}

    out = svc.hybrid_search_rrf([0.1], "q", ["Murbella"], top_k=10, collection_id="col")
    assert seen["ranked"][0] == ["Murbella"] and seen["ranked"][1] == 2
    assert seen["ranked"][2]["chunk_limit"] == 20 and seen["ranked"][2]["collection_id"] == "col"
    assert "legacy" not in seen
    g1, g2 = out["results"][0], out["results"][1]
    assert (g1["chunk_id"], g1["chunk_index"], g1["rrf_score"] > g2["rrf_score"]) == ("g1", 42, True)


def test_hybrid_falls_back_to_legacy_traversal_when_disabled():
    svc = _service(lambda q, p: [], enable_ranked_graph_traversal=False)
    seen = {}
    svc.vector_search = lambda *a, **kw: []
    svc.fulltext_search = lambda *a, **kw: []
    svc.resolve_query_entity_names = lambda names, max_per_name=2: list(names)
    svc.traverse_for_retrieval = lambda *a, **kw: seen.setdefault("ranked", True) and {}
    svc.traverse_from_entities = lambda names, hops, **kw: (
        seen.__setitem__("legacy", list(names)) or {"entities": [], "relationships": [], "chunks": []}
    )
    svc.hybrid_search_rrf([0.1], "q", ["Murbella"], top_k=5)
    assert seen == {"legacy": ["Murbella"]}
