"""Startup resume of image analysis killed by a restart.

Image analysis runs as in-process fire-and-forget futures after a document's
text pipeline completes. A restart kills the futures while the document stays
'completed' with image_progress_current < total — the startup orphan-reset
(transient statuses only) never sees these. The resume path re-extracts
images via Docling re-conversion and analyzes only the ones whose chunk
({doc_id}_image_{idx}) isn't stored yet, so paid LLM work is never redone.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.document_processor import DocumentProcessor
from app.services.neo4j_service import Neo4jService


# ---------------------------------------------------------------------------
# Neo4jService.get_existing_image_chunk_indices — id-suffix parsing
# ---------------------------------------------------------------------------

def _fake_driver_returning(rows):
    session = MagicMock()
    session.run.return_value = [{"id": r} for r in rows]
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    # dict-style record access
    session.run.return_value = [MagicMock(__getitem__=lambda self, k, r=r: r) for r in rows]
    driver = MagicMock()
    driver.session.return_value = session
    return driver


def test_existing_image_chunk_indices_parsed_from_ids():
    svc = object.__new__(Neo4jService)
    svc._driver = _fake_driver_returning([
        "doc1_image_0",
        "doc1_image_7",
        "doc1_image_12",
        "doc1_image_extra",  # non-numeric suffix ignored
    ])
    assert svc.get_existing_image_chunk_indices("doc1") == {0, 7, 12}


def test_existing_image_chunk_indices_empty():
    svc = object.__new__(Neo4jService)
    svc._driver = _fake_driver_returning([])
    assert svc.get_existing_image_chunk_indices("doc1") == set()


# ---------------------------------------------------------------------------
# DocumentProcessor.resume_image_analysis
# ---------------------------------------------------------------------------

def _bare_processor(neo4j=None, vision_available=True):
    """DocumentProcessor without running __init__ (no Haystack/OpenAI)."""
    p = object.__new__(DocumentProcessor)
    p.neo4j = neo4j or MagicMock()
    p.vision_analyzer = MagicMock()
    p.vision_analyzer.is_vision_model_available = vision_available
    return p


def _stuck_doc(tmp_path=None, suffix=".pdf", current=2, total=10):
    doc = {
        "id": "doc1",
        "filename": f"stuck{suffix}",
        "file_path": "",
        "image_progress_current": current,
        "image_progress_total": total,
    }
    if tmp_path is not None:
        f = tmp_path / f"doc1{suffix}"
        f.write_bytes(b"fake")
        doc["file_path"] = str(f)
    return doc


def test_resume_missing_file_reconciles_counters():
    p = _bare_processor()
    doc = _stuck_doc(tmp_path=None)  # file_path empty
    result = asyncio.run(p.resume_image_analysis(doc))
    assert result is False
    # Counters are closed (current == total) so the doc stops reading as in-flight
    args = p.neo4j.update_image_progress.call_args[0]
    assert args[0] == "doc1"
    assert args[1] == args[2] == 10
    assert "unavailable" in args[3]


def test_resume_without_vision_model_reconciles(tmp_path):
    p = _bare_processor(vision_available=False)
    doc = _stuck_doc(tmp_path)
    result = asyncio.run(p.resume_image_analysis(doc))
    assert result is False
    args = p.neo4j.update_image_progress.call_args[0]
    assert args[1] == args[2] == 10


def test_resume_skips_already_stored_images(tmp_path, monkeypatch):
    p = _bare_processor()
    p.neo4j.get_existing_image_chunk_indices.return_value = {0, 2}
    images = [{"base64_png": "x"} for _ in range(3)]
    monkeypatch.setattr(
        "app.services.document_processor._convert_document_subprocess",
        AsyncMock(return_value={"images": images, "markdown": "m"}),
    )
    analyze = AsyncMock()
    p._analyze_images_background_from_serialized = analyze

    doc = _stuck_doc(tmp_path, current=2, total=3)
    result = asyncio.run(p.resume_image_analysis(doc))

    assert result is True
    analyze.assert_awaited_once()
    call = analyze.await_args
    assert call.args[0] == "doc1"
    assert call.args[1] == images
    assert call.kwargs["skip_indices"] == {0, 2}


def test_resume_survives_image_count_mismatch(tmp_path, monkeypatch):
    """Re-conversion (e.g. new Docling version) may find a different image
    count than the frozen progress claims — resume runs against the fresh set."""
    p = _bare_processor()
    p.neo4j.get_existing_image_chunk_indices.return_value = set()
    monkeypatch.setattr(
        "app.services.document_processor._convert_document_subprocess",
        AsyncMock(return_value={"images": [{"base64_png": "x"}] * 5, "markdown": "m"}),
    )
    p._analyze_images_background_from_serialized = AsyncMock()

    doc = _stuck_doc(tmp_path, current=2, total=10)  # claims 10, finds 5
    assert asyncio.run(p.resume_image_analysis(doc)) is True


def test_resume_no_images_found_reconciles(tmp_path, monkeypatch):
    p = _bare_processor()
    monkeypatch.setattr(
        "app.services.document_processor._convert_document_subprocess",
        AsyncMock(return_value={"images": [], "markdown": "m"}),
    )
    doc = _stuck_doc(tmp_path)
    result = asyncio.run(p.resume_image_analysis(doc))
    assert result is False
    args = p.neo4j.update_image_progress.call_args[0]
    assert args[1] == args[2] == 10


def test_resume_conversion_error_propagates(tmp_path, monkeypatch):
    """Conversion failure must NOT close the counters — the document keeps
    its stuck progress and is retried on the next startup."""
    p = _bare_processor()
    monkeypatch.setattr(
        "app.services.document_processor._convert_document_subprocess",
        AsyncMock(side_effect=RuntimeError("docling died")),
    )
    doc = _stuck_doc(tmp_path)
    with pytest.raises(RuntimeError):
        asyncio.run(p.resume_image_analysis(doc))
    # Only the "Re-extracting images..." progress write happened — counters
    # were never force-closed.
    for call in p.neo4j.update_image_progress.call_args_list:
        assert call.args[1] != call.args[2]


# ---------------------------------------------------------------------------
# Startup wiring: the scan runs, nothing spawns when the graph is clean
# ---------------------------------------------------------------------------

def test_startup_scans_for_incomplete_image_analysis(client, mock_neo4j):
    # client fixture booted the app lifespan with the mocked service
    assert mock_neo4j.get_documents_with_incomplete_image_analysis.called
