"""Characterization tests for entity-storage resolution semantics.

`store_entity_with_resolution` and `store_entity_with_embedding` define the
dedup/merge contract that the batched writer (enable_batched_kg_writes) must
preserve: which entity wins (canonical), when aliases are added, how document
provenance is updated, and what a brand-new entity's MERGE sets.

The Neo4j driver is mocked; assertions are on returned canonical names and on
the Cypher/parameters issued.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models import Entity
from app.services.neo4j_service import Neo4jService


def _make_service():
    """Neo4jService with a mocked driver whose sessions record run() calls."""
    svc = Neo4jService()
    driver = MagicMock()
    session = MagicMock()
    # `with svc.driver.session() as session:` → our session mock
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    svc._driver = driver
    return svc, session


def _run_calls(session):
    """All (cypher, params) pairs issued through session.run."""
    return [
        (" ".join(call.args[0].split()), call.kwargs)
        for call in session.run.call_args_list
    ]


# ---------------------------------------------------------------------------
# store_entity_with_resolution (Levenshtein path)
# ---------------------------------------------------------------------------

class TestStoreEntityWithResolution:
    def test_merges_into_similar_entity_and_adds_alias(self, monkeypatch):
        svc, session = _make_service()
        monkeypatch.setattr(
            svc, "find_similar_entities",
            lambda name, threshold=0.85: [
                {"name": "Neo4j", "type": "Technology",
                 "description": "", "similarity": 0.92}
            ],
        )
        alias_calls = []
        monkeypatch.setattr(
            svc, "_add_entity_alias",
            lambda canonical, alias: alias_calls.append((canonical, alias)),
        )

        canonical = svc.store_entity_with_resolution(
            Entity(name="Neo4J db", type="Technology", description="x"),
            chunk_id="chunk-1",
            document_id="doc-1",
        )

        assert canonical == "Neo4j"
        assert alias_calls == [("Neo4j", "Neo4J db")]

        calls = _run_calls(session)
        # chunk link
        assert any(
            "MERGE (c)-[:MENTIONS]->(e)" in cypher
            and params.get("chunk_id") == "chunk-1"
            and params.get("name") == "Neo4j"
            for cypher, params in calls
        )
        # provenance update
        assert any(
            "e.extraction_count = coalesce(e.extraction_count, 0) + 1" in cypher
            and params.get("doc_id") == "doc-1"
            for cypher, params in calls
        )

    def test_same_name_match_does_not_add_alias(self, monkeypatch):
        svc, session = _make_service()
        monkeypatch.setattr(
            svc, "find_similar_entities",
            lambda name, threshold=0.85: [
                {"name": "Neo4j", "type": "Technology",
                 "description": "", "similarity": 1.0}
            ],
        )
        alias_calls = []
        monkeypatch.setattr(
            svc, "_add_entity_alias",
            lambda canonical, alias: alias_calls.append((canonical, alias)),
        )

        canonical = svc.store_entity_with_resolution(
            Entity(name="neo4j", type="Technology", description="")
        )
        assert canonical == "Neo4j"
        assert alias_calls == []  # case-insensitive same name → no alias

    def test_no_similar_creates_new_via_store_entity(self, monkeypatch):
        svc, _session = _make_service()
        monkeypatch.setattr(
            svc, "find_similar_entities", lambda name, threshold=0.85: []
        )
        store_calls = []
        monkeypatch.setattr(
            svc, "store_entity",
            lambda entity, chunk_id=None, document_id=None: (
                store_calls.append((entity.name, chunk_id, document_id))
                or entity.name
            ),
        )

        canonical = svc.store_entity_with_resolution(
            Entity(name="Brand New", type="Concept", description=""),
            chunk_id="c9",
            document_id="d9",
        )
        assert canonical == "Brand New"
        assert store_calls == [("Brand New", "c9", "d9")]

    def test_below_threshold_match_creates_new(self, monkeypatch):
        svc, _session = _make_service()
        monkeypatch.setattr(
            svc, "find_similar_entities",
            lambda name, threshold=0.85: [
                {"name": "Other", "type": "Concept",
                 "description": "", "similarity": 0.70}
            ],
        )
        monkeypatch.setattr(
            svc, "store_entity",
            lambda entity, chunk_id=None, document_id=None: entity.name,
        )
        canonical = svc.store_entity_with_resolution(
            Entity(name="Fresh", type="Concept", description="")
        )
        assert canonical == "Fresh"


# ---------------------------------------------------------------------------
# store_entity_with_embedding (semantic path + Levenshtein backup + create)
# ---------------------------------------------------------------------------

class TestStoreEntityWithEmbedding:
    def test_embedding_match_merges_and_returns_not_new(self, monkeypatch):
        svc, session = _make_service()
        svc.settings.enable_semantic_entity_resolution = True
        monkeypatch.setattr(
            svc, "find_similar_entities_by_embedding",
            lambda emb, threshold: [{"name": "Canonical", "similarity": 0.95}],
        )
        alias_calls = []
        monkeypatch.setattr(
            svc, "_add_entity_alias",
            lambda canonical, alias: alias_calls.append((canonical, alias)),
        )

        name, is_new = svc.store_entity_with_embedding(
            Entity(name="Variant", type="Concept", description=""),
            chunk_id="c1",
            document_id="d1",
            embedding=[0.1, 0.2],
        )
        assert (name, is_new) == ("Canonical", False)
        assert alias_calls == [("Canonical", "Variant")]
        calls = _run_calls(session)
        assert any(
            "MERGE (c)-[:MENTIONS]->(e)" in cypher
            and params.get("name") == "Canonical"
            for cypher, params in calls
        )
        assert any(
            "e.extraction_count = coalesce(e.extraction_count, 0) + 1" in cypher
            for cypher, _ in calls
        )

    def test_levenshtein_backup_when_no_embedding_match(self, monkeypatch):
        svc, _session = _make_service()
        svc.settings.enable_semantic_entity_resolution = True
        monkeypatch.setattr(
            svc, "find_similar_entities_by_embedding",
            lambda emb, threshold: [],
        )
        monkeypatch.setattr(
            svc, "find_similar_entities",
            lambda name, threshold=0.85: [
                {"name": "Typo Target", "type": "Concept",
                 "description": "", "similarity": 0.9}
            ],
        )
        monkeypatch.setattr(svc, "_add_entity_alias", lambda *a: None)

        name, is_new = svc.store_entity_with_embedding(
            Entity(name="Typo Targt", type="Concept", description=""),
            embedding=[0.3],
        )
        assert (name, is_new) == ("Typo Target", False)

    def test_new_entity_created_with_expected_set_semantics(self, monkeypatch):
        svc, session = _make_service()
        svc.settings.enable_semantic_entity_resolution = True
        monkeypatch.setattr(
            svc, "find_similar_entities_by_embedding",
            lambda emb, threshold: [],
        )
        monkeypatch.setattr(
            svc, "find_similar_entities", lambda name, threshold=0.85: []
        )
        record = {"name": "NewEnt"}
        session.run.return_value.single.return_value = record

        name, is_new = svc.store_entity_with_embedding(
            Entity(name="NewEnt", type="Concept", description="d"),
            chunk_id="c2",
            document_id="d2",
            embedding=[0.5],
        )
        assert (name, is_new) == ("NewEnt", True)

        cypher, params = _run_calls(session)[-1]
        # The MERGE contract the batched writer must reproduce:
        assert "MERGE (e:Entity {name: $name})" in cypher
        assert "e.extraction_count = 1" in cypher  # ON CREATE
        assert (
            "e.type = CASE WHEN e.type IS NULL OR e.type = '' "
            "THEN $type ELSE e.type END" in cypher
        )  # ON MATCH keeps existing type
        assert (
            "size(coalesce(e.description, '')) < size($description)" in cypher
        )  # longer description wins
        assert (
            "e.embedding = CASE WHEN e.embedding IS NULL "
            "THEN $embedding ELSE e.embedding END" in cypher
        )  # first embedding wins
        assert "MERGE (c)-[:MENTIONS]->(e)" in cypher  # chunk link inline
        assert params["chunk_id"] == "c2"
        assert params["doc_id"] == "d2"

    def test_semantic_resolution_disabled_skips_embedding_lookup(self, monkeypatch):
        svc, session = _make_service()
        svc.settings.enable_semantic_entity_resolution = False

        def _boom(*a, **k):
            raise AssertionError("embedding lookup must not run when disabled")

        monkeypatch.setattr(svc, "find_similar_entities_by_embedding", _boom)
        monkeypatch.setattr(
            svc, "find_similar_entities", lambda name, threshold=0.85: []
        )
        session.run.return_value.single.return_value = {"name": "X"}

        name, is_new = svc.store_entity_with_embedding(
            Entity(name="X", type="Concept", description=""),
            embedding=[0.1],
        )
        assert (name, is_new) == ("X", True)

    def test_numpy_like_embedding_converted_to_list(self, monkeypatch):
        svc, session = _make_service()
        svc.settings.enable_semantic_entity_resolution = False
        monkeypatch.setattr(
            svc, "find_similar_entities", lambda name, threshold=0.85: []
        )
        session.run.return_value.single.return_value = {"name": "X"}

        class FakeArray:
            def tolist(self):
                return [1.0, 2.0]

        svc.store_entity_with_embedding(
            Entity(name="X", type="Concept", description=""),
            embedding=FakeArray(),
        )
        _, params = _run_calls(session)[-1]
        assert params["embedding"] == [1.0, 2.0]
