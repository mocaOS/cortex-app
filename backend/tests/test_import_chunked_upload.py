"""HTTP tests for the chunked import upload endpoints.

Large export ZIPs uploaded as a single request get cut off by reverse-proxy
body-read timeouts (Traefik v3 defaults to 60s), so the frontend uploads in
small sequential chunks: start -> chunk (offset-validated) -> finish. These
tests exercise the session lifecycle, offset resync contract, size validation,
and cleanup — with the actual import task stubbed out.
"""

from __future__ import annotations

import os

import pytest

from app.main import _import_upload_sessions, _task_store


@pytest.fixture(autouse=True)
def _clean_sessions():
    _import_upload_sessions.clear()
    _task_store.clear()
    yield
    for sess in _import_upload_sessions.values():
        try:
            os.unlink(sess["path"])
        except OSError:
            pass
    _import_upload_sessions.clear()
    _task_store.clear()


@pytest.fixture
def stub_transfer(monkeypatch):
    """Stub the transfer service so finish doesn't run a real import."""
    calls = []

    class _Stub:
        def import_library(self, task_id, zip_path, mode, *cbs):
            calls.append({"task_id": task_id, "zip_path": zip_path, "mode": mode})

    import app.services.library_transfer_service as lts

    monkeypatch.setattr(lts, "get_library_transfer_service", lambda: _Stub())
    return calls


def _start(client, total_size):
    res = client.post("/api/admin/import/upload/start", json={"total_size": total_size})
    assert res.status_code == 200
    return res.json()["upload_id"]


def _put_chunk(client, upload_id, offset, data: bytes):
    return client.put(
        f"/api/admin/import/upload/{upload_id}/chunk",
        params={"offset": offset},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_full_upload_assembles_file_and_starts_import(client, stub_transfer):
    payload = b"a" * 10 + b"b" * 10 + b"c" * 5
    upload_id = _start(client, len(payload))

    assert _put_chunk(client, upload_id, 0, payload[:10]).json() == {"received": 10}
    assert _put_chunk(client, upload_id, 10, payload[10:20]).json() == {"received": 20}
    assert _put_chunk(client, upload_id, 20, payload[20:]).json() == {"received": 25}

    tmp_path = _import_upload_sessions[upload_id]["path"]
    with open(tmp_path, "rb") as f:
        assert f.read() == payload

    res = client.post(f"/api/admin/import/upload/{upload_id}/finish", params={"mode": "clean"})
    assert res.status_code == 200
    assert res.json()["status"] == "pending"
    assert upload_id not in _import_upload_sessions
    assert len(stub_transfer) == 1
    assert stub_transfer[0]["zip_path"] == tmp_path
    assert stub_transfer[0]["mode"] == "clean"
    os.unlink(tmp_path)  # the stubbed import task doesn't clean up like the real one


def test_offset_mismatch_returns_409_with_server_offset(client):
    upload_id = _start(client, 20)
    _put_chunk(client, upload_id, 0, b"x" * 10)

    # Retry of an already-landed chunk: server reports where it actually is
    res = _put_chunk(client, upload_id, 0, b"x" * 10)
    assert res.status_code == 409
    assert res.json()["detail"]["received"] == 10


def test_finish_rejects_incomplete_upload(client):
    upload_id = _start(client, 100)
    _put_chunk(client, upload_id, 0, b"x" * 10)

    res = client.post(f"/api/admin/import/upload/{upload_id}/finish")
    assert res.status_code == 400
    assert "incomplete" in res.json()["detail"].lower()
    # Session survives so the client can keep uploading
    assert upload_id in _import_upload_sessions


def test_oversized_upload_discards_session(client):
    upload_id = _start(client, 5)
    tmp_path = _import_upload_sessions[upload_id]["path"]

    res = _put_chunk(client, upload_id, 0, b"x" * 10)
    assert res.status_code == 400
    assert upload_id not in _import_upload_sessions
    assert not os.path.exists(tmp_path)


def test_unknown_session_returns_404(client):
    assert _put_chunk(client, "nope", 0, b"x").status_code == 404
    assert client.post("/api/admin/import/upload/nope/finish").status_code == 404


def test_abort_removes_session_and_file(client):
    upload_id = _start(client, 10)
    _put_chunk(client, upload_id, 0, b"x" * 4)
    tmp_path = _import_upload_sessions[upload_id]["path"]

    res = client.delete(f"/api/admin/import/upload/{upload_id}")
    assert res.status_code == 200
    assert upload_id not in _import_upload_sessions
    assert not os.path.exists(tmp_path)


def test_start_validates_total_size(client):
    res = client.post("/api/admin/import/upload/start", json={"total_size": 0})
    assert res.status_code == 422
