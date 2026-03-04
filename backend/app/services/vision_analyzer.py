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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from docling_core.types.doc import DoclingDocument, PictureItem
from PIL import Image

from app.config import get_settings

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


class VisionAnalyzer:
    """Extract and analyze images from documents using vision models."""

    def __init__(self):
        self.settings = get_settings()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vision_")

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

    async def analyze_image_with_vision_model(
        self,
        pil_image: Image.Image,
        prompt: Optional[str] = None,
    ) -> Optional[str]:
        """Analyze an image using the configured vision model.

        Args:
            pil_image: PIL Image to analyze
            prompt: Custom prompt for analysis (optional)

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

        # Default analysis prompt
        analysis_prompt = prompt or (
            "Analyze this image in detail. Describe what you see, including:\n"
            "1. Main objects, people, or elements visible\n"
            "2. Text visible in the image (if any)\n"
            "3. Charts, diagrams, or data visualizations (if any)\n"
            "4. Overall context and purpose of the image\n"
            "5. Any relevant details that would help understand the document\n\n"
            "Provide a comprehensive description suitable for document retrieval and understanding."
        )

        # Convert image to base64 data URL
        image_url = self._pil_to_data_url(pil_image)

        # Prepare request payload for OpenAI-compatible vision API
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

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
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
                else:
                    logger.error(
                        f"Vision model API error: {response.status_code} - {response.text}"
                    )
                    return None

        except httpx.TimeoutException:
            logger.error("Vision model API request timed out")
            return None
        except Exception as e:
            logger.error(f"Error calling vision model API: {e}")
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

    async def analyze_all_images(
        self,
        docling_doc: DoclingDocument,
        force_vision_model: bool = False,
        custom_prompt: Optional[str] = None,
    ) -> list[ImageAnalysisResult]:
        """Extract and analyze all images from a document.

        Args:
            docling_doc: The converted DoclingDocument
            force_vision_model: Force using vision model for all images
            custom_prompt: Custom prompt for vision model analysis

        Returns:
            List of ImageAnalysisResult objects
        """
        # Extract images
        extracted_images = await asyncio.get_event_loop().run_in_executor(
            self._executor,
            functools.partial(self.extract_images_from_document, docling_doc),
        )

        if not extracted_images:
            logger.info("No images extracted from document - nothing to analyze")
            return []

        logger.info(
            f"Analyzing {len(extracted_images)} images "
            f"(force_vision_model={force_vision_model}, vision_available={self.is_vision_model_available})"
        )

        # Analyze each image
        results = []
        for idx, img in enumerate(extracted_images):
            logger.info(f"Analyzing image {idx + 1}/{len(extracted_images)}: {img.image_id} (page {img.page_number})")
            result = await self.analyze_image(
                img, force_vision_model=force_vision_model, custom_prompt=custom_prompt
            )
            logger.info(f"Image {idx + 1} result: method={result.analysis_method}, description_length={len(result.description)}")
            results.append(result)

        return results

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
