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
"""

import os
import uuid
import logging
import functools
import re
from pathlib import Path
from typing import Optional, List, AsyncGenerator, Callable, Tuple, Dict
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime

from haystack import Document as HaystackDocument
from docling_haystack.converter import DoclingConverter, ExportType
from haystack.components.preprocessors import DocumentSplitter

from app.config import get_settings
from app.models import (
    DocumentChunk,
    DocumentMetadata,
    ProcessingStatus,
    GraphContext,
    ConversationMessage,
    ReasoningStep,
    ThinkingEvent,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.graph_extractor import get_graph_extractor
from app.services.llm_config import get_llm_config
from app.services.prompt_security import (
    validate_and_process_input,
    get_anti_injection_instruction,
    get_safe_refusal_message,
)

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
    r'(?:https?://|ftp://|mailto:)'  # Protocol
    r'[^\s<>\[\](){}\"\'`]+',  # URL body (no whitespace or brackets)
    re.IGNORECASE
)

# Placeholder format that's unlikely to appear in real text and won't be split
URL_PLACEHOLDER_PREFIX = "§§URL_PLACEHOLDER_"
URL_PLACEHOLDER_SUFFIX = "§§"


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
        placeholder = f"{URL_PLACEHOLDER_PREFIX}{placeholder_id}{URL_PLACEHOLDER_SUFFIX}"
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

# Thread pool for document processing (CPU-intensive operations like embeddings)
# Initialized lazily to use config settings
_processing_executor: Optional[ThreadPoolExecutor] = None


def _get_processing_executor() -> ThreadPoolExecutor:
    """Get or create the processing thread pool executor."""
    global _processing_executor
    if _processing_executor is None:
        settings = get_settings()
        _processing_executor = ThreadPoolExecutor(
            max_workers=settings.processing_thread_workers,
            thread_name_prefix="docproc"
        )
        logger.info(f"Initialized processing executor with {settings.processing_thread_workers} workers")
    return _processing_executor


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
        
        # Initialize Docling converter (handles all document formats)
        # Uses MARKDOWN export type for consistent processing with existing pipeline
        self.docling_converter = DoclingConverter(
            export_type=ExportType.MARKDOWN,
        )
        
        # Initialize splitter based on configuration
        # Sentence-based splitting preserves semantic units better
        if self.settings.chunk_by == "sentence":
            self.splitter = DocumentSplitter(
                split_by="sentence",
                split_length=self.settings.sentences_per_chunk,
                split_overlap=1  # 1 sentence overlap for context continuity
            )
            logger.info(f"Using sentence-based chunking: {self.settings.sentences_per_chunk} sentences per chunk")
        else:
            self.splitter = DocumentSplitter(
                split_by="word",
                split_length=self.settings.chunk_size,
                split_overlap=self.settings.chunk_overlap
            )
            logger.info(f"Using word-based chunking: {self.settings.chunk_size} words per chunk")
        
        # Initialize embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.openai_api_key:
            from haystack.components.embedders import OpenAIDocumentEmbedder
            from haystack.utils import Secret
            self.embedder = OpenAIDocumentEmbedder(
                api_key=Secret.from_token(self.settings.openai_api_key),
                api_base_url=self.settings.openai_api_base,
                model=self.settings.embedding_model,
                dimensions=self.settings.embedding_dimension,
            )
            logger.info(f"Using OpenAI embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})")
        else:
            from haystack.components.embedders import SentenceTransformersDocumentEmbedder
            self.embedder = SentenceTransformersDocumentEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.embedder.warm_up()
            logger.info("Using SentenceTransformers embeddings")
        
        logger.info(f"Document processor initialized (GraphRAG: {self.graph_extractor.is_available})")
    
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
                    logger.info(f"Sent cancel signal to processing task for document {doc_id}")
        
        # Wait for task outside the lock to avoid deadlock
        if task_to_wait and not task_to_wait.done():
            try:
                # Wait for the task to actually finish
                await asyncio.wait_for(
                    asyncio.gather(task_to_wait, return_exceptions=True),
                    timeout=10.0  # Increased timeout
                )
                logger.info(f"Processing task for document {doc_id} has stopped")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for task cancellation for document {doc_id}")
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
                    error_message="Processing cancelled (document deleted)"
                )
            except Exception as e:
                logger.warning(f"Could not update status for cancelled document {doc_id}: {e}")
        
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
        return [
            doc_id for doc_id, task in _active_tasks.items()
            if not task.done()
        ]
    
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
    
    async def _start_processing(self, doc_id: str, file_path: str, file_type: str) -> None:
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
        self, 
        doc_id: str, 
        file_path: str, 
        file_type: str
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
            doc_id, 
            ProcessingStatus.PENDING,
            progress_message="Queued for reprocessing"
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
            doc_id, 
            ProcessingStatus.PENDING,
            progress_message="Queued for reprocessing"
        )
        
        # Start reprocessing in background using stored file (with task tracking)
        await self._start_processing(doc_id, file_path, file_type)
        
        return True
    
    async def reprocess_document_from_file(
        self, 
        doc_id: str,
        file_path: str,
        file_type: str
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
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        # Web pages
        ".html", ".htm",
        # Text files
        ".txt", ".md", ".markdown", ".rst",
        # Images (OCR)
        ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
        # Audio (ASR)
        ".wav", ".mp3", ".webvtt", ".vtt",
        # LaTeX
        ".tex", ".latex",
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
        collection_id: Optional[str] = None
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
            processing_status=ProcessingStatus.PENDING
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
        collection_id: Optional[str] = None
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
        doc_id = await self.store_file_only(file_path, filename, file_size, collection_id)
        
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
        progress_callback: Optional[Callable[[int, int, str], None]] = None
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
        
        logger.info(f"Starting processing of {total} pending documents (concurrency: {concurrency})")
        
        semaphore = asyncio.Semaphore(concurrency)
        completed = 0
        failed = 0
        
        async def process_one(doc: dict):
            nonlocal completed, failed
            async with semaphore:
                doc_id = doc["id"]
                file_path = doc.get("file_path")
                file_type = doc.get("file_type", "")
                
                if not file_path or not os.path.exists(file_path):
                    logger.error(f"File not found for document {doc_id}: {file_path}")
                    self.neo4j.update_document_status(
                        doc_id,
                        ProcessingStatus.FAILED,
                        error_message=f"File not found: {file_path}"
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
                    progress_callback(completed + failed, total, f"Processed {completed + failed}/{total}")
        
        # Process all pending documents
        tasks = [process_one(doc) for doc in pending]
        await asyncio.gather(*tasks)
        
        logger.info(f"Processing complete: {completed} succeeded, {failed} failed out of {total}")
        
        return {
            "processed": completed,
            "failed": failed,
            "total": total,
            "message": f"Processed {completed} documents, {failed} failed"
        }
    
    async def _process_document(
        self, 
        doc_id: str, 
        file_path: str,
        file_type: str
    ):
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
                    doc_id, ProcessingStatus.PROCESSING,
                    progress_message="Starting document processing..."
                )
            )
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 0, 100, "Converting document...")
            )
            
            # Yield control to allow other async tasks to run
            await asyncio.sleep(0)
            
            # Check for cancellation before conversion
            self._check_cancellation(doc_id)
            
            # Convert file to Haystack document
            converter = self._get_converter(file_type)
            if not converter:
                raise ValueError(f"Unsupported file type: {file_type}")
            
            # Run conversion in thread pool (CPU-bound)
            # DoclingConverter.run() expects 'paths' parameter (not 'sources')
            result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(converter.run, paths=[Path(file_path)])
            )
            documents = result.get("documents", [])
            
            if not documents:
                raise ValueError("No content extracted from file")
            
            # Check for cancellation after conversion
            self._check_cancellation(doc_id)
            
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 10, 100, "Splitting into chunks...")
            )
            
            # Yield control
            await asyncio.sleep(0)
            
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
                    content=protected_content,
                    meta=doc.meta
                )
                protected_documents.append(protected_doc)
                
                if url_map:
                    logger.debug(f"Protected {len(url_map)} URLs from chunking")
            
            # Split documents into chunks (run in thread pool)
            split_result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.splitter.run, documents=protected_documents)
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
                functools.partial(self.neo4j.update_document_progress, doc_id, 15, 100, f"Generating embeddings for {len(chunks)} chunks...")
            )
            
            # Yield control before heavy embedding operation
            await asyncio.sleep(0)
            
            # Check for cancellation before embedding
            self._check_cancellation(doc_id)
            
            # Generate embeddings (most CPU-intensive - run in thread pool)
            embed_result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.embedder.run, documents=chunks)
            )
            embedded_chunks = embed_result.get("documents", [])
            
            # Check for cancellation after embedding
            self._check_cancellation(doc_id)
            
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 25, 100, "Storing chunks in database...")
            )
            
            # Store chunks in Neo4j
            chunk_ids = []
            for idx, chunk in enumerate(embedded_chunks):
                # Check for cancellation BEFORE EVERY chunk storage
                self._check_cancellation(doc_id)
                
                chunk_id = f"{doc_id}_chunk_{idx}"
                chunk_ids.append(chunk_id)
                chunk_model = DocumentChunk(
                    id=chunk_id,
                    document_id=doc_id,
                    content=chunk.content,
                    embedding=chunk.embedding,
                    chunk_index=idx,
                    metadata=chunk.meta
                )
                # Store chunk in thread pool
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(self.neo4j.store_chunk, chunk_model)
                )
                
                # Update progress for chunk storage (25-35%)
                storage_progress = 25 + int((idx + 1) / len(embedded_chunks) * 10)
                if idx % 5 == 0 or idx == len(embedded_chunks) - 1:  # Update every 5 chunks
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.update_document_progress,
                            doc_id, storage_progress, 100, 
                            f"Stored chunk {idx + 1}/{len(embedded_chunks)}"
                        )
                    )
                
                # Yield control every 10 chunks to keep API responsive
                if idx % 10 == 0:
                    await asyncio.sleep(0)
            
            logger.info(f"Document {doc_id}: stored {len(embedded_chunks)} chunks")
            
            # Check for cancellation before graph extraction
            self._check_cancellation(doc_id)
            
            # =================================================================
            # GraphRAG: Extract entities and relationships from chunks
            # Uses extraction with document summary for context
            # Processes multiple chunks concurrently based on concurrent_extractions setting
            # =================================================================
            if self.graph_extractor.is_available and self.settings.enable_graph_extraction:
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_status,
                        doc_id, ProcessingStatus.EXTRACTING,
                        progress_message="Extracting knowledge graph..."
                    )
                )
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(self.neo4j.update_document_progress, doc_id, 35, 100, "Generating document summary...")
                )
                logger.info(f"Document {doc_id}: starting graph extraction...")
                
                # Yield control before graph extraction
                await asyncio.sleep(0)
                
                # Generate document summary for context
                # Combine all chunk content for summary (limited to first ~5000 chars)
                full_text = " ".join([c.content for c in embedded_chunks])[:5000]
                document_summary = await self.graph_extractor.generate_document_summary_async(full_text)
                
                if document_summary:
                    logger.info(f"Document {doc_id}: generated summary for extraction context")
                
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(self.neo4j.update_document_progress, doc_id, 40, 100, "Extracting entities and relationships...")
                )
                
                # Use semaphore for concurrent extraction (controlled by config)
                concurrent_limit = self.settings.concurrent_extractions
                semaphore = asyncio.Semaphore(concurrent_limit)
                extraction_results = {}  # Store results by index
                completed_count = 0
                
                # Track active extractions for logging
                active_extractions = 0
                active_lock = asyncio.Lock()
                
                async def extract_chunk(idx: int, chunk, chunk_id: str):
                    """Extract from a single chunk with semaphore control."""
                    nonlocal completed_count, active_extractions, total_entities
                    async with semaphore:
                        # Check for cancellation before starting extraction
                        self._check_cancellation(doc_id)
                        
                        async with active_lock:
                            active_extractions += 1
                            current_active = active_extractions
                        
                        if idx < 5 or idx % 10 == 0:  # Log for first 5 and every 10th
                            logger.info(
                                f"Document {doc_id}: Starting chunk {idx+1}/{len(embedded_chunks)} "
                                f"(active: {current_active}/{concurrent_limit})"
                            )
                        
                        try:
                            extraction = await self.graph_extractor.extract_from_text_async(
                                chunk.content, 
                                document_summary=document_summary
                            )
                            extraction_results[idx] = (chunk_id, extraction)
                            # Count entities from extraction result for progress reporting
                            if extraction and extraction.entities:
                                total_entities += len(extraction.entities)
                        except CancellationRequested:
                            raise  # Re-raise cancellation
                        except Exception as e:
                            logger.warning(f"Graph extraction failed for chunk {chunk_id}: {e}")
                            extraction_results[idx] = (chunk_id, None)
                        finally:
                            async with active_lock:
                                active_extractions -= 1
                        
                        # Update progress (run in executor)
                        completed_count += 1
                        extraction_progress = 40 + int(completed_count / len(embedded_chunks) * 55)
                        await loop.run_in_executor(
                            _get_processing_executor(),
                            functools.partial(
                                self.neo4j.update_document_progress,
                                doc_id, extraction_progress, 100,
                                f"Extracted {completed_count}/{len(embedded_chunks)} chunks ({total_entities} entities found)"
                            )
                        )
                        
                        # Yield control periodically
                        if completed_count % 5 == 0:
                            await asyncio.sleep(0)
                
                # Create extraction tasks for all chunks
                extraction_tasks = [
                    extract_chunk(idx, chunk, chunk_ids[idx])
                    for idx, chunk in enumerate(embedded_chunks)
                ]
                
                logger.info(
                    f"Document {doc_id}: processing {len(extraction_tasks)} chunks "
                    f"with concurrency limit of {concurrent_limit}"
                )
                
                # Run all extractions concurrently (limited by semaphore)
                # Use return_exceptions=True to collect results even if some fail
                try:
                    await asyncio.gather(*extraction_tasks, return_exceptions=True)
                except CancellationRequested:
                    logger.info(f"Document {doc_id}: extraction cancelled")
                    raise
                
                # Check for cancellation after extraction phase
                self._check_cancellation(doc_id)
                
                # Yield control after all extractions
                await asyncio.sleep(0)
                
                # Store results in order (run Neo4j operations in executor)
                # Note: total_entities is already counted during extraction for progress reporting
                # Here we track stored counts separately for accurate final logging
                stored_entities = 0
                stored_relationships = 0
                for idx in sorted(extraction_results.keys()):
                    # Check for cancellation BEFORE EVERY storage operation
                    # This is critical to stop entity storage when deletion is requested
                    self._check_cancellation(doc_id)
                    
                    chunk_id, extraction = extraction_results[idx]
                    if extraction and (extraction.entities or extraction.relationships):
                        counts = await loop.run_in_executor(
                            _get_processing_executor(),
                            functools.partial(self.neo4j.store_graph_extraction, chunk_id, extraction)
                        )
                        stored_entities += counts["entities"]
                        stored_relationships += counts["relationships"]
                        
                        logger.debug(
                            f"Chunk {idx}: stored {counts['entities']} entities, "
                            f"{counts['relationships']} relationships"
                        )
                    
                    # Yield control every 10 chunks
                    if idx % 10 == 0:
                        await asyncio.sleep(0)
                
                # Update totals from stored counts for final reporting
                total_entities = stored_entities
                total_relationships = stored_relationships
                
                logger.info(
                    f"Document {doc_id}: graph extraction complete - "
                    f"{total_entities} entities, {total_relationships} relationships"
                )
            
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 100, 100, "Processing complete!")
            )
            
            # Update document status
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_status,
                    doc_id, 
                    ProcessingStatus.COMPLETED,
                    chunk_count=len(embedded_chunks)
                )
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
                    error_message=str(e)
                )
            )
        # NOTE: We no longer delete the file after processing.
        # Files are kept for reprocessing without needing re-upload.


class QueryProcessor:
    """Process queries for semantic search and GraphRAG enhancements."""
    
    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        self._reranker = None  # Lazy load cross-encoder
        
        # Initialize text embedder based on configuration
        if self.settings.use_openai_embeddings and self.settings.openai_api_key:
            from haystack.components.embedders import OpenAITextEmbedder
            from haystack.utils import Secret
            self.text_embedder = OpenAITextEmbedder(
                api_key=Secret.from_token(self.settings.openai_api_key),
                api_base_url=self.settings.openai_api_base,
                model=self.settings.embedding_model,
                dimensions=self.settings.embedding_dimension,
            )
            logger.info(f"Using OpenAI text embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})")
        else:
            from haystack.components.embedders import SentenceTransformersTextEmbedder
            self.text_embedder = SentenceTransformersTextEmbedder(
                model="sentence-transformers/all-MiniLM-L6-v2"
            )
            self.text_embedder.warm_up()
            logger.info("Using SentenceTransformers text embeddings")
        
        logger.info("Query processor initialized (GraphRAG + Reranking + Agentic RAG enabled)")
    
    @property
    def reranker(self):
        """Lazy load cross-encoder for re-ranking."""
        if self._reranker is None and self.settings.enable_reranking:
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(self.settings.reranking_model)
                logger.info(f"Loaded cross-encoder: {self.settings.reranking_model}")
            except Exception as e:
                logger.warning(f"Failed to load cross-encoder, disabling reranking: {e}")
                self._reranker = False  # Mark as unavailable
        return self._reranker if self._reranker else None
    
    def rerank_results(
        self,
        query: str,
        results: List[dict],
        top_k: int = 5
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
            reranked = sorted(results, key=lambda x: x.get("rerank_score", 0), reverse=True)
            
            logger.debug(f"Reranked {len(results)} results")
            return reranked[:top_k]
            
        except Exception as e:
            logger.warning(f"Reranking failed: {e}")
            return results[:top_k]
    
    async def rerank_results_async(
        self,
        query: str,
        results: List[dict],
        top_k: int = 5
    ) -> List[dict]:
        """Async version of rerank_results."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _rerank_executor,
            lambda: self.rerank_results(query, results, top_k)
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
        collection_id: Optional[str] = None
    ) -> list[dict]:
        """Perform semantic search, optionally scoped to a collection."""
        # Generate query embedding
        query_embedding = self.embed_query(query)
        
        # Search in Neo4j
        results = self.neo4j.vector_search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
            collection_id=collection_id
        )
        
        return results
    
    def hybrid_search(
        self,
        query: str,
        top_k: int = 10,
        vector_weight: float = 0.5,
        keyword_weight: float = 0.3,
        metadata_weight: float = 0.2
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
            metadata_weight=metadata_weight
        )
        
        return results
    
    async def graph_search_async(
        self,
        query: str,
        top_k: int = 5,
        max_hops: int = 2,
        use_hybrid_rrf: bool = True,
        collection_id: Optional[str] = None
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
            query_entities = await self.graph_extractor.extract_entities_from_query_async(query)
        
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
                collection_id=collection_id
            )
            return {
                "results": hybrid_result["results"],
                "graph_context": hybrid_result["graph_context"],
                "search_method": "hybrid_rrf",
                "vector_count": hybrid_result.get("vector_count", 0),
                "keyword_count": hybrid_result.get("keyword_count", 0),
                "graph_chunk_count": hybrid_result.get("graph_chunk_count", 0)
            }
        else:
            # Legacy hybrid search
            result = self.neo4j.hybrid_search(
                query_embedding=query_embedding,
                entity_names=query_entities,
                top_k=top_k,
                max_hops=max_hops
            )
            return {
                "results": result["vector_results"],
                "graph_context": result["graph_context"],
                "search_method": "vector_graph"
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
        collection_id: Optional[str] = None
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
                collection_id=collection_id
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
                collection_id=collection_id
            )
            results = search_result["results"]
            graph_data = search_result["graph_context"]
            search_metadata = {
                "search_method": search_result.get("search_method", "unknown"),
                "vector_count": search_result.get("vector_count", 0),
                "keyword_count": search_result.get("keyword_count", 0),
                "graph_chunk_count": search_result.get("graph_chunk_count", 0)
            }
            
            # Build graph context object
            if graph_data["entities"] or graph_data["relationships"]:
                graph_context = GraphContext(
                    entities=graph_data["entities"],
                    relationships=graph_data["relationships"],
                    chunks=graph_data["chunks"]
                )
        else:
            # Fall back to vector-only search
            results = self.search(question, top_k=top_k * 2, collection_id=collection_id)
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
                "reasoning_steps": None
            }
        
        # Build context from graph (entities and relationships)
        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join([
                f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                for e in graph_context.entities[:10]
            ])
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"
        
        if graph_context and graph_context.relationships:
            rel_info = "\n".join([
                f"- {r['source']} --[{r['type']}]--> {r['target']}"
                for r in graph_context.relationships[:15]
            ])
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"
        
        # Check if OpenAI is configured
        if not self.settings.openai_api_key:
            full_context = "\n\n".join([
                f"[Source: {r['filename']}]\n{r['content']}"
                for r in results
            ]) + graph_context_str
            return {
                "question": question,
                "answer": f"Here is the relevant information:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None
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
- If sources conflict, acknowledge the discrepancy objectively""" + get_anti_injection_instruction(enabled=self.settings.prompt_security)
            
            # Format sources with reference IDs
            formatted_sources = ""
            if results:
                for idx, r in enumerate(results):
                    ref_id = f"src_{idx+1}"
                    rerank_info = f" (relevance: {r.get('rerank_score', r.get('score', 0)):.3f})" if reranked else ""
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
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })
            
            messages.append({"role": "user", "content": prompt})
            
            response = client.chat.completions.create(
                model=llm_config.model,
                messages=messages,
                temperature=0.3,
                max_tokens=1200  # Increased for more complete answers
            )
            
            answer = response.choices[0].message.content
            
            return {
                "question": question,
                "answer": answer,
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None,
                **search_metadata
            }
            
        except Exception as e:
            logger.error(f"Error in GraphRAG query: {e}")
            full_context = "\n\n".join([
                f"[Source: {r['filename']}]\n{r['content']}"
                for r in results
            ]) + graph_context_str
            return {
                "question": question,
                "answer": f"Error generating answer: {str(e)}. Here is the relevant context:\n\n{full_context}",
                "sources": results,
                "graph_context": graph_context.model_dump() if graph_context else None,
                "reranked": reranked,
                "reasoning_steps": None
            }
    
    async def _agentic_rag_query(
        self,
        question: str,
        top_k: int = 5,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None,
        thinking_callback: Optional[Callable[[ThinkingEvent], None]] = None
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
        from openai import OpenAI
        import re
        
        def emit_thinking(event_type: str, content: str, metadata: dict = None):
            """Helper to emit thinking events."""
            if thinking_callback:
                thinking_callback(ThinkingEvent(
                    event_type=event_type,
                    content=content,
                    metadata=metadata
                ))
        
        if not self.settings.openai_api_key:
            return await self.rag_query(
                question=question,
                top_k=top_k,
                use_graph=True,
                max_hops=max_hops,
                conversation_history=conversation_history,
                use_agentic=False,
                collection_id=collection_id
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
        reasoning_steps.append(ReasoningStep(
            step_number=step_number,
            action="decompose",
            description="Analyzing question complexity and identifying sub-questions"
        ))
        
        decompose_response = client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {"role": "system", "content": """You help break down complex questions into simpler sub-questions.
Output a JSON array of sub-questions that together would answer the main question.
If the question is simple, just return a single-element array with the original question.
Maximum 3 sub-questions. Format: {"sub_questions": ["q1", "q2", ...]}"""},
                {"role": "user", "content": f"Break down this question: {question}"}
            ],
            temperature=0.2,
            max_tokens=300
        )
        
        try:
            decompose_text = decompose_response.choices[0].message.content
            json_match = re.search(r'\{[^{}]*"sub_questions"[^{}]*\}', decompose_text, re.DOTALL)
            if json_match:
                sub_questions = json.loads(json_match.group())["sub_questions"]
            else:
                sub_questions = [question]
        except Exception as e:
            logger.warning(f"Failed to decompose question: {e}")
            sub_questions = [question]
        
        emit_thinking("thinking", f"Identified {len(sub_questions)} research areas: {sub_questions}")
        reasoning_steps.append(ReasoningStep(
            step_number=step_number,
            action="decompose",
            description=f"Identified {len(sub_questions)} research areas",
            details={"sub_questions": sub_questions}
        ))
        
        # =====================================================================
        # Step 2: Search relevant communities for context
        # =====================================================================
        if self.settings.enable_community_detection:
            step_number += 1
            emit_thinking("thinking", "Searching knowledge graph communities for relevant context...")
            
            relevant_communities = self.neo4j.search_communities_by_content(question, limit=3)
            if relevant_communities:
                communities_used.update(c["id"] for c in relevant_communities)
                community_context = "\n".join([
                    f"- {c.get('name') or 'Community ' + str(c['id'])}: {c.get('summary', '')[:200]}"
                    for c in relevant_communities
                ])
                emit_thinking("retrieval", f"Found {len(relevant_communities)} relevant communities")
                reasoning_steps.append(ReasoningStep(
                    step_number=step_number,
                    action="community_search",
                    description=f"Found {len(relevant_communities)} relevant entity communities",
                    details={"communities": [c.get("name") for c in relevant_communities]}
                ))
        
        # =====================================================================
        # Step 3: Research each sub-question
        # =====================================================================
        for i, sub_q in enumerate(sub_questions[:self.settings.max_agentic_steps]):
            step_number += 1
            emit_thinking("search", f"Researching: {sub_q}")
            reasoning_steps.append(ReasoningStep(
                step_number=step_number,
                action="search",
                description=f"Searching for: {sub_q[:100]}"
            ))
            
            search_result = await self.graph_search_async(
                sub_q,
                top_k=top_k,
                max_hops=max_hops,
                use_hybrid_rrf=True,
                collection_id=collection_id
            )
            
            # Re-rank results
            if self.settings.enable_reranking and search_result["results"]:
                reranked_results = await self.rerank_results_async(
                    sub_q, 
                    search_result["results"], 
                    top_k
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
        
        unique_results.sort(key=lambda x: x.get("rerank_score", x.get("score", 0)), reverse=True)
        final_results = unique_results[:top_k * 2]
        
        reasoning_steps.append(ReasoningStep(
            step_number=step_number,
            action="rerank",
            description=f"Gathered and ranked {len(final_results)} unique sources from {len(all_results)} total",
            details={"total_found": len(all_results), "after_dedup": len(final_results)}
        ))
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
                    merged_communities.append({
                        "id": com_id,
                        "name": community.get("name"),
                        "summary": community.get("summary")
                    })
        
        graph_context = GraphContext(
            entities=list(merged_entities.values())[:15],
            relationships=merged_relationships[:20],
            chunks=[],
            communities=merged_communities
        ) if merged_entities else None
        
        # =====================================================================
        # Step 6: Generate comprehensive answer
        # =====================================================================
        step_number += 1
        emit_thinking("synthesis", "Synthesizing comprehensive answer...")
        reasoning_steps.append(ReasoningStep(
            step_number=step_number,
            action="synthesize",
            description="Synthesizing comprehensive answer from gathered context"
        ))
        
        # Build context
        formatted_sources = ""
        for idx, r in enumerate(final_results):
            ref_id = f"src_{idx+1}"
            formatted_sources += f"\n[{ref_id}] Source: {r['filename']}\n{r['content']}\n"
        
        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join([
                f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                for e in graph_context.entities
            ])
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"
        
        if graph_context and graph_context.relationships:
            rel_info = "\n".join([
                f"- {r['source']} --[{r['type']}]--> {r['target']}"
                for r in graph_context.relationships
            ])
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"
        
        # Add community context
        if graph_context and graph_context.communities:
            community_info = "\n".join([
                f"- {c.get('name') or 'Community ' + str(c.get('id', ''))}: {c.get('summary', '')}"
                for c in graph_context.communities
            ])
            graph_context_str += f"\n\n=== Relevant Knowledge Communities ===\n{community_info}"
        
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
- Present information confidently as expert knowledge""" + get_anti_injection_instruction(enabled=self.settings.prompt_security)
        
        # Build messages with conversation history
        messages = [{"role": "system", "content": system_prompt}]
        
        if conversation_history:
            max_history = self.settings.max_conversation_history
            for msg in conversation_history[-max_history:]:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        prompt = f"""Provide a detailed answer to this question.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str if graph_context_str else ""}

### Question:
{question}

### Answer:"""
        
        messages.append({"role": "user", "content": prompt})
        
        response = client.chat.completions.create(
            model=llm_config.model,
            messages=messages,
            temperature=0.3,
            max_tokens=2000
        )
        
        answer = response.choices[0].message.content
        
        # Final thinking event
        emit_thinking("done", "Answer generated successfully")
        reasoning_steps.append(ReasoningStep(
            step_number=step_number + 1,
            action="complete",
            description="Answer generated successfully"
        ))
        
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
                "communities_referenced": len(communities_used)
            }
        }
    
    async def agentic_rag_stream(
        self,
        question: str,
        top_k: int = 5,
        max_hops: int = 2,
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None
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
        from openai import AsyncOpenAI
        import re
        
        # Validate user input for prompt injection (if enabled)
        processed_question, was_blocked, reason = validate_and_process_input(
            question, strict_mode=True, enabled=self.settings.prompt_security
        )
        
        if was_blocked:
            logger.warning(f"Blocked potential prompt injection in agentic RAG: {reason}")
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
                {"role": "system", "content": """Break down complex questions into sub-questions.
Output JSON: {"sub_questions": ["q1", "q2", ...]}. Max 3 sub-questions."""},
                {"role": "user", "content": f"Break down: {question}"}
            ],
            temperature=0.2,
            max_tokens=300
        )
        
        try:
            decompose_text = decompose_response.choices[0].message.content
            json_match = re.search(r'\{[^{}]*"sub_questions"[^{}]*\}', decompose_text, re.DOTALL)
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
            relevant_communities = self.neo4j.search_communities_by_content(question, limit=3)
            if relevant_communities:
                communities_used.update(c["id"] for c in relevant_communities)
                yield {"thinking": f"Found {len(relevant_communities)} relevant communities"}
        
        # Step 3: Research each sub-question
        for i, sub_q in enumerate(sub_questions[:self.settings.max_agentic_steps]):
            yield {"thinking": f"Researching ({i+1}/{len(sub_questions)}): {sub_q[:60]}..."}
            
            search_result = await self.graph_search_async(
                sub_q,
                top_k=top_k,
                max_hops=max_hops,
                use_hybrid_rrf=True,
                collection_id=collection_id
            )
            
            if self.settings.enable_reranking and search_result["results"]:
                reranked = await self.rerank_results_async(sub_q, search_result["results"], top_k)
                all_results.extend(reranked)
            else:
                all_results.extend(search_result["results"][:top_k])
            
            if search_result["graph_context"]:
                all_graph_contexts.append(search_result["graph_context"])
            
            yield {"retrieval": f"Found {len(search_result['results'])} sources for sub-question {i+1}"}
        
        # Deduplicate
        yield {"thinking": "Consolidating and ranking sources..."}
        seen_chunks = set()
        unique_results = []
        for r in all_results:
            chunk_id = r.get("chunk_id", "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                unique_results.append(r)
        
        unique_results.sort(key=lambda x: x.get("rerank_score", x.get("score", 0)), reverse=True)
        final_results = unique_results[:top_k * 2]
        
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
                    merged_communities.append({
                        "id": com_id,
                        "name": community.get("name"),
                        "summary": community.get("summary")
                    })
        
        graph_context = GraphContext(
            entities=list(merged_entities.values())[:15],
            relationships=merged_relationships[:20],
            chunks=[],
            communities=merged_communities
        ) if merged_entities else None
        
        # Yield sources and graph context
        sources = [
            {
                "document_id": r["document_id"],
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "score": r.get("rerank_score", r.get("score", 0)),
                "metadata": {"filename": r["filename"]}
            }
            for r in final_results
        ]
        yield {"sources": sources}
        
        if graph_context:
            yield {"graph_context": graph_context.model_dump()}
        
        yield {"retrieval_stats": {
            "total_sources": len(all_results),
            "unique_sources": len(final_results),
            "communities_used": len(communities_used)
        }}
        
        # Step 4: Generate streaming answer
        yield {"thinking": "Synthesizing comprehensive answer..."}
        
        # Build context
        formatted_sources = ""
        for idx, r in enumerate(final_results):
            formatted_sources += f"\n[src_{idx+1}] Source: {r['filename']}\n{r['content']}\n"
        
        graph_context_str = ""
        if graph_context and graph_context.entities:
            entity_info = "\n".join([
                f"- {e['name']} ({e.get('type', 'Unknown')}): {e.get('description', '')}"
                for e in graph_context.entities[:10]
            ])
            graph_context_str += f"\n\n=== Related Entities ===\n{entity_info}"
        
        if graph_context and graph_context.relationships:
            rel_info = "\n".join([
                f"- {r['source']} --[{r['type']}]--> {r['target']}"
                for r in graph_context.relationships[:15]
            ])
            graph_context_str += f"\n\n=== Entity Relationships ===\n{rel_info}"
        
        if graph_context and graph_context.communities:
            community_info = "\n".join([
                f"- {c.get('name') or 'Community ' + str(c.get('id', ''))}: {c.get('summary', '')}"
                for c in graph_context.communities
            ])
            graph_context_str += f"\n\n=== Knowledge Communities ===\n{community_info}"
        
        agentic_system_prompt = """You are an expert research assistant providing comprehensive, accurate answers.
Cite sources as [src_1], [src_2], etc. Structure complex answers clearly.
Never mention "context", "provided documents", "knowledge graph", or similar phrases - answer naturally as an expert.""" + get_anti_injection_instruction(enabled=self.settings.prompt_security)
        
        messages = [
            {"role": "system", "content": agentic_system_prompt},
        ]
        
        if conversation_history:
            for msg in conversation_history[-self.settings.max_conversation_history:]:
                messages.append({"role": msg.role, "content": msg.content})
        
        messages.append({
            "role": "user",
            "content": f"""Research Context:
{formatted_sources}
{graph_context_str}

Question: {question}

Comprehensive Answer:"""
        })
        
        # Stream the response
        stream = await client.chat.completions.create(
            model=llm_config.model,
            messages=messages,
            temperature=0.3,
            max_tokens=2000,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield {"content": chunk.choices[0].delta.content}
        
        yield {"done": True, "communities_used": list(communities_used)}


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
