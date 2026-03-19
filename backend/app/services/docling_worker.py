"""Standalone Docling conversion worker.

Runs document conversion in a separate process so that CPU-bound ML
inference (layout detection, OCR, table structure) does NOT hold the
GIL in the main FastAPI process, keeping the event loop responsive.

Memory optimizations for large documents:
- Chunked processing: PDFs with many pages are processed in smaller
  page ranges to avoid OOM (exit code -9 from SIGKILL).
- Backend unload: Releases internal caches after each conversion.
- max_num_pages / max_file_size: Hard limits passed to Docling.
- PyPdfium fallback: For very large files, uses memory-efficient backend.

Protocol (stdin → stdout):
    Input  – JSON line: {"file_path": "...", "use_vision": true/false}
    Output – JSON line: {"markdown": "...", "images": [...], "error": null}
             images is a list of {image_id, page_number, bbox, caption,
             existing_description, base64_png}
"""

import base64
import io
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("docling_worker")

# Memory limits for large document processing (env overridable)
PAGE_CHUNK_SIZE = int(os.environ.get("DOCLING_PAGE_CHUNK_SIZE", "50"))
MAX_PAGES_PER_CHUNK = int(os.environ.get("DOCLING_MAX_PAGES_PER_CHUNK", "50"))
MAX_FILE_SIZE_BYTES = int(os.environ.get("DOCLING_MAX_FILE_SIZE_BYTES", "0"))  # 0 = use default
USE_PYPDFIUM_FOR_LARGE_MB = float(os.environ.get("DOCLING_USE_PYPDFIUM_FOR_LARGE_MB", "0"))  # 0 = disabled


def _get_pdf_page_count(file_path: str):  # -> Optional[int]
    """Get PDF page count using pypdf. Returns None if not a PDF or on error."""
    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        return None
    try:
        from pypdf import PdfReader

        with open(path, "rb") as f:
            reader = PdfReader(f)
            return len(reader.pages)
    except Exception as exc:
        logger.warning(f"Could not get PDF page count: {exc}")
        return None


def _build_converter(use_vision: bool, use_pypdfium: bool = False):
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        EasyOcrOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        InputFormat,
        PdfFormatOption,
    )

    opts = PdfPipelineOptions()
    opts.do_ocr = not use_vision
    opts.do_table_structure = True
    opts.do_picture_description = not use_vision
    opts.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
        mode=TableFormerMode.ACCURATE,
    )

    if not use_vision:
        opts.ocr_options = EasyOcrOptions(
            lang=["en", "de"],
            use_gpu=True,
            confidence_threshold=0.2,
        )

    opts.accelerator_options = AcceleratorOptions(
        num_threads=4,
        device=AcceleratorDevice.AUTO,
    )
    opts.generate_page_images = True
    opts.images_scale = 2.0

    format_opts = {InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)}

    if use_pypdfium:
        try:
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

            format_opts[InputFormat.PDF] = PdfFormatOption(
                pipeline_options=opts,
                backend=PyPdfiumDocumentBackend,
            )
            logger.info("Using PyPdfium backend for memory efficiency")
        except ImportError:
            logger.warning("PyPdfium backend not available, using default")
            format_opts[InputFormat.PDF] = PdfFormatOption(pipeline_options=opts)
    else:
        format_opts[InputFormat.PDF] = PdfFormatOption(pipeline_options=opts)

    return DocumentConverter(format_options=format_opts)


def _extract_images(docling_doc):
    """Extract images from a DoclingDocument and return serialisable dicts.

    Mirrors the logic in VisionAnalyzer.extract_images_from_document but
    serialises PIL images to base64 so they can cross process boundaries.
    """
    from docling_core.types.doc import PictureItem
    from PIL import Image

    images = []
    if docling_doc is None:
        return images

    for item, _level in docling_doc.iterate_items():
        if not isinstance(item, PictureItem):
            continue
        try:
            pil_image = item.get_image(docling_doc)

            if pil_image is None and item.image and item.image.uri:
                uri_str = str(item.image.uri)
                if uri_str.startswith("data:image"):
                    b64_data = uri_str.split(",", 1)[1]
                    pil_image = Image.open(io.BytesIO(base64.b64decode(b64_data)))
                elif uri_str.startswith(("file://", "/")):
                    fp = uri_str.replace("file://", "")
                    if Path(fp).exists():
                        pil_image = Image.open(fp)

            if pil_image is None:
                continue

            if pil_image.mode in ("RGBA", "LA", "P"):
                pil_image = pil_image.convert("RGB")
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()

            page_no = None
            bbox_dict = None
            if item.prov:
                page_no = item.prov[0].page_no
                if item.prov[0].bbox:
                    bb = item.prov[0].bbox
                    bbox_dict = {"l": bb.l, "t": bb.t, "r": bb.r, "b": bb.b}

            caption = None
            try:
                caption = item.caption_text(doc=docling_doc)
            except Exception:
                pass

            existing_description = None
            if item.meta and getattr(item.meta, "description", None):
                existing_description = item.meta.description.text

            image_id = item.self_ref.replace("/", "_").replace("#", "_")

            images.append({
                "image_id": image_id,
                "page_number": page_no,
                "bbox": bbox_dict,
                "caption": caption,
                "existing_description": existing_description,
                "base64_png": b64,
            })
        except Exception as exc:
            logger.warning(f"Skipping image {getattr(item, 'self_ref', '?')}: {exc}")
    return images


