"""HTTP client for the shared crawl4ai service — "MDHarvest powered by Crawl4ai".

cortex-app never embeds a browser/crawler stack. It speaks crawl4ai's native
REST API over HTTP:
  - POST /md     -> clean markdown for one URL (the content harvest path)
  - POST /crawl  -> structured result incl. links (the link-discovery path)

One code path, two deployments — only env differs:
  - self-host : CRAWL_SERVICE_URL -> the user's own crawl4ai (:11235)
  - cloud     : CRAWL_SERVICE_URL -> the shared per-host crawl4ai (set by the
                AaaS operator; one container per server, many tenant stacks)
Empty URL => the feature is off (no in-process fallback by design).

Privacy (cloud, multi-tenant): cross-customer isolation is enforced where the
operator controls it — crawl4ai is run cache-disabled, with ephemeral storage,
on an internal network only. This client adds defense-in-depth by requesting
cache-bypass per call (c="0") and using ONLY the synchronous /md + /crawl
endpoints, never the addressable async /crawl/job API — so a crawl leaves
nothing retained or queryable by another tenant.

Transport discipline mirrors helper_client.py: one shared AsyncClient, bounded
retries with backoff+jitter, and a per-process circuit breaker so a down
crawl4ai fails fast instead of paying the full retry ladder on every request.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import socket
import threading
import time
from typing import List, Optional
from urllib.parse import urlparse, urldefrag, urljoin

import httpx

from app.config import get_settings
from app.services.helper_client import CircuitBreaker

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 4.0

_crawl_breaker = CircuitBreaker("crawl")

_async_client: Optional[httpx.AsyncClient] = None
_async_client_lock = threading.Lock()

# Link-discovery noise filters (ported from mdharvest): never surface these as
# crawlable candidates.
_SKIP_PATTERNS = (
    "/login", "/signin", "/signup", "/register", "/logout",
    "/cart", "/checkout", "/account", "/privacy", "/terms",
    "/cookie", "/subscribe", "mailto:", "tel:", "javascript:",
)
_SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".json", ".xml", ".pdf", ".zip", ".mp4", ".mp3",
)


class CrawlUnavailableError(RuntimeError):
    """The crawl4ai service could not serve the request (circuit open or all
    attempts failed)."""


def get_breaker_state() -> str:
    return _crawl_breaker.state


def _record(op: str, outcome: str) -> None:
    try:
        from app.metrics import HELPER_REQUESTS

        HELPER_REQUESTS.labels(op=op, outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def _headers() -> dict:
    settings = get_settings()
    headers = {}
    if settings.crawl_service_token:
        headers["Authorization"] = f"Bearer {settings.crawl_service_token}"
    instance = getattr(settings, "instance_id", "") or socket.gethostname()
    headers["X-Tenant-ID"] = instance
    request_id = _current_request_id()
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _current_request_id() -> Optional[str]:
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
                _async_client = httpx.AsyncClient()
    return _async_client


async def close_async_client() -> None:
    """Close the shared client (FastAPI lifespan cleanup)."""
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


def _base_url() -> str:
    url = (get_settings().crawl_service_url or "").rstrip("/")
    if not url:
        raise CrawlUnavailableError("CRAWL_SERVICE_URL is not configured")
    return url


async def _post(path: str, payload: dict, op: str) -> dict:
    """POST to crawl4ai with retries + circuit breaker. Returns parsed JSON."""
    if not _crawl_breaker.allow():
        raise CrawlUnavailableError(
            "crawl4ai circuit is open (recent consecutive failures)"
        )
    url = _base_url() + path
    timeout = float(get_settings().crawl_http_timeout or 60)
    client = _get_async_client()
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = await client.post(
                url, json=payload, headers=_headers(), timeout=timeout
            )
            resp.raise_for_status()
            data = resp.json()
            _crawl_breaker.record_success()
            _record(op, "ok")
            return data
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if not _is_retryable(exc):
                # 4xx — caller's problem (bad URL, auth). Don't trip the breaker.
                _crawl_breaker.record_success()
                _record(op, "client_error")
                raise CrawlUnavailableError(
                    f"crawl4ai returned {exc.response.status_code} for {path}"
                ) from exc
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_backoff(attempt))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable(exc):
                _crawl_breaker.record_failure()
                _record(op, "error")
                raise CrawlUnavailableError(f"crawl4ai call failed: {exc}") from exc
            logger.warning(
                f"crawl4ai {op} attempt {attempt + 1}/{_RETRY_ATTEMPTS} "
                f"failed ({exc}); retrying"
            )
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_backoff(attempt))

    _crawl_breaker.record_failure()
    _record(op, "unavailable")
    raise CrawlUnavailableError(
        f"crawl4ai unreachable after {_RETRY_ATTEMPTS} attempts: {last_exc}"
    )


def _title_from_markdown(markdown: str, url: str) -> str:
    """Best-effort title: first H1, else the URL's last path segment / host."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:200] or _title_from_url(url)
    return _title_from_url(url)


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    tail = (parsed.path or "").rstrip("/").rsplit("/", 1)[-1]
    base = tail or parsed.netloc
    return (base.replace("-", " ").replace("_", " ").strip() or url)[:200]


