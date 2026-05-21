"""Vision analyzer service for extracting and analyzing images from documents.

This service handles:
- Image extraction from PDF, DOCX, PPTX, and other document formats
- Image analysis using configurable vision models
- Fallback to Docling's built-in image description capabilities
- Storage of image analysis results for RAG integration
"""

import asyncio
import base64
import functools
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from docling_core.types.doc import DoclingDocument, PictureItem
from PIL import Image

from app.config import get_settings
from app.services.reasoning_config import (
    ReasoningMode,
    build_reasoning_kwargs,
    flatten_reasoning_body,
    is_reasoning_unsupported,
    is_unsupported_reasoning_error,
    mark_reasoning_unsupported,
)

logger = logging.getLogger(__name__)


@dataclass
class ExtractedImage:
    """Represents an extracted image from a document."""

    image_id: str
    pil_image: Image.Image
    page_number: Optional[int]
    bbox: Optional[dict]
    caption: Optional[str]
    existing_description: Optional[str]


@dataclass
class ImageAnalysisResult:
    """Result of image analysis."""

    image_id: str
    description: str
    analysis_method: str
    confidence: Optional[float] = None
    labels: Optional[list[str]] = None
    ocr_text: Optional[str] = None


_vision_semaphore: Optional[asyncio.Semaphore] = None


def _get_vision_semaphore(max_concurrent: int | None = None) -> asyncio.Semaphore:
    """Global semaphore shared across all documents to limit total concurrent vision API calls."""
    global _vision_semaphore
    if _vision_semaphore is None:
        if max_concurrent is None:
            max_concurrent = get_settings().vision_max_concurrent
        _vision_semaphore = asyncio.Semaphore(max_concurrent)
    return _vision_semaphore


