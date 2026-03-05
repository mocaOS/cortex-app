"""Standalone Docling conversion worker.

Runs document conversion in a separate process so that CPU-bound ML
inference (layout detection, OCR, table structure) does NOT hold the
GIL in the main FastAPI process, keeping the event loop responsive.

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
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("docling_worker")


def _build_converter(use_vision: bool):
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        EasyOcrOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, InputFormat, PdfFormatOption, ImageFormatOption

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

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        }
    )


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


def convert(file_path: str, use_vision: bool):
    converter = _build_converter(use_vision)
    result = converter.convert(file_path)
    dl_doc = result.document
    md_text = dl_doc.export_to_markdown()
    images = []
    if use_vision:
        try:
            images = _extract_images(dl_doc)
        except Exception as exc:
            logger.error(f"Image extraction failed (markdown still returned): {exc}")
    return {"markdown": md_text, "filename": Path(file_path).name, "images": images, "error": None}


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
