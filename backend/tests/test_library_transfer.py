"""Unit tests for library export/import NDJSON serialization primitives.

The endpoint-level cap rejection is covered by test_max_files/entities; this
exercises the streaming round-trip core that those tests mock past:
_serialize_value (Neo4j type coercion), _write_ndjson/_iter_ndjson (write->read
round-trip), _iter_ndjson_batches (batching), and _count_ndjson (parse-free count,
missing-entry safety).

Also covers the import-side file-restore hardening (crafted-archive defense):
_safe_import_basename rejects hostile file_path values instead of aborting the
import, and _doc_scoped_filename makes same-basename documents collide-free on
disk — both proven end to end through import_library with a mocked graph.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from neo4j.time import DateTime as Neo4jDateTime

from app.services.library_transfer_service import (
    LibraryTransferService,
    _count_ndjson,
    _doc_scoped_filename,
    _iter_ndjson,
    _iter_ndjson_batches,
    _safe_import_basename,
    _serialize_value,
    _write_ndjson,
)


# --- serialization -----------------------------------------------------------

def test_serialize_value_coerces_datetimes():
    assert _serialize_value(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    n = Neo4jDateTime(2026, 1, 2, 3, 4, 5, 0)
    assert _serialize_value(n) == n.isoformat()


def test_serialize_value_recurses_into_collections():
    out = _serialize_value({"a": [datetime(2026, 1, 1)], "b": ("x", 1)})
    assert out == {"a": ["2026-01-01T00:00:00"], "b": ["x", 1]}


def test_serialize_value_passthrough_scalars():
    assert _serialize_value(42) == 42
    assert _serialize_value("s") == "s"
    assert _serialize_value(None) is None


# --- NDJSON round-trip -------------------------------------------------------

def _zip_with(records):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        n = _write_ndjson(zf, "data.ndjson", records)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r"), n


def test_write_then_iter_round_trip_preserves_records():
    records = [{"id": i, "ts": datetime(2026, 1, 1)} for i in range(3)]
    zf, written = _zip_with(records)
    assert written == 3
    out = list(_iter_ndjson(zf, "data.ndjson"))
    assert out == [{"id": i, "ts": "2026-01-01T00:00:00"} for i in range(3)]


def test_iter_ndjson_missing_entry_yields_nothing():
    zf, _ = _zip_with([{"id": 1}])
    assert list(_iter_ndjson(zf, "nope.ndjson")) == []


def test_count_ndjson_counts_without_parsing():
    zf, _ = _zip_with([{"id": i} for i in range(5)])
    assert _count_ndjson(zf, "data.ndjson") == 5
    assert _count_ndjson(zf, "absent.ndjson") == 0


def test_iter_ndjson_batches_chunks_records():
    zf, _ = _zip_with([{"id": i} for i in range(7)])
    batches = list(_iter_ndjson_batches(zf, "data.ndjson", batch_size=3))
    assert [len(b) for b in batches] == [3, 3, 1]
    assert sum(len(b) for b in batches) == 7


# --- _safe_import_basename (crafted-archive defense) --------------------------

@pytest.mark.parametrize(
    "path,expected",
    [
        ("/data/uploads/uuid_report.pdf", "uuid_report.pdf"),
        ("a/b/report.pdf", "report.pdf"),
        ("sub/../../etc/passwd", "passwd"),  # directories stripped, no traversal
        ("report.txt", "report.txt"),
    ],
)
def test_safe_import_basename_accepts_plain_names(path, expected):
    assert _safe_import_basename(path) == expected


@pytest.mark.parametrize(
    "path",
    ["..", "x/..", "x/./..", ".", ""],
)
def test_safe_import_basename_rejects_hostile_names(path):
    # ".." as a basename would turn the restore target into the parent
    # directory itself (IsADirectoryError previously aborted the whole import).
    # ("x/." is fine: Path collapses the "." and the basename is plain "x".)
    assert _safe_import_basename(path) is None


# --- _doc_scoped_filename (collision-free restore names) ----------------------

def test_doc_scoped_filename_scopes_to_doc_id():
    used = set()
    a = _doc_scoped_filename("doc-a", "report.pdf", used)
    b = _doc_scoped_filename("doc-b", "report.pdf", used)
    assert a == "doc-a_report.pdf"
    assert b == "doc-b_report.pdf"
    assert a != b


def test_doc_scoped_filename_sanitizes_weird_doc_ids():
    # An archive-controlled doc id must never become a path that traverses:
    # separators flatten to "_", so the result is always a single component
    # (and always contains the fname suffix, so it can never be "." / "..").
    name = _doc_scoped_filename("../evil", "f.txt", set())
    assert "/" not in name
    assert Path(name).name == name


def test_doc_scoped_filename_dedupes_sanitize_collisions():
    used = set()
    first = _doc_scoped_filename("a/b", "f.txt", used)
    second = _doc_scoped_filename("a_b", "f.txt", used)
    assert first != second  # both sanitize to a_b → counter kicks in


# --- import_library: file restore hardening end to end ------------------------

def _make_export_zip(tmp_path, documents, files: dict) -> str:
    """A minimal valid export archive: manifest + documents.ndjson + files/*."""
    zip_path = tmp_path / "export.zip"
    manifest = {"version": "1.0", "stats": {}}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(
            "documents.ndjson",
            "\n".join(json.dumps(d) for d in documents) + "\n",
        )
        for arcname, data in files.items():
            zf.writestr(arcname, data)
    return str(zip_path)


def _transfer_svc():
    """The service with a mocked graph (empty instance → clean-mode import)."""
    neo4j = MagicMock()
    neo4j.get_stats.return_value = {"document_count": 0, "entity_count": 0}
    neo4j.import_documents_batch.side_effect = lambda docs: len(docs)
    return LibraryTransferService(neo4j), neo4j


def _run_import(svc, zip_path):
    completed, failed = {}, {}
    svc.import_library(
        "task-1",
        zip_path,
        "clean",
        lambda *args: None,
        lambda tid, result: completed.update(result),
        lambda tid, err: failed.update({"error": err}),
    )
    return completed, failed


def test_import_same_basename_docs_restore_distinct_files(tmp_path):
    svc, neo4j = _transfer_svc()
    docs = [
        {"id": "doc-a", "filename": "report.txt", "file_path": "/old/a/report.txt"},
        {"id": "doc-b", "filename": "report.txt", "file_path": "/other/b/report.txt"},
    ]
    zip_path = _make_export_zip(
        tmp_path, docs, {"files/doc-a.txt": b"AAA", "files/doc-b.txt": b"BBB"}
    )
    completed, failed = _run_import(svc, zip_path)

    assert not failed
    assert completed["files_imported"] == 2
    stored = neo4j.import_documents_batch.call_args[0][0]
    paths = [d["file_path"] for d in stored]
    assert paths[0] != paths[1]  # no silent overwrite of one doc's bytes
    assert Path(paths[0]).read_bytes() == b"AAA"
    assert Path(paths[1]).read_bytes() == b"BBB"
    # On-disk names are doc-scoped, confined to the upload dir
    from app.config import get_settings
    upload_dir = Path(get_settings().upload_dir)
    assert all(Path(p).parent == upload_dir for p in paths)
    assert {Path(p).name for p in paths} == {"doc-a_report.txt", "doc-b_report.txt"}


def test_import_hostile_file_path_skips_file_without_aborting(tmp_path):
    svc, neo4j = _transfer_svc()
    docs = [
        {"id": "evil", "filename": "evil.txt", "file_path": ".."},
        {"id": "good", "filename": "good.txt", "file_path": "/ok/good.txt"},
    ]
    zip_path = _make_export_zip(
        tmp_path, docs, {"files/evil.txt": b"E", "files/good.txt": b"G"}
    )
    completed, failed = _run_import(svc, zip_path)

    # Previously this raised IsADirectoryError and aborted the whole import.
    assert not failed
    assert completed["files_imported"] == 1
    stored = {d["id"]: d for d in neo4j.import_documents_batch.call_args[0][0]}
    # The hostile doc's node still imports — without a restorable file path.
    assert stored["evil"]["file_path"] == ""
    assert stored["good"]["file_path"].endswith("good_good.txt")
    assert any("unsafe file_path" in w for w in completed["warnings"])
    # And nothing was written outside the upload dir.
    from app.config import get_settings
    upload_dir = Path(get_settings().upload_dir)
    assert [p.name for p in upload_dir.iterdir()] == ["good_good.txt"]