class VisionAnalyzer:
    """Extract and analyze images from documents using vision models."""

    def __init__(self):
        self.settings = get_settings()
        self._executor = ThreadPoolExecutor(
            max_workers=self.settings.vision_max_concurrent, thread_name_prefix="vision_"
        )

    @property
    def is_vision_model_available(self) -> bool:
        """Check if a vision model is configured and available."""
        return self.settings.vision_model_available

    def _get_pil_image_from_picture(
        self, picture: PictureItem, doc: DoclingDocument
    ) -> Optional[Image.Image]:
        """Extract PIL image from a PictureItem.

        Args:
            picture: The PictureItem from DoclingDocument
            doc: The parent DoclingDocument

        Returns:
            PIL Image or None if extraction fails
        """
        try:
            # Use Docling's built-in method to get the PIL image
            pil_image = picture.get_image(doc=doc)
            if pil_image:
                return pil_image

            # If direct extraction fails, try to load from URI
            if picture.image and picture.image.uri:
                # Handle embedded base64 images
                uri_str = str(picture.image.uri)
                if uri_str.startswith("data:image"):
                    # Extract base64 data
                    try:
                        base64_data = uri_str.split(",", 1)[1]
                        image_data = base64.b64decode(base64_data)
                        return Image.open(io.BytesIO(image_data))
                    except Exception as e:
                        logger.warning(f"Failed to decode base64 image: {e}")

                # Handle file paths
                elif uri_str.startswith(("file://", "/")):
                    file_path = uri_str.replace("file://", "")
                    if Path(file_path).exists():
                        return Image.open(file_path)

        except Exception as e:
            logger.warning(f"Failed to extract image from picture: {e}")

        return None

    def extract_images_from_document(
        self, docling_doc: DoclingDocument
    ) -> list[ExtractedImage]:
        """Extract all images from a DoclingDocument.

        Args:
            docling_doc: The converted DoclingDocument

        Returns:
            List of ExtractedImage objects
        """
        extracted_images = []

        try:
            # Iterate through all items in the document
            for item, level in docling_doc.iterate_items():
                if isinstance(item, PictureItem):
                    # Extract PIL image
                    pil_image = self._get_pil_image_from_picture(item, docling_doc)

                    if pil_image:
                        # Get page number from provenance
                        page_number = None
                        bbox = None
                        if item.prov:
                            prov = item.prov[0]
                            page_number = prov.page_no
                            if prov.bbox:
                                bbox = {
                                    "l": prov.bbox.l,
                                    "t": prov.bbox.t,
                                    "r": prov.bbox.r,
                                    "b": prov.bbox.b,
                                }

                        # Get existing caption
                        caption = None
                        try:
                            caption = item.caption_text(doc=docling_doc)
                        except Exception:
                            pass

                        # Check for existing description (from Docling's picture description)
                        existing_description = None
                        if item.meta and item.meta.description:
                            existing_description = item.meta.description.text

                        # Generate unique image ID
                        image_id = item.self_ref.replace("/", "_").replace("#", "_")

                        extracted_images.append(
                            ExtractedImage(
                                image_id=image_id,
                                pil_image=pil_image,
                                page_number=page_number,
                                bbox=bbox,
                                caption=caption,
                                existing_description=existing_description,
                            )
                        )

        except Exception as e:
            logger.error(f"Error extracting images from document: {e}")

        logger.info(f"Extracted {len(extracted_images)} images from document")
        return extracted_images

    def _pil_to_base64(self, pil_image: Image.Image, format: str = "PNG") -> str:
        """Convert PIL image to base64 string.

        Args:
            pil_image: PIL Image object
            format: Image format (PNG, JPEG, etc.)

        Returns:
            Base64 encoded image string
        """
        buffer = io.BytesIO()

        # Convert to RGB if necessary (for JPEG compatibility)
        if pil_image.mode in ("RGBA", "LA", "P"):
            pil_image = pil_image.convert("RGB")

        pil_image.save(buffer, format=format)
        image_data = buffer.getvalue()
        return base64.b64encode(image_data).decode("utf-8")

    def _pil_to_data_url(self, pil_image: Image.Image) -> str:
        """Convert PIL image to data URL format.

        Args:
            pil_image: PIL Image object

        Returns:
            Data URL string (data:image/...;base64,...)
        """
        # Determine format and MIME type
        format = "PNG" if pil_image.mode == "RGBA" else "JPEG"
        mime_type = "image/png" if format == "PNG" else "image/jpeg"

        base64_data = self._pil_to_base64(pil_image, format)
        return f"data:{mime_type};base64,{base64_data}"

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create an async HTTP client for vision API calls.

        Uses thread-local client when called from analyze_image_sync (thread pool),
        falls back to shared instance for main event loop usage.
        """
        # Prefer thread-local client (set by analyze_image_sync) to avoid cross-loop errors
        thread_client = getattr(self._thread_local, "http_client", None)
        if thread_client is not None and not thread_client.is_closed:
            return thread_client
        if not hasattr(self, "_http_client") or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def analyze_image_with_vision_model(
        self,
        pil_image: Image.Image,
        prompt: Optional[str] = None,
        max_retries: int = 3,
    ) -> Optional[str]:
        """Analyze an image using the configured vision model.

        Includes retry logic with exponential backoff.

        Args:
            pil_image: PIL Image to analyze
            prompt: Custom prompt for analysis (optional)
            max_retries: Number of retry attempts on failure

        Returns:
            Analysis text or None if analysis fails
        """
        if not self.is_vision_model_available:
            logger.warning("No vision model configured")
            return None

        api_key, base_url, model = self.settings.vision_model_config

        if not api_key:
            logger.error("No API key available for vision model")
            return None

        analysis_prompt = prompt or (
            "Analyze this image in detail for document retrieval purposes. "
            "Output ONLY the description, without any conversational filler.\n\n"
            "Provide a structured description including:\n"
            "- Summary: A concise 1-2 sentence overview.\n"
            "- Visual Elements: Describe the main objects, layout, or elements visible.\n"
            "- Text content (OCR): Transcribe any visible text accurately.\n"
            "- Data representation: For charts/graphs, explicitly describe axes, legends, trends, and key data points.\n"
            "- Context: The overall purpose and any details that help understand the document.\n\n"
            "Format your response using clear markdown headings."
        )

        image_url = self._pil_to_data_url(pil_image)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": analysis_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                }
            ],
            "max_tokens": 4096,
        }

        # Suppress reasoning on capable vision models (e.g. Qwen3-VL-27B) so
        # image descriptions don't include <think> tokens or burn budget on CoT.
        # No-op for pure instruct vision models (LLaVA, GPT-4o w/o reasoning).
        reasoning_mode = ReasoningMode.parse(self.settings.vision_reasoning_mode)
        send_reasoning = not is_reasoning_unsupported(base_url, model)
        if send_reasoning:
            sdk_kwargs = build_reasoning_kwargs(
                base_url=base_url,
                model=model,
                mode=reasoning_mode,
                overrides=self.settings.parsed_reasoning_overrides,
            )
            payload.update(flatten_reasoning_body(sdk_kwargs))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        client = self._get_http_client()
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    logger.info(f"Successfully analyzed image with vision model: {model}")
                    return content

                # If the endpoint rejected our reasoning params, strip them and
                # retry on the next loop iteration (no backoff sleep). One
                # attempt is consumed but max_retries=3 leaves plenty of slack.
                # Cache the (base_url, model) so future calls skip the params.
                if (
                    response.status_code == 400
                    and send_reasoning
                    and is_unsupported_reasoning_error(Exception(response.text))
                ):
                    logger.warning(
                        f"Vision model rejected reasoning params (model={model}): "
                        f"{response.text[:200]}. Retrying without them and caching."
                    )
                    mark_reasoning_unsupported(base_url, model)
                    for k in (
                        "reasoning_effort",
                        "reasoning",
                        "thinking",
                        "chat_template_kwargs",
                        "venice_parameters",
                    ):
                        payload.pop(k, None)
                    send_reasoning = False
                    continue  # skip the backoff sleep below and re-POST

                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(
                    f"Vision model API error (attempt {attempt}/{max_retries}): {last_error}"
                )

            except httpx.TimeoutException:
                last_error = "request timed out"
                logger.warning(
                    f"Vision model API timeout (attempt {attempt}/{max_retries})"
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Vision model API error (attempt {attempt}/{max_retries}): {e}"
                )

            if attempt < max_retries:
                backoff = 2 ** (attempt - 1)
                logger.info(f"Retrying in {backoff}s...")
                await asyncio.sleep(backoff)

        logger.error(f"Vision model failed after {max_retries} attempts: {last_error}")
        return None

    def analyze_image_with_docling(
        self, extracted_image: ExtractedImage
    ) -> Optional[str]:
        """Return Docling's pre-generated image description from conversion.

        This method returns the description that Docling generated during document
        conversion when do_picture_description=True is enabled in the pipeline options.

        NOTE: This requires do_picture_description=True in Docling pipeline options.
        If not enabled during conversion, this will always return None.

        Args:
            extracted_image: The extracted image with metadata

        Returns:
            Description text or None if no description was generated
        """
        # Return existing description from Docling if available
        if extracted_image.existing_description:
            return extracted_image.existing_description

        logger.info("No Docling description available for this image")
        return None

    async def analyze_image(
        self,
        extracted_image: ExtractedImage,
        force_vision_model: bool = False,
        custom_prompt: Optional[str] = None,
    ) -> ImageAnalysisResult:
        """Analyze an extracted image.

        Uses vision model if configured and available, otherwise falls back to
        Docling's built-in description (if available).

        Args:
            extracted_image: The extracted image to analyze
            force_vision_model: Force using vision model even if Docling description exists
            custom_prompt: Custom prompt for vision model analysis

        Returns:
            ImageAnalysisResult with description and metadata
        """
        description = None
        analysis_method = "none"

        # Try vision model first if configured
        if self.is_vision_model_available and (
            force_vision_model or not extracted_image.existing_description
        ):
            description = await self.analyze_image_with_vision_model(
                extracted_image.pil_image, prompt=custom_prompt
            )
            if description:
                analysis_method = "vision_model"

        # Fall back to Docling's description if vision model failed or not configured
        if not description:
            description = self.analyze_image_with_docling(extracted_image)
            if description:
                analysis_method = "docling"

        # If still no description, create a basic one
        if not description:
            description = f"Image on page {extracted_image.page_number or 'unknown'}"
            if extracted_image.caption:
                description += f" - Caption: {extracted_image.caption}"
            analysis_method = "fallback"

        return ImageAnalysisResult(
            image_id=extracted_image.image_id,
            description=description,
            analysis_method=analysis_method,
        )

    _thread_local = threading.local()

    def analyze_image_sync(
        self,
        extracted_image: ExtractedImage,
        force_vision_model: bool = False,
        custom_prompt: Optional[str] = None,
    ) -> ImageAnalysisResult:
        """Synchronous version of analyze_image for thread pool execution.

        This runs the async analyze_image in a new event loop within the thread,
        preventing the main event loop from being blocked by long HTTP calls.
        Uses a thread-local HTTP client to avoid cross-loop conflicts.

        IMPORTANT: This method must only be called from within a ThreadPoolExecutor
        to avoid blocking the main event loop.
        """
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            # Thread-local HTTP client avoids "Event loop is closed" errors
            # when multiple threads each run their own event loop
            client = httpx.AsyncClient(timeout=120.0)
            self._thread_local.http_client = client
            try:
                result = loop.run_until_complete(
                    self.analyze_image(extracted_image, force_vision_model, custom_prompt)
                )
                return result
            finally:
                loop.run_until_complete(client.aclose())
        finally:
            loop.close()

    async def analyze_all_images(
        self,
        docling_doc: DoclingDocument,
        force_vision_model: bool = False,
        custom_prompt: Optional[str] = None,
    ) -> list[ImageAnalysisResult]:
        """Extract and analyze all images from a document.

        Uses a global semaphore (shared across all documents) to limit total
        concurrent vision API calls. Images are processed concurrently within
        each document, gated by the semaphore.

        Args:
            docling_doc: The converted DoclingDocument
            force_vision_model: Force using vision model for all images
            custom_prompt: Custom prompt for vision model analysis

        Returns:
            List of ImageAnalysisResult objects (in original order)
        """
        extracted_images = await asyncio.get_event_loop().run_in_executor(
            self._executor,
            functools.partial(self.extract_images_from_document, docling_doc),
        )

        if not extracted_images:
            logger.info("No images extracted from document - nothing to analyze")
            return []

        total = len(extracted_images)
        semaphore = _get_vision_semaphore()
        logger.info(
            f"Analyzing {total} images "
            f"(force_vision_model={force_vision_model}, vision_available={self.is_vision_model_available})"
        )

        async def analyze_one(idx: int, img: ExtractedImage) -> ImageAnalysisResult:
            async with semaphore:
                logger.info(f"Analyzing image {idx + 1}/{total}: {img.image_id} (page {img.page_number})")
                result = await self.analyze_image(
                    img, force_vision_model=force_vision_model, custom_prompt=custom_prompt
                )
                logger.info(f"Image {idx + 1} result: method={result.analysis_method}, description_length={len(result.description)}")
                return result

        tasks = [analyze_one(idx, img) for idx, img in enumerate(extracted_images)]
        results = await asyncio.gather(*tasks)
        return list(results)

    def extract_and_save_images(
        self,
        docling_doc: DoclingDocument,
        output_dir: Path,
    ) -> list[tuple[str, Path]]:
        """Extract all images from a document and save them to disk.

        Args:
            docling_doc: The converted DoclingDocument
            output_dir: Directory to save images

        Returns:
            List of (image_id, file_path) tuples
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        extracted_images = self.extract_images_from_document(docling_doc)

        saved_images = []
        for img in extracted_images:
            try:
                # Generate filename
                ext = "png" if img.pil_image.mode == "RGBA" else "jpg"
                filename = f"{img.image_id}.{ext}"
                file_path = output_dir / filename

                # Save image
                img.pil_image.save(file_path)
                saved_images.append((img.image_id, file_path))
                logger.info(f"Saved image: {file_path}")

            except Exception as e:
                logger.error(f"Failed to save image {img.image_id}: {e}")

        return saved_images


# Singleton instance
_vision_analyzer: Optional[VisionAnalyzer] = None


def get_vision_analyzer() -> VisionAnalyzer:
    """Get the singleton VisionAnalyzer instance."""
    global _vision_analyzer
    if _vision_analyzer is None:
        _vision_analyzer = VisionAnalyzer()
    return _vision_analyzer
