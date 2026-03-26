"""MOCA Knowledge Base - FastAPI Backend."""

import os
import logging
import asyncio
import uuid
import shutil
import glob
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, List

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field
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
    # Extended models
    Collection,
    CollectionCreate,
    CollectionUpdate,
    Community,
    CommunitySummaryRequest,
    # Task tracking models
    TaskStatus,
    TaskProgress,
    CommunityDetectionTaskRequest,
    # Custom input models
    CustomInputCreate,
    CustomInputResponse,
    CustomInputType,
    # API Key models
    APIKeyPermission,
    CreateAPIKeyRequest,
    CreateAPIKeyResponse,
    APIKeyListItem,
    UpdateAPIKeyRequest,
    # API Key Stats models
    APIKeyStats,
    APIKeyUsageDataPoint,
    APIKeyUsageHistoryResponse,
    AdminStatsOverview,
    APIKeyWithStats,
    # System Reset models
    SystemResetRequest,
    SystemResetResponse,
    # System Config model
    SystemConfigResponse,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.document_processor import get_document_processor, get_query_processor
from app.services.graph_extractor import get_graph_extractor
from app.services.prompt_security import (
    validate_and_process_input,
    get_anti_injection_instruction,
    filter_output,
    get_safe_refusal_message,
)
from app.services.compute3_service import get_compute3_service
from app.services.llm_config import get_llm_config, is_turbo_mode_active
from app.services.auth_service import (
    require_api_key,
    require_read_permission,
    require_manage_permission,
    require_admin,
    AuthResult,
)
from app.services.api_key_service import get_api_key_service
from app.services.api_usage_service import get_api_usage_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
# Suppress Neo4j notification warnings about missing properties/relationships
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)


_api_executor: Optional[ThreadPoolExecutor] = None


async def _event_loop_watchdog():
    """Background task that monitors event loop health and dumps thread stacks
    when the loop appears blocked."""
    import time
    import sys
    import threading
    
    consecutive_blocks = 0
    
    while True:
        start = time.monotonic()
        await asyncio.sleep(5)
        elapsed = time.monotonic() - start
        
        if elapsed > 7:
            consecutive_blocks += 1
            logger.warning(
                f"Event loop was blocked for {elapsed - 5:.1f}s "
                f"(heartbeat took {elapsed:.1f}s instead of 5s, "
                f"consecutive: {consecutive_blocks})"
            )
            
            # Dump all thread stacks to identify what's blocking
            if consecutive_blocks >= 2:
                logger.warning("=== THREAD DUMP (event loop blocked repeatedly) ===")
                for thread_id, frame in sys._current_frames().items():
                    thread_name = "unknown"
                    for t in threading.enumerate():
                        if t.ident == thread_id:
                            thread_name = t.name
                            break
                    import traceback
                    stack = "".join(traceback.format_stack(frame))
                    logger.warning(f"Thread {thread_name} ({thread_id}):\n{stack}")
                logger.warning("=== END THREAD DUMP ===")
                consecutive_blocks = 0
        else:
            consecutive_blocks = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global _api_executor
    settings = get_settings()

    # Dedicated thread pool for API endpoint handlers (asyncio.to_thread).
    # Kept separate from the document processing executor so blocking DB
    # queries in request handlers never compete with processing workers.
    _api_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="api_")
    asyncio.get_event_loop().set_default_executor(_api_executor)

    # Start event loop watchdog to detect GIL/blocking issues
    watchdog_task = asyncio.create_task(_event_loop_watchdog())
    
    # Create upload directory
    os.makedirs(settings.upload_dir, exist_ok=True)
    
    # Create custom inputs directory (for manually entered Q&A, text, markdown)
    os.makedirs(settings.custom_inputs_dir, exist_ok=True)
    
    # Initialize Neo4j
    neo4j = get_neo4j_service()
    try:
        neo4j.initialize_schema()
        logger.info("Neo4j schema initialized")
    except Exception as e:
        logger.warning(f"Could not initialize Neo4j schema: {e}")
    
    # Ensure admin API key record exists for usage tracking (only if tracking is enabled)
    if settings.track_admin_api_key_usage:
        try:
            admin_key_prefix = "admin"
            if settings.admin_api_key:
                # Use first 8 chars of the actual admin key as prefix for identification
                admin_key_prefix = settings.admin_api_key[:8] + "..." if len(settings.admin_api_key) >= 8 else "admin"
            neo4j.ensure_admin_key_exists(admin_key_prefix)
            logger.info("Admin API key record ensured for usage tracking")
        except Exception as e:
            logger.warning(f"Could not ensure admin API key record: {e}")
    else:
        logger.info("Admin API key usage tracking is disabled")
    
    # Warm up processors
    try:
        get_document_processor()
        get_query_processor()
        logger.info("Processors initialized")
    except Exception as e:
        logger.warning(f"Could not initialize processors: {e}")
    
    yield
    
    # Cleanup
    watchdog_task.cancel()
    neo4j.close()
    if _api_executor:
        _api_executor.shutdown(wait=False)
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
# API Usage Tracking Middleware
# =============================================================================

class APIUsageMiddleware(BaseHTTPMiddleware):
    """Middleware to track API usage per API key."""
    
    async def dispatch(self, request: Request, call_next):
        # Skip tracking for non-API paths
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        
        # Skip tracking for certain endpoints that don't need it
        skip_paths = ["/api/admin/api-keys/with-stats", "/api/admin/stats"]
        if any(request.url.path.startswith(p) for p in skip_paths):
            return await call_next(request)
        
        # Extract API key from header
        api_key = request.headers.get("X-API-Key")
        key_id = None
        is_admin_key = False
        
        if api_key:
            # Validate and get key_id
            from app.services.auth_service import validate_api_key
            auth = await validate_api_key(api_key)
            if auth.is_authenticated and auth.key_id:
                key_id = auth.key_id
                is_admin_key = auth.is_admin
        
        # Process the request
        response = await call_next(request)
        
        # Record usage if we have a valid key_id
        if key_id:
            # Check if we should skip tracking for admin key
            app_settings = get_settings()
            if is_admin_key and not app_settings.track_admin_api_key_usage:
                return response
            
            try:
                is_error = response.status_code >= 400
                error_message = None
                if is_error:
                    error_message = f"HTTP {response.status_code}"
                
                usage_service = get_api_usage_service()
                usage_service.record_request(
                    key_id=key_id,
                    endpoint_path=request.url.path,
                    is_error=is_error,
                    error_message=error_message
                )
            except Exception as e:
                # Don't let usage tracking failures break the API
                logger.warning(f"Failed to record API usage: {e}")
        
        return response


# Add usage tracking middleware
app.add_middleware(APIUsageMiddleware)


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
    connected = await asyncio.to_thread(neo4j.verify_connectivity)
    
    return HealthResponse(
        status="healthy" if connected else "degraded",
        neo4j_connected=connected,
        version="1.0.0"
    )


@app.get("/api/stats", response_model=GraphStatsResponse)
async def get_stats(auth: AuthResult = Depends(require_read_permission)):
    """Get knowledge base and knowledge graph statistics."""
    try:
        neo4j = get_neo4j_service()
        stats = await asyncio.to_thread(neo4j.get_stats)
        return GraphStatsResponse(
            document_count=stats["document_count"],
            chunk_count=stats["chunk_count"],
            entity_count=stats.get("entity_count", 0),
            relationship_count=stats.get("relationship_count", 0),
            total_size=stats["total_size"],
            community_count=stats.get("community_count", 0),
            collection_count=stats.get("collection_count", 0),
            pending_count=stats.get("pending_count", 0),
            completed_count=stats.get("completed_count", 0),
            failed_count=stats.get("failed_count", 0),
            processing_count=stats.get("processing_count", 0),
            avg_chunks_per_doc=stats.get("avg_chunks_per_doc", 0.0),
            entity_type_counts=stats.get("entity_type_counts", {}),
            avg_entity_mentions=stats.get("avg_entity_mentions", 0.0),
            last_relationship_analysis_at=stats.get("last_relationship_analysis_at"),
            last_community_detection_at=stats.get("last_community_detection_at"),
            last_entity_merge_at=stats.get("last_entity_merge_at"),
            entity_relationship_ratio=stats.get("entity_relationship_ratio", 0.0),
            relationship_target_ratio=stats.get("relationship_target_ratio", 3.0),
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    collection_id: Optional[str] = Query(default=None, description="Collection to add document to"),
    start_processing: bool = Query(default=False, description="Start processing immediately (set to false for bulk uploads)"),
    auth: AuthResult = Depends(require_manage_permission)
):
    """
    Upload a file to the knowledge base.
    
    For bulk uploads (100+ files), set start_processing=false to upload all files first,
    then call POST /api/documents/process-pending to start processing.
    """
    settings = get_settings()
    
    # Enforce file limit
    if settings.max_files > 0:
        neo4j = get_neo4j_service()
        stats = await asyncio.to_thread(neo4j.get_stats)
        if stats["document_count"] >= settings.max_files:
            raise HTTPException(
                status_code=403,
                detail=f"File limit reached (max: {settings.max_files}). Delete existing files or increase MAX_FILES."
            )
    
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

    # Check for duplicate document (same filename and file size)
    neo4j = get_neo4j_service()
    existing = await asyncio.to_thread(neo4j.find_document_by_filename_and_size, file.filename, file_size)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A document with the same name and size already exists: '{file.filename}' ({file_size} bytes)"
        )
    
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


