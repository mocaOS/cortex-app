"""HTTP client for the shared per-host model service (cortex-helper).

Centralizes every call to the helper (docling /convert, reranker /rerank):
- connection reuse (one shared AsyncClient instead of per-call construction)
- bounded retries with exponential backoff + jitter on transient failures
  (connect errors, timeouts, 5xx) — a network blip no longer instantly
  degrades a tenant to its local fallback path
- a small in-process circuit breaker per operation so a *down* helper fails
  fast instead of paying the full retry ladder on every request
- auth (X-Helper-Token) and tenant identification (X-Tenant-ID) headers

The caller decides what a final failure means (local fallback vs strict
failure via HELPER_STRICT_REMOTE) — this module only does transport.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import threading
import time
from pathlib import Path
from typing import List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 4.0


class HelperUnavailableError(RuntimeError):
    """The helper service could not serve the request (after retries /
    while the circuit is open)."""


class CircuitBreaker:
    """Minimal thread-safe circuit breaker.

    closed → open after `failure_threshold` consecutive failures;
    half-open (one probe allowed) after `recovery_seconds`;
    success closes it again.
    """

    def __init__(self, name: str, failure_threshold: int = 5, recovery_seconds: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if time.monotonic() - self._opened_at >= self.recovery_seconds:
                return "half-open"
            return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            if self._opened_at is not None:
                logger.info(f"Helper circuit '{self.name}' closed again")
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if (
                self._consecutive_failures >= self.failure_threshold
                and self._opened_at is None
            ):
                self._opened_at = time.monotonic()
                logger.warning(
                    f"Helper circuit '{self.name}' OPEN after "
                    f"{self._consecutive_failures} consecutive failures — "
                    f"failing fast for {self.recovery_seconds}s"
                )
            elif self._opened_at is not None:
                # failed half-open probe → re-open the window
                self._opened_at = time.monotonic()


_convert_breaker = CircuitBreaker("convert")
_rerank_breaker = CircuitBreaker("rerank")

_async_client: Optional[httpx.AsyncClient] = None
_async_client_lock = threading.Lock()


def get_breaker_states() -> dict:
    """Expose breaker states (metrics/debugging)."""
    return {"convert": _convert_breaker.state, "rerank": _rerank_breaker.state}


def _record(op: str, outcome: str) -> None:
    try:
        from app.metrics import HELPER_REQUESTS

        HELPER_REQUESTS.labels(op=op, outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def _headers() -> dict:
    settings = get_settings()
    headers = {}
    if settings.helper_service_token:
        headers["X-Helper-Token"] = settings.helper_service_token
    instance = getattr(settings, "instance_id", "") or socket.gethostname()
    headers["X-Tenant-ID"] = instance
    request_id = _current_request_id()
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _current_request_id() -> Optional[str]:
    """Propagate the inbound request id when the logging middleware set one."""
    try:
        from app.logging_setup import get_request_id

        return get_request_id()
    except Exception:
        return None


def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        with _async_client_lock:
            if _async_client is None:
                _async_client = httpx.AsyncClient(timeout=600.0)
    return _async_client


async def close_async_client() -> None:
    """Close the shared client (called from the FastAPI lifespan cleanup)."""
    global _async_client
    if _async_client is not None:
        try:
            await _async_client.aclose()
        except Exception:
            pass
        _async_client = None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                        httpx.ReadTimeout, httpx.WriteTimeout,
                        httpx.PoolTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 503
    return False


def _backoff(attempt: int) -> float:
    raw = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt))
    return raw * (0.5 + random.random() / 2)  # jitter: 50-100% of raw


async def convert_document(file_path: str, use_vision: bool) -> dict:
    """Convert a document via the helper's /convert endpoint.

    Retries transient failures; raises HelperUnavailableError when the
    circuit is open or all attempts fail. 4xx responses are NOT retried
    (they are the caller's problem, e.g. an oversized file).
    """
    settings = get_settings()
    url = settings.docling_service_url.rstrip("/") + "/convert"
    if not _convert_breaker.allow():
        raise HelperUnavailableError(
            "docling service circuit is open (recent consecutive failures)"
        )

    client = _get_async_client()
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            with open(file_path, "rb") as fh:
                resp = await client.post(
                    url,
                    files={"file": (Path(file_path).name, fh)},
                    data={"use_vision": str(use_vision).lower()},
                    headers=_headers(),
                )
            resp.raise_for_status()
            result = resp.json()
            if result.get("error"):
                # Service processed the file but conversion failed — a real
                # document problem, not a transport problem. Don't retry,
                # don't trip the breaker.
                raise RuntimeError(f"Docling service error: {result['error']}")
            _convert_breaker.record_success()
            _record("convert", "ok")
            return result
        except RuntimeError:
            _convert_breaker.record_success()  # service itself is healthy
            _record("convert", "document_error")
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc):
                _convert_breaker.record_failure()
                raise
            logger.warning(
                f"Helper convert attempt {attempt + 1}/{_RETRY_ATTEMPTS} "
                f"failed ({exc}); retrying"
            )
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_backoff(attempt))

    _convert_breaker.record_failure()
    _record("convert", "unavailable")
    raise HelperUnavailableError(
        f"docling service unreachable after {_RETRY_ATTEMPTS} attempts: {last_exc}"
    )


def rerank(query: str, passages: List[str]) -> Optional[List[float]]:
    """Score passages via the helper's /rerank endpoint (sync — runs inside
    the rerank executor thread).

    Returns None on final failure: rerank degradation is safe (callers keep
    the original order), so unlike convert this never raises.
    """
    settings = get_settings()
    url = settings.reranker_service_url.rstrip("/") + "/rerank"
    if not _rerank_breaker.allow():
        logger.debug("Rerank circuit open — skipping remote rerank")
        return None

    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = httpx.post(
                url,
                json={"query": query, "passages": passages},
                headers=_headers(),
                timeout=30.0,
            )
            resp.raise_for_status()
            _rerank_breaker.record_success()
            _record("rerank", "ok")
            return resp.json()["scores"]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc):
                break
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_backoff(attempt))

    _rerank_breaker.record_failure()
    _record("rerank", "unavailable")
    logger.warning(
        f"Remote rerank failed ({url}); falling back to no rerank: {last_exc}"
    )
    return None
