"""Logging configuration: plain (legacy) or JSON output + request IDs.

`LOG_FORMAT=plain` (default) keeps the exact format the app has always
emitted, so existing log scraping keeps working. `LOG_FORMAT=json` switches
every record to one JSON object per line with a `request_id` field — the
ingredient log aggregators need to correlate a request across services
(cortex-app → cortex-helper forwards the same X-Request-ID).

The 500+ existing logger call sites are untouched; only the root handler's
formatter changes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

_PLAIN_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: Optional[str]) -> None:
    _request_id.set(value)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id (or '-')."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# Per-key state for rate_limited_warning: key -> (last_emit_monotonic, suppressed_count).
# Dict writes are atomic under the GIL; callers span the event loop and worker
# threads, and an occasional lost increment of the suppressed counter is fine.
_warn_state: dict[str, tuple[float, int]] = {}


def rate_limited_warning(
    log: logging.Logger, key: str, message: str, min_interval_s: float = 300.0
) -> None:
    """Emit a warning at most once per `min_interval_s` per key.

    Background loops that tick every few seconds (task persistence, usage-meter
    flush, schedulers) would otherwise emit tens of identical warnings per
    minute for the whole duration of a Neo4j outage. Suppressed repeats are
    counted and reported with the next emitted warning.
    """
    now = time.monotonic()
    # -inf sentinel: monotonic time is host uptime, which can be < min_interval_s
    # shortly after boot — a 0.0 default would swallow a key's first warning there.
    last, suppressed = _warn_state.get(key, (float("-inf"), 0))
    if now - last >= min_interval_s:
        if suppressed:
            message = f"{message} [{suppressed} similar warning(s) suppressed]"
        log.warning(message)
        _warn_state[key] = (now, 0)
    else:
        _warn_state[key] = (last, suppressed + 1)


def configure(log_format: str = "plain", level: int = logging.INFO) -> None:
    """Install the root handler. Idempotent (replaces prior handlers)."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if (log_format or "plain").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_PLAIN_FORMAT))
    root.addHandler(handler)
