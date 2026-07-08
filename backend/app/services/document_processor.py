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
import dataclasses
import functools
import gc
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Tuple

# NOTE: docling is intentionally NOT imported at module scope. Importing it
# (docling.document_converter et al.) pulls torch + docling-ibm-models (~244 MB)
# into every backend process at startup. The live ingestion path converts in a
# dedicated subprocess (docling_worker.py, which builds its own converter), so
# the in-process converter is built lazily in _build_docling_converter().
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
from app.services import usage_meter
from app.services.graph_extractor import get_graph_extractor
from app.services.llm_config import (
    get_llm_config,
    build_chat_params,
    make_openai_client,
    make_async_openai_client,
    stream_usage_kwargs,
)
from app.services.neo4j_service import get_neo4j_service
from app.services.prompt_security import (
    get_anti_injection_instruction,
    get_safe_refusal_message,
    validate_and_process_input,
    wrap_untrusted,
)
from app.services.injection_scanner import scan_document
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


# Conservative chars-per-token ratio for BPE-family embedding models.
# English averages ~4.0; markdown/code with heavy punctuation lands closer to 3.0;
# CJK content is denser still. 2.8 is a deliberately conservative single value:
# accepts ~30% over-truncation on plain English in exchange for never overshooting
# the server cap. The trade is worth it — losing a few % of a chunk's tail is
# negligible vs losing the entire embedding to an HTTP 400.
_EMBED_CHARS_PER_TOKEN = 2.8

# Lazily-built tiktoken encoding for token-accurate embed caps. False = tried
# and unavailable (fall back to the char heuristic).
_tiktoken_encoding = None


def _token_len(text: str) -> Optional[int]:
    """Count embed-input tokens (cl100k_base), or None when tiktoken is missing.

    The char heuristic (`_EMBED_CHARS_PER_TOKEN` = 2.8) undercounts dense
    punctuation/number text — a book's index page measured ~2.4 chars/token,
    putting a 22.4k-char chunk at ~9.5k real tokens, past the API's 8192 cap.
    """
    global _tiktoken_encoding
    if _tiktoken_encoding is None:
        try:
            import tiktoken

            _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 — optional accuracy, never required
            _tiktoken_encoding = False
    if _tiktoken_encoding is False:
        return None
    return len(_tiktoken_encoding.encode(text))


def _drop_empty_chunks(docs):
    """Drop chunks whose content is empty/whitespace-only before embedding.

    Embed APIs reject empty strings with HTTP 400 — and some gateways (Venice)
    wrap that upstream error in an HTTP 200 envelope with `data: null`, which
    the OpenAI SDK parses as a success; downstream len()/iteration on the null
    data then raises and fails the whole document. Empty chunks carry no
    retrieval value, so remove them before the API ever sees one.
    """
    kept = [d for d in docs if (getattr(d, "content", None) or "").strip()]
    dropped = len(docs) - len(kept)
    if dropped:
        logger.warning(f"Dropped {dropped} empty chunk(s) before embedding")
    return kept


def _truncate_for_embedding(docs, max_tokens: int):
    """Truncate Haystack Document content client-side to stay under the embed API's token cap.

    The OpenAI-compatible embeddings endpoints reject inputs over their model-specific
    limit with HTTP 400 ("Input text exceeds the maximum token limit of N tokens"),
    losing the whole chunk's embedding. This guard truncates by an approximate char
    budget so the call still succeeds with a (slightly) shorter input.

    With `_enforce_embed_token_cap` running ahead of this guard, truncation should fire
    essentially never — kept as a belt-and-suspenders safety net.
    """
    if max_tokens <= 0:
        return docs
    char_budget = int(max_tokens * _EMBED_CHARS_PER_TOKEN)
    truncated = 0
    for d in docs:
        content = getattr(d, "content", None)
        if not content:
            continue
        tokens = _token_len(content)
        if tokens is not None:
            if tokens > max_tokens:
                # Cut proportionally to this text's own chars-per-token ratio,
                # with 10% headroom for boundary effects.
                d.content = content[: max(1, int(len(content) / tokens * max_tokens * 0.9))]
                truncated += 1
        elif len(content) > char_budget:
            d.content = content[:char_budget]
            truncated += 1
    if truncated:
        logger.warning(
            f"Truncated {truncated} input(s) to ~{max_tokens} tokens "
            f"({char_budget} chars) before embedding"
        )
    return docs


def _split_to_budget(text: str, char_budget: int) -> List[str]:
    """Recursively split text into pieces <= char_budget chars.

    Walks a separator hierarchy (paragraph → line → sentence → space → hard char slice)
    and greedily merges adjacent fragments back together, recursing into any single
    fragment that still exceeds the budget. Final hard-slice fallback handles content
    with no whitespace at all (e.g. a 50K-char unbroken token blob).
    """
    if len(text) <= char_budget:
        return [text]
    for sep in ("\n\n", "\n", ". ", " ", ""):
        if sep == "":
            # Last resort — hard char slice. Guaranteed to terminate.
            return [text[i : i + char_budget] for i in range(0, len(text), char_budget)]
        parts = text.split(sep)
        if len(parts) == 1:
            continue  # separator not present in text
        pieces: List[str] = []
        current = ""
        for p in parts:
            candidate = (current + sep + p) if current else p
            if len(candidate) <= char_budget:
                current = candidate
            else:
                if current:
                    pieces.append(current)
                if len(p) > char_budget:
                    # Single fragment still too big — recurse with finer separators
                    pieces.extend(_split_to_budget(p, char_budget))
                    current = ""
                else:
                    current = p
        if current:
            pieces.append(current)
        return pieces
    return [text]  # unreachable, satisfies type checker


def _split_to_token_budget(content: str, max_tokens: int, char_budget: int) -> List[str]:
    """Split to a char budget, then re-verify each piece's REAL token count.

    A piece that still exceeds the token cap (denser text than the ratio the
    budget was derived from) is recursively re-split with a budget derived
    from its own chars-per-token ratio. Pieces of <=64 chars are accepted as
    is — 64 chars cannot exceed any realistic token cap.
    """
    pieces = _split_to_budget(content, char_budget)
    out: List[str] = []
    for p in pieces:
        tokens = _token_len(p)
        if tokens is not None and tokens > max_tokens and len(p) > 64:
            tighter = max(64, int(len(p) / tokens * max_tokens * 0.9))
            out.extend(_split_to_token_budget(p, max_tokens, tighter))
        else:
            out.append(p)
    return out


def _enforce_embed_token_cap(docs, max_tokens: int):
    """Sub-split any chunk that exceeds the embed token budget into safe pieces.

    Runs before `_truncate_for_embedding` so the embed API call always sees safe inputs
    AND no content is lost (truncation drops tail bytes). New sub-chunks preserve the
    parent's `meta` dict so downstream chunk-storage logic doesn't need to know they
    were split. `chunk_index` is assigned later by the storage loop's `enumerate`, so
    the extra pieces just slot into the index sequence naturally.

    Use case: a Docling-extracted markdown table or a custom-input paste with no
    sentence boundaries can produce a single chunk that exceeds the embed cap.
    Without this pass, that chunk either errors (LiteLLM-style ContextWindowExceeded)
    or gets its tail silently truncated. With it, the chunk gets cleanly subdivided
    along the best available boundary.
    """
    if max_tokens <= 0:
        return docs
    char_budget = int(max_tokens * _EMBED_CHARS_PER_TOKEN)

    def _is_over(content: str) -> Optional[int]:
        """Return the real token count when over the cap, else None.

        Token-accurate when tiktoken is available (the char heuristic
        undercounts dense punctuation/number text like book indexes — measured
        ~2.4 chars/token vs the assumed 2.8, letting ~9.5k-token chunks
        through a "8192-token" cap and 400-ing the embed call); falls back to
        the char budget otherwise (returning -1 as "over, count unknown").
        """
        tokens = _token_len(content)
        if tokens is not None:
            return tokens if tokens > max_tokens else None
        return -1 if len(content) > char_budget else None

    over_budget = [d for d in docs if _is_over(getattr(d, "content", "") or "")]
    if not over_budget:
        return docs

    from haystack import Document as HaystackDocument

    result: list = []
    extra_pieces = 0
    for d in docs:
        content = getattr(d, "content", None) or ""
        tokens = _is_over(content)
        if tokens is None:
            result.append(d)
            continue
        meta = dict(getattr(d, "meta", {}) or {})
        # Derive the char budget from this text's own chars-per-token ratio
        # (10% headroom); pieces are re-verified against the real token count.
        budget = char_budget
        if tokens > 0:
            budget = min(budget, max(64, int(len(content) / tokens * max_tokens * 0.9)))
        pieces = _split_to_token_budget(content, max_tokens, budget)
        for piece in pieces:
            result.append(HaystackDocument(content=piece, meta=dict(meta)))
        extra_pieces += len(pieces) - 1
    logger.info(
        f"Sub-split {len(over_budget)} oversize chunk(s) into {len(over_budget) + extra_pieces} "
        f"pieces to fit embed token cap (~{max_tokens} tokens, {char_budget} chars)"
    )
    return result


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


async def _convert_via_service(file_path: str, use_vision: bool) -> dict:
    """Convert via the shared docling service (cortex-helper).

    Transport (connection reuse, retries with backoff, circuit breaker,
    auth/tenant headers) lives in helper_client. Raising lets the caller
    decide between the local-subprocess fallback and strict failure.
    """
    from app.services.helper_client import convert_document

    logger.info(f"Converting {Path(file_path).name} via docling service")
    result = await convert_document(file_path, use_vision)
    logger.info(
        f"Service conversion complete for {result.get('filename', '?')} "
        f"(md_len={len(result.get('markdown') or '')}, images={len(result.get('images', []))})"
    )
    return result


