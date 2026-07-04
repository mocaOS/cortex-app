"""Tests for the failed/degraded-documents feature.

A document is *degraded* when it completed processing but is unusable for
retrieval: graph extraction ran and produced 0 entities, or chunks are missing
embeddings. Covered here:

- `update_document_status` persists `entity_count` only when provided
- `store_chunk` mirrors the embedding into a `has_embedding` boolean
- `backfill_degraded_document_signals` runs batched (`CALL {} IN TRANSACTIONS`)
  idempotent updates, and skips entity-count backfill when extraction is off
- `get_all_documents` / `get_document` / `get_document_fingerprint` expose the
  degraded signals (entity_count coalesced to -1 = unknown, unembedded count
  only counting `has_embedding = false`, never NULL)
- `_reprocess_delta_skip` bypasses the delta skip for degraded documents so an
  unchanged file+config cannot no-op the reprocess that would fix them
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.models import DocumentChunk, ProcessingStatus
from app.services.neo4j_service import Neo4jService


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


# ---------------------------------------------------------------------------
# update_document_status: optional entity_count persistence
# ---------------------------------------------------------------------------


class TestUpdateDocumentStatusEntityCount:
    def test_sets_entity_count_when_provided(self, svc_with_session):
        svc, session = svc_with_session
        svc.update_document_status(
            "doc-1", ProcessingStatus.COMPLETED, chunk_count=5, entity_count=42
        )
        cypher = session.run.call_args.args[0]
        kwargs = session.run.call_args.kwargs
        assert "d.entity_count = $entity_count" in cypher
        assert kwargs["entity_count"] == 42
        assert kwargs["status"] == "completed"

    def test_zero_entity_count_is_persisted(self, svc_with_session):
        # 0 is the degraded signal itself — it must not be dropped as falsy.
        svc, session = svc_with_session
        svc.update_document_status(
            "doc-1", ProcessingStatus.COMPLETED, chunk_count=5, entity_count=0
        )
        assert session.run.call_args.kwargs["entity_count"] == 0
        assert "d.entity_count" in session.run.call_args.args[0]

    def test_entity_count_untouched_when_omitted(self, svc_with_session):
        # With extraction disabled the caller omits entity_count; the query
        # must not SET (or null out) the property.
        svc, session = svc_with_session
        svc.update_document_status("doc-1", ProcessingStatus.COMPLETED, chunk_count=5)
        cypher = session.run.call_args.args[0]
        assert "entity_count" not in cypher
        assert "entity_count" not in session.run.call_args.kwargs


# ---------------------------------------------------------------------------
# store_chunk: has_embedding boolean mirror
# ---------------------------------------------------------------------------


class TestStoreChunkHasEmbedding:
    def _chunk(self, embedding):
        return DocumentChunk(
            id="c-1", document_id="d-1", content="text",
            embedding=embedding, chunk_index=0,
        )

    def test_true_when_embedded(self, svc_with_session):
        svc, session = svc_with_session
        session.run.return_value.single.return_value = {"id": "c-1"}
        svc.store_chunk(self._chunk([0.1, 0.2]))
        assert "c.has_embedding = $has_embedding" in session.run.call_args.args[0]
        assert session.run.call_args.kwargs["has_embedding"] is True

    def test_false_when_embedding_missing(self, svc_with_session):
        # Image chunks can be stored with embedding=None when the embed step
        # returns nothing — that must be queryable without loading vectors.
        svc, session = svc_with_session
        session.run.return_value.single.return_value = {"id": "c-1"}
        svc.store_chunk(self._chunk(None))
        assert session.run.call_args.kwargs["has_embedding"] is False


# ---------------------------------------------------------------------------
# Startup backfill
# ---------------------------------------------------------------------------


def _count_result(n):
    result = MagicMock()
    result.single.return_value = {"n": n}
    return result


class TestBackfill:
    def test_backfills_chunks_and_documents_batched(self, svc_with_session):
        svc, session = svc_with_session
        session.run.side_effect = [
            _count_result(120),  # chunks missing has_embedding
            MagicMock(),         # batched chunk update
            _count_result(7),    # completed docs missing entity_count
            MagicMock(),         # batched doc update
        ]

        summary = svc.backfill_degraded_document_signals(include_entity_counts=True)

        assert summary == {"chunks_backfilled": 120, "documents_backfilled": 7}
        queries = [c.args[0] for c in session.run.call_args_list]
        chunk_update = queries[1]
        doc_update = queries[3]
        # Memory safety: both updates must be batched (auto-commit session +
        # CALL {} IN TRANSACTIONS), like the batched deletes.
        assert "IN TRANSACTIONS" in chunk_update
        assert "IN TRANSACTIONS" in doc_update
        # Idempotency: only NULL fields are touched.
        assert "c.has_embedding IS NULL" in chunk_update
        assert "d.entity_count IS NULL" in doc_update
        # Signal shapes.
        assert "c.embedding IS NOT NULL" in chunk_update
        assert "MENTIONS" in doc_update and "count(DISTINCT e)" in doc_update
        assert "d.processing_status = 'completed'" in doc_update

    def test_skips_entity_counts_when_extraction_disabled(self, svc_with_session):
        # With extraction off, 0 entities is normal — entity_count must stay
        # unset so nothing is ever flagged degraded.
        svc, session = svc_with_session
        session.run.side_effect = [_count_result(3), MagicMock()]

        summary = svc.backfill_degraded_document_signals(include_entity_counts=False)

        assert summary == {"chunks_backfilled": 3, "documents_backfilled": 0}
        queries = [c.args[0] for c in session.run.call_args_list]
        assert not any("entity_count" in q for q in queries)

    def test_noop_when_nothing_pending(self, svc_with_session):
        svc, session = svc_with_session
        session.run.side_effect = [_count_result(0), _count_result(0)]

        summary = svc.backfill_degraded_document_signals(include_entity_counts=True)

        assert summary == {"chunks_backfilled": 0, "documents_backfilled": 0}
        # Only the two count queries ran — no batched updates.
        assert session.run.call_count == 2


# ---------------------------------------------------------------------------
# Read queries expose the degraded signals
# ---------------------------------------------------------------------------


class TestReadQueriesExposeSignals:
    def test_get_all_documents_fields(self, svc_with_session):
        svc, session = svc_with_session
        session.run.return_value = [
            {"id": "d-1", "entity_count": 0, "unembedded_chunk_count": 2}
        ]
        docs = svc.get_all_documents()
        cypher = session.run.call_args.args[0]
        # -1 = unknown → never degraded (pre-backfill / extraction disabled)
        assert "coalesce(d.entity_count, -1) as entity_count" in cypher
        # Only has_embedding = false counts; NULL (pre-backfill) must not.
        assert "has_embedding = false" in cypher
        assert "unembedded_chunk_count" in cypher
        # Embedding vectors themselves are never loaded.
        assert "c.embedding" not in cypher and "uc.embedding" not in cypher
        assert docs[0]["entity_count"] == 0
        assert docs[0]["unembedded_chunk_count"] == 2

    def test_get_document_fields(self, svc_with_session):
        svc, session = svc_with_session
        session.run.return_value.single.return_value = {
            "id": "d-1", "entity_count": -1, "unembedded_chunk_count": 0,
        }
        doc = svc.get_document("d-1")
        cypher = session.run.call_args.args[0]
        assert "coalesce(d.entity_count, -1) as entity_count" in cypher
        assert "has_embedding = false" in cypher
        assert doc["entity_count"] == -1

    def test_get_document_fingerprint_fields(self, svc_with_session):
        svc, session = svc_with_session
        session.run.return_value.single.return_value = {
            "file_sha256": "abc", "config_hash": "cfg",
            "processing_status": "completed",
            "entity_count": 0, "unembedded_chunk_count": 1,
        }
        fp = svc.get_document_fingerprint("d-1")
        cypher = session.run.call_args.args[0]
        assert "entity_count" in cypher
        assert "has_embedding = false" in cypher
        assert fp["entity_count"] == 0
        assert fp["unembedded_chunk_count"] == 1


# ---------------------------------------------------------------------------
# Reprocess delta-skip bypass for degraded documents
# ---------------------------------------------------------------------------


def _make_processor(neo4j_mock):
    from app.services.document_processor import DocumentProcessor
    from app.services.graph_extractor import GraphExtractor

    proc = object.__new__(DocumentProcessor)
    proc.settings = get_settings()
    proc.neo4j = neo4j_mock
    proc.graph_extractor = GraphExtractor()
    return proc


class TestDeltaSkipDegradedBypass:
    def _fingerprint(self, proc, file_path, **overrides):
        fp = {
            "file_sha256": proc._file_sha256(file_path),
            "config_hash": proc._reprocess_config_hash(),
            "processing_status": "completed",
            "entity_count": 25,
            "unembedded_chunk_count": 0,
        }
        fp.update(overrides)
        return fp

    async def _run(self, tmp_path, **fp_overrides):
        f = tmp_path / "doc.md"
        f.write_text("hello world")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.settings.enable_reprocess_delta = True
        try:
            neo4j.get_document_fingerprint.return_value = self._fingerprint(
                proc, str(f), **fp_overrides
            )
            neo4j.get_document.return_value = {"chunk_count": 7}
            return await proc._reprocess_delta_skip("doc-1", str(f)), neo4j
        finally:
            proc.settings.enable_reprocess_delta = False

    async def test_healthy_unchanged_doc_is_skipped(self, tmp_path):
        skipped, neo4j = await self._run(tmp_path)
        assert skipped is True

    async def test_zero_entities_bypasses_skip(self, tmp_path):
        skipped, neo4j = await self._run(tmp_path, entity_count=0)
        assert skipped is False
        # The skip path must not have touched the document status.
        neo4j.update_document_status.assert_not_called()

    async def test_unembedded_chunks_bypass_skip(self, tmp_path):
        skipped, neo4j = await self._run(tmp_path, unembedded_chunk_count=3)
        assert skipped is False
        neo4j.update_document_status.assert_not_called()

    async def test_unknown_entity_count_is_not_degraded(self, tmp_path):
        # -1 = unknown (extraction disabled / pre-backfill) → normal skip.
        skipped, _ = await self._run(tmp_path, entity_count=-1)
        assert skipped is True

    async def test_legacy_fingerprint_without_signals_still_skips(self, tmp_path):
        # Fingerprints recorded before this feature carry neither key.
        f = tmp_path / "doc.md"
        f.write_text("hello world")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.settings.enable_reprocess_delta = True
        try:
            neo4j.get_document_fingerprint.return_value = {
                "file_sha256": proc._file_sha256(str(f)),
                "config_hash": proc._reprocess_config_hash(),
                "processing_status": "completed",
            }
            neo4j.get_document.return_value = {"chunk_count": 7}
            assert await proc._reprocess_delta_skip("doc-1", str(f)) is True
        finally:
            proc.settings.enable_reprocess_delta = False