def _convert_chunk(
    converter,
    file_path: str,
    use_vision: bool,
    page_start: int,
    page_end: int,
    max_file_size: int,
) -> tuple[str, list]:
    """Convert a page range. Returns (markdown, images).

    Docling uses 1-based page indices. page_range=(start, end) is inclusive.
    We receive 0-based start/end, so convert: (page_start+1, page_end+1).
    """
    page_range = (page_start + 1, page_end + 1)
    max_pages = page_end - page_start + 1

    convert_kwargs = {
        "max_num_pages": max_pages,
        "page_range": page_range,
    }
    if max_file_size > 0:
        convert_kwargs["max_file_size"] = max_file_size

    result = converter.convert(file_path, **convert_kwargs)

    dl_doc = result.document
    md_text = dl_doc.export_to_markdown()
    images = []
    if use_vision:
        try:
            images = _extract_images(dl_doc)
        except Exception as exc:
            logger.error(f"Image extraction failed (markdown still returned): {exc}")

    # Release backend memory (Docling internal caches)
    try:
        if hasattr(result, "input") and result.input is not None:
            backend = getattr(result.input, "_backend", None)
            if backend is not None and hasattr(backend, "unload"):
                backend.unload()
    except Exception as exc:
        logger.debug(f"Backend unload: {exc}")

    return md_text, images


def _convert_xml_fallback(file_path: str) -> dict:
    """Fallback for XML files that Docling can't auto-detect (not USPTO/JATS/XBRL).

    Reads the XML as plain text so it can still be ingested, chunked, and searched.
    """
    path = Path(file_path)
    logger.info(f"Using XML plain-text fallback for {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
    md_text = f"```xml\n{text}\n```"
    return {"markdown": md_text, "filename": path.name, "images": [], "error": None}


def convert(file_path: str, use_vision: bool):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size = path.stat().st_size
    use_pypdfium = (
        USE_PYPDFIUM_FOR_LARGE_MB > 0
        and file_size > USE_PYPDFIUM_FOR_LARGE_MB * 1024 * 1024
    )

    max_file_size = MAX_FILE_SIZE_BYTES if MAX_FILE_SIZE_BYTES > 0 else (2**31 - 1)

    page_count = _get_pdf_page_count(file_path)
    is_large_pdf = page_count is not None and page_count > MAX_PAGES_PER_CHUNK

    if is_large_pdf:
        logger.info(f"Large PDF ({page_count} pages), processing in chunks of {PAGE_CHUNK_SIZE}")
        all_markdown = []
        all_images = []
        converter = _build_converter(use_vision, use_pypdfium)

        try:
            for start in range(0, page_count, PAGE_CHUNK_SIZE):
                end = min(start + PAGE_CHUNK_SIZE - 1, page_count - 1)
                logger.info(f"Converting pages {start + 1}-{end + 1} of {page_count}")
                md, imgs = _convert_chunk(
                    converter, file_path, use_vision, start, end, max_file_size
                )
                if md:
                    all_markdown.append(md)
                all_images.extend(imgs)
        finally:
            converter = None  # Allow GC

        md_text = "\n\n".join(all_markdown) if all_markdown else ""
        images = all_images
    else:
        converter = _build_converter(use_vision, use_pypdfium)
        convert_kwargs = {"max_num_pages": 500}  # Safety limit
        if max_file_size > 0 and max_file_size < (2**31 - 1):
            convert_kwargs["max_file_size"] = max_file_size
        try:
            result = converter.convert(file_path, **convert_kwargs)
        except Exception as exc:
            # XML files may fail if they don't match a known schema (USPTO/JATS/XBRL)
            if path.suffix.lower() == ".xml":
                return _convert_xml_fallback(file_path)
            raise
        dl_doc = result.document
        md_text = dl_doc.export_to_markdown()
        images = []
        if use_vision:
            try:
                images = _extract_images(dl_doc)
            except Exception as exc:
                logger.error(f"Image extraction failed (markdown still returned): {exc}")

        # Release backend memory
        try:
            if hasattr(result, "input") and result.input is not None:
                backend = getattr(result.input, "_backend", None)
                if backend is not None and hasattr(backend, "unload"):
                    backend.unload()
        except Exception as exc:
            logger.debug(f"Backend unload: {exc}")

    return {"markdown": md_text, "filename": path.name, "images": images, "error": None}


def main():
    line = sys.stdin.readline()
    if not line:
        sys.exit(1)
    req = json.loads(line)
    file_path = req["file_path"]
    use_vision = req.get("use_vision", False)

    logger.info(f"Converting {file_path} (use_vision={use_vision})")
    try:
        result = convert(file_path, use_vision)
    except Exception as exc:
        logger.error(f"Conversion failed: {exc}", exc_info=True)
        result = {"markdown": None, "filename": Path(file_path).name, "images": [], "error": str(exc)}

    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
