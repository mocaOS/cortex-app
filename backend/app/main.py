"""Cortex - FastAPI Backend."""

import os
import logging
import asyncio
import time
import uuid
import shutil
import glob
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, List
from urllib.parse import urlparse

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field
import aiofiles
import httpx
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
    InstanceStatusResponse,
    RunningTaskSummary,
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
    CollectionScope,
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
    # Agent Skills models
    SkillInstallRequest,
    SkillUpdateRequest,
    SkillConfigSaveRequest,
    UpdateEntityRequest,
    # Git integration models
    GitConnectionCreate,
    GitConnectionUpdate,
    GitConnectionResponse,
    GitConnectionVerifyRequest,
    GitVerifyResponse,
    GitRepoBrowseItem,
    GitSyncTriggerResponse,
    # MDHarvest powered by Crawl4ai models
    WebImportRequest,
    WebImportResponse,
    WebDiscoverRequest,
    WebDiscoverResponse,
    WebDiscoverLink,
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
from app.services.llm_config import get_llm_config, build_chat_params, make_async_openai_client, stream_usage_kwargs
from app.services.observability import traced_sse
from app.services.reasoning_config import safe_chat_completion, ReasoningMode
from app.services.auth_service import (
    require_api_key,
    require_read_permission,
    require_manage_permission,
    require_admin,
    AuthResult,
    validate_collection_access,
)
from app.services.api_key_service import get_api_key_service
from app.services.api_usage_service import get_api_usage_service
from app.services.crypto_service import get_crypto_service, migrate_secrets_at_rest

# Configure logging (LOG_FORMAT=plain keeps the legacy format byte-identical;
# LOG_FORMAT=json emits one JSON object per line with request_id correlation)
from app.logging_setup import (
    configure as _configure_logging,
    get_request_id,
    new_request_id,
    set_request_id,
)
from app import metrics

_configure_logging(getattr(get_settings(), "log_format", "plain"))
# Suppress Neo4j notification warnings about missing properties/relationships
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)


_HEARTBEAT_DONE = object()

# Set by the lifespan cleanup when the process is going down. SSE generators
# watch it between chunks: instead of a dead socket mid-answer, clients get a
# terminal `event: shutdown` frame and can reconnect to the replacement
# instance.
SHUTTING_DOWN = asyncio.Event()


def sse_error_frame(exc: Exception, *, context: str = "answer") -> str:
    """Build a client-safe SSE error frame.

    The raw exception text can contain internal URLs, provider error bodies,
    connection strings, or stack-trace fragments — never send `str(exc)` to the
    browser. We log the full exception server-side (already done by callers) and
    return a generic, actionable message tagged with the request id so support
    can correlate it to the server logs.
    """
    rid = get_request_id()
    message = (
        f"The assistant hit an error generating this {context}. Please try again."
    )
    if rid:
        message += f" (reference: {rid})"
    return f"data: {json.dumps({'error': message})}\n\n"


async def with_sse_heartbeat(gen, interval: float = 8.0):
    """Wrap an SSE string generator, injecting `: ping` comment lines during
    silent windows so proxies/load balancers don't idle-timeout and the client
    can tell "still working" from "connection died". Comment lines are ignored by
    the SSE spec and by clients, so this is additive and safe.

    Races the wrapped generator against a timer; on each `interval` of silence it
    emits one keep-alive. Cancels the pump if the client disconnects. On process
    shutdown it emits a terminal `event: shutdown` frame and closes the stream.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump():
        try:
            async for chunk in gen:
                await queue.put(chunk)
        except Exception as e:  # surface as an error event, then end
            logger.error("Error in SSE stream pump: %s", e, exc_info=True)
            await queue.put(sse_error_frame(e))
        finally:
            await queue.put(_HEARTBEAT_DONE)

    task = asyncio.create_task(_pump())
    from app import metrics as _metrics
    _metrics.SSE_ACTIVE_STREAMS.inc()
    try:
        while True:
            if SHUTTING_DOWN.is_set():
                yield "event: shutdown\ndata: {\"reason\": \"server restarting\"}\n\n"
                break
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if chunk is _HEARTBEAT_DONE:
                break
            yield chunk
    finally:
        _metrics.SSE_ACTIVE_STREAMS.dec()
        task.cancel()


_api_executor: Optional[ThreadPoolExecutor] = None


async def _reranker_idle_reaper():
    """Periodically unload the local cross-encoder if it's been idle past its TTL.

    Reclaims ~1 GB on low-traffic tenant stacks; the model reloads on the next
    query. No-op when reranking is remote/disabled or TTL is 0 (never unload).
    """
    settings = get_settings()
    ttl = settings.reranker_idle_ttl_seconds
    if ttl <= 0 or settings.reranker_service_url or not settings.enable_reranking:
        return
    check_interval = max(30, min(ttl, 300))
    while True:
        await asyncio.sleep(check_interval)
        try:
            get_query_processor().maybe_unload_reranker()
        except Exception as e:
            logger.debug(f"Reranker reaper: {e}")


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

    # Start the reranker idle-unload reaper (no-op in remote/disabled/TTL=0 mode)
    reranker_reaper_task = asyncio.create_task(_reranker_idle_reaper())
    
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

    # Recover documents orphaned mid-processing by a previous shutdown/crash.
    # Processing runs as in-process background tasks, so anything left in a
    # transient state at startup can never resume on its own — it would spin
    # forever in the UI and keep `/api/instance/status` permanently
    # unsafe-to-redeploy. Reset them to 'pending' so they rejoin the queue.
    try:
        reset_ids = neo4j.reset_orphaned_processing_documents()
        if reset_ids:
            logger.warning(
                "Reset %d document(s) stranded mid-processing by a prior "
                "shutdown back to 'pending': %s",
                len(reset_ids),
                ", ".join(reset_ids[:10]) + ("…" if len(reset_ids) > 10 else ""),
            )
    except Exception as e:
        logger.warning(f"Could not reconcile orphaned processing documents: {e}")
    
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
    
    # Discover agent skills
    if settings.enable_skills:
        try:
            from app.services.skill_service import get_skill_service
            skill_service = get_skill_service()
            count = skill_service.discover_local_skills()
            logger.info(f"Agent skills discovered: {count}")
        except Exception as e:
            logger.warning(f"Could not discover agent skills: {e}")

    # At-rest secret encryption: status banner + idempotent migration of any
    # plaintext (or rotated-key) git PATs and skill secret fields.
    # Runs after skill discovery so skill nodes/schemas exist.
    crypto = get_crypto_service()  # raises on malformed ENCRYPTION_KEY (fail fast)
    crypto.log_startup_status()
    if crypto.is_enabled():
        try:
            await asyncio.to_thread(migrate_secrets_at_rest)
        except Exception as e:
            logger.warning(f"Secret encryption migration failed: {e}")

    # Initialize Langfuse observability (no-op unless LANGFUSE_* creds are set).
    # Done before processor warm-up so the global client is registered before
    # any traced LLM call can fire.
    try:
        from app.services.observability import init_langfuse
        init_langfuse()
    except Exception as e:
        logger.warning(f"Langfuse init failed; continuing untraced: {e}")

    # Web crawl: crawl4ai >= 0.9.0 requires an API token — without one it binds
    # its API to 127.0.0.1 only, so a cross-container/shared deployment can't
    # reach it. Warn loudly rather than fail (older tokenless crawl4ai or a
    # same-host loopback URL is still valid).
    if settings.enable_web_crawl and settings.crawl_service_url and not settings.crawl_service_token:
        logger.warning(
            "ENABLE_WEB_CRAWL is on with CRAWL_SERVICE_URL=%s but no "
            "CRAWL_SERVICE_TOKEN set. crawl4ai >= 0.9.0 requires an API token "
            "(CRAWL4AI_API_TOKEN) and serves its API only on 127.0.0.1 without "
            "one — Web Import will fail unless crawl4ai is reachable tokenless. "
            "Set CRAWL_SERVICE_TOKEN to match crawl4ai's CRAWL4AI_API_TOKEN.",
            settings.crawl_service_url,
        )

    # Start the git scheduled-sync poller
    git_scheduler_task = None
    if settings.enable_git_integration:
        os.makedirs(settings.git_work_dir, exist_ok=True)
        git_scheduler_task = asyncio.create_task(_git_sync_scheduler())
        logger.info("Git integration enabled; scheduled-sync poller started")

    # Warm up processors
    try:
        get_document_processor()
        query_processor = get_query_processor()
        logger.info("Processors initialized")

        # Optionally pre-warm the cross-encoder reranker so the first Q+A
        # request doesn't pay the cold-start cost (HF weight load can take
        # 10–30s and would otherwise block the event loop mid-request).
        # Runs in a thread because sentence_transformers init does sync I/O + CPU.
        #
        # Off by default: the local reranker drags torch + sentence-transformers
        # (~780 MB) into the process. Deferring the load until first use keeps
        # idle/low-traffic tenant stacks lean. Enable RERANKER_PRELOAD=true for
        # latency-sensitive deployments that want zero cold start.
        if settings.reranker_service_url:
            logger.info(
                f"Reranking offloaded to service at {settings.reranker_service_url}; "
                "no local cross-encoder will be loaded"
            )
        elif settings.enable_reranking and settings.reranker_preload:
            await asyncio.to_thread(lambda: query_processor.reranker)
            logger.info("Cross-encoder reranker pre-warmed at startup")
        elif settings.enable_reranking:
            logger.info(
                "Reranker load deferred to first use (RERANKER_PRELOAD=false); "
                "torch/sentence-transformers stay unloaded until then"
            )
    except Exception as e:
        logger.warning(f"Could not initialize processors: {e}")
    
    yield

    # Cleanup. uvicorn's --timeout-graceful-shutdown has already stopped new
    # connections and is draining in-flight requests; SSE generators watch
    # SHUTTING_DOWN to terminate their streams cleanly (terminal event) so
    # clients reconnect instead of seeing a dead socket.
    SHUTTING_DOWN.set()

    background_tasks = [watchdog_task, reranker_reaper_task]
    if git_scheduler_task:
        background_tasks.append(git_scheduler_task)
    for task in background_tasks:
        task.cancel()
    # Let cancellation handlers actually run before tearing down their deps.
    await asyncio.gather(*background_tasks, return_exceptions=True)

    # Close the shared helper HTTP client (no-op when unused).
    try:
        from app.services.helper_client import close_async_client
        await close_async_client()
    except Exception:
        pass

    # Close the shared crawl4ai HTTP client (no-op when unused).
    try:
        from app.services.crawl_client import close_async_client as close_crawl_client
        await close_crawl_client()
    except Exception:
        pass

    # Flush + shut down Langfuse so buffered traces are delivered (no-op when
    # tracing is inactive). The lifespan has the stop grace period to drain.
    try:
        from app.services.observability import shutdown_langfuse
        shutdown_langfuse()
    except Exception:
        pass

    if _api_executor:
        # Bounded: give blocked DB threads a moment to finish, but never hang
        # shutdown past the stop grace period. The shutdown itself must run on
        # a dedicated thread — asyncio.to_thread would submit it INTO
        # _api_executor (it is the loop's default executor) and deadlock.
        import threading as _threading

        _pool_drained = _threading.Event()

        def _drain_pool():
            try:
                _api_executor.shutdown(wait=True, cancel_futures=True)
            finally:
                _pool_drained.set()

        _threading.Thread(target=_drain_pool, daemon=True).start()
        for _ in range(200):  # up to 20s, without blocking the event loop
            if _pool_drained.is_set():
                break
            await asyncio.sleep(0.1)
        if not _pool_drained.is_set():
            logger.warning("API executor did not drain within 20s; continuing shutdown")

    neo4j.close()
    logger.info("Application shutdown complete")


# Interactive docs are disabled in production by default (EXPOSE_API_DOCS=auto)
# so a directly-exposed backend doesn't leak its full API schema to anonymous
# callers. Set EXPOSE_API_DOCS=true to force them on.
_docs_on = get_settings().docs_enabled
app = FastAPI(
    title="Cortex",
    description="A Neo4j + Haystack powered GraphRAG knowledge base with entity extraction, knowledge graph construction, and semantic search",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

# CORS middleware
# Origins come from CORS_ALLOWED_ORIGINS (comma-separated). Per the CORS spec,
# a wildcard origin is only valid without credentials — and that's fine here
# because all auth is header-based (X-API-Key), never cookies. An explicit
# allowlist re-enables credentialed requests.
_cors_settings = get_settings()
_cors_origins = _cors_settings.cors_origins_list
_cors_allow_credentials = _cors_origins != ["*"]
if not _cors_allow_credentials:
    logger.warning(
        "CORS is configured with wildcard origins (CORS_ALLOWED_ORIGINS=*). "
        "Set an explicit origin allowlist for production deployments."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Query quota enforcement (MAX_QUERIES_PER_MONTH)
# =============================================================================

def _seconds_until_next_utc_month() -> int:
    """Seconds until the start of the next UTC calendar month, for Retry-After."""
    now = datetime.utcnow()
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1)
    else:
        nxt = datetime(now.year, now.month + 1, 1)
    return max(1, int((nxt - now).total_seconds()))


async def enforce_rate_limit(request: Request) -> None:
    """Per-key token-bucket guardrail on expensive endpoints (opt-in).

    No-op when RATE_LIMIT_QPM is unset (default 0). Keyed by API key,
    falling back to client IP for unauthenticated callers.
    """
    settings = get_settings()
    qpm = getattr(settings, "rate_limit_qpm", 0)
    if qpm <= 0:
        return
    from app.services.rate_limiter import get_rate_limiter, rate_limit_key

    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization")
    client_ip = request.client.host if request.client else None
    allowed, retry_after = get_rate_limiter().check(
        rate_limit_key(api_key, client_ip),
        qpm,
        getattr(settings, "rate_limit_burst", 10),
    )
    if not allowed:
        metrics.RATE_LIMITED.labels(route=request.url.path).inc()
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({qpm} requests/minute). Slow down.",
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )


async def enforce_query_quota() -> None:
    """Reject chat requests with 429 once MAX_QUERIES_PER_MONTH is hit.

    No-op when the env var is unset (default 0 = unlimited).
    """
    settings = get_settings()

    # Kick off the reranker load now (non-blocking) so its ~7 s cold start
    # overlaps the query-analysis LLM call + embedding + search that run before
    # reranking, rather than stalling the rerank step. No-op in remote/disabled
    # mode or once the model is loaded.
    try:
        get_query_processor().prewarm_reranker()
    except Exception:
        pass

    if settings.max_queries_per_month <= 0:
        return
    neo4j = get_neo4j_service()
    count = await asyncio.to_thread(neo4j.get_query_count_this_month)
    if count >= settings.max_queries_per_month:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Monthly query limit reached "
                f"(max: {settings.max_queries_per_month}). "
                f"Upgrade your plan or wait until next month."
            ),
            headers={"Retry-After": str(_seconds_until_next_utc_month())},
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


@app.middleware("http")
async def request_id_and_metrics_middleware(request: Request, call_next):
    """Correlation + telemetry for every request.

    - Reads/generates X-Request-ID, stores it in a contextvar (stamped on
      every log line; forwarded to cortex-helper) and echoes it back.
    - Records request count + latency by ROUTE TEMPLATE (bounded cardinality).
    """
    set_request_id(request.headers.get("X-Request-ID") or new_request_id())
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        route = getattr(request.scope.get("route"), "path", request.url.path)
        metrics.HTTP_REQUESTS.labels(
            route=route, method=request.method, status="5xx"
        ).inc()
        raise
    route = getattr(request.scope.get("route"), "path", None)
    if route:  # unmatched paths (404 noise) are not labeled
        metrics.HTTP_REQUESTS.labels(
            route=route,
            method=request.method,
            status=f"{response.status_code // 100}xx",
        ).inc()
        metrics.HTTP_DURATION.labels(route=route).observe(
            time.monotonic() - start
        )
    rid = get_request_id()
    if rid:
        response.headers["X-Request-ID"] = rid
    return response


@app.get("/metrics")
async def prometheus_metrics(auth: AuthResult = Depends(require_admin)):
    """Prometheus metrics (admin-only; not routed through the prod nginx)."""
    settings = get_settings()
    if not getattr(settings, "metrics_enabled", True):
        raise HTTPException(status_code=404, detail="Metrics disabled")
    if not metrics.AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="prometheus-client is not installed in this image",
        )
    payload, content_type = metrics.render()
    from fastapi.responses import Response as _Response

    return _Response(content=payload, media_type=content_type)


# =============================================================================
# Background Task Store
# =============================================================================

# In-memory task store (for production, consider Redis or database)
_task_store: Dict[str, TaskProgress] = {}


# =============================================================================
# AskAI activity tracking (for redeploy-safety reporting)
# =============================================================================

# Count of in-flight AskAI/research queries. The event loop is single-threaded,
# so plain int +=/-= is safe here without a lock. Reset to 0 on process restart,
# which is correct: a restart has no in-flight queries by definition.
_active_query_count: int = 0


async def track_ask_activity():
    """FastAPI dependency that marks an AskAI query as in-flight.

    Used as a `yield` dependency on the ask endpoints. The teardown after
    `yield` runs only once the response (including a fully-consumed
    StreamingResponse) is sent or the client disconnects, so the decrement is
    guaranteed for both buffered and streamed answers. While any query is in
    flight, GET /api/instance/status reports the instance as unsafe to redeploy.
    """
    global _active_query_count
    _active_query_count += 1
    try:
        await asyncio.to_thread(
            get_neo4j_service().set_meta,
            "last_query_at",
            datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:  # never block a query on bookkeeping
        logger.warning(f"Failed to record last_query_at: {e}")
    try:
        yield
    finally:
        _active_query_count = max(0, _active_query_count - 1)


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
# Pipeline Chain Orchestration
# =============================================================================
#
# Backend-side chaining for the "Generate Graph" flow on the /extract page.
# A request can pass `chain=relationship_analysis,community_detection` to have
# the backend automatically spawn each subsequent pipeline step as its own
# task when the prior step's task completes. This lets the full 3-step flow
# survive the user navigating away or closing the browser.
#
# Each chained step still has its own task_id / task_type / progress message
# so the UI can clearly indicate "Step N in progress" — unlike the deleted
# AUTO_*_AFTER_BATCH flags which hid all three phases inside Step 1's task.

_ALLOWED_CHAIN_STEPS = {"relationship_analysis", "community_detection"}

# Keep strong refs so spawned chain tasks aren't garbage-collected mid-flight.
_chain_tasks: set = set()


def _parse_chain(chain: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated chain string into a list of valid step names."""
    if not chain:
        return None
    items = [x.strip() for x in chain.split(",") if x.strip()]
    filtered = [x for x in items if x in _ALLOWED_CHAIN_STEPS]
    return filtered or None


