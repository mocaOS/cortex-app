"""Append-only JSONL audit trail (ENABLE_AUDIT_LOG).

Records security-relevant events for compliance review: authentication
failures, API-key-attributed mutating requests (uploads, deletions,
configuration changes, key CRUD), and search/ask activity. One JSON object
per line:

    {"ts": "...", "event": "api.request", "actor": "key_abc", "outcome": "ok",
     "request_id": "...", "meta": {"method": "POST", "path": "/api/upload", ...}}

Design constraints:
- Fail-open: an audit-write failure must never break the request. Failures
  are logged (rate-limited) and dropped.
- Privacy-consistent: no document content, no query text — only event
  metadata. This matches the Langfuse content-masking stance: operational
  visibility without content exfiltration.
- Single-process appends guarded by a lock; size-based rotation keeps the
  file bounded (audit.log -> audit.log.1, one generation).
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.logging_setup import get_request_id

logger = logging.getLogger(__name__)

_ROTATE_BYTES = 50 * 1024 * 1024


class AuditLogger:
    def __init__(self):
        self._lock = threading.Lock()
        self._fh = None
        self._path: Optional[str] = None
        self._write_failures = 0

    def _ensure_open(self, path: str):
        if self._fh is not None and self._path == path:
            return
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._path = path

    def _rotate_if_needed(self, path: str):
        try:
            if self._fh is not None and self._fh.tell() >= _ROTATE_BYTES:
                self._fh.close()
                self._fh = None
                os.replace(path, path + ".1")
        except OSError:
            pass

    def record(self, event: str, *, actor: Optional[str] = None,
               outcome: str = "ok", **meta) -> None:
        """Append one audit event. No-op unless ENABLE_AUDIT_LOG=true; never raises."""
        settings = get_settings()
        if not settings.enable_audit_log:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor": actor or "-",
            "outcome": outcome,
            "request_id": get_request_id() or "-",
        }
        if meta:
            entry["meta"] = meta
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str)
            with self._lock:
                path = settings.audit_log_path
                self._ensure_open(path)
                self._fh.write(line + "\n")
                self._rotate_if_needed(path)
        except Exception as e:
            self._write_failures += 1
            if self._write_failures <= 3 or self._write_failures % 1000 == 0:
                logger.warning(f"Audit log write failed ({self._write_failures}x): {e}")

    def close(self):
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
                self._path = None


_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def audit(event: str, *, actor: Optional[str] = None,
          outcome: str = "ok", **meta) -> None:
    """Module-level convenience wrapper around the singleton."""
    get_audit_logger().record(event, actor=actor, outcome=outcome, **meta)
