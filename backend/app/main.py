"""Cortex - FastAPI Backend."""

import os
import logging
import asyncio
import threading
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

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from starlette.concurrency import run_in_threadpool
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
    RuntimeSettingsUpdate,
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
    # x402 Payments models
    X402ConfigUpdate,
    X402ConfigResponse,
    X402VerifyResponse,
    X402EarningsResponse,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.document_processor import get_document_processor, get_query_processor
from app.services.graph_extractor import get_graph_extractor
from app.services.prompt_security import (
    validate_and_process_input,
    get_anti_injection_instruction,
    filter_stream,
    get_safe_refusal_message,
    wrap_untrusted,
)
from app.services.llm_config import get_llm_config, build_chat_params, make_async_openai_client, stream_usage_kwargs
from app.services import usage_meter
from app.services.prompt_guard_client import guard_user_question
from app.services.observability import traced_sse
from app.services.reasoning_config import safe_chat_completion, ReasoningMode
from app.services.auth_service import (
    require_api_key,
    require_read_permission,
    require_manage_permission,
    require_admin,
    AuthResult,
    invalidate_api_key_cache,
    validate_collection_access,
)
from app.services.api_key_service import get_api_key_service
from app.services.api_usage_service import get_api_usage_service
from app.services import x402_service
from app.services.x402_service import enforce_x402_payment
from app.services.audit_log import audit, get_audit_logger
from app.services.crypto_service import get_crypto_service, migrate_secrets_at_rest

# Configure logging (LOG_FORMAT=plain keeps the legacy format byte-identical;
# LOG_FORMAT=json emits one JSON object per line with request_id correlation)
from app.logging_setup import (
    configure as _configure_logging,
    get_request_id,
    new_request_id,
    rate_limited_warning,
    set_request_id,
)
from app import metrics

_configure_logging(getattr(get_settings(), "log_format", "plain"))
# Suppress Neo4j notification warnings about missing properties/relationships
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# Error tracking (GlitchTip/Sentry) — no-op unless SENTRY_DSN is set. Must run
# BEFORE `app = FastAPI(...)` below so the SDK's Starlette/FastAPI integrations
# hook the app while it is constructed; they capture unhandled exceptions ahead
# of the sanitizing exception handlers at the bottom of this file.
from app.services.error_tracking import init_sentry  # noqa: E402

init_sentry(service="backend")


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

# Set once initialize_schema() has succeeded (startup retry loop or the
# background retry task). /health reports it; deploy gates key off it.
_schema_initialized = False


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
    # 16 workers: transient-retry sleeps and slow queries hold a thread each,
    # so 8 stuck calls used to queue ALL to_thread DB access instance-wide.
    _api_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="api_")
    asyncio.get_event_loop().set_default_executor(_api_executor)

    # Start event loop watchdog to detect GIL/blocking issues
    watchdog_task = asyncio.create_task(_event_loop_watchdog())

    # Start the reranker idle-unload reaper (no-op in remote/disabled/TTL=0 mode)
    reranker_reaper_task = asyncio.create_task(_reranker_idle_reaper())
    
    # Create upload directory
    os.makedirs(settings.upload_dir, exist_ok=True)
    
    # Create custom inputs directory (for manually entered Q&A, text, markdown)
    os.makedirs(settings.custom_inputs_dir, exist_ok=True)
    
    # Initialize Neo4j schema. The neo4j container's healthcheck (HTTP :7474)
    # can pass before bolt auth is ready, so the first attempts may fail even
    # in a healthy deploy — retry with backoff instead of giving up. Running
    # without constraints/vector indexes means broken dedup and dead semantic
    # search, so if all retries fail we keep retrying in the background and
    # /health reports "degraded" (schema_initialized=false) until it lands.
    neo4j = get_neo4j_service()
    global _schema_initialized
    for attempt in range(1, 6):
        try:
            await asyncio.to_thread(neo4j.initialize_schema)
            _schema_initialized = True
            logger.info("Neo4j schema initialized")
            break
        except Exception as e:
            delay = min(2 ** attempt, 15)
            logger.warning(
                f"Neo4j schema init attempt {attempt}/5 failed: {e} — retrying in {delay}s"
            )
            await asyncio.sleep(delay)

    async def _schema_init_background_retry():
        global _schema_initialized
        while not _schema_initialized:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(neo4j.initialize_schema)
                _schema_initialized = True
                logger.info("Neo4j schema initialized (background retry)")
            except Exception as e:
                rate_limited_warning(
                    logger, "schema-init", f"Neo4j schema init still failing: {e}"
                )

    schema_retry_task = None
    if not _schema_initialized:
        schema_retry_task = asyncio.create_task(_schema_init_background_retry())

    # Wire the LLM-completion usage meter to Neo4j (unit-denominated quota).
    usage_meter.configure(get_neo4j_service)

    # Recover documents orphaned mid-processing by a previous shutdown/crash.
    # Processing runs as in-process background tasks, so anything left in a
    # transient state at startup can never resume on its own — it would spin
    # forever in the UI and keep `/api/instance/status` permanently
    # unsafe-to-redeploy. Reset them to 'pending' so they rejoin the queue.
    # Reconcile persisted task records from the previous process: anything
    # still pending/running can never resume (tasks are in-process coroutines)
    # — mark failed so pollers get a real answer instead of an eternal 404/202.
    interrupted_records: list[dict] = []
    try:
        interrupted_records = await asyncio.to_thread(neo4j.fail_interrupted_task_records)
        if interrupted_records:
            logger.warning(
                "Marked %d persisted task record(s) from the previous run as "
                "failed (interrupted by restart)", len(interrupted_records),
            )
    except Exception as e:
        logger.warning(f"Could not reconcile persisted task records: {e}")

    # Start the write-through persistence loop for the in-memory task store.
    task_persist_task = asyncio.create_task(_task_persist_loop())

    reset_ids: list[str] = []
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

    # Auto-resume the graph pipeline when the previous process died mid-run —
    # otherwise every fleet redeploy silently parks customers' in-flight work
    # until someone manually clicks "Generate Graph". Two independent signals:
    #   1. reset_ids — documents stranded mid-processing (Step 1 was running),
    #   2. an interrupted pipeline task record — covers runs killed between
    #      queueing and the first document (docs sit at 'pending', so the
    #      orphan-reset sees nothing) and Steps 2/3, which aren't per-document.
    # The interrupted record carries the persisted resume_context, so a
    # Generate Graph run resumes INTO its remaining chain (Steps 2/3) instead
    # of stopping after Step 1. Documents bulk-uploaded with
    # start_processing=false stay parked, as intended: no processing was
    # started for them, so no pipeline task record exists.
    pipeline_resume = _pick_interrupted_pipeline_step(interrupted_records)
    if settings.auto_resume_pending_on_startup and (reset_ids or pipeline_resume):

        async def _auto_resume_after_restart():
            await asyncio.sleep(5)  # let startup settle before heavy work
            try:
                if await _quota_exceeded():
                    logger.info(
                        "Auto-resume skipped: monthly usage limit reached; "
                        "interrupted pipeline work stays parked"
                    )
                    return
                step_type = pipeline_resume["task_type"] if pipeline_resume else "batch_processing"
                ctx = pipeline_resume["context"] if pipeline_resume else {}
                is_batch_step = step_type in ("batch_processing", "reprocess_batch")
                batch_ctx = ctx if is_batch_step else {}

                # Step 1 first: docs stranded mid-processing (reset_ids) or a
                # killed batch run. Runs even when the newest interrupted task
                # was Step 2/3 — an upload may have been processing alongside.
                # In that case the interrupted step is folded INTO the batch
                # chain (instead of run separately afterwards) so it's persisted
                # with the batch task and survives a second kill mid-resume.
                ran_chain: list = []
                if reset_ids or is_batch_step:
                    chain = batch_ctx.get("chain") or None
                    if step_type == "relationship_analysis":
                        chain = ["relationship_analysis", *(ctx.get("chain") or [])]
                    elif step_type == "community_detection":
                        chain = ["community_detection"]
                    concurrency = batch_ctx.get("concurrency") or settings.batch_processing_concurrency
                    pending = await asyncio.to_thread(
                        get_document_processor().get_pending_documents
                    )
                    if pending or reset_ids or chain:
                        task = create_task("batch_processing", resume_context={
                            "concurrency": concurrency, "chain": chain,
                        })
                        task.message = (
                            f"Auto-resuming processing of {len(pending)} document(s) "
                            "interrupted by restart..."
                        )
                        logger.info(
                            "Auto-resuming batch processing for %d pending document(s) "
                            "interrupted by the previous shutdown (chain=%s)",
                            len(pending), chain,
                        )
                        await _run_batch_processing_task(task.task_id, concurrency, chain)
                        ran_chain = chain or []
                    else:
                        logger.info(
                            "Auto-resume: no pending documents and no remaining "
                            "pipeline chain — nothing to do"
                        )

                # A killed Step 2/3 with its own record — run it directly
                # (with its full persisted scope) unless it was folded into
                # the batch chain above.
                if step_type == "relationship_analysis" and "relationship_analysis" not in ran_chain:
                    task = create_task("relationship_analysis", resume_context=ctx)
                    task.message = "Auto-resuming deep relationship analysis interrupted by restart..."
                    logger.info(
                        "Auto-resuming relationship analysis interrupted by the "
                        "previous shutdown (chain=%s)", ctx.get("chain"),
                    )
                    await _run_relationship_analysis_task(
                        task.task_id,
                        collection_id=ctx.get("collection_id"),
                        scope=ctx.get("scope") or "full",
                        rebuild=bool(ctx.get("rebuild")),
                        chain=ctx.get("chain") or None,
                    )
                elif step_type == "community_detection" and "community_detection" not in ran_chain:
                    task = create_task("community_detection", resume_context=ctx)
                    task.message = "Auto-resuming community detection interrupted by restart..."
                    logger.info(
                        "Auto-resuming community detection interrupted by the "
                        "previous shutdown"
                    )
                    await _run_community_detection_task(
                        task.task_id,
                        min_size=ctx.get("min_size") or settings.min_community_size,
                        collection_id=ctx.get("collection_id"),
                    )
            except Exception as e:
                logger.warning(f"Startup auto-resume failed: {e}")

        _spawn_chain_task(_auto_resume_after_restart())

    # Resume image analysis killed by the previous shutdown. Image analysis
    # runs as fire-and-forget futures AFTER a document completes, so the
    # orphan-reset above never sees these: the document sits at 'completed'
    # with image_progress_current < total forever, Step 1 reads as stuck,
    # and no LLM traffic flows. Re-extract images via Docling re-conversion
    # (CPU only) and analyze ONLY the ones whose chunk isn't stored yet —
    # already-paid vision/extraction work is never redone.
    if settings.auto_resume_image_analysis:
        try:
            stuck_image_docs = neo4j.get_documents_with_incomplete_image_analysis()
        except Exception as e:
            stuck_image_docs = []
            logger.warning(f"Could not scan for incomplete image analysis: {e}")
        if stuck_image_docs:

            async def _resume_image_analysis_after_restart():
                await asyncio.sleep(10)  # let startup (and text auto-resume) settle
                try:
                    if await _quota_exceeded():
                        logger.info(
                            "Image-analysis resume skipped: monthly usage "
                            "limit reached; documents keep their partial "
                            "image progress"
                        )
                        return
                    task = create_task("image_analysis_resume")
                    total_missing = sum(
                        (d.get("image_progress_total") or 0)
                        - (d.get("image_progress_current") or 0)
                        for d in stuck_image_docs
                    )
                    logger.warning(
                        "Resuming image analysis for %d document(s) with "
                        "~%d unanalyzed image(s) left behind by the previous "
                        "shutdown", len(stuck_image_docs), total_missing,
                    )
                    processor = get_document_processor()
                    resumed = failed = 0
                    for i, doc in enumerate(stuck_image_docs):
                        update_task_progress(
                            task.task_id, i, len(stuck_image_docs),
                            f"Resuming image analysis: {doc.get('filename') or doc['id']} "
                            f"({i + 1}/{len(stuck_image_docs)})...",
                        )
                        try:
                            if await processor.resume_image_analysis(doc):
                                resumed += 1
                        except Exception as e:
                            failed += 1
                            logger.error(
                                "Image-analysis resume failed for document "
                                "%s (%s): %s — will retry on next startup",
                                doc["id"], doc.get("filename"), e,
                            )
                    complete_task(task.task_id, {
                        "documents_scanned": len(stuck_image_docs),
                        "documents_resumed": resumed,
                        "documents_failed": failed,
                    })
                    logger.info(
                        "Image-analysis resume finished: %d resumed, %d "
                        "failed, %d reconciled",
                        resumed, failed,
                        len(stuck_image_docs) - resumed - failed,
                    )
                except Exception as e:
                    logger.warning(f"Startup image-analysis resume failed: {e}")

            _spawn_chain_task(_resume_image_analysis_after_restart())

    # One-time, idempotent backfill of the degraded-document signals
    # (Chunk.has_embedding + Document.entity_count for data that predates
    # them). Runs as a non-blocking background task — on a large knowledge
    # base the batched updates can take a while and must not delay startup.
    async def _backfill_degraded_signals():
        try:
            summary = await asyncio.to_thread(
                neo4j.backfill_degraded_document_signals,
                settings.enable_graph_extraction,
            )
            logger.info(
                "Degraded-signal backfill complete: %d chunk(s) got "
                "has_embedding, %d completed document(s) got entity_count",
                summary["chunks_backfilled"],
                summary["documents_backfilled"],
            )
        except Exception as e:
            logger.warning(
                f"Degraded-signal backfill failed (retried next startup): {e}"
            )

    backfill_task = asyncio.create_task(_backfill_degraded_signals())

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

    # Final write-through flush so terminal task states from this run survive
    # the restart (the reconcile above only has to catch true crashes).
    try:
        await _flush_dirty_tasks()
    except Exception:
        pass

    background_tasks = [watchdog_task, reranker_reaper_task, backfill_task,
                        task_persist_task]
    if schema_retry_task:
        background_tasks.append(schema_retry_task)
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

    # Close the shared prompt-guard HTTP client (no-op when unused).
    try:
        from app.services.prompt_guard_client import close_async_client as close_guard_client
        await close_guard_client()
    except Exception:
        pass

    # Flush + shut down Langfuse so buffered traces are delivered (no-op when
    # tracing is inactive). The lifespan has the stop grace period to drain.
    try:
        from app.services.observability import shutdown_langfuse
        shutdown_langfuse()
    except Exception:
        pass

    # Persist any LLM-completion counts still buffered in the usage meter.
    try:
        await asyncio.to_thread(usage_meter.flush_now)
    except Exception:
        pass

    # Close the audit log file handle (no-op when auditing is disabled).
    try:
        get_audit_logger().close()
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

