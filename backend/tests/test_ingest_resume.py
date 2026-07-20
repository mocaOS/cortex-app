"""Tests for mid-document ingest checkpoint/resume (enable_ingest_resume)
and the LLM-endpoint outage pause (llm_outage_max_wait_seconds).

Covers:
- connection-error classification (_is_connection_error) — distinct from
  timeouts (split-retry) and rate limits (requeue) in the entity batch loop
- entity extraction: connection errors requeue the batch and wait for the
  endpoint instead of silently dropping it; past the budget the run raises
  ExtractionEndpointUnavailable so the caller keeps its checkpoint
- per-batch checkpoint hook (on_batch_entities) + skip_chunk_indices resume
- DocumentProcessor._prepare_ingest_resume decision matrix
- _cleanup_before_reprocess keeping resumable chunks
- watermark range codec (_ranges_to_indices / _indices_to_ranges)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.services.graph_extractor import (
    ExtractionEndpointUnavailable,
    GraphExtractor,
    _is_connection_error,
)


ENTITY_XML = (
    '<entity name="Alice"><type>Person</type>'
    "<description>Alice works at Acme.</description></entity>"
)
SMALL_CHUNKS = ["Alice works at Acme.", "Acme builds rockets."]


@pytest.fixture
def extractor():
    return GraphExtractor()


def _resp(content: str, finish_reason: str = "stop") -> MagicMock:
    r = MagicMock()
    choice = MagicMock()
    choice.finish_reason = finish_reason
    r.choices = [choice]
    r._content = content
    return r


def _wire_llm(extractor, monkeypatch, responses):
    """Fake extraction client + canned per-call responses."""
    monkeypatch.setattr(
        GraphExtractor, "async_extraction_client",
        property(lambda self: MagicMock()),
    )
    monkeypatch.setattr(
        GraphExtractor, "extraction_model_name",
        property(lambda self: "fake-model"),
    )
    extractor._async_safe_completion = AsyncMock(side_effect=responses)
    monkeypatch.setattr(
        extractor, "_extract_response_content", lambda response: response._content
    )
    extractor.generate_document_summary_async = AsyncMock(
        return_value="CANNED SUMMARY"
    )


def _no_sleep(monkeypatch):
    async def fake_sleep(_delay):
        pass

    monkeypatch.setattr("app.services.graph_extractor.asyncio.sleep", fake_sleep)


# ---------------------------------------------------------------------------
# Connection-error classification
# ---------------------------------------------------------------------------


class TestConnectionErrorClassification:
    def test_matches_by_class_name(self):
        class APIConnectionError(Exception):
            pass

        class ConnectError(Exception):
            pass

        class ConnectTimeout(Exception):
            pass

        assert _is_connection_error(APIConnectionError("boom"))
        assert _is_connection_error(ConnectError("boom"))
        # A connect timeout is an outage symptom, not a slow-decode symptom.
        assert _is_connection_error(ConnectTimeout("boom"))

    def test_matches_by_message_and_status(self):
        assert _is_connection_error(Exception("Connection error."))
        assert _is_connection_error(Exception("connection refused by host"))
        exc = Exception("bad gateway")
        exc.status_code = 502
        assert _is_connection_error(exc)

    def test_read_timeout_is_not_a_connection_error(self):
        # openai APITimeoutError subclasses APIConnectionError, but the
        # name-based check must route it to the split-retry arm instead.
        class APITimeoutError(Exception):
            pass

        assert not _is_connection_error(APITimeoutError("Request timed out."))
        assert not _is_connection_error(Exception("Request timed out."))
        assert not _is_connection_error(Exception("rate limit exceeded"))


# ---------------------------------------------------------------------------
# Entity extraction: outage pause instead of silent batch drops
# ---------------------------------------------------------------------------


class TestEntityExtractionOutage:
    async def test_connection_error_requeues_batch_and_recovers(
        self, extractor, monkeypatch
    ):
        _no_sleep(monkeypatch)
        _wire_llm(
            extractor,
            monkeypatch,
            [Exception("Connection error."), _resp(ENTITY_XML)],
        )
        run_stats: dict = {}
        entities = await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, run_stats=run_stats
        )
        # The batch was retried, not dropped: same prompt sent twice, the
        # entity landed, and nothing was counted as a hard error.
        assert [e.name for e in entities] == ["Alice"]
        assert extractor._async_safe_completion.await_count == 2
        assert run_stats["connection_retries"] == 1
        assert run_stats["errors"] == 0
        prompts = [
            c.kwargs["messages"][1]["content"]
            for c in extractor._async_safe_completion.await_args_list
        ]
        assert prompts[0] == prompts[1]

    async def test_connection_error_does_not_split_multi_chunk_batch(
        self, extractor, monkeypatch
    ):
        _no_sleep(monkeypatch)
        _wire_llm(
            extractor,
            monkeypatch,
            [Exception("Connection error."), _resp(ENTITY_XML)],
        )
        run_stats: dict = {}
        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, run_stats=run_stats
        )
        assert run_stats["timeout_splits"] == 0
        assert run_stats["connection_retries"] == 1

    async def test_outage_state_callback_fires_on_transitions_only(
        self, extractor, monkeypatch
    ):
        # Two connection failures then success: the paused-state callback must
        # fire once for entering the outage and once for recovery — not per
        # retry — so the UI flag isn't rewritten on every probe.
        _no_sleep(monkeypatch)
        _wire_llm(
            extractor,
            monkeypatch,
            [
                Exception("Connection error."),
                Exception("Connection error."),
                _resp(ENTITY_XML),
            ],
        )
        transitions: list = []

        async def on_outage(active, reason):
            transitions.append((active, reason))

        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, on_outage_state=on_outage
        )
        assert [t[0] for t in transitions] == [True, False]
        assert "unreachable" in transitions[0][1]

    async def test_outage_state_callback_failure_never_breaks_the_run(
        self, extractor, monkeypatch
    ):
        _no_sleep(monkeypatch)
        _wire_llm(
            extractor,
            monkeypatch,
            [Exception("Connection error."), _resp(ENTITY_XML)],
        )

        async def broken_callback(active, reason):
            raise RuntimeError("ui flag write failed")

        entities = await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, on_outage_state=broken_callback
        )
        assert [e.name for e in entities] == ["Alice"]

    async def test_outage_budget_exhausted_raises_with_stats(
        self, extractor, monkeypatch
    ):
        _no_sleep(monkeypatch)
        _wire_llm(extractor, monkeypatch, [Exception("Connection error.")] * 5)
        monkeypatch.setattr(
            extractor.settings, "llm_outage_max_wait_seconds", 0
        )
        run_stats: dict = {}
        with pytest.raises(ExtractionEndpointUnavailable):
            await extractor.extract_entities_from_document_async(
                chunks=SMALL_CHUNKS, max_tokens=32768, run_stats=run_stats
            )
        # Stats were flushed before raising, and the batch was never
        # misclassified as a generic error (which would have dropped it).
        assert run_stats["errors"] == 0


# ---------------------------------------------------------------------------
# Entity extraction: checkpoint hook + resume skip
# ---------------------------------------------------------------------------


class TestExtractionCheckpoint:
    async def test_on_batch_entities_receives_entities_and_chunk_range(
        self, extractor, monkeypatch
    ):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML)])
        calls: list = []

        async def hook(entities, lo, hi):
            calls.append(([e.name for e in entities], lo, hi))

        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, on_batch_entities=hook
        )
        assert calls == [(["Alice"], 0, 2)]

    async def test_skip_chunk_indices_excludes_done_chunks(
        self, extractor, monkeypatch
    ):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML)])
        calls: list = []

        async def hook(entities, lo, hi):
            calls.append((lo, hi))

        progress: list = []

        async def cb(done, total, detail=""):
            progress.append((done, total))

        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS,
            max_tokens=32768,
            on_batch_entities=hook,
            skip_chunk_indices={0},
            progress_callback=cb,
        )
        # Only chunk 1 was packed; its prompt excludes chunk 0's text.
        assert extractor._async_safe_completion.await_count == 1
        prompt = extractor._async_safe_completion.await_args.kwargs["messages"][1]["content"]
        assert SMALL_CHUNKS[1] in prompt
        assert SMALL_CHUNKS[0] not in prompt
        assert calls == [(1, 2)]
        # Progress resumes at 1/2 instead of restarting at 0.
        assert progress[0][0] == 1
        assert all(total == 2 for _, total in progress)

    async def test_all_chunks_skipped_makes_no_llm_calls(
        self, extractor, monkeypatch
    ):
        _wire_llm(extractor, monkeypatch, [])
        entities = await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, skip_chunk_indices={0, 1}
        )
        assert entities == []
        assert extractor._async_safe_completion.await_count == 0

    async def test_checkpoint_hook_failure_propagates(
        self, extractor, monkeypatch
    ):
        # A hook that can't persist must fail the run — swallowing it would
        # double-spend the batch on the next resume.
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML)])

        async def hook(entities, lo, hi):
            raise RuntimeError("neo4j write lost")

        with pytest.raises(RuntimeError, match="neo4j write lost"):
            await extractor.extract_entities_from_document_async(
                chunks=SMALL_CHUNKS, max_tokens=32768, on_batch_entities=hook
            )


# ---------------------------------------------------------------------------
# DocumentProcessor: watermark codec
# ---------------------------------------------------------------------------


def _make_processor(neo4j_mock):
    from app.services.document_processor import DocumentProcessor

    proc = object.__new__(DocumentProcessor)
    proc.settings = get_settings()
    proc.neo4j = neo4j_mock
    return proc


class TestWatermarkCodec:
    def test_round_trip(self):
        from app.services.document_processor import DocumentProcessor

        indices = {0, 1, 2, 5, 6, 9}
        ranges = DocumentProcessor._indices_to_ranges(indices)
        assert ranges == [[0, 3], [5, 7], [9, 10]]
        import json

        assert DocumentProcessor._ranges_to_indices(json.dumps(ranges)) == indices

    def test_malformed_watermark_degrades_to_empty(self):
        from app.services.document_processor import DocumentProcessor

        assert DocumentProcessor._ranges_to_indices(None) == set()
        assert DocumentProcessor._ranges_to_indices("") == set()
        assert DocumentProcessor._ranges_to_indices("not json") == set()
        assert DocumentProcessor._ranges_to_indices('{"a": 1}') == set()


# ---------------------------------------------------------------------------
# DocumentProcessor: resume preparation decision matrix
# ---------------------------------------------------------------------------


class TestPrepareIngestResume:
    def _fingerprint(self, proc, file_path, **overrides):
        fp = {
            "file_sha256": proc._file_sha256(file_path),
            "config_hash": proc._reprocess_config_hash(),
            "processing_status": "extracting",
            "entity_count": -1,
            "unembedded_chunk_count": 0,
            "text_chunk_count": 2,
        }
        fp.update(overrides)
        return fp

    def _chunks(self):
        return [
            {"id": "doc-1_chunk_0", "content": "Alice works at Acme."},
            {"id": "doc-1_chunk_1", "content": "Acme builds rockets."},
        ]

    def _setup(self, tmp_path, monkeypatch, enabled=True):
        f = tmp_path / "doc.md"
        f.write_text("hello world")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        # graph_extractor only needed for _reprocess_config_hash
        proc.graph_extractor = MagicMock(
            extraction_model_name="m", relationship_model_name="m"
        )
        monkeypatch.setattr(proc.settings, "enable_ingest_resume", enabled)
        return proc, neo4j, str(f)

    async def test_flag_off_returns_default_and_touches_nothing(
        self, tmp_path, monkeypatch
    ):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch, enabled=False)
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is False and out["resumed"] is False
        neo4j.get_document_fingerprint.assert_not_called()
        neo4j.set_document_fingerprint.assert_not_called()

    async def test_interrupted_run_resumes_with_checkpoint(
        self, tmp_path, monkeypatch
    ):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = self._fingerprint(proc, path)
        neo4j.get_text_chunks_for_document.return_value = self._chunks()
        neo4j.get_document_extraction_progress.return_value = "[[0, 1]]"
        neo4j.get_rels_extracted_chunk_ids.return_value = ["doc-1_chunk_0"]
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is True
        assert out["resumed"] is True
        assert out["done_indices"] == {0}
        assert out["rels_done_chunk_ids"] == {"doc-1_chunk_0"}
        neo4j.delete_document_chunks.assert_not_called()
        neo4j.clear_ingest_checkpoint.assert_not_called()
        neo4j.set_document_fingerprint.assert_called_once()

    async def test_completed_doc_reuses_chunks_but_clears_checkpoint(
        self, tmp_path, monkeypatch
    ):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = self._fingerprint(
            proc, path, processing_status="completed"
        )
        neo4j.get_text_chunks_for_document.return_value = self._chunks()
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is True
        assert out["resumed"] is False
        assert out["done_indices"] == set()
        neo4j.clear_ingest_checkpoint.assert_called_once_with("doc-1")
        neo4j.get_document_extraction_progress.assert_not_called()

    async def test_fingerprint_mismatch_clears_stale_chunks(
        self, tmp_path, monkeypatch
    ):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = self._fingerprint(
            proc, path, config_hash="something-else"
        )
        neo4j.delete_document_chunks.return_value = {
            "chunks_deleted": 2,
            "orphaned_entities_removed": 0,
        }
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is False
        neo4j.delete_document_chunks.assert_called_once_with("doc-1")
        neo4j.clear_ingest_checkpoint.assert_called_once_with("doc-1")
        neo4j.set_document_fingerprint.assert_called_once()

    async def test_unembedded_chunks_force_full_run(self, tmp_path, monkeypatch):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = self._fingerprint(
            proc, path, unembedded_chunk_count=1
        )
        neo4j.delete_document_chunks.return_value = {
            "chunks_deleted": 2,
            "orphaned_entities_removed": 0,
        }
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is False

    async def test_fresh_document_records_fingerprint_only(
        self, tmp_path, monkeypatch
    ):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = None
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is False
        neo4j.delete_document_chunks.assert_not_called()
        neo4j.set_document_fingerprint.assert_called_once()

    async def test_neo4j_failure_degrades_to_full_run(self, tmp_path, monkeypatch):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.side_effect = RuntimeError("db down")
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is False and out["resumed"] is False

    async def test_empty_stored_content_bails_to_full_run(
        self, tmp_path, monkeypatch
    ):
        # Watermark indices are positions in the chunk list — a blank stored
        # chunk would shift them, so reuse must be refused.
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = self._fingerprint(proc, path)
        neo4j.get_text_chunks_for_document.return_value = [
            {"id": "doc-1_chunk_0", "content": "   "},
            {"id": "doc-1_chunk_1", "content": "Acme builds rockets."},
        ]
        out = await proc._prepare_ingest_resume("doc-1", path)
        assert out["reuse_chunks"] is False


# ---------------------------------------------------------------------------
# Reprocess cleanup: keep resumable chunks
# ---------------------------------------------------------------------------


class TestCleanupBeforeReprocess:
    def _setup(self, tmp_path, monkeypatch, enabled=True):
        f = tmp_path / "doc.md"
        f.write_text("hello world")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.graph_extractor = MagicMock(
            extraction_model_name="m", relationship_model_name="m"
        )
        neo4j.delete_document_chunks.return_value = {
            "chunks_deleted": 0,
            "orphaned_entities_removed": 0,
        }
        monkeypatch.setattr(proc.settings, "enable_ingest_resume", enabled)
        return proc, neo4j, str(f)

    def test_matching_fingerprint_keeps_chunks(self, tmp_path, monkeypatch):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = {
            "file_sha256": proc._file_sha256(path),
            "config_hash": proc._reprocess_config_hash(),
            "text_chunk_count": 5,
        }
        proc._cleanup_before_reprocess("doc-1", path)
        neo4j.delete_document_chunks.assert_not_called()

    def test_mismatch_deletes_chunks(self, tmp_path, monkeypatch):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch)
        neo4j.get_document_fingerprint.return_value = {
            "file_sha256": "different",
            "config_hash": proc._reprocess_config_hash(),
            "text_chunk_count": 5,
        }
        proc._cleanup_before_reprocess("doc-1", path)
        neo4j.delete_document_chunks.assert_called_once_with("doc-1")

    def test_flag_off_always_deletes(self, tmp_path, monkeypatch):
        proc, neo4j, path = self._setup(tmp_path, monkeypatch, enabled=False)
        proc._cleanup_before_reprocess("doc-1", path)
        neo4j.delete_document_chunks.assert_called_once_with("doc-1")
        neo4j.get_document_fingerprint.assert_not_called()
