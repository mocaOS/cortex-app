"""Replace-in-place uploads keyed by source_key / replace_document_id.

Sync sources (apps, web import) re-send a changed file under a stable key; the
document keeps its id (and everything attached to it) instead of being deleted
and re-uploaded as a new document. Unchanged bytes of a processed document are
a no-op.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings


def _target(tmp_path, *, content=b"old", status="completed", ext=".md", collection="default"):
    path = os.path.join(get_settings().upload_dir, f"doc-1{ext}")
    with open(path, "wb") as fh:
        fh.write(content)
    return {
        "id": "doc-1", "filename": f"notes{ext}", "file_path": path, "file_type": ext,
        "collection_id": collection, "file_sha256": hashlib.sha256(content).hexdigest(),
        "processing_status": status, "source_key": "nextcloud:42",
    }


def test_new_upload_with_source_key_stores_key(client, mock_neo4j, mock_processors):
    mock_neo4j.find_document_by_source_key.return_value = None
    resp = client.post(
        "/api/upload?source_key=nextcloud:42",
        files={"file": ("notes.md", b"hello", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replaced"] is False and body["unchanged"] is False
    kwargs = mock_processors.doc.store_file_only.call_args.kwargs
    assert kwargs["source_key"] == "nextcloud:42"
    mock_neo4j.find_document_by_filename_and_size.assert_not_called()


def test_source_key_as_form_field(client, mock_neo4j, mock_processors):
    mock_neo4j.find_document_by_source_key.return_value = None
    resp = client.post(
        "/api/upload",
        data={"source_key": "dropbox:id:abc"},
        files={"file": ("notes.md", b"hello", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert mock_processors.doc.store_file_only.call_args.kwargs["source_key"] == "dropbox:id:abc"


def test_changed_file_replaces_in_place(client, mock_neo4j, mock_processors, tmp_path):
    target = _target(tmp_path)
    mock_neo4j.find_document_by_source_key.return_value = target
    mock_processors.doc._start_processing = AsyncMock()
    resp = client.post(
        "/api/upload?source_key=nextcloud:42&start_processing=true",
        files={"file": ("notes.md", b"new content", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document_id"] == "doc-1"
    assert body["replaced"] is True and body["unchanged"] is False
    with open(target["file_path"], "rb") as fh:
        assert fh.read() == b"new content"
    kwargs = mock_neo4j.replace_document_file.call_args.kwargs
    assert kwargs["file_path"] == target["file_path"]
    assert kwargs["file_size"] == len(b"new content")
    # Nothing is deleted up front — the pipeline decides what to reuse.
    mock_neo4j.delete_document_chunks.assert_not_called()
    mock_neo4j.delete_document.assert_not_called()
    mock_processors.doc.store_file_only.assert_not_called()
    mock_processors.doc._start_processing.assert_awaited_once_with(
        "doc-1", target["file_path"], ".md"
    )


def test_unchanged_bytes_are_a_noop(client, mock_neo4j, mock_processors, tmp_path):
    mock_neo4j.find_document_by_source_key.return_value = _target(tmp_path, content=b"same")
    resp = client.post(
        "/api/upload?source_key=nextcloud:42&start_processing=true",
        files={"file": ("notes.md", b"same", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unchanged"] is True and body["document_id"] == "doc-1"
    mock_neo4j.replace_document_file.assert_not_called()


def test_same_bytes_but_unfinished_doc_is_requeued(client, mock_neo4j, mock_processors, tmp_path):
    mock_neo4j.find_document_by_source_key.return_value = _target(
        tmp_path, content=b"same", status="failed")
    resp = client.post(
        "/api/upload?source_key=nextcloud:42",
        files={"file": ("notes.md", b"same", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unchanged"] is False
    mock_neo4j.replace_document_file.assert_called_once()


def test_extension_change_moves_file(client, mock_neo4j, mock_processors, tmp_path):
    target = _target(tmp_path, ext=".txt")
    mock_neo4j.get_document_replace_target.return_value = target
    resp = client.post(
        "/api/upload?replace_document_id=doc-1",
        files={"file": ("notes.md", b"# new", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    kwargs = mock_neo4j.replace_document_file.call_args.kwargs
    assert kwargs["file_path"].endswith("doc-1.md") and kwargs["file_type"] == ".md"
    assert not os.path.exists(target["file_path"])


def test_replace_deleted_document_falls_back_to_new_upload(client, mock_neo4j, mock_processors):
    """The user deleted the tracked doc: re-create it (with the key), never 404."""
    mock_neo4j.get_document_replace_target.return_value = None
    mock_neo4j.find_document_by_source_key.return_value = None
    resp = client.post(
        "/api/upload?replace_document_id=missing&source_key=nextcloud:42",
        files={"file": ("notes.md", b"x", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["replaced"] is False
    assert mock_processors.doc.store_file_only.call_args.kwargs["source_key"] == "nextcloud:42"
    mock_neo4j.find_document_by_filename_and_size.assert_not_called()


def test_replace_deleted_document_falls_back_to_source_key(client, mock_neo4j, mock_processors,
                                                           tmp_path):
    mock_neo4j.get_document_replace_target.return_value = None
    mock_neo4j.find_document_by_source_key.return_value = _target(tmp_path)
    resp = client.post(
        "/api/upload?replace_document_id=gone&source_key=nextcloud:42",
        files={"file": ("notes.md", b"changed", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"] == "doc-1" and resp.json()["replaced"] is True


def test_empty_keys_are_treated_as_absent(client, mock_neo4j, mock_processors):
    resp = client.post(
        "/api/upload?replace_document_id=&source_key=",
        files={"file": ("notes.md", b"x", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    mock_neo4j.get_document_replace_target.assert_not_called()
    mock_neo4j.find_document_by_source_key.assert_not_called()
    # plain upload path: the filename+size duplicate guard still runs
    mock_neo4j.find_document_by_filename_and_size.assert_called_once()


def test_plain_upload_always_reports_replace_flags(client, mock_neo4j, mock_processors):
    resp = client.post("/api/upload", files={"file": ("plain.txt", b"x", "text/plain")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replaced"] is False and body["unchanged"] is False


def test_replace_skips_file_limit(client, mock_neo4j, mock_processors, override_max_files, tmp_path):
    override_max_files(10)
    mock_neo4j.set_document_count(10)
    mock_neo4j.find_document_by_source_key.return_value = _target(tmp_path)
    resp = client.post(
        "/api/upload?source_key=nextcloud:42",
        files={"file": ("notes.md", b"changed", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text


def test_replace_requires_access_to_target_collection(client, mock_neo4j, tmp_path):
    from app.main import app
    from app.models import APIKeyPermission
    from app.services.auth_service import AuthResult, require_manage_permission

    scoped = AuthResult(
        is_authenticated=True, is_admin=False,
        permissions=[APIKeyPermission.READ, APIKeyPermission.MANAGE],
        key_id="scoped", collection_scope="restricted", allowed_collections=["default"],
    )
    app.dependency_overrides[require_manage_permission] = lambda: scoped
    mock_neo4j.find_document_by_source_key.return_value = _target(tmp_path, collection="private")
    resp = client.post(
        "/api/upload?source_key=nextcloud:42",
        files={"file": ("notes.md", b"changed", "text/markdown")},
    )
    assert resp.status_code == 403
    mock_neo4j.replace_document_file.assert_not_called()


def test_concurrent_first_uploads_create_one_document(mock_neo4j, mock_processors):
    """Two simultaneous uploads of a new key: the second sees the first's doc."""
    from app import main
    from app.services.auth_service import AuthResult

    created: dict = {}

    def find(key):
        return created.get(key)

    async def store(file_path, filename, file_size, collection_id, *, source, source_key):
        await asyncio.sleep(0.01)  # widen the race window
        created[source_key] = {"id": "doc-new", "collection_id": "default",
                               "file_path": file_path, "file_sha256": None,
                               "processing_status": "pending", "filename": filename}
        return "doc-new"

    mock_neo4j.find_document_by_source_key.side_effect = find
    mock_processors.doc.store_file_only = AsyncMock(side_effect=store)
    auth = AuthResult(is_authenticated=True, is_admin=True, permissions=[], key_id="t")

    async def one(body):
        async with main._upload_key_lock("key:k"):
            return await main._upload_replace_or_create(
                auth=auth, content=body, filename="a.md", file_ext=".md", file_size=len(body),
                collection_id=None, start_processing=False, source=None,
                source_key="k", replace_document_id=None,
            )

    async def scenario():
        return await asyncio.gather(one(b"one"), one(b"two"))

    r1, r2 = asyncio.run(scenario())
    assert mock_processors.doc.store_file_only.await_count == 1
    assert {r1.document_id, r2.document_id} == {"doc-new"}
    assert r1.replaced is not r2.replaced


