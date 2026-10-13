"""Aborting graph generation must stop a running git sync and unlock its connection.

Regression: a git sync's ingestion runs through process_pending_documents, which
never registers in _active_tasks, so POST /api/graph/generation/abort neither
stopped the batch nor touched the `git_repo_sync` task record — every later sync
of that connection got 409 "A sync is already running" until a restart.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.services import document_processor as dp
from app.services.git_connector_service import GitConnectorService


# --- document processor: abort reaches batch processing ----------------------

def _stub_processor(docs, started, gate):
    proc = object.__new__(dp.DocumentProcessor)
    proc.settings = SimpleNamespace(batch_processing_concurrency=1, max_queries_per_month=0)
    proc.get_pending_documents = lambda: docs

    async def _process_document(doc_id, file_path, file_type):
        started.append(doc_id)
        await gate.wait()
        proc._check_cancellation(doc_id)

    proc._process_document = _process_document
    return proc


def test_cancel_all_processing_stops_batch(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x")
    docs = [{"id": f"d{i}", "file_path": str(f), "file_type": "md"} for i in range(3)]

    async def scenario():
        started: list[str] = []
        gate = asyncio.Event()
        proc = _stub_processor(docs, started, gate)
        batch = asyncio.create_task(proc.process_pending_documents())
        while not started:
            await asyncio.sleep(0)
        cancelled = await proc.cancel_all_processing()
        gate.set()
        return cancelled, started, await batch

    cancelled, started, result = asyncio.run(scenario())
    assert cancelled == 1                  # the in-flight batch doc was signalled
    assert started == ["d0"]               # no new document started after abort
    assert result["processed"] == 0
    assert result["aborted"] == 2
    assert not dp._cancellation_flags


# --- endpoints: abort unlocks the connection ---------------------------------

@pytest.fixture
def git_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_git_integration", True)


def test_abort_fails_running_git_sync_and_allows_resync(client, mock_neo4j, mock_processors,
                                                         monkeypatch, git_enabled):
    import app.main as main
    from app.models import TaskStatus

    mock_processors.doc.cancel_all_processing = AsyncMock(return_value=0)
    mock_neo4j.reset_orphaned_processing_documents.return_value = []
    mock_neo4j.get_git_connection.return_value = {"id": "conn1"}
    monkeypatch.setattr(main, "_run_git_sync_task", AsyncMock())

    stale = main.create_task("git_repo_sync")
    stale.result = {"connection_id": "conn1"}
    stale.status = TaskStatus.RUNNING
    try:
        resp = client.post("/api/integrations/git/connections/conn1/sync")
        assert resp.status_code == 409

        assert client.post("/api/graph/generation/abort").status_code == 200
        assert main._task_store[stale.task_id].status == TaskStatus.FAILED
        assert main._task_store[stale.task_id].error == "Sync aborted"

        resp = client.post("/api/integrations/git/connections/conn1/sync")
        assert resp.status_code == 200, resp.text
    finally:
        for tid, t in list(main._task_store.items()):
            if t.task_type == "git_repo_sync":
                main._task_store.pop(tid, None)


def test_cancelled_sync_runner_fails_its_record(monkeypatch):
    import app.main as main
    from app.models import TaskStatus

    async def hang(*a, **k):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "app.services.git_connector_service.get_git_connector_service",
        lambda: SimpleNamespace(sync_connection=hang),
    )
    task = main.create_task("git_repo_sync")
    task.result = {"connection_id": "conn1"}

    async def scenario():
        runner = asyncio.create_task(main._run_git_sync_task("conn1", task.task_id))
        await asyncio.sleep(0.01)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    try:
        asyncio.run(scenario())
        assert task.status == TaskStatus.FAILED
        assert not main._git_connection_has_active_sync("conn1")
    finally:
        main._task_store.pop(task.task_id, None)


# --- re-sync after abort: finished docs are not re-ingested -------------------

class _FakeNeo4j:
    def __init__(self, existing):
        self.existing = existing

    def find_git_document(self, cid, path):
        return self.existing

    def __getattr__(self, name):  # any write would betray a re-ingest
        raise AssertionError(f"unexpected neo4j call: {name}")


@pytest.mark.parametrize("status,queued", [("completed", False), ("pending", True)])
def test_resync_same_blob_skips_finished_and_queues_unfinished(tmp_path, monkeypatch,
                                                                status, queued):
    existing = {"id": "doc1", "git_blob_sha": "abc", "git_sync_status": "synced",
                "processing_status": status}
    fake = _FakeNeo4j(existing)
    monkeypatch.setattr(GitConnectorService, "neo4j", property(lambda self: fake))
    svc = GitConnectorService()
    monkeypatch.setattr(svc, "_blob_bytes", AsyncMock(return_value=b"x"))
    monkeypatch.setattr(svc, "_content_path", lambda cid, path: tmp_path / path)
    stats = {"created": 0, "modified": 0, "skipped": 0}
    touched: list[str] = []
    asyncio.run(svc._ingest_file("conn1", tmp_path, "a.md", "abc", 1, 0, None, "sha",
                                 stats, touched))
    assert touched == (["doc1"] if queued else [])
    assert stats["modified"] == 0 and stats["created"] == 0
    assert os.path.exists(tmp_path / "a.md")