def _spawn_chain_task(coro) -> asyncio.Task:
    """Schedule a follow-up pipeline task and keep a strong reference to it."""
    task = asyncio.create_task(coro)
    _chain_tasks.add(task)
    task.add_done_callback(_chain_tasks.discard)
    return task


async def _wait_for_image_analysis_complete(
    task_id: str,
    base_message: str,
    poll_interval: float = 3.0,
) -> None:
    """Block until no documents have pending background image analysis.

    Image analysis runs on a separate thread pool after a document's text
    processing finishes (see document_processor._analyze_images_background_*).
    Calling this from the batch_processing task keeps Step 1 in 'running'
    state — and the chain on hold — until image entities have landed.
    """
    # Image analysis is fire-and-forget on a separate thread pool; if it dies
    # before reaching image_progress_total (crash, exception before the final
    # reconcile write), the documents would sit at current < total forever and
    # this loop — and the whole pipeline chain (Steps 2/3) — would hang. Bail if
    # image progress flatlines for STALL_TIMEOUT so the chain can advance.
    STALL_TIMEOUT = 600.0  # seconds of zero progress before giving up
    neo4j = get_neo4j_service()
    last_done = -1
    last_progress_at = time.monotonic()
    while True:
        all_docs = await asyncio.to_thread(neo4j.get_all_documents)
        pending = [
            d for d in all_docs
            if d.get("processing_status") == "completed"
            and (d.get("image_progress_total") or 0) > 0
            and (d.get("image_progress_current") or 0) < (d.get("image_progress_total") or 0)
        ]
        if not pending:
            return
        total_imgs = sum(d.get("image_progress_total", 0) for d in pending)
        done_imgs = sum(d.get("image_progress_current", 0) for d in pending)
        if done_imgs > last_done:
            last_done = done_imgs
            last_progress_at = time.monotonic()
        elif time.monotonic() - last_progress_at > STALL_TIMEOUT:
            logger.warning(
                "Image analysis stalled at %d/%d across %d document(s) for >%.0fs; "
                "no longer blocking the pipeline chain (task %s).",
                done_imgs, total_imgs, len(pending), STALL_TIMEOUT, task_id,
            )
            return
        update_task_progress(
            task_id,
            done_imgs,
            total_imgs,
            f"{base_message} — analyzing images: {done_imgs}/{total_imgs} across {len(pending)} document(s)...",
        )
        await asyncio.sleep(poll_interval)


# =============================================================================
# Task Status Endpoints
# =============================================================================

