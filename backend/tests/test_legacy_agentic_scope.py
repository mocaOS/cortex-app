"""Legacy agentic_rag_stream scope gate (ENABLE_AGENT_RESEARCH=false).

Real-auth HTTP streaming entry (`POST /api/ask/stream`, `POST
/api/ask/stream/thinking`, depth "deep" / legacy `use_agentic`) → real
`QueryProcessor.agentic_rag_stream` → real `graph_search_async` → real
Neo4jService query builders over a recording transport with canned LLM,
extractor and embedding controls.

Oracle (see output/legacy-agentic-scope-20261004/evaluation/GATE-ORACLE.md):
the legacy streaming branch drops the key's multi/empty collection allowlist
before `agentic_rag_stream`, which also calls `search_communities_by_content`
and `get_community` with no scope at all. Scalar/allowlist precedence follows
main.py's scope resolution; community scope shape follows the researcher's
`_tool_scope` precedent (`[collection_id] if collection_id else
allowed_collection_ids`). `None` stays unrestricted; `[]` must bind as a real
empty filter.

Evidence boundary: recording-driver assembly only. The store never executes;
this proves neither returned-data isolation, live Cypher, complete graph
entity/relationship metadata confinement, nor production parity. Direct
`_agentic_rag_query` is not REST evidence — the non-streaming agentic 400
control pins that boundary.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services.neo4j_service import Neo4jService, lucene_or_terms

QUESTION = "fixture governance question"
SUB_QUESTIONS = ["sub-alpha", "sub-beta"]
CANNED_ENTITY = "Fixture Entity"
EMBEDDINGS = {"sub-alpha": [0.125, -0.25], "sub-beta": [0.375, -0.5]}
CANNED_ANSWER_TOKENS = ("Canned answer alpha. ", "Canned answer beta.")
COMMUNITY_ID = 7
ENDPOINTS = ["/api/ask/stream", "/api/ask/stream/thinking"]

SCOPE_CASES = {
    "unrestricted": {"key": "key-all"},
    "requested-scalar": {"key": "key-all", "request_collection": "collection-requested"},
    "single": {"key": "key-single"},
    "multi": {"key": "key-multi"},
    "empty": {"key": "key-empty"},
    "scalar-precedence": {"key": "key-precedence", "request_collection": "collection-requested"},
}

_KEY_BODIES = {
    "key-all": "a" * 64,
    "key-single": "b" * 64,
    "key-multi": "c" * 64,
    "key-empty": "d" * 64,
    "key-precedence": "e" * 64,
}
_KEY_ALLOWED = {
    "key-all": None,
    "key-single": ["col-one"],
    "key-multi": ["grant-alpha", "grant-beta"],
    "key-empty": [],
    "key-precedence": ["collection-requested", "key-grant-1"],
}

SCALAR_CLAUSE = "MATCH (col:Collection {id: $collection_id})-[:CONTAINS]->(d)"
IN_CLAUSE = (
    "MATCH (col:Collection)-[:CONTAINS]->(d) WHERE col.id IN $allowed_collection_ids"
)


# ---------------------------------------------------------------------------
# Scripted principals — validated through the REAL auth dependency chain
# ---------------------------------------------------------------------------


def _minted(key_id: str) -> str:
    return "cortex_" + _KEY_BODIES[key_id]


def _stored_key(key_id: str) -> dict:
    return {
        "id": key_id,
        "name": key_id,
        "key_hash": hashlib.sha256(_minted(key_id).encode()).hexdigest(),
        "permissions": ["read"],
        "collection_scope": "all" if _KEY_ALLOWED[key_id] is None else "restricted",
        "allowed_collections": list(_KEY_ALLOWED[key_id] or []),
        "last_used_at": None,
    }


def _header(key_id: str) -> dict:
    return {"X-API-Key": _minted(key_id)}


def _install_key_registry(mock_neo4j) -> None:
    def get_by_prefix(prefix: str):
        return [
            _stored_key(kid) for kid in _KEY_BODIES
            if _minted(kid)[:12] == prefix
        ]

    mock_neo4j.get_api_key_by_prefix.side_effect = get_by_prefix


# ---------------------------------------------------------------------------
# Oracle helpers — derived expectations (independent of the product code)
# ---------------------------------------------------------------------------


def _effective_graph_scope(case: str):
    spec = SCOPE_CASES[case]
    request_collection = spec.get("request_collection")
    if request_collection:
        return request_collection, None
    allowed = _KEY_ALLOWED[spec["key"]]
    if allowed is None:
        return None, None
    if len(allowed) == 1:
        return allowed[0], None
    return None, list(allowed)


def _expected_community_scope(case: str):
    scalar, allowlist = _effective_graph_scope(case)
    return [scalar] if scalar else allowlist


def _graph_clause_expectations(case: str) -> dict:
    scalar, allowlist = _effective_graph_scope(case)
    return {
        "scalar_clause": bool(scalar),
        "in_clause": allowlist is not None,
        "collection_id": scalar,
        "allowed_collection_ids": allowlist,
    }


# ---------------------------------------------------------------------------
# Recording transport (real builders, canned rows, no store execution)
# ---------------------------------------------------------------------------


def _classify(query: str) -> str:
    if "db.index.vector.queryNodes('chunk_embedding'" in query:
        return "vector"
    if "db.index.fulltext.queryNodes('chunk_content'" in query:
        return "keyword"
    if "community_summary_fulltext" in query:
        return "community_search"
    if "MATCH (com:Community {id: $id})" in query:
        return "community_get"
    if "MATCH (e1:Entity {community_id: $id})" in query:
        return "community_rels"
    if "MATCH (e:Entity {name: q})" in query:
        return "resolve"
    if "n1 AS related" in query:
        return "rings"
    if "rank_score" in query:
        return "ranked_chunks"
    if "relationship_type" in query:
        return "rels"
    if "start.name IN $entity_names" in query:
        return "legacy_chunks"
    return "unknown"


class _RecordingResult(list):
    def single(self):
        return self[0] if self else None


class _RecordingSession:
    def __init__(self, handler, calls):
        self._handler = handler
        self._calls = calls

    def run(self, query, **params):
        self._calls.append((_classify(query), query, params))
        return _RecordingResult(self._handler(query, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingDriver:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def session(self):
        return _RecordingSession(self._handler, self.calls)


_SUBQ_BY_EMBED = {tuple(v): k for k, v in EMBEDDINGS.items()}


def _store_handler(query, params):
    marker = _classify(query)
    if marker == "vector":
        subq = _SUBQ_BY_EMBED[tuple(params["embedding"])]
        return [{
            "document_id": "doc-" + subq,
            "filename": subq + ".pdf",
            "chunk_id": "vec-" + subq,
            "content": "vector content " + subq,
            "chunk_index": 0,
            "score": 0.9,
        }]
    if marker == "keyword":
        return [{
            "document_id": "doc-keyword",
            "filename": "keyword.pdf",
            "chunk_id": "kw-" + hashlib.md5(params["search_text"].encode()).hexdigest()[:8],
            "content": "keyword content",
            "chunk_index": 1,
            "score": 3.0,
        }]
    if marker == "resolve":
        return [
            {"q": q, "name": q} for q in params["names"] if q == CANNED_ENTITY
        ]
    if marker == "rings":
        return [{
            "start_name": CANNED_ENTITY,
            "start_type": "Person",
            "start_description": "fixture entity",
            "related": [],
        }]
    if marker == "ranked_chunks":
        return [{
            "chunk_id": "graph-ranked-1",
            "content": "ranked graph chunk",
            "chunk_index": 2,
            "document_id": "doc-graph",
            "filename": "graph.pdf",
            "score": 6,
        }]
    if marker == "rels":
        return []
    if marker == "legacy_chunks":
        return [{
            "start_entity": CANNED_ENTITY,
            "start_type": "Person",
            "start_description": "fixture entity",
            "related": [],
            "chunks": [{
                "chunk_id": "graph-legacy-1",
                "content": "legacy graph chunk",
                "document_id": "doc-graph",
                "filename": "graph.pdf",
            }],
        }]
    if marker == "community_search":
        return [{
            "id": COMMUNITY_ID,
            "name": "Fixture Community",
            "summary": "fixture community summary",
            "entity_count": 2,
            "score": 1.5,
        }]
    if marker == "community_get":
        return [{
            "id": COMMUNITY_ID,
            "name": "Fixture Community",
            "summary": "fixture community summary",
            "entity_count": 2,
            "entities": [],
        }]
    if marker == "community_rels":
        return []
    raise AssertionError(f"unexpected store query shape: {query[:160]}")


# ---------------------------------------------------------------------------
# Canned LLM / extractor / embedding controls (no real LLM call possible)
# ---------------------------------------------------------------------------


def _canned_embed(text):
    assert text in EMBEDDINGS, f"unexpected embedding query: {text!r}"
    return list(EMBEDDINGS[text])


async def _canned_extract(query):
    assert query in SUB_QUESTIONS, f"unexpected extraction query: {query!r}"
    return [CANNED_ENTITY]


def _canned_llm_factory(llm_calls):
    decompose = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"sub_questions": ["sub-alpha", "sub-beta"]}'))]
    )

    class _Client:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            llm_calls.append({"stream": bool(kwargs.get("stream"))})
            if kwargs.get("stream"):
                async def _tokens():
                    for tok in CANNED_ANSWER_TOKENS:
                        yield SimpleNamespace(choices=[SimpleNamespace(
                            delta=SimpleNamespace(content=tok))])
                return _tokens()
            return decompose

    return _Client


# ---------------------------------------------------------------------------
# Fixture: real-auth client + real QueryProcessor over the recording service
# ---------------------------------------------------------------------------


@pytest.fixture
def legacy_agentic_env(mock_neo4j, _isolate_env, monkeypatch):
    from app.main import app
    from app.services import document_processor as dp
    from app.services.document_processor import QueryProcessor

    _install_key_registry(mock_neo4j)
    driver = _RecordingDriver(_store_handler)
    svc = Neo4jService.__new__(Neo4jService)
    svc._driver = driver
    svc._vector_search_failures = 0
    svc._vector_search_failure_warned = False
    svc._query_entity_cache = {}
    svc.settings = SimpleNamespace(
        enable_query_entity_resolution=True,
        vector_scoped_overfetch=10,
        # Deterministic store-call order for grouping (adapter constraint).
        enable_parallel_search_legs=False,
        enable_ranked_graph_traversal=True,
    )

    qp = QueryProcessor.__new__(QueryProcessor)
    qp.settings = SimpleNamespace(
        prompt_security=True,
        prompt_guard_service_url="",
        prompt_guard_local=False,
        prompt_guard=False,
        enable_community_detection=True,
        enable_graph_summarization=True,
        max_agentic_steps=3,
        max_conversation_history=6,
        enable_reranking=False,
        enable_hybrid_search=True,
        vector_weight=0.5,
        keyword_weight=0.3,
        graph_weight=0.2,
    )
    qp.neo4j = svc
    qp.graph_extractor = SimpleNamespace(
        is_available=True,
        extract_entities_from_query_async=_canned_extract,
    )
    qp.embed_query = _canned_embed

    llm_calls = []
    monkeypatch.setattr("app.main.get_query_processor", lambda: qp, raising=True)
    monkeypatch.setattr(
        "app.services.document_processor.get_query_processor", lambda: qp,
        raising=True,
    )
    monkeypatch.setattr(dp, "_query_processor", qp, raising=False)
    fake_doc = MagicMock()
    monkeypatch.setattr("app.main.get_document_processor", lambda: fake_doc, raising=True)
    monkeypatch.setattr(
        "app.services.document_processor.get_document_processor",
        lambda: fake_doc, raising=True,
    )
    monkeypatch.setattr(dp, "_document_processor", fake_doc, raising=False)
    monkeypatch.setattr(
        dp, "make_async_openai_client",
        lambda **kwargs: _canned_llm_factory(llm_calls)(), raising=True,
    )
    monkeypatch.setattr(_isolate_env, "enable_agent_research", False, raising=True)
    monkeypatch.setattr(_isolate_env, "enable_agentic_rag", True, raising=True)
    monkeypatch.setattr(_isolate_env, "enable_reranking", False, raising=True)
    monkeypatch.setattr(
        _isolate_env, "openai_api_key", "test-key-legacy-agentic-scope",
        raising=True,
    )

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, driver=driver, svc=svc, qp=qp,
            settings=_isolate_env, llm_calls=llm_calls, neo4j=mock_neo4j,
        )
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Stream driver + observation helpers
# ---------------------------------------------------------------------------


def _parse_sse_frames(text):
    frames = []
    for line in text.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[len("data: "):]))
    return frames


def _frames_of_type(frames, type_):
    return [f for f in frames if f.get("type") == type_]


def _single_frame(frames, type_):
    got = _frames_of_type(frames, type_)
    assert len(got) == 1, f"expected exactly one {type_} frame, got {len(got)}"
    return got[0]


def _run_stream(env, endpoint, case, entry="depth"):
    payload = {"question": QUESTION, "top_k": 5, "max_hops": 2}
    if entry == "depth":
        payload["depth"] = "deep"
    else:
        payload["use_agentic"] = True
    request_collection = SCOPE_CASES[case].get("request_collection")
    if request_collection:
        payload["collection_id"] = request_collection
    r = env.client.post(endpoint, json=payload, headers=_header(SCOPE_CASES[case]["key"]))
    assert r.status_code == 200, (r.status_code, r.text[:500])
    frames = _parse_sse_frames(r.text)
    errors = [f for f in frames if "error" in f]
    assert not errors, (
        "unexpected SSE error frame (machinery or product failure): "
        f"{errors} raw={r.text[:400]}"
    )
    return frames


def _segment_calls(calls):
    preamble, groups, tail = [], [], []
    current = None
    for marker, query, params in calls:
        if marker in ("community_get", "community_rels"):
            tail.append((marker, query, params))
        elif marker == "vector":
            current = {"vector": (query, params)}
            groups.append(current)
        elif current is None:
            preamble.append((marker, query, params))
        else:
            current[marker] = (query, params)
    return preamble, groups, tail


def _subquery_observations(env):
    preamble, groups, tail = _segment_calls(env.driver.calls)
    assert len(groups) == len(SUB_QUESTIONS), (
        f"expected one store-search group per canned sub-question "
        f"({len(SUB_QUESTIONS)}), got {len(groups)}; call markers: "
        f"{[m for m, _, _ in env.driver.calls]}"
    )
    return preamble, groups, tail


def _assert_attribution(marker, params, subq, issues):
    if marker == "vector":
        if params.get("embedding") != EMBEDDINGS[subq]:
            issues.append(
                f"sub-question {subq!r}: vector call is not attributed to this "
                f"sub-question (embedding {params.get('embedding')!r})"
            )
    elif marker == "keyword":
        expected = lucene_or_terms(subq)
        if params.get("search_text") != expected:
            issues.append(
                f"sub-question {subq!r}: keyword call is not attributed to this "
                f"sub-question (search_text {params.get('search_text')!r})"
            )
    elif marker == "ranked_chunks":
        if params.get("start_names") != [CANNED_ENTITY]:
            issues.append(
                f"sub-question {subq!r}: ranked traversal start_names "
                f"{params.get('start_names')!r} != [{CANNED_ENTITY!r}]"
            )
    elif marker == "legacy_chunks":
        if params.get("entity_names") != [CANNED_ENTITY]:
            issues.append(
                f"sub-question {subq!r}: traversal entity_names "
                f"{params.get('entity_names')!r} != [{CANNED_ENTITY!r}]"
            )


def _assert_graph_leg_scope(case, marker, query, params, subq, issues):
    exp = _graph_clause_expectations(case)
    scalar_clause = SCALAR_CLAUSE in query
    in_clause = IN_CLAUSE in query
    if scalar_clause is not exp["scalar_clause"]:
        issues.append(
            f"sub-question {subq!r} [{marker}]: scalar collection clause "
            f"present={scalar_clause}, expected {exp['scalar_clause']} (case {case})"
        )
    if in_clause is not exp["in_clause"]:
        issues.append(
            f"sub-question {subq!r} [{marker}]: allowlist IN clause "
            f"present={in_clause}, expected {exp['in_clause']} (case {case})"
        )
    if params.get("collection_id") != exp["collection_id"]:
        issues.append(
            f"sub-question {subq!r} [{marker}]: bound collection_id "
            f"{params.get('collection_id')!r} != {exp['collection_id']!r} (case {case})"
        )
    if params.get("allowed_collection_ids") != exp["allowed_collection_ids"]:
        issues.append(
            f"sub-question {subq!r} [{marker}]: bound allowed_collection_ids "
            f"{params.get('allowed_collection_ids')!r} != "
            f"{exp['allowed_collection_ids']!r} (case {case})"
        )


_LEG_RRF_MARKERS = {"vector": "vector", "keyword": "keyword", "graph-chunks": "ranked_chunks"}
_LEG_OFF_MARKERS = {"vector": "vector", "graph-chunks": "legacy_chunks"}


def _run_leg_scope_case(env, endpoint, case, route_flag, leg):
    env.qp.settings.enable_hybrid_search = route_flag
    _run_stream(env, endpoint, case)
    _, groups, _ = _subquery_observations(env)
    marker = (_LEG_RRF_MARKERS if route_flag else _LEG_OFF_MARKERS)[leg]
    issues = []
    for i, subq in enumerate(SUB_QUESTIONS):
        entry = groups[i].get(marker)
        if entry is None:
            issues.append(
                f"sub-question {subq!r}: no {marker} store call recorded "
                f"(group markers: {list(groups[i])})"
            )
            continue
        query, params = entry
        _assert_attribution(marker, params, subq, issues)
        _assert_graph_leg_scope(case, marker, query, params, subq, issues)
    assert not issues, (
        "independent per-sub-question scope observations failed:\n"
        + "\n".join(issues)
    )


# ---------------------------------------------------------------------------
# Reachable entry + progress/done/sources healthy controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["depth", "flag"], ids=["depth-deep", "legacy-flag"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_legacy_agentic_stream_reaches_legacy_pipeline(endpoint, entry, legacy_agentic_env):
    frames = _run_stream(legacy_agentic_env, endpoint, "unrestricted", entry=entry)

    thinking = _frames_of_type(frames, "thinking")
    assert thinking, "legacy agentic stream must emit thinking frames"
    assert thinking[0]["thinking"] == "Analyzing question complexity...", (
        f"first thinking frame is not the legacy pipeline's: {thinking[0]!r}"
    )
    assert _single_frame(frames, "sub_questions")["sub_questions"] == SUB_QUESTIONS
    assert _frames_of_type(frames, "retrieval"), "retrieval progress frames missing"

    sources = _single_frame(frames, "sources")["sources"]
    assert sources, "legacy agentic stream must deliver populated sources"
    stats = _single_frame(frames, "retrieval_stats")["retrieval_stats"]
    assert stats["total_sources"] > 0
    assert stats["communities_used"] >= 1
    assert _frames_of_type(frames, "graph_context"), "graph_context frame missing"
    content = "".join(f["content"] for f in _frames_of_type(frames, "content"))
    assert content == "".join(CANNED_ANSWER_TOKENS)
    done = _single_frame(frames, "done")
    assert done.get("done") is True
    assert "communities_used" in done, (
        "done frame lacks communities_used — not the legacy agentic pipeline's"
    )

    assert [c["stream"] for c in legacy_agentic_env.llm_calls] == [False, True], (
        f"expected exactly one canned decompose (non-stream) and one canned "
        f"writer (stream) LLM call, got {legacy_agentic_env.llm_calls}"
    )

    preamble, groups, tail = _segment_calls(legacy_agentic_env.driver.calls)
    assert len(preamble) == 1 and preamble[0][0] == "community_search", (
        f"expected exactly one community_search call before the searches, got {preamble}"
    )
    assert len(groups) == len(SUB_QUESTIONS)
    assert [m for m, _, _ in tail] == ["community_get", "community_rels"], (
        f"expected one scoped-able community_get (+ its relationships query) "
        f"after the searches, got {tail}"
    )


@pytest.mark.parametrize("case", ["single", "multi"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_restricted_legacy_stream_still_delivers_progress_and_sources(
    endpoint, case, legacy_agentic_env
):
    frames = _run_stream(legacy_agentic_env, endpoint, case)
    assert _single_frame(frames, "sources")["sources"], (
        f"restricted case {case} must still deliver sources — a blanket "
        f"denial fails this control"
    )
    assert _single_frame(frames, "done").get("done") is True
    stats = _single_frame(frames, "retrieval_stats")["retrieval_stats"]
    assert stats["total_sources"] > 0
    assert _frames_of_type(frames, "content")
    assert _frames_of_type(frames, "sub_questions")


# ---------------------------------------------------------------------------
# Graph-leg scope: initial search AND every sub-question, both routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leg", ["vector", "keyword", "graph-chunks"])
@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_legacy_agentic_rrf_leg_scope(endpoint, case, leg, legacy_agentic_env):
    _run_leg_scope_case(legacy_agentic_env, endpoint, case, route_flag=True, leg=leg)


@pytest.mark.parametrize("leg", ["vector", "graph-chunks"])
@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_legacy_agentic_hybrid_off_leg_scope(endpoint, case, leg, legacy_agentic_env):
    _run_leg_scope_case(legacy_agentic_env, endpoint, case, route_flag=False, leg=leg)


# ---------------------------------------------------------------------------
# Community faces: real community builders must carry the derived scope
# ---------------------------------------------------------------------------


_COMMUNITY_IN_CLAUSE = "col.id IN $allowed_collection_ids"


def _assert_community_call_scope(case, query, params, issues, face):
    expected = _expected_community_scope(case)
    in_clause = _COMMUNITY_IN_CLAUSE in query
    if expected is None:
        if in_clause:
            issues.append(f"{face} (case {case}): unrestricted request must not bind an allowlist clause")
        if "allowed_collection_ids" in params:
            issues.append(
                f"{face} (case {case}): unrestricted request must not send "
                f"allowed_collection_ids, got {params['allowed_collection_ids']!r}"
            )
    else:
        if not in_clause:
            issues.append(
                f"{face} (case {case}): expected the allowlist IN clause bound "
                f"with {expected!r}; assembled clause-less query instead"
            )
        if params.get("allowed_collection_ids") != expected:
            issues.append(
                f"{face} (case {case}): bound allowed_collection_ids "
                f"{params.get('allowed_collection_ids')!r} != {expected!r}"
            )


@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_legacy_agentic_community_search_scope(endpoint, case, legacy_agentic_env):
    _run_stream(legacy_agentic_env, endpoint, case)
    preamble, _, _ = _subquery_observations(legacy_agentic_env)
    searches = [(q, p) for m, q, p in preamble if m == "community_search"]
    issues = []
    if len(searches) != 1:
        issues.append(f"expected exactly one community_search call, got {len(searches)}")
    else:
        query, params = searches[0]
        _assert_community_call_scope(case, query, params, issues, "community_search")
        if params.get("search_query") != lucene_or_terms(QUESTION):
            issues.append(
                f"community_search search_query {params.get('search_query')!r} != "
                f"{lucene_or_terms(QUESTION)!r}"
            )
        if params.get("limit") != 3:
            issues.append(f"community_search limit {params.get('limit')!r} != 3")
    assert not issues, "community-search scope observations failed:\n" + "\n".join(issues)


@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_legacy_agentic_community_summary_scope(endpoint, case, legacy_agentic_env):
    _run_stream(legacy_agentic_env, endpoint, case)
    _, _, tail = _subquery_observations(legacy_agentic_env)
    gets = [(q, p) for m, q, p in tail if m == "community_get"]
    issues = []
    if len(gets) != 1:
        issues.append(f"expected exactly one community_get call, got {len(gets)}")
    else:
        query, params = gets[0]
        if params.get("id") != COMMUNITY_ID:
            issues.append(
                f"community_get id {params.get('id')!r} != {COMMUNITY_ID!r}"
            )
        _assert_community_call_scope(case, query, params, issues, "community_get")
    assert not issues, "community-summary scope observations failed:\n" + "\n".join(issues)


# ---------------------------------------------------------------------------
# Distinct healthy REST boundary controls
# ---------------------------------------------------------------------------


def test_nonstream_rest_agentic_rejected_with_stream_guidance(
    legacy_agentic_env, monkeypatch
):
    monkeypatch.setattr(
        legacy_agentic_env.settings, "enable_agent_research", True, raising=True
    )
    r = legacy_agentic_env.client.post(
        "/api/ask",
        json={"question": QUESTION, "depth": "deep"},
        headers=_header("key-all"),
    )
    assert r.status_code == 400, (r.status_code, r.text[:400])
    detail = r.json()["detail"]
    assert detail["error"] == "agentic_requires_streaming"
    assert detail["use_endpoint"] == "/api/ask/stream"
    assert legacy_agentic_env.driver.calls == [], (
        "the non-streaming agentic rejection must happen before any store "
        "assembly — direct _agentic_rag_query is not REST evidence"
    )


@pytest.mark.parametrize(
    "case, collection",
    [("multi", "col-forbidden"), ("empty", "col-one")],
    ids=["multi-foreign-collection", "empty-key-any-collection"],
)
def test_restricted_key_requesting_out_of_scope_collection_rejected_at_rest(
    legacy_agentic_env, case, collection
):
    r = legacy_agentic_env.client.post(
        "/api/ask/stream",
        json={"question": QUESTION, "depth": "deep", "collection_id": collection},
        headers=_header(SCOPE_CASES[case]["key"]),
    )
    assert r.status_code == 403, (r.status_code, r.text[:400])
    assert legacy_agentic_env.driver.calls == [], (
        "a REST-rejected request must not reach the store layer"
    )
