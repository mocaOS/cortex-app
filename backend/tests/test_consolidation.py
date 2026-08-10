"""Consolidation scheduler (_maybe_run_consolidation) — trigger logic.

The scheduler is env-gated (default off) and must only fire when the instance
is idle, the cooldown has passed, and there is actual work (stale communities
or corpus growth past the threshold). It launches the standard
community-detection task; these tests stub that out and assert on the
decision, the SystemMeta bookkeeping, and every skip path.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.main as main_mod
from app.main import _maybe_run_consolidation


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime.now(timezone.utc)


def _stats(**overrides):
    base = {
        "pending_count": 0,
        "processing_count": 0,
        "entity_count": 500,
        "completed_count": 100,
        "community_count": 10,
        "last_community_detection_at": _iso(NOW - timedelta(days=3)),
        "last_relationship_analysis_at": _iso(NOW - timedelta(days=5)),
        "last_entity_merge_at": _iso(NOW - timedelta(days=5)),
    }
    base.update(overrides)
    return base


@pytest.fixture
def sched_env(_isolate_env, mock_neo4j, monkeypatch):
    """Enabled scheduler on an idle instance with a mocked launch path."""
    _isolate_env.enable_consolidation_scheduler = True
    _isolate_env.enable_community_detection = True
    _isolate_env.consolidation_min_new_documents = 25
    _isolate_env.consolidation_idle_minutes = 60
    _isolate_env.consolidation_cooldown_hours = 24

    monkeypatch.setattr(main_mod, "_active_query_count", 0)
    monkeypatch.setattr(
        "app.services.document_processor.get_active_processing_ids", lambda: []
    )

    meta: dict = {}
    mock_neo4j._get_meta.side_effect = lambda key: meta.get(key)
    mock_neo4j.set_meta.side_effect = lambda key, value: meta.__setitem__(key, value)
    mock_neo4j.get_stats.return_value = _stats()

    launched = []

    async def fake_detection(task_id, min_size, collection_id):
        launched.append((task_id, min_size, collection_id))

    monkeypatch.setattr(main_mod, "_run_community_detection_task", fake_detection)

    class Env:
        pass

    env = Env()
    env.meta = meta
    env.launched = launched
    env.settings = _isolate_env
    env.neo4j = mock_neo4j
    return env


class TestConsolidationScheduler:
    async def test_disabled_is_total_noop(self, sched_env):
        sched_env.settings.enable_consolidation_scheduler = False
        await _maybe_run_consolidation()
        assert not sched_env.neo4j.get_stats.called
        assert sched_env.launched == []

    async def test_stale_communities_trigger_run(self, sched_env):
        # relationships analyzed AFTER the last detection → stale
        sched_env.neo4j.get_stats.return_value = _stats(
            last_relationship_analysis_at=_iso(NOW - timedelta(days=1)),
        )
        await _maybe_run_consolidation()
        await asyncio.sleep(0)  # let the scheduled detection coroutine run
        assert len(sched_env.launched) == 1
        assert "last_consolidation_at" in sched_env.meta
        assert sched_env.meta["consolidation_doc_baseline"] == "100"

    async def test_entity_merge_staleness_triggers(self, sched_env):
        sched_env.neo4j.get_stats.return_value = _stats(
            last_entity_merge_at=_iso(NOW - timedelta(hours=2)),
        )
        await _maybe_run_consolidation()
        await asyncio.sleep(0)  # let the scheduled detection coroutine run
        assert len(sched_env.launched) == 1

    async def test_fresh_graph_no_growth_skips(self, sched_env):
        sched_env.meta["consolidation_doc_baseline"] = "100"
        await _maybe_run_consolidation()  # detection newest, no growth
        assert sched_env.launched == []

    async def test_growth_past_threshold_triggers(self, sched_env):
        sched_env.meta["consolidation_doc_baseline"] = "70"  # +30 ≥ 25
        await _maybe_run_consolidation()
        await asyncio.sleep(0)  # let the scheduled detection coroutine run
        assert len(sched_env.launched) == 1
        assert sched_env.meta["consolidation_doc_baseline"] == "100"

    async def test_growth_below_threshold_skips(self, sched_env):
        sched_env.meta["consolidation_doc_baseline"] = "90"  # +10 < 25
        await _maybe_run_consolidation()
        assert sched_env.launched == []

    async def test_first_check_records_baseline_only(self, sched_env):
        # no baseline yet, graph not stale → record and wait
        await _maybe_run_consolidation()
        assert sched_env.meta["consolidation_doc_baseline"] == "100"
        assert sched_env.launched == []

    async def test_active_queries_block(self, sched_env, monkeypatch):
        monkeypatch.setattr(main_mod, "_active_query_count", 1)
        sched_env.neo4j.get_stats.return_value = _stats(
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        assert sched_env.launched == []

    async def test_active_pipelines_block(self, sched_env, monkeypatch):
        monkeypatch.setattr(
            "app.services.document_processor.get_active_processing_ids",
            lambda: ["doc-1"],
        )
        sched_env.neo4j.get_stats.return_value = _stats(
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        assert sched_env.launched == []

    async def test_recent_ask_activity_blocks(self, sched_env):
        sched_env.meta["last_query_at"] = _iso(NOW - timedelta(minutes=5))
        sched_env.neo4j.get_stats.return_value = _stats(
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        assert sched_env.launched == []

    async def test_ingestion_backlog_blocks(self, sched_env):
        sched_env.neo4j.get_stats.return_value = _stats(
            pending_count=3,
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        assert sched_env.launched == []

    async def test_cooldown_blocks(self, sched_env):
        sched_env.meta["last_consolidation_at"] = _iso(NOW - timedelta(hours=2))
        sched_env.neo4j.get_stats.return_value = _stats(
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        assert sched_env.launched == []

    async def test_cooldown_expiry_allows(self, sched_env):
        sched_env.meta["last_consolidation_at"] = _iso(NOW - timedelta(hours=30))
        sched_env.neo4j.get_stats.return_value = _stats(
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        await asyncio.sleep(0)  # let the scheduled detection coroutine run
        assert len(sched_env.launched) == 1

    async def test_empty_graph_skips(self, sched_env):
        sched_env.neo4j.get_stats.return_value = _stats(
            entity_count=0,
            last_relationship_analysis_at=_iso(NOW),
        )
        await _maybe_run_consolidation()
        assert sched_env.launched == []
