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
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.calls += 1
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
