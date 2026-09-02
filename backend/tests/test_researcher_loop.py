"""Behavioral tests for the researcher loop's v2 efficiency paths.

Drives `_run_researcher_loop` with a scripted fake LLM client + processor to
lock the new contracts:
- parallel read-only tool calls (both get tool replies, sources accumulate)
- speed early-write (no second LLM round-trip after a fruitful search)
- cross-iteration search dedup (repeat queries answered from cache)
- researcher-supplied entity hints reaching the search pipeline
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services.researcher_agent import _run_researcher_loop


def _tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def _assistant(tool_calls=None, content=None):
    msg = SimpleNamespace(tool_calls=tool_calls or None, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeClient:
    """Returns scripted responses in order; records call count."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.call_kwargs = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.calls += 1
        self.call_kwargs.append(kwargs)
        if not self._responses:
            # Out of script — behave like a model that just calls done.
            return _assistant(
                [_tool_call("fallback", "done", '{"summary": "done"}')]
            )
        return self._responses.pop(0)


class FakeProcessor:
    """Minimal processor surface for _execute_knowledge_search."""

    def __init__(self):
        self.search_calls = []  # (query, precomputed_entities)
        self.graph_extractor = SimpleNamespace(is_available=False)

    def embed_queries(self, queries):
        return [[0.1] * 4 for _ in queries]

    async def graph_search_async(self, query, **kwargs):
        self.search_calls.append(
            (query, kwargs.get("precomputed_entities"))
        )
        return {
            "results": [
                {
                    "chunk_id": f"c-{query}",
                    "content": f"content for {query}",
                    "score": 0.9,
                    "filename": "doc.md",
                }
            ],
            "graph_context": {"entities": [], "relationships": [], "chunks": []},
        }

    async def rerank_results_async(self, question, results, top_k=15):
        return results[:top_k]


@pytest.fixture()
def loop_settings():
    s = get_settings()
    saved = {
        k: getattr(s, k)
        for k in (
            "enable_skills",
            "enable_git_integration",
            "enable_reranking",
            "researcher_speed_early_write",
            "researcher_parallel_tool_calls",
            "researcher_search_dedup",
            "researcher_tool_entity_hints",
            "stream_reasoning_steps",
        )
    }
    s.enable_skills = False
    s.enable_git_integration = False
    s.enable_reranking = False
    s.stream_reasoning_steps = False
    yield s
    for k, v in saved.items():
        setattr(s, k, v)


async def _drive(client, processor, settings, mode="speed"):
    events = []
    async for ev in _run_researcher_loop(
        question="q?",
        mode=mode,
        conversation_history=[],
        collection_id=None,
        processor=processor,
        neo4j_service=None,
        client=client,
        llm_config=SimpleNamespace(model="m", base_url="http://x", api_key="k"),
        settings=settings,
    ):
        events.append(ev)
    return events


async def test_parallel_tool_calls_both_answered(loop_settings):
    """Two knowledge_search calls in one assistant message → both executed,
    both tool replies produced, sources from both accumulated."""
    client = FakeClient([
        _assistant([
            _tool_call("a", "knowledge_search", '{"queries": ["alpha"]}'),
            _tool_call("b", "knowledge_search", '{"queries": ["beta"]}'),
        ]),
        _assistant([_tool_call("c", "done", '{"summary": "ok"}')]),
    ])
    processor = FakeProcessor()
    events = await _drive(client, processor, loop_settings, mode="quality")

    result = next(e for e in events if e["type"] == "result")["data"]
    assert {q for q, _ in processor.search_calls} == {"alpha", "beta"}
    assert result.search_count == 2
    assert {s["chunk_id"] for s in result.sources} == {"c-alpha", "c-beta"}


async def test_speed_early_write_skips_confirmation_round_trip(loop_settings):
    """Speed mode: one fruitful search iteration → loop breaks to the writer
    without a second LLM call asking the model to confirm done."""
    client = FakeClient([
        _assistant([
            _tool_call("a", "knowledge_search", '{"queries": ["alpha"]}')
        ]),
        # Would be the confirmation round-trip — must never be requested.
        _assistant([_tool_call("z", "done", '{"summary": "unused"}')]),
    ])
    events = await _drive(client, FakeProcessor(), loop_settings, mode="speed")

    result = next(e for e in events if e["type"] == "result")["data"]
    assert result.sources, "search results expected"
    assert client.calls == 1, "early-write must skip the second LLM round-trip"


async def test_speed_early_write_disabled_keeps_legacy_loop(loop_settings):
    loop_settings.researcher_speed_early_write = False
    client = FakeClient([
        _assistant([
            _tool_call("a", "knowledge_search", '{"queries": ["alpha"]}')
        ]),
        _assistant([_tool_call("z", "done", '{"summary": "ok"}')]),
    ])
    await _drive(client, FakeProcessor(), loop_settings, mode="speed")
    assert client.calls == 2


