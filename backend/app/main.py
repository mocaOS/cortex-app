"""MOCA Knowledge Base - FastAPI Backend."""

import os
import logging
import asyncio
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, List

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import aiofiles
import json

from app.config import get_settings
from app.models import (
    UploadResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    RAGRequest,
    RAGResponse,
    HealthResponse,
    ProcessingStatus,
    GraphStatsResponse,
    GraphContext,
    ConversationMessage,
    ReprocessRequest,
    DeleteRequest,
    MoveDocumentsRequest,
    # New R2R-style models
    Collection,
    CollectionCreate,
    CollectionUpdate,
    Community,
    CommunitySummaryRequest,
    # Task tracking models
    TaskStatus,
    TaskProgress,
    CommunityDetectionTaskRequest,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.document_processor import get_document_processor, get_query_processor
from app.services.graph_extractor import get_graph_extractor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    settings = get_settings()
    
    # Create upload directory
    os.makedirs(settings.upload_dir, exist_ok=True)
    
    # Initialize Neo4j
    neo4j = get_neo4j_service()
    try:
        neo4j.initialize_schema()
        logger.info("Neo4j schema initialized")
    except Exception as e:
        logger.warning(f"Could not initialize Neo4j schema: {e}")
    
    # Warm up processors
    try:
        get_document_processor()
        get_query_processor()
        logger.info("Processors initialized")
    except Exception as e:
        logger.warning(f"Could not initialize processors: {e}")
    
    yield
    
    # Cleanup
    neo4j.close()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="MOCA Knowledge Base",
    description="A Neo4j + Haystack powered GraphRAG knowledge base with entity extraction, knowledge graph construction, and semantic search",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Background Task Store
# =============================================================================

# In-memory task store (for production, consider Redis or database)
_task_store: Dict[str, TaskProgress] = {}


def create_task(task_type: str) -> TaskProgress:
    """Create a new task and return its progress tracker."""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task = TaskProgress(
        task_id=task_id,
        task_type=task_type,
        status=TaskStatus.PENDING,
        started_at=datetime.utcnow()
    )
    _task_store[task_id] = task
    return task


def get_task(task_id: str) -> Optional[TaskProgress]:
    """Get a task by ID."""
    return _task_store.get(task_id)


def update_task_progress(
    task_id: str,
    current: int,
    total: int,
    message: str,
    status: TaskStatus = TaskStatus.RUNNING
) -> None:
    """Update task progress."""
    task = _task_store.get(task_id)
    if task:
        task.progress_current = current
        task.progress_total = total
        task.progress_percent = (current / total * 100) if total > 0 else 0
        task.message = message
        task.status = status


def complete_task(task_id: str, result: dict) -> None:
    """Mark a task as completed with results."""
    task = _task_store.get(task_id)
    if task:
        task.status = TaskStatus.COMPLETED
        task.progress_percent = 100.0
        task.completed_at = datetime.utcnow()
        task.result = result
        task.message = "Completed successfully"


def fail_task(task_id: str, error: str) -> None:
    """Mark a task as failed."""
    task = _task_store.get(task_id)
    if task:
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.utcnow()
        task.error = error
        task.message = f"Failed: {error}"


def cleanup_old_tasks(max_age_hours: int = 24) -> int:
    """Remove completed/failed tasks older than max_age_hours."""
    now = datetime.utcnow()
    to_remove = []
    for task_id, task in _task_store.items():
        if task.completed_at:
            age = (now - task.completed_at).total_seconds() / 3600
            if age > max_age_hours:
                to_remove.append(task_id)
    for task_id in to_remove:
        del _task_store[task_id]
    return len(to_remove)


# =============================================================================
# Task Status Endpoints
# =============================================================================

@app.get("/api/tasks/{task_id}", response_model=TaskProgress)
async def get_task_status(task_id: str):
    """
    Get the current status and progress of a background task.
    
    Poll this endpoint to track long-running operations like community detection.
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str):
    """
    Get the result of a completed background task.
    
    Returns 202 if the task is still running, 200 with result if completed,
    or 500 if the task failed.
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    if task.status == TaskStatus.PENDING or task.status == TaskStatus.RUNNING:
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task.task_id,
                "status": task.status.value,
                "progress_percent": task.progress_percent,
                "message": task.message
            }
        )
    
    if task.status == TaskStatus.FAILED:
        raise HTTPException(status_code=500, detail=task.error or "Task failed")
    
    # Task completed
    return task.result


@app.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = Query(default=None, description="Filter by status"),
    task_type: Optional[str] = Query(default=None, description="Filter by task type")
):
    """List all active tasks, optionally filtered by status or type."""
    tasks = list(_task_store.values())
    
    if status:
        tasks = [t for t in tasks if t.status.value == status]
    if task_type:
        tasks = [t for t in tasks if t.task_type == task_type]
    
    # Sort by started_at descending (newest first)
    tasks.sort(key=lambda t: t.started_at or datetime.min, reverse=True)
    
    return {
        "tasks": [t.model_dump() for t in tasks],
        "total": len(tasks)
    }