# Body-size enforcement. Registered BEFORE CORS so it sits inside the CORS
# wrapper — its 413 responses then pass through CORSMiddleware on the way out
# and stay readable from the cross-origin frontend (app vs api- subdomains).
from app.body_limit import BodySizeLimitMiddleware  # noqa: E402

app.add_middleware(BodySizeLimitMiddleware)

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
    # x402 protocol headers ride on 402/200 responses; without exposure,
    # browser-based agents can't read them (server-side clients are fine).
    expose_headers=["PAYMENT-REQUIRED", "PAYMENT-RESPONSE", "X-Request-ID"],
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

    # Only X-API-Key is a trusted identity here; the backend never authenticates
    # via Authorization, so bucketing on it would let a caller mint unlimited
    # fresh buckets by rotating that header. Always enforce a per-IP bucket, and
    # additionally a per-key bucket when a key is present — a rotated/forged key
    # header can therefore never escape the per-IP cap.
    api_key = request.headers.get("X-API-Key")
    client_ip = request.client.host if request.client else None
    limiter = get_rate_limiter()
    burst = getattr(settings, "rate_limit_burst", 10)

    bucket_keys = [rate_limit_key(None, client_ip)]
    if api_key:
        bucket_keys.append(rate_limit_key(api_key, client_ip))

    retry_after = 0.0
    denied = False
    for bucket_key in bucket_keys:
        allowed, ra = limiter.check(bucket_key, qpm, burst)
        if not allowed:
            denied = True
            retry_after = max(retry_after, ra)
    if denied:
        metrics.RATE_LIMITED.labels(route=request.url.path).inc()
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({qpm} requests/minute). Slow down.",
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )


def _ensure_disk_space(incoming_bytes: int = 0) -> None:
    """Refuse new data when the uploads filesystem is nearly full (507).

    Disk-full corrupts Neo4j checkpoints, so rejecting an upload early is
    strictly safer than accepting it and letting the volume fill. Guarded by
    MIN_FREE_DISK_MB (0 disables). incoming_bytes is added to the requirement
    when the payload size is known up front.
    """
    settings = get_settings()
    floor_mb = settings.min_free_disk_mb
    if floor_mb <= 0:
        return
    try:
        free = shutil.disk_usage(settings.upload_dir).free
    except OSError:
        return  # can't measure — don't block ingestion on a stat failure
    required = floor_mb * 1024 * 1024 + max(0, incoming_bytes)
    if free < required:
        metrics.UPLOADS_REJECTED_DISK.inc()
        logger.error(
            "Rejecting ingest: %d MB free on uploads filesystem, %d MB required "
            "(MIN_FREE_DISK_MB=%d + incoming %d bytes)",
            free // (1024 * 1024), required // (1024 * 1024), floor_mb, incoming_bytes,
        )
        raise HTTPException(
            status_code=507,
            detail=(
                "Insufficient storage on this instance. Free up space "
                "(delete documents or old data) or contact your operator."
            ),
        )