async def test_search_dedup_serves_repeat_from_cache(loop_settings):
    """An identical repeat knowledge_search must not re-run retrieval and must
    not double-count sources."""
    loop_settings.researcher_speed_early_write = False
    client = FakeClient([
        _assistant([
            _tool_call("a", "knowledge_search", '{"queries": ["alpha"]}')
        ]),
        _assistant([
            _tool_call("b", "knowledge_search", '{"queries": ["ALPHA "]}')
        ]),  # same query modulo case/whitespace
        _assistant([_tool_call("c", "done", '{"summary": "ok"}')]),
    ])
    processor = FakeProcessor()
    events = await _drive(client, processor, loop_settings, mode="speed")

    result = next(e for e in events if e["type"] == "result")["data"]
    assert len(processor.search_calls) == 1, "repeat must be served from cache"
    assert result.search_count == 1
    assert len(result.sources) == 1


async def test_entity_hints_skip_extraction_and_reach_search(loop_settings):
    """entities on the tool call flow through as precomputed_entities."""
    client = FakeClient([
        _assistant([
            _tool_call(
                "a",
                "knowledge_search",
                '{"queries": ["alpha"], "entities": ["Cortex", "Neo4j"]}',
            )
        ]),
    ])
    processor = FakeProcessor()
    await _drive(client, processor, loop_settings, mode="speed")

    assert processor.search_calls, "search must run"
    _, precomputed = processor.search_calls[0]
    assert precomputed == ["Cortex", "Neo4j"]


def _thinking_suppressed(kwargs: dict) -> bool:
    """True when a recorded call carried reasoning-suppression params.

    The fake llm_config uses model "m" on a plain OpenAI-compatible base_url,
    which resolves to the defensive both-keys payload.
    """
    return "enable_thinking" in (
        (kwargs.get("extra_body") or {}).get("chat_template_kwargs") or {}
    )


async def test_quality_mode_suppresses_thinking_by_default(loop_settings):
    """Deep research runs non-thinking unless RESEARCH_REASONING_MODE says otherwise.

    Deliberation in this loop is explicit (the `reasoning` tool, forced by the
    reflection micro-call) and persists in the message history; hidden CoT is
    discarded between turns while competing for the same token budget.
    """
    client = FakeClient([_assistant([_tool_call("z", "done", '{"summary": "ok"}')])])
    await _drive(client, FakeProcessor(), loop_settings, mode="quality")

    assert client.call_kwargs, "the loop must have called the LLM"
    assert _thinking_suppressed(client.call_kwargs[0])


async def test_research_reasoning_mode_auto_restores_thinking(loop_settings):
    """The escape hatch reaches the wire, not just the mode helper."""
    saved = loop_settings.research_reasoning_mode
    loop_settings.research_reasoning_mode = "auto"
    try:
        client = FakeClient([_assistant([_tool_call("z", "done", '{"summary": "ok"}')])])
        await _drive(client, FakeProcessor(), loop_settings, mode="quality")
    finally:
        loop_settings.research_reasoning_mode = saved

    assert not _thinking_suppressed(client.call_kwargs[0])


async def test_empty_choices_retry_keeps_reasoning_params(loop_settings):
    """The retry paths must not silently drop suppression.

    A bare create() on the retry runs WITH thinking on a thinking-by-default
    model — the slowest path taken exactly when the model is already flaky.
    """
    client = FakeClient([
        SimpleNamespace(choices=[]),  # provider returned 200 with no choices
        _assistant([_tool_call("z", "done", '{"summary": "ok"}')]),
    ])
    await _drive(client, FakeProcessor(), loop_settings, mode="quality")

    assert client.calls == 2, "the empty-choices retry must have fired"
    assert all(_thinking_suppressed(kw) for kw in client.call_kwargs)


# ---------------------------------------------------------------------------
# Retrieval recall fixes: tool scoping + rerank pool sizing
# ---------------------------------------------------------------------------


class FakeNeo4j:
    """Records the scope each read-only tool receives."""

    def __init__(self):
        self.community_calls = []
        self.entity_calls = []

    def search_communities_by_content(self, query, limit=5, allowed_collection_ids=None):
        self.community_calls.append((query, limit, allowed_collection_ids))
        return [{"id": "com1", "name": "Governance", "summary": "s", "entity_count": 3}]

    def find_entities_by_name(self, names, allowed_collection_ids=None):
        self.entity_calls.append((list(names), allowed_collection_ids))
        return [{"name": "Polygon", "type": "Technology", "description": "d", "connection_count": 2}]


