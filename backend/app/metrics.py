"""Prometheus metrics (dependency-light, degrade-to-noop).

Uses `prometheus-client` (pure Python, <1MB) when installed; if the package
is missing — e.g. an older image that hasn't rebuilt — every metric becomes a
no-op and the /metrics endpoint reports 501, so this module can never break a
deployment.

Cardinality discipline: HTTP metrics label by ROUTE TEMPLATE (e.g.
`/api/documents/{doc_id}`), never raw paths.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    AVAILABLE = False

    class _Noop:
        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            pass

        def dec(self, *a, **k):
            pass

        def observe(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

    def Counter(*a, **k):  # noqa: N802
        return _Noop()

    def Gauge(*a, **k):  # noqa: N802
        return _Noop()

    def Histogram(*a, **k):  # noqa: N802
        return _Noop()

    CONTENT_TYPE_LATEST = "text/plain"

    def generate_latest():  # noqa: N802
        return b""


HTTP_REQUESTS = Counter(
    "cortex_http_requests_total",
    "HTTP requests by route template, method and status class",
    ["route", "method", "status"],
)
HTTP_DURATION = Histogram(
    "cortex_http_request_duration_seconds",
    "HTTP request latency by route template",
    ["route"],
    buckets=(0.05, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
SSE_ACTIVE_STREAMS = Gauge(
    "cortex_sse_active_streams",
    "SSE streams currently open",
)
DOCUMENTS_PROCESSED = Counter(
    "cortex_documents_processed_total",
    "Documents that finished processing, by outcome",
    ["status"],
)
CONVERSION_SECONDS = Histogram(
    "cortex_document_conversion_seconds",
    "Document conversion duration by path (remote helper vs local subprocess)",
    ["path"],
    buckets=(0.1, 0.5, 1, 5, 15, 60, 180, 600),
)
HELPER_REQUESTS = Counter(
    "cortex_helper_requests_total",
    "Calls to the shared helper service, by operation and outcome",
    ["op", "outcome"],
)
HELPER_BREAKER_OPEN = Gauge(
    "cortex_helper_breaker_open",
    "1 when the helper circuit breaker is open, by operation",
    ["op"],
)
RATE_LIMITED = Counter(
    "cortex_rate_limited_total",
    "Requests rejected by the per-key rate limiter",
    ["route"],
)
DISK_FREE_BYTES = Gauge(
    "cortex_disk_free_bytes",
    "Free bytes on the filesystem backing a data directory",
    ["dir"],
)
DISK_TOTAL_BYTES = Gauge(
    "cortex_disk_total_bytes",
    "Total bytes on the filesystem backing a data directory",
    ["dir"],
)
UPLOADS_REJECTED_DISK = Counter(
    "cortex_uploads_rejected_disk_total",
    "Uploads/imports rejected by the free-disk-space guard",
)


def _refresh_disk_gauges() -> None:
    """Set disk gauges at scrape time. Disk-full corrupts Neo4j checkpoints —
    this is the operator's early-warning signal (alert on it fleet-side)."""
    import shutil

    from app.config import get_settings

    settings = get_settings()
    for name, path in (
        ("uploads", settings.upload_dir),
        ("custom_inputs", settings.custom_inputs_dir),
    ):
        try:
            usage = shutil.disk_usage(path)
            DISK_FREE_BYTES.labels(dir=name).set(usage.free)
            DISK_TOTAL_BYTES.labels(dir=name).set(usage.total)
        except OSError:
            pass


def render() -> tuple[bytes, str]:
    """(payload, content_type) for the /metrics endpoint."""
    if AVAILABLE:
        try:
            _refresh_disk_gauges()
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.services.helper_client import get_breaker_states

            for op, state in get_breaker_states().items():
                HELPER_BREAKER_OPEN.labels(op=op).set(1 if state == "open" else 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.services.crawl_client import get_breaker_state

            HELPER_BREAKER_OPEN.labels(op="crawl").set(
                1 if get_breaker_state() == "open" else 0
            )
        except Exception:  # noqa: BLE001
            pass
    return generate_latest(), CONTENT_TYPE_LATEST