async def _quota_exceeded() -> bool:
    """Whether the monthly LLM-completion quota is exhausted.

    MAX_QUERIES_PER_MONTH is denominated in internal LLM completions (each
    Q&A-loop completion, extraction call, vision call, ... consumes one unit),
    counted at the client-factory choke point and read here. 0 = unlimited.
    """
    settings = get_settings()
    if settings.max_queries_per_month <= 0:
        return False
    counts = await asyncio.to_thread(usage_meter.get_completions_this_month)
    return counts["total"] >= settings.max_queries_per_month


def _quota_429(detail: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=detail,
        headers={"Retry-After": str(_seconds_until_next_utc_month())},
    )


async def enforce_query_quota() -> None:
    """Reject chat requests with 429 once MAX_QUERIES_PER_MONTH is hit.

    No-op when the env var is unset (default 0 = unlimited). Requests that
    pass the gate run to completion — in-flight answers are never cut off
    mid-stream by the quota.
    """
    settings = get_settings()

    # Stamp this context as query work so its LLM completions are attributed
    # to "query" in the usage meter (inherited by tasks the handler spawns).
    usage_meter.set_usage_kind(usage_meter.KIND_QUERY)

    # Kick off the reranker load now (non-blocking) so its ~7 s cold start
    # overlaps the query-analysis LLM call + embedding + search that run before
    # reranking, rather than stalling the rerank step. No-op in remote/disabled
    # mode or once the model is loaded.
    try:
        get_query_processor().prewarm_reranker()
    except Exception:
        pass

    if await _quota_exceeded():
        raise _quota_429(
            f"Monthly usage limit reached "
            f"(max: {settings.max_queries_per_month} LLM completions). "
            f"Upgrade your plan or wait until next month."
        )


async def enforce_processing_quota() -> None:
    """Reject new document/graph processing with 429 once the quota is hit.

    Document processing consumes LLM completions (extraction, relationships,
    vision), so it draws from the same MAX_QUERIES_PER_MONTH pool. In-flight
    documents always finish; this gate only blocks *starting* new work.
    """
    settings = get_settings()
    if await _quota_exceeded():
        raise _quota_429(
            f"Monthly usage limit reached "
            f"(max: {settings.max_queries_per_month} LLM completions). "
            f"Document processing is paused until next month or a plan upgrade."
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
            # Validate and get key_id (short-TTL cached; the route dependency
            # doing the authoritative check right after hits the same cache).
            from app.services.auth_service import validate_api_key
            auth = await validate_api_key(api_key)
            if auth.is_authenticated and auth.key_id:
                key_id = auth.key_id
                is_admin_key = auth.is_admin
            elif not auth.service_error:
                # Authentication event: a key was presented and rejected.
                # (service_error means the key couldn't be checked, which is
                # an availability incident, not a rejection.)
                audit(
                    "auth.key_rejected", outcome="denied",
                    method=request.method, path=request.url.path,
                )

        # Process the request
        response = await call_next(request)

        # Audit trail (ENABLE_AUDIT_LOG): key-attributed mutating requests
        # (uploads, deletions, config/key changes) plus search/ask activity,
        # and every 401/403 as an authentication event. Metadata only.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            actor = key_id or ("admin" if is_admin_key else None)
            if response.status_code in (401, 403):
                audit(
                    "auth.unauthorized", actor=actor, outcome="denied",
                    method=request.method, path=request.url.path,
                    status=response.status_code,
                )
            else:
                audit(
                    "api.request", actor=actor,
                    outcome="ok" if response.status_code < 400 else "error",
                    method=request.method, path=request.url.path,
                    status=response.status_code,
                )

        # Record usage if we have a valid key_id
        if key_id:
            # Check if we should skip tracking for admin key
            app_settings = get_settings()
            if is_admin_key and not app_settings.track_admin_api_key_usage:
                return response
            
            try:
                # 402 is excluded: it's not a failure but the first leg of
                # every x402 payment handshake (challenge → pay → retry).
                # Counting challenges as errors would pin a healthy monetized
                # key's error rate near 50% forever.
                is_error = response.status_code >= 400 and response.status_code != 402
                error_message = None
                if is_error:
                    error_message = f"HTTP {response.status_code}"

                usage_service = get_api_usage_service()
                # Sync Neo4j writes — threadpool keeps them off the event loop
                # so slow usage bookkeeping can't stall unrelated requests.
                await run_in_threadpool(
                    usage_service.record_request,
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
    # x402: settle-before-serve stashes the SettlementResponse on
    # request.state (enforce_x402_payment); emit it here so streaming
    # responses carry it before the first body byte.
    payment_response = getattr(request.state, "x402_payment_response", None)
    if payment_response:
        response.headers[x402_service.HEADER_PAYMENT_RESPONSE] = payment_response
    return response


# =============================================================================
# 5xx sanitization — server errors never leak internals to clients in
# production. Endpoints across main.py raise HTTPException(500, detail=str(e));
# the detail is useful in development and in server logs, but in production it
# can carry bolt URIs, provider error bodies, or file paths. These handlers
# keep the full detail in logs (with request id) and return a generic message.
# The SSE paths already enforce this policy separately (sse_error_frame).
# =============================================================================

from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402
from fastapi.exception_handlers import (  # noqa: E402
    http_exception_handler as _default_http_exception_handler,
)

_GENERIC_5XX_DETAIL = "Internal server error. Check server logs for details."


def _sanitized_error_response(status_code: int) -> JSONResponse:
    rid = get_request_id()
    body = {"detail": _GENERIC_5XX_DETAIL}
    headers = {}
    if rid:
        body["request_id"] = rid
        headers["X-Request-ID"] = rid
    return JSONResponse(status_code=status_code, content=body, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def sanitized_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code >= 500 and get_settings().is_production:
        logger.error(
            f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}"
        )
        return _sanitized_error_response(exc.status_code)
    return await _default_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
    if get_settings().is_production:
        return _sanitized_error_response(500)
    rid = get_request_id()
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}", "request_id": rid or "-"},
        headers={"X-Request-ID": rid} if rid else {},
    )


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

# In-memory task store — the live source of truth for this process. A
# write-through shadow is persisted to Neo4j (TaskRecord nodes) by
# _task_persist_loop so a restart doesn't turn every in-flight task id into a
# 404: startup marks persisted pending/running records failed ("interrupted by
# server restart") and the task endpoints fall back to the record when the id
# is no longer in memory.
_task_store: Dict[str, TaskProgress] = {}

# Task ids mutated since the last persist flush. Helpers below are called from
# the event loop AND from worker threads (processing progress callbacks), so
# writes go through this dirty-set + periodic flusher instead of hitting Neo4j
# inline. set.add() is atomic under the GIL — no lock needed.
_task_dirty: set = set()

_TASK_PERSIST_INTERVAL_S = 3.0
_TASK_CLEANUP_INTERVAL_S = 3600.0
_TASK_RESULT_JSON_MAX = 200_000  # cap persisted result size (Neo4j property)

# Last time each task was created/progressed (monotonic). Every live task
# heartbeats through update_task_progress — even 10h+ graph rebuilds report
# per-document progress — so a PENDING/RUNNING task silent for this long has
# lost its coroutine (cancelled, killed by a BaseException outside its
# try-block) and will never complete on its own.
_task_last_touch: Dict[str, float] = {}
_TASK_STALE_REAP_S = 2 * 3600.0


def _serialize_task(task: TaskProgress) -> dict:
    """Flatten a TaskProgress for storage as a Neo4j node (no nested maps)."""
    result_json = None
    if task.result is not None:
        try:
            result_json = json.dumps(task.result, default=str)
            if len(result_json) > _TASK_RESULT_JSON_MAX:
                result_json = json.dumps({"truncated": True})
        except (TypeError, ValueError):
            result_json = json.dumps({"unserializable": True})
    context_json = None
    if task.resume_context:
        try:
            context_json = json.dumps(task.resume_context, default=str)
        except (TypeError, ValueError):
            context_json = None
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status.value,
        "progress_current": task.progress_current,
        "progress_total": task.progress_total,
        "progress_percent": task.progress_percent,
        "message": task.message,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "error": task.error,
        "result_json": result_json,
        "context_json": context_json,
    }


def _deserialize_task_record(record: dict) -> TaskProgress:
    """Rebuild a TaskProgress from a persisted TaskRecord node."""
    result = None
    if record.get("result_json"):
        try:
            result = json.loads(record["result_json"])
        except (TypeError, ValueError):
            result = None
    resume_context = None
    if record.get("context_json"):
        try:
            resume_context = json.loads(record["context_json"])
        except (TypeError, ValueError):
            resume_context = None

    def _parse_dt(value):
        try:
            return datetime.fromisoformat(value) if value else None
        except (TypeError, ValueError):
            return None

    return TaskProgress(
        task_id=record["task_id"],
        task_type=record.get("task_type", "unknown"),
        status=TaskStatus(record.get("status", "failed")),
        progress_current=record.get("progress_current", 0),
        progress_total=record.get("progress_total", 0),
        progress_percent=record.get("progress_percent", 0.0),
        message=record.get("message", ""),
        started_at=_parse_dt(record.get("started_at")),
        completed_at=_parse_dt(record.get("completed_at")),
        error=record.get("error"),
        result=result,
        resume_context=resume_context,
    )


