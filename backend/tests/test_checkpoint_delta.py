"""Tests for Phase B checkpointing (enable_phaseb_checkpointing) and the
reprocess delta skip (enable_reprocess_delta)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.models import Relationship
from app.services.graph_extractor import GraphExtractor, PhaseBCheckpointHooks


def _entities(n):
    return [
        {"name": f"Entity {i}", "type": "Concept", "description": ""}
        for i in range(n)
    ]


def _hooks(done_keys=None, candidates_by_key=None):
    done_keys = done_keys or set()
    candidates_by_key = candidates_by_key or {}
    saved = {}
    marked = []

    async def is_done(key):
        return key in done_keys

    async def get_candidates(key):
        return candidates_by_key.get(key)

    async def save_candidates(key, candidates):
        saved[key] = candidates

    async def mark_done(key):
        marked.append(key)

    hooks = PhaseBCheckpointHooks(
        is_batch_done=is_done,
        get_candidates=get_candidates,
        save_candidates=save_candidates,
        mark_batch_done=mark_done,
    )
    return hooks, saved, marked


class TestPhaseBCheckpointing:
    def test_batch_key_deterministic_and_order_insensitive(self):
        a = GraphExtractor.phaseb_batch_key(_entities(5))
        b = GraphExtractor.phaseb_batch_key(list(reversed(_entities(5))))
        assert a == b
        assert a != GraphExtractor.phaseb_batch_key(_entities(6))

    async def test_completed_batch_skipped_and_candidates_reused(self, monkeypatch):
        extractor = GraphExtractor()
        ents = _entities(6)
        key = GraphExtractor.phaseb_batch_key(ents)
        stored_candidates = [("Entity 0", "Entity 1")]
        hooks, saved, marked = _hooks(
            candidates_by_key={key: stored_candidates}
        )

        scan_calls = []

        async def _scan(*a, **k):
            scan_calls.append(1)
            return [("Entity 2", "Entity 3")]

        analyze_calls = []

        async def _analyze(batch, ctx, existing, max_out, candidate_pairs=None):
            analyze_calls.append(candidate_pairs)
            return [Relationship(
                source="Entity 0", target="Entity 1",
                relationship_type="RELATED_TO", description="", weight=5.0,
            )]

        monkeypatch.setattr(extractor, "scan_candidate_pairs_async", _scan)
        monkeypatch.setattr(extractor, "analyze_relationships_async", _analyze)

        rels = await extractor.analyze_relationships_batched_async(
            ents, checkpoint=hooks
        )

        # Phase 1 skipped (candidates came from the checkpoint), Phase 2 ran
        assert scan_calls == []
        assert analyze_calls == [stored_candidates]
        assert len(rels) == 1
        assert marked == [key]  # marked done after the (no-op) store callback

    async def test_done_batch_fully_skipped(self, monkeypatch):
        extractor = GraphExtractor()
        ents = _entities(4)
        key = GraphExtractor.phaseb_batch_key(ents)
        hooks, saved, marked = _hooks(done_keys={key})

        async def _boom(*a, **k):
            raise AssertionError("must not run for a completed batch")

        monkeypatch.setattr(extractor, "scan_candidate_pairs_async", _boom)
        monkeypatch.setattr(extractor, "analyze_relationships_async", _boom)

        rels = await extractor.analyze_relationships_batched_async(
            ents, checkpoint=hooks
        )
        assert rels == []

    async def test_fresh_batch_saves_candidates(self, monkeypatch):
        extractor = GraphExtractor()
        ents = _entities(4)
        key = GraphExtractor.phaseb_batch_key(ents)
        hooks, saved, marked = _hooks()

        async def _scan(*a, **k):
            return [("Entity 0", "Entity 1")]

        async def _analyze(batch, ctx, existing, max_out, candidate_pairs=None):
            return []

        monkeypatch.setattr(extractor, "scan_candidate_pairs_async", _scan)
        monkeypatch.setattr(extractor, "analyze_relationships_async", _analyze)

        await extractor.analyze_relationships_batched_async(
            ents, checkpoint=hooks
        )
        assert saved == {key: [("Entity 0", "Entity 1")]}
        assert marked == [key]

    async def test_no_checkpoint_unchanged_behavior(self, monkeypatch):
        extractor = GraphExtractor()
        ents = _entities(4)

        async def _scan(*a, **k):
            return [("Entity 0", "Entity 1")]

        async def _analyze(batch, ctx, existing, max_out, candidate_pairs=None):
            return []

        monkeypatch.setattr(extractor, "scan_candidate_pairs_async", _scan)
        monkeypatch.setattr(extractor, "analyze_relationships_async", _analyze)
        rels = await extractor.analyze_relationships_batched_async(ents)
        assert rels == []


# ---------------------------------------------------------------------------
# Reprocess delta
# ---------------------------------------------------------------------------

def _make_processor(neo4j_mock):
    from app.services.document_processor import DocumentProcessor
    from app.services.graph_extractor import GraphExtractor

    proc = object.__new__(DocumentProcessor)
    proc.settings = get_settings()
    proc.neo4j = neo4j_mock
    proc.graph_extractor = GraphExtractor()
    return proc


class TestReprocessDelta:
    async def test_skip_when_file_and_config_unchanged(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hello world")

        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.settings.enable_reprocess_delta = True
        try:
            file_hash = proc._file_sha256(str(f))
            neo4j.get_document_fingerprint.return_value = {
                "file_sha256": file_hash,
                "config_hash": proc._reprocess_config_hash(),
                "processing_status": "completed",
            }
            neo4j.get_document.return_value = {"chunk_count": 7}

            skipped = await proc._reprocess_delta_skip("doc-1", str(f))
            assert skipped is True
            # status refreshed to COMPLETED with the skip message
            args = neo4j.update_document_status.call_args.args
            assert args[0] == "doc-1"
            assert "unchanged" in args[4].lower()
        finally:
            proc.settings.enable_reprocess_delta = False

    async def test_no_skip_when_file_changed(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hello world v2")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.settings.enable_reprocess_delta = True
        try:
            neo4j.get_document_fingerprint.return_value = {
                "file_sha256": "old-hash",
                "config_hash": proc._reprocess_config_hash(),
                "processing_status": "completed",
            }
            assert await proc._reprocess_delta_skip("doc-1", str(f)) is False
        finally:
            proc.settings.enable_reprocess_delta = False

    async def test_no_skip_when_config_changed(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hello world")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.settings.enable_reprocess_delta = True
        try:
            neo4j.get_document_fingerprint.return_value = {
                "file_sha256": proc._file_sha256(str(f)),
                "config_hash": "different-config",
                "processing_status": "completed",
            }
            assert await proc._reprocess_delta_skip("doc-1", str(f)) is False
        finally:
            proc.settings.enable_reprocess_delta = False

    async def test_no_skip_when_flag_off(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hello")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        assert await proc._reprocess_delta_skip("doc-1", str(f)) is False
        neo4j.get_document_fingerprint.assert_not_called()

    async def test_no_skip_without_fingerprint_or_incomplete(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hello")
        neo4j = MagicMock()
        proc = _make_processor(neo4j)
        proc.settings.enable_reprocess_delta = True
        try:
            neo4j.get_document_fingerprint.return_value = None
            assert await proc._reprocess_delta_skip("doc-1", str(f)) is False
            neo4j.get_document_fingerprint.return_value = {
                "file_sha256": proc._file_sha256(str(f)),
                "config_hash": proc._reprocess_config_hash(),
                "processing_status": "failed",
            }
            assert await proc._reprocess_delta_skip("doc-1", str(f)) is False
        finally:
            proc.settings.enable_reprocess_delta = False
