"""Legacy non-streaming agentic scope gate (ENABLE_AGENT_RESEARCH=false).

Real-auth HTTP entry (`POST /api/ask`, depth "deep" / legacy `use_agentic`)
with the research flag off → real `QueryProcessor.rag_query` → real
`_agentic_rag_query` → real `graph_search_async` and the real Neo4jService
query builders over the imported recording transport, with a canned SYNC
OpenAI client (the source's `asyncio.to_thread` wrapping is preserved — the
canned `create` records its thread and must never run on the event loop).

Oracle (output/legacy-agentic-nonstream-scope-20261004/evaluation/GATE-ORACLE.md):
the flag-off non-streaming branch forwards the request-scalar scope but drops
the key's multi/empty collection allowlist at the `rag_query` →
`_agentic_rag_query` call site; `_agentic_rag_query` then runs
`search_communities_by_content` and `get_community` with no scope argument at
all, and its no-LLM-key recursive fallback (`_agentic_rag_query` →
`rag_query(use_agentic=False)`) drops the allowlist again before the chunk
builders. Scalar/allowlist precedence follows main.py's scope resolution;
community scope follows the researcher `_tool_scope` precedent
(`[collection_id] if collection_id else allowed_collection_ids`); `None`
stays unrestricted; `[]` must bind as a real empty filter.

v3 delta (output/legacy-agentic-nonstream-flags-20261004/evaluation/
GATE-ORACLE.md): the provider synthesis completion's finish_reason is intended
to reach the public RAGResponse (`stop`→stop/false, `length`→length/true);
the decompose call's reason must never become the answer's public reason, and
an absent/null provider reason stays null/false. Gate v2's null/false on this
branch was observed characterization of the unpropagated agentic return, not
intent.

Evidence boundary: recording-driver assembly only. The store never executes;
this proves neither returned-data isolation, live Cypher, complete
graph-entity/relationship metadata confinement, nor production parity. The
direct `_agentic_rag_query(..., thinking_callback)` control pins the old
positional callback contract at the library boundary and is NOT REST
evidence. The flag-on 400 control pins the streaming boundary; it cannot
establish flag-off rejection. Fixture/helpers are imported from
`tests.test_legacy_agentic_scope`; the raw-question embed/extractor fakes are
extended locally (no global harness mutation).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.models import ThinkingEvent
from app.services.neo4j_service import Neo4jService, lucene_or_terms
from tests.test_legacy_agentic_scope import (  # noqa: F401
    CANNED_ENTITY,
    COMMUNITY_ID,
    EMBEDDINGS,
    QUESTION,
    SUB_QUESTIONS,
    SCOPE_CASES,
    _LEG_OFF_MARKERS,
    _LEG_RRF_MARKERS,
    _RecordingDriver,
    _assert_attribution,
    _assert_community_call_scope,
    _assert_graph_leg_scope,
    _canned_embed,
    _canned_extract,
    _classify,
    _header,
    _install_key_registry,
    _segment_calls,
    _store_handler,
    _subquery_observations,
)

CANNED_NONSTREAM_ANSWER = "Canned nonstream answer."
QUESTION_EMBEDDING = [0.625, -0.75]
EMBED_BY_QUERY = {**EMBEDDINGS, QUESTION: QUESTION_EMBEDDING}
QUERY_BY_EMBED = {tuple(v): k for k, v in EMBED_BY_QUERY.items()}


# ---------------------------------------------------------------------------
# Extended canned controls — raw-question embed/extractor/handler, local only
# ---------------------------------------------------------------------------


def _nonstream_store_handler(query, params):
    if _classify(query) == "vector":
        query_text = QUERY_BY_EMBED[tuple(params["embedding"])]
        return [{
            "document_id": "doc-" + query_text,
            "filename": query_text + ".pdf",
            "chunk_id": "vec-" + query_text,
            "content": "vector content " + query_text,
            "chunk_index": 0,
            "score": 0.9,
        }]
    return _store_handler(query, params)


def _extended_embed(text):
    if text == QUESTION:
        return list(QUESTION_EMBEDDING)
    return _canned_embed(text)


async def _extended_extract(query):
    if query == QUESTION:
        return [CANNED_ENTITY]
    return await _canned_extract(query)


def _canned_sync_llm_factory(llm_calls):
    decompose = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='{"sub_questions": ["sub-alpha", "sub-beta"]}'),
            finish_reason="stop",
        )]
    )
    answer = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=CANNED_NONSTREAM_ANSWER),
            finish_reason="stop",
        )]
    )

    class _SyncClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            llm_calls.append({
                "stream": bool(kwargs.get("stream")),
                "ident": threading.get_ident(),
            })
            messages = kwargs.get("messages") or []
            if any(
                "Break down this question" in (m.get("content") or "")
                for m in messages
            ):
                return decompose
            return answer

    return _SyncClient


def _canned_reasons_sync_llm_factory(llm_calls, decompose_reason, answer_reason):
    """Scenario variant of the canned SYNC client: per-call finish_reason.

    Same canned answer/decomposition and the same decompose-vs-synthesis
    prompt discrimination as `_canned_sync_llm_factory`, but each returned
    completion carries the scenario's raw provider `finish_reason`, and every
    call records it (with the stream flag and thread identity) so the raw
    observation stays independent of the public projection.
    """
    decompose = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='{"sub_questions": ["sub-alpha", "sub-beta"]}'),
            finish_reason=decompose_reason,
        )]
    )
    answer = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=CANNED_NONSTREAM_ANSWER),
            finish_reason=answer_reason,
        )]
    )

    class _SyncClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            messages = kwargs.get("messages") or []
            is_decompose = any(
                "Break down this question" in (m.get("content") or "")
                for m in messages
            )
            response = decompose if is_decompose else answer
            llm_calls.append({
                "stream": bool(kwargs.get("stream")),
                "ident": threading.get_ident(),
                "role": "decompose" if is_decompose else "synthesis",
                "raw_finish_reason": response.choices[0].finish_reason,
            })
            return response

    return _SyncClient


# ---------------------------------------------------------------------------
# Fixture: real-auth client + real QueryProcessor over the recording service
# ---------------------------------------------------------------------------


@pytest.fixture
def legacy_nonstream_env(mock_neo4j, _isolate_env, monkeypatch):
    from app.main import app
    from app.services import document_processor as dp
    from app.services.document_processor import QueryProcessor

    _install_key_registry(mock_neo4j)
    driver = _RecordingDriver(_nonstream_store_handler)
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
        enable_agentic_rag=True,
        openai_api_key="test-key-legacy-agentic-nonstream",
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
        extract_entities_from_query_async=_extended_extract,
    )
    qp.embed_query = _extended_embed

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
        dp, "make_openai_client",
        lambda **kwargs: _canned_sync_llm_factory(llm_calls)(), raising=True,
    )
    monkeypatch.setattr(_isolate_env, "enable_agent_research", False, raising=True)
    monkeypatch.setattr(_isolate_env, "enable_agentic_rag", True, raising=True)
    monkeypatch.setattr(_isolate_env, "enable_reranking", False, raising=True)
    monkeypatch.setattr(
        _isolate_env, "openai_api_key", "test-key-legacy-agentic-nonstream",
        raising=True,
    )

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, driver=driver, svc=svc, qp=qp,
            settings=_isolate_env, llm_calls=llm_calls, neo4j=mock_neo4j,
        )
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# REST driver + observation helpers
# ---------------------------------------------------------------------------


def _run_ask(env, case, entry="depth"):
    payload = {"question": QUESTION, "top_k": 5, "max_hops": 2}
    if entry == "depth":
        payload["depth"] = "deep"
    else:
        payload["use_agentic"] = True
    request_collection = SCOPE_CASES[case].get("request_collection")
    if request_collection:
        payload["collection_id"] = request_collection
    r = env.client.post(
        "/api/ask", json=payload, headers=_header(SCOPE_CASES[case]["key"])
    )
    assert r.status_code == 200, (r.status_code, r.text[:500])
    return r.json()


def _single_search_group(env):
    preamble, groups, tail = _segment_calls(env.driver.calls)
    assert not preamble and not tail, (
        f"unexpected extra store calls outside the single search: "
        f"preamble={preamble} tail={tail}"
    )
    assert len(groups) == 1, (
        f"expected exactly one store-search group, got {len(groups)}; "
        f"call markers: {[m for m, _, _ in env.driver.calls]}"
    )
    return groups[0]


def _assert_query_attribution(marker, params, query_text, issues):
    if marker == "vector":
        if params.get("embedding") != EMBED_BY_QUERY[query_text]:
            issues.append(
                f"query {query_text!r}: vector call is not attributed to this "
                f"query (embedding {params.get('embedding')!r})"
            )
    elif marker == "keyword":
        expected = lucene_or_terms(query_text)
        if params.get("search_text") != expected:
            issues.append(
                f"query {query_text!r}: keyword call is not attributed to this "
                f"query (search_text {params.get('search_text')!r})"
            )
    elif marker == "ranked_chunks":
        if params.get("start_names") != [CANNED_ENTITY]:
            issues.append(
                f"query {query_text!r}: ranked traversal start_names "
                f"{params.get('start_names')!r} != [{CANNED_ENTITY!r}]"
            )
    elif marker == "legacy_chunks":
        if params.get("entity_names") != [CANNED_ENTITY]:
            issues.append(
                f"query {query_text!r}: traversal entity_names "
                f"{params.get('entity_names')!r} != [{CANNED_ENTITY!r}]"
            )


def _run_nonstream_leg_scope_case(env, case, route_flag, leg):
    env.qp.settings.enable_hybrid_search = route_flag
    _run_ask(env, case)
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
# Healthy flag-off 200 — positive answer/sources/subquestions/reasoning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["depth", "flag"], ids=["depth-deep", "legacy-flag"])
def test_nonstream_agentic_healthy_positive(entry, legacy_nonstream_env):
    env = legacy_nonstream_env
    body = _run_ask(env, "unrestricted", entry=entry)

    assert body["answer"] == CANNED_NONSTREAM_ANSWER
    assert body["refused"] is False
    assert body["truncated"] is False
    # v3 acceptance delta (output/legacy-agentic-nonstream-flags-20261004):
    # the canned synthesis completion reports finish_reason="stop" and that
    # reason is intended to reach the public RAGResponse. Gate v2 observed
    # null/false here — scoped characterization of the unpropagated agentic
    # return, not a permanent product obligation.
    assert body["finish_reason"] == "stop"
    assert body["reranked"] is True
    assert body["collection_id"] is None

    sources = body["sources"]
    assert sources, "flag-off agentic ask must deliver populated sources"
    contents = {s["content"] for s in sources}
    assert "vector content sub-alpha" in contents
    assert "vector content sub-beta" in contents
    assert any("keyword content" in c for c in contents)
    assert any("ranked graph chunk" in c for c in contents)

    steps = body["reasoning_steps"]
    assert steps, "reasoning_steps missing on the flag-off agentic response"
    assert any("sub-alpha" in s for s in steps), (
        f"decomposed sub-question 'sub-alpha' not visible in reasoning_steps: {steps}"
    )
    assert any("sub-beta" in s for s in steps)
    assert any("Identified 2 research areas" in s for s in steps)

    graph_context = body["graph_context"]
    assert graph_context, "graph_context missing on the flag-off agentic response"
    assert any(e.get("name") == CANNED_ENTITY for e in graph_context["entities"])
    assert any(c.get("id") == COMMUNITY_ID for c in graph_context["communities"])

    assert [c["stream"] for c in env.llm_calls] == [False, False], (
        f"expected exactly two canned SYNC (non-stream) LLM calls — decompose "
        f"then answer — got {env.llm_calls}"
    )

    preamble, groups, tail = _segment_calls(env.driver.calls)
    assert len(preamble) == 1 and preamble[0][0] == "community_search", (
        f"expected exactly one community_search call before the searches, got {preamble}"
    )
    assert len(groups) == len(SUB_QUESTIONS)
    assert [m for m, _, _ in tail] == ["community_get", "community_rels"], (
        f"expected one community_get (+ its relationships query) after the "
        f"searches, got {tail}"
    )


# ---------------------------------------------------------------------------
# v3 positive provider gates: synthesis stop/length must project publicly;
# the decompose reason must not. Raw provider reason and public fields are
# observed independently per case.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decompose_reason,answer_reason,entry,expected_reason,expected_truncated",
    [
        ("stop", "stop", "depth", "stop", False),
        ("stop", "stop", "flag", "stop", False),
        ("stop", "length", "depth", "length", True),
        ("length", "stop", "depth", "stop", False),
        ("stop", "length", "flag", "length", True),
        ("stop", None, "depth", None, False),
    ],
    ids=[
        "stop-synthesis-depth-deep",
        "stop-synthesis-legacy-flag",
        "length-synthesis-depth-deep",
        "decompose-length-synthesis-stop-depth-deep",
        "decompose-stop-synthesis-length-legacy-flag",
        "null-reason-compat-depth-deep",
    ],
)
def test_nonstream_agentic_provider_finish_reason_projection(
    decompose_reason, answer_reason, entry, expected_reason, expected_truncated,
    legacy_nonstream_env, monkeypatch,
):
    from app.services import document_processor as dp

    env = legacy_nonstream_env
    reason_calls = []
    monkeypatch.setattr(
        dp, "make_openai_client",
        lambda **kwargs: _canned_reasons_sync_llm_factory(
            reason_calls, decompose_reason, answer_reason)(),
        raising=True,
    )
    body = _run_ask(env, "unrestricted", entry=entry)

    # Raw provider observations, independent of the public projection:
    # exactly the decompose call then the synthesis call, both SYNC
    # (non-stream), the synthesis never on the event-loop thread.
    assert [c["stream"] for c in reason_calls] == [False, False], (
        f"expected exactly two canned SYNC LLM calls, got {reason_calls}"
    )
    assert [c["role"] for c in reason_calls] == ["decompose", "synthesis"]
    assert [c["raw_finish_reason"] for c in reason_calls] == [
        decompose_reason, answer_reason
    ], (
        f"raw provider finish_reason observations drifted from the scenario: "
        f"{reason_calls}"
    )
    loop_ident = threading.get_ident()
    assert all(c["ident"] != loop_ident for c in reason_calls), (
        "the canned SYNC create ran on the event loop thread — the "
        "asyncio.to_thread wrapping regressed"
    )

    # Actual public fields: the synthesis reason is the answer's reason,
    # never the decompose call's; a null provider reason stays null/false.
    assert body["finish_reason"] == expected_reason
    assert body["truncated"] is expected_truncated
    assert body["answer"] == CANNED_NONSTREAM_ANSWER
    assert body["refused"] is False


# ---------------------------------------------------------------------------
# Per-leg scope: every decomposed query, RRF and hybrid-off routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leg", ["vector", "keyword", "graph-chunks"])
@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
def test_nonstream_agentic_rrf_leg_scope(case, leg, legacy_nonstream_env):
    _run_nonstream_leg_scope_case(
        legacy_nonstream_env, case, route_flag=True, leg=leg
    )


@pytest.mark.parametrize("leg", ["vector", "graph-chunks"])
@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
def test_nonstream_agentic_hybrid_off_leg_scope(case, leg, legacy_nonstream_env):
    _run_nonstream_leg_scope_case(
        legacy_nonstream_env, case, route_flag=False, leg=leg
    )


# ---------------------------------------------------------------------------
# Community faces: real community builders must carry the derived scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
def test_nonstream_agentic_community_search_scope(case, legacy_nonstream_env):
    env = legacy_nonstream_env
    _run_ask(env, case)
    preamble, _, _ = _subquery_observations(env)
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
def test_nonstream_agentic_community_summary_scope(case, legacy_nonstream_env):
    env = legacy_nonstream_env
    _run_ask(env, case)
    _, _, tail = _subquery_observations(env)
    gets = [(q, p) for m, q, p in tail if m == "community_get"]
    issues = []
    if len(gets) != 1:
        issues.append(f"expected exactly one community_get call, got {len(gets)}")
    else:
        query, params = gets[0]
        if params.get("id") != COMMUNITY_ID:
            issues.append(f"community_get id {params.get('id')!r} != {COMMUNITY_ID!r}")
        _assert_community_call_scope(case, query, params, issues, "community_get")
    assert not issues, "community-summary scope observations failed:\n" + "\n".join(issues)


# ---------------------------------------------------------------------------
# Distinct healthy REST boundary controls
# ---------------------------------------------------------------------------


def test_nonstream_rest_flag_on_agentic_rejected_with_stream_guidance(
    legacy_nonstream_env, monkeypatch
):
    env = legacy_nonstream_env
    monkeypatch.setattr(env.settings, "enable_agent_research", True, raising=True)
    r = env.client.post(
        "/api/ask",
        json={"question": QUESTION, "depth": "deep"},
        headers=_header("key-all"),
    )
    assert r.status_code == 400, (r.status_code, r.text[:400])
    detail = r.json()["detail"]
    assert detail["error"] == "agentic_requires_streaming"
    assert detail["use_endpoint"] == "/api/ask/stream"
    assert env.driver.calls == [], (
        "the flag-on rejection must happen before any store assembly — the "
        "400 control pins the streaming boundary, not the flag-off path"
    )


@pytest.mark.parametrize(
    "case, collection",
    [("multi", "col-forbidden"), ("empty", "col-one")],
    ids=["multi-foreign-collection", "empty-key-any-collection"],
)
def test_nonstream_rest_restricted_key_forbidden_collection_rejected(
    case, collection, legacy_nonstream_env
):
    env = legacy_nonstream_env
    r = env.client.post(
        "/api/ask",
        json={"question": QUESTION, "depth": "deep", "collection_id": collection},
        headers=_header(SCOPE_CASES[case]["key"]),
    )
    assert r.status_code == 403, (r.status_code, r.text[:400])
    assert env.driver.calls == [], (
        "a REST-rejected request must not reach the store layer"
    )


# ---------------------------------------------------------------------------
# No-LLM-key recursive fallback: _agentic_rag_query → rag_query(use_agentic=False)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leg", ["vector", "keyword", "graph-chunks"])
@pytest.mark.parametrize("case", list(SCOPE_CASES), ids=list(SCOPE_CASES))
def test_no_key_fallback_drops_allowlist_scope(case, leg, legacy_nonstream_env):
    env = legacy_nonstream_env
    env.qp.settings.openai_api_key = ""
    body = _run_ask(env, case)

    assert env.llm_calls == [], (
        f"the no-key fallback must return before any LLM call, got {env.llm_calls}"
    )
    assert body["answer"].startswith("Here is the relevant information:")
    assert body["reasoning_steps"] is None

    group = _single_search_group(env)
    marker = _LEG_RRF_MARKERS[leg]
    entry = group.get(marker)
    issues = []
    if entry is None:
        issues.append(
            f"no {marker} store call recorded (group markers: {list(group)})"
        )
    else:
        query, params = entry
        _assert_query_attribution(marker, params, QUESTION, issues)
        _assert_graph_leg_scope(case, marker, query, params, QUESTION, issues)
    assert not issues, (
        "no-key fallback scope observations failed:\n" + "\n".join(issues)
    )


def test_no_key_fallback_healthy_context_assembly_control(legacy_nonstream_env):
    env = legacy_nonstream_env
    env.qp.settings.openai_api_key = ""
    body = _run_ask(env, "unrestricted")

    assert body["answer"].startswith("Here is the relevant information:")
    assert body["sources"], "no-key fallback must still deliver populated sources"
    assert any(
        s["content"] == "vector content " + QUESTION for s in body["sources"]
    ), "the raw question's vector row must be attributed and returned"
    assert body["graph_context"], "no-key fallback graph_context missing"
    assert env.llm_calls == [], "no LLM call may happen on the no-key path"

    group = _single_search_group(env)
    assert "vector" in group and "ranked_chunks" in group, (
        f"expected vector + ranked graph assembly, got {list(group)}"
    )


@pytest.mark.parametrize("case", ["multi", "empty"], ids=["multi", "empty"])
def test_nonstream_nonagentic_rest_forwards_allowlist_control(
    case, legacy_nonstream_env
):
    env = legacy_nonstream_env
    r = env.client.post(
        "/api/ask",
        json={"question": QUESTION, "top_k": 5, "max_hops": 2},
        headers=_header(SCOPE_CASES[case]["key"]),
    )
    assert r.status_code == 200, (r.status_code, r.text[:400])
    body = r.json()
    assert body["answer"] == CANNED_NONSTREAM_ANSWER
    assert body["sources"], "non-agentic control must deliver populated sources"
    assert [c["stream"] for c in env.llm_calls] == [False]

    group = _single_search_group(env)
    entry = group.get("vector")
    assert entry is not None, f"no vector store call recorded (group: {list(group)})"
    query, params = entry
    issues = []
    _assert_query_attribution("vector", params, QUESTION, issues)
    _assert_graph_leg_scope(case, "vector", query, params, QUESTION, issues)
    assert not issues, (
        "non-agentic control must forward the full scope to the chunk "
        "builders — failures here invalidate the agentic-drop diagnosis:\n"
        + "\n".join(issues)
    )


# ---------------------------------------------------------------------------
# Direct library boundary: old positional thinking_callback contract
# (NOT REST evidence — /api/ask passes no callback)
# ---------------------------------------------------------------------------


async def test_direct_agentic_query_positional_thinking_callback_boundary(
    legacy_nonstream_env,
):
    env = legacy_nonstream_env
    events = []
    loop_ident = threading.get_ident()

    result = await env.qp._agentic_rag_query(
        QUESTION, 5, 2, None, None,
        lambda event: events.append(event),
    )

    assert result["sub_questions"] == SUB_QUESTIONS
    assert result["answer"] == CANNED_NONSTREAM_ANSWER
    assert result["communities_used"] == [COMMUNITY_ID]
    assert result["retrieval_stats"]["sub_questions_researched"] == len(SUB_QUESTIONS)

    assert events, "thinking_callback received no events"
    assert all(isinstance(e, ThinkingEvent) for e in events)
    assert [e.event_type for e in events] == [
        "thinking", "thinking", "thinking", "retrieval", "search", "search",
        "thinking", "retrieval", "synthesis", "done",
    ], f"unexpected thinking-event sequence: {[e.event_type for e in events]}"
    assert events[0].content == "Analyzing question complexity..."

    assert len(env.llm_calls) == 2
    assert all(not c["stream"] for c in env.llm_calls)
    assert all(c["ident"] != loop_ident for c in env.llm_calls), (
        "the canned SYNC create ran on the event loop thread — the "
        "asyncio.to_thread wrapping regressed"
    )
