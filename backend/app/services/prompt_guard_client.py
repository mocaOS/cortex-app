"""Async client for the shared prompt-guard classifier (cortex-helper /classify).

The query-time prompt-injection/jailbreak gate. Mirrors helper_client's transport
(shared AsyncClient, bounded retries, per-op circuit breaker, X-Helper-Token /
X-Tenant-ID / X-Request-ID headers) and rerank's **fail-open** policy: a down or
unconfigured helper never raises into the ask path — it returns None and asks
proceed unguarded (availability > strictness by default).

Enablement is URL-gated: when ``prompt_guard_service_url`` is empty the guard is a
no-op, so self-hosters without cortex-helper are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import threading
import time
from typing import List, Optional

import httpx

from app.config import get_settings
from app.services.helper_client import CircuitBreaker, _is_retryable, _record

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 2
_BACKOFF_BASE_SECONDS = 0.25
_BACKOFF_MAX_SECONDS = 2.0

_guard_breaker = CircuitBreaker("classify")

_async_client: Optional[httpx.AsyncClient] = None
_async_client_lock = threading.Lock()


def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        with _async_client_lock:
            if _async_client is None:
                # Guard sits on the critical path before retrieval — keep the
                # timeout tight so a stalled helper can't hold the ask open.
                _async_client = httpx.AsyncClient(timeout=15.0)
    return _async_client


async def close_async_client() -> None:
    """Close the shared client (called from the FastAPI lifespan cleanup)."""
    global _async_client
    if _async_client is not None:
        try:
            await _async_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _async_client = None


def _headers() -> dict:
    settings = get_settings()
    headers: dict = {}
    if settings.helper_service_token:
        headers["X-Helper-Token"] = settings.helper_service_token
    instance = getattr(settings, "instance_id", "") or socket.gethostname()
    headers["X-Tenant-ID"] = instance
    try:
        from app.logging_setup import get_request_id

        request_id = get_request_id()
    except Exception:  # noqa: BLE001
        request_id = None
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _backoff(attempt: int) -> float:
    raw = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt))
    return raw * (0.5 + random.random() / 2)  # jitter: 50-100% of raw


async def classify(texts: List[str]) -> Optional[List[dict]]:
    """Classify texts via the helper's /classify endpoint.

    Returns the per-text result dicts ({label, score, flagged}) in input order,
    or ``None`` on any failure / when the guard is unconfigured (fail-open — the
    caller must treat None as "not blocked").
    """
    settings = get_settings()
    base = (settings.prompt_guard_service_url or "").rstrip("/")
    if not base:
        return None  # guard disabled — no service configured
    if not texts:
        return []
    if not _guard_breaker.allow():
        logger.debug("Prompt-guard circuit open — skipping classify")
        _record("classify", "circuit_open")
        return None

    url = base + "/classify"
    payload = {"texts": texts, "threshold": settings.prompt_guard_threshold}
    client = _get_async_client()
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = await client.post(url, json=payload, headers=_headers())
            resp.raise_for_status()
            _guard_breaker.record_success()
            _record("classify", "ok")
            return resp.json().get("results", [])
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc):
                break
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_backoff(attempt))

    _guard_breaker.record_failure()
    _record("classify", "unavailable")
    logger.warning(f"Prompt-guard classify failed ({url}); failing open: {last_exc}")
    return None


def _backend_configured(settings) -> bool:
    """A guard backend exists: a remote helper URL, or the in-process fallback."""
    return bool(settings.prompt_guard_service_url or "") or bool(
        settings.prompt_guard_local
    )


async def _classify_dispatch(texts, settings):
    """Route to the remote helper when a URL is set, else the in-process model
    (if prompt_guard_local is on). Returns results, or None (fail-open)."""
    if settings.prompt_guard_service_url:
        return await classify(texts)
    if settings.prompt_guard_local:
        from app.services import prompt_guard_local

        return await asyncio.to_thread(
            prompt_guard_local.classify, texts, settings.prompt_guard_threshold
        )
    return None


async def _guard_enabled(settings, neo4j) -> bool:
    """Effective toggle: env default overridable via the SystemMeta runtime
    setting, AND a guard backend (remote URL or local model) must be configured."""
    if not _backend_configured(settings):
        return False
    if neo4j is None:
        return bool(settings.prompt_guard)
    try:
        return await asyncio.to_thread(
            neo4j.get_runtime_setting, "prompt_guard", settings.prompt_guard
        )
    except Exception:  # noqa: BLE001 — never let a settings read break the ask
        return bool(settings.prompt_guard)


async def guard_user_question(question: str, settings=None, neo4j=None):
    """Query-time prompt-injection gate.

    Returns ``(blocked: bool, reason: str | None)``. Blocks only when the guard
    is enabled, the helper is reachable, and the classifier flags the question.
    Fails open (``(False, None)``) when the guard is off, unconfigured, or the
    helper is unreachable. On any *real* classify call it records one query-unit
    (usage_meter) and one Langfuse generation, so the cost/trace reflect the
    extra security query.
    """
    if settings is None:
        settings = get_settings()
    if not question or not question.strip():
        return (False, None)
    if not await _guard_enabled(settings, neo4j):
        return (False, None)

    results = await _classify_dispatch([question], settings)
    if not results:
        # None (service down / model unavailable) or empty → fail-open.
        return (False, None)

    result = results[0]
    # Real call happened → meter + trace regardless of the verdict.
    try:
        from app.services import usage_meter

        usage_meter.record_completion(kind=usage_meter.KIND_QUERY)
    except Exception:  # noqa: BLE001
        logger.debug("prompt_guard usage metering failed", exc_info=True)
    try:
        from app.services.observability import record_generation

        record_generation(
            name="prompt_guard.classify",
            model=settings.prompt_guard_model,
            usage=None,
            input=question,
            output=result,
            metadata={"stage": "prompt_guard"},
        )
    except Exception:  # noqa: BLE001
        logger.debug("prompt_guard tracing failed", exc_info=True)

    if result.get("flagged"):
        reason = (
            f"prompt_guard flagged (label={result.get('label')}, "
            f"score={result.get('score'):.3f})"
        )
        return (True, reason)
    return (False, None)
