"""Document processing service using Haystack with GraphRAG support.

Features:
- Hybrid search with RRF
- Conversation memory
- Re-ranking with cross-encoder
- Agentic multi-step RAG with extended thinking
- Enhanced chunking
- Collection-level organization
- Community-aware retrieval
- Semantic entity resolution
- Image extraction and vision model analysis
"""

import asyncio
import functools
import json
import logging
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Callable, Dict, List, Optional, Tuple

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    OcrOptions,
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
    TesseractOcrOptions,
)

# Docling imports for enhanced OCR
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_haystack.converter import DoclingConverter, ExportType
from haystack import Document as HaystackDocument
from haystack.components.preprocessors import DocumentSplitter

from app.config import get_settings
from app.models import (
    ConversationMessage,
    DocumentChunk,
    DocumentMetadata,
    GraphContext,
    ProcessingStatus,
    ReasoningStep,
    ThinkingEvent,
)
from app.services.graph_extractor import get_graph_extractor
from app.services.llm_config import get_llm_config
from app.services.neo4j_service import get_neo4j_service
from app.services.prompt_security import (
    get_anti_injection_instruction,
    get_safe_refusal_message,
    validate_and_process_input,
)
from app.services.vision_analyzer import get_vision_analyzer

logger = logging.getLogger(__name__)

# =============================================================================
# URL Protection for Chunking
# =============================================================================
# URLs contain word boundaries (/, ., -, etc.) that can cause them to be split
# when using word-based or sentence-based chunking. To prevent this, we:
# 1. Replace URLs with unique placeholders before splitting
# 2. Perform the split operation
# 3. Restore URLs from placeholders after splitting

# Regex pattern to match URLs (http, https, ftp, mailto, etc.)
# This pattern captures most common URL formats including:
# - http://example.com, https://example.com
# - ftp://files.example.com
# - mailto:user@example.com
# - URLs with paths, query params, fragments
URL_PATTERN = re.compile(
    r"(?:https?://|ftp://|mailto:)"  # Protocol
    r"[^\s<>\[\](){}\"\'`]+",  # URL body (no whitespace or brackets)
    re.IGNORECASE,
)

# Placeholder format that's unlikely to appear in real text and won't be split
URL_PLACEHOLDER_PREFIX = "§§URL_PLACEHOLDER_"
URL_PLACEHOLDER_SUFFIX = "§§"

# Docling inserts <!-- image --> HTML comments as placeholders for images in markdown.
# These are noise when images are analyzed separately by a vision model.
IMAGE_PLACEHOLDER_PATTERN = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)


def _clean_image_placeholders(text: str) -> str:
    """Remove Docling image placeholder comments and collapse resulting blank lines."""
    cleaned = IMAGE_PLACEHOLDER_PATTERN.sub("", text)
    # Collapse runs of 3+ newlines (left behind after removing placeholders) into 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _protect_urls(text: str) -> Tuple[str, dict]:
    """
    Replace URLs in text with placeholders to prevent splitting.

    Returns:
        Tuple of (modified text, mapping of placeholder -> original URL)
    """
    url_map = {}

    def replace_url(match):
        url = match.group(0)
        placeholder_id = len(url_map)
        placeholder = (
            f"{URL_PLACEHOLDER_PREFIX}{placeholder_id}{URL_PLACEHOLDER_SUFFIX}"
        )
        url_map[placeholder] = url
        return placeholder

    protected_text = URL_PATTERN.sub(replace_url, text)
    return protected_text, url_map


def _restore_urls(text: str, url_map: dict) -> str:
    """
    Restore URLs from placeholders.

    Args:
        text: Text with placeholders
        url_map: Mapping of placeholder -> original URL

    Returns:
        Text with original URLs restored
    """
    result = text
    for placeholder, url in url_map.items():
        result = result.replace(placeholder, url)
    return result


# Thread pool for re-ranking (cross-encoder can be slow)
_rerank_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reranker")

# Thread pool for document processing (Neo4j writes, embeddings, etc.)
# Initialized lazily to use config settings.
_processing_executor: Optional[ThreadPoolExecutor] = None

# Semaphore to limit concurrent subprocess conversions (avoid OOM from
# running too many Docling processes at once).
_conversion_semaphore: Optional[asyncio.Semaphore] = None

# Separate thread pool for background image analysis so it never competes
# with the main processing pipeline for thread pool capacity.
_image_executor: Optional[ThreadPoolExecutor] = None


def _get_processing_executor() -> ThreadPoolExecutor:
    """Get or create the processing thread pool executor."""
    global _processing_executor
    if _processing_executor is None:
        settings = get_settings()
        _processing_executor = ThreadPoolExecutor(
            max_workers=settings.processing_thread_workers, thread_name_prefix="docproc"
        )
        logger.info(
            f"Initialized processing executor with {settings.processing_thread_workers} workers"
        )
    return _processing_executor


def _get_conversion_semaphore() -> asyncio.Semaphore:
    """Get or create the semaphore that limits concurrent subprocess conversions."""
    global _conversion_semaphore
    if _conversion_semaphore is None:
        _conversion_semaphore = asyncio.Semaphore(1)
    return _conversion_semaphore


async def _convert_document_subprocess(file_path: str, use_vision: bool) -> dict:
    """Run Docling conversion in a subprocess to avoid GIL contention.

    Returns dict with keys: markdown, filename, images, error.
    """
    import json as _json

    sem = _get_conversion_semaphore()
    async with sem:
        logger.info(f"Starting subprocess conversion for {Path(file_path).name}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.services.docling_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        request_data = _json.dumps({"file_path": file_path, "use_vision": use_vision}) + "\n"
        stdout, stderr = await proc.communicate(request_data.encode())

        if stderr:
            for line in stderr.decode(errors="replace").strip().split("\n"):
                if line.strip():
                    logger.info(f"[docling-worker] {line.strip()}")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Docling worker exited with code {proc.returncode}: "
                f"{stderr.decode(errors='replace')[:500]}"
            )

        stdout_text = stdout.decode().strip()
        if not stdout_text:
            raise RuntimeError("Docling worker returned empty output")

        result = _json.loads(stdout_text)
        if result.get("error"):
            raise RuntimeError(f"Docling worker error: {result['error']}")

        logger.info(
            f"Subprocess conversion complete for {result.get('filename', '?')} "
            f"(md_len={len(result.get('markdown') or '')}, images={len(result.get('images', []))})"
        )
        return result


def _get_image_executor() -> ThreadPoolExecutor:
    """Get or create a dedicated thread pool for background image analysis."""
    global _image_executor
    if _image_executor is None:
        settings = get_settings()
        _image_executor = ThreadPoolExecutor(
            max_workers=settings.vision_max_concurrent, thread_name_prefix="imgproc"
        )
    return _image_executor


# =============================================================================
# Task Tracking for Document Processing
# =============================================================================
# Track active processing tasks per document to enable cancellation when
# documents are deleted. This ensures clean shutdown of processing before
# removing document data from the knowledge graph.

# Registry of active processing tasks by document ID
_active_tasks: Dict[str, asyncio.Task] = {}

# Lock for thread-safe access to task registry
_task_lock: Optional[asyncio.Lock] = None

# Cancellation flags per document - set when processing should stop
_cancellation_flags: Dict[str, asyncio.Event] = {}


def _get_task_lock() -> asyncio.Lock:
    """Get or create the task registry lock (must be created in async context)."""
    global _task_lock
    if _task_lock is None:
        _task_lock = asyncio.Lock()
    return _task_lock


class CancellationRequested(Exception):
    """Raised when document processing is cancelled."""

    pass


