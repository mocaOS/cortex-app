"""Tests for the no-content terminal state (empty / password-protected files).

A file that yields nothing to ingest is *not* a pipeline failure: a zero-byte
file, a PDF with an empty page tree, and a locked PDF produce the identical
result on every retry, so parking them in `failed` leaves an archive import
with permanently-red documents no reprocess can clear. They land in COMPLETED
carrying a `content_status` flag instead. Covered here:

- `_probe_no_content` classifies empty / zero-page / encrypted sources, and
  stays silent (→ normal conversion path, real failures still fail) on
  anything it cannot positively identify
- `_process_document` turns `NoExtractableContent` into COMPLETED + flag,
  never FAILED, and clears a stale flag at the start of every run
- `set_document_content_status` writes and clears the pair of properties
- `get_all_documents` / `get_document` expose them to the API
"""

from __future__ import annotations

import types
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.models import ProcessingStatus
from app.services.document_processor import (
    DocumentProcessor,
    NoExtractableContent,
    _probe_no_content,
)
from app.services.neo4j_service import Neo4jService


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def _write_pdf(path, *, pages: int = 1, user_password=None, owner_password=None):
    """Write a real PDF so the probe exercises pypdf, not a stub."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if user_password is not None or owner_password is not None:
        writer.encrypt(
            user_password=user_password or "",
            owner_password=owner_password,
        )
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


# ---------------------------------------------------------------------------
# _probe_no_content: positive identifications
# ---------------------------------------------------------------------------


class TestProbePositive:
    def test_zero_byte_file_of_any_type(self, tmp_path):
        empty = tmp_path / "notes.md"
        empty.write_bytes(b"")
        result = _probe_no_content(str(empty), ".md")
        assert isinstance(result, NoExtractableContent)
        assert result.reason == "empty"
        assert "0 bytes" in result.note

    def test_pdf_with_no_pages(self, tmp_path):
        path = _write_pdf(tmp_path / "zero.pdf", pages=0)
        result = _probe_no_content(path, ".pdf")
        assert isinstance(result, NoExtractableContent)
        assert result.reason == "empty"
        assert "no pages" in result.note

    def test_password_protected_pdf(self, tmp_path):
        # A real scan can hide behind this — 19 pages of images the pipeline
        # can never reach — so the note has to explain, not just say "empty".
        path = _write_pdf(tmp_path / "locked.pdf", pages=3, user_password="s3cret")
        result = _probe_no_content(path, ".pdf")
        assert isinstance(result, NoExtractableContent)
        assert result.reason == "encrypted"
        assert "password-protected" in result.note


# ---------------------------------------------------------------------------
# _probe_no_content: stays out of the way when it cannot be sure
# ---------------------------------------------------------------------------


class TestProbeConservative:
    def test_normal_pdf_passes_through(self, tmp_path):
        path = _write_pdf(tmp_path / "ok.pdf", pages=2)
        assert _probe_no_content(path, ".pdf") is None

    def test_owner_password_only_pdf_passes_through(self, tmp_path):
        # Empty user password: pypdf (and docling) open it fine, so this must
        # NOT be classified as unreachable content.
        path = _write_pdf(
            tmp_path / "owner.pdf", pages=1, user_password="", owner_password="own"
        )
        assert _probe_no_content(path, ".pdf") is None

    def test_non_empty_non_pdf_is_never_probed(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("# hello")
        assert _probe_no_content(str(path), ".md") is None

    def test_malformed_pdf_is_inconclusive(self, tmp_path):
        # Corrupt is not the same as empty: leave it to the converter so a
        # genuine conversion failure is still reported as a failure.
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.7\nnot really a pdf at all")
        assert _probe_no_content(str(path), ".pdf") is None

    def test_missing_file_is_inconclusive(self, tmp_path):
        assert _probe_no_content(str(tmp_path / "gone.pdf"), ".pdf") is None


# ---------------------------------------------------------------------------
# _process_document: terminal state is COMPLETED + flag, never FAILED
# ---------------------------------------------------------------------------


def _bare_processor(neo4j):
    proc = object.__new__(DocumentProcessor)
    proc.neo4j = neo4j
    proc.settings = get_settings()

    async def _no_resume(self, doc_id, file_path, force_full=False):
        return {"reuse_chunks": False}

    proc._prepare_ingest_resume = types.MethodType(_no_resume, proc)
    return proc


@pytest.mark.asyncio
class TestProcessDocumentNoContent:
    async def test_empty_pdf_completes_with_flag(self, tmp_path):
        neo4j = MagicMock()
        proc = _bare_processor(neo4j)
        path = _write_pdf(tmp_path / "zero.pdf", pages=0)

        await proc._process_document("doc-1", path, ".pdf")

        statuses = [
            call.args[1] for call in neo4j.update_document_status.call_args_list
        ]
        assert ProcessingStatus.FAILED not in statuses
        assert statuses[-1] == ProcessingStatus.COMPLETED
        final = neo4j.update_document_status.call_args
        assert final.kwargs["chunk_count"] == 0
        # entity_count stays unset — 0 would read as "extraction found nothing"
        # (the degraded signal) on a document that simply has no text.
        assert "entity_count" not in final.kwargs

        flag = neo4j.set_document_content_status.call_args_list[-1]
        assert flag.args[0] == "doc-1"
        assert flag.args[1] == "empty"

    async def test_locked_pdf_reports_encrypted(self, tmp_path):
        neo4j = MagicMock()
        proc = _bare_processor(neo4j)
        path = _write_pdf(tmp_path / "locked.pdf", pages=2, user_password="pw")

        await proc._process_document("doc-2", path, ".pdf")

        assert neo4j.set_document_content_status.call_args_list[-1].args[1] == "encrypted"
        assert (
            neo4j.update_document_status.call_args.args[1] == ProcessingStatus.COMPLETED
        )

    async def test_whitespace_only_text_file_completes_with_flag(self, tmp_path):
        # Non-zero bytes, so the probe passes it through — the raw-text branch
        # is what classifies it.
        neo4j = MagicMock()
        proc = _bare_processor(neo4j)
        path = tmp_path / "blank.md"
        path.write_text("   \n\n\t")

        await proc._process_document("doc-4", str(path), ".md")

        assert (
            neo4j.update_document_status.call_args.args[1] == ProcessingStatus.COMPLETED
        )
        assert neo4j.set_document_content_status.call_args_list[-1].args[1] == "empty"

    async def test_unsupported_extension_still_fails(self, tmp_path):
        # A zero-byte .exe is not an empty *document* — it is not a document,
        # and "unsupported file type" is the honest answer.
        neo4j = MagicMock()
        proc = _bare_processor(neo4j)
        path = tmp_path / "thing.exe"
        path.write_bytes(b"")

        await proc._process_document("doc-5", str(path), ".exe")

        assert neo4j.update_document_status.call_args.args[1] == ProcessingStatus.FAILED
        # Only the start-of-run clear ran; nothing was flagged.
        assert neo4j.set_document_content_status.call_args_list[-1].args[1] is None

    async def test_run_clears_a_stale_flag_first(self, tmp_path):
        # A document re-uploaded as a decrypted copy must not keep its badge.
        neo4j = MagicMock()
        proc = _bare_processor(neo4j)
        path = _write_pdf(tmp_path / "zero.pdf", pages=0)

        await proc._process_document("doc-3", path, ".pdf")

        first = neo4j.set_document_content_status.call_args_list[0]
        assert first.args[1] is None


# ---------------------------------------------------------------------------
# Neo4j plumbing
# ---------------------------------------------------------------------------


class TestContentStatusPersistence:
    def test_sets_status_and_note(self, svc_with_session):
        svc, session = svc_with_session
        svc.set_document_content_status("doc-1", "encrypted", "PDF is locked")
        cypher = session.run.call_args.args[0]
        kwargs = session.run.call_args.kwargs
        assert "d.content_status = $status" in cypher
        assert "d.content_note = $note" in cypher
        assert kwargs["status"] == "encrypted"
        assert kwargs["note"] == "PDF is locked"

    def test_clears_with_none(self, svc_with_session):
        svc, session = svc_with_session
        svc.set_document_content_status("doc-1", None, "")
        kwargs = session.run.call_args.kwargs
        assert kwargs["status"] is None
        assert kwargs["note"] == ""

    def test_empty_string_status_clears_rather_than_flags(self, svc_with_session):
        svc, session = svc_with_session
        svc.set_document_content_status("doc-1", "", "")
        assert session.run.call_args.kwargs["status"] is None


class TestDocumentReadsExposeContentStatus:
    @pytest.mark.parametrize("method", ["get_all_documents", "get_document"])
    def test_query_returns_content_fields(self, svc_with_session, method):
        svc, session = svc_with_session
        session.run.return_value = MagicMock(single=lambda: None, __iter__=lambda s: iter([]))
        getattr(svc, method)("doc-1") if method == "get_document" else getattr(svc, method)()
        cypher = session.run.call_args.args[0]
        # Not coalesced to a string: absent means "has content", and the UI
        # branches on the flag being set at all.
        assert "d.content_status as content_status" in cypher
        assert "coalesce(d.content_note, '') as content_note" in cypher