async def _convert_document_subprocess(
    file_path: str,
    use_vision: bool,
    on_progress: Optional[Callable[[str, float], Awaitable[None]]] = None,
) -> dict:
    """Convert a document to markdown.

    Uses the shared docling service when DOCLING_SERVICE_URL is set. On
    service failure (after the helper client's retries): with
    HELPER_STRICT_REMOTE the document fails cleanly; otherwise it falls back
    to the local subprocess. Without a service URL, Docling runs in a
    subprocess to avoid GIL contention.

    on_progress(message, fraction) is fired when the conversion actually
    starts (after the conversion-slot semaphore is acquired — callers should
    show "waiting for a slot" until then) and on every page-chunk line the
    worker logs, giving large PDFs a real progress fraction instead of a
    frozen "Converting document...". Callback errors are swallowed.

    Returns dict with keys: markdown, filename, images, error.
    """
    import json as _json

    from app.metrics import CONVERSION_SECONDS

    settings = get_settings()
    if settings.docling_service_url:
        try:
            _t0 = time.monotonic()
            result = await _convert_via_service(file_path, use_vision)
            CONVERSION_SECONDS.labels(path="remote").observe(time.monotonic() - _t0)
            return result
        except Exception as e:
            if getattr(settings, "helper_strict_remote", False):
                raise RuntimeError(
                    f"Docling service conversion failed and HELPER_STRICT_REMOTE "
                    f"is enabled (no local fallback): {e}"
                ) from e
            logger.warning(
                f"Docling service unavailable ({e}); falling back to local subprocess"
            )

    # Slim images ship without docling — fail with an actionable message
    # instead of an opaque subprocess crash.
    import importlib.util
    if importlib.util.find_spec("docling") is None:
        raise RuntimeError(
            "Local docling is not available in this image (slim build without "
            "the ML stack). Set DOCLING_SERVICE_URL to the shared cortex-helper "
            "(recommended with HELPER_STRICT_REMOTE=true), or deploy the full "
            "image (INSTALL_LOCAL_ML=true)."
        )

    sem = _get_conversion_semaphore()
    async with sem:
        _t0 = time.monotonic()
        logger.info(f"Starting subprocess conversion for {Path(file_path).name}")
        if on_progress:
            try:
                await on_progress("Converting document...", 0.0)
            except Exception:  # noqa: BLE001 — progress must never fail conversion
                pass
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.services.docling_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        request_data = _json.dumps({"file_path": file_path, "use_vision": use_vision}) + "\n"

        # Stream stderr live instead of buffering until exit: the worker logs
        # "Converting pages X-Y of Z" per page chunk, which is the only real
        # progress signal during a multi-minute conversion. Chunked reads with
        # manual \n/\r splitting — tqdm redraws use bare \r and would overflow
        # readline()'s line limit.
        stderr_tail: list[str] = []
        _page_rx = re.compile(r"Converting pages \d+-(\d+) of (\d+)")

        async def _handle_stderr_line(text: str) -> None:
            text = text.strip()
            if not text:
                return
            stderr_tail.append(text)
            if len(stderr_tail) > 200:
                stderr_tail.pop(0)
            # tqdm redraw fragments ("... 37%|███| ... it/s]") are debug noise
            if "it/s" in text or "%|" in text:
                logger.debug(f"[docling-worker] {text}")
            else:
                logger.info(f"[docling-worker] {text}")
            if on_progress:
                m = _page_rx.search(text)
                if m:
                    page_end, page_total = int(m.group(1)), int(m.group(2))
                    try:
                        await on_progress(
                            f"Converting document: page {page_end}/{page_total}...",
                            page_end / max(1, page_total),
                        )
                    except Exception:  # noqa: BLE001
                        pass

        async def _pump_stderr() -> None:
            assert proc.stderr is not None
            buf = b""
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    if buf:
                        await _handle_stderr_line(buf.decode(errors="replace"))
                    return
                buf += chunk
                while True:
                    nl = buf.find(b"\n")
                    cr = buf.find(b"\r")
                    idx = min(i for i in (nl, cr) if i >= 0) if (nl >= 0 or cr >= 0) else -1
                    if idx < 0:
                        break
                    line, buf = buf[:idx], buf[idx + 1 :]
                    await _handle_stderr_line(line.decode(errors="replace"))
                if len(buf) > 65536:  # runaway unterminated line guard
                    buf = buf[-4096:]

        async def _run_worker() -> bytes:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(request_data.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            pump = asyncio.create_task(_pump_stderr())
            try:
                out = await proc.stdout.read()
                await proc.wait()
            finally:
                # stderr hits EOF when the process exits; bound the wait so a
                # pathological pipe state can't hang us past the timeout.
                try:
                    await asyncio.wait_for(pump, timeout=10)
                except Exception:  # noqa: BLE001
                    pump.cancel()
            return out

        try:
            stdout = await asyncio.wait_for(
                _run_worker(), timeout=settings.docling_conversion_timeout
            )
        except asyncio.TimeoutError:
            # Kill the hung worker so it can't linger holding the conversion
            # semaphore (and memory). Raising here lands the document in FAILED
            # with an actionable message instead of stuck in 'processing'.
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            raise RuntimeError(
                f"Docling conversion timed out after "
                f"{settings.docling_conversion_timeout}s for "
                f"{Path(file_path).name} (file may be too large or malformed)"
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"Docling worker exited with code {proc.returncode}: "
                f"{' | '.join(stderr_tail[-8:])[:500]}"
            )

        stdout_text = stdout.decode().strip()
        if not stdout_text:
            raise RuntimeError("Docling worker returned empty output")

        result = _json.loads(stdout_text)
        if result.get("error"):
            raise RuntimeError(f"Docling worker error: {result['error']}")

        CONVERSION_SECONDS.labels(path="local").observe(time.monotonic() - _t0)
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


def get_active_processing_ids() -> list[str]:
    """Doc ids with any live in-process activity, across both entry paths.

    Individually-started docs register an asyncio task in `_active_tasks`;
    batch processing (`process_pending_documents`) deliberately doesn't, but
    always holds a `_cancellation_flags` entry for the docs it is running.
    Module-level so callers (the hourly stranded-document sweep) don't have
    to construct the heavy DocumentProcessor just to read the registries.
    """
    ids = {doc_id for doc_id, task in _active_tasks.items() if not task.done()}
    ids.update(_cancellation_flags.keys())
    return sorted(ids)


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

        # In-process Docling converter is built lazily (see _build_docling_converter
        # / _get_converter). The live ingestion path converts in a subprocess
        # (_convert_document_subprocess), so this stays None on startup and avoids
        # importing docling/torch (~244 MB) into every backend instance.
        self.docling_converter = None

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
            try:
                from haystack.components.embedders import (
                    SentenceTransformersDocumentEmbedder,
                )

                self.embedder = SentenceTransformersDocumentEmbedder(
                    model="sentence-transformers/all-MiniLM-L6-v2"
                )
                self.embedder.warm_up()
                logger.info("Using SentenceTransformers embeddings")
            except ImportError as e:
                raise RuntimeError(
                    "Local SentenceTransformers embeddings are not available in "
                    "this image (slim build without the ML stack). Set "
                    "USE_OPENAI_EMBEDDINGS=true with EMBEDDING_API_KEY/"
                    "OPENAI_API_KEY, or deploy the full image "
                    "(INSTALL_LOCAL_ML=true)."
                ) from e

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

    def _get_strict_embedder(self):
        """Embedder that raises APIError instead of skipping a failed batch.

        Recovery needs the HTTP status to tell throttling (429/5xx → wait and
        retry the same text) from input rejection (400 → halve the text). The
        regular embedder swallows APIErrors (raise_on_failure=False), making
        the two cases indistinguishable.
        """
        if getattr(self, "_strict_embedder", None) is not None:
            return self._strict_embedder
        if self.settings.use_openai_embeddings and self.settings.embed_api_key:
            from haystack.components.embedders import OpenAIDocumentEmbedder
            from haystack.utils import Secret

            kwargs = dict(
                api_key=Secret.from_token(self.settings.embed_api_key),
                api_base_url=self.settings.embed_api_base,
                model=self.settings.embedding_model,
                raise_on_failure=True,
            )
            if self.settings.embedding_send_dimensions:
                kwargs["dimensions"] = self.settings.embedding_dimension
            self._strict_embedder = OpenAIDocumentEmbedder(**kwargs)
        else:
            self._strict_embedder = self.embedder
        return self._strict_embedder

    async def _recover_missing_embeddings(self, loop, docs):
        """Re-embed docs the batch pass left without embeddings, one by one.

        A batched embed request is rejected wholesale when ANY input fails the
        provider's validation — Haystack logs the APIError and returns every
        doc of that batch embedding-less (raise_on_failure=False), so one bad
        input silently strips embeddings from 31 innocent neighbours. Venice
        also validates the 8192-token input cap with its own tokenizer, which
        counts punctuation-heavy text ~1.2-1.4x higher than cl100k — a chunk
        can pass our client-side cap and still be rejected.

        Retrying each missing doc alone embeds the innocents. 429/5xx waits
        and retries the same text (halving a rate-limited input would only
        generate more rate-limited calls); a 400 rejection halves the text
        (both halves keep the parent's meta) until accepted or 512 chars, so
        every stored chunk ends up with an embedding regardless of tokenizer
        mismatches.
        """
        missing_idx = [
            i for i, d in enumerate(docs) if getattr(d, "embedding", None) is None
        ]
        if not missing_idx:
            return docs

        logger.warning(
            f"{len(missing_idx)} chunk(s) missing embeddings after batch pass; "
            f"retrying individually"
        )
        strict = self._get_strict_embedder()

        async def _embed_one(text: str, meta: dict):
            result = await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    strict.run,
                    documents=[HaystackDocument(content=text, meta=dict(meta))],
                ),
            )
            out = result.get("documents") or []
            return out[0] if out and out[0].embedding is not None else None

        recovered: Dict[int, list] = {}
        for i in missing_idx:
            d = docs[i]
            meta = getattr(d, "meta", {}) or {}
            queue = [d.content or ""]
            done = []
            while queue:
                piece = queue.pop(0)
                if not piece.strip():
                    continue
                embedded = None
                rejected = False
                throttle_retries = 3
                while True:
                    try:
                        embedded = await _embed_one(piece, meta)
                        break
                    except Exception as exc:  # noqa: BLE001 — keep recovering others
                        status = getattr(exc, "status_code", None)
                        if (
                            status == 429 or (status is not None and status >= 500)
                        ) and throttle_retries > 0:
                            throttle_retries -= 1
                            await asyncio.sleep(10)
                            continue
                        if status is not None and 400 <= status < 500:
                            rejected = True  # input the provider won't take — halve
                            break
                        # Unknown failure (e.g. a 200-wrapped error envelope):
                        # one blind retry, then treat as rejection.
                        if throttle_retries > 0:
                            throttle_retries = 0
                            await asyncio.sleep(5)
                            continue
                        logger.warning(f"Singleton embed retry failed: {exc}")
                        rejected = True
                        break
                if embedded is not None:
                    done.append(embedded)
                elif rejected and len(piece) > 512:
                    mid = len(piece) // 2
                    queue = [piece[:mid], piece[mid:]] + queue
                else:
                    # Exhausted retries or the provider rejects even a tiny
                    # piece — store unembedded (surfaces via the degraded-
                    # document signal).
                    logger.warning(
                        f"Chunk piece ({len(piece)} chars) could not be embedded; "
                        f"storing without embedding"
                    )
                    done.append(HaystackDocument(content=piece, meta=dict(meta)))
            recovered[i] = done

        rebuilt = []
        for i, d in enumerate(docs):
            if i in recovered:
                rebuilt.extend(recovered[i])
            else:
                rebuilt.append(d)
        still_missing = sum(
            1 for d in rebuilt if getattr(d, "embedding", None) is None
        )
        logger.info(
            f"Embedding recovery: {len(missing_idx)} missing -> "
            f"{still_missing} still unembedded ({len(rebuilt)} chunks total)"
        )
        return rebuilt

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

    def _reprocess_config_hash(self) -> str:
        """Identity of the settings that change reprocessing output.

        A delta-skip is only safe when the file AND this hash both match the
        values recorded at the last successful processing run.
        """
        s = self.settings
        src = "|".join([
            self.graph_extractor.extraction_model_name or "",
            self.graph_extractor.relationship_model_name or "",
            s.chunk_by,
            str(s.chunk_size),
            str(s.chunk_overlap),
            str(getattr(s, "sentences_per_chunk", "")),
            str(s.enable_graph_extraction),
            str(s.enable_semantic_entity_resolution),
            s.embedding_model,
            str(s.embedding_dimension),
            # Batching/capping budgets change what gets embedded and extracted
            str(s.embedding_max_input_tokens),
            str(s.extraction_max_context),
            # Bump when extraction prompt/output format changes materially —
            # existing docs must re-extract instead of delta-skipping.
            # compact-v1: ENT|/REL| line format (2026-07-08).
            "extraction-prompts:compact-v1",
        ])
        return hashlib.sha256(src.encode()).hexdigest()[:16]

    @staticmethod
    def _file_sha256(file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    async def _reprocess_delta_skip(self, doc_id: str, file_path: str) -> bool:
        """True when reprocessing can be skipped entirely: the document
        completed before, the file bytes are unchanged, and the extraction
        config matches (enable_reprocess_delta)."""
        if not getattr(self.settings, "enable_reprocess_delta", False):
            return False
        try:
            fingerprint = await asyncio.to_thread(
                self.neo4j.get_document_fingerprint, doc_id
            )
            if (
                not fingerprint
                or fingerprint.get("processing_status") != "completed"
                or not fingerprint.get("file_sha256")
            ):
                return False
            # Degraded documents (completed but 0 entities extracted, or
            # chunks missing embeddings) must NOT be delta-skipped: the whole
            # point of reprocessing them is to redo the work the unchanged
            # file+config produced incompletely last time.
            if (
                fingerprint.get("entity_count") == 0
                or (fingerprint.get("unembedded_chunk_count") or 0) > 0
            ):
                logger.info(
                    f"Document {doc_id}: degraded (entity_count="
                    f"{fingerprint.get('entity_count')}, unembedded_chunks="
                    f"{fingerprint.get('unembedded_chunk_count')}) — "
                    f"bypassing reprocess delta-skip"
                )
                return False
            file_hash = await asyncio.to_thread(self._file_sha256, file_path)
            if (
                file_hash == fingerprint["file_sha256"]
                and self._reprocess_config_hash() == fingerprint.get("config_hash")
            ):
                logger.info(
                    f"Document {doc_id}: content and extraction config "
                    f"unchanged — skipping reprocess (delta)"
                )
                await asyncio.to_thread(
                    self.neo4j.update_document_status,
                    doc_id,
                    ProcessingStatus.COMPLETED,
                    self.neo4j.get_document(doc_id).get("chunk_count", 0),
                    None,
                    "Content unchanged — reprocess skipped",
                )
                return True
        except Exception as e:
            logger.warning(
                f"Document {doc_id}: reprocess delta check failed "
                f"({e}); doing a full reprocess"
            )
        return False

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

        if await self._reprocess_delta_skip(doc_id, file_path):
            return True

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
        if await self._reprocess_delta_skip(doc_id, file_path):
            return True

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

    # Code + lightweight-markup files ingested as-is (no Docling conversion).
    # Markdown/plaintext are handled here too — running Docling on them is wasteful.
    RAW_TEXT_EXTENSIONS = {
        ".md", ".mdx", ".markdown", ".rst", ".txt",
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
        ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift",
        ".sh", ".bash", ".zsh", ".sql", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".json", ".env", ".dockerfile", ".tf", ".proto", ".graphql",
        ".vue", ".svelte", ".scala", ".lua", ".r", ".m", ".pl",
    }

    # Map extensions to a Markdown code-fence language hint so the splitter and
    # downstream entity extraction get clean, well-delimited text.
    _LANG_BY_EXT = {
        ".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript",
        ".jsx": "jsx", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp",
        ".hpp": "cpp", ".cc": "cpp", ".cs": "csharp", ".swift": "swift",
        ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".sql": "sql",
        ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini",
        ".cfg": "ini", ".json": "json", ".tf": "hcl", ".proto": "protobuf",
        ".graphql": "graphql", ".vue": "vue", ".svelte": "svelte",
        ".scala": "scala", ".lua": "lua", ".r": "r", ".pl": "perl",
    }

    def _build_docling_converter(self):
        """Build the in-process Docling converter on demand.

        docling is imported here, not at module scope: importing it pulls torch +
        docling-ibm-models (~244 MB) into the process. The live ingestion path
        converts in the docling_worker subprocess, so most instances never call
        this and never pay that cost.
        """
        from docling.datamodel.accelerator_options import (
            AcceleratorDevice,
            AcceleratorOptions,
        )
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            EasyOcrOptions,
            PdfPipelineOptions,
            TableFormerMode,
            TableStructureOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_haystack.converter import DoclingConverter, ExportType

        # Configure pipeline for accuracy on scanned docs/images. When a vision
        # model is set, skip local OCR and picture description and let the vision
        # model handle images instead.
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = not self.vision_analyzer.is_vision_model_available
        pipeline_options.do_table_structure = True
        pipeline_options.do_picture_description = not self.vision_analyzer.is_vision_model_available
        pipeline_options.table_structure_options = TableStructureOptions(
            do_cell_matching=True,
            mode=TableFormerMode.ACCURATE,  # Prioritize accuracy over speed
        )
        if not self.vision_analyzer.is_vision_model_available:
            pipeline_options.ocr_options = EasyOcrOptions(
                lang=["en", "de"],
                use_gpu=True,
                confidence_threshold=0.2,
            )
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=8,
            device=AcceleratorDevice.AUTO,  # Auto-detect CUDA/MPS/CPU
        )
        pipeline_options.generate_page_images = True
        pipeline_options.images_scale = 2.0  # 2x resolution for better text recognition

        underlying_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
        logger.info("In-process Docling converter built (lazy)")
        return DoclingConverter(
            export_type=ExportType.MARKDOWN,
            converter=underlying_converter,
        )

    def _get_converter(self, file_type: str):
        """Get the appropriate converter for a file type (built lazily).

        Note: the live ingestion path converts via _convert_document_subprocess
        (docling_worker.py). This in-process converter is retained for callers
        needing synchronous conversion and is built on first use.
        """
        if file_type.lower() in self.DOCLING_EXTENSIONS:
            if self.docling_converter is None:
                self.docling_converter = self._build_docling_converter()
            return self.docling_converter
        return None

    def _read_raw_text_file(self, file_path: str, ext: str) -> str:
        """Read a code/markdown file as text for the Docling-free fast path.

        Markdown/plaintext is returned verbatim; code is wrapped in a fenced
        block with a language hint plus a filename heading so the splitter and
        entity extraction get clean, well-delimited content.
        """
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        lang = self._LANG_BY_EXT.get(ext)
        if lang is None:
            # Markdown / rst / txt — already prose-like, ingest as-is.
            return content
        name = Path(file_path).name
        return f"# {name}\n\n```{lang}\n{content}\n```\n"

    async def store_file_only(
        self,
        file_path: str,
        filename: str,
        file_size: int,
        collection_id: Optional[str] = None,
        source: str = "upload",
        git_provenance: Optional[dict] = None,
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
            source: Origin identifier for the document

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
            source=source,
            git_connection_id=(git_provenance or {}).get("git_connection_id"),
            git_path=(git_provenance or {}).get("git_path"),
            git_blob_sha=(git_provenance or {}).get("git_blob_sha"),
            git_commit_sha=(git_provenance or {}).get("git_commit_sha"),
            git_sync_status=(git_provenance or {}).get("git_sync_status"),
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
        source: str = "upload",
        git_provenance: Optional[dict] = None,
    ) -> str:
        """
        Process a file and store it in the knowledge base.

        The original file is permanently stored and can be used for reprocessing.

        Args:
            file_path: Path to the uploaded file
            filename: Original filename
            file_size: Size in bytes
            collection_id: Optional collection to add document to
            source: Origin identifier for the document
        """
        # First store the file
        doc_id = await self.store_file_only(
            file_path, filename, file_size, collection_id, source=source,
            git_provenance=git_provenance,
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
        quota_skipped = 0

        async def process_one(doc: dict):
            nonlocal completed, failed, quota_skipped
            async with semaphore:
                # Yield before starting so the event loop can process pending requests
                await asyncio.sleep(0)

                # Unit-denominated quota: in-flight documents always finish,
                # but no NEW document starts once the monthly LLM-completion
                # budget is spent. Skipped docs stay 'pending' and resume
                # next month (or after a plan upgrade).
                if await self._monthly_quota_exhausted():
                    quota_skipped += 1
                    return

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
            + (f", {quota_skipped} skipped (monthly usage limit)" if quota_skipped else "")
        )

        message = f"Processed {completed} documents, {failed} failed"
        if quota_skipped:
            message += (
                f"; {quota_skipped} left pending — monthly usage limit reached "
                f"(they will process next month or after a plan upgrade)"
            )
        return {
            "processed": completed,
            "failed": failed,
            "quota_skipped": quota_skipped,
            "total": total,
            "message": message,
        }

    async def _monthly_quota_exhausted(self) -> bool:
        """Whether the monthly LLM-completion budget (MAX_QUERIES_PER_MONTH,
        unit-denominated) is spent. Used to stop STARTING new documents in a
        batch; never interrupts a document already processing."""
        limit = getattr(self.settings, "max_queries_per_month", 0)
        if limit <= 0:
            return False
        counts = await asyncio.to_thread(usage_meter.get_completions_this_month)
        return counts["total"] >= limit

    async def _process_document(self, doc_id: str, file_path: str, file_type: str):
        """Background task to process a document with GraphRAG extraction.

        CPU-intensive operations are run in a thread pool to avoid blocking
        the async event loop, keeping the API responsive during batch processing.

        Supports cancellation via cancellation flags - checked at key stages.
        """
        # Attribute this pipeline's LLM completions to "processing" in the
        # usage meter (covers extraction, relationships, and image analysis).
        usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)

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
                    # Flipped to "Converting document..." by the on_progress
                    # callback once the conversion-slot semaphore is acquired —
                    # a queued doc no longer claims to be converting.
                    "Waiting for a conversion slot...",
                ),
            )

            # Yield control to allow other async tasks to run
            await asyncio.sleep(0)

            # Check for cancellation before conversion
            self._check_cancellation(doc_id)

            async def _conversion_progress(message: str, fraction: float) -> None:
                # Conversion owns the 0→10% band of the pipeline.
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_progress,
                        doc_id,
                        int(10 * max(0.0, min(1.0, fraction))),
                        100,
                        message,
                    ),
                )

            # Choose conversion path. Code/markdown is ingested as-is (fast path);
            # everything else goes through Docling.
            ext = file_type.lower()
            if ext in self.RAW_TEXT_EXTENSIONS:
                use_vision = False
                md_text = await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(self._read_raw_text_file, file_path, ext),
                )
                if not md_text or not md_text.strip():
                    raise ValueError("No content extracted from file")
                filename = Path(file_path).name
                conversion_result = {"markdown": md_text, "filename": filename, "images": []}
            elif ext in self.DOCLING_EXTENSIONS:
                use_vision = self.vision_analyzer.is_vision_model_available
                conversion_result = await _convert_document_subprocess(
                    file_path, use_vision, on_progress=_conversion_progress
                )
                md_text = conversion_result["markdown"]
                if not md_text:
                    raise ValueError("No content extracted from file")
                filename = conversion_result.get("filename", Path(file_path).name)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

            documents = [
                HaystackDocument(
                    content=md_text,
                    meta={"dl_meta": {"origin": {"filename": filename}}},
                )
            ]

            # Check for cancellation after conversion
            self._check_cancellation(doc_id)

            # =================================================================
            # Prompt-injection scan (flag-only; never blocks ingestion)
            # =================================================================
            # Free heuristic always runs; the LLM classifier runs only when the
            # runtime toggle is on (env default overridable via SystemMeta).
            # Fully guarded so a scanner failure never fails ingestion.
            try:
                _llm_scan_enabled = await asyncio.to_thread(
                    self.neo4j.get_runtime_setting,
                    "ingestion_injection_scan",
                    self.settings.ingestion_injection_scan,
                )
                _scan = await scan_document(
                    md_text, llm_enabled=_llm_scan_enabled, settings=self.settings
                )
                await asyncio.to_thread(
                    self.neo4j.set_document_injection_flag,
                    doc_id, _scan.flagged, _scan.reason or "",
                )
                if _scan.flagged:
                    logger.warning(
                        "Ingestion injection scan flagged document %s via %s: %s",
                        doc_id, _scan.method, _scan.reason,
                    )
            except Exception as _scan_err:
                logger.warning(
                    "Injection scan skipped for document %s: %s", doc_id, _scan_err
                )

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
                documents = [
                    dataclasses.replace(
                        doc, content=_clean_image_placeholders(doc.content)
                    )
                    for doc in documents
                ]

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

            # Enforce embed-token cap after URL restoration (URLs can grow content).
            # Splits any oversize chunk into safe pieces so the downstream embed call
            # never trips ContextWindowExceeded errors on managed providers / litellm
            # proxies we can't tune. Zero content loss — see _enforce_embed_token_cap.
            chunks = _drop_empty_chunks(chunks)
            chunks = _enforce_embed_token_cap(chunks, self.settings.embedding_max_input_tokens)

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
            _truncate_for_embedding(chunks, self.settings.embedding_max_input_tokens)
            _embed_call = functools.partial(self.embedder.run, documents=chunks)
            try:
                embed_result = await loop.run_in_executor(
                    _get_processing_executor(), _embed_call
                )
            except Exception as exc:
                # One retry: gateways like Venice wrap upstream errors in an
                # HTTP 200 envelope with data=null, which surfaces here as a
                # TypeError from response parsing rather than an APIError the
                # embedder would handle — transient causes deserve a second try.
                logger.warning(
                    f"Embedding pass failed ({exc}); retrying once", exc_info=True
                )
                await asyncio.sleep(5)
                embed_result = await loop.run_in_executor(
                    _get_processing_executor(), _embed_call
                )
            embedded_chunks = embed_result.get("documents", [])
            embedded_chunks = await self._recover_missing_embeddings(
                loop, embedded_chunks
            )

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
                        40,
                        100,
                        "Extracting entities from document...",
                    ),
                )
                logger.info(f"Document {doc_id}: starting per-document entity extraction...")

                # Yield control before graph extraction
                await asyncio.sleep(0)

                # Check for cancellation before extraction
                self._check_cancellation(doc_id)

                # Per-document entity extraction (batched if needed). Summary
                # generation is auto: the extractor makes the summary call only
                # for multi-batch documents — a single-batch prompt already
                # contains the full document text.
                chunk_contents = [c.content for c in embedded_chunks if c.content]

                async def _entity_progress(done: int, total: int) -> None:
                    # Entity extraction owns the 40→68% band of the pipeline.
                    pct = 40 + int(28 * done / max(1, total))
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.update_document_progress,
                            doc_id,
                            pct,
                            100,
                            f"Finding entities: {done}/{total} chunks...",
                        ),
                    )

                entities = await self.graph_extractor.extract_entities_from_document_async(
                    chunks=chunk_contents,
                    max_tokens=self.settings.extraction_max_context,
                    progress_callback=_entity_progress,
                )

                # Decide dedup strategy up-front so we can pre-batch embeddings
                # for the whole document (one HTTP call) instead of one per entity.
                use_embedding_dedup = (
                    self.settings.enable_semantic_entity_resolution
                    and self.graph_extractor.async_extraction_client is not None
                )

                entity_embeddings: List[Optional[List[float]]] = []
                if use_embedding_dedup and entities:
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.update_document_progress,
                            doc_id,
                            65,
                            100,
                            f"Embedding {len(entities)} entities for semantic dedup...",
                        ),
                    )
                    try:
                        entity_embeddings = await self.graph_extractor.generate_entity_embeddings_batch_async(
                            entities
                        )
                    except Exception as e:
                        logger.warning(
                            f"Document {doc_id}: batch entity embedding failed ({e}); "
                            f"falling back to Levenshtein for all entities"
                        )
                        entity_embeddings = [None] * len(entities)

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

                # Track original → canonical name mapping for relationship extraction
                entity_canonical_map: dict[str, str] = {}

                if getattr(self.settings, "enable_batched_kg_writes", False):
                    entity_canonical_map.update(
                        await self._store_entities_batched(
                            doc_id,
                            entities,
                            entity_embeddings if use_embedding_dedup else None,
                            loop,
                        )
                    )
                    total_entities += len(entities)
                else:
                    # Update the "Storing N/total" message at most ~10 times to
                    # avoid flooding Neo4j with progress writes.
                    store_progress_interval = max(1, len(entities) // 10) if entities else 1

                    for idx, entity in enumerate(entities):
                        self._check_cancellation(doc_id)

                        embedding = entity_embeddings[idx] if use_embedding_dedup and entity_embeddings else None

                        if embedding:
                            result = await loop.run_in_executor(
                                _get_processing_executor(),
                                functools.partial(
                                    self.neo4j.store_entity_with_embedding,
                                    entity,
                                    chunk_id=None,
                                    document_id=doc_id,
                                    embedding=embedding,
                                ),
                            )
                            canonical_name = result[0] if isinstance(result, tuple) else entity.name
                            entity_canonical_map[entity.name.lower()] = canonical_name
                        else:
                            canonical_name = await loop.run_in_executor(
                                _get_processing_executor(),
                                functools.partial(
                                    self.neo4j.store_entity_with_resolution,
                                    entity,
                                    document_id=doc_id,
                                    similarity_threshold=0.85,
                                ),
                            )
                            entity_canonical_map[entity.name.lower()] = canonical_name or entity.name

                        total_entities += 1

                        if (
                            idx == 0
                            or (idx + 1) % store_progress_interval == 0
                            or idx == len(entities) - 1
                        ):
                            # Progress band 70-85% reserved for entity storage;
                            # 85% is the next phase ("Linking entities to chunks...").
                            storage_progress = 70 + int((idx + 1) / max(1, len(entities)) * 14)
                            await loop.run_in_executor(
                                _get_processing_executor(),
                                functools.partial(
                                    self.neo4j.update_document_progress,
                                    doc_id,
                                    storage_progress,
                                    100,
                                    f"Storing entity {idx + 1}/{len(entities)}...",
                                ),
                            )

                        if idx % 10 == 0:
                            await asyncio.sleep(0)

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
                if getattr(self.settings, "enable_batched_kg_writes", False):
                    await loop.run_in_executor(
                        _get_processing_executor(),
                        functools.partial(
                            self.neo4j.link_entities_to_chunks_batch,
                            [
                                {"chunk_id": cid, "entity_name": name}
                                for cid, name in chunk_entity_links
                            ],
                        ),
                    )
                else:
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
                    sem = _asyncio.Semaphore(self.settings.concurrent_relations)

                    async def _extract_from_chunk(cid: str, ents: list):
                        """Single-chunk extraction → (chunks_done, rels)."""
                        async with sem:
                            text = chunk_content_map.get(cid, "")
                            rels = await self.graph_extractor.extract_chunk_relationships_async(
                                text,
                                ents,
                                max_output_tokens=self.settings.relationship_max_output_tokens,
                            )
                            return 1, rels

                    async def _extract_from_batch(batch: list):
                        """Multi-chunk extraction (one LLM call) → (chunks_done, rels)."""
                        async with sem:
                            results_map = await self.graph_extractor.extract_chunk_relationships_batch_async(
                                batch,
                                max_output_tokens=self.settings.relationship_max_output_tokens,
                            )
                            return len(batch), [
                                r for rels in results_map.values() for r in rels
                            ]

                    # Gather all chunks with 2+ entities
                    tasks = []
                    if getattr(self.settings, "enable_batched_chunk_relationships", False):
                        # Greedy-pack eligible chunks into batches: hard cap
                        # relationship_chunks_per_call, plus a character budget
                        # (~60% of the relationship context at ~4 chars/token)
                        # so oversized chunks don't blow the prompt.
                        eligible_items = [
                            {
                                "key": cid,
                                "chunk_text": chunk_content_map.get(cid, ""),
                                "entities": ents,
                            }
                            for cid, ents in chunk_entities_map.items()
                            if len(ents) >= 2
                        ]
                        max_per_call = max(1, self.settings.relationship_chunks_per_call)
                        char_budget = max(
                            4000,
                            int(self.settings.relationship_max_context * 0.6 * 4),
                        )
                        batch: list = []
                        batch_chars = 0
                        for item in eligible_items:
                            item_chars = len(item["chunk_text"])
                            if batch and (
                                len(batch) >= max_per_call
                                or batch_chars + item_chars > char_budget
                            ):
                                tasks.append(_extract_from_batch(batch))
                                batch, batch_chars = [], 0
                            batch.append(item)
                            batch_chars += item_chars
                        if batch:
                            tasks.append(_extract_from_batch(batch))
                        if tasks:
                            logger.info(
                                f"Document {doc_id}: batched per-chunk extraction — "
                                f"{eligible_chunks} chunks in {len(tasks)} LLM calls"
                            )
                    else:
                        for cid, ents in chunk_entities_map.items():
                            if len(ents) >= 2:
                                tasks.append(_extract_from_chunk(cid, ents))

                    if tasks:
                        # Stream results via as_completed so each chunk's relationships
                        # land in Neo4j (and the visible counter ticks) the moment its
                        # LLM call returns — instead of waiting for the entire batch
                        # before any storage happens. Also offload the Neo4j write
                        # via run_in_executor so it doesn't block the event loop
                        # while other LLM tasks are still running concurrently.
                        failed_count = 0
                        completed_count = 0
                        progress_interval = max(1, eligible_chunks // 10)
                        for coro in _asyncio.as_completed(tasks):
                            try:
                                chunks_done, result = await coro
                            except Exception:
                                failed_count += 1
                                completed_count += 1
                                continue
                            chunk_new_rels = []
                            for rel in result:
                                # Remap to canonical entity names from dedup
                                rel.source = entity_canonical_map.get(rel.source.lower(), rel.source)
                                rel.target = entity_canonical_map.get(rel.target.lower(), rel.target)
                                key = (rel.source.lower(), rel.target.lower(), rel.relationship_type)
                                if key not in seen_rels:
                                    seen_rels.add(key)
                                    chunk_new_rels.append(rel)
                            if chunk_new_rels and getattr(
                                self.settings, "enable_batched_kg_writes", False
                            ):
                                # One UNWIND write per completed chunk instead of
                                # one round trip per relationship.
                                try:
                                    stored_count = await loop.run_in_executor(
                                        _get_processing_executor(),
                                        functools.partial(
                                            self.neo4j.store_relationships_batch,
                                            chunk_new_rels,
                                            source_document_id=doc_id,
                                            extraction_method="per_chunk",
                                        ),
                                    )
                                    total_relationships += stored_count
                                except Exception as e:
                                    logger.warning(f"Failed to store per-chunk relationship batch: {e}")
                            else:
                                for rel in chunk_new_rels:
                                    try:
                                        stored = await loop.run_in_executor(
                                            _get_processing_executor(),
                                            functools.partial(
                                                self.neo4j.store_relationship,
                                                rel,
                                                source_document_id=doc_id,
                                                extraction_method="per_chunk",
                                            ),
                                        )
                                        if stored:
                                            total_relationships += 1
                                    except Exception as e:
                                        logger.warning(f"Failed to store per-chunk relationship: {e}")
                            completed_count += chunks_done
                            if (
                                completed_count <= chunks_done
                                or completed_count % progress_interval < chunks_done
                                or completed_count >= eligible_chunks
                            ):
                                await loop.run_in_executor(
                                    _get_processing_executor(),
                                    functools.partial(
                                        self.neo4j.update_document_progress,
                                        doc_id,
                                        90,
                                        100,
                                        f"Extracting per-chunk relationships: {completed_count}/{eligible_chunks} chunks ({total_relationships} found)...",
                                    ),
                                )
                        if failed_count:
                            logger.warning(f"Document {doc_id}: {failed_count}/{len(tasks)} per-chunk extraction calls failed")

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

            # Update document status. entity_count is persisted only when
            # graph extraction actually ran — with extraction off (or the
            # extractor unavailable), 0 entities is normal and the field must
            # stay unset so the doc is never flagged as degraded.
            graph_extraction_ran = (
                self.graph_extractor.is_available
                and self.settings.enable_graph_extraction
            )
            await loop.run_in_executor(
                _get_processing_executor(),
                functools.partial(
                    self.neo4j.update_document_status,
                    doc_id,
                    ProcessingStatus.COMPLETED,
                    chunk_count=len(embedded_chunks),
                    entity_count=total_entities if graph_extraction_ran else None,
                ),
            )

            # Record the processed file + config identity so a later reprocess
            # of unchanged content can be skipped (enable_reprocess_delta).
            try:
                file_hash = await asyncio.to_thread(self._file_sha256, file_path)
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.set_document_fingerprint,
                        doc_id,
                        file_hash,
                        self._reprocess_config_hash(),
                    ),
                )
            except Exception as e:
                logger.debug(f"Could not record document fingerprint: {e}")

            try:
                from app.metrics import DOCUMENTS_PROCESSED
                DOCUMENTS_PROCESSED.labels(status="completed").inc()
            except Exception:
                pass

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
            try:
                from app.metrics import DOCUMENTS_PROCESSED
                DOCUMENTS_PROCESSED.labels(status="failed").inc()
            except Exception:
                pass
            try:
                await loop.run_in_executor(
                    _get_processing_executor(),
                    functools.partial(
                        self.neo4j.update_document_status,
                        doc_id,
                        ProcessingStatus.FAILED,
                        error_message=str(e),
                    ),
                )
            except Exception as status_err:
                # If even the (transient-retried) failure write can't reach
                # Neo4j, don't let it escape — the document would silently
                # strand in 'processing'. The hourly stranded-document sweep
                # resets it to pending once its heartbeat goes stale.
                logger.critical(
                    f"Could not mark document {doc_id} as failed ({status_err}); "
                    "leaving it for the stranded-document sweep"
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

    async def _store_entities_batched(
        self,
        doc_id: str,
        entities: list,
        entity_embeddings: Optional[list],
        loop,
    ) -> dict:
        """Batched resolve → cluster → write entity storage.

        Used when enable_batched_kg_writes is on. Preserves the sequential
        path's dedup semantics:
        1. Resolve each entity against the EXISTING graph (one batched
           vector-index round trip, then one batched Levenshtein round trip
           for the rest — the same embedding-first / Levenshtein-backup order
           as store_entity_with_embedding).
        2. Cluster the still-unresolved entities among themselves in Python
           (cosine >= threshold or Levenshtein >= 0.85; first occurrence is
           canonical — matching the sequential "first stored wins" order).
        3. Write new entities and merges in a few UNWIND calls.

        Returns the original-name(lower) → canonical-name map.
        """
        settings = self.settings
        n = len(entities)
        embeddings = entity_embeddings if entity_embeddings else [None] * n
        threshold = settings.entity_similarity_threshold
        executor = _get_processing_executor()

        # --- Step 1: resolution against the existing graph -------------------
        resolved: dict[int, dict] = {}
        if settings.enable_semantic_entity_resolution:
            emb_rows = [(i, embeddings[i]) for i in range(n) if embeddings[i]]
            if emb_rows:
                resolved = await loop.run_in_executor(
                    executor,
                    functools.partial(
                        self.neo4j.resolve_entities_batch_by_embedding,
                        emb_rows,
                        threshold,
                    ),
                )
        lev_rows = [
            (i, entities[i].name) for i in range(n) if i not in resolved
        ]
        if lev_rows:
            lev_matches = await loop.run_in_executor(
                executor,
                functools.partial(
                    self.neo4j.resolve_entities_batch_by_name, lev_rows, 0.85
                ),
            )
            for idx, match in lev_matches.items():
                resolved.setdefault(idx, match)

        # --- Step 2: cluster unresolved entities within the batch -------------
        def _cosine(a, b) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        def _same_entity(i: int, r: int) -> bool:
            from rapidfuzz.distance import Levenshtein

            if (
                settings.enable_semantic_entity_resolution
                and embeddings[i] is not None
                and embeddings[r] is not None
                and _cosine(embeddings[i], embeddings[r]) >= threshold
            ):
                return True
            return (
                Levenshtein.normalized_similarity(
                    entities[i].name.lower(), entities[r].name.lower()
                )
                >= 0.85
            )

        cluster_rep: dict[int, int] = {}
        reps: list[int] = []
        for i in range(n):
            if i in resolved:
                continue
            rep = next((r for r in reps if _same_entity(i, r)), None)
            if rep is None:
                reps.append(i)
                cluster_rep[i] = i
            else:
                cluster_rep[i] = rep

        # --- Step 3: batched writes ------------------------------------------
        canonical_map: dict[str, str] = {}
        new_rows: list[dict] = []
        merge_rows: list[dict] = []
        for i in range(n):
            name = entities[i].name
            if i in resolved:
                canonical = resolved[i]["name"]
                merge_rows.append({
                    "canonical": canonical,
                    "alias": name if canonical.lower() != name.lower() else None,
                    "doc_id": doc_id,
                })
            else:
                rep = cluster_rep[i]
                canonical = entities[rep].name
                if i == rep:
                    new_rows.append({
                        "name": name,
                        "type": entities[i].type,
                        "description": entities[i].description,
                        "embedding": embeddings[i],
                        "doc_id": doc_id,
                    })
                else:
                    merge_rows.append({
                        "canonical": canonical,
                        "alias": name if canonical.lower() != name.lower() else None,
                        "doc_id": doc_id,
                    })
            canonical_map[name.lower()] = canonical

        write_batch_size = 500
        total_writes = len(new_rows) + len(merge_rows)
        written = 0
        for start in range(0, len(new_rows), write_batch_size):
            self._check_cancellation(doc_id)
            batch = new_rows[start:start + write_batch_size]
            await loop.run_in_executor(
                executor,
                functools.partial(self.neo4j.store_entities_batch, batch),
            )
            written += len(batch)
            await self._report_entity_store_progress(
                doc_id, written, total_writes, loop, executor
            )
        for start in range(0, len(merge_rows), write_batch_size):
            self._check_cancellation(doc_id)
            batch = merge_rows[start:start + write_batch_size]
            await loop.run_in_executor(
                executor,
                functools.partial(self.neo4j.apply_entity_merges_batch, batch),
            )
            written += len(batch)
            await self._report_entity_store_progress(
                doc_id, written, total_writes, loop, executor
            )

        logger.info(
            f"Document {doc_id}: batched entity storage — {len(new_rows)} new, "
            f"{len(merge_rows)} merged into existing/cluster canonicals "
            f"({len(resolved)} resolved against graph)"
        )
        return canonical_map

    async def _report_entity_store_progress(
        self, doc_id: str, done: int, total: int, loop, executor
    ) -> None:
        """Progress tick inside the 70-85% entity-storage band."""
        progress = 70 + int(done / max(1, total) * 14)
        try:
            await loop.run_in_executor(
                executor,
                functools.partial(
                    self.neo4j.update_document_progress,
                    doc_id,
                    progress,
                    100,
                    f"Storing entity {done}/{total}...",
                ),
            )
        except Exception:
            pass

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

        # Targeted mode (default): zero-LLM candidate generation + pair
        # verification. The legacy full-batch LLM scan remains available via
        # RELATIONSHIP_DISCOVERY_MODE=llm_scan.
        mode = (self.settings.relationship_discovery_mode or "targeted").strip().lower()
        if mode != "llm_scan":
            return await self._analyze_relationships_targeted(
                entities, collection_id, progress_callback
            )

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

        # Phase B checkpointing: a crash/redeploy mid-analysis resumes instead
        # of re-paying every batch's LLM cost, and rounds 2+ reuse round 1's
        # Phase 1 candidates (Phase 2 already avoids existing relationships).
        checkpointing = getattr(self.settings, "enable_phaseb_checkpointing", False)
        run_signature = None
        if checkpointing:
            import hashlib as _hashlib

            sig_src = (
                "|".join(sorted(n.lower() for n in entity_names))
                + f"::{self.graph_extractor.extraction_model_name}"
                + f"::{self.graph_extractor.relationship_model_name}"
                + f"::{target_ratio}::{max_rounds}"
            )
            run_signature = _hashlib.sha256(sig_src.encode()).hexdigest()[:32]
            try:
                if rebuild:
                    cleared = await asyncio.to_thread(
                        self.neo4j.clear_phaseb_checkpoints
                    )
                else:
                    # Drop checkpoints from runs with a different entity set /
                    # config — they can never match this run's batch keys.
                    cleared = await asyncio.to_thread(
                        self.neo4j.clear_phaseb_checkpoints, run_signature
                    )
                if cleared:
                    logger.info(f"Cleared {cleared} stale Phase B checkpoint(s)")
            except Exception as e:
                logger.warning(f"Could not clear Phase B checkpoints: {e}")

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

            checkpoint_hooks = None
            if checkpointing:
                from app.services.graph_extractor import PhaseBCheckpointHooks

                _round = round_num

                async def _is_done(batch_key: str, _r=_round) -> bool:
                    cp = await asyncio.to_thread(
                        self.neo4j.get_phaseb_checkpoint, run_signature, batch_key
                    )
                    return bool(cp and cp["phase2_done"] and cp["round"] == _r)

                async def _get_candidates(batch_key: str):
                    cp = await asyncio.to_thread(
                        self.neo4j.get_phaseb_checkpoint, run_signature, batch_key
                    )
                    return cp["candidates"] if cp else None

                async def _save_candidates(batch_key: str, candidates: list, _r=_round) -> None:
                    await asyncio.to_thread(
                        self.neo4j.upsert_phaseb_checkpoint,
                        run_signature, batch_key, _r, candidates,
                    )

                async def _mark_done(batch_key: str, _r=_round) -> None:
                    await asyncio.to_thread(
                        self.neo4j.upsert_phaseb_checkpoint,
                        run_signature, batch_key, _r, None, True,
                    )

                checkpoint_hooks = PhaseBCheckpointHooks(
                    is_batch_done=_is_done,
                    get_candidates=_get_candidates,
                    save_candidates=_save_candidates,
                    mark_batch_done=_mark_done,
                )

            # Run two-phase batched relationship analysis
            # Phase 1 uses extraction model context (larger), Phase 2 uses main model context
            relationships = await self.graph_extractor.analyze_relationships_batched_async(
                all_entities=entities,
                context="",
                max_context_tokens=self.settings.relationship_max_context,
                max_output_tokens=self.settings.relationship_batch_max_output_tokens,
                existing_relationships=existing_relationships,
                on_batch_complete=store_batch_relationships,
                get_batch_context=get_batch_context,
                progress_stats=storage_stats,
                parallel_batches=self.settings.parallel_relationship_batches or self.settings.concurrent_extractions,
                entity_co_occurrence=entity_co_occurrence,
                extraction_max_context=self.settings.extraction_max_context,
                checkpoint=checkpoint_hooks,
            )

            rounds_completed += 1
            round_elapsed = time.monotonic() - _round_start
            logger.info(
                f"{round_prefix}Complete: {len(relationships)} discovered, "
                f"{storage_stats['stored']} stored in {round_elapsed:.1f}s"
            )

        # All rounds completed — the checkpoints have served their purpose.
        if checkpointing:
            try:
                await asyncio.to_thread(self.neo4j.clear_phaseb_checkpoints)
            except Exception as e:
                logger.warning(f"Could not clear Phase B checkpoints: {e}")

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

    async def _analyze_relationships_targeted(
        self,
        entities: List[dict],
        collection_id: Optional[str],
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """Targeted Phase B discovery (Step 2 v2).

        Candidate pairs are generated WITHOUT the LLM — entity-embedding kNN
        via the `entity_embedding` vector index plus document co-mention —
        then ranked, capped, and verified in small LLM calls of
        `relationship_pairs_per_call` pairs each. Compared to the legacy
        full-batch scan (2-3 near-context-window LLM calls per 120-entity
        batch, ~250 batches per round on a 28k-entity graph), this reduces
        LLM work by orders of magnitude while focusing it on pairs that have
        an actual signal.
        """
        import time

        from app.services.relationship_candidates import (
            group_pairs_for_verification,
            merge_and_rank_candidates,
        )

        settings = self.settings
        entity_map = {e["name"]: e for e in entities if e.get("name")}
        entity_names = list(entity_map.keys())
        entity_count = len(entity_names)
        target_ratio = settings.relationship_target_ratio
        analysis_start = time.monotonic()
        max_hours = settings.relationship_max_hours
        deadline = (analysis_start + max_hours * 3600) if max_hours > 0 else None

        def report(current: int, total: int, msg: str):
            if progress_callback:
                progress_callback(current, total, msg)

        def final_result(discovered: int, stored: int, candidates_count: int) -> dict:
            final_rel_count = self.neo4j.get_relationship_count()
            final_ratio = final_rel_count / entity_count if entity_count else 0.0
            return {
                "relationships_discovered": discovered,
                "relationships_stored": stored,
                "entities_analyzed": entity_count,
                "collection_id": collection_id,
                "entity_relationship_ratio": round(final_ratio, 2),
                "target_ratio": target_ratio,
                "rounds_completed": 1,
                "total_relationships": final_rel_count,
                "discovery_mode": "targeted",
                "candidate_pairs": candidates_count,
            }

        # --- Stage 1: embedding coverage for the kNN candidate scan ---
        knn_enabled = bool(settings.embed_api_key)
        if knn_enabled:
            report(0, 1, "Checking entity embedding coverage...")
            missing = await asyncio.to_thread(
                self.neo4j.get_entities_missing_embedding, entity_names
            )
            if missing:
                report(
                    0, len(missing),
                    f"Embedding {len(missing)} entities for candidate search...",
                )
                from app.models import Entity as EntityModel

                entity_objs = [
                    EntityModel(
                        name=m["name"],
                        type=m.get("type") or "Other",
                        description=m.get("description") or "",
                    )
                    for m in missing
                ]
                embeddings = await self.graph_extractor.generate_entity_embeddings_batch_async(
                    entity_objs
                )
                rows = [
                    {"name": m["name"], "embedding": emb}
                    for m, emb in zip(missing, embeddings)
                    if emb
                ]
                if rows:
                    await asyncio.to_thread(
                        self.neo4j.set_entity_embeddings_bulk, rows
                    )
                logger.info(
                    f"Targeted Phase B: backfilled embeddings for "
                    f"{len(rows)}/{len(missing)} entities"
                )
                if not rows and len(missing) == entity_count:
                    knn_enabled = False  # embedding provider unavailable
        else:
            logger.warning(
                "Targeted Phase B: no embedding API key — kNN candidate "
                "generation disabled, using document co-mention only"
            )

        # --- Stage 2: candidate generation (no LLM) ---
        report(0, 1, "Scanning for candidate pairs (vector kNN + document co-mentions)...")
        knn_raw: list = []
        doc_raw: list = []
        if knn_enabled:
            try:
                knn_raw = await asyncio.to_thread(
                    self.neo4j.get_knn_candidate_pairs,
                    entity_names,
                    settings.relationship_knn_k,
                    settings.relationship_knn_min_similarity,
                )
            except Exception as e:
                logger.warning(f"kNN candidate scan failed (continuing without): {e}")
        if settings.relationship_min_shared_docs > 0:
            try:
                doc_raw = await asyncio.to_thread(
                    self.neo4j.get_doc_cooccurrence_pairs,
                    entity_names,
                    settings.relationship_min_shared_docs,
                    settings.relationship_doc_freq_cap,
                )
            except Exception as e:
                logger.warning(f"Doc co-mention candidate scan failed (continuing without): {e}")

        # Restrict to the analyzed entity set (kNN targets are index-global,
        # which matters for collection-scoped runs).
        knn_raw = [p for p in knn_raw if p[0] in entity_map and p[1] in entity_map]
        doc_raw = [p for p in doc_raw if p[0] in entity_map and p[1] in entity_map]

        candidates = merge_and_rank_candidates(
            knn_raw,
            doc_raw,
            per_entity_cap=settings.relationship_candidates_per_entity,
            total_cap=settings.relationship_max_candidate_pairs,
        )
        logger.info(
            f"Targeted Phase B: {len(knn_raw)} kNN + {len(doc_raw)} co-mention "
            f"raw pairs → {len(candidates)} ranked candidates "
            f"({entity_count} entities)"
        )
        if not candidates:
            report(1, 1, "No new candidate pairs found — graph is well connected")
            return final_result(0, 0, 0)

        # --- Stage 3: LLM verification in small pair batches ---
        groups = group_pairs_for_verification(
            candidates, settings.relationship_pairs_per_call
        )
        total_groups = len(groups)
        report(
            0, total_groups,
            f"Verifying {len(candidates)} candidate pairs "
            f"({total_groups} batches)...",
        )

        max_per_entity = settings.relationship_max_per_entity
        if max_per_entity > 0:
            entity_degrees = await asyncio.to_thread(
                self.neo4j.get_entity_degree_map, entity_names
            )
        else:
            entity_degrees = {}

        stats = {"discovered": 0, "stored": 0, "groups_done": 0, "skipped_time": 0}
        seen_keys: set = set()
        store_lock = asyncio.Lock()
        parallel = max(
            1,
            settings.parallel_relationship_batches or settings.concurrent_extractions,
        )
        semaphore = asyncio.Semaphore(parallel)

        async def verify_group(pairs: list) -> None:
            async with semaphore:
                if deadline and time.monotonic() > deadline:
                    async with store_lock:
                        stats["skipped_time"] += 1
                        stats["groups_done"] += 1
                    return

                group_names: list = []
                seen_names: set = set()
                for src, tgt in pairs:
                    for n in (src, tgt):
                        if n not in seen_names:
                            seen_names.add(n)
                            group_names.append(n)
                group_entities = [entity_map[n] for n in group_names]

                context = ""
                if settings.relationship_pair_context_tokens > 0:
                    try:
                        context = await asyncio.to_thread(
                            self.neo4j.get_chunk_context_for_entities,
                            group_names,
                            token_budget=settings.relationship_pair_context_tokens,
                        )
                    except Exception as e:
                        logger.warning(f"Pair context fetch failed (verifying without): {e}")

                rels = await self.graph_extractor.analyze_relationships_async(
                    group_entities,
                    context,
                    None,
                    settings.relationship_batch_max_output_tokens,
                    candidate_pairs=pairs,
                )

            async with store_lock:
                stats["groups_done"] += 1
                for rel in rels:
                    key = (rel.source.lower(), rel.target.lower(), rel.relationship_type)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    stats["discovered"] += 1
                    if getattr(rel, "confidence", 1.0) < 0.5:
                        continue
                    if max_per_entity > 0:
                        src_deg = entity_degrees.get(rel.source, 0)
                        tgt_deg = entity_degrees.get(rel.target, 0)
                        if src_deg >= max_per_entity and tgt_deg >= max_per_entity:
                            continue
                    try:
                        stored = await asyncio.to_thread(
                            self.neo4j.store_relationship,
                            rel,
                            None,
                            "cross_collection",
                        )
                        if stored:
                            stats["stored"] += 1
                            entity_degrees[rel.source] = entity_degrees.get(rel.source, 0) + 1
                            entity_degrees[rel.target] = entity_degrees.get(rel.target, 0) + 1
                    except Exception as e:
                        logger.warning(
                            f"Failed to store relationship {rel.source} -> {rel.target}: {e}"
                        )

                done = stats["groups_done"]
                elapsed = time.monotonic() - analysis_start
                avg = elapsed / done if done else 0
                eta_seconds = int(avg * (total_groups - done))
                if eta_seconds > 60:
                    eta_str = f"~{eta_seconds // 60}m remaining"
                elif eta_seconds > 0:
                    eta_str = f"~{eta_seconds}s remaining"
                else:
                    eta_str = "almost done"
                report(
                    done, total_groups,
                    f"Batch {done}/{total_groups} ({stats['stored']} found), {eta_str}",
                )

        await asyncio.gather(*(verify_group(g) for g in groups))

        if stats["skipped_time"]:
            logger.info(
                f"Targeted Phase B: time budget ({max_hours}h) exhausted — "
                f"skipped {stats['skipped_time']}/{total_groups} verification batches"
            )

        result = final_result(stats["discovered"], stats["stored"], len(candidates))
        elapsed_min = (time.monotonic() - analysis_start) / 60
        report(
            total_groups, total_groups,
            f"Relationship analysis complete — {stats['stored']} stored, "
            f"ratio {result['entity_relationship_ratio']:.1f}/{target_ratio}",
        )
        logger.info(
            f"Targeted Phase B complete in {elapsed_min:.1f}m: {result}"
        )
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
        # Collect entity names from image extractions for cross-linking to text chunks
        image_entity_names: list[str] = []
        entity_names_lock = asyncio.Lock()
        # Shared across all of this document's images: the same entity often
        # appears in several images (logos, recurring diagram nodes) — embed it
        # once. Concurrent misses may rarely double-embed; results identical.
        entity_embedding_cache: dict = {}

        logger.info(
            f"Document {doc_id}: starting background analysis of {total} images "
            f"(extraction tier={self.graph_extractor.extraction_model_name})"
        )

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

                image_metadata = {
                    "type": "image_analysis",
                    "image_id": analysis.image_id,
                    "analysis_method": analysis.analysis_method,
                }
                if extracted.page_number is not None:
                    image_metadata["page_number"] = extracted.page_number
                if extracted.caption:
                    image_metadata["caption"] = extracted.caption

                image_chunk = DocumentChunk(
                    id=image_chunk_id,
                    document_id=doc_id,
                    content=image_content,
                    embedding=None,
                    chunk_index=1000 + idx,
                    metadata=image_metadata,
                )

                if (image_content or "").strip():
                    image_doc = HaystackDocument(
                        content=image_content,
                        meta=image_chunk.metadata,
                    )
                    _truncate_for_embedding(
                        [image_doc], self.settings.embedding_max_input_tokens
                    )
                    embed_result = await loop.run_in_executor(
                        img_executor,
                        functools.partial(
                            self.embedder.run,
                            documents=[image_doc],
                        ),
                    )
                    embedded_docs = embed_result.get("documents", [])
                    # Same recovery as text chunks: a transient 429 on this
                    # singleton call must not strand the image chunk unembedded.
                    embedded_docs = await self._recover_missing_embeddings(
                        loop, embedded_docs
                    )
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

                # Graph extraction for image content — single combined call
                # (entities + relationships) on the extraction tier (qwen3).
                # The combined-prompt approach co-generates relationships with
                # entity descriptions, which empirically produces a much richer
                # relationship set on short image-description content than the
                # split entity-then-relationship pattern used for text chunks.
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
                            # Mirror the text-entity path: batch-embed extracted
                            # entities so image entities also get embedding-first
                            # dedup when ENABLE_SEMANTIC_ENTITY_RESOLUTION=true.
                            entity_embeddings: Optional[List[Optional[List[float]]]] = None
                            if (
                                self.settings.enable_semantic_entity_resolution
                                and extraction.entities
                                and self.graph_extractor.async_extraction_client is not None
                            ):
                                try:
                                    entity_embeddings = await self.graph_extractor.generate_entity_embeddings_batch_async(
                                        extraction.entities,
                                        cache=entity_embedding_cache,
                                    )
                                except Exception as embed_err:
                                    logger.warning(
                                        f"Document {doc_id}: image entity batch "
                                        f"embedding failed for image {idx + 1} "
                                        f"({embed_err}); falling back to Levenshtein"
                                    )
                                    entity_embeddings = None
                            await loop.run_in_executor(
                                img_executor,
                                functools.partial(
                                    self.neo4j.store_graph_extraction,
                                    image_chunk_id,
                                    extraction,
                                    source_document_id=doc_id,
                                    extraction_method="per_chunk",
                                    entity_embeddings=entity_embeddings,
                                ),
                            )
                            if extraction.entities:
                                async with entity_names_lock:
                                    image_entity_names.extend(
                                        e.name for e in extraction.entities
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

        # Cross-link image-extracted entities to text chunks so they're
        # discoverable via text-chunk traversal in RAG queries.
        if image_entity_names:
            try:
                text_chunks = await loop.run_in_executor(
                    img_executor,
                    functools.partial(
                        self.neo4j.get_text_chunks_for_document, doc_id
                    ),
                )
                if text_chunks:
                    from rapidfuzz import fuzz

                    unique_names = list(dict.fromkeys(image_entity_names))
                    cross_links = 0
                    for chunk_row in text_chunks:
                        chunk_content_lower = (chunk_row["content"] or "").lower()
                        if not chunk_content_lower:
                            continue
                        for entity_name in unique_names:
                            name_lower = entity_name.lower()
                            if (
                                name_lower in chunk_content_lower
                                or fuzz.partial_ratio(name_lower, chunk_content_lower) >= 85
                            ):
                                await loop.run_in_executor(
                                    img_executor,
                                    functools.partial(
                                        self.neo4j.link_entity_to_chunk,
                                        entity_name,
                                        chunk_row["id"],
                                    ),
                                )
                                cross_links += 1
                    if cross_links:
                        logger.info(
                            f"Document {doc_id}: cross-linked {cross_links} "
                            f"image entity→text chunk MENTIONS"
                        )
            except Exception as e:
                logger.warning(
                    f"Document {doc_id}: image entity cross-linking failed: {e}"
                )

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
        self._reranker_last_used = 0.0  # monotonic ts of last local rerank
        self._reranker_lock = threading.Lock()  # guard concurrent load/unload

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
            # Batch-capable embedder for embedding several queries in one call,
            # configured IDENTICALLY to text_embedder so query vectors stay
            # comparable with stored document vectors.
            from haystack.components.embedders import OpenAIDocumentEmbedder

            self.batch_embedder = OpenAIDocumentEmbedder(
                **text_embedder_kwargs, progress_bar=False
            )
            logger.info(
                f"Using OpenAI text embeddings: {self.settings.embedding_model} (dim={self.settings.embedding_dimension})"
            )
        else:
            try:
                from haystack.components.embedders import (
                    SentenceTransformersTextEmbedder,
                    SentenceTransformersDocumentEmbedder,
                )

                self.text_embedder = SentenceTransformersTextEmbedder(
                    model="sentence-transformers/all-MiniLM-L6-v2"
                )
                self.text_embedder.warm_up()
                self.batch_embedder = SentenceTransformersDocumentEmbedder(
                    model="sentence-transformers/all-MiniLM-L6-v2", progress_bar=False
                )
                self.batch_embedder.warm_up()
                logger.info("Using SentenceTransformers text embeddings")
            except ImportError as e:
                raise RuntimeError(
                    "Local SentenceTransformers embeddings are not available in "
                    "this image (slim build without the ML stack). Set "
                    "USE_OPENAI_EMBEDDINGS=true with EMBEDDING_API_KEY/"
                    "OPENAI_API_KEY, or deploy the full image "
                    "(INSTALL_LOCAL_ML=true)."
                ) from e

        logger.info(
            "Query processor initialized (GraphRAG + Reranking + Agentic RAG enabled)"
        )

    @property
    def reranker(self):
        """Lazy-load the local cross-encoder (thread-safe).

        Returns None when reranking is disabled or a remote reranker service is
        configured (in which case scoring happens over HTTP, not in-process).
        """
        if self.settings.reranker_service_url or not self.settings.enable_reranking:
            return None
        if self._reranker is None:
            with self._reranker_lock:
                if self._reranker is None:  # re-check inside the lock
                    try:
                        from sentence_transformers import CrossEncoder

                        self._reranker = CrossEncoder(self.settings.reranking_model)
                        logger.info(
                            f"Loaded cross-encoder: {self.settings.reranking_model}"
                        )
                    except ImportError:
                        logger.warning(
                            "Local cross-encoder unavailable (slim image without "
                            "the ML stack) — set RERANKER_SERVICE_URL to use the "
                            "shared helper, or deploy the full image. Reranking "
                            "disabled."
                        )
                        self._reranker = False  # Mark as unavailable
                    except Exception as e:
                        logger.warning(
                            f"Failed to load cross-encoder, disabling reranking: {e}"
                        )
                        self._reranker = False  # Mark as unavailable
        return self._reranker if self._reranker else None

    def prewarm_reranker(self) -> None:
        """Fire-and-forget background load of the local reranker.

        Call this at the START of a request so the ~7 s cold start overlaps the
        query-analysis LLM call + embedding + search that run before reranking,
        instead of blocking the rerank step. Idempotent and non-blocking; no-op
        in remote/disabled mode or once the model is loaded.
        """
        if self.settings.reranker_service_url or not self.settings.enable_reranking:
            return
        if self._reranker is not None:  # loaded or marked-failed
            return
        _rerank_executor.submit(lambda: self.reranker)

    def maybe_unload_reranker(self) -> bool:
        """Unload the local reranker if idle past the configured TTL.

        Called by the background reaper. Reclaims ~1 GB; the model reloads on the
        next query. Returns True if it unloaded. TTL 0 = never unload.
        """
        ttl = self.settings.reranker_idle_ttl_seconds
        if ttl <= 0 or not self._reranker:
            return False
        if time.monotonic() - self._reranker_last_used < ttl:
            return False
        with self._reranker_lock:
            if not self._reranker:
                return False
            self._reranker = None
        gc.collect()
        logger.info(f"Unloaded idle cross-encoder (idle > {ttl}s); reclaimed reranker memory")
        return True

    def _rerank_remote(self, query: str, passages: List[str]) -> Optional[List[float]]:
        """Score passages via the shared reranker service. None on failure.

        Transport (retries, circuit breaker, headers) lives in helper_client;
        rerank degradation is safe so a final failure just keeps the
        original result order.
        """
        from app.services.helper_client import rerank

        return rerank(query, passages)

    def rerank_results(
        self, query: str, results: List[dict], top_k: int = 5
    ) -> List[dict]:
        """
        Re-rank results using a cross-encoder for better precision.

        Uses the shared reranker service when configured, else the local model.
        Cross-encoders score query-document pairs directly, more accurately than
        the bi-encoder similarity that produced the candidates.
        """
        if not results:
            return results[:top_k]

        try:
            passages = [r.get("content", "") for r in results]
            if self.settings.reranker_service_url:
                scores = self._rerank_remote(query, passages)
            else:
                model = self.reranker
                if not model:
                    return results[:top_k]
                self._reranker_last_used = time.monotonic()
                scores = model.predict([(query, p) for p in passages])

            if scores is None:  # remote failed → keep original order
                return results[:top_k]

            for i, score in enumerate(scores):
                results[i]["rerank_score"] = float(score)
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

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """Embed several queries in ONE batched call (order preserved).

        Uses batch_embedder, which is configured identically to text_embedder,
        so the resulting vectors are comparable with stored document vectors.
        """
        if not queries:
            return []
        from haystack import Document

        docs = [Document(content=q) for q in queries]
        result = self.batch_embedder.run(documents=docs)
        return [doc.embedding for doc in result["documents"]]

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None,
    ) -> list[dict]:
        """Perform semantic search, optionally scoped to a collection or list of collections."""
        # Generate query embedding
        query_embedding = self.embed_query(query)

        # Search in Neo4j
        results = self.neo4j.vector_search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
            collection_id=collection_id,
            allowed_collection_ids=allowed_collection_ids,
        )

        return results

    def hybrid_search(
        self,
        query: str,
        top_k: int = 10,
        vector_weight: float = 0.5,
        keyword_weight: float = 0.3,
        metadata_weight: float = 0.2,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None,
    ) -> list[dict]:
        """
        Perform hybrid search combining:
        - Vector similarity (semantic search)
        - Full-text keyword search (content matching)
        - Metadata search (filename, topic hint for custom inputs)

        Uses Reciprocal Rank Fusion (RRF) to merge results.
        Optionally scoped to a specific collection or list of collections.
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
            collection_id=collection_id,
            allowed_collection_ids=allowed_collection_ids,
        )

        return results

    async def graph_search_async(
        self,
        query: str,
        top_k: int = 5,
        max_hops: int = 2,
        use_hybrid_rrf: bool = True,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None,
        precomputed_entities: Optional[List[str]] = None,
        precomputed_embedding: Optional[List[float]] = None,
    ) -> dict:
        """
        Perform hybrid search combining vector similarity, keyword search, and graph traversal.
        Uses Reciprocal Rank Fusion (RRF) for better results.
        Optionally scoped to a specific collection or list of collections.

        Embed + Neo4j calls are I/O-bound and synchronous, so they run in threads.
        Without this, three parallel `graph_search_async` calls from the researcher
        serialize on the loop and produce multi-second event-loop blocks.

        Returns:
            Dict with 'results', 'graph_context', and search metadata
        """
        # Query embedding. Use the caller's precomputed vector when provided
        # (e.g. the researcher batches all queries into one embedding call);
        # otherwise embed on demand (HTTPS call → run in thread).
        if precomputed_embedding is not None:
            query_embedding = precomputed_embedding
        else:
            query_embedding = await asyncio.to_thread(self.embed_query, query)

        # Entities for graph traversal. When the caller has already extracted them
        # (e.g. the researcher batches all queries into ONE LLM call), use those and
        # skip the per-query extraction call. Otherwise extract on demand.
        if precomputed_entities is not None:
            query_entities = precomputed_entities
        elif self.graph_extractor.is_available:
            query_entities = (
                await self.graph_extractor.extract_entities_from_query_async(query)
            )
        else:
            query_entities = []

        # Use hybrid search with RRF if enabled
        if use_hybrid_rrf and self.settings.enable_hybrid_search:
            hybrid_result = await asyncio.to_thread(
                self.neo4j.hybrid_search_rrf,
                query_embedding=query_embedding,
                query_text=query,
                entity_names=query_entities,
                top_k=top_k,
                max_hops=max_hops,
                vector_weight=self.settings.vector_weight,
                keyword_weight=self.settings.keyword_weight,
                graph_weight=self.settings.graph_weight,
                collection_id=collection_id,
                allowed_collection_ids=allowed_collection_ids,
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
            # Legacy hybrid search — no collection filter available here, falls back to full scan
            result = await asyncio.to_thread(
                self.neo4j.hybrid_search,
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
        allowed_collection_ids: Optional[List[str]] = None,
    ) -> dict:
        """
        Answer a question using enhanced GraphRAG features.
        Optionally scoped to a specific collection or list of collections.

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
                allowed_collection_ids=allowed_collection_ids,
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
                question, top_k=top_k * 2, collection_id=collection_id,
                allowed_collection_ids=allowed_collection_ids
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
            # Resolve the LLM config from settings
            llm_config = get_llm_config()
            client = make_openai_client(
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

            # Run the blocking OpenAI SDK call in a thread: this is an async
            # endpoint, and a synchronous generation call (~15-20s) would pin
            # the event loop, starving every other in-flight request's async
            # work (Neo4j acquisition, etc.) and cascading into timeouts/500s
            # under concurrency. Embeddings/Neo4j already use to_thread.
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=llm_config.model,
                messages=messages,
                **build_chat_params(llm_config.model, temperature=0.3, max_tokens=1200),
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

        # Resolve the LLM config from settings
        llm_config = get_llm_config()
        client = make_openai_client(
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

        decompose_response = await asyncio.to_thread(
            client.chat.completions.create,
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
            **build_chat_params(llm_config.model, temperature=0.2, max_tokens=300),
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

        # Threaded for the same reason as the non-agentic path: a sync LLM
        # call here would block the event loop for the whole generation.
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=llm_config.model,
            messages=messages,
            **build_chat_params(llm_config.model, temperature=0.3, max_tokens=2000),
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

        # Resolve the LLM config from settings
        llm_config = get_llm_config()

        if not llm_config.api_key:
            yield {"error": "OpenAI API key required for streaming"}
            return

        client = make_async_openai_client(
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
            **build_chat_params(llm_config.model, temperature=0.2, max_tokens=300),
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

        # Fence retrieved/graph context as untrusted data (spotlighting).
        _sec = self.settings.prompt_security
        fenced_sources = wrap_untrusted(
            formatted_sources, source="retrieved documents", enabled=_sec
        )
        fenced_graph = wrap_untrusted(
            graph_context_str, source="knowledge graph", enabled=_sec
        )

        messages.append(
            {
                "role": "user",
                "content": f"""Research Context:
{fenced_sources}
{fenced_graph}

Question: {question}

Comprehensive Answer:""",
            }
        )

        # Stream the response
        stream = await client.chat.completions.create(
            model=llm_config.model,
            messages=messages,
            stream=True,
            **stream_usage_kwargs(),
            **build_chat_params(llm_config.model, temperature=0.3, max_tokens=2000),
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
        allowed_collection_ids: Optional[List[str]] = None,
        conversation_memory: Optional[dict] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Stream research pipeline results using the agent-based architecture.

        The researcher agent uses tool-calling to iteratively gather information,
        then the writer synthesizes it into a streamed answer.

        Args:
            question: The user's question
            mode: "speed" for chat, "quality" for deep research
            conversation_history: Previous conversation messages
            collection_id: Optional single collection scope
            allowed_collection_ids: Optional list of allowed collections (for restricted API keys)
        """
        from app.services.researcher_agent import run_research_pipeline
        from app.services.llm_config import get_llm_config

        llm_config = get_llm_config()

        async for event in run_research_pipeline(
            question=question,
            mode=mode,
            conversation_history=conversation_history,
            collection_id=collection_id,
            allowed_collection_ids=allowed_collection_ids,
            processor=self,
            neo4j_service=self.neo4j,
            llm_config=llm_config,
            settings=self.settings,
            conversation_memory=conversation_memory,
        ):
            yield event

    async def agent_rag_query(
        self,
        question: str,
        mode: str = "quality",
        conversation_history: Optional[List[ConversationMessage]] = None,
        collection_id: Optional[str] = None,
        allowed_collection_ids: Optional[List[str]] = None,
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
            allowed_collection_ids=allowed_collection_ids,
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