async def _drive_scoped(client, processor, neo4j, settings, *, collection_id, allowed):
    events = []
    async for ev in _run_researcher_loop(
        question="q?",
        mode="quality",
        conversation_history=[],
        collection_id=collection_id,
        processor=processor,
        neo4j_service=neo4j,
        client=client,
        llm_config=SimpleNamespace(model="m", base_url="http://x", api_key="k"),
        settings=settings,
        allowed_collection_ids=allowed,
    ):
        events.append(ev)
    return events


async def test_community_and_entity_tools_receive_request_collection_scope(loop_settings):
    """Sequential branch (one tool per message): both tools get the request's
    collection as scope. Previously they ran unscoped — communities from
    outside the scope reached the client via the graph_context frame."""
    # Each retrieval round carries a `reasoning` call so quality mode's
    # forced-reflection micro-call never consumes a scripted response.
    client = FakeClient([
        _assistant([
            _tool_call("r1", "reasoning", '{"thought": "look at themes"}'),
            _tool_call("a", "community_search", '{"query": "themes"}'),
        ]),
        _assistant([
            _tool_call("r2", "reasoning", '{"thought": "look up Poly"}'),
            _tool_call("b", "entity_lookup", '{"names": ["Poly"]}'),
        ]),
        _assistant([_tool_call("c", "done", '{"summary": "ok"}')]),
    ])
    neo4j = FakeNeo4j()
    await _drive_scoped(
        client, FakeProcessor(), neo4j, loop_settings, collection_id="col-1", allowed=["col-1", "col-2"]
    )
    assert neo4j.community_calls == [("themes", 3, ["col-1"])]
    assert neo4j.entity_calls == [(["Poly"], ["col-1"])]


async def test_tools_fall_back_to_key_restriction_scope_in_parallel_branch(loop_settings):
    """Parallel branch (≥2 read-only calls in one message): without a request
    collection, the key's allowed collections are the scope."""
    client = FakeClient([
        _assistant([
            _tool_call("a", "community_search", '{"query": "themes"}'),
            _tool_call("b", "entity_lookup", '{"names": ["Poly"]}'),
        ]),
        _assistant([_tool_call("c", "done", '{"summary": "ok"}')]),
    ])
    neo4j = FakeNeo4j()
    await _drive_scoped(
        client, FakeProcessor(), neo4j, loop_settings, collection_id=None, allowed=["k-a", "k-b"]
    )
    assert neo4j.community_calls == [("themes", 3, ["k-a", "k-b"])]
    assert neo4j.entity_calls == [(["Poly"], ["k-a", "k-b"])]


async def test_tools_unscoped_when_no_scope_applies(loop_settings):
    client = FakeClient([
        _assistant([_tool_call("a", "community_search", '{"query": "themes"}')]),
        _assistant([_tool_call("c", "done", '{"summary": "ok"}')]),
    ])
    neo4j = FakeNeo4j()
    await _drive_scoped(client, FakeProcessor(), neo4j, loop_settings, collection_id=None, allowed=None)
    assert neo4j.community_calls == [("themes", 3, None)]


class PoolingProcessor(FakeProcessor):
    """Every query returns a shared chunk plus its own; records the per-query
    depth requested and how many candidates reach the reranker."""

    def __init__(self):
        super().__init__()
        self.top_ks = []
        self.rerank_inputs = []

    async def graph_search_async(self, query, **kwargs):
        self.top_ks.append(kwargs.get("top_k"))
        return {
            "results": [
                {"chunk_id": "shared", "content": "same", "score": 0.5, "filename": "doc.md"},
                {"chunk_id": f"c-{query}", "content": f"content for {query}", "score": 0.9, "filename": "doc.md"},
            ],
            "graph_context": {"entities": [], "relationships": [], "chunks": []},
            "vector_count": 2, "keyword_count": 1, "graph_chunk_count": 0,
        }

    async def rerank_results_async(self, question, results, top_k=15):
        self.rerank_inputs.append(len(results))
        return results[:top_k]


async def test_rerank_pool_is_deeper_than_kept_and_deduped_before_rerank(loop_settings):
    """3 queries → 10 candidates requested per query (pool ~2× rerank_top_k=15),
    and the shared chunk occupies ONE rerank slot, not three."""
    loop_settings.enable_reranking = True
    client = FakeClient([
        _assistant([
            _tool_call("a", "knowledge_search", '{"queries": ["alpha", "beta", "gamma"]}')
        ]),
    ])
    processor = PoolingProcessor()
    events = await _drive(client, processor, loop_settings, mode="speed")

    assert processor.top_ks == [10, 10, 10]
    # 3 queries × 2 results = 6 pooled, 4 unique → the reranker sees 4.
    assert processor.rerank_inputs == [4]
    result = next(e for e in events if e["type"] == "result")["data"]
    assert {s["chunk_id"] for s in result.sources} == {"shared", "c-alpha", "c-beta", "c-gamma"}
