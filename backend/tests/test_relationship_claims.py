"""Claim-set provenance on Entity→Entity relationships.

An edge is MERGEd on (source, type, target), so several documents/chunks can
assert it. Regression: the single-valued `source_document_id` was last-writer-
wins, so reprocessing doc B deleted an edge doc A also asserted. These tests
pin the Cypher contract (hermetic — Neo4j is mocked) and the library-transfer
handling of claim lists + retired entities. Semantics were additionally
verified against a disposable Neo4j 5.26 + APOC (see test_claims_live below,
skipped unless CORTEX_CLAIMS_NEO4J_URI is set).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from app.models import Relationship
from app.services import neo4j_service as ns
from app.services.document_processor import _group_rel_claims


def _svc():
    svc = object.__new__(ns.Neo4jService)
    session = MagicMock()
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    svc._driver = driver
    return svc, session


def _rel(src="A", tgt="B"):
    return Relationship(source=src, target=tgt, relationship_type="USES")


# --- store paths -------------------------------------------------------------

def test_claim_append_reads_legacy_claim_before_overwrite():
    cy = ns._claim_append_cypher("rel", "$d", "$c")
    assert cy.startswith("SET rel.source_document_ids")
    assert "rel.source_document_id IS NULL THEN [] ELSE [rel.source_document_id]" in cy


def test_store_relationship_appends_claim_then_sets_legacy():
    svc, session = _svc()
    svc.store_relationship(_rel(), source_document_id="doc1",
                           extraction_method="per_chunk", source_chunk_ids=["doc1_chunk_0"])
    query, kwargs = session.run.call_args.args[0], session.run.call_args.kwargs
    claim = query.index("rel.source_document_ids =")
    legacy = query.index("rel.source_document_id = coalesce(")
    assert claim < legacy  # legacy-init reads the previous claimant first
    assert kwargs["source_doc_id"] == "doc1"
    assert kwargs["source_chunk_ids"] == ["doc1_chunk_0"]


def test_store_relationships_batch_carries_chunk_claims_per_row():
    svc, session = _svc()
    session.run.return_value.single.return_value = {"stored": 1}
    svc.store_relationships_batch([_rel()], source_document_id="doc1",
                                  source_chunk_ids=["doc1_chunk_2", "doc1_chunk_3"])
    query, kwargs = session.run.call_args.args[0], session.run.call_args.kwargs
    assert "row.source_chunk_ids" in query
    assert kwargs["rows"][0]["source_chunk_ids"] == ["doc1_chunk_2", "doc1_chunk_3"]


def test_store_relationships_batch_per_rel_chunk_claims():
    svc, session = _svc()
    session.run.return_value.single.return_value = {"stored": 2}
    svc.store_relationships_batch([_rel("A", "B"), _rel("A", "C")], source_document_id="d",
                                  chunk_ids_per_rel=[["d_chunk_1"], ["d_chunk_2", "d_chunk_3"]])
    rows = session.run.call_args.kwargs["rows"]
    assert [r["source_chunk_ids"] for r in rows] == [["d_chunk_1"], ["d_chunk_2", "d_chunk_3"]]
    with pytest.raises(ValueError):
        svc.store_relationships_batch([_rel()], chunk_ids_per_rel=[])


def test_group_rel_claims_attributes_each_rel_to_its_own_chunks():
    """A 4-chunk batched call must not stamp every relationship with all 4 ids."""
    seen: set = set()
    pairs = [("c1", _rel("A", "B")), ("c2", _rel("A", "C")), ("c3", _rel("a", "B")),
             ("c4", _rel("X", "Y"))]
    new, new_claims, only, only_claims = _group_rel_claims(pairs, {}, seen)
    assert [(r.source, r.target) for r in new] == [("A", "B"), ("A", "C"), ("X", "Y")]
    assert new_claims == [["c1", "c3"], ["c2"], ["c4"]]
    assert only == [] and only_claims == []
    # next unit re-finds A→B in c7: claim-only, claimed by c7 alone
    new, new_claims, only, only_claims = _group_rel_claims(
        [("c7", _rel("A", "B")), ("c8", _rel("B", "Z"))], {}, seen)
    assert [(r.source, r.target) for r in only] == [("A", "B")] and only_claims == [["c7"]]
    assert new_claims == [["c8"]]


def test_group_rel_claims_remaps_to_canonical_names():
    new, _, _, _ = _group_rel_claims([("c1", _rel("acme inc", "B"))], {"acme inc": "ACME"}, set())
    assert new[0].source == "ACME"


def test_cross_collection_write_marks_step2_claim():
    cy = ns._claim_append_cypher("rel", "$d", "$c")
    assert "rel.step2_verified = CASE WHEN $d IS NULL AND $extraction_method <> 'per_chunk' THEN true" in cy


def test_release_paths_keep_step2_verified_edges():
    for q in (ns._RELEASE_DOCUMENT_CLAIMS, ns._REMOVE_CHUNK_CLAIMS):
        assert "coalesce(r.step2_verified, false) AS verified" in q
        assert "AND NOT verified) AS gone" in q
        assert "r.source_document_id = null" in q  # kept edge's doc claims cleared


def test_rebuild_drops_step2_claim_not_doc_claimed_edges():
    svc, session = _svc()
    session.run.return_value.single.return_value = {"deleted": 0}
    svc.delete_batch_relationships()
    queries = [c.args[0] for c in session.run.call_args_list]
    assert any("REMOVE r.step2_verified" in q for q in queries)


def test_store_graph_extraction_claims_its_chunk(monkeypatch):
    svc, _ = _svc()
    svc.settings = MagicMock(enable_semantic_entity_resolution=False)
    calls = []
    monkeypatch.setattr(svc, "store_relationship", lambda *a, **k: calls.append(k) or True)
    from app.models import ExtractionResult
    svc.store_graph_extraction("doc1_image_1000000", ExtractionResult(entities=[], relationships=[_rel()]),
                               source_document_id="doc1")
    assert calls[0]["source_chunk_ids"] == ["doc1_image_1000000"]


# --- removal paths -----------------------------------------------------------

@pytest.mark.parametrize("method", ["delete_document_chunks", "delete_document"])
def test_document_removal_releases_claims_instead_of_deleting(method):
    svc, session = _svc()
    session.run.return_value.single.return_value = {"deleted": 0, "released": 0,
                                                     "orphaned_entities": [], "chunks_deleted": 0}
    try:
        getattr(svc, method)("doc1")
    except Exception:
        pass  # later steps may need richer mocks; only Step 0 matters here
    step0 = session.run.call_args_list[0]
    assert step0.args[0] is ns._RELEASE_DOCUMENT_CLAIMS
    assert step0.kwargs == {"id": "doc1"}


def test_release_query_deletes_only_unclaimed_edges():
    q = ns._RELEASE_DOCUMENT_CLAIMS
    assert "(size(docs) = 0 AND NOT verified) AS gone" in q
    assert "CASE WHEN gone THEN [1] ELSE [] END | DELETE r" in q
    assert "r.source_document_id = last(docs)" in q
    assert "NOT c STARTS WITH ($id + '_')" in q
    assert "RetiredEntity" in q  # edges to retired entities keep their claims


def test_delete_relationships_by_source_document_uses_claims():
    svc, session = _svc()
    session.run.return_value.single.return_value = {"deleted": 3, "released": 2}
    assert svc.delete_relationships_by_source_document("doc1") == 3
    assert session.run.call_args.args[0] is ns._RELEASE_DOCUMENT_CLAIMS


def test_remove_chunk_claims_contract():
    svc, session = _svc()
    session.run.return_value.single.return_value = {"claims_removed": 4, "relationships_deleted": 1}
    out = svc.remove_chunk_claims("doc1", ["doc1_chunk_a", "doc1_chunk_b"])
    assert out == {"claims_removed": 4, "relationships_deleted": 1}
    kwargs = session.run.call_args.kwargs
    assert kwargs == {"doc_id": "doc1", "prefix": "doc1_", "chunk_ids": ["doc1_chunk_a", "doc1_chunk_b"]}


def test_remove_chunk_claims_noop_without_ids():
    svc, session = _svc()
    assert svc.remove_chunk_claims("doc1", []) == {"claims_removed": 0, "relationships_deleted": 0}
    session.run.assert_not_called()


# --- library transfer --------------------------------------------------------

def test_import_retired_entities_restores_retired_at_as_datetime():
    svc, session = _svc()
    session.run.return_value.single.return_value = {"cnt": 1}
    svc.import_retired_entities_batch([{"name": "R", "retired_at": "2026-05-06T00:00:00+00:00"}])
    q = session.run.call_args.args[0]
    assert "CREATE (e:RetiredEntity)" in q and "datetime(entity.retired_at)" in q


def test_import_relationship_passes_claim_lists():
    svc, session = _svc()
    svc.import_relationship("A", "B", "USES", {"source_document_id": "d2",
                                               "source_document_ids": ["d1", "d2"],
                                               "source_chunk_ids": ["d1_chunk_0"]})
    kwargs = session.run.call_args.kwargs
    assert kwargs["source_doc_ids"] == ["d1", "d2"]
    assert kwargs["source_chunk_ids"] == ["d1_chunk_0"]
    assert "RetiredEntity" in session.run.call_args.args[0]


# --- live semantics (opt-in, disposable Neo4j + APOC only) --------------------

_LIVE = os.environ.get("CORTEX_CLAIMS_NEO4J_URI")


@pytest.mark.skipif(not _LIVE, reason="set CORTEX_CLAIMS_NEO4J_URI to a DISPOSABLE Neo4j")
def test_claims_live():
    from neo4j import GraphDatabase
    svc = object.__new__(ns.Neo4jService)
    svc._driver = GraphDatabase.driver(_LIVE, auth=("neo4j", os.environ["CORTEX_CLAIMS_NEO4J_PASSWORD"]))
    run = lambda q, **k: svc.driver.session().run(q, **k).data()  # noqa: E731
    run("MATCH (n) DETACH DELETE n")
    run("CREATE (:Entity {name:'A'}), (:Entity {name:'B'}), (:Entity {name:'C'})")
    # legacy edge from before claim sets
    run("MATCH (a:Entity {name:'A'}),(c:Entity {name:'C'}) CREATE (a)-[:OLD {source_document_id:'docL'}]->(c)")

    svc.store_relationship(_rel(), source_document_id="docA", source_chunk_ids=["docA_chunk_0"])
    svc.store_relationships_batch([_rel()], source_document_id="docB", source_chunk_ids=["docB_chunk_1"])
    svc.store_relationships_batch([_rel()], source_document_id="docB", source_chunk_ids=["docB_chunk_2"])
    edge = lambda: run("MATCH (:Entity {name:'A'})-[r:USES]->() RETURN r{.*} AS r")  # noqa: E731
    r = edge()[0]["r"]
    assert r["source_document_ids"] == ["docA", "docB"] and r["source_document_id"] == "docB"
    assert r["source_chunk_ids"] == ["docA_chunk_0", "docB_chunk_1", "docB_chunk_2"]

    # removing one of B's chunks keeps B's claim (chunk 2 still supports it)
    assert svc.remove_chunk_claims("docB", ["docB_chunk_1"]) == {"claims_removed": 1, "relationships_deleted": 0}
    assert edge()[0]["r"]["source_document_ids"] == ["docA", "docB"]
    # removing B's last chunk drops B's claim; A keeps the edge alive
    assert svc.remove_chunk_claims("docB", ["docB_chunk_2"]) == {"claims_removed": 1, "relationships_deleted": 0}
    r = edge()[0]["r"]
    assert r["source_document_ids"] == ["docA"] and r["source_document_id"] == "docA"

    # reprocessing doc B no longer deletes A's edge …
    svc.store_relationship(_rel(), source_document_id="docB", source_chunk_ids=["docB_chunk_9"])
    assert svc.delete_relationships_by_source_document("docB") == 0
    r = edge()[0]["r"]
    assert r["source_document_ids"] == ["docA"] and r["source_chunk_ids"] == ["docA_chunk_0"]
    # … but the last claimant's removal does
    assert svc.delete_relationships_by_source_document("docA") == 1
    assert edge() == []

    # legacy single-valued edges are read as one claim
    assert svc.delete_relationships_by_source_document("docL") == 1
    # unattributed edges (null source) are never touched
    run("MATCH (a:Entity {name:'A'}),(c:Entity {name:'C'}) CREATE (a)-[:CROSS {extraction_method:'cross_collection'}]->(c)")
    svc.delete_relationships_by_source_document("docA")
    assert run("MATCH ()-[r:CROSS]->() RETURN count(r) AS n")[0]["n"] == 1
    # Step-2 verification survives the last document claim; rebuild then drops it
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="S2"),
                           source_document_id="docS", source_chunk_ids=["docS_chunk_0"],
                           extraction_method="per_chunk")
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="S2"),
                           extraction_method="cross_collection")
    s2 = lambda: run("MATCH ()-[r:S2]->() RETURN r{.*} AS r")  # noqa: E731
    assert s2()[0]["r"]["source_document_ids"] == ["docS"]
    assert svc.delete_relationships_by_source_document("docS") == 0
    r = s2()[0]["r"]
    assert r["source_document_ids"] == [] and r.get("source_document_id") is None
    assert r["extraction_method"] == "cross_collection" and r["step2_verified"] is True
    # chunk-delta path: same rule
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="S3"),
                           source_document_id="docT", source_chunk_ids=["docT_chunk_0"],
                           extraction_method="per_chunk")
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="S3"),
                           extraction_method="cross_collection")
    assert svc.remove_chunk_claims("docT", ["docT_chunk_0"])["relationships_deleted"] == 0
    # rebuild: doc-claimed edge keeps living but loses the Step-2 mark;
    # Step-2-only edges (S2, S3 now) are deleted
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="S4"),
                           source_document_id="docU", source_chunk_ids=["docU_chunk_0"],
                           extraction_method="per_chunk")
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="S4"),
                           extraction_method="cross_collection")
    svc.delete_batch_relationships()
    assert run("MATCH ()-[r:S2|S3]->() RETURN count(r) AS n")[0]["n"] == 0
    r = run("MATCH ()-[r:S4]->() RETURN r{.*} AS r")[0]["r"]
    assert r["extraction_method"] == "per_chunk" and "step2_verified" not in r
    assert svc.delete_relationships_by_source_document("docU") == 1

    # retired endpoints still release claims
    run("MATCH (c:Entity {name:'C'}) REMOVE c:Entity SET c:RetiredEntity")
    svc.store_relationship(Relationship(source="A", target="B", relationship_type="KNOWS"),
                           source_document_id="docR")
    run("MATCH (b:Entity {name:'B'}) REMOVE b:Entity SET b:RetiredEntity")
    assert svc.delete_relationships_by_source_document("docR") == 1
    run("MATCH (n) DETACH DELETE n")
    svc.driver.close()
