"""Parity tests for the batched KG write path (enable_batched_kg_writes).

The batched pipeline (resolve → cluster → UNWIND write) must produce the same
canonical-name decisions as the sequential per-entity path, and its Cypher
must carry the same SET semantics the characterization tests in
test_entity_resolution.py lock for the per-item methods.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.models import Entity, Relationship
from app.services.neo4j_service import Neo4jService


def _make_processor(neo4j_mock):
    """DocumentProcessor stub: skip __init__ (haystack/embedder setup) and
    wire only what _store_entities_batched touches."""
    from app.services.document_processor import DocumentProcessor

    proc = object.__new__(DocumentProcessor)
    proc.settings = get_settings()
    proc.neo4j = neo4j_mock
    proc._check_cancellation = lambda doc_id: None
    return proc


def _entity(name, etype="Concept", desc=""):
    return Entity(name=name, type=etype, description=desc)


# ---------------------------------------------------------------------------
# _store_entities_batched: canonical-map parity
# ---------------------------------------------------------------------------

class TestStoreEntitiesBatched:
    async def test_resolve_cluster_write_pipeline(self):
        neo4j = MagicMock()
        # idx 2 ("Acme") resolves to an existing graph entity via embedding
        neo4j.resolve_entities_batch_by_embedding.return_value = {
            2: {"name": "ACME Corp", "similarity": 0.95}
        }
        neo4j.resolve_entities_batch_by_name.return_value = {}
        proc = _make_processor(neo4j)
        proc.settings.enable_semantic_entity_resolution = True

        entities = [
            _entity("Neo4j", "Technology"),
            _entity("Neo4J db", "Technology"),   # clusters with Neo4j
            _entity("Acme", "Organization"),     # resolves to ACME Corp
            _entity("Widget", "Product"),        # new + unique
        ]
        embeddings = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]

        loop = asyncio.get_event_loop()
        canonical_map = await proc._store_entities_batched(
            "doc-1", entities, embeddings, loop
        )

        assert canonical_map == {
            "neo4j": "Neo4j",
            "neo4j db": "Neo4j",       # first occurrence wins the cluster
            "acme": "ACME Corp",       # merged into existing graph entity
            "widget": "Widget",
        }

        # New entities written once, with their embeddings + provenance
        new_rows = neo4j.store_entities_batch.call_args.args[0]
        assert [r["name"] for r in new_rows] == ["Neo4j", "Widget"]
        assert all(r["doc_id"] == "doc-1" for r in new_rows)
        assert new_rows[0]["embedding"] == [1.0, 0.0]

        # Merges carry aliases when names differ
        merge_rows = neo4j.apply_entity_merges_batch.call_args.args[0]
        by_canonical = {r["canonical"]: r for r in merge_rows}
        assert by_canonical["ACME Corp"]["alias"] == "Acme"
        assert by_canonical["Neo4j"]["alias"] == "Neo4J db"

    async def test_same_name_merge_has_no_alias(self):
        neo4j = MagicMock()
        neo4j.resolve_entities_batch_by_embedding.return_value = {}
        neo4j.resolve_entities_batch_by_name.return_value = {
            0: {"name": "neo4j", "similarity": 1.0}
        }
        proc = _make_processor(neo4j)

        loop = asyncio.get_event_loop()
        canonical_map = await proc._store_entities_batched(
            "doc-1", [_entity("Neo4j")], [[1.0]], loop
        )
        assert canonical_map == {"neo4j": "neo4j"}
        merge_rows = neo4j.apply_entity_merges_batch.call_args.args[0]
        # case-insensitive same name → no alias (matches per-item path)
        assert merge_rows[0]["alias"] is None

    async def test_semantic_disabled_skips_embedding_resolution(self):
        neo4j = MagicMock()
        neo4j.resolve_entities_batch_by_name.return_value = {}
        proc = _make_processor(neo4j)
        proc.settings.enable_semantic_entity_resolution = False
        try:
            loop = asyncio.get_event_loop()
            await proc._store_entities_batched(
                "doc-1", [_entity("X")], [[0.1]], loop
            )
            neo4j.resolve_entities_batch_by_embedding.assert_not_called()
        finally:
            proc.settings.enable_semantic_entity_resolution = True

    async def test_levenshtein_clusters_without_embeddings(self):
        neo4j = MagicMock()
        neo4j.resolve_entities_batch_by_embedding.return_value = {}
        neo4j.resolve_entities_batch_by_name.return_value = {}
        proc = _make_processor(neo4j)

        loop = asyncio.get_event_loop()
        canonical_map = await proc._store_entities_batched(
            "doc-1",
            [_entity("Kubernetes"), _entity("Kubernetess"), _entity("Redis")],
            None,
            loop,
        )
        assert canonical_map["kubernetes"] == "Kubernetes"
        assert canonical_map["kubernetess"] == "Kubernetes"  # typo clusters
        assert canonical_map["redis"] == "Redis"


# ---------------------------------------------------------------------------
# Cypher contract of the batch writers
# ---------------------------------------------------------------------------

def _service_with_session():
    svc = Neo4jService()
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    svc._driver = driver
    return svc, session


class TestBatchWriterCypher:
    def test_store_entities_batch_set_semantics(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {"stored": 1}
        svc.store_entities_batch([
            {"name": "X", "type": "Concept", "description": "d",
             "embedding": None, "doc_id": "doc-1"}
        ])
        cypher = " ".join(session.run.call_args.args[0].split())
        # Same contract clauses test_entity_resolution.py locks per-item:
        assert "MERGE (e:Entity {name: row.name})" in cypher
        assert "e.extraction_count = 1" in cypher
        assert (
            "e.type = CASE WHEN e.type IS NULL OR e.type = '' "
            "THEN row.type ELSE e.type END" in cypher
        )
        assert "size(coalesce(e.description, '')) < size(row.description)" in cypher
        assert (
            "e.embedding = CASE WHEN e.embedding IS NULL "
            "OR (row.embedding IS NOT NULL AND size(e.embedding) <> size(row.embedding)) "
            "THEN row.embedding ELSE e.embedding END" in cypher
        )  # first embedding wins, except a stale wrong-dimension one is replaced

    def test_apply_entity_merges_batch_alias_and_provenance(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {"merged": 1}
        svc.apply_entity_merges_batch([
            {"canonical": "X", "alias": "x variant", "doc_id": "doc-1"}
        ])
        cypher = " ".join(session.run.call_args.args[0].split())
        assert "MATCH (e:Entity {name: row.canonical})" in cypher
        assert "WHEN row.alias IS NULL THEN e.aliases" in cypher
        assert "WHEN NOT row.alias IN e.aliases THEN e.aliases + row.alias" in cypher
        assert "e.extraction_count = coalesce(e.extraction_count, 0) + 1" in cypher

    def test_link_entities_to_chunks_batch_chunked_tx(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {"linked": 1}
        pairs = [{"chunk_id": f"c{i}", "entity_name": "E"} for i in range(2500)]
        svc.link_entities_to_chunks_batch(pairs, tx_size=1000)
        assert session.run.call_count == 3  # 1000 + 1000 + 500

    def test_store_relationships_batch_skips_self_refs(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {"stored": 1}
        stored = svc.store_relationships_batch([
            Relationship(source="A", target="a", relationship_type="USES",
                         description="", weight=5.0),
            Relationship(source="A", target="B", relationship_type="USES",
                         description="", weight=5.0),
        ])
        rows = session.run.call_args.kwargs["rows"]
        assert len(rows) == 1  # self-ref filtered before the DB call
        assert rows[0]["source"] == "A" and rows[0]["target"] == "B"
        assert stored == 1

    def test_store_relationships_batch_falls_back_per_item(self, monkeypatch):
        svc, session = _service_with_session()
        session.run.side_effect = RuntimeError("no apoc")
        fallback_calls = []
        monkeypatch.setattr(
            svc, "store_relationship",
            lambda rel, **kw: fallback_calls.append(rel.source) or True,
        )
        stored = svc.store_relationships_batch([
            Relationship(source="A", target="B", relationship_type="USES",
                         description="", weight=5.0),
            Relationship(source="C", target="D", relationship_type="USES",
                         description="", weight=5.0),
        ])
        assert fallback_calls == ["A", "C"]
        assert stored == 2

    def test_resolve_batch_by_embedding_failure_returns_empty(self):
        svc, session = _service_with_session()
        session.run.side_effect = RuntimeError("index gone")
        out = svc.resolve_entities_batch_by_embedding([(0, [0.1])], 0.85)
        assert out == {}
        assert svc._vector_search_failures == 1

    def test_store_relationships_batch_carries_confidence(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {"stored": 1}
        svc.store_relationships_batch([
            Relationship(source="A", target="B", relationship_type="USES",
                         description="d", weight=5.0, confidence=0.8),
        ])
        rows = session.run.call_args.kwargs["rows"]
        assert rows[0]["confidence"] == 0.8
        assert "confidence" in session.run.call_args.args[0]

    def test_bulk_deletes_run_in_transactions(self):
        svc, session = _service_with_session()
        session.run.return_value.single.return_value = {"deleted": 0}
        svc.delete_all_entities()
        assert "IN TRANSACTIONS" in session.run.call_args.args[0]
        svc.delete_all_relationships()
        assert "IN TRANSACTIONS" in session.run.call_args.args[0]
        svc.delete_batch_relationships()
        assert "IN TRANSACTIONS" in session.run.call_args.args[0]

    def test_knn_batch_failure_skips_not_raises(self):
        svc, session = _service_with_session()
        session.run.side_effect = RuntimeError("query vector has 4096 dimensions")
        pairs = svc.get_knn_candidate_pairs(["A", "B"])
        assert pairs == []

    def test_knn_query_guards_embedding_dimension(self):
        svc, session = _service_with_session()
        session.run.return_value = []
        svc.get_knn_candidate_pairs(["A"])
        query = session.run.call_args.args[0]
        assert "size(e.embedding) = $dim" in query
        assert session.run.call_args.kwargs["dim"] == svc.settings.embedding_dimension

    def test_missing_embedding_includes_wrong_dimension(self):
        svc, session = _service_with_session()
        session.run.return_value = []
        svc.get_entities_missing_embedding(["A"])
        query = session.run.call_args.args[0]
        assert "size(e.embedding) <> $dim" in query
