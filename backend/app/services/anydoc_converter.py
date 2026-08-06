"""In-process anydoc conversion with a routing gate.

anydoc (PyPI: firecrawl-anydoc) is a pure-Rust, no-ML document converter:
text-based PDFs and office formats convert in milliseconds at ~41 MB peak
RSS, versus docling's per-page layout-model inference (~1 s/page CPU, the
600 s book-timeout class of failures). It is NOT a docling replacement —
it has no OCR and no layout model, and PDFs convert straight to Markdown
with no document model, so PDF images are invisible to it. Measured
head-to-head + trade-offs: 2026-08-06 evaluation (memory:
anydoc-vs-docling-eval).

Routing contract: `convert_with_anydoc()` returns the same dict as the
docling worker ({markdown, filename, images, error}) when anydoc is the
right engine for the file, or None when the caller should fall through to
the existing docling paths (helper service / local subprocess). It never
raises — any anydoc failure (typed or not) is a routing decision, not an
ingestion failure, because the docling fallback is always safe.

Gate rules (env-tunable, see config.py):
- extension must be in ANYDOC_EXTENSIONS (office/epub/pdf; html, images,
  audio, latex, xml keep their existing paths);
- scanned/encrypted/malformed inputs raise typed anydoc errors -> fallback;
- PDFs with a vestigial text layer (hybrid scans) are caught by a
  chars-per-page yield check AFTER conversion -> fallback;
- when vision analysis is on, image-rich PDFs (embedded XObjects per page
  above threshold, counted via pypdf in ~ms) go to docling so figures keep
  flowing to the vision pipeline — anydoc would silently drop them.

Office formats (docx/pptx/... and epub) DO expose embedded images via
anydoc's document model; they are mapped to the docling images contract
(base64_png; page_number/bbox are None — office formats have no page
geometry) so vision analysis keeps working on that path.
"""

import base64
import importlib.util
import io
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Formats routed to anydoc. Subset of DocumentProcessor.DOCLING_EXTENSIONS —
# everything else (html, images, audio, tex, xml, ...) keeps its current path.
ANYDOC_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".epub",
}

# Office-model formats where anydoc's to_document() carries embedded assets.
# PDFs are absent by design: anydoc PDFs convert straight to Markdown and
# have no document-model form (its docstring is explicit about this).
_ASSET_EXTENSIONS = ANYDOC_EXTENSIONS - {".pdf"}

_anydoc_available: Optional[bool] = None


def is_anydoc_available() -> bool:
    """Cached import probe — the wheel is in requirements-base, but stay
    graceful on images built before it was added."""
    global _anydoc_available
    if _anydoc_available is None:
        _anydoc_available = importlib.util.find_spec("anydoc") is not None
        if not _anydoc_available:
            logger.info("anydoc not installed — all conversions use docling")
    return _anydoc_available


def anydoc_version() -> str:
    """Version marker for _reprocess_config_hash (engine identity)."""
    if not is_anydoc_available():
        return "absent"
    try:
        from importlib.metadata import version

        return version("firecrawl-anydoc")
    except Exception:  # noqa: BLE001 — hash marker only, never fail on it
        return "unknown"


def _pdf_page_count(file_path: str) -> Optional[int]:
    try:
        from pypdf import PdfReader

        with open(file_path, "rb") as f:
            return len(PdfReader(f).pages)
    except Exception as exc:  # noqa: BLE001 — gate check, docling can retry
        logger.warning(f"anydoc gate: could not read PDF page count: {exc}")
        return None


def _pdf_embedded_image_count(file_path: str) -> Optional[int]:
    """Raw embedded-image XObject count via pypdf (~ms, no ML). Over-counts
    relative to semantic figures (one figure is often many XObjects) and
    misses pure-vector art — good enough as a routing signal, NOT as a
    figure count."""
    try:
        from pypdf import PdfReader

        with open(file_path, "rb") as f:
            return sum(len(page.images) for page in PdfReader(f).pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"anydoc gate: could not count PDF images: {exc}")
        return None