@pytest.mark.parametrize("urls,expected", [
    (["https://Example.com/Docs/#intro"], "url:https://example.com/Docs"),
    (["https://example.com/"], "url:https://example.com/"),
    (["https://example.com/a/", "https://example.com/a"], "url:https://example.com/a"),
])
def test_web_import_source_key_normalisation(urls, expected):
    from app.main import _web_import_source_key
    assert _web_import_source_key(urls) == expected


def test_web_import_site_key_is_order_independent():
    from app.main import _web_import_source_key
    a = _web_import_source_key(["https://ex.com/a", "https://ex.com/b"])
    b = _web_import_source_key(["https://ex.com/b/", "https://EX.com/a#x"])
    assert a == b and a.startswith("site:ex.com:")


def test_web_import_stage_replaces_and_noops(mock_neo4j, mock_processors, tmp_path):
    from app.main import _stage_web_import_doc

    existing = tmp_path / "old.md"
    existing.write_text("old")
    target = {"id": "doc-w", "collection_id": "default", "file_path": str(existing),
              "file_sha256": hashlib.sha256(b"same").hexdigest(),
              "processing_status": "completed"}
    mock_neo4j.find_document_by_source_key.return_value = target
    new_path = str(tmp_path / "new.md")

    out = asyncio.run(_stage_web_import_doc(
        mock_processors.doc, "url:https://ex.com/a", new_path, "same", "ex.com.md", 4, None, "crawl:ex.com"))
    assert out == "unchanged"

    out = asyncio.run(_stage_web_import_doc(
        mock_processors.doc, "url:https://ex.com/a", new_path, "changed", "ex.com.md", 7, None, "crawl:ex.com"))
    assert out == "replaced"
    assert existing.read_text() == "changed" and not os.path.exists(new_path)
    mock_processors.doc.store_file_only.assert_not_called()

    mock_neo4j.find_document_by_source_key.return_value = None
    out = asyncio.run(_stage_web_import_doc(
        mock_processors.doc, "url:https://ex.com/b", new_path, "fresh", "ex.com.md", 5, None, "crawl:ex.com"))
    assert out == "created"
    assert mock_processors.doc.store_file_only.call_args.kwargs["source_key"] == "url:https://ex.com/b"


@pytest.mark.parametrize("query", [
    "replace_document_id=doc-1",
    "source_key=nextcloud:42",
])
def test_git_synced_document_cannot_be_replaced(client, mock_neo4j, tmp_path, query):
    target = {**_target(tmp_path), "git_connection_id": "conn-1"}
    mock_neo4j.get_document_replace_target.return_value = target
    mock_neo4j.find_document_by_source_key.return_value = target
    resp = client.post(f"/api/upload?{query}",
                       files={"file": ("notes.md", b"changed", "text/markdown")})
    assert resp.status_code == 409
    assert "git sync" in resp.json()["detail"]
    mock_neo4j.replace_document_file.assert_not_called()
    with open(target["file_path"], "rb") as fh:
        assert fh.read() == b"old"
