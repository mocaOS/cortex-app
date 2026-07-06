"""Task-store persistence: serialize/deserialize, dirty-flush, restart fallback."""

import json
from datetime import datetime

from app import main as app_main
from app.models import TaskProgress, TaskStatus


def _make_task(**overrides) -> TaskProgress:
    base = dict(
        task_id="task_abc123",
        task_type="library_export",
        status=TaskStatus.RUNNING,
        progress_current=5,
        progress_total=10,
        progress_percent=50.0,
        message="halfway",
        started_at=datetime(2026, 7, 7, 12, 0, 0),
    )
    base.update(overrides)
    return TaskProgress(**base)


class TestSerialization:
    def test_round_trip(self):
        task = _make_task(
            status=TaskStatus.COMPLETED,
            completed_at=datetime(2026, 7, 7, 12, 5, 0),
            result={"exported": 42},
        )
        record = app_main._serialize_task(task)
        assert record["status"] == "completed"
        assert json.loads(record["result_json"]) == {"exported": 42}

        rebuilt = app_main._deserialize_task_record(record)
        assert rebuilt.task_id == task.task_id
        assert rebuilt.status == TaskStatus.COMPLETED
        assert rebuilt.result == {"exported": 42}
        assert rebuilt.started_at == task.started_at
        assert rebuilt.completed_at == task.completed_at

    def test_oversized_result_truncated(self):
        task = _make_task(result={"blob": "x" * (app_main._TASK_RESULT_JSON_MAX + 1)})
        record = app_main._serialize_task(task)
        assert json.loads(record["result_json"]) == {"truncated": True}

    def test_no_result_serializes_none(self):
        record = app_main._serialize_task(_make_task())
        assert record["result_json"] is None
        assert app_main._deserialize_task_record(record).result is None


class TestDirtyFlush:
    async def test_helpers_mark_dirty_and_flush_persists(self, mock_neo4j, monkeypatch):
        monkeypatch.setattr(app_main, "_task_store", {})
        monkeypatch.setattr(app_main, "_task_dirty", set())

        task = app_main.create_task("community_detection")
        app_main.update_task_progress(task.task_id, 1, 4, "step 1")
        app_main.complete_task(task.task_id, {"communities": 3})
        assert task.task_id in app_main._task_dirty

        await app_main._flush_dirty_tasks()
        assert app_main._task_dirty == set()
        (records,), _ = mock_neo4j.upsert_task_records.call_args
        assert len(records) == 1
        assert records[0]["task_id"] == task.task_id
        assert records[0]["status"] == "completed"

    async def test_failed_flush_remarks_dirty(self, mock_neo4j, monkeypatch):
        monkeypatch.setattr(app_main, "_task_store", {})
        monkeypatch.setattr(app_main, "_task_dirty", set())
        mock_neo4j.upsert_task_records.side_effect = RuntimeError("neo4j down")

        task = app_main.create_task("library_import")
        await app_main._flush_dirty_tasks()
        assert task.task_id in app_main._task_dirty  # retried next tick


class TestRestartFallback:
    def test_status_endpoint_falls_back_to_record(self, client, mock_neo4j):
        mock_neo4j.get_task_record.return_value = {
            "task_id": "task_gone",
            "task_type": "library_export",
            "status": "failed",
            "message": "Failed: interrupted by server restart",
            "error": "Interrupted by server restart",
            "started_at": "2026-07-07T12:00:00",
            "completed_at": "2026-07-07T12:01:00",
        }
        resp = client.get("/api/tasks/task_gone")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "restart" in body["error"].lower()

    def test_unknown_task_still_404s(self, client, mock_neo4j):
        mock_neo4j.get_task_record.return_value = None
        resp = client.get("/api/tasks/task_never_existed")
        assert resp.status_code == 404

    def test_result_endpoint_surfaces_persisted_failure(self, client, mock_neo4j):
        mock_neo4j.get_task_record.return_value = {
            "task_id": "task_gone",
            "task_type": "library_import",
            "status": "failed",
            "error": "Interrupted by server restart",
        }
        resp = client.get("/api/tasks/task_gone/result")
        assert resp.status_code == 500
