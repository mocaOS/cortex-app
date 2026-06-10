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
