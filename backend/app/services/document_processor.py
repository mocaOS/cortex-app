"""Document processing service using Haystack with GraphRAG support.

Enhanced with R2R-style features:
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
from pathlib import Path
from typing import Optional, List, AsyncGenerator, Callable
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime

from haystack import Document as HaystackDocument
from haystack.components.converters import (
    PyPDFToDocument,
    TextFileToDocument,
    MarkdownToDocument,
)
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
from app.services.prompt_security import (
    validate_and_process_input,
    get_anti_injection_instruction,
    get_safe_refusal_message,
)

logger = logging.getLogger(__name__)

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


class DocumentProcessor:
    """Process documents using Haystack components with GraphRAG extraction."""
    
    def __init__(self):
        self.settings = get_settings()
        self.neo4j = get_neo4j_service()
        self.graph_extractor = get_graph_extractor()
        
        # Initialize converters
        self.pdf_converter = PyPDFToDocument()
        self.text_converter = TextFileToDocument()
        self.md_converter = MarkdownToDocument()
        
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
        
        # Start reprocessing in background using stored file
        asyncio.create_task(self._process_document(doc_id, file_path, file_type))
        
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
        
        # Process in background (same as new document)
        asyncio.create_task(self._process_document(doc_id, file_path, file_type))
        
        return True
    
    def _get_converter(self, file_type: str):
        """Get the appropriate converter for a file type."""
        converters = {
            ".pdf": self.pdf_converter,
            ".txt": self.text_converter,
            ".md": self.md_converter,
            ".markdown": self.md_converter,
        }
        return converters.get(file_type.lower())
    
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
        
        # Then start processing
        file_type = Path(filename).suffix.lower()
        asyncio.create_task(self._process_document(doc_id, file_path, file_type))
        
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
                    await self._process_document(doc_id, file_path, file_type)
                    completed += 1
                except Exception as e:
                    logger.error(f"Error processing document {doc_id}: {e}")
                    failed += 1
                
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
        """
        total_entities = 0
        total_relationships = 0
        loop = asyncio.get_event_loop()
        
        try:
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
            
            # Convert file to Haystack document
            converter = self._get_converter(file_type)
            if not converter:
                raise ValueError(f"Unsupported file type: {file_type}")
            
            # Run conversion in thread pool (CPU-bound)
            result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(converter.run, sources=[Path(file_path)])
            )
            documents = result.get("documents", [])
            
            if not documents:
                raise ValueError("No content extracted from file")
            
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 10, 100, "Splitting into chunks...")
            )
            
            # Yield control
            await asyncio.sleep(0)
            
            # Split documents into chunks (run in thread pool)
            split_result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.splitter.run, documents=documents)
            )
            chunks = split_result.get("documents", [])
            
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 15, 100, f"Generating embeddings for {len(chunks)} chunks...")
            )
            
            # Yield control before heavy embedding operation
            await asyncio.sleep(0)
            
            # Generate embeddings (most CPU-intensive - run in thread pool)
            embed_result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.embedder.run, documents=chunks)
            )
            embedded_chunks = embed_result.get("documents", [])
            
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(self.neo4j.update_document_progress, doc_id, 25, 100, "Storing chunks in database...")
            )
            
            # Store chunks in Neo4j
            chunk_ids = []
            for idx, chunk in enumerate(embedded_chunks):
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
            
            # =================================================================
            # GraphRAG: Extract entities and relationships from chunks
            # Uses R2R-style extraction with document summary for context
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
                
                # Generate document summary for context (R2R-style)
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
                await asyncio.gather(*extraction_tasks)
                
                # Yield control after all extractions
                await asyncio.sleep(0)
                
                # Store results in order (run Neo4j operations in executor)
                for idx in sorted(extraction_results.keys()):
                    chunk_id, extraction = extraction_results[idx]
                    if extraction and (extraction.entities or extraction.relationships):
                        counts = await loop.run_in_executor(
                            _get_processing_executor(),
                            functools.partial(self.neo4j.store_graph_extraction, chunk_id, extraction)
                        )
                        total_entities += counts["entities"]
                        total_relationships += counts["relationships"]
                        
                        logger.debug(
                            f"Chunk {idx}: extracted {counts['entities']} entities, "
                            f"{counts['relationships']} relationships"
                        )
                    
                    # Yield control every 10 chunks
                    if idx % 10 == 0:
                        await asyncio.sleep(0)
                
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
    """Process queries for semantic search and GraphRAG with R2R-style enhancements."""
    
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
        filters: Optional[dict] = None
    ) -> list[dict]:
        """Perform semantic search."""
        # Generate query embedding
        query_embedding = self.embed_query(query)
        
        # Search in Neo4j
        results = self.neo4j.vector_search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters
        )
        
        return results
    
    async def graph_search_async(
        self,
        query: str,
        top_k: int = 5,
        max_hops: int = 2,
        use_hybrid_rrf: bool = True
    ) -> dict:
        """
        Perform hybrid search combining vector similarity, keyword search, and graph traversal.
        Uses Reciprocal Rank Fusion (RRF) for better results.
        
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
                graph_weight=self.settings.graph_weight
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
        use_agentic: bool = False
    ) -> dict:
        """
        Answer a question using enhanced GraphRAG with R2R-style features.
        
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
                conversation_history=conversation_history
            )
        
        graph_context = None
        search_metadata = {}
        
        if use_graph and self.graph_extractor.is_available:
            # Use hybrid search with RRF
            search_result = await self.graph_search_async(
                question, 
                top_k=top_k * 2,  # Get more for reranking
                max_hops=max_hops,
                use_hybrid_rrf=self.settings.enable_hybrid_search
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
            results = self.search(question, top_k=top_k * 2)
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
            
            client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_api_base,
            )
            
            # Enhanced R2R-style system prompt with anti-injection protection
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
                model=self.settings.openai_model,
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
        
        R2R-style Deep Research with visible reasoning:
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
                use_agentic=False
            )
        
        client = OpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_api_base,
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
            model=self.settings.openai_model,
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
                use_hybrid_rrf=True
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
        
        # Add community context (R2R-style)
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
            model=self.settings.openai_model,
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
        
        if not self.settings.openai_api_key:
            yield {"error": "OpenAI API key required for streaming"}
            return
        
        client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_api_base,
        )
        
        reasoning_steps = []
        all_results = []
        all_graph_contexts = []
        communities_used = set()
        
        # Step 1: Emit thinking - analyzing question
        yield {"thinking": "Analyzing question complexity..."}
        
        decompose_response = await client.chat.completions.create(
            model=self.settings.openai_model,
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
                use_hybrid_rrf=True
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
            model=self.settings.openai_model,
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
