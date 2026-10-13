"""Chunk-delta reprocess: a changed document re-extracts only its changed chunks.

Hermetic tests cover the planner, eligibility, the no-pre-wipe callers and the
Step-2 rebuild guard. `test_chunk_delta_live` drives the real pipeline twice
against a DISPOSABLE Neo4j (opt-in, same gate as test_relationship_claims):
v1 of a changelog, then v2 with a prepended section and one section removed.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Entity, Relationship
from app.services import document_processor as dp
from app.services import neo4j_service as ns
from app.services.git_connector_service import GitConnectorService


# --- planner -----------------------------------------------------------------

def _stored(*hashes, pending=()):
    return [{"id": f"c{i}", "content_hash": h, "chunk_index": i,
             "delta_pending": i in pending} for i, h in enumerate(hashes)]


def test_plan_prepend_keeps_every_old_chunk():
    plan = dp.plan_chunk_delta(_stored("a", "b", "c"), ["new", "a", "b", "c"])
    assert plan["sequence"] == [None, "c0", "c1", "c2"]
    assert plan["removed_ids"] == [] and plan["done_positions"] == [1, 2, 3]
    assert plan["reuse_ratio"] == 0.75


def test_plan_edit_and_removal():
    plan = dp.plan_chunk_delta(_stored("a", "b", "c"), ["a", "b2", "c"])
    assert plan["sequence"] == ["c0", None, "c2"]
    assert plan["removed_ids"] == ["c1"] and plan["kept_ids"] == ["c0", "c2"]


def test_plan_duplicates_pair_up_once():
    plan = dp.plan_chunk_delta(_stored("x", "x", "y"), ["x", "y", "x", "x"])
    assert plan["sequence"] == ["c0", "c2", "c1", None]
    assert plan["removed_ids"] == []


def test_plan_pending_chunks_are_reused_but_not_done():
    plan = dp.plan_chunk_delta(_stored("a", "b", pending=(1,)), ["a", "b"])
    assert plan["kept_ids"] == ["c0", "c1"] and plan["done_positions"] == [0]


def test_plan_empty_new_content():
    assert dp.plan_chunk_delta(_stored("a"), [])["reuse_ratio"] == 0.0


def test_new_chunk_ids_are_content_derived_and_unique():
    taken = {"d_chunk_0"}
    a = dp.new_delta_chunk_id("d", "f" * 32, taken)
    b = dp.new_delta_chunk_id("d", "f" * 32, taken)
    assert a == "d_chunk_h" + "f" * 16 and b == a + "_2"
    assert dp.chunk_content_hash("x") == dp.chunk_content_hash("x") != dp.chunk_content_hash("y")


# --- eligibility -------------------------------------------------------------

def _proc(**settings):
    p = object.__new__(dp.DocumentProcessor)
    base = dict(enable_chunk_delta_reprocess=True, enable_ingest_resume=True,
                chunk_delta_min_reuse_ratio=0.3)
    base.update(settings)
    p.settings = SimpleNamespace(**base)
    return p


_ELIGIBLE = {"claims_version": 1, "completed_file_sha256": "old", "file_sha256": "old",
             "completed_config_hash": "cfg", "text_chunk_count": 5,
             "unembedded_chunk_count": 0, "image_chunk_count": 0}


def test_delta_eligible_happy_path():
    assert _proc()._delta_eligible(dict(_ELIGIBLE), "new", "cfg")


@pytest.mark.parametrize("change", [
    {"claims_version": None},              # legacy doc: one full reprocess first
    {"file_sha256": "failed-run"},         # a later run already rewrote the chunks
    {"completed_config_hash": "other"},    # extraction/chunking config changed
    {"text_chunk_count": 0},
    {"unembedded_chunk_count": 2},
    {"image_chunk_count": 1},              # image chunks are index-keyed → full path
])
def test_delta_ineligible(change):
    assert not _proc()._delta_eligible({**_ELIGIBLE, **change}, "new", "cfg")


def test_delta_ineligible_when_unchanged_or_disabled():
    assert not _proc()._delta_eligible(dict(_ELIGIBLE), "old", "cfg")
    assert not _proc(enable_chunk_delta_reprocess=False)._delta_eligible(dict(_ELIGIBLE), "new", "cfg")
    assert not _proc(enable_ingest_resume=False)._delta_eligible(dict(_ELIGIBLE), "new", "cfg")


def test_prepare_resume_keeps_chunks_for_delta_candidate(monkeypatch):
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.get_document_fingerprint.return_value = dict(_ELIGIBLE)
    monkeypatch.setattr(p, "_file_sha256", lambda path: "new")
    monkeypatch.setattr(p, "_reprocess_config_hash", lambda: "cfg")
    out = asyncio.run(p._prepare_ingest_resume("doc", "/x"))
    assert out["delta_candidate"] and out["file_hash"] == "new"
    p.neo4j.delete_document_chunks.assert_not_called()
    p.neo4j.set_document_fingerprint.assert_not_called()


def test_prepare_resume_forced_engine_is_never_a_delta(monkeypatch):
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.get_document_fingerprint.return_value = dict(_ELIGIBLE)
    p.neo4j.delete_document_chunks.return_value = {"chunks_deleted": 5}
    monkeypatch.setattr(p, "_file_sha256", lambda path: "new")
    monkeypatch.setattr(p, "_reprocess_config_hash", lambda: "cfg")
    out = asyncio.run(p._prepare_ingest_resume("doc", "/x", force_full=True))
    assert not out["delta_candidate"]
    p.neo4j.delete_document_chunks.assert_called_once_with("doc")


def test_cleanup_before_reprocess_keeps_delta_eligible_chunks(monkeypatch):
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.get_document_fingerprint.return_value = dict(_ELIGIBLE)
    monkeypatch.setattr(p, "_file_sha256", lambda path: "new")
    monkeypatch.setattr(p, "_reprocess_config_hash", lambda: "cfg")
    p._cleanup_before_reprocess("doc", "/x")
    p.neo4j.delete_document_chunks.assert_not_called()


# --- git sync no longer pre-wipes a modified file -----------------------------

class _GitNeo4j:
    def __init__(self):
        self.calls = []

    def find_git_document(self, cid, path):
        return {"id": "doc1", "git_blob_sha": "old", "git_sync_status": "synced",
                "processing_status": "completed"}

    def update_document_status(self, *a, **k):
        self.calls.append("update_document_status")

    def set_document_git_provenance(self, *a, **k):
        self.calls.append("set_document_git_provenance")

    def __getattr__(self, name):  # delete_document_chunks would land here
        raise AssertionError(f"unexpected neo4j call: {name}")


def test_git_modify_does_not_prewipe_chunks(tmp_path, monkeypatch):
    fake = _GitNeo4j()
    monkeypatch.setattr(GitConnectorService, "neo4j", property(lambda self: fake))
    monkeypatch.setattr(GitConnectorService, "processor",
                        property(lambda self: SimpleNamespace(neo4j=fake)))
    svc = GitConnectorService()
    monkeypatch.setattr(svc, "_blob_bytes", AsyncMock(return_value=b"v2"))
    monkeypatch.setattr(svc, "_content_path", lambda cid, path: tmp_path / path)
    stats = {"created": 0, "modified": 0, "skipped": 0}
    touched: list = []
    asyncio.run(svc._ingest_file("conn1", tmp_path, "a.md", "new", 2, 0, None, "sha",
                                 stats, touched))
    assert touched == ["doc1"] and stats["modified"] == 1
    assert fake.calls == ["update_document_status", "set_document_git_provenance"]
    assert (tmp_path / "a.md").read_bytes() == b"v2"


# --- Step-2 rebuild keeps claimed edges ---------------------------------------

def test_rebuild_deletes_only_unclaimed_batch_edges():
    svc = object.__new__(ns.Neo4jService)
    session = MagicMock()
    session.run.return_value.single.return_value = {"deleted": 3}
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    svc._driver = driver
    assert svc.delete_batch_relationships() == {"relationships_deleted": 3}
    queries = [c.args[0] for c in session.run.call_args_list]
    restore = next(q for q in queries if "SET r.extraction_method = 'per_chunk'" in q)
    delete = next(q for q in queries if "DELETE r" in q)
    assert "SET r.extraction_method = 'per_chunk'" in restore
    assert "source_chunk_ids" in restore
    assert "= 0" in delete and "source_document_ids" in delete and "DELETE r" in delete
    assert "{{" not in delete  # f-string braces rendered


# --- live end-to-end (opt-in, DISPOSABLE Neo4j + APOC only) --------------------

_LIVE = os.environ.get("CORTEX_CLAIMS_NEO4J_URI")

_SECTIONS = {
    "s1": "## 1.0.0\n\nAlpha introduced the Router and Beta helped with Gamma.",
    "s2": "## 1.1.0\n\nDelta fixed the Router crash reported by Epsilon in Zeta mode.",
    "s3": "## 1.2.0\n\nKappa replaced Lambda; Kappa and Mu reviewed it.",
    "s4": "## 1.3.0\n\nOmicron added Pi support while Rho maintained Sigma.",
    "new": "## 2.0.0\n\nTau rewrote Zeta and Upsilon ported it to Phi.",
}
_V1_VOCAB = {"Alpha", "Router", "Beta", "Gamma", "Delta", "Epsilon", "Kappa",
             "Lambda", "Mu", "Omicron", "Pi", "Rho", "Sigma"}
_V2_VOCAB = _V1_VOCAB | {"Tau", "Zeta", "Upsilon", "Phi"}


class _FakeExtractor:
    is_available = True
    extraction_model_name = "fake-extract"
    relationship_model_name = "fake-rel"
    async_extraction_client = None

    def __init__(self):
        self.vocab = set(_V1_VOCAB)
        self.entity_calls: list = []
        self.rel_calls: list = []

    def _ents(self, text):
        return [Entity(name=w, type="Concept", description=f"{w} desc")
                for w in sorted(self.vocab) if w in text]

    async def extract_entities_from_document_async(self, chunks, max_tokens=0, progress_callback=None,
                                                   run_stats=None, on_batch_entities=None,
                                                   skip_chunk_indices=None, on_outage_state=None,
                                                   **_):
        out = []
        for i, text in enumerate(chunks):
            if skip_chunk_indices and i in skip_chunk_indices:
                continue
            self.entity_calls.append(text)
            ents = self._ents(text)
            if on_batch_entities:
                await on_batch_entities(ents, i, i + 1)
            out.extend(ents)
        return out

    async def extract_chunk_relationships_async(self, chunk_text, entities, max_output_tokens=0):
        self.rel_calls.append(chunk_text)
        names = [e["name"] for e in entities]
        return [Relationship(source=a, target=b, relationship_type="RELATES_TO", description="x")
                for a, b in zip(names, names[1:])]


class _FakeEmbedder:
    def __init__(self, dim):
        self.dim = dim
        self.calls: list = []

    def run(self, documents):
        for d in documents:
            self.calls.append(d.content)
            d.embedding = [0.01] * self.dim
        return {"documents": documents}


@pytest.mark.skipif(not _LIVE, reason="set CORTEX_CLAIMS_NEO4J_URI to a DISPOSABLE Neo4j")
def test_chunk_delta_live(tmp_path):
    from neo4j import GraphDatabase

    from app.config import get_settings
    from app.services.structural_chunker import StructuralSplitter

    neo = object.__new__(ns.Neo4jService)
    neo.settings = get_settings()
    neo._driver = GraphDatabase.driver(_LIVE, auth=("neo4j", os.environ["CORTEX_CLAIMS_NEO4J_PASSWORD"]))
    neo._vector_search_failures = 0
    neo._vector_search_failure_warned = False
    neo._query_entity_cache = {}
    run = lambda q, **k: neo.driver.session().run(q, **k).data()  # noqa: E731
    run("MATCH (n) DETACH DELETE n")
    neo.initialize_schema()

    settings = get_settings().model_copy(update=dict(
        enable_ingest_resume=True, enable_chunk_delta_reprocess=True,
        chunk_delta_min_reuse_ratio=0.3, enable_graph_extraction=True,
        enable_semantic_entity_resolution=False, enable_batched_kg_writes=False,
        enable_batched_chunk_relationships=False, enable_ingestion_injection_scan=False,
        concurrent_relations=2, chunking_strategy="structural",
        structural_chunk_min_words=1, structural_chunk_max_words=400,
        structural_chunk_boundary_divisor=1,
    ))
    p = object.__new__(dp.DocumentProcessor)
    p.settings = settings
    p.neo4j = neo
    p.splitter = StructuralSplitter(1, 400, 1)
    p.embedder = _FakeEmbedder(settings.embedding_dimension)
    p.graph_extractor = _FakeExtractor()
    p.vision_analyzer = SimpleNamespace(is_vision_model_available=False)
    monkey_quota = AsyncMock(return_value=False)
    p._monthly_quota_exhausted = monkey_quota

    doc_id = str(uuid.uuid4())
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n" + "\n\n".join(_SECTIONS[k] for k in ("s1", "s2", "s3", "s4")))
    run("CREATE (:Document {id: $id, filename: 'CHANGELOG.md', file_path: $p, file_type: '.md', "
        "processing_status: 'pending'})", id=doc_id, p=str(path))

    try:
        asyncio.run(p._process_document(doc_id, str(path), ".md"))
        chunks_v1 = {r["content"]: r["id"] for r in run(
            "MATCH (:Document {id:$id})-[:HAS_CHUNK]->(c) RETURN c.id AS id, c.content AS content", id=doc_id)}
        assert len(chunks_v1) == 5   # title + 4 sections
        d = run("MATCH (d:Document {id:$id}) RETURN d{.*} AS d", id=doc_id)[0]["d"]
        assert d["processing_status"] == "completed" and d["claims_version"] == 1

        # curated entity only in s3 (to be removed) → must be retired, not deleted
        neo.update_entity("Lambda", new_description="hand-written note")
        # an edge s3 supports that another document ALSO asserts → must survive
        neo.store_relationship(Relationship(source="Kappa", target="Lambda", relationship_type="RELATES_TO"),
                               source_document_id="other-doc")
        assert run("MATCH (:Entity {name:'Mu'})-[r]-(:Entity) RETURN count(r) AS n")[0]["n"] == 1

        ext, emb = p.graph_extractor, p.embedder
        ext.entity_calls.clear(); ext.rel_calls.clear(); emb.calls.clear()
        ext.vocab = set(_V2_VOCAB)
        # v2: new section prepended, s3 removed, rest unchanged
        path.write_text("# Changelog\n\n" + "\n\n".join(_SECTIONS[k] for k in ("new", "s1", "s2", "s4")))
        asyncio.run(p._process_document(doc_id, str(path), ".md"))

        chunks_v2 = {r["content"]: r for r in run(
            "MATCH (:Document {id:$id})-[:HAS_CHUNK]->(c) RETURN c.id AS id, c.content AS content, "
            "c.chunk_index AS idx, c.delta_pending AS pending", id=doc_id)}
        assert len(chunks_v2) == 5
        new_chunk = next(c for t, c in chunks_v2.items() if "Tau" in t)
        assert new_chunk["id"].startswith(f"{doc_id}_chunk_h") and new_chunk["idx"] == 1
        for text, row in chunks_v2.items():   # unchanged chunks kept their ids
            if "Tau" not in text:
                assert row["id"] == chunks_v1[text]
            assert row["pending"] is None
        # only the new chunk hit the embedder and the entity extractor
        assert len(emb.calls) == 1 and "Tau" in emb.calls[0]
        assert len(ext.entity_calls) == 1 and "Tau" in ext.entity_calls[0]
        # Zeta (new entity) also occurs in the unchanged s2 chunk → re-opened
        assert any("Tau" in t for t in ext.rel_calls)
        assert any("Epsilon" in t for t in ext.rel_calls)
        assert not any("Omicron" in t for t in ext.rel_calls)   # untouched chunk stays done
        assert run("MATCH (:Chunk {id:$c})-[:MENTIONS]->(:Entity {name:'Zeta'}) RETURN count(*) AS n",
                   c=chunks_v1[next(t for t in chunks_v1 if "Epsilon" in t)])[0]["n"] == 1

        # removed chunk's own edge (Mu) is gone, plain entity Mu deleted
        assert run("MATCH (e:Entity {name:'Mu'}) RETURN count(e) AS n")[0]["n"] == 0
        # curated Lambda retired; Kappa (s3 only) holds the other-doc edge,
        # which is not a per-chunk extraction → retired too; the edge survives
        # with only the other document's claim.
        assert run("MATCH (e:RetiredEntity) RETURN collect(e.name) AS n")[0]["n"] in (
            ["Kappa", "Lambda"], ["Lambda", "Kappa"])
        edge = run("MATCH (:RetiredEntity {name:'Kappa'})-[r:RELATES_TO]->(:RetiredEntity {name:'Lambda'}) "
                   "RETURN r.source_document_ids AS ids")
        assert edge and edge[0]["ids"] == ["other-doc"]
        d = run("MATCH (d:Document {id:$id}) RETURN d{.*} AS d", id=doc_id)[0]["d"]
        assert d["processing_status"] == "completed" and d["chunk_count"] == 5
        assert '"delta"' in (d.get("extraction_stats") or "")
        assert d["completed_file_sha256"] == d["file_sha256"]
        assert d.get("delta_orphan_candidates") is None
    finally:
        run("MATCH (n) DETACH DELETE n")
        neo.driver.close()


# --- review regressions ------------------------------------------------------

def test_cancel_reaches_batch_run_document():
    """(1) A batch-run doc (flag only, no task) keeps its set flag until the
    runner observes it — deleting it made _check_cancellation a no-op."""
    p = _proc()
    p.neo4j = MagicMock()

    async def scenario():
        dp._cancellation_flags["batch-doc"] = flag = asyncio.Event()

        async def runner():  # stands in for process_one: polls, finally pops
            while True:
                await asyncio.sleep(0.01)
                try:
                    p._check_cancellation("batch-doc")
                except dp.CancellationRequested:
                    dp._cancellation_flags.pop("batch-doc", None)
                    return "cancelled"

        task = asyncio.create_task(runner())
        await p.cancel_document_processing("batch-doc")
        assert flag.is_set()
        return await task

    try:
        assert asyncio.run(scenario()) == "cancelled"
    finally:
        dp._cancellation_flags.pop("batch-doc", None)


def test_prepare_without_resume_clears_stale_chunks(monkeypatch):
    """(2) Resume off: callers no longer pre-wipe, so stale chunks are
    cleared before the full path (and the start hash recorded)."""
    p = _proc(enable_ingest_resume=False)
    p.neo4j = MagicMock()
    p.neo4j.delete_document_chunks.return_value = {"chunks_deleted": 7}
    monkeypatch.setattr(p, "_file_sha256", lambda path: "start-hash")
    out = asyncio.run(p._prepare_ingest_resume("doc", "/x"))
    p.neo4j.delete_document_chunks.assert_called_once_with("doc")
    assert out["clean_start"] and out["file_hash"] == "start-hash"
    assert not out["delta_candidate"] and not out["reuse_chunks"]


def test_prepare_failure_still_clears_stale_chunks(monkeypatch):
    """(2) A failing resume preparation degrades to a CLEAN full run."""
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.get_document_fingerprint.side_effect = RuntimeError("neo4j hiccup")
    p.neo4j.delete_document_chunks.return_value = {"chunks_deleted": 3}
    monkeypatch.setattr(p, "_file_sha256", lambda path: "h")
    monkeypatch.setattr(p, "_reprocess_config_hash", lambda: "cfg")
    out = asyncio.run(p._prepare_ingest_resume("doc", "/x"))
    p.neo4j.delete_document_chunks.assert_called_once_with("doc")
    assert out["clean_start"]


def test_interrupted_delta_with_reverted_bytes_is_not_reused(monkeypatch):
    """(6) Marker set + OLD bytes back: no reuse of the mixed chunk set,
    no delta → stale cleanup."""
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.get_document_fingerprint.return_value = {
        **_ELIGIBLE, "config_hash": "cfg", "delta_in_progress": "new"}
    p.neo4j.delete_document_chunks.return_value = {"chunks_deleted": 5}
    monkeypatch.setattr(p, "_file_sha256", lambda path: "old")
    monkeypatch.setattr(p, "_reprocess_config_hash", lambda: "cfg")
    out = asyncio.run(p._prepare_ingest_resume("doc", "/x"))
    assert not out["reuse_chunks"] and not out["delta_candidate"]
    p.neo4j.delete_document_chunks.assert_called_once_with("doc")


def test_interrupted_delta_with_same_new_bytes_replans(monkeypatch):
    """(6) The same new bytes after an interruption re-plan the delta."""
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.get_document_fingerprint.return_value = {
        **_ELIGIBLE, "config_hash": "cfg", "delta_in_progress": "new"}
    monkeypatch.setattr(p, "_file_sha256", lambda path: "new")
    monkeypatch.setattr(p, "_reprocess_config_hash", lambda: "cfg")
    out = asyncio.run(p._prepare_ingest_resume("doc", "/x"))
    assert out["delta_candidate"]
    p.neo4j.delete_document_chunks.assert_not_called()


def _finalize_session(legacy: bool):
    svc = object.__new__(ns.Neo4jService)
    session = MagicMock()

    def run(q, **k):
        res = MagicMock()
        if "RETURN count(r) > 0 AS legacy" in q:
            res.single.return_value = {"legacy": legacy}
        else:
            res.single.return_value = {"names": []}
        return res

    session.run.side_effect = run
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    svc._driver = driver
    return svc, session


@pytest.mark.parametrize("clean,legacy,tracked", [
    (True, True, True),     # cleanup ran: legacy edges were released
    (False, True, False),   # resume-reuse with pre-claim edges: NOT tracked
    (False, False, True),   # no pre-claim edges remain
])
def test_claims_version_only_when_tracked(clean, legacy, tracked):
    """(5) claims_version=1 must not be granted over unreleased legacy edges."""
    svc, session = _finalize_session(legacy)
    svc.finalize_chunk_delta("doc", "sha", "cfg", clean)
    kwargs = next(c.kwargs for c in session.run.call_args_list if "tracked" in c.kwargs)
    assert kwargs["tracked"] is tracked
    ran_check = any("AS legacy" in c.args[0] for c in session.run.call_args_list)
    assert ran_check is (not clean)


def test_image_bearing_new_version_falls_back_before_image_analysis(monkeypatch):
    """(9b) Cleanup for an image-bearing delta candidate happens before
    background image analysis is scheduled (it would store image chunks the
    cleanup then deletes)."""
    import inspect
    src = inspect.getsource(dp.DocumentProcessor._process_document)
    fallback = src.index('"new version contains images"')
    launch = src.index("_analyze_images_background_from_serialized")
    assert fallback < launch
    p = _proc()
    p.neo4j = MagicMock()
    p.neo4j.delete_document_chunks.return_value = {"chunks_deleted": 1}
    resume = {"delta_candidate": True, "file_hash": "h", "config_hash": "c"}
    asyncio.run(p._fall_back_to_full_reprocess("doc", resume, "why"))
    assert resume["delta_candidate"] is False and resume["clean_start"] is True
    p.neo4j.set_delta_in_progress.assert_called_once_with("doc", None)


def test_completion_records_start_hash_not_rehash():
    """(1) The completion fingerprint uses the hash taken at run start."""
    import inspect
    src = inspect.getsource(dp.DocumentProcessor._process_document)
    tail = src[src.index("Record the processed file + config identity"):]
    assert 'resume.get("file_hash")' in tail
    assert "self._file_sha256, file_path" not in tail[:800]