async def crawl_markdown(url: str, content_filter: Optional[str] = None,
                         query: Optional[str] = None) -> dict:
    """Harvest one URL into clean markdown via crawl4ai's /md endpoint.

    Returns {"url", "title", "markdown"}. Raises CrawlUnavailableError on
    failure (circuit open, network, 4xx, or an unsuccessful crawl).
    """
    settings = get_settings()
    f = content_filter or settings.crawl_content_filter or "fit"
    payload = {"url": url, "f": f, "c": "0"}  # c="0" => cache-bust / fresh
    if query:
        payload["q"] = query
    data = await _post("/md", payload, op="crawl_md")

    if data.get("success") is False:
        raise CrawlUnavailableError(f"crawl4ai could not crawl {url}")
    markdown = data.get("markdown")
    # Newer crawl4ai may nest markdown variants; the /md endpoint returns the
    # selected string, but stay defensive.
    if isinstance(markdown, dict):
        markdown = (
            markdown.get("fit_markdown")
            or markdown.get("raw_markdown")
            or markdown.get("markdown")
            or ""
        )
    markdown = (markdown or "").strip()
    if not markdown:
        raise CrawlUnavailableError(f"crawl4ai returned empty markdown for {url}")
    return {
        "url": url,
        "title": _title_from_markdown(markdown, url),
        "markdown": markdown,
    }


def _normalize_link(href: str, base_url: str) -> Optional[str]:
    """Return a clean same-host http(s) URL, or None to drop it.

    Relative hrefs are resolved against base_url (crawl4ai normally returns
    absolute internal links, but stay robust if it doesn't).
    """
    if not href:
        return None
    href = urljoin(base_url, href.strip())  # resolve relative → absolute
    href, _ = urldefrag(href)  # strip #fragment
    if not href:
        return None
    lower = href.lower()
    if any(p in lower for p in _SKIP_PATTERNS):
        return None
    if any(lower.split("?")[0].endswith(ext) for ext in _SKIP_EXTENSIONS):
        return None
    parsed = urlparse(href)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.netloc != urlparse(base_url).netloc:
        return None  # same-host only
    return href


async def discover_links(url: str) -> dict:
    """Discover same-host candidate links on a page via crawl4ai's /crawl.

    Returns {"source_url", "domain", "links": [{"url", "title"}]}.
    """
    settings = get_settings()
    data = await _post("/crawl", {"urls": [url]}, op="crawl_discover")
    results = data.get("results") or []
    if not results:
        raise CrawlUnavailableError(f"crawl4ai returned no result for {url}")
    result = results[0]
    if result.get("success") is False:
        raise CrawlUnavailableError(
            f"crawl4ai failed to load {url}: {result.get('error_message')}"
        )

    base_host = urlparse(url).netloc
    links_obj = result.get("links") or {}
    internal = links_obj.get("internal") or []

    seen: set = set()
    links: List[dict] = []
    cap = int(settings.crawl_discover_max_links or 200)
    for item in internal:
        href = item.get("href") if isinstance(item, dict) else item
        normalized = _normalize_link(href, url)
        if not normalized or normalized in seen or normalized == url:
            continue
        seen.add(normalized)
        text = (item.get("text") or item.get("title") or "").strip() if isinstance(item, dict) else ""
        links.append({"url": normalized, "title": (text or _title_from_url(normalized))[:200]})
        if len(links) >= cap:
            break

    return {"source_url": url, "domain": base_host, "links": links}
