"""Entity retirement: curated orphans are retired (label swap), not deleted.

Reprocessing or deleting a document used to DETACH DELETE every entity it
alone mentioned — wiping manual renames/descriptions, merge aliases, Step-2
cross-document edges and community membership. Curated orphans now become
`:RetiredEntity` (invisible to every :Entity-labelled index and query, edges
kept) and are revived when a later extraction produces the same name.
Neo4j is mocked: these tests pin the Cypher contract and the Python glue.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import Entity
from app.services import neo4j_service as ns


# --- fakes -------------------------------------------------------------------

class _Result:
    def __init__(self, single=None, rows=None):
        self._single = single
        self._rows = rows or []

    def single(self):
        return self._single

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """Records every query; answers by matching a substring of the Cypher."""

    def __init__(self, answers=None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or []

    def run(self, query, **params):
        self.calls.append((query, params))
        for needle, result in self.answers:
            if needle in query:
                return result
        return _Result(single={"retired": 0, "deleted": 0, "orphaned_entities": [],
                               "removed_count": 0, "names": []})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _service(session):
    svc = object.__new__(ns.Neo4jService)
    svc._driver = SimpleNamespace(session=lambda **kw: session)
    svc.settings = SimpleNamespace()
    return svc


# --- the shared helper -------------------------------------------------------

def test_helper_retires_curated_then_deletes_rest():
    s = _Session([("SET e:RetiredEntity", _Result(single={"retired": 2})),
                  ("DETACH DELETE e", _Result(single={"deleted": 3}))])
    assert ns._retire_or_delete_orphans(s, ["A", "B", "", "C"], "doc1") == (2, 3)
    (retire_q, retire_p), (delete_q, delete_p) = s.calls
    assert retire_p == {"names": ["A", "B", "C"], "doc_id": "doc1"}
    assert "REMOVE e:Entity" in retire_q and "e.retired_from_document = $doc_id" in retire_q
    # Retired nodes lost :Entity, so the delete only hits the plain orphans.
    assert "MATCH (e:Entity {name: name})" in delete_q and delete_p == {"names": ["A", "B", "C"]}


def test_helper_noop_without_names():
    s = _Session()
    assert ns._retire_or_delete_orphans(s, [], "doc1") == (0, 0)
    assert s.calls == []


def test_retire_writes_lookup_keys_and_schema_indexes_them():
    import inspect
    assert "MERGE (:RetiredEntityKey {key: key, name: e.name})" in ns._RETIRE_CURATED_ORPHANS
    src = inspect.getsource(ns.Neo4jService.initialize_schema)
    for idx in ("retired_entity_name", "retired_entity_key", "retired_entity_key_name"):
        assert idx in src
    assert "_INDEX_RETIRED_KEYS_BACKFILL" in src


def test_curation_criteria_in_retire_query():
    q = ns._RETIRE_CURATED_ORPHANS
    assert "e.curated_at IS NOT NULL" in q
    assert "size(coalesce(e.aliases, [])) > 0" in q
    # Any edge that is not a Step-1 per-chunk extraction keeps the entity.
    assert "<> 'per_chunk'" in q and "x:RetiredEntity" in q


# --- delete paths ------------------------------------------------------------

def _orphans_answer(names):
    return ("RETURN collect(e.name) as orphaned_entities",
            _Result(single={"orphaned_entities": names}))


def test_delete_document_chunks_retires_and_reports():
    s = _Session([
        _orphans_answer(["Curated", "Plain"]),
        ("SET e:RetiredEntity", _Result(single={"retired": 1})),
        ("DETACH DELETE e", _Result(single={"deleted": 1})),
        ("DETACH DELETE chunk", _Result(single={"deleted": 4})),
    ])
    out = _service(s).delete_document_chunks("doc1")
    assert out["orphaned_entities_removed"] == 1
    assert out["orphaned_entities_retired"] == 1
    queries = [q for q, _ in s.calls]
    assert not any("WHERE e.name IN $names\n                    DETACH DELETE e" in q for q in queries)
    # Provenance scrub reaches retired nodes too.
    assert any("e:RetiredEntity" in q and "source_documents" in q for q in queries)


def test_delete_document_retires_and_reports():
    s = _Session([
        _orphans_answer(["Curated"]),
        ("SET e:RetiredEntity", _Result(single={"retired": 1})),
        ("DETACH DELETE e", _Result(single={"deleted": 0})),
        ("as removed_count", _Result(single={"removed_count": 0})),
        ("DETACH DELETE d, c", _Result(single={"deleted": 1})),
    ])
    out = _service(s).delete_document("doc1")
    assert out["deleted"] is True
    assert out["orphaned_entities_retired"] == 1
    assert out["orphaned_entities_removed"] == 0


def test_cleanup_orphaned_entities_uses_retirement():
    s = _Session([
        ("RETURN collect(e.name) as names", _Result(single={"names": ["X", "Y"]})),
        ("SET e:RetiredEntity", _Result(single={"retired": 1})),
        ("DETACH DELETE e", _Result(single={"deleted": 1})),
    ])
    assert _service(s).cleanup_orphaned_entities() == 1
    assert any(p.get("doc_id", "unset") is None for _, p in s.calls)


def test_full_resets_remove_retired_layer():
    s = _Session([("count(d) as doc_count", _Result(single={"doc_count": 0})),
                  ("count(e) as entity_count", _Result(single={"entity_count": 0})),
                  ("count(com) as community_count", _Result(single={"community_count": 0}))])
    _service(s).delete_all_documents()
    assert any("e:RetiredEntity" in q and "DETACH DELETE" in q for q, _ in s.calls)

    s = _Session([("RETURN count(*) as deleted", _Result(single={"deleted": 2}))])
    _service(s).delete_all_entities()
    assert any("e:RetiredEntity OR e:RetiredEntityKey" in q and "DETACH DELETE" in q for q, _ in s.calls)


# --- curation marker ---------------------------------------------------------

def test_update_entity_sets_curation_marker():
    s = _Session([("RETURN e.name as name, e.type as type",
                   _Result(single={"name": "A", "type": "T", "description": "d", "aliases": []}))])
    _service(s).update_entity("A", new_description="edited")
    assert any("e.curated_at = datetime()" in q for q, _ in s.calls)


# --- revival -----------------------------------------------------------------

def test_revive_returns_lowercased_mapping():
    s = _Session([("REMOVE r:RetiredEntity", _Result(rows=[
        {"requested": "IBM", "name": "International Business Machines"},
        {"requested": "Acme", "name": "Acme"},
    ]))])
    out = _service(s).revive_retired_entities(["IBM", "Acme", "IBM", ""])
    assert out == {"ibm": "International Business Machines", "acme": "Acme"}
    (q, p), = s.calls
    assert p == {"names": ["IBM", "Acme"]}
    assert "NOT EXISTS { MATCH (:Entity {name: name}) }" in q
    # Index-backed: lowercase key nodes, never a label scan / list membership.
    assert "MATCH (k:RetiredEntityKey {key: toLower(name)})" in q
    assert "IN coalesce(r.aliases" not in q


def test_revive_noop_without_names():
    s = _Session()
    assert _service(s).revive_retired_entities([]) == {}
    assert s.calls == []


def test_revived_entity_takes_canonical_name_on_alias_match():
    svc = _service(_Session())
    svc.revive_retired_entities = lambda names: {"ibm": "International Business Machines"}
    e = Entity(name="IBM", type="Organization", description="d")
    assert svc._revived_entity(e).name == "International Business Machines"
    assert e.name == "IBM"  # caller's object untouched


def test_revived_entity_is_best_effort():
    svc = _service(_Session())
    svc.revive_retired_entities = MagicMock(side_effect=RuntimeError("down"))
    e = Entity(name="IBM", type="Organization")
    assert svc._revived_entity(e) is e
    svc.revive_retired_entities = MagicMock(return_value=MagicMock())  # mocked graph
    assert svc._revived_entity(e) is e


def test_store_entity_merges_onto_revived_name():
    s = _Session([("RETURN e.name as name", _Result(single={"name": "Acme Corp"}))])
    svc = _service(s)
    svc.revive_retired_entities = lambda names: {"acme": "Acme Corp"}
    assert svc.store_entity(Entity(name="acme", type="Organization")) == "Acme Corp"
    merge_params = [p for q, p in s.calls if "MERGE (e:Entity {name: $name})" in q][0]
    assert merge_params["name"] == "Acme Corp"


def test_batched_processor_path_revives_and_maps_original_names():
    from app.services import document_processor as dp

    proc = object.__new__(dp.DocumentProcessor)
    proc.neo4j = SimpleNamespace(
        revive_retired_entities=lambda names: {"ibm": "International Business Machines"}
    )
    proc.settings = SimpleNamespace(enable_batched_kg_writes=True)
    seen = {}

    async def fake_batched(doc_id, entities, embeddings, loop):
        seen["names"] = [e.name for e in entities]
        return {e.name.lower(): e.name for e in entities}

    proc._store_entities_batched = fake_batched
    ents = [Entity(name="IBM", type="Organization"), Entity(name="Other", type="Concept")]

    async def run():
        return await proc._store_entity_batch("doc1", ents, None, asyncio.get_running_loop())

    out = asyncio.run(run())
    assert seen["names"] == ["International Business Machines", "Other"]
    assert out["ibm"] == "International Business Machines"
    assert out["other"] == "Other"


def test_batched_processor_path_tolerates_mocked_graph():
    from app.services import document_processor as dp

    proc = object.__new__(dp.DocumentProcessor)
    proc.neo4j = MagicMock()  # revive returns a MagicMock, not a dict
    ents = [Entity(name="IBM", type="Organization")]

    async def run():
        return await proc._revive_retired_for_batch(ents, asyncio.get_running_loop())

    out, renamed = asyncio.run(run())
    assert out == ents and renamed == {}


# --- retrieval stays blind to retired nodes ---------------------------------

def test_traversal_never_hops_through_retired_entities():
    import inspect
    src = inspect.getsource(ns.Neo4jService)
    assert 'WHERE NONE(n IN nodes(path) WHERE n:RetiredEntity)' in src


# --- admin endpoints ---------------------------------------------------------

def test_list_and_purge_endpoints(client, mock_neo4j):
    mock_neo4j.list_retired_entities.return_value = {
        "entities": [{"name": "A", "retired_at": "2026-10-13T00:00:00Z",
                      "retired_from_document": "doc1", "degree": 2,
                      "type": "T", "description": "d"}],
        "total": 1, "limit": 10, "offset": 0,
    }
    resp = client.get("/api/graph/retired-entities?limit=10")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
    mock_neo4j.list_retired_entities.assert_called_once_with(10, 0)

    mock_neo4j.purge_retired_entities.return_value = {"purged": 1, "remaining": 0}
    resp = client.delete("/api/graph/retired-entities?older_than_days=0")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"purged": 1, "remaining": 0}
    mock_neo4j.purge_retired_entities.assert_called_once_with(0)


def test_purge_endpoint_rejects_negative_days(client):
    assert client.delete("/api/graph/retired-entities?older_than_days=-1").status_code == 422

