"""Tests for the anydoc fast-path conversion router.

Covers:
- gate decisions in convert_with_anydoc: master switch, extension
  eligibility, image-rich PDFs (vision on/off), low text-yield PDFs,
  typed-error fallback on scanned PDFs
- output contract parity with the docling worker (markdown/filename/
  images/error) and office-format asset -> images mapping
- router wiring in _convert_document_subprocess: engine="docling" skips
  the fast path; a fast-path hit never touches the docling paths
- _reprocess_config_hash flips when the conversion engine flips
- reprocess_document(engine=...) bypasses the delta-skip;
  _prepare_ingest_resume(force_full=True) refuses chunk reuse

Fixtures are real files built in-test (minimal xref-correct PDFs, a
minimal OOXML docx with one embedded PNG, a PIL image-only "scanned"
PDF) so the real anydoc wheel is exercised, not a mock.
"""

from __future__ import annotations

import base64
import io
import types
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import anydoc_converter
from app.services.anydoc_converter import (
    ANYDOC_EXTENSIONS,
    anydoc_version,
    convert_with_anydoc,
    is_anydoc_available,
)

pytestmark = pytest.mark.skipif(
    not is_anydoc_available(), reason="firecrawl-anydoc not installed"
)


def _settings(**overrides) -> types.SimpleNamespace:
    base = dict(
        enable_anydoc=True,
        anydoc_pdf_min_chars_per_page=200,
        anydoc_pdf_max_images_per_page=0.5,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Fixture builders — smallest real files the real parsers accept
# ---------------------------------------------------------------------------

def build_pdf(texts: list[str]) -> bytes:
    """Minimal text PDF, one page per entry, with a correct xref table."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(texts)))
    objects.append(b"<</Type/Catalog/Pages 2 0 R>>")
    objects.append(f"<</Type/Pages/Kids[{kids}]/Count {len(texts)}>>".encode())
    for i, text in enumerate(texts):
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            f"/Contents {4 + 2 * i} 0 R"
            f"/Resources<</Font<</F1 {3 + 2 * len(texts)} 0 R>>>>>>".encode()
        )
        objects.append(b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream))
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


_DOCX_CT = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
    'package/2006/content-types"><Default Extension="rels" ContentType='
    '"application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_DOCX_RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
    '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
    "schemas.openxmlformats.org/officeDocument/2006/relationships/"
    'officeDocument" Target="word/document.xml"/></Relationships>'
)
_DOCX_DOCRELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
    '.org/package/2006/relationships"><Relationship Id="rId9" Type="http://'
    'schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
    'Target="media/image1.png"/></Relationships>'
)
_DOCX_DOC = (
    '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
    '.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats'
    '.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.'
    'openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http:'
    '//schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://'
    'schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'
    "<w:p><w:r><w:t>Hello from docx with an image.</w:t></w:r></w:p>"
    "<w:p><w:r><w:drawing><wp:inline><wp:extent cx=\"914400\" cy=\"914400\"/>"
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
    'drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" '
    'name="img"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip '
    'r:embed="rId9"/></pic:blipFill><pic:spPr/></pic:pic></a:graphicData>'
    "</a:graphic></wp:inline></w:drawing></w:r></w:p></w:body></w:document>"
)


def build_docx_with_image() -> bytes:
    from PIL import Image

    img_buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(img_buf, "PNG")
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("[Content_Types].xml", _DOCX_CT)
        zf.writestr("_rels/.rels", _DOCX_RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOCX_DOCRELS)
        zf.writestr("word/document.xml", _DOCX_DOC)
        zf.writestr("word/media/image1.png", img_buf.getvalue())
    return z.getvalue()


def build_scanned_pdf() -> bytes:
    """Image-only PDF: pixels, no text layer — anydoc must refuse it."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buf, "PDF")
    return buf.getvalue()


@pytest.fixture
def text_pdf(tmp_path):
    p = tmp_path / "text.pdf"
    p.write_bytes(build_pdf(["Alice works at Acme Rockets Inc. " * 10] * 2))
    return str(p)


# ---------------------------------------------------------------------------
# Gate decisions
# ---------------------------------------------------------------------------

class TestGate:
    def test_disabled_returns_none(self, text_pdf):
        assert convert_with_anydoc(text_pdf, False, _settings(enable_anydoc=False)) is None

    def test_ineligible_extension_returns_none(self, tmp_path):
        p = tmp_path / "page.html"
        p.write_text("<html><body>hi</body></html>")
        assert ".html" not in ANYDOC_EXTENSIONS
        assert convert_with_anydoc(str(p), False, _settings()) is None

    def test_scanned_pdf_falls_back(self, tmp_path):
        p = tmp_path / "scan.pdf"
        p.write_bytes(build_scanned_pdf())
        # Typed UnsupportedError ("OCR is required") -> routing decision, not a raise
        assert convert_with_anydoc(str(p), True, _settings()) is None

    def test_low_text_yield_falls_back(self, tmp_path):
        p = tmp_path / "hybrid.pdf"
        p.write_bytes(build_pdf(["x", "y"]))  # 2 pages, ~4 chars total
        assert convert_with_anydoc(str(p), False, _settings()) is None
        # yield floor disabled -> accepted
        result = convert_with_anydoc(
            str(p), False, _settings(anydoc_pdf_min_chars_per_page=0)
        )
        assert result is not None and "x" in result["markdown"]

    def test_image_rich_pdf_falls_back_only_when_vision_on(self, text_pdf, monkeypatch):
        monkeypatch.setattr(
            anydoc_converter, "_pdf_embedded_image_count", lambda _p: 50
        )
        # 50 images / 2 pages >> 0.5/page ceiling
        assert convert_with_anydoc(text_pdf, True, _settings()) is None
        # without vision, images are irrelevant — fast path stays
        assert convert_with_anydoc(text_pdf, False, _settings()) is not None
        # negative ceiling disables the check even with vision
        assert (
            convert_with_anydoc(
                text_pdf, True, _settings(anydoc_pdf_max_images_per_page=-1)
            )
            is not None
        )


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

class TestConvert:
    def test_text_pdf_contract(self, text_pdf):
        result = convert_with_anydoc(text_pdf, True, _settings())
        assert result is not None
        assert set(result) == {"markdown", "filename", "images", "error"}
        assert "Alice works at Acme Rockets" in result["markdown"]
        assert result["filename"] == "text.pdf"
        assert result["error"] is None
        # PDFs have no document model in anydoc — images are always empty
        assert result["images"] == []

    def test_docx_assets_mapped_when_vision_on(self, tmp_path):
        p = tmp_path / "doc.docx"
        p.write_bytes(build_docx_with_image())
        result = convert_with_anydoc(str(p), True, _settings())
        assert result is not None
        assert "Hello from docx" in result["markdown"]
        assert len(result["images"]) == 1
        img = result["images"][0]
        assert img["image_id"].startswith("anydoc_asset_")
        assert img["page_number"] is None and img["bbox"] is None
        # payload is a decodable PNG
        png = base64.b64decode(img["base64_png"])
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_docx_assets_skipped_when_vision_off(self, tmp_path):
        p = tmp_path / "doc.docx"
        p.write_bytes(build_docx_with_image())
        result = convert_with_anydoc(str(p), False, _settings())
        assert result is not None and result["images"] == []

    def test_version_marker(self):
        assert anydoc_version() not in ("absent", "unknown")


# ---------------------------------------------------------------------------
# Router wiring in _convert_document_subprocess
# ---------------------------------------------------------------------------

class TestRouterWiring:
    @pytest.mark.asyncio
    async def test_fast_path_hit_skips_docling(self, text_pdf, monkeypatch):
        from app.services import document_processor as dp

        sentinel = {"markdown": "MD", "filename": "text.pdf", "images": [], "error": None}
        monkeypatch.setattr(
            anydoc_converter, "convert_with_anydoc", lambda *a: sentinel
        )
        result = await dp._convert_document_subprocess(text_pdf, False)
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_engine_docling_skips_fast_path(self, text_pdf, monkeypatch):
        from app.services import document_processor as dp

        def _must_not_run(*_a):
            raise AssertionError("anydoc fast path ran despite engine='docling'")

        monkeypatch.setattr(anydoc_converter, "convert_with_anydoc", _must_not_run)
        # Patch the docling seam to raise instead of relying on docling being
        # absent from the venv (CI installs the full ML stack, so the real
        # conversion would run and succeed). Reaching the seam proves the
        # fast path was skipped.
        docling = AsyncMock(side_effect=RuntimeError("docling unavailable (test)"))
        monkeypatch.setattr(dp, "_convert_document_docling", docling)
        with pytest.raises(RuntimeError, match="docling"):
            await dp._convert_document_subprocess(text_pdf, False, engine="docling")
        docling.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fast_path_decline_falls_through(self, tmp_path, monkeypatch):
        from app.services import document_processor as dp

        monkeypatch.setattr(
            anydoc_converter, "convert_with_anydoc", lambda *a: None
        )
        docling = AsyncMock(side_effect=RuntimeError("docling unavailable (test)"))
        monkeypatch.setattr(dp, "_convert_document_docling", docling)
        p = tmp_path / "scan.pdf"
        p.write_bytes(build_scanned_pdf())
        with pytest.raises(RuntimeError, match="docling"):
            await dp._convert_document_subprocess(str(p), False)
        docling.assert_awaited_once()


# ---------------------------------------------------------------------------
# Scanned-PDF OCR retry (force_ocr)
# ---------------------------------------------------------------------------

class TestOcrRetry:
    """Vision-enabled instances run docling with do_ocr=False; a scan then
    yields neither markdown nor images (its text lines are layout-classified
    as text regions, not pictures). The router retries once with OCR forced."""

    @staticmethod
    def _empty(filename="scan.pdf"):
        return {"markdown": "", "filename": filename, "images": [], "error": None}

    @staticmethod
    def _wire(monkeypatch, results):
        """Replace _convert_document_docling with a recorder returning the
        canned results in order."""
        from app.services import document_processor as dp

        calls = []

        async def fake_docling(file_path, use_vision, on_progress=None, force_ocr=False):
            calls.append(force_ocr)
            return results[len(calls) - 1]

        monkeypatch.setattr(dp, "_convert_document_docling", fake_docling)
        monkeypatch.setattr(
            anydoc_converter, "convert_with_anydoc", lambda *a: None
        )
        return calls

    @pytest.mark.asyncio
    async def test_empty_pdf_retries_with_force_ocr(self, monkeypatch, tmp_path):
        from app.services import document_processor as dp

        ocr_result = {"markdown": "OCR text", "filename": "scan.pdf", "images": [], "error": None}
        calls = self._wire(monkeypatch, [self._empty(), ocr_result])
        p = tmp_path / "scan.pdf"
        p.write_bytes(b"%PDF-fake")
        result = await dp._convert_document_subprocess(str(p), True)
        assert calls == [False, True]
        assert result["markdown"] == "OCR text"

    @pytest.mark.asyncio
    async def test_no_retry_when_text_or_images_present(self, monkeypatch, tmp_path):
        from app.services import document_processor as dp

        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-fake")
        # text present
        good = {"markdown": "content", "filename": "doc.pdf", "images": [], "error": None}
        calls = self._wire(monkeypatch, [good])
        assert (await dp._convert_document_subprocess(str(p), True)) is good
        assert calls == [False]
        # images present, no text (image-only doc handled by vision) — no retry
        imgs = {"markdown": "", "filename": "doc.pdf", "images": [{"image_id": "x"}], "error": None}
        calls = self._wire(monkeypatch, [imgs])
        assert (await dp._convert_document_subprocess(str(p), True)) is imgs
        assert calls == [False]

    @pytest.mark.asyncio
    async def test_no_retry_without_vision_or_for_non_pdf(self, monkeypatch, tmp_path):
        from app.services import document_processor as dp

        # use_vision=False → OCR already ran; empty means empty
        p = tmp_path / "scan.pdf"
        p.write_bytes(b"%PDF-fake")
        calls = self._wire(monkeypatch, [self._empty()])
        await dp._convert_document_subprocess(str(p), False)
        assert calls == [False]
        # non-PDF → no OCR retry
        h = tmp_path / "page.html"
        h.write_text("<html></html>")
        calls = self._wire(monkeypatch, [self._empty("page.html")])
        await dp._convert_document_subprocess(str(h), True)
        assert calls == [False]


# ---------------------------------------------------------------------------
# Fingerprint / reprocess semantics
# ---------------------------------------------------------------------------

def _bare_processor():
    from app.config import get_settings
    from app.services.document_processor import DocumentProcessor

    proc = object.__new__(DocumentProcessor)
    proc.settings = get_settings()
    proc.graph_extractor = MagicMock(
        extraction_model_name="m1", relationship_model_name="m2"
    )
    return proc


class TestEngineFingerprint:
    def test_config_hash_flips_with_engine(self, monkeypatch):
        proc = _bare_processor()
        h_anydoc = proc._reprocess_config_hash()
        monkeypatch.setattr(proc.settings, "enable_anydoc", False)
        h_docling = proc._reprocess_config_hash()
        assert h_anydoc != h_docling

    @pytest.mark.asyncio
    async def test_forced_engine_bypasses_delta_skip(self, monkeypatch, tmp_path):
        proc = _bare_processor()
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-fake")
        proc.neo4j = MagicMock()
        proc.neo4j.get_document.return_value = {
            "file_path": str(f), "file_type": ".pdf",
        }
        proc.neo4j.update_document_status = MagicMock()
        proc._reprocess_delta_skip = AsyncMock(return_value=True)
        proc._cleanup_before_reprocess = MagicMock()
        proc._start_processing = AsyncMock()

        # Normal reprocess: delta-skip wins, nothing starts
        assert await proc.reprocess_document("d1") is True
        proc._start_processing.assert_not_awaited()

        # Forced engine: delta-skip bypassed, processing starts with engine
        assert await proc.reprocess_document("d1", engine="docling") is True
        proc._start_processing.assert_awaited_once_with(
            "d1", str(f), ".pdf", engine="docling"
        )

    @pytest.mark.asyncio
    async def test_force_full_refuses_chunk_reuse(self, monkeypatch, tmp_path):
        proc = _bare_processor()
        monkeypatch.setattr(proc.settings, "enable_ingest_resume", True)
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-fake")
        file_hash = proc._file_sha256(str(f))
        cfg_hash = proc._reprocess_config_hash()
        proc.neo4j = MagicMock()
        proc.neo4j.get_document_fingerprint.return_value = {
            "file_sha256": file_hash,
            "config_hash": cfg_hash,
            "text_chunk_count": 3,
            "unembedded_chunk_count": 0,
            "processing_status": "completed",
        }
        proc.neo4j.get_text_chunks_for_document.return_value = [
            {"content": "a"}, {"content": "b"}, {"content": "c"},
        ]
        proc.neo4j.delete_document_chunks.return_value = {"chunks_deleted": 3}

        # Matching fingerprint normally reuses chunks...
        out = await proc._prepare_ingest_resume("d1", str(f))
        assert out["reuse_chunks"] is True

        # ...but a forced engine treats them as stale and deletes them
        out = await proc._prepare_ingest_resume("d1", str(f), force_full=True)
        assert out["reuse_chunks"] is False
        proc.neo4j.delete_document_chunks.assert_called_once_with("d1")
