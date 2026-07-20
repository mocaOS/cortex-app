"""Platform "storage" capability — per-app key/value store.

Each app that declares the ``storage`` capability gets its own SQLite file in
its app directory ({apps_dir}/{app_id}/storage.sqlite), so data lives in the
same apps_data volume as the bundle and dies with an uninstall. No cross-app
access is possible by construction: every operation resolves the database
through the app id.

Values are JSON documents (stored serialized). Quotas: a per-value size cap
(APP_STORAGE_MAX_VALUE_KB) and a per-app total cap (APP_STORAGE_MAX_MB).

Used by two consumers with identical semantics:
- the app itself via GET/PUT/DELETE /apps/{id}/api/platform/storage/{key}
- ``store`` steps inside platform tasks (app_task_service)

All methods are synchronous sqlite3 (short single-row transactions) — call
them through asyncio.to_thread from async code.
"""

import json
import logging
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# Keys are path-like identifiers ("transcripts/abc-123", "sync/cursor").
_KEY_RE = re.compile(r"^[A-Za-z0-9._\-/:]{1,512}$")
# Defense-in-depth: every reachable caller already slug-validates app_id via
# app_service, but the db path is derived from it, so re-check here too.
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class AppStorageError(ValueError):
    """A storage operation was rejected (bad key, quota, size)."""


class AppStorageService:
    """SQLite-backed per-app KV store."""

    def __init__(self, apps_dir: Path):
        self._apps_dir = apps_dir
        self._lock = threading.Lock()

    def _db_path(self, app_id: str) -> Path:
        if not _SLUG_RE.match(app_id or ""):
            raise AppStorageError(f"invalid app id {app_id!r}")
        app_dir = self._apps_dir / app_id
        if not app_dir.is_dir():
            raise AppStorageError(f"app '{app_id}' is not installed")
        return app_dir / "storage.sqlite"

    def _connect(self, app_id: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path(app_id), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            " key TEXT PRIMARY KEY,"
            " value TEXT NOT NULL,"
            " size INTEGER NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        return conn

    @staticmethod
    def _check_key(key: str) -> str:
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise AppStorageError(
                f"invalid storage key {key!r} (allowed: letters, digits, . _ - / :, max 512 chars)"
            )
        return key

    def get(self, app_id: str, key: str) -> Optional[Any]:
        """Return the deserialized value, or None if absent."""
        self._check_key(key)
        with closing(self._connect(app_id)) as conn, conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return row[0]

    def exists(self, app_id: str, key: str) -> bool:
        self._check_key(key)
        with closing(self._connect(app_id)) as conn, conn:
            return (
                conn.execute("SELECT 1 FROM kv WHERE key = ?", (key,)).fetchone()
                is not None
            )

    def put(self, app_id: str, key: str, value: Any) -> None:
        """Store a JSON-serializable value; enforces value + quota caps."""
        self._check_key(key)
        settings = get_settings()
        try:
            serialized = json.dumps(value, default=str)
        except (TypeError, ValueError) as e:
            raise AppStorageError(f"value is not JSON-serializable: {e}")
        size = len(serialized.encode())
        if size > settings.app_storage_max_value_kb * 1024:
            raise AppStorageError(
                f"value is {size // 1024} KB — exceeds the "
                f"{settings.app_storage_max_value_kb} KB per-value cap"
            )
        quota = settings.app_storage_max_mb * 1024 * 1024
        with self._lock, closing(self._connect(app_id)) as conn, conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(size), 0) FROM kv WHERE key != ?", (key,)
            ).fetchone()
            if row[0] + size > quota:
                raise AppStorageError(
                    f"storage quota exceeded ({settings.app_storage_max_mb} MB per app)"
                )
            conn.execute(
                "INSERT INTO kv (key, value, size, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "size = excluded.size, updated_at = excluded.updated_at",
                (key, serialized, size, datetime.now(timezone.utc).isoformat()),
            )

    def delete(self, app_id: str, key: str) -> bool:
        self._check_key(key)
        with closing(self._connect(app_id)) as conn, conn:
            cur = conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            return cur.rowcount > 0

    def list_keys(
        self, app_id: str, prefix: str = "", limit: int = 100, after: str = ""
    ) -> Dict[str, Any]:
        """Key listing (metadata only) with prefix filter + keyset pagination."""
        limit = max(1, min(int(limit or 100), 500))
        with closing(self._connect(app_id)) as conn, conn:
            rows = conn.execute(
                "SELECT key, size, updated_at FROM kv "
                "WHERE key LIKE ? ESCAPE '\\' AND key > ? ORDER BY key LIMIT ?",
                (f"{_escape_like(prefix)}%", after, limit + 1),
            ).fetchall()
        keys: List[dict] = [
            {"key": r[0], "size": r[1], "updated_at": r[2]} for r in rows[:limit]
        ]
        return {
            "keys": keys,
            "next": keys[-1]["key"] if len(rows) > limit else None,
        }

    def missing(self, app_id: str, keys: List[str]) -> List[str]:
        """Of the given keys, return those NOT present (dedup primitive)."""
        checked = [self._check_key(k) for k in keys]
        if not checked:
            return []
        found = set()
        with closing(self._connect(app_id)) as conn, conn:
            for chunk_start in range(0, len(checked), 500):
                chunk = checked[chunk_start : chunk_start + 500]
                placeholders = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT key FROM kv WHERE key IN ({placeholders})", chunk
                ):
                    found.add(row[0])
        return [k for k in checked if k not in found]


def _escape_like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


_storage_service: Optional[AppStorageService] = None


def get_app_storage_service() -> AppStorageService:
    global _storage_service
    if _storage_service is None:
        from app.services.app_service import get_app_service

        _storage_service = AppStorageService(get_app_service()._apps_dir)
    return _storage_service