def _assets_to_images(document) -> list:
    """Map anydoc document-model image assets to the docling worker's images
    contract. Non-image assets and undecodable payloads are skipped — image
    analysis is best-effort, exactly like docling's _extract_images."""
    from PIL import Image

    images = []
    for asset in getattr(document, "assets", None) or []:
        media_type = (getattr(asset, "media_type", "") or "").lower()
        if not media_type.startswith("image/"):
            continue
        try:
            data = bytes(asset.data)
            pil_image = Image.open(io.BytesIO(data))
            if pil_image.mode in ("RGBA", "LA", "P"):
                pil_image = pil_image.convert("RGB")
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            images.append({
                "image_id": f"anydoc_asset_{asset.id}",
                # Office formats have no page geometry; the contract already
                # allows None for both (docling emits None when prov is absent).
                "page_number": None,
                "bbox": None,
                "caption": None,
                "existing_description": None,
                "base64_png": base64.b64encode(buf.getvalue()).decode(),
            })
        except Exception as exc:  # noqa: BLE001 — skip asset, keep the doc
            logger.warning(
                f"anydoc: skipping asset {getattr(asset, 'id', '?')}: {exc}"
            )
    return images


def convert_with_anydoc(file_path: str, use_vision: bool, settings) -> Optional[dict]:
    """Convert via anydoc when it is the right engine; None => use docling.

    Synchronous and CPU-bound (Rust) — call via asyncio.to_thread. Returns
    the docling worker contract: {markdown, filename, images, error(None)}.
    """
    if not getattr(settings, "enable_anydoc", True) or not is_anydoc_available():
        return None

    path = Path(file_path)
    ext = path.suffix.lower()
    if ext not in ANYDOC_EXTENSIONS:
        return None

    import anydoc

    page_count = None
    if ext == ".pdf":
        page_count = _pdf_page_count(file_path)
        # Image-rich PDFs keep the docling path so figures reach the vision
        # pipeline (anydoc returns no images for PDFs, ever). Only relevant
        # when a vision model would actually analyze them.
        if use_vision:
            max_per_page = getattr(settings, "anydoc_pdf_max_images_per_page", 0.5)
            image_count = _pdf_embedded_image_count(file_path)
            if (
                image_count is not None
                and page_count
                and max_per_page >= 0
                and image_count / page_count > max_per_page
            ):
                logger.info(
                    f"anydoc gate: {path.name} is image-rich "
                    f"({image_count} embedded images / {page_count} pages) — using docling"
                )
                return None

    try:
        markdown = anydoc.to_markdown(file_path)
    except anydoc.ConvertError as exc:
        # Typed refusals: scanned (UnsupportedError: "OCR is required"),
        # EncryptedError, MalformedError, ResourceLimitError, ... — all
        # subclass ConvertError. The fallback IS the handler.
        logger.info(f"anydoc declined {path.name} ({exc}) — falling back to docling")
        return None
    except Exception as exc:  # noqa: BLE001 — never fail ingestion from the fast path
        logger.warning(
            f"anydoc unexpected error on {path.name} ({exc}) — falling back to docling"
        )
        return None

    if not markdown or not markdown.strip():
        logger.info(f"anydoc produced no text for {path.name} — falling back to docling")
        return None

    # Hybrid scans: a vestigial text layer converts "successfully" with a
    # few chars/page. Below the yield floor, docling's OCR path does better.
    if ext == ".pdf" and page_count:
        min_chars = getattr(settings, "anydoc_pdf_min_chars_per_page", 200)
        if min_chars > 0 and len(markdown) / page_count < min_chars:
            logger.info(
                f"anydoc gate: {path.name} text yield too low "
                f"({len(markdown)} chars / {page_count} pages) — using docling"
            )
            return None

    images = []
    if use_vision and ext in _ASSET_EXTENSIONS:
        try:
            fmt = anydoc.format_from_path(file_path)
            document = anydoc.to_document(path.read_bytes(), fmt)
            images = _assets_to_images(document)
        except Exception as exc:  # noqa: BLE001 — markdown is already good
            logger.warning(f"anydoc asset extraction failed for {path.name}: {exc}")

    logger.info(
        f"anydoc converted {path.name}: {len(markdown):,} chars, {len(images)} images"
    )
    return {
        "markdown": markdown,
        "filename": path.name,
        "images": images,
        "error": None,
    }