@app.delete("/api/tasks/{task_id}")
async def cancel_task(task_id: str):
    """
    Cancel/remove a task from the store.
    
    Note: This only removes the task record, it doesn't stop a running task.
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    del _task_store[task_id]
    return {"message": f"Task {task_id} removed"}


@app.post("/api/tasks/cleanup")
async def cleanup_tasks(max_age_hours: int = Query(default=24, ge=1, le=168)):
    """Remove old completed/failed tasks."""
    removed = cleanup_old_tasks(max_age_hours)
    return {"removed": removed, "remaining": len(_task_store)}


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    neo4j = get_neo4j_service()
    connected = neo4j.verify_connectivity()
    
    return HealthResponse(
        status="healthy" if connected else "degraded",
        neo4j_connected=connected,
        version="1.0.0"
    )


@app.get("/api/stats", response_model=GraphStatsResponse)
async def get_stats():
    """Get knowledge base and knowledge graph statistics."""
    try:
        neo4j = get_neo4j_service()
        stats = neo4j.get_stats()
        return GraphStatsResponse(
            document_count=stats["document_count"],
            chunk_count=stats["chunk_count"],
            entity_count=stats.get("entity_count", 0),
            relationship_count=stats.get("relationship_count", 0),
            total_size=stats["total_size"],
            community_count=stats.get("community_count", 0),
            collection_count=stats.get("collection_count", 0),
            pending_count=stats.get("pending_count", 0)
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    collection_id: Optional[str] = Query(default=None, description="Collection to add document to"),
    start_processing: bool = Query(default=False, description="Start processing immediately (set to false for bulk uploads)")
):
    """
    Upload a file to the knowledge base.
    
    For bulk uploads (100+ files), set start_processing=false to upload all files first,
    then call POST /api/documents/process-pending to start processing.
    """
    settings = get_settings()
    
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} not supported. Allowed: {settings.allowed_extensions}"
        )
    
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Validate file size
    max_size = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
        )
    
    # Save file permanently
    import uuid
    doc_id = str(uuid.uuid4())
    stored_filename = f"{doc_id}{file_ext}"
    file_path = os.path.join(settings.upload_dir, stored_filename)
    
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    try:
        processor = get_document_processor()
        
        if start_processing:
            # Legacy behavior: start processing immediately
            doc_id = await processor.process_file(file_path, file.filename, file_size, collection_id)
            return UploadResponse(
                document_id=doc_id,
                filename=file.filename,
                status=ProcessingStatus.PROCESSING,
                message="File uploaded and processing started"
            )
        else:
            # New behavior: just store the file, don't process yet
            doc_id = await processor.store_file_only(file_path, file.filename, file_size, collection_id)
            return UploadResponse(
                document_id=doc_id,
                filename=file.filename,
                status=ProcessingStatus.PENDING,
                message="File uploaded. Call /api/documents/process-pending to start processing."
            )
    except Exception as e:
        logger.error(f"Error storing file: {e}")
        # Clean up on error
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents():
    """List all documents in the knowledge base."""
    try:
        neo4j = get_neo4j_service()
        documents = neo4j.get_all_documents()
        return {"documents": documents, "total": len(documents)}
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}")
async def get_document(document_id: str):
    """Get a specific document."""
    try:
        neo4j = get_neo4j_service()
        document = neo4j.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return document
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}/content")
async def get_document_content(document_id: str):
    """
    Get a document with its full content (all chunks concatenated).
    
    Returns document metadata plus:
    - chunks: Array of chunk objects with id, content, chunk_index
    - full_content: All chunks concatenated as a single string
    """
    try:
        neo4j = get_neo4j_service()
        content = neo4j.get_document_content(document_id)
        if not content:
            raise HTTPException(status_code=404, detail="Document not found")
        return content
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document content: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document and clean up orphaned entities and communities from the knowledge base."""
    try:
        neo4j = get_neo4j_service()
        result = neo4j.delete_document(document_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return {
            "message": "Document deleted successfully",
            "orphaned_entities_removed": result["orphaned_entities_removed"],
            "orphaned_communities_removed": result["orphaned_communities_removed"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/delete")
async def delete_documents(request: DeleteRequest):
    """
    Delete multiple documents from the knowledge base.
    
    This endpoint deletes the specified documents and cleans up any orphaned entities and communities.
    """
    try:
        neo4j = get_neo4j_service()
        result = neo4j.delete_documents(request.document_ids)
        
        return {
            "message": f"Successfully deleted {result['deleted_count']} document(s)",
            "deleted_count": result["deleted_count"],
            "orphaned_entities_removed": result["orphaned_entities_removed"],
            "orphaned_communities_removed": result["orphaned_communities_removed"]
        }
    except Exception as e:
        logger.error(f"Error deleting documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents")
async def delete_all_documents():
    """
    Delete all documents from the knowledge base.
    
    WARNING: This is a destructive operation that removes all documents, chunks, entities, and communities.
    """
    try:
        neo4j = get_neo4j_service()
        result = neo4j.delete_all_documents()
        
        return {
            "message": f"Successfully deleted all {result['deleted_count']} document(s)",
            "deleted_count": result["deleted_count"],
            "entities_removed": result["entities_removed"],
            "communities_removed": result["communities_removed"]
        }
    except Exception as e:
        logger.error(f"Error deleting all documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/{document_id}/reprocess")
async def reprocess_document(
    document_id: str,
    file: Optional[UploadFile] = File(default=None)
):
    """
    Reprocess a single document.
    
    If no file is provided, uses the stored original file.
    If a file is provided, updates the stored file and reprocesses.
    """
    settings = get_settings()
    neo4j = get_neo4j_service()
    processor = get_document_processor()
    
    # Check document exists
    document = neo4j.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # If file is provided, use it (and update stored file)
    if file and file.filename:
        # Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in settings.allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"File type {file_ext} not supported. Allowed: {settings.allowed_extensions}"
            )
        
        # Read file content
        content = await file.read()
        file_size = len(content)
        
        # Validate file size
        max_size = settings.max_file_size_mb * 1024 * 1024
        if file_size > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
            )
        
        # Save new file (use document_id to maintain consistent path)
        new_filename = f"{document_id}{file_ext}"
        file_path = os.path.join(settings.upload_dir, new_filename)
        
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)
        
        try:
            await processor.reprocess_document_from_file(document_id, file_path, file_ext)
            
            return {
                "document_id": document_id,
                "filename": file.filename,
                "status": ProcessingStatus.PROCESSING,
                "message": "Reprocessing started with new file"
            }
        except Exception as e:
            logger.error(f"Error reprocessing document: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # No file provided - use stored file
        try:
            await processor.reprocess_document(document_id)
            
            return {
                "document_id": document_id,
                "filename": document["filename"],
                "status": ProcessingStatus.PROCESSING,
                "message": "Reprocessing started from stored file"
            }
        except ValueError as e:
            # File not available
            raise HTTPException(
                status_code=400,
                detail=str(e)
            )
        except Exception as e:
            logger.error(f"Error reprocessing document: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/reprocess")
async def reprocess_documents(
    request: ReprocessRequest,
    background_tasks: BackgroundTasks,
    concurrency: Optional[int] = Query(default=None, ge=1, le=50, description="Number of documents to process concurrently")
):
    """
    Reprocess multiple documents using their stored original files.
    
    Original files are permanently stored, so no re-upload is needed.
    This clears existing chunks and entities, queues them for reprocessing,
    then starts batch processing with controlled concurrency.
    
    Returns a task_id that can be used to poll for progress.
    """
    try:
        settings = get_settings()
        neo4j = get_neo4j_service()
        processor = get_document_processor()
        
        # Queue all documents for reprocessing (doesn't start processing yet)
        results = []
        queued_count = 0
        for doc_id in request.document_ids:
            try:
                doc = neo4j.get_document(doc_id)
                if not doc:
                    results.append({
                        "document_id": doc_id,
                        "status": "error",
                        "message": "Document not found"
                    })
                    continue
                
                # Queue for reprocessing (sets status to pending)
                processor.queue_document_for_reprocessing(doc_id)
                results.append({
                    "document_id": doc_id,
                    "status": "queued",
                    "message": "Queued for reprocessing"
                })
                queued_count += 1
            except ValueError as e:
                # File not available
                results.append({
                    "document_id": doc_id,
                    "status": "error",
                    "message": str(e)
                })
            except Exception as e:
                results.append({
                    "document_id": doc_id,
                    "status": "error",
                    "message": str(e)
                })
        
        # If any documents were queued, start batch processing
        if queued_count > 0:
            actual_concurrency = concurrency if concurrency is not None else settings.batch_processing_concurrency
            
            # Create a task and start it in the background
            task = create_task("reprocess_batch")
            task.message = f"Queued {queued_count} documents for reprocessing..."
            task.progress_total = queued_count
            
            # Schedule the background task
            background_tasks.add_task(
                _run_batch_processing_task,
                task.task_id,
                actual_concurrency
            )
            
            return {
                "results": results,
                "total_queued": queued_count,
                "task_id": task.task_id,
                "concurrency": actual_concurrency,
                "message": f"Queued {queued_count} documents. Processing with concurrency={actual_concurrency}. Poll /api/tasks/{task.task_id} for progress."
            }
        else:
            return {
                "results": results,
                "total_queued": 0,
                "message": "No documents were queued for reprocessing"
            }
    except Exception as e:
        logger.error(f"Error reprocessing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Batch Processing Endpoints
# =============================================================================

@app.get("/api/documents/pending")
async def get_pending_documents():
    """
    Get all documents with 'pending' status that are waiting to be processed.
    
    Use this to check how many documents are queued before calling process-pending.
    """
    try:
        processor = get_document_processor()
        pending = processor.get_pending_documents()
        return {
            "pending_count": len(pending),
            "documents": pending
        }
    except Exception as e:
        logger.error(f"Error getting pending documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _run_batch_processing_task(
    task_id: str,
    concurrency: int
) -> None:
    """Background task for batch document processing with progress tracking."""
    try:
        processor = get_document_processor()
        pending = processor.get_pending_documents()
        total = len(pending)
        
        if total == 0:
            complete_task(task_id, {
                "processed": 0,
                "failed": 0,
                "total": 0,
                "message": "No pending documents to process"
            })
            return
        
        update_task_progress(task_id, 0, total, f"Starting processing of {total} documents...")
        
        def progress_callback(current: int, total: int, message: str):
            update_task_progress(task_id, current, total, message)
        
        result = await processor.process_pending_documents(
            concurrency=concurrency,
            progress_callback=progress_callback
        )
        
        complete_task(task_id, result)
        
    except Exception as e:
        logger.error(f"Error in batch processing task {task_id}: {e}")
        fail_task(task_id, str(e))


@app.post("/api/documents/process-pending")
async def process_pending_documents(
    background_tasks: BackgroundTasks,
    concurrency: Optional[int] = Query(default=None, ge=1, le=50, description="Number of documents to process concurrently (defaults to BATCH_PROCESSING_CONCURRENCY env var)")
):
    """
    Start processing all pending documents as a background task.
    
    Use this after bulk uploading files with start_processing=false.
    Processing happens with controlled concurrency to avoid server overload.
    
    Returns a task_id that can be used to poll for progress:
    - GET /api/tasks/{task_id} - Check progress
    - GET /api/tasks/{task_id}/result - Get final results
    """
    try:
        settings = get_settings()
        processor = get_document_processor()
        pending = processor.get_pending_documents()
        
        # Use provided concurrency or fall back to config default
        actual_concurrency = concurrency if concurrency is not None else settings.batch_processing_concurrency
        
        if len(pending) == 0:
            return {
                "message": "No pending documents to process",
                "pending_count": 0
            }
        
        # Create a task and start it in the background
        task = create_task("batch_processing")
        task.message = f"Queued {len(pending)} documents for processing..."
        task.progress_total = len(pending)
        
        # Schedule the background task
        background_tasks.add_task(
            _run_batch_processing_task,
            task.task_id,
            actual_concurrency
        )
        
        return {
            "task_id": task.task_id,
            "status": task.status,
            "pending_count": len(pending),
            "concurrency": actual_concurrency,
            "message": f"Started processing {len(pending)} documents. Poll /api/tasks/{task.task_id} for progress."
        }
    except Exception as e:
        logger.error(f"Error starting batch processing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cleanup/orphaned-entities")
async def cleanup_orphaned_entities():
    """
    Clean up orphaned entities from the knowledge graph.
    
    Orphaned entities are those not connected to any document chunk.
    This can happen from previous deletions or data inconsistencies.
    """
    try:
        neo4j = get_neo4j_service()
        deleted_count = neo4j.cleanup_orphaned_entities()
        return {
            "message": "Cleanup completed",
            "orphaned_entities_removed": deleted_count
        }
    except Exception as e:
        logger.error(f"Error cleaning up orphaned entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Perform semantic search on the knowledge base."""
    try:
        processor = get_query_processor()
        results = processor.search(
            query=request.query,
            top_k=request.top_k,
            filters=request.filters
        )
        
        search_results = [
            SearchResult(
                document_id=r["document_id"],
                chunk_id=r["chunk_id"],
                content=r["content"],
                score=r["score"],
                metadata={"filename": r["filename"], "chunk_index": r["chunk_index"]}
            )
            for r in results
        ]
        
        return SearchResponse(
            query=request.query,
            results=search_results,
            total_results=len(search_results)
        )
    except Exception as e:
        logger.error(f"Error in search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ask", response_model=RAGResponse)
async def ask_question(request: RAGRequest):
    """
    Ask a question using enhanced GraphRAG.
    
    Features:
    - Hybrid search with RRF (vector + keyword + graph)
    - Cross-encoder re-ranking for precision
    - Conversation memory for context
    - Agentic multi-step reasoning (optional)
    """
    try:
        processor = get_query_processor()
        
        # Convert conversation history if provided
        conversation_history = None
        if request.conversation_history:
            conversation_history = request.conversation_history
        
        result = await processor.rag_query(
            question=request.question,
            top_k=request.top_k,
            use_graph=request.use_graph,
            max_hops=request.max_hops,
            conversation_history=conversation_history,
            use_reranking=request.use_reranking,
            use_agentic=request.use_agentic
        )
        
        sources = [
            SearchResult(
                document_id=r["document_id"],
                chunk_id=r["chunk_id"],
                content=r["content"],
                score=r.get("rerank_score", r.get("score", 0)),
                metadata={
                    "filename": r["filename"], 
                    "chunk_index": r.get("chunk_index", 0),
                    "rerank_score": r.get("rerank_score")
                }
            )
            for r in result["sources"]
        ]
        
        # Build graph context if available
        graph_context = None
        if result.get("graph_context"):
            graph_context = GraphContext(**result["graph_context"])
        
        return RAGResponse(
            question=result["question"],
            answer=result["answer"],
            sources=sources,
            graph_context=graph_context,
            reranked=result.get("reranked", False),
            reasoning_steps=result.get("reasoning_steps")
        )
    except Exception as e:
        logger.error(f"Error in GraphRAG query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ask/stream")
async def ask_question_stream(request: RAGRequest):
    """
    Stream the RAG response for better UX.
    
    Returns Server-Sent Events (SSE) with:
    - content: Streamed answer tokens
    - sources: Retrieved sources (at end)
    - graph_context: Graph context (at end)
    - done: Completion signal
    
    When use_agentic=True (deep research mode), also includes:
    - thinking: Reasoning step updates
    - sub_questions: Decomposed research questions
    - retrieval: Source retrieval progress
    - retrieval_stats: Final retrieval statistics
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=400, 
            detail="OpenAI API key required for streaming"
        )
    
    # Route to agentic streaming if deep research is enabled
    if request.use_agentic and settings.enable_agentic_rag:
        async def generate_agentic():
            try:
                processor = get_query_processor()
                
                async for event in processor.agentic_rag_stream(
                    question=request.question,
                    top_k=request.top_k,
                    max_hops=request.max_hops,
                    conversation_history=request.conversation_history
                ):
                    yield f"data: {json.dumps(event)}\n\n"
                
            except Exception as e:
                logger.error(f"Error in streaming agentic RAG: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return StreamingResponse(
            generate_agentic(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    
    # Standard streaming RAG
    async def generate():
        try:
            from openai import AsyncOpenAI
            
            processor = get_query_processor()
            
            # First, do the retrieval (non-streaming part)
            conversation_history = request.conversation_history
            
            graph_context = None
            
            if request.use_graph:
                search_result = await processor.graph_search_async(
                    request.question,
                    top_k=request.top_k * 2,
                    max_hops=request.max_hops,
                    use_hybrid_rrf=settings.enable_hybrid_search
                )
                results = search_result["results"]
                graph_data = search_result["graph_context"]
                
                if graph_data["entities"] or graph_data["relationships"]:
                    graph_context = GraphContext(
                        entities=graph_data["entities"],
                        relationships=graph_data["relationships"],
                        chunks=graph_data["chunks"]
                    )
            else:
                results = processor.search(request.question, top_k=request.top_k * 2)
            
            # Re-rank if enabled
            if request.use_reranking and settings.enable_reranking and results:
                results = await processor.rerank_results_async(
                    request.question, results, request.top_k
                )
            else:
                results = results[:request.top_k]
            
            # Send sources first
            sources = [
                {
                    "document_id": r["document_id"],
                    "chunk_id": r["chunk_id"],
                    "content": r["content"],
                    "score": r.get("rerank_score", r.get("score", 0)),
                    "metadata": {"filename": r["filename"]}
                }
                for r in results
            ]
            yield f"data: {json.dumps({'sources': sources})}\n\n"
            
            # Send graph context
            if graph_context:
                yield f"data: {json.dumps({'graph_context': graph_context.model_dump()})}\n\n"
            
            # Build context for generation
            formatted_sources = ""
            for idx, r in enumerate(results):
                ref_id = f"src_{idx+1}"
                formatted_sources += f"\n[{ref_id}] Source: {r['filename']}\n{r['content']}\n"
            
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
            
            system_prompt = """You are an expert research assistant providing accurate, helpful answers.

Guidelines:
1. Synthesize information into a coherent, natural-sounding answer
2. Cite sources inline using [src_1], [src_2] notation when referencing specific information
3. Be precise and factual - avoid speculation
4. If you cannot fully answer, explain what aspects you can address

Response Style:
- Write naturally as if you're an expert directly answering the question
- Never mention "context", "provided documents", or similar phrases
- Never say "Based on the context" or "According to the documents provided"
- Present information confidently as expert knowledge"""
            
            prompt = f"""Answer the following question. Use [src_1], [src_2], etc. to cite specific information.

=== Reference Material ===
{formatted_sources if formatted_sources else "No references available."}
{graph_context_str}

### Question:
{request.question}

### Answer:"""
            
            # Build messages with conversation history
            messages = [{"role": "system", "content": system_prompt}]
            
            if conversation_history:
                max_history = settings.max_conversation_history
                for msg in conversation_history[-max_history:]:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })
            
            messages.append({"role": "user", "content": prompt})
            
            # Stream the response using async client
            client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base,
            )
            
            stream = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=0.3,
                max_tokens=1200,
                stream=True
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'content': content})}\n\n"
            
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except Exception as e:
            logger.error(f"Error in streaming RAG: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# =============================================================================
# GraphRAG Endpoints
# =============================================================================

@app.get("/api/graph/visualization")
async def get_graph_visualization(
    limit: int = Query(default=100, ge=0, le=10000, description="Max entities (0 = all)"),
    include_neighbors: bool = Query(default=True, description="Include 1-hop neighbor entities for more relationships")
):
    """
    Get knowledge graph data for visualization (R2R-style enhanced).
    
    This endpoint returns entities and ALL their relationships in both directions,
    optionally expanding to include neighbor entities to show more graph structure.
    
    The response includes:
    - nodes: Entity data for visualization
    - edges: Relationship data between entities
    - stats: Metadata about what's displayed vs total graph size
    
    Set limit=0 to fetch ALL entities (use with caution for large graphs).
    """
    try:
        neo4j = get_neo4j_service()
        data = neo4j.get_graph_visualization_data(limit=limit, include_neighbors=include_neighbors)
        return data
    except Exception as e:
        logger.error(f"Error getting graph visualization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity/{entity_name}/relationships")
async def get_entity_relationships(
    entity_name: str,
    max_depth: int = Query(default=2, ge=1, le=3, description="Maximum relationship hops to traverse"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum relationships to return")
):
    """
    Get an entity and all its relationships up to max_depth hops (R2R-style).
    
    This enables focused graph exploration from a specific entity,
    showing all connected entities and the relationships between them.
    """
    try:
        neo4j = get_neo4j_service()
        data = neo4j.get_entity_relationships(entity_name, max_depth=max_depth, limit=limit)
        if not data.get("entity"):
            raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity relationships: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/graph/subgraph")
async def get_graph_subgraph(
    entity_names: List[str],
    include_connections: bool = Query(default=True, description="Include entities that connect the specified entities")
):
    """
    Get a subgraph containing specified entities and their interconnections.
    
    R2R-style endpoint for focused graph visualization of specific entities.
    If include_connections is True, also includes bridging entities that
    connect the specified entities (up to 2 hops apart).
    """
    try:
        neo4j = get_neo4j_service()
        data = neo4j.get_graph_subgraph(entity_names, include_connections=include_connections)
        return data
    except Exception as e:
        logger.error(f"Error getting graph subgraph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entities")
async def list_entities(
    entity_type: Optional[str] = Query(default=None, description="Filter by entity type"),
    limit: int = Query(default=50, ge=1, le=200)
):
    """List entities in the knowledge graph."""
    try:
        neo4j = get_neo4j_service()
        with neo4j.driver.session() as session:
            if entity_type:
                result = session.run("""
                    MATCH (e:Entity {type: $type})
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    RETURN e.name as name, e.type as type, e.description as description,
                           count(c) as mention_count
                    ORDER BY mention_count DESC
                    LIMIT $limit
                """, type=entity_type, limit=limit)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e)
                    RETURN e.name as name, e.type as type, e.description as description,
                           count(c) as mention_count
                    ORDER BY mention_count DESC
                    LIMIT $limit
                """, limit=limit)
            
            entities = [dict(record) for record in result]
            return {"entities": entities, "total": len(entities)}
    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity/{entity_name}")
async def get_entity_details(entity_name: str, max_hops: int = Query(default=2, ge=1, le=3)):
    """Get details about a specific entity and its relationships."""
    try:
        neo4j = get_neo4j_service()
        context = neo4j.traverse_from_entities([entity_name], max_hops=max_hops)
        
        if not context["entities"]:
            raise HTTPException(status_code=404, detail="Entity not found")
        
        return context
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/search")
async def search_entities(query: str = Query(..., min_length=1)):
    """Search for entities by name."""
    try:
        neo4j = get_neo4j_service()
        results = neo4j.find_entities_by_name([query])
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/status")
async def get_graph_status():
    """Get GraphRAG system status."""
    try:
        settings = get_settings()
        extractor = get_graph_extractor()
        neo4j = get_neo4j_service()
        stats = neo4j.get_stats()
        
        return {
            "graph_extraction_enabled": settings.enable_graph_extraction,
            "llm_available": extractor.is_available,
            "model": settings.openai_model if extractor.is_available else None,
            "entity_count": stats.get("entity_count", 0),
            "relationship_count": stats.get("relationship_count", 0),
            "community_count": stats.get("community_count", 0),
            "collection_count": stats.get("collection_count", 0),
            # New R2R-style features
            "community_detection_enabled": settings.enable_community_detection,
            "graph_summarization_enabled": settings.enable_graph_summarization,
            "semantic_entity_resolution_enabled": settings.enable_semantic_entity_resolution,
            "collections_enabled": settings.enable_collections,
        }
    except Exception as e:
        logger.error(f"Error getting graph status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Collection Endpoints (R2R-style)
# =============================================================================

@app.get("/api/collections")
async def list_collections():
    """List all collections."""
    try:
        neo4j = get_neo4j_service()
        collections = neo4j.list_collections()
        return {"collections": collections, "total": len(collections)}
    except Exception as e:
        logger.error(f"Error listing collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/collections")
async def create_collection(request: CollectionCreate):
    """Create a new collection."""
    try:
        neo4j = get_neo4j_service()
        collection = neo4j.create_collection(request.name, request.description)
        if not collection:
            raise HTTPException(status_code=500, detail="Failed to create collection")
        return collection
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collections/{collection_id}")
async def get_collection(collection_id: str):
    """Get a specific collection with stats."""
    try:
        neo4j = get_neo4j_service()
        collection = neo4j.get_collection(collection_id)
        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")
        return collection
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: str):
    """
    Delete a collection and move all its documents to the default collection.
    
    Documents are preserved in the default collection and can be deleted
    individually from there if needed, which properly cleans up chunks and
    orphaned entities.
    """
    try:
        # Prevent deletion of the default collection
        if collection_id == "default":
            raise HTTPException(status_code=400, detail="Cannot delete the default collection")
        
        neo4j = get_neo4j_service()
        result = neo4j.delete_collection(collection_id)
        if not result.get("deleted"):
            raise HTTPException(status_code=404, detail="Collection not found")
        
        documents_moved = result.get("documents_moved", 0)
        return {
            "message": f"Collection deleted, {documents_moved} document(s) moved to default collection",
            "documents_moved": documents_moved
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/collections/{collection_id}/documents/{document_id}")
async def add_document_to_collection(collection_id: str, document_id: str):
    """Add a document to a collection."""
    try:
        neo4j = get_neo4j_service()
        success = neo4j.add_document_to_collection(document_id, collection_id)
        if not success:
            raise HTTPException(status_code=404, detail="Collection or document not found")
        return {"message": "Document added to collection"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding document to collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/move")
async def move_documents_to_collection(request: MoveDocumentsRequest):
    """Move multiple documents to a collection."""
    try:
        neo4j = get_neo4j_service()
        result = neo4j.move_documents_to_collection(
            request.document_ids, 
            request.target_collection_id
        )
        return {
            "message": f"Successfully moved {result['moved_count']} document(s)",
            "moved_count": result["moved_count"]
        }
    except Exception as e:
        logger.error(f"Error moving documents to collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collections/{collection_id}/entities")
async def get_collection_entities(
    collection_id: str,
    limit: int = Query(default=100, ge=1, le=500)
):
    """Get entities in a collection's knowledge graph."""
    try:
        neo4j = get_neo4j_service()
        entities = neo4j.get_collection_entities(collection_id, limit)
        return {"entities": entities, "total": len(entities)}
    except Exception as e:
        logger.error(f"Error getting collection entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Community Detection Endpoints (R2R-style)
# =============================================================================

@app.get("/api/graph/communities")
async def list_communities(limit: int = Query(default=50, ge=1, le=200)):
    """List all detected communities."""
    try:
        neo4j = get_neo4j_service()
        communities = await asyncio.to_thread(neo4j.list_communities, limit)
        return {"communities": communities, "total": len(communities)}
    except Exception as e:
        logger.error(f"Error listing communities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _run_community_detection_task(
    task_id: str,
    min_size: int,
    collection_id: Optional[str]
) -> None:
    """Background task for community detection with progress tracking."""
    try:
        settings = get_settings()
        neo4j = get_neo4j_service()
        extractor = get_graph_extractor()
        
        # Step 1: Detect communities
        update_task_progress(task_id, 0, 1, "Detecting communities in knowledge graph...")
        communities = await asyncio.to_thread(neo4j.detect_communities, min_size, collection_id)
        
        if not communities:
            complete_task(task_id, {
                "communities": [],
                "total": 0,
                "collection_id": collection_id
            })
            return
        
        # Step 2: Generate summaries if enabled
        if settings.enable_graph_summarization and extractor.is_available:
            total_steps = len(communities)
            update_task_progress(
                task_id, 0, total_steps,
                f"Generating summaries for {total_steps} communities..."
            )
            
            for i, community in enumerate(communities):
                update_task_progress(
                    task_id, i, total_steps,
                    f"Generating summary for community {i + 1}/{total_steps}..."
                )
                
                # Get relationships for this community
                entity_names = [e.get("name") for e in community.get("entities", [])]
                if community.get("id") is not None:
                    relationships = await asyncio.to_thread(
                        neo4j.get_community_relationships, community["id"]
                    )
                else:
                    relationships = []
                
                # Generate summary using async version
                summary_result = await extractor.generate_community_summary_async(
                    community.get("entities", []),
                    relationships
                )
                
                # Store community with summary
                await asyncio.to_thread(
                    neo4j.store_community,
                    community["id"],
                    entity_names,
                    summary_result.get("summary"),
                    summary_result.get("name")
                )
                
                community["name"] = summary_result.get("name")
                community["summary"] = summary_result.get("summary")
            
            update_task_progress(task_id, total_steps, total_steps, "Finalizing...")
        
        complete_task(task_id, {
            "communities": communities,
            "total": len(communities),
            "collection_id": collection_id
        })
        
    except Exception as e:
        logger.error(f"Error in community detection task {task_id}: {e}")
        fail_task(task_id, str(e))


@app.post("/api/graph/communities/detect")
async def detect_communities(
    background_tasks: BackgroundTasks,
    min_size: int = Query(default=3, ge=2, le=20, description="Minimum community size"),
    collection_id: Optional[str] = Query(default=None, description="Scope to collection")
):
    """
    Start community detection on the knowledge graph as a background task.
    
    Returns immediately with a task_id that can be used to poll for progress.
    Use GET /api/tasks/{task_id} to check progress.
    Use GET /api/tasks/{task_id}/result to get the final results.
    """
    try:
        settings = get_settings()
        if not settings.enable_community_detection:
            raise HTTPException(status_code=400, detail="Community detection is disabled")
        
        # Create a task and start it in the background
        task = create_task("community_detection")
        task.message = "Initializing community detection..."
        
        # Schedule the background task
        background_tasks.add_task(
            _run_community_detection_task,
            task.task_id,
            min_size,
            collection_id
        )
        
        return {
            "task_id": task.task_id,
            "status": task.status,
            "message": "Community detection started. Poll /api/tasks/{task_id} for progress."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting community detection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/communities/{community_id}")
async def get_community(community_id: int):
    """Get a specific community with its entities and relationships."""
    try:
        neo4j = get_neo4j_service()
        community = await asyncio.to_thread(neo4j.get_community, community_id)
        if not community:
            raise HTTPException(status_code=404, detail="Community not found")
        return community
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting community: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/graph/communities/summarize")
async def summarize_communities(request: CommunitySummaryRequest):
    """
    Generate or regenerate summaries for communities.
    
    Uses LLM to create descriptive names and summaries for entity communities.
    """
    try:
        settings = get_settings()
        if not settings.enable_graph_summarization:
            raise HTTPException(status_code=400, detail="Graph summarization is disabled")
        
        neo4j = get_neo4j_service()
        extractor = get_graph_extractor()
        
        if not extractor.is_available:
            raise HTTPException(status_code=400, detail="LLM not available for summarization")
        
        # Get communities to summarize - run in thread pool to not block
        if request.community_ids:
            # Fetch communities concurrently
            community_tasks = [asyncio.to_thread(neo4j.get_community, cid) for cid in request.community_ids]
            communities = await asyncio.gather(*community_tasks)
            communities = [c for c in communities if c]
        else:
            communities = await asyncio.to_thread(neo4j.list_communities, settings.max_communities)
        
        results = []
        for community in communities:
            # Skip if already has summary and not forcing regeneration
            if community.get("summary") and not request.force_regenerate:
                results.append({"id": community["id"], "status": "skipped", "reason": "already has summary"})
                continue
            
            # Get relationships - run in thread pool
            relationships = await asyncio.to_thread(neo4j.get_community_relationships, community["id"])
            
            # Generate summary
            summary_result = await extractor.generate_community_summary_async(
                community.get("entities", []),
                relationships
            )
            
            # Store updated community - run in thread pool
            entity_names = [e.get("name") for e in community.get("entities", [])]
            await asyncio.to_thread(
                neo4j.store_community,
                community["id"],
                entity_names,
                summary_result.get("summary"),
                summary_result.get("name")
            )
            
            results.append({
                "id": community["id"],
                "status": "summarized",
                "name": summary_result.get("name"),
                "summary": summary_result.get("summary")
            })
        
        return {"results": results, "total_processed": len(results)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error summarizing communities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/communities/search")
async def search_communities(
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(default=5, ge=1, le=20)
):
    """Search communities by their summary content."""
    try:
        neo4j = get_neo4j_service()
        results = neo4j.search_communities_by_content(query, limit)
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching communities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Extended Thinking / Streaming Agentic RAG (R2R-style)
# =============================================================================

@app.post("/api/ask/stream/thinking")
async def ask_with_thinking_stream(request: RAGRequest):
    """
    Stream the RAG response with extended thinking visibility.
    
    Returns Server-Sent Events (SSE) with:
    - thinking: Reasoning step updates (visible agent thinking)
    - search: Search operations being performed
    - retrieval: Sources found and retrieval stats
    - sub_questions: Decomposed research questions
    - sources: Retrieved sources
    - graph_context: Graph context including communities
    - content: Streamed answer tokens
    - done: Completion signal with final stats
    
    This provides R2R-style extended thinking where users can see
    the agent's reasoning process in real-time.
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key required for streaming")
    
    async def generate():
        try:
            processor = get_query_processor()
            
            async for event in processor.agentic_rag_stream(
                question=request.question,
                top_k=request.top_k,
                max_hops=request.max_hops,
                conversation_history=request.conversation_history
            ):
                yield f"data: {json.dumps(event)}\n\n"
            
        except Exception as e:
            logger.error(f"Error in streaming agentic RAG: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