async def _flush_dirty_tasks() -> None:
    """Persist every dirty task snapshot; re-mark on failure for the next tick."""
    if not _task_dirty:
        return
    dirty_ids = list(_task_dirty)
    _task_dirty.difference_update(dirty_ids)
    records = [
        _serialize_task(task)
        for task_id in dirty_ids
        if (task := _task_store.get(task_id)) is not None
    ]
    if not records:
        return
    try:
        neo4j = get_neo4j_service()
        await asyncio.to_thread(neo4j.upsert_task_records, records)
    except Exception as e:
        _task_dirty.update(dirty_ids)  # retry next tick
        rate_limited_warning(
            logger, "task-persist-flush",
            f"Task persistence flush failed ({len(records)} task(s)): {e}",
        )


async def _hourly_maintenance() -> None:
    """Hourly housekeeping run from the persist loop."""
    removed = cleanup_old_tasks(max_age_hours=24)
    pruned = await asyncio.to_thread(get_neo4j_service().prune_task_records, 7)
    if removed or pruned:
        logger.info(
            f"Task cleanup: {removed} in-memory, {pruned} persisted record(s) removed"
        )
    # Documents stranded in 'processing' with no live task (e.g. the failure
    # status write itself lost Neo4j past its retries) — reset to pending.
    try:
        from app.services.document_processor import get_active_processing_ids
        stranded = await asyncio.to_thread(
            get_neo4j_service().reset_stranded_processing_documents,
            get_active_processing_ids(),
        )
        if stranded:
            logger.warning(
                f"Stranded-document sweep reset {len(stranded)} document(s) "
                f"to pending: {stranded[:5]}"
            )
    except Exception as e:
        logger.warning(f"Stranded-document sweep failed: {e}")
    # Abandoned chunked import-upload sessions (otherwise only swept when the
    # next import upload starts, which may be never).
    try:
        await asyncio.to_thread(_purge_stale_import_uploads)
    except Exception as e:
        logger.warning(f"Import upload sweep failed: {e}")


async def _task_persist_loop():
    """Flush dirty task snapshots every few seconds; run maintenance hourly."""
    last_cleanup = time.monotonic()
    while True:
        try:
            await asyncio.sleep(_TASK_PERSIST_INTERVAL_S)
            await _flush_dirty_tasks()
            if time.monotonic() - last_cleanup >= _TASK_CLEANUP_INTERVAL_S:
                last_cleanup = time.monotonic()
                await _hourly_maintenance()
        except asyncio.CancelledError:
            break
        except Exception as e:
            rate_limited_warning(
                logger, "task-persist-loop", f"Task persist loop tick failed: {e}"
            )


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


def create_task(task_type: str, resume_context: Optional[dict] = None) -> TaskProgress:
    """Create a new task and return its progress tracker.

    `resume_context` holds the parameters needed to restart this step if the
    process dies mid-run (pipeline `chain`, concurrency, scope/rebuild, ...).
    It is persisted with the task record and consumed by the startup
    auto-resume — without it, a restart during a Generate Graph run would
    silently drop the remaining Steps 2/3.
    """
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task = TaskProgress(
        task_id=task_id,
        task_type=task_type,
        status=TaskStatus.PENDING,
        started_at=datetime.utcnow(),
        resume_context=resume_context,
    )
    _task_store[task_id] = task
    _task_dirty.add(task_id)
    _task_last_touch[task_id] = time.monotonic()
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
        _task_dirty.add(task_id)
        _task_last_touch[task_id] = time.monotonic()


def complete_task(task_id: str, result: dict) -> None:
    """Mark a task as completed with results."""
    task = _task_store.get(task_id)
    if task:
        task.status = TaskStatus.COMPLETED
        task.progress_percent = 100.0
        task.completed_at = datetime.utcnow()
        task.result = result
        task.message = "Completed successfully"
        _task_dirty.add(task_id)


def fail_task(task_id: str, error: str) -> None:
    """Mark a task as failed."""
    task = _task_store.get(task_id)
    if task:
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.utcnow()
        task.error = error
        task.message = f"Failed: {error}"
        _task_dirty.add(task_id)


def cleanup_old_tasks(max_age_hours: int = 24) -> int:
    """Remove completed/failed tasks older than max_age_hours; reap dead ones.

    A PENDING/RUNNING task whose coroutine died without reaching
    complete_task/fail_task has no completed_at, so the age-out alone would
    keep it in the store forever — permanently reporting the instance as
    unsafe to redeploy. Any such task silent past _TASK_STALE_REAP_S is marked
    failed here (see _task_last_touch) and then ages out normally.
    """
    now = datetime.utcnow()
    mono = time.monotonic()
    for task_id, task in list(_task_store.items()):
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING) and not task.completed_at:
            touched = _task_last_touch.get(task_id)
            if touched is not None:
                stale = mono - touched >= _TASK_STALE_REAP_S
            else:  # no touch record (shouldn't happen) — fall back to start time
                started = task.started_at or now
                stale = (now - started).total_seconds() >= _TASK_STALE_REAP_S
            if stale:
                logger.warning(
                    f"Reaping dead task {task_id} ({task.task_type}): no progress "
                    f"heartbeat for {int(_TASK_STALE_REAP_S / 3600)}h"
                )
                fail_task(task_id, "task stalled with no progress heartbeat — reaped as dead")
    to_remove = []
    for task_id, task in _task_store.items():
        if task.completed_at:
            age = (now - task.completed_at).total_seconds() / 3600
            if age > max_age_hours:
                to_remove.append(task_id)
    for task_id in to_remove:
        del _task_store[task_id]
        _task_last_touch.pop(task_id, None)
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


_RESUMABLE_PIPELINE_TASK_TYPES = {
    "batch_processing", "reprocess_batch",
    "relationship_analysis", "community_detection",
}


def _pick_interrupted_pipeline_step(records: List[dict]) -> Optional[dict]:
    """From the previous process's interrupted task records, pick the pipeline
    step to auto-resume (the most recently started one) and decode its
    persisted resume context (chain, concurrency, scope, ...)."""
    candidates = [
        r for r in records or []
        if r.get("task_type") in _RESUMABLE_PIPELINE_TASK_TYPES
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda r: r.get("started_at") or "")
    context: dict = {}
    if newest.get("context_json"):
        try:
            context = json.loads(newest["context_json"]) or {}
        except (TypeError, ValueError):
            context = {}
    return {"task_type": newest["task_type"], "context": context}


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
        # Not in the live store — fall back to the persisted shadow record.
        # After a restart this returns the task's last known state (terminal
        # states survive as-is; interrupted ones were marked failed at boot).
        record = await asyncio.to_thread(get_neo4j_service().get_task_record, task_id)
        if record:
            return _deserialize_task_record(record)
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found — it may have been cleaned up "
                   "after the retention window. Re-run the operation if needed.",
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
        # Fall back to the persisted shadow record (restart survivor).
        record = await asyncio.to_thread(get_neo4j_service().get_task_record, task_id)
        if record:
            task = _deserialize_task_record(record)
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Task {task_id} not found — it may have been cleaned up "
                       "after the retention window. Re-run the operation if needed.",
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
    _task_dirty.discard(task_id)
    _task_last_touch.pop(task_id, None)
    try:
        await asyncio.to_thread(get_neo4j_service().delete_task_record, task_id)
    except Exception as e:
        logger.warning(f"Could not delete persisted record for task {task_id}: {e}")
    return {"message": f"Task {task_id} removed"}