class DocumentProcessor:
    """Process documents using Haystack components with GraphRAG extraction."""

    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        self.vision_analyzer = get_vision_analyzer()

        # Initialize Docling converter with enhanced OCR settings for better image text extraction
        # Configure pipeline options for maximum accuracy on scanned documents and images
        # When vision model is set: skip local OCR and picture description - use vision model for images
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = not self.vision_analyzer.is_vision_model_available
        pipeline_options.do_table_structure = True
        pipeline_options.do_picture_description = not self.vision_analyzer.is_vision_model_available
        pipeline_options.table_structure_options = TableStructureOptions(
            do_cell_matching=True,
            mode=TableFormerMode.ACCURATE,  # Prioritize accuracy over speed
        )

        # Configure EasyOCR only when no vision model - avoid local CPU/GPU processing when vision model is set
        if not self.vision_analyzer.is_vision_model_available:
            pipeline_options.ocr_options = EasyOcrOptions(
                lang=["en", "de"],
                use_gpu=True,  # Enable GPU acceleration if available
                confidence_threshold=0.2,  # Lower threshold = more aggressive text detection
            )

        # Alternative: Tesseract OCR (uncomment to use instead of EasyOCR)
        # pipeline_options.ocr_options = TesseractOcrOptions(
        #     lang=["eng"],
        #     force_full_page_ocr=True,  # Force OCR on entire page for scanned docs
        # )

        # Configure accelerator for thorough processing
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=8,  # Increase threads for more thorough processing
            device=AcceleratorDevice.AUTO,  # Auto-detect CUDA/MPS/CPU
        )

        # Generate higher resolution images for better OCR accuracy
        pipeline_options.generate_page_images = True
        pipeline_options.images_scale = 2.0  # 2x resolution for better text recognition

        # Create underlying Docling DocumentConverter with enhanced pipeline options
        # This handles PDF, IMAGE, and other formats with the configured OCR settings
        underlying_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )

        # Create Haystack DoclingConverter with the pre-configured underlying converter
        # Uses MARKDOWN export type for consistent processing with existing pipeline
        self.docling_converter = DoclingConverter(
            export_type=ExportType.MARKDOWN,
            converter=underlying_converter,
        )
        # Log initialization with image analysis and OCR method
        if self.vision_analyzer.is_vision_model_available:
            logger.info(
                "Docling converter initialized (markdown only, no local OCR/VLM - image analysis via external vision model)"
            )
        else:
            logger.info(
                "Docling converter initialized with enhanced OCR and Docling SmolVLM (EasyOCR, 8 threads, 2x image scale)"
            )

        # Initialize splitter based on configuration
        # Sentence-based splitting preserves semantic units better
        if self.settings.chunk_by == "sentence":
            self.splitter = DocumentSplitter(
                split_by="sentence",
                split_length=self.settings.sentences_per_chunk,
                split_overlap=1,  # 1 sentence overlap for context continuity
            )
            logger.info(
                f"Using sentence-based chunking: {self.settings.sentences_per_chunk} sentences per chunk"
            )
        else:
            self.splitter = DocumentSplitter(
                split_by="word",
                split_length=self.settings.chunk_size,
                split_overlap=self.settings.chunk_overlap,
            )
            logger.info(
                f"Using word-based chunking: {self.settings.chunk_size} words per chunk"
            )

        # Initialize embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.embed_api_key:
            from haystack.components.embedders import OpenAIDocumentEmbedder
            from haystack.utils import Secret

            embedder_kwargs = dict(
                api_key=Secret.from_token(self.settings.embed_api_key),
                api_base_url=self.settings.embed_api_base,
                model=self.settings.embedding_model,
            )
            if self.settings.embedding_send_dimensions:
                embedder_kwargs["dimensions"] = self.settings.embedding_dimension
            self.embedder = OpenAIDocumentEmbedder(**embedder_kwargs)
            logger.info(
                f"Using OpenAI embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})"
            )
        else:
            from haystack.components.embedders import (
                SentenceTransformersDocumentEmbedder,
            )

            self.embedder = SentenceTransformersDocumentEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.embedder.warm_up()
            logger.info("Using SentenceTransformers embeddings")

        logger.info(
            f"Document processor initialized (GraphRAG: {self.graph_extractor.is_available}, Vision: {self.vision_analyzer.is_vision_model_available})"
        )

    # =========================================================================
    # Task Cancellation Methods
    # =========================================================================

    async def cancel_document_processing(self, doc_id: str) -> bool:
        """
        Cancel any active processing task for a document.

        This method:
        1. Sets the cancellation flag to signal the processing loop to stop
        2. Cancels the asyncio task
        3. Waits for the task to finish (with timeout)
        4. Cleans up the task registry

        Args:
            doc_id: The document ID to cancel processing for

        Returns:
            True if a task was cancelled, False if no task was running
        """
        task_lock = _get_task_lock()
        task_to_wait = None
        was_running = False

        async with task_lock:
            was_running = doc_id in _active_tasks

            # Set cancellation flag FIRST (for graceful shutdown via _check_cancellation)
            if doc_id in _cancellation_flags:
                _cancellation_flags[doc_id].set()
                logger.info(f"Set cancellation flag for document {doc_id}")

            # Get the task to cancel (we'll wait outside the lock)
            if doc_id in _active_tasks:
                task_to_wait = _active_tasks[doc_id]
                if not task_to_wait.done():
                    task_to_wait.cancel()
                    logger.info(
                        f"Sent cancel signal to processing task for document {doc_id}"
                    )

        # Wait for task outside the lock to avoid deadlock
        if task_to_wait and not task_to_wait.done():
            try:
                # Wait for the task to actually finish
                await asyncio.wait_for(
                    asyncio.gather(task_to_wait, return_exceptions=True),
                    timeout=10.0,  # Increased timeout
                )
                logger.info(f"Processing task for document {doc_id} has stopped")
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout waiting for task cancellation for document {doc_id}"
                )
            except Exception as e:
                logger.warning(f"Error waiting for task cancellation: {e}")

        # Clean up after waiting
        async with task_lock:
            if doc_id in _active_tasks:
                del _active_tasks[doc_id]
            if doc_id in _cancellation_flags:
                del _cancellation_flags[doc_id]

        if was_running:
            # Update document status to indicate cancellation
            try:
                self.neo4j.update_document_status(
                    doc_id,
                    ProcessingStatus.FAILED,
                    error_message="Processing cancelled (document deleted)",
                )
            except Exception as e:
                logger.warning(
                    f"Could not update status for cancelled document {doc_id}: {e}"
                )

        return was_running

    async def cancel_multiple_documents(self, doc_ids: List[str]) -> int:
        """
        Cancel processing for multiple documents.

        Args:
            doc_ids: List of document IDs to cancel

        Returns:
            Number of tasks that were cancelled
        """
        cancelled_count = 0
        for doc_id in doc_ids:
            if await self.cancel_document_processing(doc_id):
                cancelled_count += 1
        return cancelled_count

    async def cancel_all_processing(self) -> int:
        """
        Cancel all active processing tasks.

        Returns:
            Number of tasks that were cancelled
        """
        task_lock = _get_task_lock()
        async with task_lock:
            doc_ids = list(_active_tasks.keys())

        cancelled_count = 0
        for doc_id in doc_ids:
            if await self.cancel_document_processing(doc_id):
                cancelled_count += 1

        logger.info(f"Cancelled {cancelled_count} processing tasks")
        return cancelled_count

    def is_processing(self, doc_id: str) -> bool:
        """
        Check if a document is currently being processed.

        Args:
            doc_id: The document ID to check

        Returns:
            True if the document has an active processing task
        """
        if doc_id not in _active_tasks:
            return False
        task = _active_tasks[doc_id]
        return not task.done()

    def get_processing_documents(self) -> List[str]:
        """
        Get list of document IDs currently being processed.

        Returns:
            List of document IDs with active processing tasks
        """
        return [doc_id for doc_id, task in _active_tasks.items() if not task.done()]

    def _check_cancellation(self, doc_id: str) -> None:
        """
        Check if processing has been cancelled for a document.

        Raises:
            CancellationRequested: If cancellation was requested
        """
        if doc_id in _cancellation_flags and _cancellation_flags[doc_id].is_set():
            raise CancellationRequested(f"Processing cancelled for document {doc_id}")

    async def _register_task(self, doc_id: str, task: asyncio.Task) -> None:
        """Register a processing task in the task registry."""
        task_lock = _get_task_lock()
        async with task_lock:
            # Cancel any existing task for this document
            if doc_id in _active_tasks:
                old_task = _active_tasks[doc_id]
                if not old_task.done():
                    old_task.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(old_task), timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass

            # Create cancellation flag
            _cancellation_flags[doc_id] = asyncio.Event()

            # Register new task
            _active_tasks[doc_id] = task
            logger.debug(f"Registered processing task for document {doc_id}")

    async def _unregister_task(self, doc_id: str) -> None:
        """Unregister a processing task from the task registry."""
        task_lock = _get_task_lock()
        async with task_lock:
            if doc_id in _active_tasks:
                del _active_tasks[doc_id]
            if doc_id in _cancellation_flags:
                del _cancellation_flags[doc_id]
            logger.debug(f"Unregistered processing task for document {doc_id}")

    async def _start_processing(
        self, doc_id: str, file_path: str, file_type: str
    ) -> None:
        """
        Start a document processing task with proper tracking.

        This method:
        1. Sets up cancellation flag FIRST (before task starts)
        2. Creates the asyncio task
        3. Registers it in the task registry
        4. Ensures cleanup on completion

        Args:
            doc_id: Document ID to process
            file_path: Path to the file
            file_type: File extension/type
        """
        task_lock = _get_task_lock()

        async with task_lock:
            # Cancel any existing task for this document FIRST
            if doc_id in _active_tasks:
                old_task = _active_tasks[doc_id]
                if doc_id in _cancellation_flags:
                    _cancellation_flags[doc_id].set()
                if not old_task.done():
                    old_task.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(old_task), timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass

            # Create cancellation flag BEFORE starting task
            # This ensures _check_cancellation works from the first checkpoint
            _cancellation_flags[doc_id] = asyncio.Event()

            # Now create the processing task
            task = asyncio.create_task(
                self._process_document_with_cleanup(doc_id, file_path, file_type)
            )

            # Register it for tracking
            _active_tasks[doc_id] = task
            logger.info(f"Started processing task for document {doc_id}")

    async def _process_document_with_cleanup(
        self, doc_id: str, file_path: str, file_type: str
    ) -> None:
        """
        Wrapper around _process_document that ensures task cleanup on completion.
        """
        try:
            await self._process_document(doc_id, file_path, file_type)
        finally:
            # Always unregister the task when done (success or failure)
            await self._unregister_task(doc_id)

    # =========================================================================
    # Document Processing Methods
    # =========================================================================

    def queue_document_for_reprocessing(self, doc_id: str) -> bool:
        """
        Queue a document for reprocessing by resetting its status to pending.

        This only clears chunks and sets status - does NOT start processing.
        Call process_pending_documents() to start the actual processing.

        Returns True if successfully queued.
        Raises ValueError if document not found or file not available.
        """
        # Get document info
        doc_info = self.neo4j.get_document(doc_id)
        if not doc_info:
            raise ValueError(f"Document {doc_id} not found")

        file_path = doc_info.get("file_path")

        # Check if original file exists
        if not file_path or not os.path.exists(file_path):
            raise ValueError(
                f"Original file not available for document {doc_id}. "
                f"File path: {file_path}"
            )

        # Delete existing chunks and entities
        cleanup_result = self.neo4j.delete_document_chunks(doc_id)
        logger.info(
            f"Cleaned up document {doc_id}: "
            f"{cleanup_result['chunks_deleted']} chunks, "
            f"{cleanup_result['orphaned_entities_removed']} orphaned entities"
        )

        # Update status to pending (will be picked up by process_pending_documents)
        self.neo4j.update_document_status(
            doc_id, ProcessingStatus.PENDING, progress_message="Queued for reprocessing"
        )

        return True

    async def reprocess_document(self, doc_id: str) -> bool:
        """
        Reprocess an existing document using its stored file.

        WARNING: This starts processing immediately. For batch reprocessing,
        use queue_document_for_reprocessing() + process_pending_documents() instead.

        Returns True if reprocessing started successfully.
        Raises ValueError if document not found or file not available.
        """
        # Get document info
        doc_info = self.neo4j.get_document(doc_id)
        if not doc_info:
            raise ValueError(f"Document {doc_id} not found")

        file_path = doc_info.get("file_path")
        file_type = doc_info["file_type"]

        # Check if original file exists
        if not file_path or not os.path.exists(file_path):
            raise ValueError(
                f"Original file not available for document {doc_id}. "
                f"File path: {file_path}"
            )

        # Delete existing chunks and entities
        cleanup_result = self.neo4j.delete_document_chunks(doc_id)
        logger.info(
            f"Cleaned up document {doc_id}: "
            f"{cleanup_result['chunks_deleted']} chunks, "
            f"{cleanup_result['orphaned_entities_removed']} orphaned entities"
        )

        # Update status to pending
        self.neo4j.update_document_status(
            doc_id, ProcessingStatus.PENDING, progress_message="Queued for reprocessing"
        )

        # Start reprocessing in background using stored file (with task tracking)
        await self._start_processing(doc_id, file_path, file_type)

        return True

    async def reprocess_document_from_file(
        self, doc_id: str, file_path: str, file_type: str
    ) -> bool:
        """
        Reprocess a document from an existing file.

        This deletes existing chunks/entities and reprocesses the file.
        """
        # Delete existing chunks and entities
        cleanup_result = self.neo4j.delete_document_chunks(doc_id)
        logger.info(
            f"Cleaned up document {doc_id}: "
            f"{cleanup_result['chunks_deleted']} chunks, "
            f"{cleanup_result['orphaned_entities_removed']} orphaned entities"
        )

        # Process in background (same as new document, with task tracking)
        await self._start_processing(doc_id, file_path, file_type)

        return True

    # Docling-supported file extensions
    DOCLING_EXTENSIONS = {
        # Office documents
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        # Web pages
        ".html",
        ".htm",
        # Text files
        ".txt",
        ".md",
        ".mdx",
        ".markdown",
        ".rst",
        # Images (OCR)
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".tif",
        ".bmp",
        # Audio (ASR)
        ".wav",
        ".mp3",
        ".webvtt",
        ".vtt",
        # LaTeX
        ".tex",
        ".latex",
        # XML schemas (USPTO, JATS, XBRL)
        ".xml",
    }

    def _get_converter(self, file_type: str):
        """Get the appropriate converter for a file type.

        Docling handles all supported document formats natively.
        """
        if file_type.lower() in self.DOCLING_EXTENSIONS:
            return self.docling_converter
        return None

    async def store_file_only(
        self,
        file_path: str,
        filename: str,
        file_size: int,
        collection_id: Optional[str] = None,
    ) -> str:
        """
        Store a file without processing it.

        Used for bulk uploads where processing happens later.
        Call process_pending_documents() after all uploads complete.

        Args:
            file_path: Path to the uploaded file
            filename: Original filename
            file_size: Size in bytes
            collection_id: Optional collection to add document to

        Returns:
            Document ID
        """
        doc_id = str(uuid.uuid4())
        file_type = Path(filename).suffix.lower()

        # Use default collection if enabled and none specified
        if collection_id is None and self.settings.enable_collections:
            collection_id = self.settings.default_collection

        # Create document metadata with file path for permanent storage
        metadata = DocumentMetadata(
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            file_path=file_path,
            processing_status=ProcessingStatus.PENDING,
        )

        # Store document node (no processing yet)
        self.neo4j.store_document(doc_id, metadata)

        # Add to collection if specified
        if collection_id:
            self.neo4j.add_document_to_collection(doc_id, collection_id)

        logger.info(f"Stored file {filename} as document {doc_id} (pending processing)")
        return doc_id

    async def process_file(
        self,
        file_path: str,
        filename: str,
        file_size: int,
        collection_id: Optional[str] = None,
    ) -> str:
        """
        Process a file and store it in the knowledge base.

        The original file is permanently stored and can be used for reprocessing.

        Args:
            file_path: Path to the uploaded file
            filename: Original filename
            file_size: Size in bytes
            collection_id: Optional collection to add document to
        """
        # First store the file
        doc_id = await self.store_file_only(
            file_path, filename, file_size, collection_id
        )

        # Then start processing (with task tracking for cancellation support)
        file_type = Path(filename).suffix.lower()
        await self._start_processing(doc_id, file_path, file_type)

        return doc_id

    def get_pending_documents(self) -> List[dict]:
        """Get all documents with pending status."""
        all_docs = self.neo4j.get_all_documents()
        return [d for d in all_docs if d.get("processing_status") == "pending"]

    async def process_pending_documents(
        self,
        concurrency: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict:
        """
        Process all pending documents with controlled concurrency.

        Args:
            concurrency: Number of documents to process concurrently (defaults to config)
            progress_callback: Optional callback(current, total, message)

        Returns:
            Dict with processing stats
        """
        # Use config default if not specified
        if concurrency is None:
            concurrency = self.settings.batch_processing_concurrency

        pending = self.get_pending_documents()
        total = len(pending)

        if total == 0:
            return {"processed": 0, "total": 0, "message": "No pending documents"}

        logger.info(
            f"Starting processing of {total} pending documents (concurrency: {concurrency})"
        )

        semaphore = asyncio.Semaphore(concurrency)
        completed = 0
        failed = 0

        async def process_one(doc: dict):
            nonlocal completed, failed
            async with semaphore:
                # Yield before starting so the event loop can process pending requests
                await asyncio.sleep(0)
                doc_id = doc["id"]
                file_path = doc.get("file_path")
                file_type = doc.get("file_type", "")

                if not file_path or not os.path.exists(file_path):
                    logger.error(f"File not found for document {doc_id}: {file_path}")
                    await asyncio.get_event_loop().run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.update_document_status,
                            doc_id,
                            ProcessingStatus.FAILED,
                            error_message=f"File not found: {file_path}",
                        ),
                    )
                    failed += 1
                    return

                try:
                    # Register task for tracking (enables cancellation during batch processing)
                    task_lock = _get_task_lock()
                    async with task_lock:
                        _cancellation_flags[doc_id] = asyncio.Event()
                        # We don't store in _active_tasks since we await directly

                    await self._process_document(doc_id, file_path, file_type)
                    completed += 1
                except CancellationRequested:
                    logger.info(f"Processing cancelled for document {doc_id}")
                    failed += 1
                except Exception as e:
                    logger.error(f"Error processing document {doc_id}: {e}")
                    failed += 1
                finally:
                    # Clean up cancellation flag
                    if doc_id in _cancellation_flags:
                        del _cancellation_flags[doc_id]

                if progress_callback:
                    progress_callback(
                        completed + failed,
                        total,
                        f"Processed {completed + failed}/{total}",
                    )

        # Process all pending documents
        tasks = [process_one(doc) for doc in pending]
        await asyncio.gather(*tasks)

        logger.info(
            f"Processing complete: {completed} succeeded, {failed} failed out of {total}"
        )

        # Auto-trigger relationship analysis after batch processing
        if self.settings.auto_relationship_analysis_after_batch and completed > 0:
            logger.info("Auto-triggering relationship analysis after batch processing")
            try:
                await self.analyze_collection_relationships(
                    collection_id=None,
                    scope="full",
                    progress_callback=progress_callback,
                )
            except Exception as e:
                logger.error(f"Auto relationship analysis failed: {e}")

        # Auto-trigger community detection after batch processing
        if self.settings.auto_community_detection_after_batch and completed > 0:
            logger.info("Auto-triggering community detection after batch processing")
            try:
                communities = await asyncio.to_thread(
                    self.neo4j.detect_communities,
                    self.settings.min_community_size,
                )
                if communities and self.settings.enable_graph_summarization:
                    for community in communities:
                        entity_names = [e.get("name") for e in community.get("entities", [])]
                        rels = await asyncio.to_thread(
                            self.neo4j.get_community_relationships, community["id"]
                        )
                        summary = await self.graph_extractor.generate_community_summary_async(
                            community.get("entities", []), rels
                        )
                        await asyncio.to_thread(
                            self.neo4j.store_community,
                            community["id"], entity_names,
                            summary.get("summary"), summary.get("name"),
                        )
            except Exception as e:
                logger.error(f"Auto community detection failed: {e}")

        return {
            "processed": completed,
            "failed": failed,
            "total": total,
            "message": f"Processed {completed} documents, {failed} failed",
        }

    async def _process_document(self, doc_id: str, file_path: str, file_type: str):
        """Background task to process a document with GraphRAG extraction.

        CPU-intensive operations are run in a thread pool to avoid blocking
        the async event loop, keeping the API responsive during batch processing.

        Supports cancellation via cancellation flags - checked at key stages.
        """
        total_entities = 0
        total_relationships = 0
        loop = asyncio.get_event_loop()

        try:
            # Check for cancellation before starting
            self._check_cancellation(doc_id)

            # Update status to processing (run in executor to not block)
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_status,
                    doc_id,
                    ProcessingStatus.PROCESSING,
                    progress_message="Starting document processing...",
                ),
            )
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_progress,
                    doc_id,
                    0,
                    100,
                    "Converting document...",
                ),
            )

            # Yield control to allow other async tasks to run
            await asyncio.sleep(0)

            # Check for cancellation before conversion
            self._check_cancellation(doc_id)

            # Verify the file type is supported before launching subprocess
            if file_type.lower() not in self.DOCLING_EXTENSIONS:
                raise ValueError(f"Unsupported file type: {file_type}")

            use_vision = self.vision_analyzer.is_vision_model_available
            conversion_result = await _convert_document_subprocess(file_path, use_vision)

            md_text = conversion_result["markdown"]
            if not md_text:
                raise ValueError("No content extracted from file")

            filename = conversion_result.get("filename", Path(file_path).name)
            documents = [
                HaystackDocument(
                    content=md_text,
                    meta={"dl_meta": {"origin": {"filename": filename}}},
                )
            ]

            # Check for cancellation after conversion
            self._check_cancellation(doc_id)

            # =================================================================
            # Image Extraction and Analysis (runs in background)
            # =================================================================
            serialized_images = conversion_result.get("images", [])
            if serialized_images and use_vision:
                # Set initial image progress so frontend knows images were found
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_image_progress,
                        doc_id, 0, len(serialized_images),
                        f"Queued {len(serialized_images)} image{'s' if len(serialized_images) != 1 else ''} for analysis",
                    ),
                )
                asyncio.ensure_future(
                    self._analyze_images_background_from_serialized(
                        doc_id, serialized_images, use_vision
                    )
                )

            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_progress,
                    doc_id,
                    10,
                    100,
                    "Splitting into chunks...",
                ),
            )

            # Yield control
            await asyncio.sleep(0)

            # =================================================================
            # Clean image placeholders from markdown before chunking
            # =================================================================
            if use_vision:
                for doc in documents:
                    doc.content = _clean_image_placeholders(doc.content)

            # =================================================================
            # URL Protection: Prevent URLs from being split across chunks
            # =================================================================
            # Replace URLs with placeholders before splitting, then restore after
            url_maps = []  # Store URL map for each document
            protected_documents = []

            for doc in documents:
                protected_content, url_map = _protect_urls(doc.content)
                url_maps.append(url_map)
                # Create a new document with protected content
                protected_doc = HaystackDocument(
                    content=protected_content, meta=doc.meta
                )
                protected_documents.append(protected_doc)

                if url_map:
                    logger.debug(f"Protected {len(url_map)} URLs from chunking")

            # Split documents into chunks (run in thread pool)
            split_result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.splitter.run, documents=protected_documents),
            )
            chunks = split_result.get("documents", [])

            # Restore URLs in each chunk
            # Note: We need to restore URLs from all URL maps since chunks may
            # come from any of the original documents
            combined_url_map = {}
            for url_map in url_maps:
                combined_url_map.update(url_map)

            if combined_url_map:
                for chunk in chunks:
                    chunk.content = _restore_urls(chunk.content, combined_url_map)
                logger.debug(f"Restored URLs in {len(chunks)} chunks")

            # Check for cancellation after chunking
            self._check_cancellation(doc_id)

            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_progress,
                    doc_id,
                    15,
                    100,
                    f"Generating embeddings for {len(chunks)} chunks...",
                ),
            )

            # Yield control before heavy embedding operation
            await asyncio.sleep(0)

            # Check for cancellation before embedding
            self._check_cancellation(doc_id)

            # Generate embeddings (most CPU-intensive - run in thread pool)
            embed_result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.embedder.run, documents=chunks),
            )
            embedded_chunks = embed_result.get("documents", [])

            # Check for cancellation after embedding
            self._check_cancellation(doc_id)

            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_progress,
                    doc_id,
                    25,
                    100,
                    "Storing chunks in database...",
                ),
            )

            # Store chunks in Neo4j
            chunk_ids = []
            for idx, chunk in enumerate(embedded_chunks):
                # Check for cancellation BEFORE EVERY chunk storage
                self._check_cancellation(doc_id)

                chunk_id = f"{doc_id}_chunk_{idx}"
                chunk_ids.append(chunk_id)
                # Store chunk_id in meta so fuzzy entity linking can find the correct ID
                chunk.meta["chunk_id"] = chunk_id
                chunk_model = DocumentChunk(
                    id=chunk_id,
                    document_id=doc_id,
                    content=chunk.content,
                    embedding=chunk.embedding,
                    chunk_index=idx,
                    metadata=chunk.meta,
                )
                # Store chunk in thread pool
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(self.neo4j.store_chunk, chunk_model),
                )

                # Update progress for chunk storage (25-35%)
                storage_progress = 25 + int((idx + 1) / len(embedded_chunks) * 10)
                _store_interval = max(1, min(3, len(embedded_chunks) // 5))
                if (
                    idx == 0
                    or (idx + 1) % _store_interval == 0
                    or idx == len(embedded_chunks) - 1
                ):
                    remaining = len(embedded_chunks) - (idx + 1)
                    msg = f"Storing chunks: {idx + 1}/{len(embedded_chunks)} done"
                    if remaining > 0:
                        msg += f", {remaining} pending"
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.update_document_progress,
                            doc_id,
                            storage_progress,
                            100,
                            msg,
                        ),
                    )

                # Yield control every 3 chunks to keep API responsive
                if idx % 3 == 0:
                    await asyncio.sleep(0)

            logger.info(f"Document {doc_id}: stored {len(embedded_chunks)} chunks")

            # Check for cancellation before graph extraction
            self._check_cancellation(doc_id)

            # =================================================================
            # GraphRAG: Per-document entity extraction (Phase A)
            # Extracts entities from the full document, then fuzzy-links
            # them to individual chunks. Relationships are discovered
            # separately via Phase B (POST /api/graph/relationships/analyze).
            # =================================================================
            if (
                self.graph_extractor.is_available
                and self.settings.enable_graph_extraction
            ):
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_status,
                        doc_id,
                        ProcessingStatus.EXTRACTING,
                        progress_message="Extracting knowledge graph...",
                    ),
                )
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_progress,
                        doc_id,
                        35,
                        100,
                        "Generating document summary...",
                    ),
                )
                logger.info(f"Document {doc_id}: starting per-document entity extraction...")

                # Yield control before graph extraction
                await asyncio.sleep(0)

                # Generate document summary for context
                full_text = " ".join([c.content for c in embedded_chunks if c.content])[:5000]
                document_summary = (
                    await self.graph_extractor.generate_document_summary_async(
                        full_text
                    )
                )

                if document_summary:
                    logger.info(
                        f"Document {doc_id}: generated summary for extraction context"
                    )

                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_progress,
                        doc_id,
                        40,
                        100,
                        "Extracting entities from document...",
                    ),
                )

                # Check for cancellation before extraction
                self._check_cancellation(doc_id)

                # Per-document entity extraction (batched if needed)
                chunk_contents = [c.content for c in embedded_chunks if c.content]
                entities = await self.graph_extractor.extract_entities_from_document_async(
                    chunks=chunk_contents,
                    document_summary=document_summary or "",
                    max_tokens=self.settings.extraction_max_context,
                )

                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_progress,
                        doc_id,
                        70,
                        100,
                        f"Storing {len(entities)} entities...",
                    ),
                )

                # Store entities with provenance and fuzzy deduplication.
                # Uses embedding-based semantic dedup when available (catches
                # "Museum of Crypto Art" ↔ "MOCA"), falls back to Levenshtein.
                use_embedding_dedup = (
                    self.settings.enable_semantic_entity_resolution
                    and self.graph_extractor.async_extraction_client is not None
                )

                for entity in entities:
                    self._check_cancellation(doc_id)

                    if use_embedding_dedup:
                        try:
                            embedding = await self.graph_extractor.generate_entity_embedding_async(
                                entity.name, entity.type, entity.description
                            )
                        except Exception:
                            embedding = None

                        if embedding:
                            await loop.run_in_executor(
                                _get_processing_executor(),
                                functools.partial(
                                    self.neo4j.store_entity_with_embedding,
                                    entity,
                                    chunk_id=None,
                                    embedding=embedding,
                                ),
                            )
                        else:
                            # Fallback to Levenshtein if embedding failed
                            await loop.run_in_executor(
                                _get_processing_executor(),
                                functools.partial(
                                    self.neo4j.store_entity_with_resolution,
                                    entity,
                                    document_id=doc_id,
                                    similarity_threshold=0.85,
                                ),
                            )
                    else:
                        await loop.run_in_executor(
                            _get_processing_executor(),
                            functools.partial(
                                self.neo4j.store_entity_with_resolution,
                                entity,
                                document_id=doc_id,
                                similarity_threshold=0.85,
                            ),
                        )

                    total_entities += 1

                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_progress,
                        doc_id,
                        85,
                        100,
                        "Linking entities to chunks...",
                    ),
                )

                # Link entities to chunks via fuzzy string matching
                chunk_entity_links = self._match_entities_to_chunks(entities, embedded_chunks)
                for link_chunk_id, entity_name in chunk_entity_links:
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.link_entity_to_chunk,
                            entity_name,
                            link_chunk_id,
                        ),
                    )

                # Per-chunk relationship extraction (LLMGraphTransformer approach):
                # For each chunk with 2+ entities, extract relationships using
                # the chunk text as direct evidence.
                total_relationships = 0
                if self.settings.enable_graph_extraction and chunk_entity_links:
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.update_document_progress,
                            doc_id, 90, 100,
                            "Extracting per-chunk relationships...",
                        ),
                    )

                    # Build chunk_id → [entity_dicts] map
                    chunk_entities_map: dict[str, list] = {}
                    entity_map = {e.name.lower(): {"name": e.name, "type": e.type, "description": e.description} for e in entities}
                    for link_chunk_id, entity_name in chunk_entity_links:
                        ent = entity_map.get(entity_name.lower())
                        if ent:
                            chunk_entities_map.setdefault(link_chunk_id, []).append(ent)

                    # Extract relationships from chunks with 2+ entities
                    chunk_content_map = {}
                    for c in embedded_chunks:
                        cid = c.meta.get("chunk_id") or c.id if hasattr(c, "meta") else c.id
                        chunk_content_map[cid] = c.content or ""

                    # Deduplicate entities per chunk (same entity can match multiple times)
                    for cid in chunk_entities_map:
                        seen_names = set()
                        deduped = []
                        for ent in chunk_entities_map[cid]:
                            if ent["name"].lower() not in seen_names:
                                seen_names.add(ent["name"].lower())
                                deduped.append(ent)
                        chunk_entities_map[cid] = deduped

                    eligible_chunks = sum(1 for ents in chunk_entities_map.values() if len(ents) >= 2)
                    logger.info(
                        f"Document {doc_id}: per-chunk extraction — "
                        f"{len(chunk_entities_map)} chunks mapped, {eligible_chunks} with 2+ unique entities"
                    )

                    seen_rels: set[tuple] = set()
                    import asyncio as _asyncio
                    sem = _asyncio.Semaphore(self.settings.concurrent_extractions)

                    async def _extract_from_chunk(cid: str, ents: list):
                        async with sem:
                            text = chunk_content_map.get(cid, "")
                            return await self.graph_extractor.extract_chunk_relationships_async(text, ents)

                    # Gather all chunks with 2+ entities
                    tasks = []
                    for cid, ents in chunk_entities_map.items():
                        if len(ents) >= 2:
                            tasks.append(_extract_from_chunk(cid, ents))

                    if tasks:
                        results = await _asyncio.gather(*tasks, return_exceptions=True)
                        for result in results:
                            if isinstance(result, Exception):
                                continue
                            for rel in result:
                                key = (rel.source.lower(), rel.target.lower(), rel.relationship_type)
                                if key not in seen_rels:
                                    seen_rels.add(key)
                                    try:
                                        self.neo4j.store_relationship(
                                            rel,
                                            source_document_id=doc_id,
                                            extraction_method="per_chunk",
                                        )
                                        total_relationships += 1
                                    except Exception as e:
                                        logger.warning(f"Failed to store per-chunk relationship: {e}")

                    if total_relationships > 0:
                        logger.info(f"Document {doc_id}: {total_relationships} per-chunk relationships extracted")

                logger.info(
                    f"Document {doc_id}: entity extraction complete - "
                    f"{total_entities} entities, {len(chunk_entity_links)} chunk links, "
                    f"{total_relationships} per-chunk relationships"
                )

            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_progress,
                    doc_id,
                    100,
                    100,
                    "Processing complete!",
                ),
            )

            # Update document status
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_status,
                    doc_id,
                    ProcessingStatus.COMPLETED,
                    chunk_count=len(embedded_chunks),
                ),
            )

            logger.info(
                f"Document {doc_id} processed successfully: "
                f"{len(embedded_chunks)} chunks, {total_entities} entities, "
                f"{total_relationships} relationships"
            )

        except CancellationRequested as e:
            # Processing was cancelled (document being deleted)
            logger.info(f"Processing cancelled for document {doc_id}: {e}")
            # Don't update status here - the cancel method handles it
        except asyncio.CancelledError:
            # Task was cancelled externally
            logger.info(f"Processing task cancelled for document {doc_id}")
            # Don't update status here - the cancel method handles it
            raise  # Re-raise to properly cancel the task
        except Exception as e:
            logger.error(f"Error processing document {doc_id}: {e}")
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_status,
                    doc_id,
                    ProcessingStatus.FAILED,
                    error_message=str(e),
                ),
            )
        # NOTE: We no longer delete the file after processing.
        # Files are kept for reprocessing without needing re-upload.

    def _match_entities_to_chunks(
        self,
        entities,
        chunks,
    ) -> List[Tuple[str, str]]:
        """Fuzzy match entities to chunks, return (chunk_id, entity_name) pairs."""
        from rapidfuzz import fuzz

        links = []
        for chunk in chunks:
            chunk_content_lower = chunk.content.lower() if chunk.content else ""
            chunk_id = chunk.meta.get("chunk_id") or chunk.id if hasattr(chunk, "meta") else getattr(chunk, "id", None)
            if not chunk_id or not chunk_content_lower:
                continue
            for entity in entities:
                entity_name_lower = entity.name.lower()
                # Exact substring (fast path)
                if entity_name_lower in chunk_content_lower:
                    links.append((chunk_id, entity.name))
                    continue
                # Fuzzy match for variations
                if fuzz.partial_ratio(entity_name_lower, chunk_content_lower) >= 85:
                    links.append((chunk_id, entity.name))
        return links

    async def analyze_collection_relationships(
        self,
        collection_id: Optional[str] = None,
        scope: str = "full",
        progress_callback: Optional[Callable] = None,
        rebuild: bool = False,
    ) -> dict:
        """Run Phase B relationship analysis for a collection.

        Supports multi-round discovery: runs up to `relationship_max_rounds` rounds
        until the entity/relationship ratio reaches `relationship_target_ratio` or
        the time budget (`relationship_max_hours`) is exhausted.

        Uses co-occurrence-based batching, dynamic chunk context filling,
        and optionally the extraction model for faster/cheaper processing.

        Args:
            collection_id: Scope to a specific collection (None = global)
            scope: 'recent' = only entities from recent docs, 'full' = all entities
            progress_callback: For progress reporting

        Returns:
            Dict with relationship counts, ratio stats, and round info
        """
        import time

        # Fetch entities
        entities = self.neo4j.get_all_entities_for_collection(collection_id)

        if not entities:
            return {
                "relationships_discovered": 0,
                "relationships_stored": 0,
                "entities_analyzed": 0,
                "collection_id": collection_id,
                "entity_relationship_ratio": 0.0,
                "target_ratio": self.settings.relationship_target_ratio,
                "rounds_completed": 0,
            }

        entity_names = [e.get("name") for e in entities if e.get("name")]
        entity_count = len(entities)
        target_ratio = self.settings.relationship_target_ratio
        max_hours = self.settings.relationship_max_hours

        # Multi-round for initial analysis or rebuild (fresh start).
        # Re-analyze (incremental, relationships already exist) always does 1 round.
        existing_rel_count = self.neo4j.get_relationship_count()
        if existing_rel_count == 0 or rebuild:
            max_rounds = max(1, self.settings.relationship_max_rounds)
            if rebuild and existing_rel_count > 0:
                logger.info(
                    f"Rebuild mode: {existing_rel_count} existing relationships, "
                    f"running {max_rounds} round(s) as fresh analysis"
                )
        else:
            max_rounds = 1
            logger.info(
                f"Re-analyze mode: {existing_rel_count} existing relationships, running 1 round"
            )

        # Build co-occurrence map for smart batching
        if progress_callback:
            progress_callback(0, 1, f"Building co-occurrence map for {entity_count} entities...")
        entity_co_occurrence = await asyncio.to_thread(
            self.neo4j.get_entity_co_occurrence, entity_names
        )
        co_occurrence_entities = sum(1 for v in entity_co_occurrence.values() if v)
        logger.info(
            f"Co-occurrence map: {co_occurrence_entities}/{entity_count} entities have chunk mentions"
        )

        # Track cumulative stats across all rounds
        total_discovered = 0
        total_stored = 0
        rounds_completed = 0
        analysis_start = time.monotonic()
        max_per_entity = self.settings.relationship_max_per_entity

        # Cumulative progress tracking across all rounds.
        cumulative_batches_done = 0
        cumulative_total_batches = 0
        batches_per_round = 0  # Set after first round's batching is computed

        for round_num in range(1, max_rounds + 1):
            # Check time budget
            if max_hours > 0:
                elapsed_hours = (time.monotonic() - analysis_start) / 3600
                if elapsed_hours >= max_hours:
                    logger.info(
                        f"Time budget exhausted ({elapsed_hours:.1f}h >= {max_hours}h), "
                        f"stopping after {rounds_completed} rounds"
                    )
                    break

            # Check current ratio (after round 1+)
            if round_num > 1:
                current_rel_count = self.neo4j.get_relationship_count()
                current_ratio = current_rel_count / entity_count if entity_count > 0 else 0
                if current_ratio >= target_ratio:
                    logger.info(
                        f"Target ratio reached ({current_ratio:.2f} >= {target_ratio}), "
                        f"stopping after {rounds_completed} rounds"
                    )
                    break

            # round_prefix used only in backend logs, not shown in UI progress
            round_prefix = f"[Round {round_num}/{max_rounds}] " if max_rounds > 1 else ""

            # Fetch existing relationships (refreshed each round).
            # Cap per entity to prevent hub entities from dominating the LLM context.
            llm_cap = min(20, max_per_entity) if max_per_entity > 0 else 0
            existing_relationships = self.neo4j.get_existing_relationships_for_entities(
                entity_names, max_per_entity=llm_cap,
            )

            # Fetch current degree map for per-entity storage cap
            if max_per_entity > 0:
                entity_degrees = await asyncio.to_thread(
                    self.neo4j.get_entity_degree_map, entity_names
                )
            else:
                entity_degrees = {}

            if progress_callback:
                progress_callback(
                    cumulative_batches_done, max(1, cumulative_total_batches),
                    f"Analyzing {entity_count} entities..."
                )

            # Per-round storage stats (batches_done/total_batches are round-local,
            # but we accumulate into cumulative counters for progress display)
            storage_stats = {"stored": 0, "discovered": 0, "batches_done": 0, "total_batches": 0}
            _round_start = time.monotonic()

            async def store_batch_relationships(batch_rels: list):
                """Callback to store relationships incrementally as each batch completes."""
                nonlocal storage_stats, total_stored, total_discovered
                nonlocal cumulative_batches_done, cumulative_total_batches, batches_per_round
                storage_stats["discovered"] += len(batch_rels)
                storage_stats["batches_done"] += 1
                cumulative_batches_done += 1
                total_discovered += len(batch_rels)

                # On first batch of first round, learn the per-round batch count
                # and estimate total across all rounds
                if batches_per_round == 0 and storage_stats["total_batches"] > 0:
                    batches_per_round = storage_stats["total_batches"]
                    cumulative_total_batches = batches_per_round * max_rounds

                for rel in batch_rels:
                    # Skip low-confidence relationships
                    if hasattr(rel, 'confidence') and rel.confidence < 0.5:
                        continue
                    # Per-entity degree cap: skip if BOTH endpoints are saturated
                    if max_per_entity > 0:
                        src_deg = entity_degrees.get(rel.source, 0)
                        tgt_deg = entity_degrees.get(rel.target, 0)
                        if src_deg >= max_per_entity and tgt_deg >= max_per_entity:
                            continue
                    try:
                        if self.neo4j.store_relationship(
                            rel,
                            extraction_method="cross_collection",
                        ):
                            storage_stats["stored"] += 1
                            total_stored += 1
                            # Update local degree tracking
                            entity_degrees[rel.source] = entity_degrees.get(rel.source, 0) + 1
                            entity_degrees[rel.target] = entity_degrees.get(rel.target, 0) + 1
                    except Exception as e:
                        logger.warning(f"Failed to store relationship {rel.source} -> {rel.target}: {e}")

                # Update progress with ETA across all rounds
                if progress_callback:
                    display_total = cumulative_total_batches
                    if display_total <= 0:
                        display_total = max(1, cumulative_batches_done + 1)

                    elapsed = time.monotonic() - analysis_start
                    avg_per_batch = elapsed / cumulative_batches_done
                    remaining = display_total - cumulative_batches_done
                    eta_seconds = int(avg_per_batch * remaining)

                    if eta_seconds > 60:
                        eta_str = f"~{eta_seconds // 60}m remaining"
                    elif eta_seconds > 0:
                        eta_str = f"~{eta_seconds}s remaining"
                    else:
                        eta_str = "almost done"

                    progress_callback(
                        cumulative_batches_done,
                        display_total,
                        f"Batch {cumulative_batches_done}/{display_total}, "
                        f"{eta_str}"
                    )

            # Callback to fetch relevant source text with dynamic token budget
            async def get_batch_context(entity_batch: list, token_budget: int = 0) -> str:
                """Fetch relevant chunk text for the current entity batch."""
                batch_names = [e.get("name") for e in entity_batch if e.get("name")]
                return await asyncio.to_thread(
                    self.neo4j.get_chunk_context_for_entities,
                    batch_names,
                    token_budget=token_budget,
                )

            # Run two-phase batched relationship analysis
            # Phase 1 uses extraction model context (larger), Phase 2 uses main model context
            relationships = await self.graph_extractor.analyze_relationships_batched_async(
                all_entities=entities,
                context="",
                max_context_tokens=self.settings.relationship_max_context,
                max_output_tokens=self.settings.relationship_max_output_tokens,
                existing_relationships=existing_relationships,
                on_batch_complete=store_batch_relationships,
                get_batch_context=get_batch_context,
                progress_stats=storage_stats,
                parallel_batches=self.settings.parallel_relationship_batches or self.settings.concurrent_extractions,
                entity_co_occurrence=entity_co_occurrence,
                extraction_max_context=self.settings.extraction_max_context,
            )

            rounds_completed += 1
            round_elapsed = time.monotonic() - _round_start
            logger.info(
                f"{round_prefix}Complete: {len(relationships)} discovered, "
                f"{storage_stats['stored']} stored in {round_elapsed:.1f}s"
            )

        # Calculate final ratio
        final_rel_count = self.neo4j.get_relationship_count()
        final_ratio = final_rel_count / entity_count if entity_count > 0 else 0

        if progress_callback:
            ratio_str = f"ratio {final_ratio:.1f}/{target_ratio}"
            progress_callback(
                entity_count, entity_count,
                f"Relationship analysis complete — {total_stored} stored, "
                f"{rounds_completed} round(s), {ratio_str}"
            )

        result = {
            "relationships_discovered": total_discovered,
            "relationships_stored": total_stored,
            "entities_analyzed": entity_count,
            "collection_id": collection_id,
            "entity_relationship_ratio": round(final_ratio, 2),
            "target_ratio": target_ratio,
            "rounds_completed": rounds_completed,
            "total_relationships": final_rel_count,
        }
        logger.info(f"Relationship analysis complete: {result}")
        return result

    async def _analyze_images_background_from_serialized(
        self,
        doc_id: str,
        serialized_images: list,
        force_vision_model: bool,
    ):
        """Analyze images received from the subprocess converter.

        Images arrive as dicts with base64-encoded PNG data. We reconstruct
        ExtractedImage objects and feed them through the vision analyzer.
        Images are processed concurrently, gated by the global vision semaphore.
        """
        import base64
        import io

        from app.services.vision_analyzer import ExtractedImage, _get_vision_semaphore
        from PIL import Image

        loop = asyncio.get_event_loop()
        img_executor = _get_image_executor()
        total = len(serialized_images)
        semaphore = _get_vision_semaphore()

        # Thread-safe progress tracking for concurrent image tasks
        progress = {"stored": 0, "processed": 0}
        progress_lock = asyncio.Lock()

        logger.info(f"Document {doc_id}: starting background analysis of {total} images")

        # Set initial image progress
        try:
            await loop.run_in_executor(
                img_executor,
                functools.partial(
                    self.neo4j.update_image_progress,
                    doc_id, 0, total,
                    f"Analyzing {total} image{'s' if total != 1 else ''}...",
                ),
            )
        except Exception:
            pass

        async def process_single_image(idx: int, img_data: dict):
            """Process a single image: vision -> embed -> store -> graph extract -> progress."""
            try:
                pil_image = Image.open(
                    io.BytesIO(base64.b64decode(img_data["base64_png"]))
                )
                extracted = ExtractedImage(
                    image_id=img_data.get("image_id", f"image_{idx}"),
                    pil_image=pil_image,
                    page_number=img_data.get("page_number"),
                    bbox=img_data.get("bbox"),
                    caption=img_data.get("caption"),
                    existing_description=img_data.get("existing_description"),
                )

                # Vision API call gated by global semaphore
                async with semaphore:
                    logger.info(
                        f"Document {doc_id}: analyzing image {idx + 1}/{total}: "
                        f"{extracted.image_id} (page {extracted.page_number})"
                    )
                    analysis = await loop.run_in_executor(
                        img_executor,
                        functools.partial(
                            self.vision_analyzer.analyze_image_sync,
                            extracted,
                            force_vision_model,
                        ),
                    )

                # Post-vision processing (fast, no semaphore needed)
                image_chunk_id = f"{doc_id}_image_{idx}"
                if analysis.analysis_method == "vision_model":
                    image_content = f"[Image Analysis (Vision Model)]\n{analysis.description}"
                elif analysis.analysis_method == "docling":
                    image_content = f"[Image Description]\n{analysis.description}"
                else:
                    image_content = f"[Image {idx + 1}]\n{analysis.description}"

                image_chunk = DocumentChunk(
                    id=image_chunk_id,
                    document_id=doc_id,
                    content=image_content,
                    embedding=None,
                    chunk_index=1000 + idx,
                    metadata={
                        "type": "image_analysis",
                        "image_id": analysis.image_id,
                        "analysis_method": analysis.analysis_method,
                    },
                )

                embed_result = await loop.run_in_executor(
                    img_executor,
                    functools.partial(
                        self.embedder.run,
                        documents=[
                            HaystackDocument(
                                content=image_content,
                                meta=image_chunk.metadata,
                            )
                        ],
                    ),
                )
                embedded_docs = embed_result.get("documents", [])
                if embedded_docs:
                    image_chunk.embedding = embedded_docs[0].embedding

                await loop.run_in_executor(
                    img_executor,
                    functools.partial(self.neo4j.store_chunk, image_chunk),
                )

                logger.info(
                    f"Document {doc_id}: stored image {idx + 1}/{total} "
                    f"(method={analysis.analysis_method}, len={len(analysis.description)})"
                )

                # Graph extraction for image content
                if (
                    self.graph_extractor.is_available
                    and self.settings.enable_graph_extraction
                    and image_content
                ):
                    try:
                        extraction = await self.graph_extractor.extract_from_text_async(
                            image_content
                        )
                        if extraction and (extraction.entities or extraction.relationships):
                            await loop.run_in_executor(
                                img_executor,
                                functools.partial(
                                    self.neo4j.store_graph_extraction,
                                    image_chunk_id,
                                    extraction,
                                ),
                            )
                            logger.info(
                                f"Document {doc_id}: image {idx + 1}/{total} "
                                f"extracted {len(extraction.entities)} entities, "
                                f"{len(extraction.relationships)} relationships"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Document {doc_id}: graph extraction failed for "
                            f"image {idx + 1}: {e}"
                        )

                # Update progress atomically
                async with progress_lock:
                    progress["stored"] += 1
                    progress["processed"] += 1
                    current_stored = progress["stored"]

                try:
                    await loop.run_in_executor(
                        img_executor,
                        functools.partial(
                            self.neo4j.update_image_progress,
                            doc_id, current_stored, total,
                            f"Analyzed {current_stored}/{total} image{'s' if total != 1 else ''}",
                        ),
                    )
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"Document {doc_id}: failed to process image {idx + 1}/{total}: {e}")
                async with progress_lock:
                    progress["processed"] += 1
                    current_processed = progress["processed"]
                try:
                    await loop.run_in_executor(
                        img_executor,
                        functools.partial(
                            self.neo4j.update_image_progress,
                            doc_id, current_processed, total,
                            f"Analyzed {current_processed}/{total} images ({progress['stored']} stored)",
                        ),
                    )
                except Exception:
                    pass

        # Launch all image tasks concurrently -- semaphore limits actual parallelism
        tasks = [
            process_single_image(idx, img_data)
            for idx, img_data in enumerate(serialized_images)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Final image progress update + refresh chunk count to include image chunks
        stored = progress["stored"]
        final_msg = f"Complete - {stored}/{total} image{'s' if total != 1 else ''} analyzed"
        try:
            await loop.run_in_executor(
                img_executor,
                functools.partial(
                    self.neo4j.update_image_progress,
                    doc_id, total, total, final_msg,
                ),
            )
            if stored > 0:
                await loop.run_in_executor(
                    img_executor,
                    functools.partial(self.neo4j.refresh_chunk_count, doc_id),
                )
        except Exception:
            pass

        logger.info(f"Document {doc_id}: background image analysis complete ({stored}/{total} stored)")


class QueryProcessor:
    """Process queries for semantic search and GraphRAG enhancements."""

    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        self._reranker = None  # Lazy load cross-encoder

        # Initialize text embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.embed_api_key:
            from haystack.components.embedders import OpenAITextEmbedder
            from haystack.utils import Secret

            text_embedder_kwargs = dict(
                api_key=Secret.from_token(self.settings.embed_api_key),
                api_base_url=self.settings.embed_api_base,
                model=self.settings.embedding_model,
            )
            if self.settings.embedding_send_dimensions:
                text_embedder_kwargs["dimensions"] = self.settings.embedding_dimension
            self.text_embedder = OpenAITextEmbedder(**text_embedder_kwargs)
            logger.info(
                f"Using OpenAI text embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})"
            )
        else:
            from haystack.components.embedders import SentenceTransformersTextEmbedder

            self.text_embedder = SentenceTransformersTextEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.text_embedder.warm_up()
            logger.info("Using SentenceTransformers text embeddings")

        logger.info(
            "Query processor initialized (GraphRAG + Reranking + Agentic RAG enabled)"
        )

    @property
    def reranker(self):
        """Lazy load cross-encoder for re-ranking."""
        if self._reranker is None and self.settings.enable_reranking:
            try:
                from sentence_transformers import CrossEncoder

                self._reranker = CrossEncoder(self.settings.reranking_model)
                logger.info(f"Loaded cross-encoder: {self.settings.reranking_model}")
            except Exception as e:
                logger.warning(
                    f"Failed to load cross-encoder, disabling reranking: {e}"
                )
                self._reranker = False  # Mark as unavailable
        return self._reranker if self._reranker else None

    def rerank_results(
        self, query: str, results: List[dict], top_k: int = 5
    ) -> List[dict]:
        """
        Re-rank results using cross-encoder for better precision.

        Cross-encoders score query-document pairs directly,
        providing more accurate relevance scores than bi-encoders.
        """
        if not results or not self.reranker:
            return results[:top_k]

        try:
            # Create query-content pairs
            pairs = [(query, r.get("content", "")) for r in results]

            # Score with cross-encoder
            scores = self.reranker.predict(pairs)

            # Add rerank scores to results
            for i, score in enumerate(scores):
                results[i]["rerank_score"] = float(score)

            # Sort by rerank score
            reranked = sorted(
                results, key=lambda x: x.get("rerank_score", 0), reverse=True
            )

            logger.debug(f"Reranked {len(results)} results")
            return reranked[:top_k]

        except Exception as e:
            logger.warning(f"Reranking failed: {e}")
            return results[:top_k]

    async def rerank_results_async(
        self, query: str, results: List[dict], top_k: int = 5
    ) -> List[dict]:
        """Async version of rerank_results."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _rerank_executor, lambda: self.rerank_results(query, results, top_k)
        )

    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a query."""
        result = self.text_embedder.run(text=query)
        return result["embedding"]

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
        collection_id: Optional[str] = None,
    ) -> list[dict]:
        """Perform semantic search, optionally scoped to a collection."""
        # Generate query embedding
        query_embedding = self.embed_query(query)

        # Search in Neo4j
        results = self.neo4j.vector_search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
            collection_id=collection_id,
        )

        return results

    def hybrid_search(
        self,
        query: str,
        top_k: int = 10,
        vector_weight: float = 0.5,
        keyword_weight: float = 0.3,
        metadata_weight: float = 0.2,
    ) -> list[dict]:
        """
        Perform hybrid search combining:
        - Vector similarity (semantic search)
        - Full-text keyword search (content matching)
        - Metadata search (filename, topic hint for custom inputs)

        Uses Reciprocal Rank Fusion (RRF) to merge results.
        """
        # Generate query embedding
        query_embedding = self.embed_query(query)

        # Use simple hybrid search in Neo4j
        results = self.neo4j.simple_hybrid_search(
            query_embedding=query_embedding,
            query_text=query,
            top_k=top_k,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            metadata_weight=metadata_weight,
        )

        return results

    async def graph_search_async(
        self,
        query: str,
        top_k: int = 5,
        max_hops: int = 2,
        use_hybrid_rrf: bool = True,
        collection_id: Optional[str] = None,
    ) -> dict:
        """
        Perform hybrid search combining vector similarity, keyword search, and graph traversal.
        Uses Reciprocal Rank Fusion (RRF) for better results.
        Optionally scoped to a specific collection.

        Returns:
            Dict with 'results', 'graph_context', and search metadata
        """
        # Generate query embedding
        query_embedding = self.embed_query(query)

        # Extract entities from the query (async to not block event loop)
        query_entities = []
        if self.graph_extractor.is_available:
            query_entities = (
                await self.graph_extractor.extract_entities_from_query_async(query)
            )

        # Use hybrid search with RRF if enabled
        if use_hybrid_rrf and self.settings.enable_hybrid_search:
            hybrid_result = self.neo4j.hybrid_search_rrf(
                query_embedding=query_embedding,
                query_text=query,
                entity_names=query_entities,
                top_k=top_k,
                max_hops=max_hops,
                vector_weight=self.settings.vector_weight,
                keyword_weight=self.settings.keyword_weight,
                graph_weight=self.settings.graph_weight,
                collection_id=collection_id,
            )
            return {
                "results": hybrid_result["results"],
                "graph_context": hybrid_result["graph_context"],
                "search_method": "hybrid_rrf",
                "vector_count": hybrid_result.get("vector_count", 0),
                "keyword_count": hybrid_result.get("keyword_count", 0),
                "graph_chunk_count": hybrid_result.get("graph_chunk_count", 0),
            }
        else:
            # Legacy hybrid search
            result = self.neo4j.hybrid_search(
                query_embedding=query_embedding,
                entity_names=query_entities,
                top_k=top_k,
                max_hops=max_hops,
            )
            return {
                "results": result["vector_results"],
                "graph_context": result["graph_context"],
                "search_method": "vector_graph",
            }

    async def rag_query(
        self,
        question: str,
        top_k: int = 5,
        use_graph: bool = True,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None,
        use_reranking: bool = True,
        use_agentic: bool = False,
        collection_id: Optional[str] = None,
    ) -> dict:
        """
        Answer a question using enhanced GraphRAG features.
        Optionally scoped to a specific collection.

        Features:
        - Hybrid search with RRF (vector + keyword + graph)
        - Cross-encoder re-ranking for precision
        - Conversation memory for context
        - Agentic multi-step reasoning for complex questions
        - Enhanced prompts for better answers
        """

        # If agentic mode is requested, use multi-step reasoning
        if use_agentic and self.settings.enable_agentic_rag:
            return await self._agentic_rag_query(
                question=question,
                top_k=top_k,
                max_hops=max_hops,
                conversation_history=conversation_history,
                collection_id=collection_id,
            )

        graph_context = None
        search_metadata = {}

        if use_graph and self.graph_extractor.is_available:
            # Use hybrid search with RRF
            search_result = await self.graph_search_async(
                question,
                top_k=top_k * 2,  # Get more for reranking
                max_hops=max_hops,
                use_hybrid_rrf=self.settings.enable_hybrid_search,
                collection_id=collection_id,
            )
            results = search_result["results"]
            graph_data = search_result["graph_context"]
            search_metadata = {
                "search_method": search_result.get("search_method", "unknown"),
                "vector_count": search_result.get("vector_count", 0),
                "keyword_count": search_result.get("keyword_count", 0),
                "graph_chunk_count": search_result.get("graph_chunk_count", 0),
            }

            # Build graph context object
            if graph_data["entities"] or graph_data["relationships"]:
                graph_context = GraphContext(
                    entities=graph_data["entities"],
                    relationships=graph_data["relationships"],
                    chunks=graph_data["chunks"],
                )
        else:
            # Fall back to vector-only search
            results = self.search(
                question, top_k=top_k * 2, collection_id=collection_id
            )
            search_metadata = {"search_method": "vector_only"}

        # Apply re-ranking if enabled
        reranked = False
        if use_reranking and self.settings.enable_reranking and results:
            results = await self.rerank_results_async(question, results, top_k)
            reranked = True
        else:
            results = results[:top_k]

        if not results and (not graph_context or not graph_context.entities):
            return {
                "question": question,
                "answer": "I couldn't find any relevant information in the knowledge base.",
                "sources": [],
                "graph_context": None,
                "reranked": False,
                "reasoning_steps": None,
            }

        # Build context from graph (entities and relationships)
        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join(
                [
                    f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                    for e in graph_context.entities[:10]
                ]
            )
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"

        if graph_context and graph_context.relationships:
            rel_info = "\n".join(
                [
                    f"- {r['source']} --[{r['type']}]--> {r['target']}"
                    for r in graph_context.relationships[:15]
                ]
            )
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"

        # Check if OpenAI is configured
        if not self.settings.openai_api_key:
            full_context = (
                "\n\n".join(
                    [f"[Source: {r['filename']}]\n{r['content']}" for r in results]
                )
                + graph_context_str
            )
            return {
                "question": question,
                "answer": f"Here is the relevant information:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None,
            }

        # Generate answer with enhanced prompts
        try:
            from openai import OpenAI

            # Use turbo mode config if active, otherwise default settings
            llm_config = get_llm_config()
            client = OpenAI(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
            )

            # Enhanced system prompt with anti-injection protection
            system_prompt = """You are an expert research assistant providing accurate, helpful answers.

Guidelines:
1. Synthesize information into a coherent, natural-sounding answer
2. Cite sources inline using [src_1], [src_2] notation when referencing specific information
3. Structure longer answers with clear sections when appropriate
4. Be precise and factual - avoid speculation beyond what you know
5. If you cannot fully answer the question, explain what aspects you can address

Response Style:
- Write naturally as if you're an expert directly answering the question
- Never mention "context", "documents provided", "knowledge base", or similar phrases
- Prefer specific facts over vague generalizations
- Connect related concepts naturally
- If sources conflict, acknowledge the discrepancy objectively""" + get_anti_injection_instruction(
                enabled=self.settings.prompt_security
            )

            # Format sources with reference IDs
            formatted_sources = ""
            if results:
                for idx, r in enumerate(results):
                    ref_id = f"src_{idx + 1}"
                    rerank_info = (
                        f" (relevance: {r.get('rerank_score', r.get('score', 0)):.3f})"
                        if reranked
                        else ""
                    )
                    formatted_sources += f"\n[{ref_id}] Source: {r['filename']}{rerank_info}\n{r['content']}\n"

            # Build the prompt
            prompt = f"""Answer the following question. Use reference IDs like [src_1], [src_2] to cite specific information.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}

### Question:
{question}

### Answer:"""

            # Build messages with conversation history
            messages = [{"role": "system", "content": system_prompt}]

            # Add conversation history for context
            if conversation_history:
                max_history = self.settings.max_conversation_history
                for msg in conversation_history[-max_history:]:
                    messages.append({"role": msg.role, "content": msg.content})

            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model=llm_config.model,
                messages=messages,
                temperature=0.3,
                max_tokens=1200,  # Increased for more complete answers
            )

            answer = response.choices[0].message.content

            return {
                "question": question,
                "answer": answer,
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None,
                **search_metadata,
            }

        except Exception as e:
            logger.error(f"Error in GraphRAG query: {e}")
            full_context = (
                "\n\n".join(
                    [f"[Source: {r['filename']}]\n{r['content']}" for r in results]
                )
                + graph_context_str
            )
            return {
                "question": question,
                "answer": f"Error generating answer: {str(e)}. Here is the relevant context:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None,
            }

    async def _agentic_rag_query(
        self,
        question: str,
        top_k: int = 5,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None,
        thinking_callback: Optional[Callable[[ThinkingEvent], None]] = None,
    ) -> dict:
        """
        Agentic multi-step RAG for complex questions with extended thinking.

        Deep Research with visible reasoning:
        1. Break down complex questions into sub-questions
        2. Iteratively retrieve information with community context
        3. Synthesize and identify gaps
        4. Generate comprehensive answer

        Args:
            question: The user's question
            top_k: Number of results per search
            max_hops: Graph traversal depth
            conversation_history: Previous conversation messages
            collection_id: Optional collection scope
            thinking_callback: Optional callback for streaming thinking events
        """
        import re

        from openai import OpenAI

        def emit_thinking(event_type: str, content: str, metadata: dict = None):
            """Helper to emit thinking events."""
            if thinking_callback:
                thinking_callback(
                    ThinkingEvent(
                        event_type=event_type, content=content, metadata=metadata
                    )
                )

        if not self.settings.openai_api_key:
            return await self.rag_query(
                question=question,
                top_k=top_k,
                use_graph=True,
                max_hops=max_hops,
                conversation_history=conversation_history,
                use_agentic=False,
                collection_id=collection_id,
            )

        # Use turbo mode config if active, otherwise default settings
        llm_config = get_llm_config()
        client = OpenAI(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )

        # Extended thinking: detailed reasoning steps
        reasoning_steps: List[ReasoningStep] = []
        all_results = []
        all_graph_contexts = []
        communities_used = set()
        step_number = 0

        # =====================================================================
        # Step 1: Analyze question complexity and decompose
        # =====================================================================
        step_number += 1
        emit_thinking("thinking", "Analyzing question complexity...")
        reasoning_steps.append(
            ReasoningStep(
                step_number=step_number,
                action="decompose",
                description="Analyzing question complexity and identifying sub-questions",
            )
        )

        decompose_response = client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {
                    "role": "system",
                    "content": """You help break down complex questions into simpler sub-questions.
Output a JSON array of sub-questions that together would answer the main question.
If the question is simple, just return a single-element array with the original question.
Maximum 3 sub-questions. Format: {"sub_questions": ["q1", "q2", ...]}""",
                },
                {"role": "user", "content": f"Break down this question: {question}"},
            ],
            temperature=0.2,
            max_tokens=300,
        )

        try:
            decompose_text = decompose_response.choices[0].message.content
            json_match = re.search(
                r'\{[^{}]*"sub_questions"[^{}]*\}', decompose_text, re.DOTALL
            )
            if json_match:
                sub_questions = json.loads(json_match.group())["sub_questions"]
            else:
                sub_questions = [question]
        except Exception as e:
            logger.warning(f"Failed to decompose question: {e}")
            sub_questions = [question]

        emit_thinking(
            "thinking",
            f"Identified {len(sub_questions)} research areas: {sub_questions}",
        )
        reasoning_steps.append(
            ReasoningStep(
                step_number=step_number,
                action="decompose",
                description=f"Identified {len(sub_questions)} research areas",
                details={"sub_questions": sub_questions},
            )
        )

        # =====================================================================
        # Step 2: Search relevant communities for context
        # =====================================================================
        if self.settings.enable_community_detection:
            step_number += 1
            emit_thinking(
                "thinking",
                "Searching knowledge graph communities for relevant context...",
            )

            relevant_communities = self.neo4j.search_communities_by_content(
                question, limit=3
            )
            if relevant_communities:
                communities_used.update(c["id"] for c in relevant_communities)
                community_context = "\n".join(
                    [
                        f"- {c.get('name') or 'Community ' + str(c['id'])}: {c.get('summary', '')[:200]}"
                        for c in relevant_communities
                    ]
                )
                emit_thinking(
                    "retrieval",
                    f"Found {len(relevant_communities)} relevant communities",
                )
                reasoning_steps.append(
                    ReasoningStep(
                        step_number=step_number,
                        action="community_search",
                        description=f"Found {len(relevant_communities)} relevant entity communities",
                        details={
                            "communities": [c.get("name") for c in relevant_communities]
                        },
                    )
                )

        # =====================================================================
        # Step 3: Research each sub-question
        # =====================================================================
        for i, sub_q in enumerate(sub_questions[: self.settings.max_agentic_steps]):
            step_number += 1
            emit_thinking("search", f"Researching: {sub_q}")
            reasoning_steps.append(
                ReasoningStep(
                    step_number=step_number,
                    action="search",
                    description=f"Searching for: {sub_q[:100]}",
                )
            )

            search_result = await self.graph_search_async(
                sub_q,
                top_k=top_k,
                max_hops=max_hops,
                use_hybrid_rrf=True,
                collection_id=collection_id,
            )

            # Re-rank results
            if self.settings.enable_reranking and search_result["results"]:
                reranked_results = await self.rerank_results_async(
                    sub_q, search_result["results"], top_k
                )
                all_results.extend(reranked_results)
            else:
                all_results.extend(search_result["results"][:top_k])

            if search_result["graph_context"]:
                all_graph_contexts.append(search_result["graph_context"])

        # =====================================================================
        # Step 4: Deduplicate and rank results
        # =====================================================================
        step_number += 1
        emit_thinking("thinking", "Deduplicating and ranking sources...")

        seen_chunks = set()
        unique_results = []
        for r in all_results:
            chunk_id = r.get("chunk_id", "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                unique_results.append(r)

        unique_results.sort(
            key=lambda x: x.get("rerank_score", x.get("score", 0)), reverse=True
        )
        final_results = unique_results[: top_k * 2]

        reasoning_steps.append(
            ReasoningStep(
                step_number=step_number,
                action="rerank",
                description=f"Gathered and ranked {len(final_results)} unique sources from {len(all_results)} total",
                details={
                    "total_found": len(all_results),
                    "after_dedup": len(final_results),
                },
            )
        )
        emit_thinking("retrieval", f"Gathered {len(final_results)} unique sources")

        # =====================================================================
        # Step 5: Merge graph contexts with community awareness
        # =====================================================================
        merged_entities = {}
        merged_relationships = []
        merged_communities = []

        for gc in all_graph_contexts:
            for entity in gc.get("entities", []):
                name = entity.get("name", "")
                if name and name not in merged_entities:
                    merged_entities[name] = entity
                    # Track community
                    if entity.get("community_id"):
                        communities_used.add(entity["community_id"])
            for rel in gc.get("relationships", []):
                merged_relationships.append(rel)

        # Add community summaries if available
        if communities_used and self.settings.enable_graph_summarization:
            for com_id in list(communities_used)[:5]:
                community = self.neo4j.get_community(com_id)
                if community and community.get("summary"):
                    merged_communities.append(
                        {
                            "id": com_id,
                            "name": community.get("name"),
                            "summary": community.get("summary"),
                        }
                    )

        graph_context = (
            GraphContext(
                entities=list(merged_entities.values())[:15],
                relationships=merged_relationships[:20],
                chunks=[],
                communities=merged_communities,
            )
            if merged_entities
            else None
        )

        # =====================================================================
        # Step 6: Generate comprehensive answer
        # =====================================================================
        step_number += 1
        emit_thinking("synthesis", "Synthesizing comprehensive answer...")
        reasoning_steps.append(
            ReasoningStep(
                step_number=step_number,
                action="synthesize",
                description="Synthesizing comprehensive answer from gathered context",
            )
        )

        # Build context
        formatted_sources = ""
        for idx, r in enumerate(final_results):
            ref_id = f"src_{idx + 1}"
            formatted_sources += (
                f"\n[{ref_id}] Source: {r['filename']}\n{r['content']}\n"
            )

        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join(
                [
                    f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                    for e in graph_context.entities
                ]
            )
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"

        if graph_context and graph_context.relationships:
            rel_info = "\n".join(
                [
                    f"- {r['source']} --[{r['type']}]--> {r['target']}"
                    for r in graph_context.relationships
                ]
            )
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"

        # Add community context
        if graph_context and graph_context.communities:
            community_info = "\n".join(
                [
                    f"- {c.get('name') or 'Community ' + str(c.get('id', ''))}: {c.get('summary', '')}"
                    for c in graph_context.communities
                ]
            )
            graph_context_str += (
                f"\n\n=== Relevant Knowledge Communities ===\n{community_info}"
            )

        # Enhanced system prompt with community awareness and anti-injection protection
        system_prompt = """You are an expert research assistant that provides comprehensive, well-structured answers.

Guidelines:
1. Provide a comprehensive answer that addresses all aspects of the question
2. Organize complex answers with clear structure (sections, bullet points)
3. Cite sources using reference IDs: [src_1], [src_2], etc.
4. Highlight key findings and insights
5. Note any limitations if you cannot fully address the question
6. Connect related concepts naturally and coherently
7. Be precise and factual in your statements

Response Style:
- Write naturally as if you're an expert directly answering the question
- Never mention "context", "provided documents", "knowledge graph", or similar phrases
- Never say things like "Based on the provided context" or "According to the documents"
- Present information confidently as expert knowledge""" + get_anti_injection_instruction(
            enabled=self.settings.prompt_security
        )

        # Build messages with conversation history
        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            max_history = self.settings.max_conversation_history
            for msg in conversation_history[-max_history:]:
                messages.append({"role": msg.role, "content": msg.content})

        prompt = f"""Provide a detailed answer to this question.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}

### Question:
{question}

### Answer:"""

        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=llm_config.model, messages=messages, temperature=0.3, max_tokens=2000
        )

        answer = response.choices[0].message.content

        # Final thinking event
        emit_thinking("done", "Answer generated successfully")
        reasoning_steps.append(
            ReasoningStep(
                step_number=step_number + 1,
                action="complete",
                description="Answer generated successfully",
            )
        )

        # Convert reasoning steps to strings for backward compatibility
        reasoning_step_strings = [
            f"[{s.action}] {s.description}" for s in reasoning_steps
        ]

        return {
            "question": question,
            "answer": answer,
            "sources": final_results,
            "graph_context": graph_context.model_dump() if graph_context else None,
            "reranked": True,
            "reasoning_steps": reasoning_step_strings,
            "search_method": "agentic_rag",
            "sub_questions": sub_questions,
            "communities_used": list(communities_used),
            "retrieval_stats": {
                "total_sources_considered": len(all_results),
                "unique_sources": len(final_results),
                "sub_questions_researched": len(sub_questions),
                "communities_referenced": len(communities_used),
            },
        }

    async def agentic_rag_stream(
        self,
        question: str,
        top_k: int = 5,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Streaming version of agentic RAG with extended thinking.

        Yields events as they happen:
        - thinking: Reasoning step updates
        - search: Search operations
        - retrieval: Results found
        - sources: Retrieved sources
        - graph_context: Graph context data
        - content: Streamed answer tokens
        - done: Completion signal
        """
        import re

        from openai import AsyncOpenAI

        # Validate user input for prompt injection (if enabled)
        processed_question, was_blocked, reason = validate_and_process_input(
            question, strict_mode=True, enabled=self.settings.prompt_security
        )

        if was_blocked:
            logger.warning(
                f"Blocked potential prompt injection in agentic RAG: {reason}"
            )
            yield {"content": get_safe_refusal_message()}
            yield {"done": True}
            return

        # Use turbo mode config if active, otherwise default settings
        llm_config = get_llm_config()

        if not llm_config.api_key:
            yield {"error": "OpenAI API key required for streaming"}
            return

        client = AsyncOpenAI(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )

        reasoning_steps = []
        all_results = []
        all_graph_contexts = []
        communities_used = set()

        # Step 1: Emit thinking - analyzing question
        yield {"thinking": "Analyzing question complexity..."}

        decompose_response = await client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {
                    "role": "system",
                    "content": """Break down complex questions into sub-questions.
Output JSON: {"sub_questions": ["q1", "q2", ...]}. Max 3 sub-questions.""",
                },
                {"role": "user", "content": f"Break down: {question}"},
            ],
            temperature=0.2,
            max_tokens=300,
        )

        try:
            decompose_text = decompose_response.choices[0].message.content
            json_match = re.search(
                r'\{[^{}]*"sub_questions"[^{}]*\}', decompose_text, re.DOTALL
            )
            if json_match:
                sub_questions = json.loads(json_match.group())["sub_questions"]
            else:
                sub_questions = [question]
        except Exception:
            sub_questions = [question]

        yield {"thinking": f"Identified {len(sub_questions)} research areas"}
        yield {"sub_questions": sub_questions}

        # Step 2: Search communities
        if self.settings.enable_community_detection:
            yield {"thinking": "Searching knowledge graph communities..."}
            relevant_communities = self.neo4j.search_communities_by_content(
                question, limit=3
            )
            if relevant_communities:
                communities_used.update(c["id"] for c in relevant_communities)
                yield {
                    "thinking": f"Found {len(relevant_communities)} relevant communities"
                }

        # Step 3: Research each sub-question
        for i, sub_q in enumerate(sub_questions[: self.settings.max_agentic_steps]):
            yield {
                "thinking": f"Researching ({i + 1}/{len(sub_questions)}): {sub_q[:60]}..."
            }

            search_result = await self.graph_search_async(
                sub_q,
                top_k=top_k,
                max_hops=max_hops,
                use_hybrid_rrf=True,
                collection_id=collection_id,
            )

            if self.settings.enable_reranking and search_result["results"]:
                reranked = await self.rerank_results_async(
                    sub_q, search_result["results"], top_k
                )
                all_results.extend(reranked)
            else:
                all_results.extend(search_result["results"][:top_k])

            if search_result["graph_context"]:
                all_graph_contexts.append(search_result["graph_context"])

            yield {
                "retrieval": f"Found {len(search_result['results'])} sources for sub-question {i + 1}"
            }

        # Deduplicate
        yield {"thinking": "Consolidating and ranking sources..."}
        seen_chunks = set()
        unique_results = []
        for r in all_results:
            chunk_id = r.get("chunk_id", "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                unique_results.append(r)

        unique_results.sort(
            key=lambda x: x.get("rerank_score", x.get("score", 0)), reverse=True
        )
        final_results = unique_results[: top_k * 2]

        # Build graph context
        merged_entities = {}
        merged_relationships = []
        merged_communities = []

        for gc in all_graph_contexts:
            for entity in gc.get("entities", []):
                name = entity.get("name", "")
                if name and name not in merged_entities:
                    merged_entities[name] = entity
                    if entity.get("community_id"):
                        communities_used.add(entity["community_id"])
            for rel in gc.get("relationships", []):
                merged_relationships.append(rel)

        if communities_used and self.settings.enable_graph_summarization:
            for com_id in list(communities_used)[:5]:
                community = self.neo4j.get_community(com_id)
                if community and community.get("summary"):
                    merged_communities.append(
                        {
                            "id": com_id,
                            "name": community.get("name"),
                            "summary": community.get("summary"),
                        }
                    )

        graph_context = (
            GraphContext(
                entities=list(merged_entities.values())[:15],
                relationships=merged_relationships[:20],
                chunks=[],
                communities=merged_communities,
            )
            if merged_entities
            else None
        )

        # Yield sources and graph context
        sources = [
            {
                "document_id": r["document_id"],
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "score": r.get("rerank_score", r.get("score", 0)),
                "metadata": {"filename": r["filename"]},
            }
            for r in final_results
        ]
        yield {"sources": sources}

        if graph_context:
            yield {"graph_context": graph_context.model_dump()}

        yield {
            "retrieval_stats": {
                "total_sources": len(all_results),
                "unique_sources": len(final_results),
                "communities_used": len(communities_used),
            }
        }

        # Step 4: Generate streaming answer
        yield {"thinking": "Synthesizing comprehensive answer..."}

        # Build context
        formatted_sources = ""
        for idx, r in enumerate(final_results):
            formatted_sources += (
                f"\n[src_{idx + 1}] Source: {r['filename']}\n{r['content']}\n"
            )

        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join(
                [
                    f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                    for e in graph_context.entities[:10]
                ]
            )
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"

        if graph_context and graph_context.relationships:
            rel_info = "\n".join(
                [
                    f"- {r['source']} --[{r['type']}]--> {r['target']}"
                    for r in graph_context.relationships[:15]
                ]
            )
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"

        if graph_context and graph_context.communities:
            community_info = "\n".join(
                [
                    f"- {c.get('name') or 'Community ' + str(c.get('id', ''))}: {c.get('summary', '')}"
                    for c in graph_context.communities
                ]
            )
            graph_context_str += f"\n\n=== Knowledge Communities ===\n{community_info}"

        agentic_system_prompt = """You are an expert research assistant providing comprehensive, accurate answers.
Cite sources as [src_1], [src_2], etc. Structure complex answers clearly.
Never mention "context", "provided documents", "knowledge graph", or similar phrases - answer naturally as an expert.""" + get_anti_injection_instruction(
            enabled=self.settings.prompt_security
        )

        messages = [
            {"role": "system", "content": agentic_system_prompt},
        ]

        if conversation_history:
            for msg in conversation_history[-self.settings.max_conversation_history :]:
                messages.append({"role": msg.role, "content": msg.content})

        messages.append(
            {
                "role": "user",
                "content": f"""Research Context:
{formatted_sources}
{graph_context_str}

Question: {question}

Comprehensive Answer:""",
            }
        )

        # Stream the response
        stream = await client.chat.completions.create(
            model=llm_config.model,
            messages=messages,
            temperature=0.3,
            max_tokens=2000,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield {"content": chunk.choices[0].delta.content}

        yield {"done": True, "communities_used": list(communities_used)}

    # =========================================================================
    # Agent-based Research Pipeline (Researcher/Writer Architecture)
    # =========================================================================

    async def agent_rag_stream(
        self,
        question: str,
        mode: str = "quality",
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Stream research pipeline results using the agent-based architecture.

        The researcher agent uses tool-calling to iteratively gather information,
        then the writer synthesizes it into a streamed answer.

        Args:
            question: The user's question
            mode: "speed" for chat, "quality" for deep research
            conversation_history: Previous conversation messages
            collection_id: Optional collection scope
        """
        from app.services.researcher_agent import run_research_pipeline
        from app.services.llm_config import get_llm_config

        llm_config = get_llm_config()

        async for event in run_research_pipeline(
            question=question,
            mode=mode,
            conversation_history=conversation_history,
            collection_id=collection_id,
            processor=self,
            neo4j_service=self.neo4j,
            llm_config=llm_config,
            settings=self.settings,
        ):
            yield event

    async def agent_rag_query(
        self,
        question: str,
        mode: str = "quality",
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None,
    ) -> dict:
        """
        Non-streaming agent RAG query. Wraps the streaming pipeline,
        collecting events and returning a single response dict.
        """
        answer = ""
        sources = []
        graph_context = None
        retrieval_stats = None
        communities_used = []
        reasoning_steps = []

        async for event in self.agent_rag_stream(
            question=question,
            mode=mode,
            conversation_history=conversation_history,
            collection_id=collection_id,
        ):
            if "content" in event:
                answer += event["content"]
            elif "sources" in event:
                sources = event["sources"]
            elif "graph_context" in event:
                graph_context = event["graph_context"]
            elif "retrieval_stats" in event:
                retrieval_stats = event["retrieval_stats"]
            elif "thinking" in event:
                reasoning_steps.append(f"[thinking] {event['thinking']}")
            elif "retrieval" in event:
                reasoning_steps.append(f"[retrieval] {event['retrieval']}")
            elif "done" in event:
                communities_used = event.get("communities_used", [])

        return {
            "question": question,
            "answer": answer,
            "sources": sources,
            "graph_context": graph_context,
            "reasoning_steps": reasoning_steps,
            "communities_used": communities_used,
            "retrieval_stats": retrieval_stats,
            "reranked": True,
        }


# Singleton instances
_document_processor: Optional[DocumentProcessor] = None
_query_processor: Optional[QueryProcessor] = None


def get_document_processor() -> DocumentProcessor:
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor


def get_query_processor() -> QueryProcessor:
    global _query_processor
    if _query_processor is None:
        _query_processor = QueryProcessor()
    return _query_processor
