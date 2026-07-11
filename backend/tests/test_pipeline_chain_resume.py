"""Unit tests for pipeline-chain persistence across server restarts.

The Generate Graph flow chains Steps 1 → 2 → 3 via in-process asyncio tasks,
so the chain used to live only in coroutine memory: a restart mid-run (dev
reload, redeploy) killed it, and the startup auto-resume restarted Step 1
WITHOUT the chain — the pipeline visibly stopped after Step 1. These tests
pin the fix: the chain (plus step parameters) is persisted as each task's
`resume_context`, survives the TaskRecord round-trip, and
`_pick_interrupted_pipeline_step` recovers the newest interrupted step for
the startup auto-resume.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import main
from app.main import (
    _deserialize_task_record,
    _pick_interrupted_pipeline_step,
    _serialize_task,
    create_task,
)
from app.services.neo4j_service import Neo4jService


# ---------------------------------------------------------------------------
# _pick_interrupted_pipeline_step
# ---------------------------------------------------------------------------

def test_pick_returns_none_without_pipeline_records():
    assert _pick_interrupted_pipeline_step([]) is None
    assert _pick_interrupted_pipeline_step([
        {"task_type": "web_import", "started_at": "2026-07-09T10:00:00"},
        {"task_type": "image_analysis_resume", "started_at": "2026-07-09T11:00:00"},
    ]) is None


def test_pick_chooses_newest_pipeline_step_and_decodes_context():
    records = [
        {
            "task_type": "reprocess_batch",
            "started_at": "2026-07-09T10:00:00",
            "context_json": json.dumps({"concurrency": 3, "chain": ["relationship_analysis", "community_detection"]}),
        },
        {
            "task_type": "relationship_analysis",
            "started_at": "2026-07-09T11:00:00",
            "context_json": json.dumps({"collection_id": None, "scope": "full", "rebuild": True, "chain": ["community_detection"]}),
        },
        {"task_type": "web_import", "started_at": "2026-07-09T12:00:00"},
    ]
    picked = _pick_interrupted_pipeline_step(records)
    assert picked["task_type"] == "relationship_analysis"
    assert picked["context"]["chain"] == ["community_detection"]
    assert picked["context"]["rebuild"] is True


def test_pick_tolerates_missing_or_malformed_context():
    picked = _pick_interrupted_pipeline_step([
        {"task_type": "batch_processing", "started_at": "2026-07-09T10:00:00", "context_json": "{not json"},
    ])
    assert picked == {"task_type": "batch_processing", "context": {}}

    picked = _pick_interrupted_pipeline_step([
        {"task_type": "batch_processing", "started_at": None},
    ])
    assert picked == {"task_type": "batch_processing", "context": {}}


# ---------------------------------------------------------------------------
# resume_context persistence round-trip
# ---------------------------------------------------------------------------

def test_resume_context_survives_task_record_round_trip():
    ctx = {"concurrency": 5, "chain": ["relationship_analysis", "community_detection"]}
    task = create_task("reprocess_batch", resume_context=ctx)
    try:
        record = _serialize_task(task)
        assert json.loads(record["context_json"]) == ctx

        restored = _deserialize_task_record(record)
        assert restored.resume_context == ctx
    finally:
        main._task_store.pop(task.task_id, None)
        main._task_dirty.discard(task.task_id)
        main._task_last_touch.pop(task.task_id, None)


def test_task_without_context_serializes_none():
    task = create_task("community_detection")
    try:
        record = _serialize_task(task)
        assert record["context_json"] is None
        assert _deserialize_task_record(record).resume_context is None
    finally:
        main._task_store.pop(task.task_id, None)
        main._task_dirty.discard(task.task_id)
        main._task_last_touch.pop(task.task_id, None)


# ---------------------------------------------------------------------------
# Neo4jService.fail_interrupted_task_records returns the interrupted records
# ---------------------------------------------------------------------------

@pytest.fixture
def svc_with_session():
    svc = object.__new__(Neo4jService)
    session = MagicMock()

    @contextmanager
    def _session_cm():
        yield session

    driver = MagicMock()
    driver.session.side_effect = lambda *a, **k: _session_cm()
    svc._driver = driver  # back the read-only `driver` property
    return svc, session


def test_fail_interrupted_returns_records_with_context(svc_with_session):
    svc, session = svc_with_session
    session.run.return_value = [
        {
            "task_id": "task_abc", "task_type": "reprocess_batch",
            "context_json": '{"chain": ["relationship_analysis"]}',
            "started_at": "2026-07-09T10:00:00",
        },
    ]

    out = svc.fail_interrupted_task_records()

    assert out == [{
        "task_id": "task_abc", "task_type": "reprocess_batch",
        "context_json": '{"chain": ["relationship_analysis"]}',
        "started_at": "2026-07-09T10:00:00",
    }]
    cypher = session.run.call_args.args[0]
    assert "'pending'" in cypher and "'running'" in cypher
    assert "t.context_json" in cypher


def test_fail_interrupted_no_records(svc_with_session):
    svc, session = svc_with_session
    session.run.return_value = []
    assert svc.fail_interrupted_task_records() == []


# ---------------------------------------------------------------------------
# Chained Step 2 spawn carries a resume_context with the remaining chain
# ---------------------------------------------------------------------------

async def test_batch_task_spawns_rel_task_with_resume_context(monkeypatch, mock_neo4j):
    processor = MagicMock()
    processor.get_pending_documents = MagicMock(return_value=[])
    monkeypatch.setattr(main, "get_document_processor", lambda: processor)
    monkeypatch.setattr(main, "_wait_for_image_analysis_complete", AsyncMock())

    spawned = []

    def _capture(coro):
        spawned.append(coro)
        coro.close()  # never run the follow-up step in this unit test
        return MagicMock()

    monkeypatch.setattr(main, "_spawn_chain_task", _capture)

    before = set(main._task_store)
    step1 = create_task("reprocess_batch")
    await main._run_batch_processing_task(
        step1.task_id, concurrency=2,
        chain=["relationship_analysis", "community_detection"],
    )

    assert len(spawned) == 1
    new_tasks = [
        t for tid, t in main._task_store.items()
        if tid not in before and tid != step1.task_id
    ]
    try:
        assert len(new_tasks) == 1
        rel_task = new_tasks[0]
        assert rel_task.task_type == "relationship_analysis"
        # A restart during Step 2 must be able to resume it AND continue to Step 3.
        assert rel_task.resume_context == {
            "collection_id": None, "scope": "full", "rebuild": True,
            "chain": ["community_detection"],
        }
    finally:
        for t in [step1, *new_tasks]:
            main._task_store.pop(t.task_id, None)
            main._task_dirty.discard(t.task_id)
            main._task_last_touch.pop(t.task_id, None)
