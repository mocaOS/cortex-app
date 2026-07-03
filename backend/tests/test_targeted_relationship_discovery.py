"""Tests for targeted Phase B discovery (_analyze_relationships_targeted)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.models import Relationship
from app.services.document_processor import DocumentProcessor


def _entities(n):
    return [
        {"name": f"Entity {i}", "type": "Concept", "description": f"desc {i}"}
        for i in range(n)
    ]


def _processor(settings):
    """Bare DocumentProcessor with only the attributes the targeted path uses."""
    proc = DocumentProcessor.__new__(DocumentProcessor)
    proc.settings = settings
    proc.neo4j = MagicMock()
    proc.graph_extractor = MagicMock()
    return proc


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "relationship_discovery_mode", "targeted")
    monkeypatch.setattr(s, "embedding_api_key", "test-embed-key")
    monkeypatch.setattr(s, "relationship_pairs_per_call", 2)
    monkeypatch.setattr(s, "relationship_pair_context_tokens", 1000)
    monkeypatch.setattr(s, "relationship_max_per_entity", 50)
    monkeypatch.setattr(s, "relationship_max_hours", 0.0)
    return s


class TestDispatch:
    async def test_default_mode_routes_to_targeted(self, settings):
        proc = _processor(settings)
        proc.neo4j.get_all_entities_for_collection.return_value = _entities(3)
        proc._analyze_relationships_targeted = AsyncMock(return_value={"ok": True})

        result = await proc.analyze_collection_relationships(collection_id=None)

        proc._analyze_relationships_targeted.assert_awaited_once()
        assert result == {"ok": True}
        # Legacy path (co-occurrence batching) must not run
        proc.neo4j.get_entity_co_occurrence.assert_not_called()

    async def test_llm_scan_mode_skips_targeted(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "relationship_discovery_mode", "llm_scan")
        proc = _processor(settings)
        proc.neo4j.get_all_entities_for_collection.return_value = []
        proc._analyze_relationships_targeted = AsyncMock()

        # Empty entity set returns early either way; the point is the mode
        # value alone must not raise and must not call the targeted path.
        result = await proc.analyze_collection_relationships(collection_id=None)
        proc._analyze_relationships_targeted.assert_not_awaited()
        assert result["relationships_stored"] == 0


class TestTargetedFlow:
    async def test_full_flow_stores_verified_pairs(self, settings):
        proc = _processor(settings)
        entities = _entities(4)

        # Embedding backfill: one entity missing a vector
        proc.neo4j.get_entities_missing_embedding.return_value = [entities[0]]
        proc.graph_extractor.generate_entity_embeddings_batch_async = AsyncMock(
            return_value=[[0.1, 0.2]]
        )
        proc.neo4j.set_entity_embeddings_bulk.return_value = 1

        # Candidate generators
        proc.neo4j.get_knn_candidate_pairs.return_value = [
            ("Entity 0", "Entity 1", 0.92),
            ("Entity 0", "NotInScope", 0.95),  # must be filtered out
        ]
        proc.neo4j.get_doc_cooccurrence_pairs.return_value = [
            ("Entity 2", "Entity 3", 3),
        ]
        proc.neo4j.get_entity_degree_map.return_value = {}
        proc.neo4j.get_chunk_context_for_entities.return_value = "some context"
        proc.neo4j.store_relationship.return_value = True
        proc.neo4j.get_relationship_count.return_value = 10

        verified = [
            Relationship(
                source="Entity 0", target="Entity 1",
                relationship_type="RELATED_TO", description="", confidence=0.9,
            ),
            Relationship(
                source="Entity 2", target="Entity 3",
                relationship_type="PART_OF", description="", confidence=0.3,  # filtered
            ),
        ]
        proc.graph_extractor.analyze_relationships_async = AsyncMock(
            return_value=verified
        )

        result = await proc._analyze_relationships_targeted(
            entities, collection_id=None
        )

        # Embedding backfill persisted
        proc.neo4j.set_entity_embeddings_bulk.assert_called_once()

        # LLM verification received candidate pairs (2 in-scope pairs, 2/call → 1 call)
        assert proc.graph_extractor.analyze_relationships_async.await_count == 1
        call = proc.graph_extractor.analyze_relationships_async.await_args
        assert call.kwargs["candidate_pairs"] or call.args[-1]

        # Only the confident relationship stored, tagged cross_collection
        assert proc.neo4j.store_relationship.call_count == 1
        store_args = proc.neo4j.store_relationship.call_args.args
        assert store_args[0].source == "Entity 0"
        assert store_args[2] == "cross_collection"

        assert result["discovery_mode"] == "targeted"
        assert result["relationships_stored"] == 1
        assert result["relationships_discovered"] == 2
        assert result["candidate_pairs"] == 2
        assert result["rounds_completed"] == 1

    async def test_no_candidates_short_circuits_llm(self, settings):
        proc = _processor(settings)
        entities = _entities(3)
        proc.neo4j.get_entities_missing_embedding.return_value = []
        proc.neo4j.get_knn_candidate_pairs.return_value = []
        proc.neo4j.get_doc_cooccurrence_pairs.return_value = []
        proc.neo4j.get_relationship_count.return_value = 7
        proc.graph_extractor.analyze_relationships_async = AsyncMock()

        result = await proc._analyze_relationships_targeted(
            entities, collection_id=None
        )

        proc.graph_extractor.analyze_relationships_async.assert_not_awaited()
        assert result["relationships_stored"] == 0
        assert result["candidate_pairs"] == 0

    async def test_generator_failure_degrades_gracefully(self, settings):
        proc = _processor(settings)
        entities = _entities(3)
        proc.neo4j.get_entities_missing_embedding.return_value = []
        proc.neo4j.get_knn_candidate_pairs.side_effect = RuntimeError("index missing")
        proc.neo4j.get_doc_cooccurrence_pairs.return_value = [
            ("Entity 0", "Entity 1", 2),
        ]
        proc.neo4j.get_entity_degree_map.return_value = {}
        proc.neo4j.get_chunk_context_for_entities.return_value = ""
        proc.neo4j.store_relationship.return_value = True
        proc.neo4j.get_relationship_count.return_value = 1
        proc.graph_extractor.analyze_relationships_async = AsyncMock(
            return_value=[
                Relationship(
                    source="Entity 0", target="Entity 1",
                    relationship_type="RELATED_TO", description="",
                )
            ]
        )

        result = await proc._analyze_relationships_targeted(
            entities, collection_id=None
        )
        assert result["relationships_stored"] == 1

    async def test_degree_cap_skips_saturated_pairs(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "relationship_max_per_entity", 5)
        proc = _processor(settings)
        entities = _entities(2)
        proc.neo4j.get_entities_missing_embedding.return_value = []
        proc.neo4j.get_knn_candidate_pairs.return_value = [
            ("Entity 0", "Entity 1", 0.9),
        ]
        proc.neo4j.get_doc_cooccurrence_pairs.return_value = []
        proc.neo4j.get_entity_degree_map.return_value = {"Entity 0": 5, "Entity 1": 5}
        proc.neo4j.get_chunk_context_for_entities.return_value = ""
        proc.neo4j.get_relationship_count.return_value = 0
        proc.graph_extractor.analyze_relationships_async = AsyncMock(
            return_value=[
                Relationship(
                    source="Entity 0", target="Entity 1",
                    relationship_type="RELATED_TO", description="",
                )
            ]
        )

        result = await proc._analyze_relationships_targeted(
            entities, collection_id=None
        )
        proc.neo4j.store_relationship.assert_not_called()
        assert result["relationships_discovered"] == 1
        assert result["relationships_stored"] == 0
