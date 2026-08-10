"""POST /api/context — context-window assembly (retrieval without the writer).

Covers budget allocation (whole chunks, enrichment cap, at-least-one-chunk
floor), scoping, structure of the ready-to-inject text block, and the
graceful degradation paths.
"""

from unittest.mock import AsyncMock

import pytest


def _chunk(i: int, content: str = None, score: float = 0.9):
    return {
        "document_id": f"doc{i}",
        "chunk_id": f"chunk{i}",
        "content": content if content is not None else f"Chunk {i} content. " * 10,
        "score": score,
        "rerank_score": score,
        "filename": f"file{i}.md",
        "chunk_index": 0,
    }


def _search_result(chunks, entities=None, relationships=None):
    return {
        "results": chunks,
        "graph_context": {
            "entities": entities or [],
            "relationships": relationships or [],
            "chunks": [],
        },
    }


@pytest.fixture
def ctx_env(client, mock_processors, mock_neo4j, _isolate_env):
    _isolate_env.enable_hybrid_search = True
    _isolate_env.enable_reranking = False  # keep the path deterministic
    mock_processors.query.graph_search_async = AsyncMock(
        return_value=_search_result([_chunk(1), _chunk(2), _chunk(3)])
    )
    mock_neo4j.search_communities_by_content.return_value = []

    class Env:
        pass

    env = Env()
    env.client = client
    env.query = mock_processors.query
    env.neo4j = mock_neo4j
    return env


class TestContextAssembly:
    def test_basic_bundle_shape(self, ctx_env):
        r = ctx_env.client.post("/api/context", json={"query": "what is x?"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["query"] == "what is x?"
        assert len(body["chunks"]) == 3
        assert body["text"].startswith("=== Knowledge Context ===")
        assert "[src_1] Source: file1.md" in body["text"]
        assert "[src_3]" in body["text"]
        assert body["token_count"] > 0
        assert body["budget"]["max_tokens"] == 4000
        assert body["budget"]["chunks_included"] == 3

    def test_graph_and_communities_sections(self, ctx_env):
        ctx_env.query.graph_search_async = AsyncMock(return_value=_search_result(
            [_chunk(1)],
            entities=[{"name": "Alpha", "type": "System", "description": "core"}],
            relationships=[{"source": "Alpha", "target": "Beta", "type": "USES"}],
        ))
        ctx_env.neo4j.search_communities_by_content.return_value = [
            {"id": 1, "name": "Infra", "summary": "Infrastructure things."},
        ]
        body = ctx_env.client.post("/api/context", json={"query": "q"}).json()
        assert "=== Related Entities ===" in body["text"]
        assert "Alpha --[USES]--> Beta" in body["text"]
        assert "=== Knowledge Communities ===" in body["text"]
        assert body["graph_context"]["entities"][0]["name"] == "Alpha"
        assert body["communities"][0]["name"] == "Infra"
        assert body["budget"]["graph"] > 0 and body["budget"]["communities"] > 0

    def test_include_flags_disable_sections(self, ctx_env):
        ctx_env.query.graph_search_async = AsyncMock(return_value=_search_result(
            [_chunk(1)],
            entities=[{"name": "Alpha", "type": "System"}],
        ))
        body = ctx_env.client.post("/api/context", json={
            "query": "q", "include_graph": False, "include_communities": False,
        }).json()
        assert body["graph_context"] is None
        assert body["communities"] == []
        assert "Related Entities" not in body["text"]
        assert not ctx_env.neo4j.search_communities_by_content.called

    def test_budget_drops_whole_chunks(self, ctx_env):
        # Three big chunks (~250 tokens each), budget fits roughly one
        big = "word " * 1000  # ~1250 tokens
        ctx_env.query.graph_search_async = AsyncMock(return_value=_search_result(
            [_chunk(1, big), _chunk(2, big), _chunk(3, big)]
        ))
        body = ctx_env.client.post("/api/context", json={
            "query": "q", "max_tokens": 1500,
        }).json()
        assert body["budget"]["chunks_included"] == 1
        assert body["budget"]["chunks_considered"] == 3
        assert len(body["chunks"]) == 1
        assert body["token_count"] <= 1500

    def test_first_chunk_truncated_when_oversized(self, ctx_env):
        huge = "word " * 5000  # far over any small budget
        ctx_env.query.graph_search_async = AsyncMock(return_value=_search_result(
            [_chunk(1, huge)]
        ))
        body = ctx_env.client.post("/api/context", json={
            "query": "q", "max_tokens": 400,
        }).json()
        # never an empty bundle when retrieval found something
        assert body["budget"]["chunks_included"] == 1
        assert 0 < body["token_count"] <= 500  # small tolerance over budget

    def test_empty_retrieval_is_valid_empty_bundle(self, ctx_env):
        ctx_env.query.graph_search_async = AsyncMock(return_value=_search_result([]))
        body = ctx_env.client.post("/api/context", json={"query": "nothing"}).json()
        assert body["chunks"] == []
        assert body["text"] == ""
        assert body["token_count"] == 0

    def test_community_failure_degrades_gracefully(self, ctx_env):
        ctx_env.neo4j.search_communities_by_content.side_effect = RuntimeError("neo4j down")
        r = ctx_env.client.post("/api/context", json={"query": "q"})
        assert r.status_code == 200
        assert r.json()["communities"] == []

    def test_collection_id_passed_through(self, ctx_env):
        body = ctx_env.client.post("/api/context", json={
            "query": "q", "collection_id": "col-1",
        }).json()
        assert body["collection_id"] == "col-1"
        _, kwargs = ctx_env.query.graph_search_async.call_args
        assert kwargs["collection_id"] == "col-1"

    def test_validation_422s(self, ctx_env):
        assert ctx_env.client.post("/api/context", json={}).status_code == 422
        assert ctx_env.client.post(
            "/api/context", json={"query": "q", "max_tokens": 50}
        ).status_code == 422
        assert ctx_env.client.post(
            "/api/context", json={"query": "q", "top_k": 999}
        ).status_code == 422


class TestContextMonetizedAccess:
    def test_context_in_monetized_allowlist(self):
        from app.services.auth_service import MONETIZED_KEY_ALLOWED_PATHS

        assert "/api/context" in MONETIZED_KEY_ALLOWED_PATHS