async def generate_filename_with_llm(content: str, input_type: str, title: Optional[str] = None) -> str:
    """
    Generate a meaningful filename using an LLM based on the content.
    
    Returns a sanitized filename without extension.
    """
    import re
    import hashlib
    
    settings = get_settings()
    
    if not settings.openai_api_key:
        # Fallback to simple filename generation
        content_hash = hashlib.md5(content[:100].encode()).hexdigest()[:8]
        return f"custom_{input_type}_{content_hash}"
    
    try:
        from openai import AsyncOpenAI
        
        # Use fast mode config which has thinking disabled
        llm_config = get_llm_config(fast_mode=True)
        client = AsyncOpenAI(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )
        
        # Build prompt for filename generation - very direct
        hint = f" Topic: {title}." if title else ""
        prompt = f"""Filename for {input_type}:{hint}

{content[:400]}

Output only lowercase_words (3-5 words, underscores, no extension):"""

        # Build request kwargs - disable thinking
        request_kwargs = {
            "model": llm_config.model,
            "messages": [
                {"role": "system", "content": "Output only a filename. Use lowercase_words format. No thinking, no explanation."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 20,
        }
        
        # Disable thinking for DeepSeek, R1, and MiniMax models
        model_lower = llm_config.model.lower()
        if "deepseek" in model_lower or "r1" in model_lower or "minimax" in model_lower:
            request_kwargs["extra_body"] = {
                "enable_thinking": False,
                "reasoning_effort": "none",
            }
        
        response = await client.chat.completions.create(**request_kwargs)
        
        raw_filename = response.choices[0].message.content or ""
        raw_filename = raw_filename.strip()
        
        # Strip any thinking tags (complete or incomplete)
        raw_filename = re.sub(r'<think>.*?</think>', '', raw_filename, flags=re.DOTALL)
        raw_filename = re.sub(r'<thinking>.*?</thinking>', '', raw_filename, flags=re.DOTALL)
        raw_filename = re.sub(r'<think>.*$', '', raw_filename, flags=re.DOTALL)
        raw_filename = re.sub(r'<thinking>.*$', '', raw_filename, flags=re.DOTALL)
        
        # If starts with think tag, use fallback
        if raw_filename.startswith('<think') or raw_filename.startswith('<thinking'):
            content_hash = hashlib.md5(content[:100].encode()).hexdigest()[:8]
            return f"custom_{input_type}_{content_hash}"
        
        raw_filename = raw_filename.strip()
        
        # Remove any extension if LLM added one
        raw_filename = re.sub(r'\.[a-zA-Z]+$', '', raw_filename)
        # Replace spaces and invalid chars with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', raw_filename)
        # Remove multiple underscores
        sanitized = re.sub(r'_+', '_', sanitized)
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_').lower()
        # Limit length
        sanitized = sanitized[:50]
        
        if not sanitized:
            content_hash = hashlib.md5(content[:100].encode()).hexdigest()[:8]
            return f"custom_{input_type}_{content_hash}"
        
        return sanitized
        
    except Exception as e:
        logger.warning(f"LLM filename generation failed: {e}")
        content_hash = hashlib.md5(content[:100].encode()).hexdigest()[:8]
        return f"custom_{input_type}_{content_hash}"


class TopicHintRequest(BaseModel):
    """Request model for generating a topic hint."""
    content: str = Field(..., min_length=10, description="Main content to analyze")
    answer: Optional[str] = Field(default=None, description="Answer (for Q&A type)")
    input_type: str = Field(default="text", description="Type of input: qa, text, or markdown")


class TopicHintResponse(BaseModel):
    """Response model for topic hint generation."""
    topic_hint: str
    existing_similar: List[str] = Field(default_factory=list, description="Similar existing topics found")


@app.post("/api/custom-input/generate-topic", response_model=TopicHintResponse)
async def generate_topic_hint(request: TopicHintRequest):
    """
    Generate a topic hint for custom content using LLM.
    
    Also checks for existing similar topics in the knowledge base.
    """
    settings = get_settings()
    neo4j = get_neo4j_service()
    
    # Build the full content for analysis
    if request.input_type == "qa" and request.answer:
        full_content = f"Question: {request.content}\n\nAnswer: {request.answer}"
    else:
        full_content = request.content
    
    # Check for existing similar documents/topics
    existing_similar = []
    try:
        # Search for similar content in existing documents
        processor = get_query_processor()
        similar_docs = processor.search(full_content[:500], top_k=5)
        
        # Extract unique filenames as potential similar topics
        seen_files = set()
        for doc in similar_docs:
            filename = doc.get("filename", "")
            if filename and filename not in seen_files:
                # Clean up filename to get topic hint
                topic = filename.rsplit(".", 1)[0]  # Remove extension
                topic = topic.replace("_", " ").replace("-", " ")
                # Remove timestamp suffix if present (format: _YYYYMMDD_HHMMSS)
                import re
                topic = re.sub(r'\s*\d{8}\s*\d{6}$', '', topic)
                if topic and len(topic) > 3:
                    existing_similar.append(topic.strip())
                    seen_files.add(filename)
        
        existing_similar = existing_similar[:3]  # Limit to top 3
    except Exception as e:
        logger.warning(f"Error checking for similar topics: {e}")
    
    # Generate topic hint using LLM
    if not settings.openai_api_key:
        # Fallback: extract keywords from content
        words = full_content.split()[:10]
        topic_hint = " ".join(words[:5]) if words else "custom content"
        return TopicHintResponse(topic_hint=topic_hint, existing_similar=existing_similar)
    
    try:
        from openai import AsyncOpenAI
        
        # Use fast mode config which has thinking disabled
        llm_config = get_llm_config(fast_mode=True)
        client = AsyncOpenAI(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )
        
        # Build prompt for topic generation - very direct to avoid thinking
        existing_context = ""
        if existing_similar:
            existing_context = f" Similar topics: {', '.join(existing_similar)}."
        
        prompt = f"""Topic for: {full_content[:500]}{existing_context}

Output 3-7 words only:"""

        # Build request kwargs - disable thinking for models that support it
        request_kwargs = {
            "model": llm_config.model,
            "messages": [
                {"role": "system", "content": "You output short topic labels. Output only 3-7 words. Never explain or think out loud."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 20,
        }
        
        # Disable thinking for DeepSeek and similar models
        model_lower = llm_config.model.lower()
        if "deepseek" in model_lower or "r1" in model_lower or "minimax" in model_lower:
            request_kwargs["extra_body"] = {
                "enable_thinking": False,
                "reasoning_effort": "none",
            }
        
        response = await client.chat.completions.create(**request_kwargs)
        
        topic_hint = response.choices[0].message.content or ""
        topic_hint = topic_hint.strip()
        
        # Strip any thinking tags that might have leaked through (with or without closing tags)
        import re
        # Remove complete think blocks
        topic_hint = re.sub(r'<think>.*?</think>', '', topic_hint, flags=re.DOTALL)
        topic_hint = re.sub(r'<thinking>.*?</thinking>', '', topic_hint, flags=re.DOTALL)
        # Remove incomplete think blocks (no closing tag - remove everything from <think> onwards)
        topic_hint = re.sub(r'<think>.*$', '', topic_hint, flags=re.DOTALL)
        topic_hint = re.sub(r'<thinking>.*$', '', topic_hint, flags=re.DOTALL)
        # Also catch if it starts with the tag
        if topic_hint.startswith('<think>') or topic_hint.startswith('<thinking>'):
            topic_hint = ""
        
        topic_hint = topic_hint.strip()
        
        # If we ended up with empty string, try to extract from a different approach
        if not topic_hint:
            # Fallback: just use first few words of content as topic
            words = full_content.split()[:7]
            topic_hint = ' '.join(words) if words else "custom content"
        
        # Remove quotes, asterisks, and other formatting
        topic_hint = topic_hint.strip('"\'*`')
        topic_hint = re.sub(r'^(Topic hint:?|Topic:?)\s*', '', topic_hint, flags=re.IGNORECASE)
        topic_hint = topic_hint.strip()
        
        # If still too long or contains newlines, take first line
        if '\n' in topic_hint:
            topic_hint = topic_hint.split('\n')[0].strip()
        
        # Limit length
        if len(topic_hint) > 100:
            topic_hint = ' '.join(topic_hint.split()[:7])
        
        return TopicHintResponse(topic_hint=topic_hint, existing_similar=existing_similar)
        
    except Exception as e:
        logger.error(f"Error generating topic hint: {e}")
        # Fallback
        words = full_content.split()[:5]
        topic_hint = " ".join(words) if words else "custom content"
        return TopicHintResponse(topic_hint=topic_hint, existing_similar=existing_similar)


@app.post("/api/custom-input", response_model=CustomInputResponse)
async def create_custom_input(request: CustomInputCreate, auth: AuthResult = Depends(require_manage_permission)):
    """
    Create a custom knowledge input (Q&A, text, or markdown).
    
    This allows users to manually add knowledge to the knowledge base without uploading files.
    The content is saved as a markdown file and processed like any uploaded document.
    
    - For Q&A type: content is the question, answer is the answer
    - For text/markdown type: content is the full text
    
    The filename is automatically generated using an LLM to create a meaningful name.
    """
    settings = get_settings()
    neo4j = get_neo4j_service()
    
    # Enforce file limit
    if settings.max_files > 0:
        stats = await asyncio.to_thread(neo4j.get_stats)
        if stats["document_count"] >= settings.max_files:
            raise HTTPException(
                status_code=403,
                detail=f"File limit reached (max: {settings.max_files}). Delete existing files or increase MAX_FILES."
            )
    
    # Validate Q&A has an answer
    if request.input_type == CustomInputType.QA and not request.answer:
        raise HTTPException(
            status_code=400,
            detail="Answer is required for Q&A type input"
        )
    
    try:
        # Generate meaningful filename using LLM
        filename_base = await generate_filename_with_llm(
            content=request.content,
            input_type=request.input_type.value,
            title=request.title
        )
        
        # Format content based on type
        if request.input_type == CustomInputType.QA:
            file_content = f"""# Question

{request.content}

# Answer

{request.answer}
"""
        elif request.input_type == CustomInputType.MARKDOWN:
            file_content = request.content
        else:  # TEXT
            file_content = request.content
        
        # Generate unique filename
        doc_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_base}_{timestamp}.md"
        file_path = os.path.join(settings.custom_inputs_dir, f"{doc_id}.md")
        
        # Save the file
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(file_content)
        
        file_size = len(file_content.encode('utf-8'))
        
        # Process like a regular file upload
        processor = get_document_processor()
        
        # Use default collection if enabled and none specified
        collection_id = request.collection_id
        if collection_id is None and settings.enable_collections:
            collection_id = settings.default_collection
        
        if request.start_processing:
            # Start processing immediately
            doc_id = await processor.process_file(file_path, filename, file_size, collection_id)
            status = ProcessingStatus.PROCESSING
            message = "Custom input saved and processing started"
        else:
            # Just store, process later
            doc_id = await processor.store_file_only(file_path, filename, file_size, collection_id)
            status = ProcessingStatus.PENDING
            message = "Custom input saved. Call /api/documents/process-pending to start processing."
        
        # Store custom input metadata for later editing
        await asyncio.to_thread(
            neo4j.set_custom_input_metadata,
            doc_id,
            request.input_type.value,
            request.content,
            request.answer,
            request.title,
        )
        
        return CustomInputResponse(
            document_id=doc_id,
            filename=filename,
            status=status,
            message=message,
            input_type=request.input_type
        )
        
    except Exception as e:
        logger.error(f"Error creating custom input: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/custom-inputs")
async def list_custom_inputs(
    search: Optional[str] = Query(default=None, description="Search in filename, content, or topic"),
    limit: int = Query(default=50, ge=1, le=200)
):
    """
    List all custom inputs with optional search.
    
    Returns custom inputs (manually added Q&A, text, markdown) that can be edited.
    """
    try:
        neo4j = get_neo4j_service()
        custom_inputs = await asyncio.to_thread(neo4j.get_custom_inputs, search, limit)
        return {"custom_inputs": custom_inputs, "total": len(custom_inputs)}
    except Exception as e:
        logger.error(f"Error listing custom inputs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/custom-inputs/{document_id}")
async def get_custom_input(document_id: str):
    """
    Get a custom input's full data for editing.
    
    Returns the original content, answer (for Q&A), input type, and metadata.
    """
    try:
        neo4j = get_neo4j_service()
        custom_input = await asyncio.to_thread(neo4j.get_custom_input, document_id)
        if not custom_input:
            raise HTTPException(status_code=404, detail="Custom input not found")
        return custom_input
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting custom input: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents(auth: AuthResult = Depends(require_read_permission)):
    """List all documents in the knowledge base."""
    try:
        neo4j = get_neo4j_service()
        documents = await asyncio.to_thread(neo4j.get_all_documents)
        return {"documents": documents, "total": len(documents)}
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}")
async def get_document(document_id: str, auth: AuthResult = Depends(require_read_permission)):
    """Get a specific document."""
    try:
        neo4j = get_neo4j_service()
        document = await asyncio.to_thread(neo4j.get_document, document_id)
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
        content = await asyncio.to_thread(neo4j.get_document_content, document_id)
        if not content:
            raise HTTPException(status_code=404, detail="Document not found")
        return content
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document content: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}/file")
async def get_document_file(document_id: str):
    """
    Serve the original uploaded file for viewing/download.
    """
    try:
        neo4j = get_neo4j_service()
        doc = await asyncio.to_thread(neo4j.get_document, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        file_path = doc.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Original file not available")

        filename = doc.get("filename", os.path.basename(file_path))
        media_type = doc.get("file_type", "application/octet-stream")

        return FileResponse(
            path=file_path,
            filename=filename,
            media_type=media_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving document file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/download-zip")
async def download_documents_zip(request: Request):
    """
    Stream a zip archive of the original uploaded files for the given document IDs.
    Accepts JSON body: { "document_ids": ["id1", "id2", ...] }
    Uses ZIP64 and streams to handle large collections without loading everything into memory.
    """
    import zipfile
    import io

    body = await request.json()
    document_ids = body.get("document_ids", [])
    if not document_ids:
        raise HTTPException(status_code=400, detail="No document IDs provided")

    neo4j = get_neo4j_service()

    # Fetch file paths for all requested documents in one query
    docs = await asyncio.to_thread(neo4j.get_documents_file_paths, document_ids)
    if not docs:
        raise HTTPException(status_code=404, detail="No documents found")

    # Filter to documents that have files on disk
    valid_docs = []
    seen_names = {}
    for doc in docs:
        file_path = doc.get("file_path", "")
        if file_path and os.path.exists(file_path):
            filename = doc.get("filename", os.path.basename(file_path))
            # Handle duplicate filenames by appending a counter
            if filename in seen_names:
                seen_names[filename] += 1
                name, ext = os.path.splitext(filename)
                filename = f"{name} ({seen_names[filename]}){ext}"
            else:
                seen_names[filename] = 0
            valid_docs.append({"file_path": file_path, "filename": filename})

    if not valid_docs:
        raise HTTPException(status_code=404, detail="No files available for download")

    def generate_zip():
        """Generate zip file in streaming chunks."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for doc in valid_docs:
                zf.write(doc["file_path"], doc["filename"])
        buffer.seek(0)
        # Yield in 1MB chunks
        while True:
            chunk = buffer.read(1024 * 1024)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="documents-{len(valid_docs)}-files.zip"',
        },
    )


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, auth: AuthResult = Depends(require_manage_permission)):
    """
    Delete a document and clean up orphaned entities and communities from the knowledge base.
    
    This endpoint will:
    1. Cancel any active processing tasks for the document
    2. Delete the document and its chunks
    3. Remove orphaned entities (only connected to this document)
    4. Remove orphaned communities (with no remaining members)
    """
    try:
        # Cancel any active processing first
        processor = get_document_processor()
        was_processing = await processor.cancel_document_processing(document_id)
        if was_processing:
            logger.info(f"Cancelled active processing for document {document_id} before deletion")
        
        # Then delete the document and clean up graph
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_document, document_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return {
            "message": "Document deleted successfully",
            "processing_cancelled": was_processing,
            "orphaned_entities_removed": result["orphaned_entities_removed"],
            "orphaned_communities_removed": result["orphaned_communities_removed"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/delete")
async def delete_documents(request: DeleteRequest, auth: AuthResult = Depends(require_manage_permission)):
    """
    Delete multiple documents from the knowledge base.
    
    This endpoint will:
    1. Cancel any active processing tasks for the documents
    2. Delete all specified documents and their chunks
    3. Remove orphaned entities and communities
    """
    try:
        # Cancel any active processing first
        processor = get_document_processor()
        cancelled_count = await processor.cancel_multiple_documents(request.document_ids)
        if cancelled_count > 0:
            logger.info(f"Cancelled {cancelled_count} active processing tasks before bulk deletion")
        
        # Then delete the documents and clean up graph
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_documents, request.document_ids)
        
        return {
            "message": f"Successfully deleted {result['deleted_count']} document(s)",
            "deleted_count": result["deleted_count"],
            "processing_cancelled": cancelled_count,
            "orphaned_entities_removed": result["orphaned_entities_removed"],
            "orphaned_communities_removed": result["orphaned_communities_removed"]
        }
    except Exception as e:
        logger.error(f"Error deleting documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents")
async def delete_all_documents(auth: AuthResult = Depends(require_manage_permission)):
    """
    Delete all documents from the knowledge base.
    
    WARNING: This is a destructive operation that removes all documents, chunks, entities, and communities.
    
    This endpoint will:
    1. Cancel ALL active processing tasks
    2. Delete all documents, chunks, entities, and communities
    """
    try:
        # Cancel all active processing first
        processor = get_document_processor()
        cancelled_count = await processor.cancel_all_processing()
        if cancelled_count > 0:
            logger.info(f"Cancelled {cancelled_count} active processing tasks before deleting all documents")
        
        # Then delete everything
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_all_documents)
        
        return {
            "message": f"Successfully deleted all {result['deleted_count']} document(s)",
            "deleted_count": result["deleted_count"],
            "processing_cancelled": cancelled_count,
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
    document = await asyncio.to_thread(neo4j.get_document, document_id)
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
                doc = await asyncio.to_thread(neo4j.get_document, doc_id)
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
        pending = await asyncio.to_thread(processor.get_pending_documents)
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
    Clean up orphaned entities and communities from the knowledge graph.

    Orphaned entities are those not connected to any document chunk.
    Orphaned communities are those with no member entities.
    This can happen from previous deletions or data inconsistencies.
    """
    try:
        neo4j = get_neo4j_service()
        entities_deleted = await asyncio.to_thread(neo4j.cleanup_orphaned_entities)
        communities_deleted = await asyncio.to_thread(neo4j.cleanup_orphaned_communities)
        return {
            "message": "Cleanup completed",
            "orphaned_entities_removed": entities_deleted,
            "orphaned_communities_removed": communities_deleted,
        }
    except Exception as e:
        logger.error(f"Error cleaning up orphaned entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest, auth: AuthResult = Depends(require_read_permission)):
    """
    Perform hybrid search on the knowledge base.
    
    Combines:
    - Semantic/vector search (finds similar meaning)
    - Keyword search (finds exact text matches in content)
    - Metadata search (finds matches in filename, topic hints)
    
    Uses Reciprocal Rank Fusion (RRF) to merge results from all sources.
    """
    try:
        processor = get_query_processor()
        
        # Use hybrid search to combine vector + keyword + metadata
        results = processor.hybrid_search(
            query=request.query,
            top_k=request.top_k
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
async def ask_question(request: RAGRequest, auth: AuthResult = Depends(require_read_permission)):
    """
    Ask a question using enhanced GraphRAG.
    
    Features:
    - Hybrid search with RRF (vector + keyword + graph)
    - Cross-encoder re-ranking for precision
    - Conversation memory for context
    - Agentic multi-step reasoning (optional)
    """
    try:
        settings = get_settings()
        processor = get_query_processor()

        # Convert conversation history if provided
        conversation_history = None
        if request.conversation_history:
            conversation_history = request.conversation_history

        # Use agent pipeline for agentic requests if enabled
        if request.use_agentic and settings.enable_agent_research:
            result = await processor.agent_rag_query(
                question=request.question,
                mode="quality",
                conversation_history=conversation_history,
                collection_id=request.collection_id,
            )

            # Build sources from agent result (already formatted)
            sources = [
                SearchResult(
                    document_id=s.get("document_id", ""),
                    chunk_id=s.get("chunk_id", ""),
                    content=s.get("content", ""),
                    score=s.get("score", 0),
                    metadata=s.get("metadata", {}),
                )
                for s in result.get("sources", [])
            ]

            graph_context = None
            if result.get("graph_context"):
                graph_context = GraphContext(**result["graph_context"])

            return RAGResponse(
                question=result["question"],
                answer=result["answer"],
                sources=sources,
                graph_context=graph_context,
                reranked=result.get("reranked", False),
                reasoning_steps=result.get("reasoning_steps"),
                communities_used=result.get("communities_used"),
                retrieval_stats=result.get("retrieval_stats"),
            )

        # Legacy path for non-agent requests
        result = await processor.rag_query(
            question=request.question,
            top_k=request.top_k,
            use_graph=request.use_graph,
            max_hops=request.max_hops,
            conversation_history=conversation_history,
            use_reranking=request.use_reranking,
            use_agentic=request.use_agentic,
            collection_id=request.collection_id
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
async def ask_question_stream(request: RAGRequest, auth: AuthResult = Depends(require_read_permission)):
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
    
    When use_fast_search=True:
    - Uses simple vector search only (no hybrid/reranking)
    - Fastest response time for quick queries
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=400, 
            detail="OpenAI API key required for streaming"
        )
    
    # Route to agentic streaming if deep research is enabled (not available with fast search)
    if request.use_agentic and settings.enable_agentic_rag and not request.use_fast_search:
        async def generate_agentic():
            try:
                processor = get_query_processor()

                # Use new agent-based pipeline if enabled, otherwise legacy
                if settings.enable_agent_research:
                    async for event in processor.agent_rag_stream(
                        question=request.question,
                        mode="quality",
                        conversation_history=request.conversation_history,
                        collection_id=request.collection_id
                    ):
                        yield f"data: {json.dumps(event)}\n\n"
                else:
                    async for event in processor.agentic_rag_stream(
                        question=request.question,
                        top_k=request.top_k,
                        max_hops=request.max_hops,
                        conversation_history=request.conversation_history,
                        collection_id=request.collection_id
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
    
    # Fast vector search mode - optimized for speed, no sources shown
    if request.use_fast_search:
        async def generate_fast():
            try:
                from openai import AsyncOpenAI
                
                # Validate user input for prompt injection (if enabled)
                processed_question, was_blocked, reason = validate_and_process_input(
                    request.question, strict_mode=True, enabled=settings.prompt_security
                )
                
                if was_blocked:
                    logger.warning(f"Blocked potential prompt injection: {reason}")
                    yield f"data: {json.dumps({'content': get_safe_refusal_message()})}\n\n"
                    yield f"data: {json.dumps({'done': True, 'fast_mode': True})}\n\n"
                    return
                
                processor = get_query_processor()
                
                # Check if this is a follow-up question (has conversation history)
                has_history = request.conversation_history and len(request.conversation_history) > 0
                
                system_prompt = """You are a helpful assistant. Be direct and concise. Do not mention sources or citations.
When there is conversation history, prioritize continuing that conversation naturally.
Important: You do not have access to any tools. Never output tool calls, function calls, or any special syntax. Just provide plain text answers.
Important: Do NOT include any thinking, reasoning, or internal monologue in your response. Do NOT use <think> tags or similar. Respond directly with the answer only.""" + get_anti_injection_instruction(enabled=settings.prompt_security)
                
                messages = [{"role": "system", "content": system_prompt}]
                
                # Include conversation history for continuity
                if has_history:
                    max_history = settings.max_conversation_history
                    for msg in request.conversation_history[-max_history:]:
                        messages.append({
                            "role": msg.role,
                            "content": msg.content
                        })
                    # For follow-up questions, just pass the question directly
                    # This allows natural conversation flow
                    messages.append({"role": "user", "content": request.question})
                else:
                    # First message - do vector search and include context
                    results = processor.search(request.question, top_k=request.top_k, collection_id=request.collection_id)
                    context = "\n\n".join([r['content'][:600] for r in results[:3]])
                    
                    if context:
                        prompt = f"""Reference information:
{context}

Question: {request.question}"""
                    else:
                        prompt = request.question
                    
                    messages.append({"role": "user", "content": prompt})
                
                # Use turbo mode config if active, otherwise default settings
                # For fast mode, use the fast mode model (OPENAI_MODEL_FAST_MODE)
                llm_config = get_llm_config(fast_mode=True)
                client = AsyncOpenAI(
                    api_key=llm_config.api_key,
                    base_url=llm_config.base_url,
                )
                
                # Build request kwargs
                request_kwargs = {
                    "model": llm_config.model,
                    "messages": messages,
                    "temperature": 0.2,  # Lower temperature for faster, more deterministic responses
                    "max_tokens": 600,   # Reduced for faster completion
                    "stream": True,
                }
                
                # Only add thinking-related params for models that support them
                model_lower = llm_config.model.lower()
                if "deepseek" in model_lower or "r1" in model_lower:
                    request_kwargs["extra_body"] = {
                        "enable_thinking": False,  # DeepSeek-R1 style
                        "reasoning_effort": "none",
                    }
                
                stream = await client.chat.completions.create(**request_kwargs)
                
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        yield f"data: {json.dumps({'content': content})}\n\n"

                yield f"data: {json.dumps({'done': True, 'fast_mode': True})}\n\n"
                
            except Exception as e:
                logger.error(f"Error in fast streaming RAG: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return StreamingResponse(
            generate_fast(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    
    # Standard streaming RAG — optionally uses speed mode agent pipeline
    async def generate():
        try:
            # Validate user input for prompt injection (if enabled)
            processed_question, was_blocked, reason = validate_and_process_input(
                request.question, strict_mode=True, enabled=settings.prompt_security
            )

            if was_blocked:
                logger.warning(f"Blocked potential prompt injection: {reason}")
                yield f"data: {json.dumps({'content': get_safe_refusal_message()})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            processor = get_query_processor()

            # Speed mode agent pipeline for standard chat (opt-in via config)
            if settings.enable_agent_chat:
                async for event in processor.agent_rag_stream(
                    question=request.question,
                    mode="speed",
                    conversation_history=request.conversation_history,
                    collection_id=request.collection_id
                ):
                    yield f"data: {json.dumps(event)}\n\n"
                return

            # Legacy standard streaming path (hybrid search + reranking + writer)
            from openai import AsyncOpenAI

            conversation_history = request.conversation_history

            graph_context = None

            if request.use_graph:
                search_result = await processor.graph_search_async(
                    request.question,
                    top_k=request.top_k * 2,
                    max_hops=request.max_hops,
                    use_hybrid_rrf=settings.enable_hybrid_search,
                    collection_id=request.collection_id
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
                results = processor.search(request.question, top_k=request.top_k * 2, collection_id=request.collection_id)

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

            # Use improved writer prompt from research_prompts module
            from app.services.research_prompts import get_writer_system_prompt, get_writer_user_prompt

            has_history = conversation_history and len(conversation_history) > 0
            anti_injection = get_anti_injection_instruction(enabled=settings.prompt_security)
            system_prompt = get_writer_system_prompt("speed", anti_injection)

            # Build messages with conversation history
            messages = [{"role": "system", "content": system_prompt}]

            if has_history:
                max_history = settings.max_conversation_history
                for msg in conversation_history[-max_history:]:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })

            prompt = get_writer_user_prompt(
                mode="speed",
                formatted_sources=formatted_sources,
                graph_context_str=graph_context_str,
                question=request.question,
                has_history=has_history,
            )
            messages.append({"role": "user", "content": prompt})

            # Stream the response using async client
            llm_config = get_llm_config()
            client = AsyncOpenAI(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
            )

            stream = await client.chat.completions.create(
                model=llm_config.model,
                messages=messages,
                temperature=0.3,
                max_tokens=settings.writer_max_tokens_speed,
                stream=True
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
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
    Get knowledge graph data for visualization.
    
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
        data = await asyncio.to_thread(neo4j.get_graph_visualization_data, limit, include_neighbors)
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
    Get an entity and all its relationships up to max_depth hops.
    
    This enables focused graph exploration from a specific entity,
    showing all connected entities and the relationships between them.
    """
    try:
        neo4j = get_neo4j_service()
        data = await asyncio.to_thread(neo4j.get_entity_relationships, entity_name, max_depth, limit)
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
    
    Endpoint for focused graph visualization of specific entities.
    If include_connections is True, also includes bridging entities that
    connect the specified entities (up to 2 hops apart).
    """
    try:
        neo4j = get_neo4j_service()
        data = await asyncio.to_thread(neo4j.get_graph_subgraph, entity_names, include_connections)
        return data
    except Exception as e:
        logger.error(f"Error getting graph subgraph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entities")
async def list_entities(
    entity_type: Optional[str] = Query(default=None, description="Filter by entity type"),
    limit: int = Query(default=50, ge=1, le=1000),
    skip: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None, description="Search in entity name and description"),
):
    """List entities in the knowledge graph with server-side pagination and search."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(
            neo4j.list_entities_paginated, skip, limit, search, entity_type
        )
        return result
    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity-types")
async def list_entity_types():
    """Get all distinct entity types."""
    try:
        neo4j = get_neo4j_service()
        types = await asyncio.to_thread(neo4j.get_entity_types)
        return {"types": types}
    except Exception as e:
        logger.error(f"Error listing entity types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/relationships")
async def list_relationships(
    rel_type: Optional[str] = Query(default=None, description="Filter by relationship type"),
    limit: int = Query(default=50, ge=1, le=1000),
    skip: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None, description="Search in source, target, description"),
):
    """List relationships with server-side pagination and search."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(
            neo4j.list_relationships_paginated, skip, limit, search, rel_type
        )
        return result
    except Exception as e:
        logger.error(f"Error listing relationships: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/relationship-types")
async def list_relationship_types():
    """Get all distinct relationship types."""
    try:
        neo4j = get_neo4j_service()
        types = await asyncio.to_thread(neo4j.get_relationship_types)
        return {"types": types}
    except Exception as e:
        logger.error(f"Error listing relationship types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity/{entity_name}")
async def get_entity_details(entity_name: str, max_hops: int = Query(default=2, ge=1, le=3)):
    """Get details about a specific entity and its relationships."""
    try:
        neo4j = get_neo4j_service()
        context = await asyncio.to_thread(neo4j.traverse_from_entities, [entity_name], max_hops, entity_paths_only=True)
        
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
        results = await asyncio.to_thread(neo4j.find_entities_by_name, [query])
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Entity Merge & Deduplication
# =========================================================================


class MergeEntitiesRequest(BaseModel):
    canonical: str = Field(..., description="Name of the entity to keep")
    merge: List[str] = Field(..., description="Names of entities to merge into canonical")


async def _generate_merged_description(canonical: str, all_names: List[str], entity_data: dict) -> Optional[str]:
    """Generate a combined description for merged entities using the main LLM."""
    from openai import AsyncOpenAI
    from app.services.llm_config import get_llm_config

    # Collect non-empty descriptions
    entries = []
    for name in all_names:
        data = entity_data.get(name, {})
        desc = data.get("description", "")
        etype = data.get("type", "")
        if desc:
            entries.append(f'- "{name}" ({etype}): {desc}')

    if not entries:
        return None
    # If only one description exists, just use it
    if len(entries) == 1:
        return entity_data[all_names[0]].get("description") or next(
            (entity_data[n].get("description") for n in all_names if entity_data.get(n, {}).get("description")), None
        )

    try:
        config = get_llm_config()
        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=30.0,
            max_retries=1,
        )
        prompt = (
            f'The following duplicate entities are being merged into one entity named "{canonical}".\n'
            f"Write a comprehensive unified description that preserves ALL specific details, "
            f"technical specifications, facts, and context from every description below. "
            f"Do not omit any concrete information (model numbers, specs, features, relationships, use cases). "
            f"Be thorough rather than brief — it is better to be complete than concise. "
            f"Output only the description text, nothing else.\n\n"
            + "\n".join(entries)
        )
        response = await client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1000,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception as e:
        logger.warning(f"Failed to generate merged description, falling back to longest: {e}")
        return None


@app.post("/api/entities/merge")
async def merge_entities(request: MergeEntitiesRequest):
    """Merge duplicate entities into a canonical entity."""
    try:
        neo4j = get_neo4j_service()

        # Collect descriptions from all entities for LLM merging
        all_names = [request.canonical] + request.merge
        entity_data = await asyncio.to_thread(neo4j.get_entity_descriptions, all_names)

        # Generate a combined description via LLM
        merged_description = await _generate_merged_description(
            request.canonical, all_names, entity_data
        )

        result = await asyncio.to_thread(
            neo4j.merge_entities, request.canonical, request.merge, merged_description
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error merging entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/merge-history")
async def get_merge_history(
    limit: int = Query(default=50, ge=1, le=500),
):
    """Get entity merge history."""
    try:
        neo4j = get_neo4j_service()
        history = await asyncio.to_thread(neo4j.get_merge_history, limit)
        return {"history": history, "total": len(history)}
    except Exception as e:
        logger.error(f"Error getting merge history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/duplicates")
async def suggest_duplicates(
    threshold: float = Query(default=0.75, ge=0.5, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Suggest duplicate entity groups for user review."""
    try:
        neo4j = get_neo4j_service()
        groups = await asyncio.to_thread(neo4j.suggest_duplicate_entities, threshold, limit)
        return {"groups": groups, "total_groups": len(groups)}
    except Exception as e:
        logger.error(f"Error suggesting duplicates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/status")
async def get_graph_status():
    """Get GraphRAG system status."""
    try:
        settings = get_settings()
        extractor = get_graph_extractor()
        neo4j = get_neo4j_service()
        stats = await asyncio.to_thread(neo4j.get_stats)
        
        return {
            "graph_extraction_enabled": settings.enable_graph_extraction,
            "llm_available": extractor.is_available,
            "model": settings.openai_model if extractor.is_available else None,
            "entity_count": stats.get("entity_count", 0),
            "relationship_count": stats.get("relationship_count", 0),
            "community_count": stats.get("community_count", 0),
            "collection_count": stats.get("collection_count", 0),
            "community_detection_enabled": settings.enable_community_detection,
            "graph_summarization_enabled": settings.enable_graph_summarization,
            "semantic_entity_resolution_enabled": settings.enable_semantic_entity_resolution,
            "collections_enabled": settings.enable_collections,
        }
    except Exception as e:
        logger.error(f"Error getting graph status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Collection Endpoints
# =============================================================================

@app.get("/api/collections")
async def list_collections():
    """List all collections."""
    try:
        neo4j = get_neo4j_service()
        collections = await asyncio.to_thread(neo4j.list_collections)
        return {"collections": collections, "total": len(collections)}
    except Exception as e:
        logger.error(f"Error listing collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/collections")
async def create_collection(request: CollectionCreate, auth: AuthResult = Depends(require_manage_permission)):
    """Create a new collection."""
    try:
        neo4j = get_neo4j_service()
        
        # Enforce collection limit
        settings = get_settings()
        if settings.max_collections > 0:
            stats = await asyncio.to_thread(neo4j.get_stats)
            if stats["collection_count"] >= settings.max_collections:
                raise HTTPException(
                    status_code=403,
                    detail=f"Collection limit reached (max: {settings.max_collections}). Delete existing collections or increase MAX_COLLECTIONS."
                )
        
        collection = await asyncio.to_thread(neo4j.create_collection, request.name, request.description)
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
        collection = await asyncio.to_thread(neo4j.get_collection, collection_id)
        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")
        return collection
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/collections/{collection_id}")
async def update_collection(collection_id: str, data: CollectionUpdate, auth: AuthResult = Depends(require_manage_permission)):
    """Update a collection's name and/or description."""
    try:
        if collection_id == "default" and data.name and data.name != "default":
            raise HTTPException(status_code=400, detail="Cannot rename the default collection")

        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.update_collection, collection_id, data.name, data.description)
        if not result:
            raise HTTPException(status_code=404, detail="Collection not found")

        collection = await asyncio.to_thread(neo4j.get_collection, collection_id)
        return collection
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: str, auth: AuthResult = Depends(require_manage_permission)):
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
        result = await asyncio.to_thread(neo4j.delete_collection, collection_id)
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
        success = await asyncio.to_thread(neo4j.add_document_to_collection, document_id, collection_id)
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
        result = await asyncio.to_thread(
            neo4j.move_documents_to_collection,
            request.document_ids, 
            request.target_collection_id,
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
        entities = await asyncio.to_thread(neo4j.get_collection_entities, collection_id, limit)
        return {"entities": entities, "total": len(entities)}
    except Exception as e:
        logger.error(f"Error getting collection entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Relationship Analysis Endpoints (Phase B)
# =============================================================================

async def _run_relationship_analysis_task(
    task_id: str,
    collection_id: Optional[str],
    scope: str,
    rebuild: bool = False,
) -> None:
    """Background task for relationship analysis with progress tracking."""
    try:
        processor = get_document_processor()
        neo4j = get_neo4j_service()

        # If rebuild mode, delete all existing relationships first
        if rebuild:
            update_task_progress(task_id, 0, 1, "Clearing existing relationships for full rebuild...")
            await asyncio.to_thread(neo4j.delete_all_relationships)

        # Count entities for progress
        entities = await asyncio.to_thread(
            neo4j.get_all_entities_for_collection, collection_id
        )
        total = len(entities)

        if total == 0:
            complete_task(task_id, {
                "relationships_discovered": 0,
                "entities_analyzed": 0,
                "message": "No entities found to analyze",
            })
            return

        update_task_progress(
            task_id, 0, total,
            f"Analyzing relationships between {total} entities...",
        )

        def progress_cb(current, total_count, msg):
            update_task_progress(task_id, current, total_count, msg)

        result = await processor.analyze_collection_relationships(
            collection_id=collection_id,
            scope=scope,
            progress_callback=progress_cb,
            rebuild=rebuild,
        )

        # Persist the analysis timestamp
        from datetime import datetime, timezone
        await asyncio.to_thread(
            neo4j.set_meta, "last_relationship_analysis_at", datetime.now(timezone.utc).isoformat()
        )

        complete_task(task_id, result)

    except Exception as e:
        logger.error(f"Error in relationship analysis task {task_id}: {e}")
        fail_task(task_id, str(e))


@app.post("/api/graph/relationships/analyze")
async def analyze_relationships(
    background_tasks: BackgroundTasks,
    collection_id: Optional[str] = Query(
        default=None, description="Scope to a specific collection (None = global)"
    ),
    scope: str = Query(
        default="full", description="'recent' for new entities, 'full' for all"
    ),
    rebuild: bool = Query(
        default=False, description="Delete all existing relationships before analysis"
    ),
):
    """Analyze relationships between entities across documents.

    This triggers Phase B of the extraction pipeline: the main (large) model
    analyzes all entities and discovers cross-document relationships.
    Run this after batch document processing to build the relationship graph.

    Optionally followed by community detection: POST /api/graph/communities/detect
    """
    try:
        settings = get_settings()
        if not settings.enable_graph_extraction:
            raise HTTPException(status_code=400, detail="Graph extraction is disabled")

        task = create_task("relationship_analysis")
        task.message = "Starting relationship analysis..." if not rebuild else "Starting full rebuild..."

        background_tasks.add_task(
            _run_relationship_analysis_task,
            task.task_id,
            collection_id,
            scope,
            rebuild,
        )

        return {
            "task_id": task.task_id,
            "status": task.status,
            "message": f"Relationship analysis started. Poll /api/tasks/{task.task_id} for progress.",
            "tip": "Run POST /api/graph/communities/detect after this completes for community detection.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/graph/relationships")
async def delete_all_relationships():
    """Delete ALL relationships between entities."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_all_relationships)
        return result
    except Exception as e:
        logger.error(f"Error deleting all relationships: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/graph/entities")
async def delete_all_entities():
    """Delete ALL entities and their connections."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_all_entities)
        return result
    except Exception as e:
        logger.error(f"Error deleting all entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Community Detection Endpoints
# =============================================================================

@app.get("/api/graph/communities")
async def list_communities(
    limit: int = Query(default=50, ge=1, le=1000),
    skip: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None, description="Search in community name, summary, and entities"),
):
    """List all detected communities with server-side pagination and search."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(
            neo4j.list_communities_paginated, skip, limit, search
        )
        return result
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
        
        # Step 0: Clean up previous communities to avoid stale data
        update_task_progress(task_id, 0, 1, "Cleaning up previous communities...")
        await asyncio.to_thread(neo4j.delete_all_communities)

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
        
        # Persist the detection timestamp
        from datetime import datetime, timezone
        await asyncio.to_thread(
            neo4j.set_meta, "last_community_detection_at", datetime.now(timezone.utc).isoformat()
        )

        # Compute community distribution stats for diagnostics
        sizes = [c.get("entity_count", 0) for c in communities]
        total_entities = sum(sizes)
        distribution_stats = {}
        if sizes:
            sorted_sizes = sorted(sizes)
            distribution_stats = {
                "community_count": len(communities),
                "total_entities_covered": total_entities,
                "min_size": sorted_sizes[0],
                "max_size": sorted_sizes[-1],
                "median_size": sorted_sizes[len(sorted_sizes) // 2],
                "mean_size": round(total_entities / len(communities), 1),
            }

            if sorted_sizes[-1] > total_entities * 0.5:
                logger.warning(
                    f"Community detection: largest community contains >50% of entities "
                    f"({sorted_sizes[-1]}/{total_entities})"
                )
            min_size_count = sum(1 for s in sizes if s == min_size)
            if min_size_count > len(sizes) * 0.8:
                logger.warning(
                    f"Community detection: >80% of communities are at minimum size — "
                    f"consider lowering min_size or adjusting algorithm parameters"
                )

        complete_task(task_id, {
            "communities": communities,
            "total": len(communities),
            "collection_id": collection_id,
            "distribution": distribution_stats,
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


@app.delete("/api/graph/communities/{community_id}")
async def delete_community(community_id: int):
    """Delete a specific community. Entities are unlinked but not deleted."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_community, community_id)
        if not result.get("deleted"):
            raise HTTPException(status_code=404, detail="Community not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting community {community_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/graph/communities")
async def delete_all_communities():
    """Delete ALL communities. Entities are unlinked but not deleted."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_all_communities)
        return result
    except Exception as e:
        logger.error(f"Error deleting all communities: {e}")
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
        results = await asyncio.to_thread(neo4j.search_communities_by_content, query, limit)
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching communities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Extended Thinking / Streaming Agentic RAG
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
    
    This provides extended thinking where users can see
    the agent's reasoning process in real-time.
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key required for streaming")
    
    async def generate():
        try:
            processor = get_query_processor()

            # Use new agent pipeline if enabled, otherwise legacy
            if settings.enable_agent_research:
                async for event in processor.agent_rag_stream(
                    question=request.question,
                    mode="quality",
                    conversation_history=request.conversation_history,
                    collection_id=request.collection_id
                ):
                    yield f"data: {json.dumps(event)}\n\n"
            else:
                async for event in processor.agentic_rag_stream(
                    question=request.question,
                    top_k=request.top_k,
                    max_hops=request.max_hops,
                    conversation_history=request.conversation_history,
                    collection_id=request.collection_id
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


# =============================================================================
# Turbo Mode Endpoints (Compute3 GPU Acceleration)
# =============================================================================

@app.get("/api/turbo/status")
async def get_turbo_status():
    """
    Get Turbo Mode status.
    
    Returns whether Turbo Mode is available (API key configured),
    currently active (GPU job running), ready (vLLM server responding),
    and job details if active.
    
    IMPORTANT: This endpoint never exposes the actual API key.
    
    Fields:
    - available: True if COMPUTE3_API_KEY is configured
    - active: True if a GPU job is running
    - ready: True if the vLLM inference server is ready for requests
    - job: Details of the active job (if any)
    - config: GPU configuration settings
    """
    try:
        settings = get_settings()
        c3 = get_compute3_service()
        
        # Check for active job (this also checks vLLM readiness)
        active_job = await c3.get_active_turbo_job()
        
        is_running = active_job is not None and active_job.is_running
        is_ready = active_job is not None and active_job.is_ready
        
        return {
            "available": c3.is_available,
            "active": is_running,  # GPU job is running
            "ready": is_ready,     # vLLM server is ready for inference
            "job": active_job.to_dict() if active_job else None,
            "config": {
                "gpu_type": settings.compute3_gpu_type,
                "gpu_count": settings.compute3_gpu_count,
                "model": settings.compute3_model,
                "default_runtime": settings.compute3_default_runtime,
            } if c3.is_available else None,
        }
    except Exception as e:
        logger.error(f"Error getting turbo status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/turbo/balance")
async def get_turbo_balance():
    """Get Compute3 account balance."""
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        balance = await c3.get_balance()
        
        # Check for error in response
        if "error" in balance:
            return {"error": balance["error"]}
        
        # Transform Compute3 API response to frontend-expected format
        # Compute3 returns string values, convert to floats
        try:
            total = float(balance.get("total_balance", 0))
            available = float(balance.get("available_balance", 0))
            reserved = float(balance.get("pending_reservations", 0))
        except (ValueError, TypeError):
            total = 0.0
            available = 0.0
            reserved = 0.0
        
        return {
            "total": total,
            "available": available,
            "reserved": reserved,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting turbo balance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/turbo/start")
async def start_turbo_mode(
    runtime: Optional[int] = Query(default=None, ge=60, le=86400, description="Runtime in seconds (1 min to 24 hours)"),
    gpu_type: Optional[str] = Query(default=None, description="GPU type (h100, a100, l40s, etc.)"),
    gpu_count: Optional[int] = Query(default=None, ge=1, le=8, description="Number of GPUs (1-8)"),
):
    """
    Start Turbo Mode by launching a GPU job on Compute3.
    
    This creates a high-performance vLLM inference server on dedicated GPUs
    for faster document processing and LLM queries.
    
    Default configuration:
    - Model: minimax-m21
    - GPUs: 4 x H100
    - Runtime: 1 hour
    """
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        # Check if already running
        active_job = await c3.get_active_turbo_job()
        if active_job and active_job.is_running:
            return {
                "message": "Turbo Mode already active",
                "job": active_job.to_dict(),
            }
        
        # Create new turbo job
        job = await c3.create_turbo_job(
            runtime=runtime,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
        )
        
        if not job:
            raise HTTPException(status_code=500, detail="Failed to create Turbo Mode job")
        
        return {
            "message": "Turbo Mode starting",
            "job": job.to_dict(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting turbo mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/turbo/stop")
async def stop_turbo_mode(
    job_id: Optional[str] = Query(default=None, description="Specific job ID to stop (optional)")
):
    """
    Stop Turbo Mode by cancelling the active GPU job.
    
    If job_id is not specified, stops the currently active turbo mode job.
    """
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        # Get job to cancel
        if job_id:
            target_job_id = job_id
        else:
            active_job = await c3.get_active_turbo_job()
            if not active_job:
                return {"message": "No active Turbo Mode job to stop"}
            target_job_id = active_job.job_id
        
        success = await c3.cancel_job(target_job_id)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to cancel job {target_job_id}")
        
        return {
            "message": "Turbo Mode stopped",
            "job_id": target_job_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping turbo mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/turbo/extend")
async def extend_turbo_mode(
    additional_seconds: int = Query(..., ge=60, le=86400, description="Additional runtime in seconds"),
    job_id: Optional[str] = Query(default=None, description="Specific job ID to extend (optional)")
):
    """
    Extend the runtime of an active Turbo Mode job.
    """
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        # Get job to extend
        if job_id:
            target_job_id = job_id
        else:
            active_job = await c3.get_active_turbo_job()
            if not active_job:
                raise HTTPException(status_code=404, detail="No active Turbo Mode job to extend")
            target_job_id = active_job.job_id
        
        job = await c3.extend_job(target_job_id, additional_seconds)
        
        if not job:
            raise HTTPException(status_code=500, detail=f"Failed to extend job {target_job_id}")
        
        return {
            "message": f"Extended Turbo Mode by {additional_seconds} seconds",
            "job": job.to_dict(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extending turbo mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/turbo/jobs")
async def list_turbo_jobs(
    state: Optional[str] = Query(default=None, description="Filter by state (running, pending, succeeded, failed, canceled)")
):
    """
    List all Turbo Mode jobs (current and historical).
    """
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        jobs = await c3.list_jobs(state=state)
        
        # Filter to only vLLM jobs (turbo mode jobs)
        turbo_jobs = [j for j in jobs if "vllm" in j.docker_image.lower()]
        
        return {
            "jobs": [j.to_dict() for j in turbo_jobs],
            "total": len(turbo_jobs),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing turbo jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/turbo/jobs/{job_id}")
async def get_turbo_job(job_id: str):
    """Get details of a specific Turbo Mode job."""
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        job = await c3.get_job(job_id)
        
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        return job.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting turbo job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/turbo/jobs/{job_id}/logs")
async def get_turbo_job_logs(job_id: str):
    """Get logs from a Turbo Mode job."""
    try:
        c3 = get_compute3_service()
        
        if not c3.is_available:
            raise HTTPException(status_code=400, detail="Turbo Mode not available - COMPUTE3_API_KEY not configured")
        
        logs = await c3.get_job_logs(job_id)
        
        return {"job_id": job_id, "logs": logs}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting turbo job logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Admin System Configuration Endpoint
# =============================================================================

@app.get("/api/admin/config", response_model=SystemConfigResponse)
async def get_system_config(auth: AuthResult = Depends(require_admin)):
    """
    Get system configuration (safe settings only).
    
    Admin-only endpoint. Returns current system configuration excluding
    sensitive data like API keys, passwords, and secrets.
    """
    settings = get_settings()
    
    return SystemConfigResponse(
        # LLM Configuration
        openai_model=settings.openai_model,
        openai_api_base=settings.openai_api_base,
        extraction_model=settings.extraction_model,
        extraction_api_base=settings.extraction_api_base,
        extraction_max_context=settings.extraction_max_context,
        relationship_max_context=settings.relationship_max_context,
        parallel_relationship_batches=settings.parallel_relationship_batches,
        relationship_target_ratio=settings.relationship_target_ratio,
        relationship_max_rounds=settings.relationship_max_rounds,
        relationship_max_hours=settings.relationship_max_hours,

        # Vision Model
        vision_model_available=settings.vision_model_available,
        vision_model=settings.vision_model or "Not configured",
        vision_api_base=settings.vision_model_api_base or settings.openai_api_base,
        vision_max_concurrent=settings.vision_max_concurrent,

        # Embedding Configuration
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        embedding_api_base=settings.embed_api_base,
        embedding_send_dimensions=settings.embedding_send_dimensions,
        use_openai_embeddings=settings.use_openai_embeddings,
        
        # Upload Configuration
        max_file_size_mb=settings.max_file_size_mb,
        allowed_extensions=settings.allowed_extensions,
        
        # Chunking Configuration
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        chunk_by=settings.chunk_by,
        sentences_per_chunk=settings.sentences_per_chunk,
        
        # GraphRAG Configuration
        enable_graph_extraction=settings.enable_graph_extraction,
        max_graph_hops=settings.max_graph_hops,
        concurrent_extractions=settings.concurrent_extractions,
        
        # Batch Processing
        batch_processing_concurrency=settings.batch_processing_concurrency,
        processing_thread_workers=settings.processing_thread_workers,
        
        # Enhanced RAG Configuration
        enable_reranking=settings.enable_reranking,
        reranking_model=settings.reranking_model,
        enable_hybrid_search=settings.enable_hybrid_search,
        vector_weight=settings.vector_weight,
        keyword_weight=settings.keyword_weight,
        graph_weight=settings.graph_weight,
        max_conversation_history=settings.max_conversation_history,
        enable_agentic_rag=settings.enable_agentic_rag,
        max_agentic_steps=settings.max_agentic_steps,
        
        # Community Detection
        enable_community_detection=settings.enable_community_detection,
        min_community_size=settings.min_community_size,
        max_communities=settings.max_communities,
        enable_graph_summarization=settings.enable_graph_summarization,
        
        # Entity Resolution
        enable_semantic_entity_resolution=settings.enable_semantic_entity_resolution,
        entity_similarity_threshold=settings.entity_similarity_threshold,
        
        # Collections
        enable_collections=settings.enable_collections,
        default_collection=settings.default_collection,
        
        # Visibility/UX
        stream_reasoning_steps=settings.stream_reasoning_steps,
        show_retrieval_stats=settings.show_retrieval_stats,
        
        # Security
        prompt_security=settings.prompt_security,
        
        # Turbo Mode (Compute3)
        turbo_mode_available=settings.turbo_mode_available,
        compute3_gpu_type=settings.compute3_gpu_type,
        compute3_gpu_count=settings.compute3_gpu_count,
        compute3_model=settings.compute3_model,
        compute3_default_runtime=settings.compute3_default_runtime,
    )


# =============================================================================
# Admin API Key Management Endpoints
# =============================================================================

@app.get("/api/admin/api-keys", response_model=List[APIKeyListItem])
async def list_api_keys(auth: AuthResult = Depends(require_admin)):
    """
    List all API keys.
    
    Admin-only endpoint. Returns API key information without the actual keys.
    """
    try:
        api_key_service = get_api_key_service()
        keys = await asyncio.to_thread(api_key_service.list_api_keys)
        return keys
    except Exception as e:
        logger.error(f"Error listing API keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/api-keys", response_model=CreateAPIKeyResponse)
async def create_api_key(request: CreateAPIKeyRequest, auth: AuthResult = Depends(require_admin)):
    """
    Create a new API key.
    
    Admin-only endpoint. The actual API key is returned only once in this response.
    Make sure to save it securely as it cannot be retrieved again.
    """
    try:
        api_key_service = get_api_key_service()
        result = api_key_service.create_api_key(
            name=request.name,
            permissions=request.permissions,
            created_by="admin"
        )
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create API key")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: This route MUST come before the {key_id} routes to avoid path parameter conflicts
@app.get("/api/admin/api-keys/with-stats", response_model=List[APIKeyWithStats])
async def list_api_keys_with_stats(auth: AuthResult = Depends(require_admin)):
    """
    List all API keys with their usage statistics.
    
    Admin-only endpoint. Returns keys with embedded stats including:
    - Total requests
    - Requests today/this week/this month
    - Error counts
    - Endpoint breakdown
    """
    try:
        usage_service = get_api_usage_service()
        return await asyncio.to_thread(usage_service.list_keys_with_stats)
    except Exception as e:
        logger.error(f"Error listing API keys with stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/api-keys/{key_id}", response_model=APIKeyListItem)
async def get_api_key(key_id: str, auth: AuthResult = Depends(require_admin)):
    """
    Get a specific API key by ID.
    
    Admin-only endpoint. Returns API key information without the actual key.
    """
    try:
        api_key_service = get_api_key_service()
        key = api_key_service.get_api_key(key_id)
        
        if not key:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return key
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/admin/api-keys/{key_id}", response_model=APIKeyListItem)
async def update_api_key(
    key_id: str,
    request: UpdateAPIKeyRequest,
    auth: AuthResult = Depends(require_admin)
):
    """
    Update an API key's name, permissions, or active status.
    
    Admin-only endpoint.
    Note: The system admin key cannot be disabled.
    """
    # Protect the admin key from being disabled
    if key_id == "admin":
        if request.is_active is False:
            raise HTTPException(
                status_code=403,
                detail="Cannot disable the system admin key - it is protected"
            )
        # Admin key can't be modified through this endpoint
        raise HTTPException(
            status_code=403,
            detail="The system admin key cannot be modified through this endpoint"
        )
    
    try:
        api_key_service = get_api_key_service()
        result = api_key_service.update_api_key(key_id, request)
        
        if not result:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/api-keys/{key_id}")
async def delete_api_key(key_id: str, auth: AuthResult = Depends(require_admin)):
    """
    Delete an API key permanently.
    
    Admin-only endpoint.
    Note: The system admin key cannot be deleted.
    """
    # Protect the admin key from deletion
    if key_id == "admin":
        raise HTTPException(
            status_code=403,
            detail="Cannot delete the system admin key - it is protected"
        )
    
    try:
        api_key_service = get_api_key_service()
        success = api_key_service.delete_api_key(key_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return {"message": "API key deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/api-keys/{key_id}/revoke", response_model=APIKeyListItem)
async def revoke_api_key(key_id: str, auth: AuthResult = Depends(require_admin)):
    """
    Revoke an API key (deactivate without deleting).
    
    Admin-only endpoint. The key can be reactivated later.
    Note: The system admin key cannot be revoked.
    """
    # Protect the admin key from being revoked
    if key_id == "admin":
        raise HTTPException(
            status_code=403,
            detail="Cannot revoke the system admin key - it is protected"
        )
    
    try:
        api_key_service = get_api_key_service()
        result = api_key_service.revoke_api_key(key_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/api-keys/{key_id}/activate", response_model=APIKeyListItem)
async def activate_api_key(key_id: str, auth: AuthResult = Depends(require_admin)):
    """
    Reactivate a revoked API key.
    
    Admin-only endpoint.
    """
    try:
        api_key_service = get_api_key_service()
        result = api_key_service.activate_api_key(key_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error activating API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# API Key Statistics Endpoints
# =============================================================================

@app.get("/api/admin/api-keys/{key_id}/stats", response_model=APIKeyStats)
async def get_api_key_stats(key_id: str, auth: AuthResult = Depends(require_admin)):
    """
    Get detailed usage statistics for a specific API key.
    
    Admin-only endpoint. Returns:
    - Total requests all time
    - Requests today, this week, this month
    - Error count and last error details
    - Endpoint usage breakdown
    """
    try:
        usage_service = get_api_usage_service()
        stats = usage_service.get_key_stats(key_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting API key stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/api-keys/{key_id}/usage-history", response_model=APIKeyUsageHistoryResponse)
async def get_api_key_usage_history(
    key_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days of history"),
    auth: AuthResult = Depends(require_admin)
):
    """
    Get daily usage history for an API key.
    
    Admin-only endpoint. Returns daily request and error counts
    for the specified number of days (default 30, max 365).
    Useful for charting usage trends.
    """
    try:
        usage_service = get_api_usage_service()
        history = usage_service.get_key_usage_history(key_id, days)
        
        if not history:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return history
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting API key usage history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/stats/overview", response_model=AdminStatsOverview)
async def get_admin_stats_overview(auth: AuthResult = Depends(require_admin)):
    """
    Get aggregated statistics across all API keys.
    
    Admin-only endpoint. Returns dashboard-level metrics:
    - Total and active key counts
    - Total requests today/this week/this month/all time
    - Total errors
    - Most active key
    - Aggregated endpoint breakdown
    """
    try:
        usage_service = get_api_usage_service()
        return usage_service.get_admin_overview()
    except Exception as e:
        logger.error(f"Error getting admin stats overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# System Reset Endpoint
# =============================================================================

@app.post("/api/admin/reset", response_model=SystemResetResponse)
async def reset_system(request: SystemResetRequest, auth: AuthResult = Depends(require_admin)):
    """
    Reset the system by deleting selected data.
    
    WARNING: This is a destructive operation that cannot be undone.
    
    Admin-only endpoint. Allows selective deletion of:
    - Documents (includes chunks, entities, relationships, communities)
    - Uploaded files from disk
    - Custom input files from disk
    - Collections (except default)
    - API keys
    """
    settings = get_settings()
    neo4j = get_neo4j_service()
    processor = get_document_processor()
    
    result = SystemResetResponse(
        message="",
        documents_deleted=0,
        entities_removed=0,
        communities_removed=0,
        merge_history_deleted=0,
        system_meta_deleted=0,
        collections_deleted=0,
        api_keys_deleted=0,
        uploaded_files_deleted=0,
        custom_inputs_deleted=0,
        processing_cancelled=0
    )
    
    try:
        # Step 1: Cancel all active processing tasks first
        cancelled_count = await processor.cancel_all_processing()
        result.processing_cancelled = cancelled_count
        if cancelled_count > 0:
            logger.info(f"System reset: Cancelled {cancelled_count} active processing tasks")
        
        # Step 2: Delete documents from Neo4j (if requested)
        if request.delete_documents:
            doc_result = await asyncio.to_thread(neo4j.delete_all_documents)
            result.documents_deleted = doc_result.get("deleted_count", 0)
            result.entities_removed = doc_result.get("entities_removed", 0)
            result.communities_removed = doc_result.get("communities_removed", 0)
            logger.info(f"System reset: Deleted {result.documents_deleted} documents, "
                       f"{result.entities_removed} entities, {result.communities_removed} communities")

            # Also clean up merge history and system metadata (tied to knowledge graph)
            result.merge_history_deleted = await asyncio.to_thread(neo4j.delete_all_merge_history)
            result.system_meta_deleted = await asyncio.to_thread(neo4j.delete_all_system_meta)
            if result.merge_history_deleted > 0:
                logger.info(f"System reset: Deleted {result.merge_history_deleted} merge history records")
            if result.system_meta_deleted > 0:
                logger.info(f"System reset: Deleted {result.system_meta_deleted} system metadata records")
        
        # Step 3: Delete uploaded files from disk (if requested)
        if request.delete_uploaded_files:
            upload_dir = Path(settings.upload_dir)
            if upload_dir.exists():
                files_deleted = 0
                for file_path in upload_dir.iterdir():
                    if file_path.is_file():
                        try:
                            file_path.unlink()
                            files_deleted += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete file {file_path}: {e}")
                result.uploaded_files_deleted = files_deleted
                logger.info(f"System reset: Deleted {files_deleted} uploaded files")
        
        # Step 4: Delete custom input files from disk (if requested)
        if request.delete_custom_inputs:
            custom_inputs_dir = Path(settings.custom_inputs_dir)
            if custom_inputs_dir.exists():
                files_deleted = 0
                for file_path in custom_inputs_dir.iterdir():
                    if file_path.is_file():
                        try:
                            file_path.unlink()
                            files_deleted += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete file {file_path}: {e}")
                result.custom_inputs_deleted = files_deleted
                logger.info(f"System reset: Deleted {files_deleted} custom input files")
        
        # Step 5: Delete collections (if requested)
        if request.delete_collections:
            result.collections_deleted = await asyncio.to_thread(neo4j.delete_all_collections)
            logger.info(f"System reset: Deleted {result.collections_deleted} collections")
        
        # Step 6: Delete API keys (if requested - dangerous!)
        if request.delete_api_keys:
            result.api_keys_deleted = await asyncio.to_thread(neo4j.delete_all_api_keys)
            logger.info(f"System reset: Deleted {result.api_keys_deleted} API keys")
        
        # Build summary message
        parts = []
        if result.documents_deleted > 0:
            parts.append(f"{result.documents_deleted} documents")
        if result.entities_removed > 0:
            parts.append(f"{result.entities_removed} entities")
        if result.communities_removed > 0:
            parts.append(f"{result.communities_removed} communities")
        if result.merge_history_deleted > 0:
            parts.append(f"{result.merge_history_deleted} merge history records")
        if result.collections_deleted > 0:
            parts.append(f"{result.collections_deleted} collections")
        if result.api_keys_deleted > 0:
            parts.append(f"{result.api_keys_deleted} API keys")
        if result.uploaded_files_deleted > 0:
            parts.append(f"{result.uploaded_files_deleted} uploaded files")
        if result.custom_inputs_deleted > 0:
            parts.append(f"{result.custom_inputs_deleted} custom inputs")
        
        if parts:
            result.message = f"System reset complete. Deleted: {', '.join(parts)}."
        else:
            result.message = "No items were selected for deletion."
        
        if result.processing_cancelled > 0:
            result.message += f" Cancelled {result.processing_cancelled} active processing tasks."
        
        logger.info(f"System reset completed: {result.message}")
        return result
        
    except Exception as e:
        logger.error(f"Error during system reset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
