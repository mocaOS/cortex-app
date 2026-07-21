"""Individually-started pipelines are capped by BATCH_PROCESSING_CONCURRENCY.

API uploads with start_processing=true, text ingestion, and single reprocess
each spawn one asyncio task per document via _start_processing. The global
slot semaphore must keep at most batch_processing_concurrency of those
pipelines running at once — a burst of API ingests queues instead of fanning
out one pipeline per document.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
import app.services.document_processor as dp


@pytest.fixture(autouse=True)
def _fresh_module_state():
    """Reset event-loop-bound module globals around each test."""
    dp._processing_slots = None
    dp._task_lock = None
    dp._active_tasks.clear()
    dp._cancellation_flags.clear()
    yield
    dp._processing_slots = None
    dp._task_lock = None
    dp._active_tasks.clear()
    dp._cancellation_flags.clear()


def _make_processor(neo4j_mock):
    proc = object.__new__(dp.DocumentProcessor)
    proc.settings = get_settings()
    proc.neo4j = neo4j_mock
    return proc


class TestProcessingSlots:
    def test_semaphore_sized_from_settings_with_floor_of_one(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "batch_processing_concurrency", 0)
        assert dp._get_processing_slots()._value == 1

        dp._processing_slots = None
        monkeypatch.setattr(settings, "batch_processing_concurrency", 3)
        assert dp._get_processing_slots()._value == 3

    async def test_individual_starts_respect_concurrency_cap(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "batch_processing_concurrency", 2)

        neo4j = MagicMock()
        proc = _make_processor(neo4j)

        running = 0
        peak = 0
        started = 0
        release = asyncio.Event()

        async def fake_process(doc_id, file_path, file_type):
            nonlocal running, peak, started
            running += 1
            started += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1

        monkeypatch.setattr(proc, "_process_document", fake_process)

        for i in range(6):
            await proc._start_processing(f"doc-{i}", f"/tmp/doc-{i}.txt", ".txt")

        # Let the first slot-holders start and the rest hit the queued branch.
        await asyncio.sleep(0.05)
        assert running == 2 and started == 2

        # Docs beyond the cap were marked as waiting for a slot.
        queued = [
            c
            for c in neo4j.update_document_status.call_args_list
            if str(c.kwargs.get("progress_message", "")).startswith("Queued")
        ]
        assert len(queued) == 4

        release.set()
        tasks = list(dp._active_tasks.values())
        await asyncio.gather(*tasks, return_exceptions=True)

        assert started == 6  # every document eventually processed
        assert peak == 2  # the cap was never exceeded
        assert not dp._active_tasks  # all tasks unregistered

    async def test_queued_flag_set_while_waiting_and_cleared_on_slot(self, monkeypatch):
        """Docs parked on the semaphore carry processing_queued=true so the
        UI counts them as waiting, not working (a 300-doc sync-app burst must
        not read as 300 concurrent pipelines); the flag clears the moment a
        slot is acquired."""
        settings = get_settings()
        monkeypatch.setattr(settings, "batch_processing_concurrency", 1)

        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        release = asyncio.Event()

        async def fake_process(doc_id, file_path, file_type):
            await release.wait()

        monkeypatch.setattr(proc, "_process_document", fake_process)

        await proc._start_processing("doc-a", "/tmp/a.txt", ".txt")
        await proc._start_processing("doc-b", "/tmp/b.txt", ".txt")
        await asyncio.sleep(0.05)

        # only the waiting doc was flagged, and it hasn't been cleared yet
        assert neo4j.set_document_queued_state.call_args_list == [
            (("doc-b", True),)
        ]

        release.set()
        await asyncio.gather(*dp._active_tasks.values(), return_exceptions=True)

        # the flag was cleared exactly once, when doc-b took its slot
        assert neo4j.set_document_queued_state.call_args_list == [
            (("doc-b", True),),
            (("doc-b", False),),
        ]