@app.get("/api/tasks/{task_id}", response_model=TaskProgress)
async def get_task_status(task_id: str, auth: AuthResult = Depends(require_read_permission)):
    """
    Get the current status and progress of a background task.
    
    Poll this endpoint to track long-running operations like community detection.
    """
    task = get_task(task_id)
    if not task:
        # The task store is in-memory, so a known-recent task id that 404s here
        # most likely means the backend restarted (redeploy) and dropped in-flight
        # tasks — not a bad id. Say so, so the client/operator can re-run.
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found — it may have completed and been cleaned up, "
                   "or been interrupted by a server restart. Re-run the operation if needed.",
        )
    return task


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str, auth: AuthResult = Depends(require_read_permission)):
    """
    Get the result of a completed background task.
    
    Returns 202 if the task is still running, 200 with result if completed,
    or 500 if the task failed.
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found — it may have completed and been cleaned up, "
                   "or been interrupted by a server restart. Re-run the operation if needed.",
        )

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
    task_type: Optional[str] = Query(default=None, description="Filter by task type"),
    auth: AuthResult = Depends(require_read_permission)
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
async def cancel_task(task_id: str, auth: AuthResult = Depends(require_manage_permission)):
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
async def cleanup_tasks(
    max_age_hours: int = Query(default=24, ge=1, le=168),
    auth: AuthResult = Depends(require_manage_permission)
):
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
    """Get knowledge base and knowledge graph statistics.
    
    For restricted API keys, counts are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        stats = await asyncio.to_thread(neo4j.get_stats, collection_filter)
        return GraphStatsResponse(
            document_count=stats["document_count"],
            chunk_count=stats["chunk_count"],
            entity_count=stats.get("entity_count", 0),
            relationship_count=stats.get("relationship_count", 0),
            per_chunk_relationship_count=stats.get("per_chunk_relationship_count", 0),
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


@app.get("/api/instance/status", response_model=InstanceStatusResponse)
async def get_instance_status(auth: AuthResult = Depends(require_manage_permission)):
    """Operational snapshot for redeploy-safety decisions.

    Consolidates the signals deploy automation needs to decide whether it can
    safely restart/upgrade this customer instance. `safe_to_redeploy` is False
    while destructible work is in flight — documents being processed/extracted,
    background tasks in the in-memory store (which a restart would lose), or an
    active AskAI/research query (a restart kills the stream). Pending documents
    persist and resume after a restart, so they are reported but never block.
    """
    neo4j = get_neo4j_service()
    checked_at = datetime.now(timezone.utc).isoformat()

    connected = await asyncio.to_thread(neo4j.verify_connectivity)

    stats: dict = {}
    last_query_at: Optional[str] = None
    if connected:
        try:
            stats = await asyncio.to_thread(neo4j.get_stats)
            last_query_at = await asyncio.to_thread(neo4j._get_meta, "last_query_at")
        except Exception as e:
            logger.error(f"instance/status: failed to read graph state: {e}")

    processing_count = stats.get("processing_count", 0)
    pending_count = stats.get("pending_count", 0)
    failed_count = stats.get("failed_count", 0)

    # In-flight background jobs from the in-memory task store.
    running = [
        t for t in _task_store.values()
        if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
    ]
    running_tasks = [
        RunningTaskSummary(
            task_id=t.task_id,
            task_type=t.task_type,
            status=t.status.value,
            progress_percent=t.progress_percent,
            message=t.message,
            started_at=t.started_at.isoformat() if t.started_at else None,
        )
        for t in running
    ]

    reasons: List[str] = []
    if not connected:
        reasons.append("Neo4j not reachable — instance state cannot be verified")
    if processing_count > 0:
        reasons.append(f"{processing_count} document(s) currently processing")
    if running_tasks:
        reasons.append(f"{len(running_tasks)} background task(s) running")
    if _active_query_count > 0:
        reasons.append(f"{_active_query_count} AskAI query(ies) in flight")

    safe = (
        connected
        and processing_count == 0
        and not running_tasks
        and _active_query_count == 0
    )

    return InstanceStatusResponse(
        safe_to_redeploy=safe,
        reasons=reasons,
        processing_count=processing_count,
        pending_count=pending_count,
        failed_count=failed_count,
        running_task_count=len(running_tasks),
        running_tasks=running_tasks,
        active_query_count=_active_query_count,
        last_query_at=last_query_at,
        last_relationship_analysis_at=stats.get("last_relationship_analysis_at"),
        last_community_detection_at=stats.get("last_community_detection_at"),
        last_entity_merge_at=stats.get("last_entity_merge_at"),
        neo4j_connected=connected,
        checked_at=checked_at,
    )


@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    collection_id: Optional[str] = Query(default=None, description="Collection to add document to"),
    start_processing: bool = Query(default=False, description="Start processing immediately (set to false for bulk uploads)"),
    source: Optional[str] = Query(default=None, description="Source identifier for the document (e.g. 'youtube-transcriber', 'slack-bot'). Defaults to 'upload'."),
    auth: AuthResult = Depends(require_manage_permission),
    _rate: None = Depends(enforce_rate_limit),
):
    """
    Upload a file to the knowledge base.
    
    For bulk uploads (100+ files), set start_processing=false to upload all files first,
    then call POST /api/documents/process-pending to start processing.
    """
    # Validate collection access
    target_collection = collection_id or "default"
    validate_collection_access(auth, target_collection, "upload to")
    
    settings = get_settings()
    
    # Enforce file and entity limits
    if settings.max_files > 0 or settings.max_entities > 0:
        neo4j = get_neo4j_service()
        stats = await asyncio.to_thread(neo4j.get_stats)
        if settings.max_files > 0 and stats["document_count"] >= settings.max_files:
            raise HTTPException(
                status_code=403,
                detail=f"File limit reached (max: {settings.max_files}). Upgrade your plan to upload more documents."
            )
        if settings.max_entities > 0 and stats["entity_count"] >= settings.max_entities:
            raise HTTPException(
                status_code=403,
                detail=f"Entity limit reached (max: {settings.max_entities}). Upgrade your plan to extract more entities."
            )

    # A multipart upload can omit the filename; Path(None) would 500. Fail clean.
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required for upload.")

    # Validate file extension
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} not supported. Allowed: {settings.allowed_extensions}"
        )

    # Read the upload in bounded chunks so an oversized file is rejected
    # mid-stream instead of being fully buffered into memory first — a multi-GB
    # POST would otherwise pressure a small tenant container before the size
    # check ever ran. Memory is capped at ~max_size (+1 MiB).
    max_size = settings.max_file_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    file_size = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MiB
        if not chunk:
            break
        file_size += len(chunk)
        if file_size > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    # Check for duplicate document (same filename and file size)
    neo4j = get_neo4j_service()
    existing = await asyncio.to_thread(neo4j.find_document_by_filename_and_size, file.filename, file_size)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A document with the same name and size already exists: '{file.filename}' ({file_size} bytes)"
        )

    # Save file permanently
    import uuid
    doc_id = str(uuid.uuid4())
    stored_filename = f"{doc_id}{file_ext}"
    file_path = os.path.join(settings.upload_dir, stored_filename)
    
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    doc_source = source or "upload"

    try:
        processor = get_document_processor()

        if start_processing:
            # Legacy behavior: start processing immediately
            doc_id = await processor.process_file(file_path, file.filename, file_size, collection_id, source=doc_source)
            return UploadResponse(
                document_id=doc_id,
                filename=file.filename,
                status=ProcessingStatus.PROCESSING,
                message="File uploaded and processing started",
                source=doc_source
            )
        else:
            # New behavior: just store the file, don't process yet
            doc_id = await processor.store_file_only(file_path, file.filename, file_size, collection_id, source=doc_source)
            return UploadResponse(
                document_id=doc_id,
                filename=file.filename,
                status=ProcessingStatus.PENDING,
                message="File uploaded. Call /api/documents/process-pending to start processing.",
                source=doc_source
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
        # Use fast mode config which has thinking disabled
        llm_config = get_llm_config(fast_mode=True)
        client = make_async_openai_client(
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
            **build_chat_params(llm_config.model, temperature=0.2, max_tokens=20),
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
async def generate_topic_hint(request: TopicHintRequest, auth: AuthResult = Depends(require_manage_permission)):
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
        # Use fast mode config which has thinking disabled
        llm_config = get_llm_config(fast_mode=True)
        client = make_async_openai_client(
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
            **build_chat_params(llm_config.model, temperature=0.2, max_tokens=20),
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
    # Validate collection access
    target_collection = request.collection_id or "default"
    validate_collection_access(auth, target_collection, "add content to")
    
    settings = get_settings()
    neo4j = get_neo4j_service()
    
    # Enforce file and entity limits
    if settings.max_files > 0 or settings.max_entities > 0:
        stats = await asyncio.to_thread(neo4j.get_stats)
        if settings.max_files > 0 and stats["document_count"] >= settings.max_files:
            raise HTTPException(
                status_code=403,
                detail=f"File limit reached (max: {settings.max_files}). Upgrade your plan to upload more documents."
            )
        if settings.max_entities > 0 and stats["entity_count"] >= settings.max_entities:
            raise HTTPException(
                status_code=403,
                detail=f"Entity limit reached (max: {settings.max_entities}). Upgrade your plan to extract more entities."
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
        
        custom_source = request.source or "custom_input"

        if request.start_processing:
            # Start processing immediately
            doc_id = await processor.process_file(file_path, filename, file_size, collection_id, source=custom_source)
            status = ProcessingStatus.PROCESSING
            message = "Custom input saved and processing started"
        else:
            # Just store, process later
            doc_id = await processor.store_file_only(file_path, filename, file_size, collection_id, source=custom_source)
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
            input_type=request.input_type,
            source=custom_source
        )
        
    except Exception as e:
        logger.error(f"Error creating custom input: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/custom-inputs")
async def list_custom_inputs(
    search: Optional[str] = Query(default=None, description="Search in filename, content, or topic"),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthResult = Depends(require_read_permission)
):
    """
    List all custom inputs with optional search.
    
    Returns custom inputs (manually added Q&A, text, markdown) that can be edited.
    For restricted API keys, results are filtered to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        custom_inputs = await asyncio.to_thread(neo4j.get_custom_inputs, search, limit)
        
        # Filter by collection access for restricted keys
        collection_filter = auth.get_collection_filter()
        if collection_filter is not None:
            custom_inputs = [i for i in custom_inputs if i.get("collection_id") in collection_filter]
        
        return {"custom_inputs": custom_inputs, "total": len(custom_inputs)}
    except Exception as e:
        logger.error(f"Error listing custom inputs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/custom-inputs/{document_id}")
async def get_custom_input(document_id: str, auth: AuthResult = Depends(require_read_permission)):
    """
    Get a custom input's full data for editing.
    
    Returns the original content, answer (for Q&A), input type, and metadata.
    """
    try:
        neo4j = get_neo4j_service()
        custom_input = await asyncio.to_thread(neo4j.get_custom_input, document_id)
        if not custom_input:
            raise HTTPException(status_code=404, detail="Custom input not found")
        
        # Validate collection access
        validate_collection_access(auth, custom_input.get("collection_id"), "view")
        
        return custom_input
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting custom input: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents(auth: AuthResult = Depends(require_read_permission)):
    """List all documents in the knowledge base (filtered by API key collection access)."""
    try:
        neo4j = get_neo4j_service()
        documents = await asyncio.to_thread(neo4j.get_all_documents)
        
        # Filter documents based on collection access
        collection_filter = auth.get_collection_filter()
        if collection_filter is not None:
            documents = [d for d in documents if d.get("collection_id") in collection_filter]
        
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
        
        # Validate collection access
        doc_collection = document.get("collection_id")
        validate_collection_access(auth, doc_collection, "view documents in")
        
        return document
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}/content")
async def get_document_content(document_id: str, auth: AuthResult = Depends(require_read_permission)):
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
        
        # Validate collection access
        validate_collection_access(auth, content.get("collection_id"), "view content in")
        
        return content
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document content: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{document_id}/file")
async def get_document_file(document_id: str, auth: AuthResult = Depends(require_read_permission)):
    """
    Serve the original uploaded file for viewing/download.
    """
    try:
        neo4j = get_neo4j_service()
        doc = await asyncio.to_thread(neo4j.get_document, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        # Validate collection access
        validate_collection_access(auth, doc.get("collection_id"), "download files from")

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
async def download_documents_zip(request: Request, auth: AuthResult = Depends(require_read_permission)):
    """
    Stream a zip archive of the original uploaded files for the given document IDs.
    Accepts JSON body: { "document_ids": ["id1", "id2", ...] }
    Uses ZIP64 and streams to handle large collections without loading everything into memory.
    
    For restricted API keys, only includes documents from allowed collections.
    Returns 403 if no accessible documents found.
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

    # Filter to documents that have files on disk AND are accessible to this API key
    collection_filter = auth.get_collection_filter()
    valid_docs = []
    seen_names = {}
    for doc in docs:
        # Check collection access - silently skip inaccessible documents
        if collection_filter is not None and doc.get("collection_id") not in collection_filter:
            continue
            
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
        # If collection filter was applied and no docs are accessible, return 403
        if collection_filter is not None:
            raise HTTPException(status_code=403, detail="No accessible documents found for download")
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
        # First get the document to check collection access
        neo4j = get_neo4j_service()
        document = await asyncio.to_thread(neo4j.get_document, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Validate collection access
        doc_collection = document.get("collection_id")
        validate_collection_access(auth, doc_collection, "delete documents from")
        
        # Cancel any active processing first
        processor = get_document_processor()
        was_processing = await processor.cancel_document_processing(document_id)
        if was_processing:
            logger.info(f"Cancelled active processing for document {document_id} before deletion")
        
        # Then delete the document and clean up graph
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

        deleted = result["deleted_count"]
        requested = len(request.document_ids)
        if deleted == 0:
            message = "No matching documents were deleted — they may have already been removed."
        elif deleted < requested:
            message = f"Deleted {deleted} of {requested} document(s); {requested - deleted} were not found."
        else:
            message = f"Successfully deleted {deleted} document(s)"

        return {
            "message": message,
            "deleted_count": deleted,
            "requested_count": requested,
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
    file: Optional[UploadFile] = File(default=None),
    auth: AuthResult = Depends(require_manage_permission)
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
    
    # Validate collection access
    validate_collection_access(auth, document.get("collection_id"), "reprocess documents in")
    
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
    concurrency: Optional[int] = Query(default=None, ge=1, le=50, description="Number of documents to process concurrently"),
    chain: Optional[str] = Query(
        default=None,
        description="Comma-separated next pipeline steps to auto-run when this task finishes "
                    "(allowed: 'relationship_analysis', 'community_detection'). Used by the "
                    "Generate Graph flow to chain Steps 1 → 2 → 3 backend-side.",
    ),
    auth: AuthResult = Depends(require_manage_permission)
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
                
                # Check collection access
                if not auth.can_access_collection(doc.get("collection_id")):
                    results.append({
                        "document_id": doc_id,
                        "status": "error",
                        "message": "Access denied for this document's collection"
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
            
            # Schedule the background task (with optional chain to Step 2/3)
            parsed_chain = _parse_chain(chain)
            background_tasks.add_task(
                _run_batch_processing_task,
                task.task_id,
                actual_concurrency,
                parsed_chain,
            )

            return {
                "results": results,
                "total_queued": queued_count,
                "task_id": task.task_id,
                "concurrency": actual_concurrency,
                "chain": parsed_chain,
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
async def get_pending_documents(auth: AuthResult = Depends(require_read_permission)):
    """
    Get all documents with 'pending' status that are waiting to be processed.
    
    Use this to check how many documents are queued before calling process-pending.
    For restricted API keys, results are filtered to accessible collections.
    """
    try:
        processor = get_document_processor()
        pending = await asyncio.to_thread(processor.get_pending_documents)
        
        # Filter by collection access for restricted keys
        collection_filter = auth.get_collection_filter()
        if collection_filter is not None:
            pending = [d for d in pending if d.get("collection_id") in collection_filter]
        
        return {
            "pending_count": len(pending),
            "documents": pending
        }
    except Exception as e:
        logger.error(f"Error getting pending documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _run_batch_processing_task(
    task_id: str,
    concurrency: int,
    chain: Optional[List[str]] = None,
) -> None:
    """Background task for batch document processing with progress tracking.

    When `chain` includes 'relationship_analysis', a follow-up task is spawned
    automatically once this step (including background image analysis) finishes.
    Anything beyond the first chain item is passed to that follow-up so the
    chain can continue (e.g. into 'community_detection').
    """
    try:
        processor = get_document_processor()
        pending = processor.get_pending_documents()
        total = len(pending)

        if total == 0:
            # Still honour the image-analysis wait + chain — the user may have
            # clicked "Generate Graph" on a fully-processed instance and want
            # Steps 2 and 3 to run.
            await _wait_for_image_analysis_complete(task_id, "No pending documents")
            complete_task(task_id, {
                "processed": 0,
                "failed": 0,
                "total": 0,
                "message": "No pending documents to process"
            })
        else:
            update_task_progress(task_id, 0, total, f"Starting processing of {total} documents...")

            def progress_callback(current: int, total: int, message: str):
                update_task_progress(task_id, current, total, message)

            result = await processor.process_pending_documents(
                concurrency=concurrency,
                progress_callback=progress_callback
            )

            # Hold Step 1's task in 'running' until background image analysis
            # also finishes — that's what the user sees as "Step 1 In Progress".
            await _wait_for_image_analysis_complete(task_id, "Text processing complete")

            complete_task(task_id, result)

        # Chain: spawn relationship analysis as the next step's own task.
        if chain and chain[0] == "relationship_analysis":
            remaining_chain = chain[1:]
            rel_task = create_task("relationship_analysis")
            rel_task.message = "Starting deep relationship analysis..."
            _spawn_chain_task(_run_relationship_analysis_task(
                rel_task.task_id,
                collection_id=None,
                scope="full",
                rebuild=True,
                chain=remaining_chain,
            ))
            logger.info(
                f"Chained: spawned relationship_analysis task {rel_task.task_id} "
                f"(remaining chain={remaining_chain})"
            )

    except Exception as e:
        logger.error(f"Error in batch processing task {task_id}: {e}")
        fail_task(task_id, str(e))


@app.post("/api/documents/process-pending")
async def process_pending_documents(
    background_tasks: BackgroundTasks,
    concurrency: Optional[int] = Query(default=None, ge=1, le=50, description="Number of documents to process concurrently (defaults to BATCH_PROCESSING_CONCURRENCY env var)"),
    chain: Optional[str] = Query(
        default=None,
        description="Comma-separated next pipeline steps to auto-run when this task finishes "
                    "(allowed: 'relationship_analysis', 'community_detection').",
    ),
    auth: AuthResult = Depends(require_manage_permission)
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
        
        # Schedule the background task (with optional chain to Step 2/3)
        parsed_chain = _parse_chain(chain)
        background_tasks.add_task(
            _run_batch_processing_task,
            task.task_id,
            actual_concurrency,
            parsed_chain,
        )

        return {
            "task_id": task.task_id,
            "status": task.status,
            "pending_count": len(pending),
            "concurrency": actual_concurrency,
            "chain": parsed_chain,
            "message": f"Started processing {len(pending)} documents. Poll /api/tasks/{task.task_id} for progress."
        }
    except Exception as e:
        logger.error(f"Error starting batch processing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cleanup/orphaned-entities")
async def cleanup_orphaned_entities(auth: AuthResult = Depends(require_manage_permission)):
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
async def search(
    request: SearchRequest,
    auth: AuthResult = Depends(require_read_permission),
    _quota: None = Depends(enforce_query_quota),
    _rate: None = Depends(enforce_rate_limit),
):
    """
    Perform hybrid search on the knowledge base.
    
    Combines:
    - Semantic/vector search (finds similar meaning)
    - Keyword search (finds exact text matches in content)
    - Metadata search (finds matches in filename, topic hints)
    
    Uses Reciprocal Rank Fusion (RRF) to merge results from all sources.
    
    Note: For restricted API keys, results are filtered to accessible collections.
    """
    try:
        # Check if collection_id is specified in filters
        filter_collection_id = request.filters.get("collection_id") if request.filters else None
        if filter_collection_id:
            validate_collection_access(auth, filter_collection_id, "search in")

        # Resolve collection scope for restricted API keys
        effective_collection_id = filter_collection_id
        allowed_collection_ids: Optional[List[str]] = None

        if not effective_collection_id:
            collection_filter = auth.get_collection_filter()
            if collection_filter is not None:
                if len(collection_filter) == 1:
                    effective_collection_id = collection_filter[0]
                else:
                    allowed_collection_ids = collection_filter

        processor = get_query_processor()
        
        # Use hybrid search to combine vector + keyword + metadata
        results = processor.hybrid_search(
            query=request.query,
            top_k=request.top_k,
            collection_id=effective_collection_id,
            allowed_collection_ids=allowed_collection_ids,
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
async def ask_question(
    request: RAGRequest,
    auth: AuthResult = Depends(require_read_permission),
    _quota: None = Depends(enforce_query_quota),
    _rate: None = Depends(enforce_rate_limit),
    _track: None = Depends(track_ask_activity),
):
    """
    Ask a question using enhanced GraphRAG.
    
    Features:
    - Hybrid search with RRF (vector + keyword + graph)
    - Cross-encoder re-ranking for precision
    - Conversation memory for context
    - Agentic multi-step reasoning (optional)
    """
    try:
        # Validate collection access if a specific collection is requested
        if request.collection_id:
            validate_collection_access(auth, request.collection_id, "query")

        # Resolve effective collection scope for restricted API keys.
        # When the caller passes no collection_id, restricted keys must still
        # be confined to their allowed collections.
        effective_collection_id = request.collection_id
        allowed_collection_ids: Optional[List[str]] = None

        if not effective_collection_id:
            collection_filter = auth.get_collection_filter()
            if collection_filter is not None:
                if len(collection_filter) == 1:
                    # Single allowed collection — use the standard collection_id path
                    effective_collection_id = collection_filter[0]
                else:
                    # Multiple allowed collections — pass as an IN-list to the search layer
                    allowed_collection_ids = collection_filter

        settings = get_settings()
        processor = get_query_processor()

        # Convert conversation history if provided
        conversation_history = None
        if request.conversation_history:
            conversation_history = request.conversation_history

        # Agentic deep research routinely runs ~60-90s. The non-streaming
        # endpoint buffers the entire run and sends no bytes until done, so it
        # always races the edge-proxy read timeout and dies as a bare 500.
        # Fail fast with structured guidance to the SSE endpoint (which stays
        # alive via heartbeats) instead of making the client wait for a 504.
        if request.use_agentic and settings.enable_agent_research:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "agentic_requires_streaming",
                    "message": (
                        "Agentic deep research is not supported on the "
                        "non-streaming POST /api/ask endpoint (it routinely "
                        "exceeds the gateway timeout). Use POST /api/ask/stream "
                        "(SSE) for use_agentic:true requests."
                    ),
                    "use_endpoint": "/api/ask/stream",
                },
            )

        # Legacy path for non-agent requests. Bound it with an app-level
        # deadline so a slow request returns a clean 504 JSON {detail} rather
        # than letting the edge proxy (Traefik) cut the silent socket and emit
        # a bare plain-text 500. Keep ASK_DEADLINE_SECONDS just below the proxy
        # read timeout. NOTE: the underlying to_thread work (neo4j/LLM) cannot
        # be cancelled and runs to completion in the background even after we
        # return 504 — acceptable for now; raising workers/queueing is Tier 1.
        deadline = settings.ask_deadline_seconds
        result = await asyncio.wait_for(
            processor.rag_query(
                question=request.question,
                top_k=request.top_k,
                use_graph=request.use_graph,
                max_hops=request.max_hops,
                conversation_history=conversation_history,
                use_reranking=request.use_reranking,
                use_agentic=request.use_agentic,
                collection_id=effective_collection_id,
                allowed_collection_ids=allowed_collection_ids,
            ),
            timeout=deadline if deadline and deadline > 0 else None,
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
    except asyncio.TimeoutError:
        deadline = get_settings().ask_deadline_seconds
        logger.warning(
            f"/api/ask exceeded {deadline}s deadline for question: {request.question[:80]!r}"
        )
        raise HTTPException(
            status_code=504,
            detail={
                "error": "deadline_exceeded",
                "message": (
                    f"The request exceeded the server-side deadline "
                    f"({deadline}s). Retry, simplify the question, or use "
                    f"POST /api/ask/stream which streams incrementally and is "
                    f"not subject to this deadline."
                ),
                "deadline_seconds": deadline,
            },
        )
    except HTTPException:
        # Preserve intended status codes (e.g. 400 agentic guard, 403 collection
        # access, 504 deadline) instead of re-wrapping them as a generic 500.
        raise
    except Exception as e:
        logger.error(f"Error in GraphRAG query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ask/stream")
async def ask_question_stream(
    request: RAGRequest,
    auth: AuthResult = Depends(require_read_permission),
    _quota: None = Depends(enforce_query_quota),
    _rate: None = Depends(enforce_rate_limit),
    _track: None = Depends(track_ask_activity),
):
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
    # Validate collection access if a specific collection is requested
    if request.collection_id:
        validate_collection_access(auth, request.collection_id, "query")

    # Resolve effective collection scope for restricted API keys.
    # Closures inside generator functions capture variables by reference, so we
    # compute the effective values here in the outer scope where auth is available.
    _stream_effective_collection_id = request.collection_id
    _stream_allowed_collection_ids: Optional[List[str]] = None

    if not _stream_effective_collection_id:
        _stream_collection_filter = auth.get_collection_filter()
        if _stream_collection_filter is not None:
            if len(_stream_collection_filter) == 1:
                _stream_effective_collection_id = _stream_collection_filter[0]
            else:
                _stream_allowed_collection_ids = _stream_collection_filter

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
                        collection_id=_stream_effective_collection_id,
                        allowed_collection_ids=_stream_allowed_collection_ids,
                        conversation_memory=request.conversation_memory,
                    ):
                        yield f"data: {json.dumps(event)}\n\n"
                else:
                    async for event in processor.agentic_rag_stream(
                        question=request.question,
                        top_k=request.top_k,
                        max_hops=request.max_hops,
                        conversation_history=request.conversation_history,
                        collection_id=_stream_effective_collection_id
                    ):
                        yield f"data: {json.dumps(event)}\n\n"

            except Exception as e:
                logger.error("Error in streaming agentic RAG: %s", e, exc_info=True)
                yield sse_error_frame(e)

        return StreamingResponse(
            with_sse_heartbeat(traced_sse(
                generate_agentic(),
                "ask.agentic",
                user_id=auth.key_id,
                tags=["endpoint:ask_stream", "mode:agentic"],
            )),
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
                    results = processor.search(request.question, top_k=request.top_k,
                                               collection_id=_stream_effective_collection_id,
                                               allowed_collection_ids=_stream_allowed_collection_ids)
                    context = "\n\n".join([r['content'][:600] for r in results[:3]])
                    
                    if context:
                        prompt = f"""Reference information:
{context}

Question: {request.question}"""
                    else:
                        prompt = request.question
                    
                    messages.append({"role": "user", "content": prompt})
                
                # For fast mode, use the fast mode model (OPENAI_MODEL_FAST_MODE)
                llm_config = get_llm_config(fast_mode=True)
                client = make_async_openai_client(
                    api_key=llm_config.api_key,
                    base_url=llm_config.base_url,
                )
                
                # Compose the answer with hidden reasoning suppressed on the chat
                # path (centralized per-provider dispatch — incl. Venice
                # disable_thinking — replaces the old deepseek-only hack) for a
                # snappy first token; auto-falls-back if a model rejects the param.
                stream = await safe_chat_completion(
                    client.chat.completions.create,
                    base_url=llm_config.base_url,
                    model=llm_config.model,
                    reasoning_mode=ReasoningMode.parse(settings.default_reasoning_mode),
                    overrides=settings.parsed_reasoning_overrides,
                    messages=messages,
                    stream=True,
                    **stream_usage_kwargs(),
                    # Lower temperature / capped tokens for faster, more deterministic
                    # responses (params adapted per model family — GPT-5/o-series).
                    **build_chat_params(llm_config.model, temperature=0.2, max_tokens=600),
                )
                
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        yield f"data: {json.dumps({'content': content})}\n\n"

                yield f"data: {json.dumps({'done': True, 'fast_mode': True})}\n\n"
                
            except Exception as e:
                logger.error("Error in fast streaming RAG: %s", e, exc_info=True)
                yield sse_error_frame(e)
        
        return StreamingResponse(
            with_sse_heartbeat(traced_sse(
                generate_fast(),
                "ask.fast",
                user_id=auth.key_id,
                tags=["endpoint:ask_stream", "mode:fast"],
            )),
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
                    collection_id=_stream_effective_collection_id,
                    allowed_collection_ids=_stream_allowed_collection_ids,
                    conversation_memory=request.conversation_memory,
                ):
                    yield f"data: {json.dumps(event)}\n\n"
                return

            # Legacy standard streaming path (hybrid search + reranking + writer)
            conversation_history = request.conversation_history

            graph_context = None

            # Stage status (additive; removes the silent pre-token window). Gated
            # by stream_reasoning_steps so the setting matches behavior.
            _emit_status = settings.stream_reasoning_steps
            if _emit_status:
                yield f"data: {json.dumps({'status': {'stage': 'searching', 'message': 'Searching the knowledge base'}})}\n\n"

            if request.use_graph:
                search_result = await processor.graph_search_async(
                    request.question,
                    top_k=request.top_k * 2,
                    max_hops=request.max_hops,
                    use_hybrid_rrf=settings.enable_hybrid_search,
                    collection_id=_stream_effective_collection_id,
                    allowed_collection_ids=_stream_allowed_collection_ids,
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
                results = processor.search(request.question, top_k=request.top_k * 2,
                                           collection_id=_stream_effective_collection_id,
                                           allowed_collection_ids=_stream_allowed_collection_ids)

            # Re-rank if enabled
            if request.use_reranking and settings.enable_reranking and results:
                if _emit_status:
                    yield f"data: {json.dumps({'status': {'stage': 'reranking', 'message': 'Ranking the most relevant sources'}})}\n\n"
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
            client = make_async_openai_client(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
            )

            if _emit_status:
                yield f"data: {json.dumps({'status': {'stage': 'generating', 'message': 'Writing the answer'}})}\n\n"

            stream = await safe_chat_completion(
                client.chat.completions.create,
                base_url=llm_config.base_url,
                model=llm_config.model,
                reasoning_mode=ReasoningMode.parse(settings.default_reasoning_mode),
                overrides=settings.parsed_reasoning_overrides,
                messages=messages,
                stream=True,
                **stream_usage_kwargs(),
                **build_chat_params(
                    llm_config.model,
                    temperature=0.3,
                    max_tokens=settings.writer_max_tokens_speed,
                ),
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'content': content})}\n\n"

            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as e:
            logger.error("Error in streaming RAG: %s", e, exc_info=True)
            yield sse_error_frame(e)

    return StreamingResponse(
        with_sse_heartbeat(traced_sse(
            generate(),
            "ask.standard",
            user_id=auth.key_id,
            tags=["endpoint:ask_stream", "mode:standard"],
        )),
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
    include_neighbors: bool = Query(default=True, description="Include 1-hop neighbor entities for more relationships"),
    auth: AuthResult = Depends(require_read_permission)
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
    
    For restricted API keys, results are scoped to entities from accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        data = await asyncio.to_thread(
            neo4j.get_graph_visualization_data, limit, include_neighbors, collection_filter
        )
        return data
    except Exception as e:
        logger.error(f"Error getting graph visualization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity/{entity_name}/relationships")
async def get_entity_relationships(
    entity_name: str,
    max_depth: int = Query(default=2, ge=1, le=3, description="Maximum relationship hops to traverse"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum relationships to return"),
    auth: AuthResult = Depends(require_read_permission)
):
    """
    Get an entity and all its relationships up to max_depth hops.
    
    This enables focused graph exploration from a specific entity,
    showing all connected entities and the relationships between them.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        data = await asyncio.to_thread(
            neo4j.get_entity_relationships, entity_name, max_depth, limit, collection_filter
        )
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
    include_connections: bool = Query(default=True, description="Include entities that connect the specified entities"),
    auth: AuthResult = Depends(require_read_permission)
):
    """
    Get a subgraph containing specified entities and their interconnections.
    
    Endpoint for focused graph visualization of specific entities.
    If include_connections is True, also includes bridging entities that
    connect the specified entities (up to 2 hops apart).
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        data = await asyncio.to_thread(
            neo4j.get_graph_subgraph, entity_names, include_connections, collection_filter
        )
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
    auth: AuthResult = Depends(require_read_permission)
):
    """List entities in the knowledge graph with server-side pagination and search.
    
    For restricted API keys, results are scoped to entities from accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        result = await asyncio.to_thread(
            neo4j.list_entities_paginated, skip, limit, search, entity_type, collection_filter
        )
        return result
    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity-types")
async def list_entity_types(auth: AuthResult = Depends(require_read_permission)):
    """Get all distinct entity types.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        types = await asyncio.to_thread(neo4j.get_entity_types, collection_filter)
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
    auth: AuthResult = Depends(require_read_permission)
):
    """List relationships with server-side pagination and search.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        result = await asyncio.to_thread(
            neo4j.list_relationships_paginated, skip, limit, search, rel_type, collection_filter
        )
        return result
    except Exception as e:
        logger.error(f"Error listing relationships: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/relationship-types")
async def list_relationship_types(auth: AuthResult = Depends(require_read_permission)):
    """Get all distinct relationship types.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        types = await asyncio.to_thread(neo4j.get_relationship_types, collection_filter)
        return {"types": types}
    except Exception as e:
        logger.error(f"Error listing relationship types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/entity/{entity_name}")
async def get_entity_details(
    entity_name: str,
    max_hops: int = Query(default=2, ge=1, le=3),
    auth: AuthResult = Depends(require_read_permission)
):
    """Get details about a specific entity and its relationships.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        context = await asyncio.to_thread(
            neo4j.traverse_from_entities,
            [entity_name],
            max_hops,
            entity_paths_only=True,
            allowed_collection_ids=collection_filter
        )
        
        if not context["entities"]:
            raise HTTPException(status_code=404, detail="Entity not found")
        
        return context
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/search")
async def search_entities(
    query: str = Query(..., min_length=1),
    auth: AuthResult = Depends(require_read_permission)
):
    """Search for entities by name.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        results = await asyncio.to_thread(neo4j.find_entities_by_name, [query], collection_filter)
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Entity Editing
# =========================================================================


@app.patch("/api/graph/entity/{entity_name}")
async def update_entity(entity_name: str, request: UpdateEntityRequest, auth: AuthResult = Depends(require_manage_permission)):
    """Update an entity's name and/or description."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(
            neo4j.update_entity,
            entity_name,
            new_name=request.name,
            new_description=request.description,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Entity Merge & Deduplication
# =========================================================================


class MergeEntitiesRequest(BaseModel):
    canonical: str = Field(..., description="Name of the entity to keep")
    merge: List[str] = Field(..., description="Names of entities to merge into canonical")


async def _generate_merged_description(canonical: str, all_names: List[str], entity_data: dict) -> Optional[str]:
    """Generate a combined description for merged entities using the main LLM."""
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
        client = make_async_openai_client(
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
            **build_chat_params(config.model, temperature=0.2, max_tokens=1000),
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception as e:
        logger.warning(f"Failed to generate merged description, falling back to longest: {e}")
        return None


@app.post("/api/entities/merge")
async def merge_entities(request: MergeEntitiesRequest, auth: AuthResult = Depends(require_manage_permission)):
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
    auth: AuthResult = Depends(require_read_permission)
):
    """Get entity merge history.
    
    Note: This is an admin-adjacent feature showing merge operations.
    For restricted API keys, this requires collection_scope: "all".
    """
    try:
        # Merge history is a global admin feature - restrict to unrestricted keys
        if auth.get_collection_filter() is not None:
            raise HTTPException(
                status_code=403,
                detail="Merge history is only available to API keys with full collection access"
            )
        
        neo4j = get_neo4j_service()
        history = await asyncio.to_thread(neo4j.get_merge_history, limit)
        return {"history": history, "total": len(history)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting merge history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/duplicates")
async def suggest_duplicates(
    threshold: float = Query(default=0.75, ge=0.5, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthResult = Depends(require_read_permission)
):
    """Suggest duplicate entity groups for user review.
    
    For restricted API keys, results are scoped to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        groups = await asyncio.wait_for(
            asyncio.to_thread(neo4j.suggest_duplicate_entities, threshold, limit, collection_filter),
            timeout=300,  # 5 minute timeout for large graphs
        )
        return {"groups": groups, "total_groups": len(groups)}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Duplicate scan timed out — try a higher similarity threshold to reduce comparisons")
    except Exception as e:
        logger.error(f"Error suggesting duplicates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/status")
async def get_graph_status(auth: AuthResult = Depends(require_read_permission)):
    """Get GraphRAG system status.
    
    For restricted API keys, counts are scoped to accessible collections.
    """
    try:
        settings = get_settings()
        extractor = get_graph_extractor()
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        stats = await asyncio.to_thread(neo4j.get_stats, collection_filter)
        
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
async def list_collections(auth: AuthResult = Depends(require_read_permission)):
    """List all collections (filtered by API key access)."""
    try:
        neo4j = get_neo4j_service()
        collections = await asyncio.to_thread(neo4j.list_collections)
        
        # Filter collections based on API key access
        collection_filter = auth.get_collection_filter()
        if collection_filter is not None:
            collections = [c for c in collections if c.get("id") in collection_filter]
        
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
                    detail=f"Collection limit reached (max: {settings.max_collections}). Upgrade your plan to create more collections."
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
async def get_collection(collection_id: str, auth: AuthResult = Depends(require_read_permission)):
    """Get a specific collection with stats."""
    try:
        # Validate collection access
        validate_collection_access(auth, collection_id, "view")
        
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
        # Validate collection access
        validate_collection_access(auth, collection_id, "update")
        
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
        # Validate collection access
        validate_collection_access(auth, collection_id, "delete")
        
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
async def add_document_to_collection(collection_id: str, document_id: str, auth: AuthResult = Depends(require_manage_permission)):
    """Add a document to a collection."""
    try:
        # Validate access to the target collection
        validate_collection_access(auth, collection_id, "add documents to")
        
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
async def move_documents_to_collection(request: MoveDocumentsRequest, auth: AuthResult = Depends(require_manage_permission)):
    """Move multiple documents to a collection."""
    try:
        # Validate access to the target collection
        validate_collection_access(auth, request.target_collection_id, "move documents to")
        
        # If restricted, also need to verify we can access the source documents' collections
        # For simplicity, we validate target access here. Source access is implicitly granted
        # if they can see the documents in the first place (list filtering).
        
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(
            neo4j.move_documents_to_collection,
            request.document_ids, 
            request.target_collection_id,
        )
        moved = result["moved_count"]
        requested = len(request.document_ids)
        if moved == 0:
            message = "No documents were moved — none matched the given ids."
        elif moved < requested:
            message = f"Moved {moved} of {requested} document(s); {requested - moved} were not found."
        else:
            message = f"Successfully moved {moved} document(s)"
        return {
            "message": message,
            "moved_count": moved,
            "requested_count": requested,
        }
    except Exception as e:
        logger.error(f"Error moving documents to collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collections/{collection_id}/entities")
async def get_collection_entities(
    collection_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthResult = Depends(require_read_permission)
):
    """Get entities in a collection's knowledge graph."""
    try:
        # Validate collection access
        validate_collection_access(auth, collection_id, "view entities in")
        
        neo4j = get_neo4j_service()
        entities = await asyncio.to_thread(neo4j.get_collection_entities, collection_id, limit)
        return {"entities": entities, "total": len(entities)}
    except HTTPException:
        raise
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
    chain: Optional[List[str]] = None,
) -> None:
    """Background task for relationship analysis with progress tracking.

    When `chain` includes 'community_detection', a follow-up community-detection
    task is spawned automatically once this step finishes.
    """
    try:
        processor = get_document_processor()
        neo4j = get_neo4j_service()

        # If rebuild mode, delete batch-analysis relationships (preserve per-chunk from Step 1)
        if rebuild:
            update_task_progress(task_id, 0, 1, "Clearing batch relationships for full rebuild (preserving per-chunk)...")
            await asyncio.to_thread(neo4j.delete_batch_relationships)

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
        else:
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

        # Chain: spawn community detection as the next step's own task.
        if chain and chain[0] == "community_detection":
            settings = get_settings()
            com_task = create_task("community_detection")
            com_task.message = "Starting community detection..."
            _spawn_chain_task(_run_community_detection_task(
                com_task.task_id,
                min_size=settings.min_community_size,
                collection_id=collection_id,
            ))
            logger.info(f"Chained: spawned community_detection task {com_task.task_id}")

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
    chain: Optional[str] = Query(
        default=None,
        description="Comma-separated next pipeline steps to auto-run when this task finishes "
                    "(allowed: 'community_detection').",
    ),
    auth: AuthResult = Depends(require_manage_permission)
):
    """Analyze relationships between entities across documents.

    This triggers Phase B of the extraction pipeline: the main (large) model
    analyzes all entities and discovers cross-document relations.
    Run this after batch document processing to build the relationship graph.

    Optionally followed by community detection: POST /api/graph/communities/detect
    """
    try:
        settings = get_settings()
        if not settings.enable_graph_extraction:
            raise HTTPException(status_code=400, detail="Graph extraction is disabled")

        # Validate collection access if scoped
        if collection_id:
            validate_collection_access(auth, collection_id, "analyze relationships in")

        task = create_task("relationship_analysis")
        task.message = "Starting relationship analysis..." if not rebuild else "Starting full rebuild..."

        parsed_chain = _parse_chain(chain)
        background_tasks.add_task(
            _run_relationship_analysis_task,
            task.task_id,
            collection_id,
            scope,
            rebuild,
            parsed_chain,
        )

        return {
            "task_id": task.task_id,
            "status": task.status,
            "chain": parsed_chain,
            "message": f"Relationship analysis started. Poll /api/tasks/{task.task_id} for progress.",
            "tip": "Run POST /api/graph/communities/detect after this completes for community detection.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/graph/relationships")
async def delete_all_relationships(auth: AuthResult = Depends(require_manage_permission)):
    """Delete ALL relationships between entities."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_all_relationships)
        return result
    except Exception as e:
        logger.error(f"Error deleting all relationships: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/graph/entities")
async def delete_all_entities(auth: AuthResult = Depends(require_manage_permission)):
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
    auth: AuthResult = Depends(require_read_permission)
):
    """List all detected communities with server-side pagination and search.
    
    For restricted API keys, results are filtered to communities with at least one
    member entity from accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        result = await asyncio.to_thread(
            neo4j.list_communities_paginated, skip, limit, search, collection_filter
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
    collection_id: Optional[str] = Query(default=None, description="Scope to collection"),
    auth: AuthResult = Depends(require_manage_permission)
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
        
        # Validate collection access if scoped
        if collection_id:
            validate_collection_access(auth, collection_id, "detect communities in")
        
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
async def get_community(community_id: int, auth: AuthResult = Depends(require_read_permission)):
    """Get a specific community with its entities and relationships.
    
    For restricted API keys, validates the community has at least one member
    from accessible collections. Entity list is also filtered.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        community = await asyncio.to_thread(neo4j.get_community, community_id, collection_filter)
        if not community:
            raise HTTPException(status_code=404, detail="Community not found")
        return community
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting community: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/graph/communities/{community_id}")
async def delete_community(community_id: int, auth: AuthResult = Depends(require_manage_permission)):
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
async def delete_all_communities(auth: AuthResult = Depends(require_manage_permission)):
    """Delete ALL communities. Entities are unlinked but not deleted."""
    try:
        neo4j = get_neo4j_service()
        result = await asyncio.to_thread(neo4j.delete_all_communities)
        return result
    except Exception as e:
        logger.error(f"Error deleting all communities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/graph/communities/summarize")
async def summarize_communities(request: CommunitySummaryRequest, auth: AuthResult = Depends(require_manage_permission)):
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
    limit: int = Query(default=5, ge=1, le=20),
    auth: AuthResult = Depends(require_read_permission)
):
    """Search communities by their summary content.
    
    For restricted API keys, results are filtered to accessible collections.
    """
    try:
        neo4j = get_neo4j_service()
        collection_filter = auth.get_collection_filter()
        results = await asyncio.to_thread(neo4j.search_communities_by_content, query, limit, collection_filter)
        return {"query": query, "results": results}
    except Exception as e:
        logger.error(f"Error searching communities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Extended Thinking / Streaming Agentic RAG
# =============================================================================

@app.post("/api/ask/stream/thinking")
async def ask_with_thinking_stream(
    request: RAGRequest,
    auth: AuthResult = Depends(require_read_permission),
    _quota: None = Depends(enforce_query_quota),
    _rate: None = Depends(enforce_rate_limit),
    _track: None = Depends(track_ask_activity),
):
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
    # Validate collection access if a specific collection is requested
    if request.collection_id:
        validate_collection_access(auth, request.collection_id, "query")

    # Resolve effective collection scope for restricted API keys
    _stream_effective_collection_id = request.collection_id
    _stream_allowed_collection_ids: Optional[List[str]] = None

    if not _stream_effective_collection_id:
        _stream_collection_filter = auth.get_collection_filter()
        if _stream_collection_filter is not None:
            if len(_stream_collection_filter) == 1:
                _stream_effective_collection_id = _stream_collection_filter[0]
            else:
                _stream_allowed_collection_ids = _stream_collection_filter

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
                    collection_id=_stream_effective_collection_id,
                    allowed_collection_ids=_stream_allowed_collection_ids,
                    conversation_memory=request.conversation_memory,
                ):
                    yield f"data: {json.dumps(event)}\n\n"
            else:
                async for event in processor.agentic_rag_stream(
                    question=request.question,
                    top_k=request.top_k,
                    max_hops=request.max_hops,
                    conversation_history=request.conversation_history,
                    collection_id=_stream_effective_collection_id
                ):
                    yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error("Error in streaming agentic RAG: %s", e, exc_info=True)
            yield sse_error_frame(e)

    return StreamingResponse(
        with_sse_heartbeat(traced_sse(
            generate(),
            "ask.thinking",
            user_id=auth.key_id,
            tags=["endpoint:ask_stream_thinking", "mode:agentic"],
        )),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# =============================================================================
# Public Feature Flags Endpoint
# =============================================================================

@app.get("/api/features")
async def get_feature_flags(auth: AuthResult = Depends(require_read_permission)):
    """UI-relevant feature flags for non-admin clients.

    The full /api/admin/config is admin-only; this lightweight endpoint lets
    manage-permission UIs (e.g. the Add/Web Import page) gate features without
    exposing the rest of the system configuration.
    """
    settings = get_settings()
    return {
        "enable_collections": settings.enable_collections,
        "enable_skills": settings.enable_skills,
        "enable_git_integration": settings.enable_git_integration,
        # Web import needs both the master switch AND a configured crawl service.
        "enable_web_crawl": settings.enable_web_crawl and bool(settings.crawl_service_url),
    }


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
        openai_max_context=settings.openai_max_context,
        openai_max_output_tokens=settings.openai_max_output_tokens,
        extraction_model=settings.extraction_model,
        extraction_api_base=settings.extraction_api_base,
        extraction_max_context=settings.extraction_max_context,
        extraction_max_output_tokens=settings.extraction_max_output_tokens,
        relationship_max_context=settings.relationship_max_context,
        relationship_max_output_tokens=settings.relationship_max_output_tokens,
        relationship_batch_max_output_tokens=settings.relationship_batch_max_output_tokens,
        parallel_relationship_batches=settings.parallel_relationship_batches,
        relationship_target_ratio=settings.relationship_target_ratio,
        relationship_max_rounds=settings.relationship_max_rounds,
        relationship_max_hours=settings.relationship_max_hours,

        # Relationship Extraction Model
        relationship_model=settings.rel_extraction_model,
        relationship_api_base=settings.rel_extraction_api_base,
        concurrent_relations=settings.concurrent_relations,

        # Vision Model
        vision_model_available=settings.vision_model_available,
        vision_model=settings.vision_model or "Not configured",
        vision_api_base=settings.vision_model_api_base or settings.openai_api_base,
        vision_max_concurrent=settings.vision_max_concurrent,
        vision_max_output_tokens=settings.vision_max_output_tokens,
        vision_min_image_side=settings.vision_min_image_side,
        vision_max_image_side=settings.vision_max_image_side,
        vision_jpeg_quality=settings.vision_jpeg_quality,

        # Embedding Configuration
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        embedding_api_base=settings.embed_api_base,
        embedding_send_dimensions=settings.embedding_send_dimensions,
        embedding_max_input_tokens=settings.embedding_max_input_tokens,
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
        display_full_system_config=settings.display_full_system_config,
        
        # Security
        prompt_security=settings.prompt_security,

        # Privacy (LLM observability content handling)
        langfuse_tracing_active=settings.langfuse_tracing_active,
        langfuse_log_extended=settings.langfuse_log_extended,

        # Agent Skills
        enable_skills=settings.enable_skills,
        enable_skill_scripts=settings.enable_skill_scripts,
        max_skill_tools=settings.max_skill_tools,

        # Git Integration
        enable_git_integration=settings.enable_git_integration,

        # MDHarvest powered by Crawl4ai
        enable_web_crawl=settings.enable_web_crawl,
    )


# =============================================================================
# Admin Agent Skills Endpoints (agentskills.io)
# =============================================================================

@app.get("/api/admin/skills")
async def list_skills(auth: AuthResult = Depends(require_admin)):
    """List all installed agent skills."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    return skill_service.get_all_skills()


@app.get("/api/admin/skills/registry/search")
async def search_skill_registry(
    q: str = Query(..., description="Search query"),
    auth: AuthResult = Depends(require_admin),
):
    """Search the skills.sh registry for skills."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    return await skill_service.search_registry(q)


@app.get("/api/admin/skills/{skill_id}")
async def get_skill_detail(skill_id: str, auth: AuthResult = Depends(require_admin)):
    """Get full skill details including SKILL.md body and tools config."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    detail = skill_service.get_skill(skill_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return detail


@app.post("/api/admin/skills/install")
async def install_skill(
    request: SkillInstallRequest,
    auth: AuthResult = Depends(require_admin),
):
    """Install a skill from URL or skills.sh registry."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    try:
        if request.registry_id:
            return await skill_service.install_from_registry(request.registry_id)
        elif request.url:
            return await skill_service.install_from_url(request.url)
        else:
            raise HTTPException(status_code=400, detail="Provide either 'url' or 'registry_id'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch skill: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Skill installation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/admin/skills/{skill_id}")
async def update_skill(
    skill_id: str,
    request: SkillUpdateRequest,
    auth: AuthResult = Depends(require_admin),
):
    """Update a skill's settings (enable/disable)."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    result = skill_service.update_skill(skill_id, enabled=request.enabled)
    if not result:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return result


@app.delete("/api/admin/skills/{skill_id}")
async def delete_skill(skill_id: str, auth: AuthResult = Depends(require_admin)):
    """Uninstall a skill and delete its files."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    if not skill_service.delete_skill(skill_id):
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return {"message": f"Skill '{skill_id}' deleted"}


@app.post("/api/admin/skills/discover")
async def discover_skills(auth: AuthResult = Depends(require_admin)):
    """Re-scan the local skills directory for new skills."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    count = skill_service.discover_local_skills()
    return {"message": f"Discovered {count} skills", "count": count}


@app.post("/api/admin/skills/{skill_id}/analyze")
async def analyze_skill_config(skill_id: str, auth: AuthResult = Depends(require_admin)):
    """Analyze a skill's SKILL.md with the primary LLM to extract config + API base URL."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    try:
        result = await skill_service.analyze_skill_config(skill_id)
        return {
            "skill_id": skill_id,
            "variables": result.get("variables", []),
            "base_url": result.get("base_url"),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Skill analysis failed for '{skill_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/skills/{skill_id}/config")
async def get_skill_config(skill_id: str, auth: AuthResult = Depends(require_admin)):
    """Get a skill's configuration schema, base URL, and current values (secrets masked)."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    schema = skill_service.get_skill_config_schema(skill_id)
    values = skill_service.get_skill_config(skill_id)
    base_url = skill_service.get_skill_base_url(skill_id)

    # Mask secret values
    masked_values = {}
    secret_names = set()
    if schema:
        secret_names = {v["name"] for v in schema if v.get("type") == "secret"}
    for k, v in values.items():
        if k in secret_names and v:
            masked_values[k] = "********"
        else:
            masked_values[k] = v

    return {
        "skill_id": skill_id,
        "schema": schema,
        "values": masked_values,
        "base_url": base_url,
    }


@app.put("/api/admin/skills/{skill_id}/config")
async def save_skill_config(
    skill_id: str,
    request: SkillConfigSaveRequest,
    auth: AuthResult = Depends(require_admin),
):
    """Save a skill's configuration values. Masked values ('********') preserve existing secrets."""
    from app.services.skill_service import get_skill_service
    skill_service = get_skill_service()
    try:
        existing = skill_service.get_skill_config(skill_id)
        # Merge: preserve existing value if the new value is the mask placeholder
        merged = dict(existing)
        for k, v in request.values.items():
            if v == "********" and k in existing:
                continue  # keep existing secret
            merged[k] = v
        skill_service.save_skill_config(skill_id, merged)
        return {"message": "Configuration saved", "skill_id": skill_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# MDHarvest powered by Crawl4ai — web → markdown import
# =============================================================================
#
# Batch-harvest URLs into the knowledge base as clean markdown. cortex-app
# never embeds a browser/crawler stack; it calls a crawl4ai service over HTTP
# (services/crawl_client.py). Self-host points CRAWL_SERVICE_URL at the user's
# own crawl4ai; cloud points it at the shared per-host instance. Empty URL =>
# feature disabled (404). See .claude/domain/web-crawl.md.

def _require_web_crawl_enabled():
    settings = get_settings()
    if not settings.enable_web_crawl or not settings.crawl_service_url:
        raise HTTPException(status_code=404, detail="Web crawling is disabled")


def _crawl_slugify(text: str) -> str:
    """Filesystem-safe slug from a title (lowercase, hyphenated, <=100 chars)."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "page")[:100]


def _format_crawl_markdown(title: str, url: str, body: str) -> str:
    """Wrap crawled markdown with the canonical MDHarvest provenance header."""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"# {title}\n\n> Source: {url}\n> Extracted: {today}\n\n---\n\n{body}\n"


async def _run_web_import_task(
    task_id: str,
    urls: List[str],
    collection_id: Optional[str],
    content_filter: Optional[str],
    query: Optional[str],
):
    """Background runner: crawl each URL → store as markdown → process pending.

    Mirrors the git connector's two-phase shape: stage every harvested page as
    a PENDING document, then run the shared processing pass once at the end.
    """
    from app.services import crawl_client

    settings = get_settings()
    processor = get_document_processor()
    total = len(urls)
    succeeded: List[dict] = []
    failed: List[dict] = []
    update_task_progress(task_id, 0, total, "Starting web import...")

    sem = asyncio.Semaphore(max(1, int(settings.crawl_concurrency or 5)))
    done = 0
    counter_lock = asyncio.Lock()

    async def harvest_one(url: str):
        nonlocal done
        async with sem:
            try:
                res = await crawl_client.crawl_markdown(
                    url, content_filter=content_filter, query=query
                )
                file_content = _format_crawl_markdown(res["title"], url, res["markdown"])
                doc_id = str(uuid.uuid4())
                filename = f"{_crawl_slugify(res['title'])}.md"
                file_path = os.path.join(settings.custom_inputs_dir, f"{doc_id}.md")
                async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                    await f.write(file_content)
                file_size = len(file_content.encode("utf-8"))
                await processor.store_file_only(
                    file_path, filename, file_size, collection_id,
                    source=f"crawl:{urlparse(url).netloc}",
                )
                succeeded.append({"url": url, "title": res["title"]})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"web import failed for {url}: {e}")
                failed.append({"url": url, "error": str(e)})
            finally:
                async with counter_lock:
                    done += 1
                    update_task_progress(
                        task_id, done, total,
                        f"Crawled {done}/{total} ({len(failed)} failed)",
                    )

    await asyncio.gather(*[harvest_one(u) for u in urls])

    if not succeeded:
        fail_task(task_id, f"All {total} URL(s) failed to crawl")
        return

    # Phase 2: extract/embed the staged pages (shared with uploads/git).
    def progress(cur, tot, msg):
        update_task_progress(task_id, cur, tot, f"Processing: {msg}")

    try:
        proc_result = await processor.process_pending_documents(progress_callback=progress)
    except Exception as e:  # noqa: BLE001
        logger.error(f"web import processing failed: {e}")
        fail_task(task_id, f"Processing failed: {e}")
        return

    complete_task(task_id, {
        "imported": len(succeeded),
        "failed": len(failed),
        "total": total,
        "succeeded": succeeded,
        "failures": failed,
        "processing": proc_result,
    })


@app.post("/api/web-import", response_model=WebImportResponse)
async def web_import(
    request: WebImportRequest,
    auth: AuthResult = Depends(require_manage_permission),
):
    """Harvest one or more URLs into the knowledge base as markdown.

    Returns a task_id immediately; poll GET /api/tasks/{task_id} for progress.
    """
    _require_web_crawl_enabled()
    settings = get_settings()

    target_collection = request.collection_id or "default"
    validate_collection_access(auth, target_collection, "add content to")

    # Normalize + validate URLs (http/https only, dedup, preserve order).
    seen: set = set()
    urls: List[str] = []
    for raw in request.urls:
        u = (raw or "").strip()
        if not u:
            continue
        parsed = urlparse(u)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=400, detail=f"Invalid URL: {raw}")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    if not urls:
        raise HTTPException(status_code=400, detail="No valid http(s) URLs provided")

    cap = int(settings.crawl_max_urls_per_job or 0)
    if cap > 0 and len(urls) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"Too many URLs ({len(urls)}); this plan allows {cap} per job.",
        )

    # Enforce graph file/entity limits (same gate as custom-input).
    neo4j = get_neo4j_service()
    if settings.max_files > 0:
        stats = await asyncio.to_thread(neo4j.get_stats)
        if stats["document_count"] + len(urls) > settings.max_files:
            raise HTTPException(
                status_code=403,
                detail=f"File limit reached (max: {settings.max_files}). Upgrade your plan to add more documents.",
            )

    collection_id = request.collection_id
    if collection_id is None and settings.enable_collections:
        collection_id = settings.default_collection

    task = create_task("web_import")
    _spawn_chain_task(_run_web_import_task(
        task.task_id, urls, collection_id, request.content_filter, request.query
    ))
    return WebImportResponse(
        task_id=task.task_id,
        accepted_urls=len(urls),
        message=f"Web import started for {len(urls)} URL(s)",
    )


@app.post("/api/web-import/discover", response_model=WebDiscoverResponse)
async def web_import_discover(
    request: WebDiscoverRequest,
    auth: AuthResult = Depends(require_manage_permission),
):
    """Discover same-site candidate links on a page for selective import."""
    _require_web_crawl_enabled()
    from app.services import crawl_client

    url = (request.url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        result = await crawl_client.discover_links(url)
    except crawl_client.CrawlUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return WebDiscoverResponse(
        source_url=result["source_url"],
        domain=result["domain"],
        links=[WebDiscoverLink(**l) for l in result["links"]],
    )


# =============================================================================
# Git Integration Endpoints (GitHub / GitLab / Gitea connector)
# =============================================================================

def _require_git_enabled():
    if not get_settings().enable_git_integration:
        raise HTTPException(status_code=404, detail="Git integration is disabled")


def _mask_pat(pat: Optional[str]) -> str:
    if not pat:
        return "••••"
    return "••••" + pat[-4:]


def _git_conn_response(node: dict) -> GitConnectionResponse:
    """Build a masked API response from a stored GitConnection node dict."""
    return GitConnectionResponse(
        id=node["id"],
        vendor=node["vendor"],
        base_url=node.get("base_url"),
        repo_owner=node["repo_owner"],
        repo_name=node["repo_name"],
        pat_masked=node.get("pat_last4") and ("••••" + node["pat_last4"]) or _mask_pat(node.get("pat")),
        access_level=node.get("access_level", "read"),
        branch=node.get("branch"),
        default_branch=node.get("default_branch"),
        include_globs=node.get("include_globs", []) or [],
        exclude_globs=node.get("exclude_globs", []) or [],
        wiki_enabled=bool(node.get("wiki_enabled", False)),
        collection_id=node.get("collection_id"),
        sync_interval_minutes=int(node.get("sync_interval_minutes", 0) or 0),
        last_synced_sha=node.get("last_synced_sha"),
        last_synced_at=node.get("last_synced_at"),
        next_sync_due=node.get("next_sync_due"),
        sync_status=node.get("sync_status"),
        created_at=node.get("created_at"),
    )


@app.post("/api/integrations/git/verify", response_model=GitVerifyResponse)
async def verify_git_credentials(
    request: GitConnectionVerifyRequest,
    auth: AuthResult = Depends(require_admin),
):
    """Validate a PAT against the provider before creating a connection."""
    _require_git_enabled()
    from app.services.git_providers import get_provider, GitProviderError
    try:
        provider = get_provider(request.vendor.value, request.pat, request.base_url)
        result = await provider.verify()
        return GitVerifyResponse(valid=result.valid, login=result.login)
    except GitProviderError as e:
        return GitVerifyResponse(valid=False, message=str(e))
    except Exception as e:
        logger.warning(f"git verify failed: {e}")
        return GitVerifyResponse(valid=False, message="Verification failed")


@app.get("/api/integrations/git/browse", response_model=List[GitRepoBrowseItem])
async def browse_git_repos(
    vendor: str = Query(...),
    pat: str = Query(...),
    base_url: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    auth: AuthResult = Depends(require_admin),
):
    """List repositories the token can access, for the connection setup picker."""
    _require_git_enabled()
    from app.services.git_providers import get_provider, GitProviderError
    try:
        provider = get_provider(vendor, pat, base_url)
        repos = await provider.list_repos(page=page)
        return [
            GitRepoBrowseItem(
                owner=r.owner, name=r.name, full_name=r.full_name,
                default_branch=r.default_branch, private=r.private, web_url=r.web_url,
            )
            for r in repos
        ]
    except (GitProviderError, ValueError) as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/integrations/git/connections", response_model=GitConnectionResponse)
async def create_git_connection(
    request: GitConnectionCreate,
    auth: AuthResult = Depends(require_admin),
):
    """Create a git connection. Verifies the PAT and resolves the default branch first."""
    _require_git_enabled()
    from app.services.git_providers import get_provider, GitProviderError
    neo4j = get_neo4j_service()
    try:
        provider = get_provider(request.vendor.value, request.pat, request.base_url)
        verify = await provider.verify()
        if not verify.valid:
            raise HTTPException(status_code=400, detail="Invalid credentials")
        default_branch = await provider.default_branch(request.repo_owner, request.repo_name)
        if not default_branch:
            raise HTTPException(status_code=404, detail="Repository not found or inaccessible")
    except GitProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    connection_id = f"git_{uuid.uuid4().hex[:12]}"
    props = {
        "id": connection_id,
        "vendor": request.vendor.value,
        "base_url": request.base_url,
        "repo_owner": request.repo_owner,
        "repo_name": request.repo_name,
        "pat": get_crypto_service().encrypt(request.pat),
        "pat_last4": request.pat[-4:],
        "access_level": request.access_level.value,
        "branch": request.branch or default_branch,
        "default_branch": default_branch,
        "include_globs": request.include_globs,
        "exclude_globs": request.exclude_globs,
        "wiki_enabled": request.wiki_enabled,
        "collection_id": request.collection_id,
        "sync_interval_minutes": request.sync_interval_minutes,
        "last_synced_sha": None,
        "last_synced_at": None,
        "next_sync_due": None,
        "sync_status": "never_synced",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    node = neo4j.create_git_connection(props)
    return _git_conn_response(node)


@app.get("/api/integrations/git/connections", response_model=List[GitConnectionResponse])
async def list_git_connections(auth: AuthResult = Depends(require_admin)):
    """List all git connections (PATs masked)."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    return [_git_conn_response(n) for n in neo4j.list_git_connections()]


@app.get("/api/integrations/git/connections/{connection_id}", response_model=GitConnectionResponse)
async def get_git_connection(connection_id: str, auth: AuthResult = Depends(require_admin)):
    """Get a single git connection (PAT masked)."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    node = neo4j.get_git_connection(connection_id)
    if not node:
        raise HTTPException(status_code=404, detail="Connection not found")
    return _git_conn_response(node)


@app.patch("/api/integrations/git/connections/{connection_id}", response_model=GitConnectionResponse)
async def update_git_connection(
    connection_id: str,
    request: GitConnectionUpdate,
    auth: AuthResult = Depends(require_admin),
):
    """Update a git connection. PAT is rotated only when provided."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    existing = neo4j.get_git_connection(connection_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")

    props = {}
    data = request.model_dump(exclude_unset=True)
    if "pat" in data and data["pat"]:
        props["pat"] = get_crypto_service().encrypt(data["pat"])
        props["pat_last4"] = data["pat"][-4:]
    for key in ("branch", "include_globs", "exclude_globs", "wiki_enabled",
                "collection_id", "sync_interval_minutes"):
        if key in data:
            props[key] = data[key]
    if "access_level" in data and data["access_level"] is not None:
        props["access_level"] = data["access_level"].value if hasattr(data["access_level"], "value") else data["access_level"]

    node = neo4j.update_git_connection(connection_id, props)
    return _git_conn_response(node)


@app.delete("/api/integrations/git/connections/{connection_id}")
async def delete_git_connection(
    connection_id: str,
    purge_documents: bool = Query(default=False, description="Also delete all documents ingested from this connection"),
    auth: AuthResult = Depends(require_admin),
):
    """Delete a git connection. With purge_documents, also removes its ingested documents."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    existing = neo4j.get_git_connection(connection_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")

    purged = 0
    if purge_documents:
        for doc in neo4j.list_documents_for_git_connection(connection_id):
            neo4j.delete_relationships_by_source_document(doc["id"])
            neo4j.delete_document(doc["id"])
            purged += 1

    neo4j.delete_git_connection(connection_id)
    return {"message": "Connection deleted", "connection_id": connection_id, "documents_purged": purged}


async def _run_git_sync_task(connection_id: str, task_id: str):
    """Background runner: sync a git connection, reporting progress to the task store."""
    from app.services.git_connector_service import get_git_connector_service
    service = get_git_connector_service()
    update_task_progress(task_id, 0, 100, "Starting sync...")
    try:
        def progress(cur, total, msg):
            update_task_progress(task_id, cur, total, msg)
        result = await service.sync_connection(connection_id, task_id, progress=progress)
        result["connection_id"] = connection_id
        complete_task(task_id, result)
    except Exception as e:
        logger.error(f"git sync failed for {connection_id}: {e}")
        fail_task(task_id, str(e))


def _git_connection_has_active_sync(connection_id: str) -> bool:
    for t in _task_store.values():
        if (t.task_type == "git_repo_sync"
                and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
                and (t.result or {}).get("connection_id") == connection_id):
            return True
    return False


async def _git_sync_scheduler():
    """Periodically trigger syncs for connections whose scheduled interval is due."""
    settings = get_settings()
    interval_s = max(1, int(settings.git_sync_poll_interval or 5)) * 60
    while True:
        try:
            await asyncio.sleep(interval_s)
            neo4j = get_neo4j_service()
            now = datetime.now(timezone.utc)
            for conn in neo4j.list_git_connections():
                if int(conn.get("sync_interval_minutes") or 0) <= 0:
                    continue
                due = conn.get("next_sync_due")
                is_due = True
                if due:
                    try:
                        is_due = datetime.fromisoformat(due) <= now
                    except (ValueError, TypeError):
                        is_due = True
                if not is_due:
                    continue
                cid = conn["id"]
                if _git_connection_has_active_sync(cid):
                    continue
                task = create_task("git_repo_sync")
                task.result = {"connection_id": cid}
                asyncio.create_task(_run_git_sync_task(cid, task.task_id))
                logger.info(f"Scheduled git sync started for connection {cid}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"git sync scheduler tick failed: {e}")


@app.post("/api/integrations/git/connections/{connection_id}/sync", response_model=GitSyncTriggerResponse)
async def sync_git_connection(
    connection_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthResult = Depends(require_admin),
):
    """Trigger an incremental sync for a connection. Returns a task id to poll."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    conn = neo4j.get_git_connection(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    # Guard against a concurrent in-flight sync for this connection.
    for t in _task_store.values():
        if (t.task_type == "git_repo_sync" and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
                and (t.result or {}).get("connection_id") == connection_id):
            raise HTTPException(status_code=409, detail="A sync is already running for this connection")
    task = create_task("git_repo_sync")
    task.result = {"connection_id": connection_id}
    background_tasks.add_task(_run_git_sync_task, connection_id, task.task_id)
    return GitSyncTriggerResponse(
        task_id=task.task_id, connection_id=connection_id, message="Sync started",
    )


@app.get("/api/integrations/git/connections/{connection_id}/orphaned")
async def list_orphaned_git_documents(connection_id: str, auth: AuthResult = Depends(require_admin)):
    """List documents whose source file was removed from the repo (flagged for review)."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    if not neo4j.get_git_connection(connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"documents": neo4j.list_orphaned_git_documents(connection_id)}


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
    
    Collection scope options:
    - "all": Key can access all collections (default)
    - "restricted": Key can only access collections specified in allowed_collections
    """
    try:
        # Validate that if scope is restricted, we have at least one collection
        if request.collection_scope == CollectionScope.RESTRICTED:
            if not request.allowed_collections:
                raise HTTPException(
                    status_code=400, 
                    detail="At least one collection must be specified when scope is 'restricted'"
                )
            
            # Validate that all specified collections exist
            neo4j = get_neo4j_service()
            for coll_id in request.allowed_collections:
                collection = await asyncio.to_thread(neo4j.get_collection, coll_id)
                if not collection:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Collection not found: {coll_id}"
                    )
        
        api_key_service = get_api_key_service()
        result = api_key_service.create_api_key(
            name=request.name,
            permissions=request.permissions,
            created_by="admin",
            collection_scope=request.collection_scope,
            allowed_collections=request.allowed_collections
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


# =============================================================================
# Library Import/Export
# =============================================================================

@app.post("/api/admin/export")
async def start_library_export(
    background_tasks: BackgroundTasks,
    auth: AuthResult = Depends(require_admin),
):
    """Start a library export as a background task. Returns task_id for polling."""
    import tempfile as _tempfile

    # Concurrency guard
    for t in _task_store.values():
        if t.task_type in ("library_export", "library_import") and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            raise HTTPException(
                status_code=409,
                detail="An export or import is already in progress. Please wait for it to complete.",
            )

    from app.services.library_transfer_service import get_library_transfer_service
    transfer = get_library_transfer_service()

    task = create_task("library_export")
    export_dir = _tempfile.mkdtemp(prefix="cortex_export_")
    export_path = os.path.join(export_dir, f"cortex-export-{datetime.utcnow().strftime('%Y-%m-%d')}.zip")

    background_tasks.add_task(
        transfer.export_library,
        task.task_id,
        export_path,
        update_task_progress,
        complete_task,
        fail_task,
    )
    return {"task_id": task.task_id, "status": "pending", "message": "Export started"}


@app.get("/api/admin/export/{task_id}/download")
async def download_library_export(task_id: str, auth: AuthResult = Depends(require_admin)):
    """Download a completed library export ZIP file."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail=f"Export not ready. Status: {task.status.value}")
    if not task.result or not task.result.get("file_path"):
        raise HTTPException(status_code=404, detail="Export file not found")

    file_path = task.result["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export file has been cleaned up. Please re-export.")

    filename = os.path.basename(file_path)

    def stream_file():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        stream_file(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/admin/import")
async def start_library_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Query("clean", pattern="^(clean|replace)$"),
    auth: AuthResult = Depends(require_admin),
):
    """
    Upload a library export ZIP and start import as a background task.

    Modes:
    - clean: Requires the target instance to be empty (default)
    - replace: Wipes all existing data before importing
    """
    import tempfile as _tempfile

    # Concurrency guard
    for t in _task_store.values():
        if t.task_type in ("library_export", "library_import") and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            raise HTTPException(
                status_code=409,
                detail="An export or import is already in progress. Please wait for it to complete.",
            )

    # Save uploaded file to temp location
    tmp_fd, tmp_path = _tempfile.mkstemp(suffix=".zip", prefix="cortex_import_")
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                tmp_file.write(chunk)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}")

    from app.services.library_transfer_service import get_library_transfer_service
    transfer = get_library_transfer_service()

    task = create_task("library_import")
    background_tasks.add_task(
        transfer.import_library,
        task.task_id,
        tmp_path,
        mode,
        update_task_progress,
        complete_task,
        fail_task,
    )
    return {"task_id": task.task_id, "status": "pending", "message": f"Import started (mode: {mode})"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