@app.post("/api/graph/generation/abort")
async def abort_graph_generation(auth: AuthResult = Depends(require_manage_permission)):
    """
    Abort an in-flight Generate/Regenerate Graph run.

    Unlike ``DELETE /api/tasks/{id}`` (which only drops a task record and leaves
    the work running), this actually STOPS the pipeline:
      1. cancels Step-1 per-document processing via the document processor's
         cancellation flags (``_check_cancellation`` raises at the next checkpoint),
      2. cancels the backend-orchestrated Step-2/3 chain tasks so they neither
         spawn nor continue,
      3. clears the in-flight pipeline task records so the UI returns to an idle,
         re-runnable state.

    Non-destructive: documents, chunks, entities, relationships, communities and
    API keys are all left untouched — the user can rebuild the graph afterward.
    """
    processor = get_document_processor()

    # 1. Stop Step-1 per-document processing (flags + asyncio task cancel + wait).
    docs_cancelled = await processor.cancel_all_processing()

    # 2. Cancel the backend chain (the Step 1→2→3 spawns plus any running
    #    relationship_analysis / community_detection coroutine live here).
    chain_cancelled = 0
    for t in list(_chain_tasks):
        if not t.done():
            t.cancel()
            chain_cancelled += 1

    # 3. Drop the in-flight pipeline task records so the frontend clears.
    pipeline_types = {
        "batch_processing", "reprocess_batch",
        "relationship_analysis", "community_detection",
    }
    inflight = {TaskStatus.PENDING, TaskStatus.RUNNING}
    removed = []
    for tid, task in list(_task_store.items()):
        if task.task_type in pipeline_types and task.status in inflight:
            removed.append(tid)
            _task_store.pop(tid, None)
            _task_dirty.discard(tid)
            _task_last_touch.pop(tid, None)
            try:
                await asyncio.to_thread(get_neo4j_service().delete_task_record, tid)
            except Exception as e:
                logger.warning(f"abort: could not delete task record {tid}: {e}")

    # 4. Backstop: SIGKILL any docling_worker subprocess that outlived
    #    cancellation. Cancellation propagation isn't instantaneous and some
    #    paths (e.g. a converter mid-subprocess) can lag; abort means "stop
    #    everything", so killing every converter is correct and guarantees the
    #    CPU/RAM is freed immediately rather than after the next checkpoint.
    import glob as _glob
    import signal as _signal
    workers_killed = 0
    for _pth in _glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{_pth}/cmdline", "rb") as _fh:
                if b"docling_worker" in _fh.read():
                    os.kill(int(_pth.rsplit("/", 1)[-1]), _signal.SIGKILL)
                    workers_killed += 1
        except (OSError, ValueError):
            pass

    # 5. Reset docs stranded mid-pipeline ('processing'/'extracting') back to
    #    'pending' so the UI stops showing phantom "N processing" after an abort
    #    and the operator can cleanly re-run. Safe here: we just cancelled/killed
    #    all processing, so nothing is legitimately in flight.
    try:
        docs_reset = await asyncio.to_thread(
            get_neo4j_service().reset_orphaned_processing_documents
        )
    except Exception as e:
        logger.warning(f"abort: could not reset stranded documents: {e}")
        docs_reset = []

    logger.info(
        f"Graph generation aborted: {docs_cancelled} document task(s) cancelled, "
        f"{chain_cancelled} chain task(s) cancelled, {len(removed)} pipeline record(s) removed, "
        f"{workers_killed} docling worker(s) killed, {len(docs_reset)} document(s) reset to pending"
    )
    return {
        "documents_cancelled": docs_cancelled,
        "chain_tasks_cancelled": chain_cancelled,
        "tasks_removed": removed,
        "docling_workers_killed": workers_killed,
        "documents_reset": len(docs_reset),
    }


@app.post("/api/tasks/cleanup")
async def cleanup_tasks(
    max_age_hours: int = Query(default=24, ge=1, le=168),
    auth: AuthResult = Depends(require_manage_permission)
):
    """Remove old completed/failed tasks."""
    removed = cleanup_old_tasks(max_age_hours)
    return {"removed": removed, "remaining": len(_task_store)}


