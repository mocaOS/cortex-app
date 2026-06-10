"""Tests for the Phase-1 efficiency fixes: memory blob clamping, the
memory-fast-path pre-gate, the fulltext dedup prefilter, and the entity
embedding cache."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.models import Entity
from app.services.context_curator import (
    _MAX_MEMORY_BLOB_BYTES,
    clamp_memory_blob,
    is_memory_answerable,
)
from app.services.neo4j_service import Neo4jService


# ---------------------------------------------------------------------------
# clamp_memory_blob
# ---------------------------------------------------------------------------

class TestClampMemoryBlob:
    def test_non_dict_returns_none(self):
        settings = get_settings()
        assert clamp_memory_blob(None, settings) is None
        assert clamp_memory_blob("huge string", settings) is None

    def test_small_blob_passes_through(self):
        settings = get_settings()
        blob = {"facts": ["a"], "transcript": {"summary": "s", "summarized_count": 0}}
        assert clamp_memory_blob(blob, settings) == blob

    def test_oversize_ledger_trimmed_to_cap(self):
        settings = get_settings()
        cap = settings.conversation_memory_max_ledger
        ledger = [{"sid": f"s{i}", "gist": "x"} for i in range(cap + 25)]
        out = clamp_memory_blob({"source_ledger": ledger}, settings)
        assert len(out["source_ledger"]) == cap
        # most recent entries kept
        assert out["source_ledger"][-1]["sid"] == f"s{cap + 24}"

    def test_oversize_blob_drops_heavy_buckets(self):
        settings = get_settings()
        blob = {
            "kg_context": {"entities": [{"name": "x" * 100}] * 800},
            "facts": ["small fact"],
        }
        assert len(json.dumps(blob)) > _MAX_MEMORY_BLOB_BYTES
        out = clamp_memory_blob(blob, settings)
        assert out is not None
        assert "kg_context" not in out
        assert out["facts"] == ["small fact"]

    def test_unclampable_blob_discarded(self):
        settings = get_settings()
        blob = {"facts": ["y" * (2 * _MAX_MEMORY_BLOB_BYTES)]}
        assert clamp_memory_blob(blob, settings) is None

    def test_input_not_mutated(self):
        settings = get_settings()
        cap = settings.conversation_memory_max_ledger
        ledger = [{"sid": f"s{i}"} for i in range(cap + 5)]
        blob = {"source_ledger": ledger}
        clamp_memory_blob(blob, settings)
        assert len(blob["source_ledger"]) == cap + 5


# ---------------------------------------------------------------------------
# is_memory_answerable pre-gate
# ---------------------------------------------------------------------------

class TestMemoryFastPathPreGate:
    async def test_ledger_only_memory_skips_classifier(self):
        """A blob with only a source ledger must return False WITHOUT an LLM
        call — the autouse mock_llm fixture raises if one is attempted."""
        settings = get_settings()
        memory = {
            "source_ledger": [{"sid": "s1", "filename": "f", "gist": "g"}]
        }
        assert await is_memory_answerable("summarize that", memory, settings) is False

    async def test_empty_memory_returns_false(self):
        settings = get_settings()
        assert await is_memory_answerable("hi", None, settings) is False
        assert await is_memory_answerable("hi", {}, settings) is False

    async def test_disabled_flag_returns_false(self):
        settings = get_settings()
        old = settings.enable_memory_fast_path
        settings.enable_memory_fast_path = False
        try:
            memory = {"transcript": {"summary": "we discussed X"}}
            assert await is_memory_answerable("why?", memory, settings) is False
        finally:
            settings.enable_memory_fast_path = old


# ---------------------------------------------------------------------------
# find_similar_entities fulltext prefilter
# ---------------------------------------------------------------------------

def _service_with_session():
    svc = Neo4jService()
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    svc._driver = driver
    return svc, session


class TestDedupPrefilter:
    def test_flag_off_uses_full_scan(self):
        svc, session = _service_with_session()
        svc.settings.entity_dedup_prefilter = False
        session.run.return_value = iter([])
        svc.find_similar_entities("Acme")
        cypher = session.run.call_args.args[0]
        assert "MATCH (e:Entity)" in cypher
        assert "db.index.fulltext.queryNodes" not in cypher

    def test_flag_on_uses_fulltext_prefilter(self):
        svc, session = _service_with_session()
        svc.settings.entity_dedup_prefilter = True
        try:
            session.run.return_value = iter([])
            svc.find_similar_entities("Acme Corp")
            cypher = session.run.call_args.args[0]
            assert "entity_name_fulltext" in cypher
            assert "levenshteinSimilarity" in cypher
            assert session.run.call_args.kwargs["query"] == "Acme OR Corp"
        finally:
            svc.settings.entity_dedup_prefilter = False

    def test_prefilter_failure_falls_back_to_full_scan(self):
        svc, session = _service_with_session()
        svc.settings.entity_dedup_prefilter = True
        try:
            calls = []

            def _run(cypher, **params):
                calls.append(cypher)
                if "entity_name_fulltext" in cypher:
                    raise RuntimeError("fulltext index missing")
                return iter([])

            session.run.side_effect = _run
            result = svc.find_similar_entities("Acme")
            assert result == []
            assert len(calls) == 2  # prefilter attempt + full-scan fallback
            assert "MATCH (e:Entity)" in calls[1]
        finally:
            svc.settings.entity_dedup_prefilter = False


# ---------------------------------------------------------------------------
# Entity embedding cache
# ---------------------------------------------------------------------------

class TestEntityEmbeddingCache:
    async def test_cache_hit_skips_api_and_miss_populates(self):
        from app.services.graph_extractor import GraphExtractor

        extractor = GraphExtractor()
        # embed_api_key is a property deriving from embedding_api_key/openai_api_key
        extractor.settings.embedding_api_key = "test-key"

        fake_client = MagicMock()
        embedded_inputs = []

        async def _create(**kwargs):
            embedded_inputs.extend(kwargs["input"])
            resp = MagicMock()
            resp.data = [
                MagicMock(embedding=[float(i)]) for i in range(len(kwargs["input"]))
            ]
            return resp

        fake_client.embeddings.create = AsyncMock(side_effect=_create)
        extractor._async_embed_client = fake_client

        cached_entity = Entity(name="Neo4j", type="Technology", description="")
        fresh_entity = Entity(name="Cortex", type="System", description="")
        cache = {("neo4j", "Technology"): [9.9]}

        try:
            results = await extractor.generate_entity_embeddings_batch_async(
                [cached_entity, fresh_entity], cache=cache
            )
        finally:
            extractor.settings.embedding_api_key = ""

        assert results[0] == [9.9]  # served from cache
        assert results[1] == [0.0]  # embedded fresh
        assert embedded_inputs == ["Cortex (System)"]  # only the miss hit the API
        assert cache[("cortex", "System")] == [0.0]  # miss populated the cache

    async def test_no_cache_behaves_as_before(self):
        from app.services.graph_extractor import GraphExtractor

        extractor = GraphExtractor()
        extractor.settings.embedding_api_key = "test-key"
        fake_client = MagicMock()

        async def _create(**kwargs):
            resp = MagicMock()
            resp.data = [
                MagicMock(embedding=[1.0]) for _ in kwargs["input"]
            ]
            return resp

        fake_client.embeddings.create = AsyncMock(side_effect=_create)
        extractor._async_embed_client = fake_client

        try:
            results = await extractor.generate_entity_embeddings_batch_async(
                [Entity(name="A", type="Concept", description="")]
            )
        finally:
            extractor.settings.embedding_api_key = ""
        assert results == [[1.0]]
