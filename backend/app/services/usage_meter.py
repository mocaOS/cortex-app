"""Instance-wide LLM completion metering (unit-denominated quota).

``MAX_QUERIES_PER_MONTH`` is denominated in **internal LLM completions**, not
HTTP requests: every successful chat-completion call (each researcher-loop
iteration, the writer, entity extraction, per-chunk relationships, Phase B
verification, vision analysis, ...) consumes one unit, aligning quota
consumption with inference cost. Embeddings never count.

Counting happens at the client-factory choke point (`llm_config` wraps
``chat.completions.create`` on every client it builds) plus one manual call in
the raw-httpx vision path. Increments land in memory first and a single daemon
flusher batches them to Neo4j (`LLMUsageDay` nodes, one per UTC day), so a
graph build doing thousands of calls doesn't do one write per call. At most a
few seconds of counts are lost on a hard restart — acceptable undercount.

Attribution: callers stamp a contextvar (`set_usage_kind`) at pipeline entry —
"query" for ask/search requests, "processing" for document/graph work — so the
frontend meter can say what consumed the quota. Unstamped calls count as
"other" (still consume quota).
"""

import logging
import threading
import time
from contextvars import ContextVar
from datetime import datetime

logger = logging.getLogger(__name__)

# What kind of work the current (task) context is doing. Set once at pipeline
# entry; inherited by asyncio tasks / to_thread calls spawned from there.
_usage_kind: ContextVar[str] = ContextVar("llm_usage_kind", default="other")

KIND_QUERY = "query"
KIND_PROCESSING = "processing"

_FLUSH_INTERVAL_SECONDS = 2.0

_lock = threading.Lock()
_pending: dict = {}  # kind -> completions recorded but not yet flushed
_flush_event = threading.Event()
_flusher_started = False
_neo4j_getter = None  # registered from the app lifespan to avoid import cycles


def configure(neo4j_getter) -> None:
    """Register the Neo4j service getter (called once at app startup)."""
    global _neo4j_getter
    _neo4j_getter = neo4j_getter


def set_usage_kind(kind: str):
    """Stamp the current context's usage kind. Returns the contextvars Token."""
    return _usage_kind.set(kind)


def get_usage_kind() -> str:
    return _usage_kind.get()


def record_completion(n: int = 1, kind: str = None) -> None:
    """Record ``n`` successful LLM completions. Never raises."""
    try:
        k = kind or _usage_kind.get()
        with _lock:
            _pending[k] = _pending.get(k, 0) + n
        _ensure_flusher()
        _flush_event.set()
    except Exception:  # noqa: BLE001 — metering must never break an LLM call
        logger.debug("usage_meter.record_completion failed", exc_info=True)


def pending_count() -> int:
    """Completions recorded but not yet flushed to Neo4j."""
    with _lock:
        return sum(_pending.values())


def get_completions_this_month() -> dict:
    """Persisted + pending completion counts for the current UTC month.

    Returns {"total": int, "query": int, "processing": int}. Synchronous
    (Neo4j read) — call via ``asyncio.to_thread`` on request paths.
    """
    counts = {"total": 0, "query": 0, "processing": 0}
    if _neo4j_getter is not None:
        try:
            stored = _neo4j_getter().get_llm_completion_count_this_month()
            counts.update(stored)
        except Exception:  # noqa: BLE001
            logger.warning("usage_meter: reading persisted counts failed", exc_info=True)
    with _lock:
        for k, n in _pending.items():
            counts["total"] += n
            if k in counts:
                counts[k] += n
    return counts


def flush_now() -> None:
    """Synchronously flush pending counts (app shutdown / tests)."""
    _flush()


def _ensure_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    with _lock:
        if _flusher_started:
            return
        t = threading.Thread(target=_flusher_loop, name="llm-usage-flusher", daemon=True)
        _flusher_started = True
    t.start()


def _flusher_loop() -> None:
    while True:
        _flush_event.wait()
        _flush_event.clear()
        # Coalesce bursts (graph builds fire many completions per second).
        time.sleep(_FLUSH_INTERVAL_SECONDS)
        _flush()


def _flush() -> None:
    if _neo4j_getter is None:
        return
    with _lock:
        if not _pending:
            return
        snapshot = dict(_pending)
        _pending.clear()
    try:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        _neo4j_getter().increment_llm_completions(date_str, snapshot)
    except Exception:  # noqa: BLE001 — put the counts back for the next attempt
        logger.warning("usage_meter: flush to Neo4j failed; retrying later", exc_info=True)
        with _lock:
            for k, n in snapshot.items():
                _pending[k] = _pending.get(k, 0) + n
        _flush_event.set()


def _reset_for_tests() -> None:
    """Test-only: clear pending counts and deregister the Neo4j getter."""
    global _neo4j_getter
    with _lock:
        _pending.clear()
    _neo4j_getter = None
