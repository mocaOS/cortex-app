"""Library transfer state continuity — real export -> real import round trip.

Existing coverage (test_library_transfer.py) exercises the serialization
primitives and the crafted-archive hardening with a *synthetic* archive built
by the test itself. Nothing drives the REAL export writer end to end and then
restores into a fresh target, so continuity of the exported state — record
identity, referenced bytes, file restore, count integrity — is unobserved.
That is the state-continuity obligation of the transfer boundary
(REGENERATIVE-SOFTWARE §13.4: verify a multi-resource restore at the level of
records, identities, and referenced bytes — not by row counts or exit status).

Design (deterministic, hermetic):
- `ScriptedGraphStore` mirrors the documented neo4j_service export_*/import_*
  contract with in-memory tables (the store substitute at the service
  boundary). Source store is populated with sanitized deterministic fixtures
  (synthetic ids/filenames/bytes — no tenant data).
- `export_library` writes a real ZIP through the production writer; a fresh
  `ScriptedGraphStore` target imports it through the production importer in
  clean mode.
- Comparison rules (declared, frozen before evaluation):
  * records whose shape is unchanged: exact equality;
  * Neo4j DateTime values: compared via their ISO-8601 form (the archive's
    declared serialization), computed independently of _serialize_value;
  * document file_path: intentionally remapped to doc-scoped names in the
    target upload/custom-input dirs — continuity is asserted on identity,
    directory containment, and BYTE EQUALITY of the restored files.

Hard invariants gated here (see qa/QA_CONTRACT_RECORDS.md, INV-LIB-*):
- INV-LIB-001: every exported record class survives import with its identity
  (documents/chunks/entities/relationships(+promised props)/communities/
  memberships/mentions/merge history/system meta).
- INV-LIB-002: restored files are byte-identical and confined to the
  instance's upload/custom-input dirs; custom inputs land in their dir.
- INV-LIB-003: clean mode refuses a non-empty target BEFORE any destructive
  action (target tables unchanged).
- INV-LIB-004 (negative control): an archive missing a record class and a
  document's blob must NOT import silently "complete" — the result reports
  the gap (warnings + accurate restored counts).
- INV-LIB-005: the scripted store substitute cannot drift from the real
  Neo4jService contract — every call the production transfer code actually
  makes must still bind to the real method's signature.

Limitations (see qa/QA_CONTRACT_RECORDS.md): skills export/import is out of
scope here (no skill fixtures in this gate; covered by skill_service tests);
the store substitute mirrors the documented store contract, not live Cypher.
No model quality is measured — this gate is fully deterministic.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Dict, List

import pytest
from neo4j.time import DateTime as Neo4jDateTime

from app.services.library_transfer_service import LibraryTransferService

# Relationship properties the import contract restores explicitly (mirrors
# the importer's props dict — these are the promised ones).
REL_PROMISED_PROPS = (
    "description", "weight", "confidence", "extraction_method",
    "source_document_id", "extracted_at",
)

# doc-scoped restore names: {sanitized_doc_id}_{original_basename}
EXPECTED_RESTORE_NAMES = {
    "doc-alpha": "doc-alpha_alpha_report.pdf",
    "doc-beta": "doc-beta_beta_notes.md",
    "doc-gamma": "doc-gamma_gamma_qa.md",
}


# ---------------------------------------------------------------------------
# Store substitute: mirrors neo4j_service export_/import_ contract
# ---------------------------------------------------------------------------

class ScriptedGraphStore:
    def __init__(self):
        self.documents: Dict[str, dict] = {}
        self.chunks: List[dict] = []
        self.entities: List[dict] = []
        self.relationships: List[dict] = []
        self.communities: List[dict] = []
        self.community_members: List[dict] = []
        self.collections: List[dict] = []
        self.collection_members: List[dict] = []
        self.chunk_mentions: List[dict] = []
        self.merge_history: List[dict] = []
        self.system_meta: List[dict] = []
        # Protocol-drift protection (INV-LIB-005): every store call the
        # PRODUCTION transfer code actually makes is recorded here and
        # validated against the real Neo4jService signatures — so this fake
        # cannot keep "passing" after the real store contract moves.
        self.calls: List[tuple] = []
        for _name, _attr in list(vars(type(self)).items()):
            if callable(_attr) and not _name.startswith("_"):
                setattr(self, _name, self._record_call(_name, _attr))

    def _record_call(self, name: str, fn):
        def wrapper(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return fn(self, *args, **kwargs)
        return wrapper

    # -- export side -------------------------------------------------------
    def get_stats(self) -> dict:
        return {
            "document_count": len(self.documents),
            "chunk_count": len(self.chunks),
            "entity_count": len(self.entities),
            "relationship_count": len(self.relationships),
            "community_count": len(self.communities),
            "collection_count": len(self.collections),
        }

    def export_all_documents(self) -> List[dict]:
        return [dict(d) for d in self.documents.values()]

    def export_chunk_count(self) -> int:
        return len(self.chunks)

    def export_all_chunks_batched(self, batch_size: int, skip: int):
        return self.chunks[skip:skip + batch_size]

    def export_entity_count(self) -> int:
        return len(self.entities)

    def export_all_entities_batched(self, batch_size: int, skip: int):
        return self.entities[skip:skip + batch_size]

    def export_relationship_count(self) -> int:
        return len(self.relationships)

    def export_all_entity_relationships_batched(self, batch_size: int,
                                                skip: int):
        return self.relationships[skip:skip + batch_size]

    def export_all_communities(self):
        return [dict(c) for c in self.communities]

    def export_community_members(self):
        return [dict(m) for m in self.community_members]

    def export_all_collections(self):
        return [dict(c) for c in self.collections]

    def export_collection_members(self):
        return [dict(m) for m in self.collection_members]

    def export_all_chunk_mentions(self):
        return [dict(m) for m in self.chunk_mentions]

    def export_all_merge_history(self):
        return [dict(m) for m in self.merge_history]

    def export_all_system_meta(self):
        return [dict(m) for m in self.system_meta]

    def export_all_skills(self):
        return []

    # -- import side -------------------------------------------------------
    def import_collections_batch(self, rows) -> int:
        self.collections.extend(dict(r) for r in rows)
        return len(rows)

    def import_documents_batch(self, rows) -> int:
        for r in rows:
            self.documents[r["id"]] = dict(r)
        return len(rows)

    def import_chunks_batch(self, rows) -> int:
        self.chunks.extend(dict(r) for r in rows)
        return len(rows)

    def import_entities_batch(self, rows) -> int:
        self.entities.extend(dict(r) for r in rows)
        return len(rows)

    def import_chunk_mentions_batch(self, rows) -> int:
        self.chunk_mentions.extend(dict(r) for r in rows)
        return len(rows)

    def import_relationship(self, source, target, rel_type, props) -> bool:
        self.relationships.append({"source": source, "target": target,
                                   "rel_type": rel_type, **props})
        return True

    def import_communities_batch(self, rows) -> int:
        self.communities.extend(dict(r) for r in rows)
        return len(rows)

    def import_community_members_batch(self, rows) -> int:
        self.community_members.extend(dict(r) for r in rows)
        return len(rows)

    def import_collection_members_batch(self, rows) -> int:
        self.collection_members.extend(dict(r) for r in rows)
        return len(rows)

    def import_merge_history_batch(self, rows) -> int:
        self.merge_history.extend(dict(r) for r in rows)
        return len(rows)

    def import_system_meta_batch(self, rows) -> int:
        self.system_meta.extend(dict(r) for r in rows)
        return len(rows)


# ---------------------------------------------------------------------------
# Deterministic sanitized fixtures
# ---------------------------------------------------------------------------

DOC_ALPHA_PDF = b"%PDF-1.4 alpha deterministic bytes " + bytes(range(64))
DOC_BETA_MD = b"# Beta notes\n\nDeterministic markdown body.\n"
DOC_GAMMA_MD = b"Q: deterministic custom input?\nA: yes.\n"


def _populate_source(store: ScriptedGraphStore, upload_dir: Path,
                     custom_dir: Path) -> Dict[str, bytes]:
    """Populate the source store; returns {doc_id: original file bytes}."""
    files: Dict[str, bytes] = {
        "doc-alpha": DOC_ALPHA_PDF,
        "doc-beta": DOC_BETA_MD,
        "doc-gamma": DOC_GAMMA_MD,
    }
    (upload_dir / "alpha_report.pdf").write_bytes(DOC_ALPHA_PDF)
    (upload_dir / "beta_notes.md").write_bytes(DOC_BETA_MD)
    (custom_dir / "gamma_qa.md").write_bytes(DOC_GAMMA_MD)

    store.documents["doc-alpha"] = {
        "id": "doc-alpha", "filename": "alpha_report.pdf",
        "title": "Alpha Report", "size": len(DOC_ALPHA_PDF),
        "file_path": str(upload_dir / "alpha_report.pdf"),
        "is_custom_input": False,
        "created_at": Neo4jDateTime(2026, 1, 2, 3, 4, 5, 0),
    }
    store.documents["doc-beta"] = {
        "id": "doc-beta", "filename": "beta_notes.md",
        "title": "Beta Notes", "size": len(DOC_BETA_MD),
        "file_path": str(upload_dir / "beta_notes.md"),
        "is_custom_input": False,
        "created_at": "2026-02-03T04:05:06",
    }
    store.documents["doc-gamma"] = {
        "id": "doc-gamma", "filename": "gamma_qa.md",
        "title": "Gamma QA", "size": len(DOC_GAMMA_MD),
        "file_path": str(custom_dir / "gamma_qa.md"),
        "is_custom_input": True,
        "created_at": "2026-03-04T05:06:07",
    }

    store.chunks = [
        {"chunk": {"id": f"chunk-{i}", "document_id": "doc-alpha",
                   "content": f"deterministic chunk {i}",
                   "chunk_index": i,
                   "embedding": [0.1 * (i + 1), 0.2, 0.3]},
         "document_id": "doc-alpha"}
        for i in range(4)
    ]
    store.entities = [
        {"name": "Alpha Entity", "type": "Concept",
         "description": "deterministic entity",
         "mention_count": 3, "embedding": [0.5, 0.5]},
        {"name": "Beta Entity", "type": "Person",
         "description": "second deterministic entity",
         "mention_count": 1, "embedding": [0.25, 0.75]},
    ]
    store.relationships = [
        {"source": "Alpha Entity", "target": "Beta Entity",
         "rel_type": "RELATES_TO",
         "description": "deterministic relationship",
         "weight": 7.5, "confidence": 0.9,
         "extraction_method": "llm", "source_document_id": "doc-alpha",
         "extracted_at": "2026-04-05T06:07:08",
         # Not part of the import contract's props dict — observed to be
         # dropped on restore; recorded as a gap, not gated as contract.
         "evidence": "unrestored extra property"},
    ]
    store.communities = [
        {"id": "com-1", "label": "community-1", "level": 0,
         "summary": "deterministic community summary", "entity_count": 2},
    ]
    store.community_members = [
        {"community_id": "com-1", "entity_name": "Alpha Entity"},
        {"community_id": "com-1", "entity_name": "Beta Entity"},
    ]
    store.collections = [
        {"id": "col-1", "name": "Collection One", "description": "first"},
        {"id": "col-2", "name": "Collection Two", "description": None},
    ]
    store.collection_members = [
        {"collection_id": "col-1", "document_id": "doc-alpha"},
        {"collection_id": "col-2", "document_id": "doc-beta"},
    ]
    store.chunk_mentions = [
        {"chunk_id": "chunk-0", "entity_name": "Alpha Entity"},
        {"chunk_id": "chunk-1", "entity_name": "Beta Entity"},
    ]
    store.merge_history = [
        {"id": "merge-1", "surviving_entity": "Alpha Entity",
         "merged_entity": "Beta Entity",
         "merged_at": "2026-05-06T07:08:09"},
    ]
    store.system_meta = [
        {"key": "community_detection_model", "value": "leiden"},
    ]
    return files


def _run_export(store, zip_path: str) -> dict:
    completed, failed = {}, {}

    def update_progress(task_id, current, total, message):
        pass

    svc = LibraryTransferService(store)
    svc.export_library("task-export", zip_path, update_progress,
                       lambda tid, result: completed.update(result),
                       lambda tid, err: failed.update({"error": err}))
    assert not failed
    return completed


def _run_import(store, zip_path: str, mode: str = "clean"):
    completed, failed = {}, {}

    def update_progress(task_id, current, total, message):
        pass

    svc = LibraryTransferService(store)
    svc.import_library("task-import", zip_path, mode, update_progress,
                       lambda tid, result: completed.update(result),
                       lambda tid, err: failed.update({"error": err}))
    return completed, failed


@pytest.fixture
def source_env(_isolate_env):
    from app.config import get_settings

    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    custom_dir = Path(settings.custom_inputs_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    custom_dir.mkdir(parents=True, exist_ok=True)
    source = ScriptedGraphStore()
    files = _populate_source(source, upload_dir, custom_dir)
    return {"source": source, "files": files,
            "upload_dir": upload_dir, "custom_dir": custom_dir}


# ---------------------------------------------------------------------------
# INV-LIB-001/002: full round-trip state continuity
# ---------------------------------------------------------------------------

def _assert_full_continuity(src: ScriptedGraphStore, tgt: ScriptedGraphStore,
                            completed: dict, original_files: Dict[str, bytes],
                            upload_dir: Path, custom_dir: Path) -> None:
    """The INV-LIB-001/002 assertion set, shared by the round-trip tests.

    Extracted so a gate-sensitivity probe can run the EXACT gate against a
    mutant store substitute (see the probe record in qa/QA_CONTRACT_RECORDS.md).
    """
    assert completed["embedding_compatible"] is True
    assert completed["warnings"] == []
    assert completed["documents_imported"] == 3
    assert completed["chunks_imported"] == 4
    assert completed["entities_imported"] == 2
    assert completed["relationships_imported"] == 1
    assert completed["communities_imported"] == 1
    assert completed["collections_imported"] == 2
    assert completed["files_imported"] == 3

    # Documents: identity + declared-comparator continuity. file_path is
    # intentionally remapped (doc-scoped restore names); processing_status
    # is forced to "completed" (already-processed corpus).
    for doc_id, original in src.documents.items():
        restored = tgt.documents[doc_id]
        assert restored["filename"] == original["filename"], doc_id
        assert restored["title"] == original["title"], doc_id
        assert restored["size"] == original["size"], doc_id
        assert restored["is_custom_input"] == original["is_custom_input"]
        expected_created = (
            # Declared comparator: Neo4j DateTime serializes to its own ISO
            # form (nanosecond precision) — pinned literal, computed
            # independently of the exporter's _serialize_value.
            "2026-01-02T03:04:05.000000000"
            if isinstance(original["created_at"], Neo4jDateTime)
            else original["created_at"]
        )
        assert restored["created_at"] == expected_created, doc_id
        assert restored["processing_status"] == "completed"
        assert Path(restored["file_path"]).name == \
            EXPECTED_RESTORE_NAMES[doc_id], doc_id

    # Chunks/entities: exact record equality (stable JSON round trip).
    assert tgt.chunks == src.chunks
    assert tgt.entities == src.entities

    # Relationships: endpoints + promised props (declared subset).
    assert len(tgt.relationships) == len(src.relationships)
    for s_rel, t_rel in zip(src.relationships, tgt.relationships):
        assert t_rel["source"] == s_rel["source"]
        assert t_rel["target"] == s_rel["target"]
        assert t_rel["rel_type"] == s_rel["rel_type"]
        for prop in REL_PROMISED_PROPS:
            assert t_rel[prop] == s_rel[prop]

    # Graph structure + membership maps: exact equality.
    assert tgt.communities == src.communities
    assert tgt.community_members == src.community_members
    assert tgt.collections == src.collections
    assert tgt.collection_members == src.collection_members
    assert tgt.chunk_mentions == src.chunk_mentions
    assert tgt.merge_history == src.merge_history
    assert tgt.system_meta == src.system_meta

    # Referenced bytes: every restored file is byte-identical to its source
    # and confined to the right instance directory (upload vs custom inputs).
    for doc_id, original_bytes in original_files.items():
        restored_path = Path(tgt.documents[doc_id]["file_path"])
        expected_dir = (custom_dir
                        if tgt.documents[doc_id]["is_custom_input"]
                        else upload_dir)
        assert restored_path.parent == expected_dir, doc_id
        assert restored_path.read_bytes() == original_bytes, doc_id


class TestExportImportRoundTrip:
    def test_manifest_and_archive_shape(self, source_env, tmp_path):
        zip_path = str(tmp_path / "export.zip")
        _run_export(source_env["source"], zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["version"] == "1.0"
        assert manifest["stats"]["document_count"] == 3
        assert manifest["stats"]["chunk_count"] == 4
        assert manifest["stats"]["entity_count"] == 2
        assert manifest["stats"]["relationship_count"] == 1
        assert manifest["stats"]["collection_count"] == 2
        assert manifest["embedding_model"] is not None
        for entry in ("documents.ndjson", "chunks.ndjson", "entities.ndjson",
                      "relationships.ndjson", "communities.ndjson",
                      "community_members.ndjson", "collections.ndjson",
                      "collection_members.ndjson", "chunk_mentions.ndjson",
                      "merge_history.ndjson", "system_meta.ndjson"):
            assert entry in names
        assert "files/doc-alpha.pdf" in names
        assert "files/doc-gamma.md" in names

    def test_records_identities_survive_import(self, source_env, tmp_path):
        zip_path = str(tmp_path / "export.zip")
        _run_export(source_env["source"], zip_path)
        target = ScriptedGraphStore()
        completed, failed = _run_import(target, zip_path)
        assert not failed
        _assert_full_continuity(
            source_env["source"], target, completed, source_env["files"],
            source_env["upload_dir"], source_env["custom_dir"],
        )


# ---------------------------------------------------------------------------
# INV-LIB-005: store-substitute protocol drift protection
# ---------------------------------------------------------------------------

def test_store_substitute_calls_still_bind_to_real_neo4j_service_contract(
        source_env, tmp_path):
    """The scripted store must not outlive the real store contract.

    ScriptedGraphStore is a hand-written stand-in for Neo4jService's
    transfer-facing surface; if the real service renames/removes a method or
    parameter, the fake would keep passing while production code breaks. This
    gate records the calls the PRODUCTION transfer code actually makes during
    one full export→import round trip and binds each recorded call against the
    REAL Neo4jService signatures — actual observed calls, not a hardcoded
    method inventory (so extra/unused stand-in methods cause no noise).
    """
    import inspect

    from app.services.neo4j_service import Neo4jService

    zip_path = str(tmp_path / "export.zip")
    _run_export(source_env["source"], zip_path)
    target = ScriptedGraphStore()
    completed, failed = _run_import(target, zip_path)
    assert not failed

    recorded = source_env["source"].calls + target.calls
    assert len(recorded) >= 20  # the transfer flow exercised both directions
    checked = set()
    for name, args, kwargs in recorded:
        real = getattr(Neo4jService, name, None)
        assert real is not None, (
            f"Neo4jService no longer offers {name!r} — the ScriptedGraphStore "
            "substitute has drifted from the real store contract"
        )
        try:
            # functools.wraps on @retry_on_transient keeps signatures truthful.
            inspect.signature(real).bind(object(), *args, **kwargs)
        except TypeError as e:
            pytest.fail(
                f"Neo4jService.{name} can no longer accept the call the "
                f"transfer code makes {args=}, {kwargs=}: {e}"
            )
        checked.add(name)
    # Spot-verify the check has teeth: the flow really did bind named
    # store-contract methods in both directions (not just get_stats).
    assert {"export_all_documents", "export_all_chunks_batched",
            "import_documents_batch", "import_relationship"} <= checked


# ---------------------------------------------------------------------------
# INV-LIB-003: clean mode refuses a non-empty target before touching it
# ---------------------------------------------------------------------------

def test_clean_mode_refuses_nonempty_target_without_destroying_it(
        source_env, tmp_path):
    zip_path = str(tmp_path / "export.zip")
    _run_export(source_env["source"], zip_path)
    target = ScriptedGraphStore()
    target.documents["preexisting"] = {"id": "preexisting",
                                       "filename": "keep_me.md"}
    target.chunks.append({"chunk": {"id": "keep-chunk"}, "document_id": "x"})
    completed, failed = _run_import(target, zip_path, mode="clean")
    assert failed and "not empty" in failed["error"]
    assert completed == {}
    # Nothing was destroyed and nothing was imported.
    assert "preexisting" in target.documents
    assert len(target.chunks) == 1
    assert len(target.collections) == 0


# ---------------------------------------------------------------------------
# INV-LIB-004 negative control: incomplete archive must not import silently
# ---------------------------------------------------------------------------

def _drop_zip_entries(src_zip: str, dst_zip: str, drop: set) -> None:
    with zipfile.ZipFile(src_zip) as zin, \
            zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if name in drop:
                continue
            zout.writestr(name, zin.read(name))


def test_incomplete_archive_reports_gap_and_restores_only_what_is_present(
        source_env, tmp_path):
    src_zip = str(tmp_path / "full.zip")
    _run_export(source_env["source"], src_zip)
    broken_zip = str(tmp_path / "incomplete.zip")
    _drop_zip_entries(src_zip, broken_zip,
                      {"chunks.ndjson", "files/doc-gamma.md"})

    target = ScriptedGraphStore()
    completed, failed = _run_import(target, broken_zip)
    # The importer completes the restorable subset and REPORTS the gaps —
    # it must not claim a silent complete restore.
    assert not failed
    assert completed["chunks_imported"] == 0
    assert completed["files_imported"] == 2
    assert any("Missing file for document" in w and "gamma" in w
               for w in completed["warnings"])
    src = source_env["source"]
    assert len(target.documents) == 3  # nodes import even without blobs
    assert target.chunks == []
    assert target.entities == src.entities
    assert target.collections == src.collections
    assert target.collection_members == src.collection_members
    # The doc whose blob was missing must not pretend to have its file:
    # its node is present, the other two restore byte-identical.
    for doc_id in ("doc-alpha", "doc-beta"):
        assert Path(target.documents[doc_id]["file_path"]).read_bytes() == \
            source_env["files"][doc_id]