@app.get("/health", response_model=HealthResponse)
async def health_check(response: Response):
    """Health check endpoint.

    Degraded (Neo4j unreachable or schema not yet confirmed) returns HTTP 503,
    not 200-with-a-degraded-body: the compose healthchecks (`curl -f`),
    `depends_on: service_healthy` gates, and Traefik's health-aware routing
    all key off the status code, so an instance with dead vector search or
    broken dedup must not report itself healthy at the transport level.
    """
    neo4j = get_neo4j_service()
    connected = await asyncio.to_thread(neo4j.verify_connectivity)
    healthy = bool(connected) and _schema_initialized
    if not healthy:
        response.status_code = 503

    return HealthResponse(
        status="healthy" if healthy else "degraded",
        neo4j_connected=connected,
        schema_initialized=_schema_initialized,
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
        # Instance-wide (never collection-scoped): the unit quota meter.
        usage = await asyncio.to_thread(usage_meter.get_completions_this_month)
        disk_free_mb = disk_total_mb = 0
        try:
            du = shutil.disk_usage(get_settings().upload_dir)
            disk_free_mb = du.free // (1024 * 1024)
            disk_total_mb = du.total // (1024 * 1024)
        except OSError:
            pass
        return GraphStatsResponse(
            disk_free_mb=disk_free_mb,
            disk_total_mb=disk_total_mb,
            monthly_usage_used=usage["total"],
            monthly_usage_limit=max(0, get_settings().max_queries_per_month),
            monthly_usage_query=usage["query"],
            monthly_usage_processing=usage["processing"],
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
    usage = {"total": 0, "query": 0, "processing": 0}
    if connected:
        try:
            stats = await asyncio.to_thread(neo4j.get_stats)
            last_query_at = await asyncio.to_thread(neo4j._get_meta, "last_query_at")
        except Exception as e:
            logger.error(f"instance/status: failed to read graph state: {e}")
        try:
            usage = await asyncio.to_thread(usage_meter.get_completions_this_month)
        except Exception as e:
            logger.error(f"instance/status: failed to read usage meter: {e}")

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
        document_count=stats.get("document_count", 0),
        entity_count=stats.get("entity_count", 0),
        collection_count=stats.get("collection_count", 0),
        monthly_usage_used=usage["total"],
        monthly_usage_limit=max(0, get_settings().max_queries_per_month),
        monthly_usage_query=usage["query"],
        monthly_usage_processing=usage["processing"],
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
    _quota: None = Depends(enforce_processing_quota),
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
        # Kindle formats come up often enough to deserve a pointer to the
        # supported e-book route (the unpacker libraries are GPL — not shipped).
        if file_ext in {".mobi", ".azw", ".azw3"}:
            raise HTTPException(
                status_code=400,
                detail=f"File type {file_ext} is not supported. Convert the e-book "
                       f"to EPUB first (e.g. with Calibre) — .epub is supported natively."
            )
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

    # Refuse the upload if saving it would leave the disk nearly full.
    _ensure_disk_space(file_size)

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
        similar_docs = await asyncio.to_thread(processor.search, full_content[:500], top_k=5)
        
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
async def create_custom_input(
    request: CustomInputCreate,
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
):
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
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
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
        
        # Read the upload in bounded chunks so an oversized file is rejected
        # mid-stream instead of being fully buffered into memory first (same
        # pattern as /api/upload).
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

        # Refuse the new file if saving it would leave the disk nearly full.
        _ensure_disk_space(file_size)

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
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
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
                await asyncio.to_thread(processor.queue_document_for_reprocessing, doc_id)
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
            parsed_chain = _parse_chain(chain)
            task = create_task("reprocess_batch", resume_context={
                "concurrency": actual_concurrency, "chain": parsed_chain,
            })
            task.message = f"Queued {queued_count} documents for reprocessing..."
            task.progress_total = queued_count

            # Run the batch as a tracked chain task (NOT a fire-and-forget
            # FastAPI BackgroundTask) so it has a cancellable asyncio handle —
            # the graph-generation abort endpoint cancels _chain_tasks, and a
            # BackgroundTask would keep running headless (spawning docling
            # workers) with nothing able to stop it.
            _spawn_chain_task(
                _run_batch_processing_task(
                    task.task_id,
                    actual_concurrency,
                    parsed_chain,
                )
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
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
    try:
        processor = get_document_processor()
        pending = await asyncio.to_thread(processor.get_pending_documents)
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
            rel_task = create_task("relationship_analysis", resume_context={
                "collection_id": None, "scope": "full", "rebuild": True,
                "chain": remaining_chain or None,
            })
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
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
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
        pending = await asyncio.to_thread(processor.get_pending_documents)

        # Use provided concurrency or fall back to config default
        actual_concurrency = concurrency if concurrency is not None else settings.batch_processing_concurrency
        
        if len(pending) == 0:
            return {
                "message": "No pending documents to process",
                "pending_count": 0
            }
        
        # Create a task and start it in the background
        parsed_chain = _parse_chain(chain)
        task = create_task("batch_processing", resume_context={
            "concurrency": actual_concurrency, "chain": parsed_chain,
        })
        task.message = f"Queued {len(pending)} documents for processing..."
        task.progress_total = len(pending)

        # Schedule the background task (with optional chain to Step 2/3)
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
    _x402: None = Depends(enforce_x402_payment),
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
        # (embedding + Neo4j + rerank are sync/CPU-bound — keep off the event loop)
        results = await asyncio.to_thread(
            processor.hybrid_search,
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
    _x402: None = Depends(enforce_x402_payment),
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
    _x402: None = Depends(enforce_x402_payment),
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
                # Customer-managed reverse proxies (nginx variants) buffer SSE
                # unless told not to; the bundled nginx.conf already disables
                # buffering, this makes streaming proxy-agnostic.
                "X-Accel-Buffering": "no",
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

                # Query-time prompt-guard classifier (shared cortex-helper).
                guard_blocked, guard_reason = await guard_user_question(
                    processed_question, settings, get_neo4j_service()
                )
                if guard_blocked:
                    logger.warning(f"Prompt-guard blocked question: {guard_reason}")
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
                    messages.append({"role": "user", "content": processed_question})
                else:
                    # First message - do vector search and include context
                    # Sync embed+Neo4j — offload so it doesn't pin the event
                    # loop for every other in-flight stream (loop invariant).
                    results = await asyncio.to_thread(
                        processor.search, processed_question, top_k=request.top_k,
                        collection_id=_stream_effective_collection_id,
                        allowed_collection_ids=_stream_allowed_collection_ids)
                    context = "\n\n".join([r['content'][:600] for r in results[:3]])

                    if context:
                        # Fence retrieved content as untrusted data (spotlighting).
                        fenced_context = wrap_untrusted(
                            context,
                            source="knowledge base",
                            enabled=settings.prompt_security,
                        )
                        prompt = f"""Reference information:
{fenced_context}

Question: {processed_question}"""
                    else:
                        prompt = processed_question

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
                
                # Redact any system-prompt leakage from the streamed answer
                # (sliding-window filter; no-op when prompt_security is off).
                async def _fast_deltas():
                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content

                async for safe in filter_stream(
                    _fast_deltas(), system_prompt, enabled=settings.prompt_security
                ):
                    yield f"data: {json.dumps({'content': safe})}\n\n"

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
                # Customer-managed reverse proxies (nginx variants) buffer SSE
                # unless told not to; the bundled nginx.conf already disables
                # buffering, this makes streaming proxy-agnostic.
                "X-Accel-Buffering": "no",
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

            # Query-time prompt-guard classifier (shared cortex-helper).
            guard_blocked, guard_reason = await guard_user_question(
                processed_question, settings, get_neo4j_service()
            )
            if guard_blocked:
                logger.warning(f"Prompt-guard blocked question: {guard_reason}")
                yield f"data: {json.dumps({'content': get_safe_refusal_message()})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            processor = get_query_processor()

            # Speed mode agent pipeline for standard chat (opt-in via config)
            if settings.enable_agent_chat:
                async for event in processor.agent_rag_stream(
                    question=processed_question,
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
                    processed_question,
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
                # Sync embed+Neo4j — offload so it doesn't pin the event loop.
                results = await asyncio.to_thread(
                    processor.search, processed_question, top_k=request.top_k * 2,
                    collection_id=_stream_effective_collection_id,
                    allowed_collection_ids=_stream_allowed_collection_ids)

            # Re-rank if enabled
            if request.use_reranking and settings.enable_reranking and results:
                if _emit_status:
                    yield f"data: {json.dumps({'status': {'stage': 'reranking', 'message': 'Ranking the most relevant sources'}})}\n\n"
                results = await processor.rerank_results_async(
                    processed_question, results, request.top_k
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
                question=processed_question,
                has_history=has_history,
                secure=settings.prompt_security,
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

            # Redact any system-prompt leakage from the streamed answer
            # (sliding-window filter; no-op when prompt_security is off).
            async def _writer_deltas():
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            async for safe in filter_stream(
                _writer_deltas(), system_prompt, enabled=settings.prompt_security
            ):
                yield f"data: {json.dumps({'content': safe})}\n\n"

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
            "X-Accel-Buffering": "no",
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
        # The graph topology changed — cached duplicate-scan results are stale.
        invalidate_dedup_cache()
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


# ---------------------------------------------------------------------------
# Duplicate-entity scan: single-flight job + result cache.
#
# The scan is CPU-heavy and can outlast the edge proxy read timeout on large
# graphs. One scan runs at a time — identical requests join it instead of
# stacking additional scans — and slow scans surface as 202 + progress for
# the client to poll while the scan continues in the background.
# ---------------------------------------------------------------------------

class _DedupScanJob:
    def __init__(self, key: tuple):
        self.key = key
        self.cancel_event = threading.Event()
        self.progress = 0.0
        self.task: Optional[asyncio.Task] = None

    def update_progress(self, done: int, total: int):
        self.progress = done / total if total else 1.0


_dedup_scan_job: Optional[_DedupScanJob] = None
_dedup_scan_cache: Dict[tuple, dict] = {}


def invalidate_dedup_cache():
    _dedup_scan_cache.clear()


async def _run_dedup_scan(job: _DedupScanJob, threshold: float, limit: int, collection_filter):
    global _dedup_scan_job
    try:
        neo4j = get_neo4j_service()
        groups = await asyncio.to_thread(
            neo4j.suggest_duplicate_entities, threshold, limit, collection_filter,
            job.cancel_event, job.update_progress,
        )
        _dedup_scan_cache[job.key] = {"groups": groups, "completed_at": time.time()}
        return groups
    finally:
        if _dedup_scan_job is job:
            _dedup_scan_job = None


@app.get("/api/entities/duplicates")
async def suggest_duplicates(
    threshold: float = Query(default=0.75, ge=0.5, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    refresh: bool = Query(default=False),
    auth: AuthResult = Depends(require_read_permission)
):
    """Suggest duplicate entity groups for user review.

    Single-flight: one scan runs at a time and identical requests join it.
    If the scan outlasts DEDUP_SCAN_WAIT_SECONDS the response is
    202 {"status": "running", "progress": ...} — poll the same URL (without
    refresh=true) until it returns status "complete". Completed results are
    cached for DEDUP_SCAN_CACHE_TTL_SECONDS; entity merges invalidate the
    cache, refresh=true forces a rescan.

    For restricted API keys, results are scoped to accessible collections.
    """
    global _dedup_scan_job
    settings = get_settings()
    collection_filter = auth.get_collection_filter()
    key = (
        round(threshold, 4),
        limit,
        tuple(sorted(collection_filter)) if collection_filter is not None else None,
    )

    try:
        if refresh:
            _dedup_scan_cache.pop(key, None)
        else:
            cached = _dedup_scan_cache.get(key)
            if cached and time.time() - cached["completed_at"] < settings.dedup_scan_cache_ttl_seconds:
                return {
                    "status": "complete",
                    "groups": cached["groups"],
                    "total_groups": len(cached["groups"]),
                    "cached": True,
                }

        job = _dedup_scan_job
        if job is not None and job.key != key:
            # A scan with different parameters is running. Never cancel it —
            # two clients polling with different thresholds would livelock
            # cancelling each other's scans. Tell this client to poll again;
            # its scan starts once the running one finishes.
            return JSONResponse(
                status_code=202,
                content={"status": "running", "progress": round(job.progress, 3)},
            )
        if job is None:
            job = _DedupScanJob(key)
            job.task = asyncio.create_task(_run_dedup_scan(job, threshold, limit, collection_filter))
            # Retrieve the exception if every waiter timed out before the
            # scan failed (avoids "Task exception was never retrieved").
            job.task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )
            _dedup_scan_job = job

        try:
            groups = await asyncio.wait_for(
                asyncio.shield(job.task), timeout=settings.dedup_scan_wait_seconds
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=202,
                content={"status": "running", "progress": round(job.progress, 3)},
            )
        return {"status": "complete", "groups": groups, "total_groups": len(groups)}
    except HTTPException:
        raise
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

        # Security: also verify the caller can access the document's source
        # collection (see move_documents_to_collection). No-op for
        # admin/unrestricted keys.
        allowed = auth.get_collection_filter()
        if allowed is not None:
            allowed_set = set(allowed)
            sources = await asyncio.to_thread(
                neo4j.get_documents_file_paths, [document_id]
            )
            for src in sources:
                if src.get("collection_id") not in allowed_set:
                    raise HTTPException(
                        status_code=403,
                        detail="API key does not have permission to add a document "
                        f"from source collection: {src.get('collection_id')}",
                    )

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

        neo4j = get_neo4j_service()

        # Security: also verify the caller can access each document's *source*
        # collection, so a restricted key cannot pull documents out of a
        # collection it isn't scoped to (and then read them once they land in an
        # accessible collection). No-op for admin/unrestricted keys, whose
        # get_collection_filter() is None — legitimate moves are unaffected.
        allowed = auth.get_collection_filter()
        if allowed is not None:
            allowed_set = set(allowed)
            sources = await asyncio.to_thread(
                neo4j.get_documents_file_paths, request.document_ids
            )
            for src in sources:
                if src.get("collection_id") not in allowed_set:
                    raise HTTPException(
                        status_code=403,
                        detail="API key does not have permission to move documents "
                        f"from source collection: {src.get('collection_id')}",
                    )

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
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
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
            com_task = create_task("community_detection", resume_context={
                "min_size": settings.min_community_size,
                "collection_id": collection_id,
            })
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
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
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

        parsed_chain = _parse_chain(chain)
        task = create_task("relationship_analysis", resume_context={
            "collection_id": collection_id, "scope": scope, "rebuild": rebuild,
            "chain": parsed_chain,
        })
        task.message = "Starting relationship analysis..." if not rebuild else "Starting full rebuild..."
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
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
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
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
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
        task = create_task("community_detection", resume_context={
            "min_size": min_size, "collection_id": collection_id,
        })
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
async def summarize_communities(
    request: CommunitySummaryRequest,
    auth: AuthResult = Depends(require_manage_permission),
    _quota: None = Depends(enforce_processing_quota),
):
    """
    Generate or regenerate summaries for communities.
    
    Uses LLM to create descriptive names and summaries for entity communities.
    """
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
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
    _x402: None = Depends(enforce_x402_payment),
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
            "X-Accel-Buffering": "no",
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

    # Effective value = env default overlaid with the runtime admin override.
    # The ingestion scan is experimental: while its master flag is off the
    # effective value is simply false (no SystemMeta read — feature is absent).
    if settings.enable_ingestion_injection_scan:
        ingestion_injection_scan = await asyncio.to_thread(
            get_neo4j_service().get_runtime_setting,
            "ingestion_injection_scan",
            settings.ingestion_injection_scan,
        )
    else:
        ingestion_injection_scan = False
    prompt_guard = await asyncio.to_thread(
        get_neo4j_service().get_runtime_setting,
        "prompt_guard",
        settings.prompt_guard,
    )

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
        relationship_discovery_mode=settings.relationship_discovery_mode,

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
        enable_ingestion_injection_scan=settings.enable_ingestion_injection_scan,
        ingestion_injection_scan=ingestion_injection_scan,
        prompt_guard=prompt_guard,

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


@app.patch("/api/admin/config", response_model=SystemConfigResponse)
async def update_runtime_settings(
    update: RuntimeSettingsUpdate,
    auth: AuthResult = Depends(require_admin),
):
    """Update admin-editable runtime settings.

    Persisted as overrides over the env defaults (via SystemMeta) and effective
    immediately for new work — e.g. toggling the ingestion injection scan
    applies to subsequently-ingested documents without a restart. Returns the
    full, updated system configuration.
    """
    neo4j = get_neo4j_service()
    if update.ingestion_injection_scan is not None:
        if not get_settings().enable_ingestion_injection_scan:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The ingestion injection scan is an experimental feature and is "
                    "disabled on this instance. Set ENABLE_INGESTION_INJECTION_SCAN=true "
                    "to activate it."
                ),
            )
        await asyncio.to_thread(
            neo4j.set_runtime_setting,
            "ingestion_injection_scan",
            update.ingestion_injection_scan,
        )
        logger.info(
            "Admin set runtime setting ingestion_injection_scan=%s",
            update.ingestion_injection_scan,
        )
    if update.prompt_guard is not None:
        await asyncio.to_thread(
            neo4j.set_runtime_setting,
            "prompt_guard",
            update.prompt_guard,
        )
        logger.info(
            "Admin set runtime setting prompt_guard=%s",
            update.prompt_guard,
        )
    return await get_system_config(auth=auth)


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
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
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
    _quota: None = Depends(enforce_processing_quota),
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
        # SSRF guard: block loopback/link-local/metadata (and private ranges
        # unless WEB_IMPORT_ALLOW_PRIVATE) before handing the URL to crawl4ai.
        from app.services import ssrf_guard
        try:
            await asyncio.to_thread(
                ssrf_guard.validate_url,
                u,
                allow_private=settings.web_import_allow_private,
            )
        except ssrf_guard.SSRFError as e:
            raise HTTPException(status_code=400, detail=f"URL not allowed: {raw} ({e})")
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

    # SSRF guard (see /api/web-import).
    from app.services import ssrf_guard
    try:
        await asyncio.to_thread(
            ssrf_guard.validate_url,
            url,
            allow_private=get_settings().web_import_allow_private,
        )
    except ssrf_guard.SSRFError as e:
        raise HTTPException(status_code=400, detail=f"URL not allowed ({e})")

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
    node = await asyncio.to_thread(neo4j.create_git_connection, props)
    return _git_conn_response(node)


@app.get("/api/integrations/git/connections", response_model=List[GitConnectionResponse])
async def list_git_connections(auth: AuthResult = Depends(require_admin)):
    """List all git connections (PATs masked)."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    connections = await asyncio.to_thread(neo4j.list_git_connections)
    return [_git_conn_response(n) for n in connections]


@app.get("/api/integrations/git/connections/{connection_id}", response_model=GitConnectionResponse)
async def get_git_connection(connection_id: str, auth: AuthResult = Depends(require_admin)):
    """Get a single git connection (PAT masked)."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    node = await asyncio.to_thread(neo4j.get_git_connection, connection_id)
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
    existing = await asyncio.to_thread(neo4j.get_git_connection, connection_id)
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

    node = await asyncio.to_thread(neo4j.update_git_connection, connection_id, props)
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
    existing = await asyncio.to_thread(neo4j.get_git_connection, connection_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")

    purged = 0
    if purge_documents:
        # delete_document already removes relationships with this document's
        # provenance (its step 0 runs the same query as
        # delete_relationships_by_source_document), so bulk delete suffices.
        docs = await asyncio.to_thread(neo4j.list_documents_for_git_connection, connection_id)
        if docs:
            result = await asyncio.to_thread(neo4j.delete_documents, [d["id"] for d in docs])
            purged = result["deleted_count"]

    await asyncio.to_thread(neo4j.delete_git_connection, connection_id)
    return {"message": "Connection deleted", "connection_id": connection_id, "documents_purged": purged}


async def _run_git_sync_task(connection_id: str, task_id: str):
    """Background runner: sync a git connection, reporting progress to the task store."""
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
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
            # Scheduled syncs feed the processing pipeline — don't start new
            # ones once the monthly LLM-completion budget is spent.
            if await _quota_exceeded():
                logger.info("Git sync scheduler: monthly usage limit reached; skipping this round")
                continue
            now = datetime.now(timezone.utc)
            connections = await asyncio.to_thread(neo4j.list_git_connections)
            for conn in connections:
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
                # Keep a strong reference — a bare create_task can be GC'd mid-flight.
                _spawn_chain_task(_run_git_sync_task(cid, task.task_id))
                logger.info(f"Scheduled git sync started for connection {cid}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            rate_limited_warning(
                logger, "git-scheduler", f"git sync scheduler tick failed: {e}"
            )


@app.post("/api/integrations/git/connections/{connection_id}/sync", response_model=GitSyncTriggerResponse)
async def sync_git_connection(
    connection_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthResult = Depends(require_admin),
    _quota: None = Depends(enforce_processing_quota),
):
    """Trigger an incremental sync for a connection. Returns a task id to poll."""
    _require_git_enabled()
    neo4j = get_neo4j_service()
    conn = await asyncio.to_thread(neo4j.get_git_connection, connection_id)
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
    if not await asyncio.to_thread(neo4j.get_git_connection, connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    documents = await asyncio.to_thread(neo4j.list_orphaned_git_documents, connection_id)
    return {"documents": documents}


# =============================================================================
# Admin API Key Management Endpoints
# =============================================================================

# =============================================================================
# x402 Payments — admin configuration, verification, earnings
# =============================================================================

def _require_x402_enabled() -> None:
    """The whole admin surface is hidden while X402_ENABLED=false; API writes
    are rejected too (same pattern as the runtime-settings gate)."""
    if not get_settings().x402_enabled:
        raise HTTPException(
            status_code=400,
            detail="x402 payments are disabled on this instance (X402_ENABLED=false)"
        )


@app.get("/api/admin/x402/config", response_model=X402ConfigResponse)
async def get_x402_config(auth: AuthResult = Depends(require_admin)):
    """Current x402 configuration + verification state (secrets masked).

    Available even when X402_ENABLED=false so the UI can decide what to render
    (the response carries the flag).
    """
    try:
        cfg = await asyncio.to_thread(x402_service.load_x402_config, True)
        return x402_service.build_config_response(cfg)
    except Exception as e:
        logger.error(f"Error reading x402 config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/admin/x402/config", response_model=X402ConfigResponse)
async def update_x402_config(
    request: X402ConfigUpdate,
    auth: AuthResult = Depends(require_admin)
):
    """Save the x402 configuration (runtime, stored in Neo4j — survives
    redeploys, never rides library exports). Changing any payment-relevant
    field invalidates the verified state until POST /verify passes again.
    """
    _require_x402_enabled()
    try:
        result = await asyncio.to_thread(x402_service.save_x402_config, request)
        audit("x402.config_updated", actor=auth.key_id, outcome="ok")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving x402 config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/x402/verify", response_model=X402VerifyResponse)
async def verify_x402_config(auth: AuthResult = Depends(require_admin)):
    """Run the verification suite against the saved configuration:
    address formats, facilitator reachability (GET /supported), and
    scheme+network support. All checks passing stamps the config verified —
    the precondition for creating priced keys and serving paid requests.
    """
    _require_x402_enabled()
    cfg = await asyncio.to_thread(x402_service.load_x402_config, True)
    if not x402_service.config_complete(cfg):
        raise HTTPException(
            status_code=400,
            detail="Save a complete x402 configuration before verifying"
        )
    try:
        result = await x402_service.run_verification(cfg)
        audit(
            "x402.config_verified", actor=auth.key_id,
            outcome="ok" if result.valid else "failed",
        )
        return result
    except Exception as e:
        logger.error(f"Error verifying x402 config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/x402/earnings", response_model=X402EarningsResponse)
async def get_x402_earnings(auth: AuthResult = Depends(require_admin)):
    """Instance-wide settled-payment totals, overall and per key."""
    try:
        return await asyncio.to_thread(x402_service.get_earnings)
    except Exception as e:
        logger.error(f"Error reading x402 earnings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


def _validate_monetized_key_price(price_raw: str, permissions: List[APIKeyPermission]) -> str:
    """Guard rails for x402-priced ("monetized public") keys, enforced at the
    API level — a priced key must be read-only, and pricing is only available
    once the x402 config exists and has passed verification.

    Returns the normalized price string.
    """
    settings = get_settings()
    if not settings.x402_enabled:
        raise HTTPException(
            status_code=400,
            detail="x402 payments are disabled on this instance (X402_ENABLED=false)"
        )
    cfg = x402_service.load_x402_config()
    if not x402_service.is_config_verified(cfg):
        raise HTTPException(
            status_code=400,
            detail=(
                "The x402 configuration must be saved and verified before "
                "priced keys can be created (Settings → x402 Payments)"
            )
        )
    if APIKeyPermission.MANAGE in permissions:
        raise HTTPException(
            status_code=422,
            detail=(
                "Monetized keys must be read-only: 'manage' permission cannot "
                "be combined with a price_per_query"
            )
        )
    try:
        return x402_service.validate_price(price_raw, int(cfg.get("asset_decimals") or 0))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/admin/api-keys", response_model=CreateAPIKeyResponse)
async def create_api_key(request: CreateAPIKeyRequest, auth: AuthResult = Depends(require_admin)):
    """
    Create a new API key.

    Admin-only endpoint. The actual API key is returned only once in this response.
    Make sure to save it securely as it cannot be retrieved again.

    Collection scope options:
    - "all": Key can access all collections (default)
    - "restricted": Key can only access collections specified in allowed_collections

    Setting price_per_query creates a monetized public key (x402): read-only
    by construction and restricted to the retrieval endpoints.
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

        price_per_query = None
        if request.price_per_query:
            price_per_query = _validate_monetized_key_price(
                request.price_per_query, request.permissions
            )

        api_key_service = get_api_key_service()
        result = api_key_service.create_api_key(
            name=request.name,
            permissions=request.permissions,
            created_by="admin",
            collection_scope=request.collection_scope,
            allowed_collections=request.allowed_collections,
            price_per_query=price_per_query
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

        # Monetized-key invariant, checked against the EFFECTIVE post-update
        # state: a priced key can never hold MANAGE, in either direction
        # (adding a price to a manage key, or adding manage to a priced key).
        existing = api_key_service.get_api_key(key_id)
        if not existing:
            raise HTTPException(status_code=404, detail="API key not found")

        if request.price_per_query is None:
            effective_price = existing.price_per_query
        else:
            effective_price = request.price_per_query or None  # "" clears
        effective_permissions = (
            request.permissions if request.permissions is not None
            else existing.permissions
        )
        if effective_price:
            if request.price_per_query:
                # Setting/changing a price revalidates it against the current
                # verified x402 config (and the enabled flag).
                request.price_per_query = _validate_monetized_key_price(
                    request.price_per_query, effective_permissions
                )
            elif APIKeyPermission.MANAGE in effective_permissions:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "This key is monetized (x402-priced) and must stay "
                        "read-only: clear the price before granting 'manage'"
                    )
                )

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
            invalidate_api_key_cache()
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

    _guard_no_transfer_in_progress()

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


# =============================================================================
# Chunked import upload
#
# Reverse proxies (Traefik v3 defaults respondingTimeouts.readTimeout=60s)
# kill single-request uploads of large export ZIPs mid-body. The frontend
# uploads the archive in small sequential chunks instead — each request
# completes in seconds — and the backend appends them to a temp file, then
# hands the assembled ZIP to the same import task as /api/admin/import.
# =============================================================================

_import_upload_sessions: Dict[str, dict] = {}
_IMPORT_UPLOAD_TTL_SECONDS = 2 * 60 * 60


class ImportUploadStartRequest(BaseModel):
    total_size: int = Field(gt=0, description="Total ZIP size in bytes")
    filename: Optional[str] = None


def _purge_stale_import_uploads() -> None:
    """Drop upload sessions (and their temp files) abandoned past the TTL."""
    now = time.time()
    for uid in list(_import_upload_sessions):
        sess = _import_upload_sessions.get(uid)
        if sess and now - sess["created_at"] > _IMPORT_UPLOAD_TTL_SECONDS:
            _import_upload_sessions.pop(uid, None)
            try:
                os.unlink(sess["path"])
            except OSError:
                pass


def _guard_no_transfer_in_progress() -> None:
    for t in _task_store.values():
        if t.task_type in ("library_export", "library_import") and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            raise HTTPException(
                status_code=409,
                detail="An export or import is already in progress. Please wait for it to complete.",
            )


@app.post("/api/admin/import/upload/start")
async def start_import_upload(request: ImportUploadStartRequest, auth: AuthResult = Depends(require_admin)):
    """Open a chunked upload session for a library export ZIP."""
    import tempfile as _tempfile

    _purge_stale_import_uploads()
    _guard_no_transfer_in_progress()
    # The client declares the ZIP size up front — refuse the whole session
    # early if assembling it would leave the disk nearly full.
    _ensure_disk_space(request.total_size)

    tmp_fd, tmp_path = _tempfile.mkstemp(suffix=".zip", prefix="cortex_import_")
    os.close(tmp_fd)
    upload_id = uuid.uuid4().hex
    _import_upload_sessions[upload_id] = {
        "path": tmp_path,
        "received": 0,
        "total_size": request.total_size,
        "created_at": time.time(),
    }
    return {"upload_id": upload_id, "received": 0}


@app.put("/api/admin/import/upload/{upload_id}/chunk")
async def upload_import_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(..., ge=0),
    auth: AuthResult = Depends(require_admin),
):
    """Append one chunk (raw bytes) at the given offset.

    Offsets must be contiguous; on mismatch (e.g. a retried chunk that already
    landed) responds 409 with the server's byte count so the client can resync.
    """
    sess = _import_upload_sessions.get(upload_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Upload session not found or expired")

    if sess["received"] != offset:
        raise HTTPException(status_code=409, detail={"received": sess["received"], "message": "Offset mismatch"})

    received = 0
    with open(sess["path"], "ab") as f:
        async for chunk in request.stream():
            f.write(chunk)
            received += len(chunk)
    sess["received"] += received

    if sess["received"] > sess["total_size"]:
        _import_upload_sessions.pop(upload_id, None)
        try:
            os.unlink(sess["path"])
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Upload exceeds declared total_size")

    return {"received": sess["received"]}


@app.post("/api/admin/import/upload/{upload_id}/finish")
async def finish_import_upload(
    upload_id: str,
    background_tasks: BackgroundTasks,
    mode: str = Query("clean", pattern="^(clean|replace)$"),
    auth: AuthResult = Depends(require_admin),
):
    """Validate the assembled ZIP is complete and start the import task."""
    sess = _import_upload_sessions.get(upload_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Upload session not found or expired")

    if sess["received"] != sess["total_size"]:
        raise HTTPException(
            status_code=400,
            detail=f"Upload incomplete: received {sess['received']} of {sess['total_size']} bytes",
        )

    _guard_no_transfer_in_progress()
    _import_upload_sessions.pop(upload_id, None)

    from app.services.library_transfer_service import get_library_transfer_service
    transfer = get_library_transfer_service()

    task = create_task("library_import")
    background_tasks.add_task(
        transfer.import_library,
        task.task_id,
        sess["path"],
        mode,
        update_task_progress,
        complete_task,
        fail_task,
    )
    return {"task_id": task.task_id, "status": "pending", "message": f"Import started (mode: {mode})"}


@app.delete("/api/admin/import/upload/{upload_id}")
async def abort_import_upload(upload_id: str, auth: AuthResult = Depends(require_admin)):
    """Abort a chunked upload and discard the partial file."""
    sess = _import_upload_sessions.pop(upload_id, None)
    if sess:
        try:
            os.unlink(sess["path"])
        except OSError:
            pass
    return {"status": "aborted"}


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

    _guard_no_transfer_in_progress()
    # Size unknown until streamed — at least require the free-space floor.
    _ensure_disk_space()

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
