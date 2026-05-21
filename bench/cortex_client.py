"""Async HTTP client wrapping the Cortex FastAPI endpoints used by the benchmark.

Thin layer over httpx — no business logic, just typed methods for the endpoints
the orchestrator drives. Returns parsed JSON dicts; raises CortexError on non-2xx.

All methods authenticate with the admin API key passed into the constructor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import httpx


class CortexError(RuntimeError):
    """Raised when a Cortex API call returns a non-2xx response."""


class CortexClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        admin_key: str = "",
        timeout: float = 600.0,
    ) -> None:
        if not admin_key:
            raise ValueError("CortexClient requires a non-empty admin_key")
        self.base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": admin_key}
        self._client = httpx.AsyncClient(timeout=timeout, headers=self._headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CortexClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ----- health / readiness -----------------------------------------------

    async def health(self) -> dict:
        r = await self._client.get(f"{self.base_url}/health")
        if r.status_code != 200:
            raise CortexError(f"GET /health → {r.status_code}: {r.text[:200]}")
        return r.json()

    async def wait_until_ready(self, timeout_s: float = 90.0, poll_s: float = 2.0) -> dict:
        """Poll /health until status=='healthy' and neo4j_connected. Raises on timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        last: dict = {}
        while asyncio.get_event_loop().time() < deadline:
            try:
                last = await self.health()
                if last.get("status") == "healthy" and last.get("neo4j_connected"):
                    return last
            except Exception:
                pass
            await asyncio.sleep(poll_s)
        raise CortexError(f"Backend not healthy after {timeout_s}s. Last response: {last!r}")

    # ----- reset ------------------------------------------------------------

    async def reset(self, *, delete_api_keys: bool = False) -> dict:
        """Wipe documents, entities, relationships, communities, etc."""
        body = {
            "delete_documents": True,
            "delete_uploaded_files": True,
            "delete_custom_inputs": True,
            "delete_collections": True,
            "delete_api_keys": delete_api_keys,
        }
        r = await self._client.post(f"{self.base_url}/api/admin/reset", json=body)
        if r.status_code != 200:
            raise CortexError(f"POST /api/admin/reset → {r.status_code}: {r.text[:200]}")
        return r.json()

    # ----- uploads ----------------------------------------------------------

    async def upload(
        self,
        file_path: Path,
        *,
        collection_id: Optional[str] = None,
        start_processing: bool = False,
    ) -> dict:
        params = {"start_processing": str(start_processing).lower()}
        if collection_id:
            params["collection_id"] = collection_id
        with file_path.open("rb") as f:
            files = {"file": (file_path.name, f, "text/markdown")}
            r = await self._client.post(
                f"{self.base_url}/api/upload",
                params=params,
                files=files,
            )
        if r.status_code not in (200, 201):
            raise CortexError(
                f"POST /api/upload {file_path.name} → {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def upload_all(
        self, file_paths: list[Path], *, collection_id: Optional[str] = None
    ) -> list[dict]:
        """Sequential upload — backend has its own concurrency on processing."""
        results = []
        for fp in file_paths:
            results.append(
                await self.upload(fp, collection_id=collection_id, start_processing=False)
            )
        return results

    # ----- phase A trigger + wait -------------------------------------------

    async def trigger_phase_a(self, *, concurrency: Optional[int] = None) -> dict:
        params = {}
        if concurrency is not None:
            params["concurrency"] = concurrency
        r = await self._client.post(
            f"{self.base_url}/api/documents/process-pending", params=params
        )
        if r.status_code not in (200, 202):
            raise CortexError(
                f"POST /api/documents/process-pending → {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def wait_phase_a(
        self, expected_docs: int, *, poll_s: float = 5.0, timeout_s: float = 1500.0
    ) -> dict:
        """Wait for all uploaded docs to leave PENDING/PROCESSING. Returns final stats."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            stats = await self.stats()
            if (
                stats.get("pending_count", 0) == 0
                and stats.get("processing_count", 0) == 0
                and stats.get("completed_count", 0) >= expected_docs
            ):
                return stats
            await asyncio.sleep(poll_s)
        raise CortexError(
            f"Phase A did not finish in {timeout_s}s. Last stats: pending="
            f"{stats.get('pending_count')}, processing={stats.get('processing_count')}, "
            f"completed={stats.get('completed_count')}"
        )

    # ----- phase B (relationship analysis) ----------------------------------

    async def trigger_phase_b(
        self, *, rebuild: bool = False, scope: str = "full"
    ) -> dict:
        r = await self._client.post(
            f"{self.base_url}/api/graph/relationships/analyze",
            params={"rebuild": str(rebuild).lower(), "scope": scope},
        )
        if r.status_code not in (200, 202):
            raise CortexError(
                f"POST /api/graph/relationships/analyze → {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def wait_task(
        self, task_id: str, *, poll_s: float = 5.0, timeout_s: float = 1500.0
    ) -> dict:
        """Poll /api/tasks/{id} until completed or failed. Returns final task dict."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        last: dict = {}
        while asyncio.get_event_loop().time() < deadline:
            r = await self._client.get(f"{self.base_url}/api/tasks/{task_id}")
            if r.status_code == 404:
                # Task may have been cleaned up after completion; treat as done.
                return {"task_id": task_id, "status": "completed", "missing": True}
            if r.status_code != 200:
                raise CortexError(
                    f"GET /api/tasks/{task_id} → {r.status_code}: {r.text[:200]}"
                )
            last = r.json()
            status = last.get("status")
            if status in ("completed", "failed", "cancelled"):
                return last
            await asyncio.sleep(poll_s)
        raise CortexError(
            f"Task {task_id} did not finish in {timeout_s}s. Last status: {last.get('status')}"
        )

    # ----- step 3 (community detection) -------------------------------------

    async def trigger_step_3(self, *, min_size: int = 3) -> dict:
        r = await self._client.post(
            f"{self.base_url}/api/graph/communities/detect",
            params={"min_size": min_size},
        )
        if r.status_code not in (200, 202):
            raise CortexError(
                f"POST /api/graph/communities/detect → {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    # ----- stats ------------------------------------------------------------

    async def stats(self) -> dict:
        r = await self._client.get(f"{self.base_url}/api/stats")
        if r.status_code != 200:
            raise CortexError(f"GET /api/stats → {r.status_code}: {r.text[:200]}")
        return r.json()

    # ----- library export (used for pre-batch safety backup) ----------------

    async def trigger_export(self) -> dict:
        """POST /api/admin/export → starts an async export task.

        Returns the task descriptor dict (task_id, status='pending', ...).
        """
        r = await self._client.post(f"{self.base_url}/api/admin/export")
        if r.status_code not in (200, 202):
            raise CortexError(
                f"POST /api/admin/export → {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def download_export(self, task_id: str, out_path: Path) -> int:
        """Stream GET /api/admin/export/{task_id}/download to a local file.

        Returns the number of bytes written. The task must be `status=completed`
        before this is called; the server returns 4xx/5xx otherwise.
        """
        url = f"{self.base_url}/api/admin/export/{task_id}/download"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        bytes_written = 0
        async with self._client.stream("GET", url) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise CortexError(
                    f"GET /api/admin/export/{task_id}/download → "
                    f"{r.status_code}: {body[:200]!r}"
                )
            with out_path.open("wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    bytes_written += len(chunk)
        return bytes_written

    async def export_library_to_zip(
        self, out_path: Path, *, poll_s: float = 5.0, timeout_s: float = 900.0
    ) -> dict:
        """End-to-end: trigger export → wait_task → download_export.

        Returns the task result dict (file_size, stats, etc.) merged with a
        `local_path` field pointing at `out_path`.
        """
        triggered = await self.trigger_export()
        task_id = triggered.get("task_id")
        if not task_id:
            raise CortexError(
                f"Export endpoint did not return a task_id: {triggered!r}"
            )
        final = await self.wait_task(task_id, poll_s=poll_s, timeout_s=timeout_s)
        if final.get("status") != "completed":
            raise CortexError(
                f"Export task {task_id} ended in status={final.get('status')!r}: "
                f"{final.get('error') or final!r}"
            )
        size = await self.download_export(task_id, out_path)
        result = dict(final.get("result") or {})
        result["local_path"] = str(out_path)
        result.setdefault("file_size", size)
        return result
