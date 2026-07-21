"""Platform "tasks" capability — server-side execution of declarative
step-queues (ECOSYSTEM.md §5.2).

An app submits a validated JSON program (see app_task_dsl); the backend runs
it headless in the existing async loop, so the work survives a closed tab —
and, with a schedule, runs recurringly with no browser at all (the paperless
cron sync). Execution semantics:

- setup steps run once, sequentially; their outputs are readable from every
  item ($setup.<id>).
- items run through a bounded worker pool (task concurrency, additionally
  capped by a global cross-app semaphore) with per-item error isolation: one
  failing item never aborts the run.
- finally steps run once after all items complete (not on pause/cancel) —
  the place for cursor writes.
- pause/resume/cancel are cooperative (checked between steps and items);
  retryFailed re-queues failed items; runNow triggers a scheduled task early.

Persistence is filesystem-only like the rest of the Apps subsystem: one JSON
file per task at {apps_dir}/{app_id}/tasks/{task_id}.json holding the
definition, item statuses, and run history. On boot, tasks that were running
when the process died are re-run (setup re-executes; completed items are not
re-done). A scheduler tick re-runs due scheduled tasks.

Step executors share the SAME security gates as the interactive paths:
- http: allowed_http_hosts + SSRF guard + auth-header injection — one
  implementation (execute_app_http) serves both this engine and the
  POST /apps/{id}/api/platform/http endpoint.
- cortex: endpoint_allowed + the app's minted key via the loopback upstream,
  identical scope to the browser proxy.
- llm: the instance's configured model through the llm_config factory, so
  calls are unit-metered (MAX_QUERIES_PER_MONTH) and Langfuse-traced.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import get_settings
from app.services.app_task_dsl import (
    SkipItem,
    StepError,
    TaskDefinitionError,
    chunk_output_valid,
    eval_condition,
    interpolate,
    output_size_ok,
    resolve_ref,
    resolve_value,
    split_into_chunks,
    validate_task_definition,
)

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
ACTIVE_STATUSES = ("pending", "running", "paused")


class AppHttpError(Exception):
    """A platform http call was rejected or failed; mirrors HTTP semantics so
    the endpoint can map it to an HTTPException and the engine to a StepError."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# Headers an app may never set on outbound platform http calls: credential
# headers are injected exclusively from encrypted config (auth_header
# semantics) and always win on collisions, and hop-by-hop/framing headers
# belong to the client library. Cookie is deliberately NOT forbidden: app
# values can never contain server secrets (secrets are unreachable from
# app-templatable context), and legitimate scraping needs it (e.g. YouTube's
# EU consent bypass SOCS=CAI) — a config-injected Cookie still overrides it.
_FORBIDDEN_APP_HEADERS = {
    "authorization", "proxy-authorization", "host",
    "content-length", "transfer-encoding", "connection",
}


async def execute_app_http(
    app_id: str,
    *,
    method: str,
    url: str,
    body: Optional[str] = None,
    content_type: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    auth_override: Optional[str] = None,
) -> httpx.Response:
    """The single enforcement path for app-originated external HTTP: host
    allowlist (manifest-declared, config-resolved) → SSRF guard (loopback and
    metadata blocked, LAN allowed) → auth headers injected from encrypted
    config. Used by the platform/http endpoint AND http task steps.

    auth_override is a full Authorization value ("Bearer x" / "Basic y")
    resolved from the run context by a task step's "auth" field — a credential
    minted DURING the run (OAuth refresh). It wins over config-injected auth
    for this one request; config secrets can never reach it (untemplatable).
    PROPFIND is in the method set for WebDAV listings (webdav task steps and
    interactive folder browsers via the platform/http envelope)."""
    from urllib.parse import urlsplit

    from app.services.app_service import get_app_service
    from app.services.ssrf_guard import SSRFError, validate_url

    service = get_app_service()
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "PROPFIND"):
        raise AppHttpError(400, f"Unsupported method {method}")

    allowed_hosts = service.allowed_http_hosts(app_id)
    if not allowed_hosts:
        raise AppHttpError(
            503,
            "No allowed hosts resolved — the admin must fill in this app's configuration",
        )
    target_host = (urlsplit(url).hostname or "").lower()
    if target_host not in allowed_hosts:
        raise AppHttpError(403, f"Host '{target_host}' is not in this app's declared hosts")
    try:
        validate_url(url, allow_private=True)
    except SSRFError as e:
        raise AppHttpError(403, f"Blocked target: {e}")

    headers = dict(service.platform_auth_headers(app_id, target_host=target_host))
    headers.setdefault("Accept", "application/json")
    for name, value in (extra_headers or {}).items():
        if name.lower() in _FORBIDDEN_APP_HEADERS or name.lower() in (
            h.lower() for h in headers
        ):
            continue  # config-injected auth always wins; framing stays ours
        headers[name] = value
    if auth_override:
        headers["Authorization"] = auth_override
    if content_type:
        headers["Content-Type"] = content_type
    elif body is not None:
        headers.setdefault("Content-Type", "application/json")

    try:
        async with httpx.AsyncClient(
            timeout=get_settings().app_http_timeout, follow_redirects=False
        ) as client:
            response = await client.request(
                method,
                url,
                content=body.encode() if isinstance(body, str) else None,
                headers=headers,
            )
    except httpx.HTTPError as e:
        logger.warning(f"App '{app_id}' platform http to {target_host} failed: {e}")
        raise AppHttpError(502, f"Upstream request failed: {type(e).__name__}")

    if len(response.content) > 20 * 1024 * 1024:
        raise AppHttpError(502, "Upstream response exceeds 20 MB")
    return response


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_body(response: httpx.Response, response_type: Optional[str]) -> Any:
    if response_type == "text":
        return response.text
    content_type = response.headers.get("content-type", "")
    if response_type == "json" or "json" in content_type:
        try:
            return response.json()
        except ValueError:
            if response_type == "json":
                raise StepError("response is not valid JSON")
            return response.text
    return response.text


class AppTaskService:
    """Task lifecycle + the execution engine."""

    def __init__(self):
        self.settings = get_settings()
        from app.services.app_service import get_app_service

        self._apps = get_app_service()
        # (app_id, task_id) → live asyncio.Task (strong refs — see main.py's
        # _chain_tasks for the GC rationale)
        self._running: Dict[Tuple[str, str], asyncio.Task] = {}
        # cooperative control flags per task
        self._flags: Dict[Tuple[str, str], Dict[str, bool]] = {}
        self._last_persist: Dict[Tuple[str, str], float] = {}
        self._global_slots: Optional[asyncio.Semaphore] = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _tasks_dir(self, app_id: str) -> Path:
        return self._apps._app_dir(app_id) / "tasks"

    def _task_path(self, app_id: str, task_id: str) -> Path:
        if not re.fullmatch(r"apptask_[a-z0-9]{6,32}", task_id or ""):
            raise TaskDefinitionError([f"invalid task id {task_id!r}"])
        return self._tasks_dir(app_id) / f"{task_id}.json"

    def _load_task(self, app_id: str, task_id: str) -> Optional[dict]:
        path = self._task_path(app_id, task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"App '{app_id}' task {task_id} unreadable: {e}")
            return None

    def _persist(self, record: dict, *, force: bool = True) -> None:
        """Write the task file. With force=False, throttles to one write per
        2s per task (item-completion path)."""
        key = (record["app_id"], record["task_id"])
        now = time.monotonic()
        if not force and now - self._last_persist.get(key, 0.0) < 2.0:
            return
        self._last_persist[key] = now
        path = self._task_path(record["app_id"], record["task_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2, default=str))
        tmp.replace(path)

    def list_tasks(self, app_id: str) -> List[dict]:
        tasks_dir = self._tasks_dir(app_id)
        if not tasks_dir.is_dir():
            return []
        records = []
        for path in sorted(tasks_dir.glob("apptask_*.json")):
            try:
                records.append(self._summarize(json.loads(path.read_text())))
            except (json.JSONDecodeError, OSError):
                continue
        records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return records

    def get_task(self, app_id: str, task_id: str) -> Optional[dict]:
        record = self._load_task(app_id, task_id)
        if not record:
            return None
        full = self._summarize(record)
        full["items"] = [
            {k: v for k, v in item.items() if k in ("vars", "status", "error", "reason")}
            for item in record.get("items", [])
        ]
        full["definition"] = record.get("definition")
        return full

    @staticmethod
    def _summarize(record: dict) -> dict:
        return {
            "task_id": record["task_id"],
            "name": record.get("name", ""),
            "status": record.get("status", "pending"),
            "schedule": record.get("schedule"),
            "created_at": record.get("created_at"),
            "created_by": record.get("created_by"),
            "counts": record.get("counts", {}),
            "error": record.get("error"),
            "last_run": (record.get("runs") or [None])[-1],
            "message": record.get("message", ""),
        }

    # ------------------------------------------------------------------
    # Lifecycle API
    # ------------------------------------------------------------------

    def _capabilities(self, app_id: str) -> set:
        loaded = self._apps._load(app_id)
        if not loaded:
            return set()
        manifest = loaded[0]
        if manifest.get("type") != "platform":
            return set()
        return set(manifest.get("capabilities") or {})

    def submit(self, app_id: str, defn: Any, created_by: str) -> dict:
        capabilities = self._capabilities(app_id)
        if "tasks" not in capabilities:
            raise TaskDefinitionError(['this app does not declare the "tasks" capability'])
        issues = validate_task_definition(
            defn, capabilities=capabilities, settings=self.settings
        )
        if issues:
            raise TaskDefinitionError(issues)
        self._prune_old_tasks(app_id)
        existing = self.list_tasks(app_id)
        if len(existing) >= self.settings.app_task_max_per_app:
            raise TaskDefinitionError(
                [
                    f"this app already has {len(existing)} stored tasks "
                    f"(cap {self.settings.app_task_max_per_app}) — delete finished ones"
                ]
            )
        record = {
            "task_id": f"apptask_{uuid.uuid4().hex[:12]}",
            "app_id": app_id,
            "name": defn["name"],
            "definition": defn,
            "schedule": defn.get("schedule"),
            "status": "pending",
            "created_at": _utcnow_iso(),
            "created_by": created_by,
            "counts": {},
            "message": "",
            "items": [],
            "runs": [],
        }
        self._persist(record)
        self._spawn(app_id, record["task_id"], fresh=True)
        return self._summarize(record)

    def _prune_old_tasks(self, app_id: str) -> None:
        """Age out terminal one-shot tasks beyond the per-app cap (oldest
        first; scheduled tasks are never auto-pruned)."""
        records = self.list_tasks(app_id)
        removable = [
            r
            for r in records
            if r["status"] in TERMINAL_STATUSES and not r.get("schedule")
        ]
        overflow = len(records) - self.settings.app_task_max_per_app + 1
        if overflow <= 0:
            return
        removable.sort(key=lambda r: r.get("created_at") or "")
        for record in removable[:overflow]:
            self._task_path(app_id, record["task_id"]).unlink(missing_ok=True)

    def apply_action(self, app_id: str, task_id: str, action: str) -> Optional[dict]:
        """pause | resume | cancel | retryFailed | runNow"""
        record = self._load_task(app_id, task_id)
        if not record:
            return None
        key = (app_id, task_id)
        flags = self._flags.setdefault(key, {"pause": False, "cancel": False})
        is_running = key in self._running and not self._running[key].done()

        if action == "pause":
            flags["pause"] = True
            if not is_running and record["status"] in ("pending",):
                record["status"] = "paused"
                self._persist(record)
        elif action == "resume":
            flags["pause"] = False
            flags["cancel"] = False
            if not is_running and record["status"] == "paused":
                self._spawn(app_id, task_id, fresh=False)
        elif action == "cancel":
            flags["cancel"] = True
            if not is_running and record["status"] not in TERMINAL_STATUSES:
                record["status"] = "cancelled"
                self._persist(record)
        elif action == "retryFailed":
            if is_running:
                raise TaskDefinitionError(["task is running — pause or wait first"])
            changed = False
            for item in record.get("items", []):
                if item.get("status") == "failed":
                    item["status"] = "pending"
                    item.pop("error", None)
                    changed = True
            if changed:
                record["status"] = "pending"
                self._persist(record)
                self._spawn(app_id, task_id, fresh=False)
        elif action == "runNow":
            if is_running:
                raise TaskDefinitionError(["task is already running"])
            flags["pause"] = False
            flags["cancel"] = False
            self._spawn(app_id, task_id, fresh=True)
        else:
            raise TaskDefinitionError([f"unknown action {action!r}"])
        return self._summarize(self._load_task(app_id, task_id) or record)

    def delete_task(self, app_id: str, task_id: str) -> bool:
        key = (app_id, task_id)
        live = self._running.get(key)
        if live and not live.done():
            self._flags.setdefault(key, {})["cancel"] = True
            live.cancel()
        path = self._task_path(app_id, task_id)
        if not path.exists():
            return False
        path.unlink()
        self._flags.pop(key, None)
        self._last_persist.pop(key, None)
        return True

    def stop_all(self, app_id: str) -> None:
        """Cancel every live run for an app (called on uninstall)."""
        for (task_app, task_id), live in list(self._running.items()):
            if task_app == app_id and not live.done():
                self._flags.setdefault((task_app, task_id), {})["cancel"] = True
                live.cancel()

    # ------------------------------------------------------------------
    # Scheduler & boot resume
    # ------------------------------------------------------------------

    def resume_interrupted(self) -> int:
        """Re-spawn tasks that were pending/running when the process died."""
        resumed = 0
        for info in self._apps.list_apps():
            for summary in self.list_tasks(info.id):
                if summary["status"] in ("pending", "running"):
                    self._spawn(info.id, summary["task_id"], fresh=False)
                    resumed += 1
        if resumed:
            logger.info(f"App tasks: resumed {resumed} interrupted task(s) after restart")
        return resumed

    async def scheduler_loop(self) -> None:
        """Re-run due scheduled tasks. One tick per minute."""
        while True:
            try:
                await asyncio.sleep(60)
                self._schedule_tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"App task scheduler tick failed: {e}")

    def _schedule_tick(self) -> None:
        now = datetime.now(timezone.utc)
        for info in self._apps.list_apps():
            if not info.enabled:
                continue
            for summary in self.list_tasks(info.id):
                schedule = summary.get("schedule") or {}
                minutes = schedule.get("everyMinutes")
                if not minutes or summary["status"] in ("running", "paused", "pending"):
                    continue
                last_run = summary.get("last_run") or {}
                last_started = last_run.get("started_at") or summary.get("created_at")
                try:
                    last_dt = datetime.fromisoformat(last_started)
                except (TypeError, ValueError):
                    last_dt = None
                if last_dt is None or (now - last_dt).total_seconds() >= minutes * 60:
                    logger.info(
                        f"App '{info.id}': scheduled task {summary['task_id']} "
                        f"('{summary['name']}') is due — starting run"
                    )
                    self._spawn(info.id, summary["task_id"], fresh=True)

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------

    def _spawn(self, app_id: str, task_id: str, *, fresh: bool) -> None:
        key = (app_id, task_id)
        if key in self._running and not self._running[key].done():
            return
        self._flags.setdefault(key, {"pause": False, "cancel": False})
        task = asyncio.create_task(self._run_guarded(app_id, task_id, fresh=fresh))
        self._running[key] = task
        task.add_done_callback(lambda _t, k=key: self._running.pop(k, None))

    async def _run_guarded(self, app_id: str, task_id: str, *, fresh: bool) -> None:
        try:
            await self._run(app_id, task_id, fresh=fresh)
        except asyncio.CancelledError:
            record = self._load_task(app_id, task_id)
            if record and record.get("status") not in TERMINAL_STATUSES:
                record["status"] = "cancelled"
                record["message"] = "cancelled"
                self._persist(record)
        except Exception as e:
            logger.error(f"App '{app_id}' task {task_id} crashed: {e}", exc_info=True)
            record = self._load_task(app_id, task_id)
            if record:
                record["status"] = "failed"
                record["error"] = f"internal error: {e}"
                self._persist(record)

    async def _run(self, app_id: str, task_id: str, *, fresh: bool) -> None:
        record = self._load_task(app_id, task_id)
        if not record:
            return
        loaded = self._apps._load(app_id)
        if not loaded or not loaded[1].get("enabled", True):
            record["status"] = "failed"
            record["error"] = "app is disabled or uninstalled"
            self._persist(record)
            return

        if self._global_slots is None:
            self._global_slots = asyncio.Semaphore(
                max(1, self.settings.app_tasks_global_concurrency)
            )
        key = (app_id, task_id)
        flags = self._flags.setdefault(key, {"pause": False, "cancel": False})
        flags["pause"] = False if fresh else flags["pause"]

        defn = record["definition"]
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        run_started = _utcnow_iso()
        record["status"] = "running"
        record["error"] = None
        record["message"] = "running setup" if defn.get("setup") else "running"
        self._persist(record)

        run_ctx: Dict[str, Any] = {
            "taskId": task_id,
            "runId": run_id,
            "startedAt": run_started,
            "itemsTotal": 0,
            "doneCount": 0,
            "failedCount": 0,
            "skippedCount": 0,
        }
        base_ctx: Dict[str, Any] = {
            "config": self._apps.public_config(app_id) or {},
            "run": run_ctx,
            "setup": {},
            "steps": {},
            "vars": {},
        }
        llm_state = {"calls": 0, "client": None}

        def finish(status: str, error: Optional[str] = None, message: str = "") -> None:
            record["status"] = status
            record["error"] = error
            record["message"] = message or status
            record.setdefault("runs", []).append(
                {
                    "run_id": run_id,
                    "started_at": run_started,
                    "finished_at": _utcnow_iso(),
                    "status": status,
                    "counts": dict(record.get("counts") or {}),
                    "error": error,
                }
            )
            record["runs"] = record["runs"][-10:]
            self._persist(record)

        try:
            # --- setup (sequential, once; holds a global slot like items do,
            # so a scheduler tick firing many tasks can't stampede setups) ---
            for step in defn.get("setup") or []:
                if flags["cancel"]:
                    finish("cancelled", message="cancelled during setup")
                    return
                async with self._global_slots:
                    await self._exec_step(app_id, step, base_ctx, "setup", llm_state)

            # --- items ---
            if fresh:
                try:
                    expanded, deduped = await self._expand_items(app_id, defn, base_ctx)
                except (StepError, TaskDefinitionError) as e:
                    finish("failed", error=f"item expansion failed: {e}")
                    return
                record["items"] = expanded
                record["counts"] = {"deduped": deduped}
            items = record.get("items", [])
            pending = [i for i, item in enumerate(items) if item.get("status") in (None, "pending", "running")]
            for i in pending:
                items[i]["status"] = "pending"
            run_ctx["itemsTotal"] = len(items)
            for item in items:
                status = item.get("status")
                if status == "done":
                    run_ctx["doneCount"] += 1
                elif status == "skipped":
                    run_ctx["skippedCount"] += 1
            self._update_counts(record, run_ctx)
            record["message"] = f"processing {len(pending)} item(s)"
            self._persist(record)

            paused = False
            if pending:
                queue: asyncio.Queue = asyncio.Queue()
                for i in pending:
                    queue.put_nowait(i)
                concurrency = min(
                    int(defn.get("concurrency", 1)), max(1, len(pending))
                )

                async def worker():
                    nonlocal paused
                    while True:
                        if flags["cancel"] or flags["pause"]:
                            paused = flags["pause"] and not flags["cancel"]
                            return
                        try:
                            index = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        item = items[index]
                        item["status"] = "running"
                        # persist the transition so a long item (chunked llm,
                        # big download) shows as working, not pending
                        self._persist(record, force=False)
                        async with self._global_slots:
                            await self._run_item(app_id, defn, item, index, base_ctx, llm_state, flags)
                        if item["status"] == "done":
                            run_ctx["doneCount"] += 1
                        elif item["status"] == "skipped":
                            run_ctx["skippedCount"] += 1
                        elif item["status"] == "failed":
                            run_ctx["failedCount"] += 1
                        elif item["status"] == "pending":
                            # put back a pause/cancel-interrupted item
                            continue
                        self._update_counts(record, run_ctx)
                        record["message"] = (
                            f"{run_ctx['doneCount'] + run_ctx['failedCount'] + run_ctx['skippedCount']}"
                            f"/{run_ctx['itemsTotal']} items"
                        )
                        self._persist(record, force=False)

                await asyncio.gather(*(worker() for _ in range(concurrency)))

            if flags["cancel"]:
                finish("cancelled", message="cancelled")
                return
            if paused or flags["pause"]:
                record["status"] = "paused"
                record["message"] = "paused"
                self._persist(record)
                return

            # --- finally (sequential, once; skipped on pause/cancel) ---
            if defn.get("finally"):
                record["message"] = "running finally steps"
                self._persist(record)
                for step in defn["finally"]:
                    async with self._global_slots:
                        await self._exec_step(app_id, step, base_ctx, "finally", llm_state)

            failed = run_ctx["failedCount"]
            finish(
                "completed",
                message=(
                    f"{run_ctx['doneCount']} done, {failed} failed, "
                    f"{run_ctx['skippedCount']} skipped"
                ),
            )
        except (StepError, AppHttpError) as e:
            finish("failed", error=str(e))
        # NOTE: never close llm_state["client"] — make_async_openai_client
        # returns a CACHED client shared with the whole backend (ask pipeline
        # included); closing it here poisons the cache and every subsequent
        # LLM call fails with APIConnectionError until a restart.

    @staticmethod
    def _update_counts(record: dict, run_ctx: dict) -> None:
        record["counts"] = {
            "total": run_ctx["itemsTotal"],
            "done": run_ctx["doneCount"],
            "failed": run_ctx["failedCount"],
            "skipped": run_ctx["skippedCount"],
            "deduped": record.get("counts", {}).get("deduped", 0),
        }

    async def _expand_items(
        self, app_id: str, defn: dict, base_ctx: dict
    ) -> Tuple[List[dict], int]:
        """Materialize the item list: literal items pass through; a fan-out
        spec maps a source list through var templates, with optional
        skipIfStored dedup (bulk-checked against app storage). Returns
        (items, deduped_count)."""
        spec = defn.get("items")
        if spec is None:
            return [], 0
        if isinstance(spec, list):
            return [
                {"vars": dict(item.get("vars") or {}), "status": "pending"}
                for item in spec
            ], 0

        if spec.get("fromEach") is not None:
            # concat fan-out: one listing step per source (e.g. one webdav
            # PROPFIND per selected folder), items merged into a single pool
            source = []
            for ref in spec["fromEach"]:
                part = resolve_ref(ref, base_ctx)
                if part is None:
                    continue  # a "when"-skipped listing resolves to null
                if not isinstance(part, list):
                    raise StepError(f"items.fromEach entry ({ref}) did not resolve to a list")
                source.extend(part)
        else:
            source = resolve_ref(spec["from"], base_ctx)
        if source is None:
            source = []
        if not isinstance(source, list):
            raise StepError(f'items.from ({spec["from"]}) did not resolve to a list')
        limit = spec.get("limit")
        if limit:
            source = source[:limit]
        if len(source) > self.settings.app_task_max_items:
            raise StepError(
                f"fan-out produced {len(source)} items — cap is "
                f"{self.settings.app_task_max_items} (use items.limit)"
            )

        deduped = 0
        if spec.get("skipIfStored") and source:
            from app.services.app_storage_service import get_app_storage_service

            keys = [
                interpolate(spec["skipIfStored"], {**base_ctx, "item": element})
                for element in source
            ]
            missing = set(
                await asyncio.to_thread(get_app_storage_service().missing, app_id, keys)
            )
            kept = [el for el, key in zip(source, keys) if key in missing]
            deduped = len(source) - len(kept)
            source = kept

        items = []
        for element in source:
            element_ctx = {**base_ctx, "item": element}
            item_vars = {
                name: resolve_value(tpl, element_ctx)
                for name, tpl in spec["vars"].items()
            }
            ok, size = output_size_ok(item_vars, 8)
            if not ok:
                raise StepError(
                    f"item vars exceed 8 KB ({size} bytes) — keep vars small and "
                    f"fetch details in a per-item step"
                )
            items.append({"vars": item_vars, "status": "pending"})
        return items, deduped

    async def _run_item(
        self,
        app_id: str,
        defn: dict,
        item: dict,
        index: int,
        base_ctx: dict,
        llm_state: dict,
        flags: dict,
    ) -> None:
        ctx = {
            "config": base_ctx["config"],
            "setup": base_ctx["setup"],
            "run": {**base_ctx["run"], "index": index},
            "vars": item.get("vars") or {},
            "steps": {},
        }
        try:
            for step in defn.get("steps") or []:
                if flags["cancel"] or flags["pause"]:
                    item["status"] = "pending"  # re-queued on resume
                    return
                await self._exec_step(app_id, step, ctx, "steps", llm_state)
            item["status"] = "done"
            item.pop("error", None)
        except SkipItem as skip:
            item["status"] = "skipped"
            if skip.reason:
                item["reason"] = skip.reason
        except (StepError, AppHttpError) as e:
            item["status"] = "failed"
            item["error"] = str(e)[:500]
        except Exception as e:
            logger.warning(f"App '{app_id}' task item {index} internal error: {e}")
            item["status"] = "failed"
            item["error"] = f"internal error: {type(e).__name__}: {e}"[:500]

    # ------------------------------------------------------------------
    # Step executors
    # ------------------------------------------------------------------

    async def _exec_step(
        self, app_id: str, step: dict, ctx: dict, section: str, llm_state: dict
    ) -> None:
        from app.services.app_task_dsl import STEP_TYPES

        step_type = next(k for k in step if k in STEP_TYPES)
        step_id = step.get("id")
        label = step_id or step_type
        if "when" in step and not eval_condition(step["when"], ctx):
            if step_id:
                self._store_output(ctx, section, step_id, None)
            return
        spec = step[step_type]
        try:
            if step_type == "http":
                output = await self._exec_http(app_id, spec, ctx)
            elif step_type == "webdav":
                output = await self._exec_webdav(app_id, spec, ctx)
            elif step_type == "cortex":
                output = await self._exec_cortex(app_id, spec, ctx)
            elif step_type == "llm":
                output = await self._exec_llm(app_id, spec, ctx, llm_state)
            elif step_type == "store":
                output = await self._exec_store(app_id, spec, ctx)
            elif step_type == "template":
                output = self._exec_template(spec, ctx)
            else:  # skipItem
                if eval_condition(spec["when"], ctx):
                    raise SkipItem(interpolate(spec.get("reason", ""), ctx))
                output = None
        except (SkipItem, StepError):
            raise
        except AppHttpError as e:
            raise StepError(f"step '{label}': {e.detail}")
        except Exception as e:
            raise StepError(f"step '{label}': {type(e).__name__}: {e}")

        if step_id is not None:
            ok, size = output_size_ok(output, self.settings.app_task_step_output_max_kb)
            if not ok:
                raise StepError(
                    f"step '{label}' output is {size // 1024} KB — exceeds the "
                    f"{self.settings.app_task_step_output_max_kb} KB cap; write large "
                    f"artifacts to storage instead of the step context"
                )
            self._store_output(ctx, section, step_id, output)

    @staticmethod
    def _store_output(ctx: dict, section: str, step_id: str, output: Any) -> None:
        if section == "setup":
            ctx["setup"][step_id] = output
        else:
            ctx["steps"][step_id] = output

    @staticmethod
    def _resolve_auth(auth: Any, ctx: dict) -> Optional[str]:
        """Resolve a step's dynamic {"bearer"|"basic": ref/template} credential
        to a full Authorization value. Values come from the run context (e.g.
        an OAuth refresh response) — config secrets are untemplatable."""
        if not auth:
            return None
        scheme, source = next(iter(auth.items()))
        value = resolve_value(source, ctx)
        value = str(value).strip() if value is not None else ""
        if not value:
            raise StepError(f"auth.{scheme} resolved to an empty value")
        return f"{'Bearer' if scheme == 'bearer' else 'Basic'} {value}"

    async def _exec_http(self, app_id: str, spec: dict, ctx: dict) -> dict:
        method = str(spec["method"]).upper()
        url = interpolate(spec["url"], ctx)
        body = spec.get("body")
        if body is not None:
            body = resolve_value(body, ctx)
            if not isinstance(body, str):
                body = json.dumps(body, default=str)
        content_type = spec.get("contentType")
        extra_headers = {
            name: interpolate(value, ctx)
            for name, value in (spec.get("headers") or {}).items()
        }
        paginate = spec.get("paginate")
        auth_kwargs: Dict[str, Any] = {}
        auth_override = self._resolve_auth(spec.get("auth"), ctx)
        if auth_override:
            auth_kwargs["auth_override"] = auth_override

        response = await execute_app_http(
            app_id, method=method, url=url, body=body,
            content_type=content_type, extra_headers=extra_headers, **auth_kwargs,
        )
        parsed = _parse_body(response, spec.get("responseType"))
        if not paginate:
            if response.status_code >= 400:
                raise StepError(
                    f"http {method} {url} → {response.status_code}: "
                    f"{str(parsed)[:200]}"
                )
            return {"status": response.status_code, "body": parsed}

        # --- pagination: follow the "next" path, accumulate the "items" path ---
        if response.status_code >= 400:
            raise StepError(f"http {method} {url} → {response.status_code}")
        items_path = paginate["items"]
        next_path = paginate.get("next")
        max_pages = paginate.get("maxPages", 20)
        # The accumulated result is bounded DURING the loop, not just by the
        # post-hoc step-output check — otherwise maxPages × 20 MB pages could
        # buffer gigabytes headless before being discarded (review finding).
        byte_budget = self.settings.app_task_step_output_max_kb * 1024
        total_bytes = 0
        all_items: List[Any] = []
        pages = 0
        while True:
            pages += 1
            total_bytes += len(response.content)
            if total_bytes > byte_budget:
                raise StepError(
                    f"pagination accumulated {total_bytes // 1024} KB — exceeds the "
                    f"{self.settings.app_task_step_output_max_kb} KB step cap; "
                    f"narrow the query or lower maxPages"
                )
            page_items = _dig(parsed, items_path)
            if isinstance(page_items, list):
                all_items.extend(page_items)
            next_url = _dig(parsed, next_path) if next_path else None
            if not next_url or pages >= max_pages:
                break
            # every page re-passes the host allowlist + SSRF gates
            response = await execute_app_http(
                app_id, method="GET", url=str(next_url),
                extra_headers=extra_headers, **auth_kwargs,
            )
            if response.status_code >= 400:
                raise StepError(f"http pagination page {pages + 1} → {response.status_code}")
            parsed = _parse_body(response, spec.get("responseType"))
        output: Dict[str, Any] = {"status": 200, "items": all_items, "pages": pages}
        key_by = paginate.get("keyBy")
        if key_by:
            output["map"] = {
                str(el[key_by]): el
                for el in all_items
                if isinstance(el, dict) and el.get(key_by) is not None
            }
        return output

    _PROPFIND_BODY = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop>'
        "<d:getetag/><d:getlastmodified/><d:getcontentlength/>"
        "<d:getcontenttype/><d:resourcetype/><oc:fileid/>"
        "</d:prop></d:propfind>"
    )

    async def _exec_webdav(self, app_id: str, spec: dict, ctx: dict) -> dict:
        """PROPFIND folder listing with the multistatus XML parsed server-side
        into plain items — the DSL has no XML vocabulary, and cloud-folder
        sync needs listings referenceable from fan-outs/conditions. Same
        gates as http steps (host allowlist, SSRF guard, config auth)."""
        url = interpolate(spec["url"], ctx)
        depth = spec.get("depth", 1)
        auth_kwargs: Dict[str, Any] = {}
        auth_override = self._resolve_auth(spec.get("auth"), ctx)
        if auth_override:
            auth_kwargs["auth_override"] = auth_override
        response = await execute_app_http(
            app_id, method="PROPFIND", url=url, body=self._PROPFIND_BODY,
            content_type='application/xml; charset="utf-8"',
            extra_headers={"Depth": str(depth)}, **auth_kwargs,
        )
        if response.status_code >= 400:
            raise StepError(
                f"webdav PROPFIND {url} → {response.status_code}: "
                f"{response.text[:200]}"
            )
        items = _parse_multistatus(response.content, request_url=url)
        listing_filter = spec.get("filter")
        if listing_filter == "files":
            items = [item for item in items if not item["isDir"]]
        elif listing_filter == "dirs":
            items = [item for item in items if item["isDir"]]
        return {"status": response.status_code, "items": items, "count": len(items)}

    async def _exec_cortex(self, app_id: str, spec: dict, ctx: dict) -> dict:
        method = str(spec["method"]).upper()
        path = interpolate(spec["path"], ctx).lstrip("/")
        if not self._apps.endpoint_allowed(app_id, path):
            raise StepError(
                f"cortex endpoint '{path.split('?')[0]}' is not in this app's manifest allowlist"
            )
        upstream_key = self._apps.upstream_api_key(app_id)
        if not upstream_key:
            raise StepError("app key unavailable")

        url = f"{self.settings.app_proxy_upstream}/api/{path}"
        headers = {"X-API-Key": upstream_key, "Accept": "application/json"}
        request_kwargs: Dict[str, Any] = {}
        multipart = spec.get("multipart")
        if multipart and multipart.get("fromUrl"):
            # Binary passthrough: fetch through the shared http gates and hand
            # the raw bytes straight to the upload — they never enter the step
            # context (the DSL stays text-only; PDFs/images stay intact).
            fetch_url = interpolate(multipart["fromUrl"], ctx)
            fetch_headers = {
                name: interpolate(value, ctx)
                for name, value in (multipart.get("headers") or {}).items()
            }
            fetch_kwargs: Dict[str, Any] = {}
            auth_override = self._resolve_auth(multipart.get("auth"), ctx)
            if auth_override:
                fetch_kwargs["auth_override"] = auth_override
            upstream = await execute_app_http(
                app_id, method=str(multipart.get("method", "GET")).upper(),
                url=fetch_url, extra_headers=fetch_headers, **fetch_kwargs,
            )
            if upstream.status_code >= 400:
                raise StepError(
                    f"multipart fetch {fetch_url} → {upstream.status_code}: "
                    f"{upstream.text[:200]}"
                )
            filename = interpolate(multipart["filename"], ctx)
            field = multipart.get("field", "file")
            content_type = multipart.get("contentType") or (
                upstream.headers.get("content-type", "application/octet-stream")
                .split(";")[0].strip()
            )
            request_kwargs["files"] = {
                field: (filename, upstream.content, content_type)
            }
        elif multipart:
            content = resolve_value(multipart["content"], ctx)
            if not isinstance(content, str):
                content = json.dumps(content, default=str)
            filename = interpolate(multipart["filename"], ctx)
            field = multipart.get("field", "file")
            content_type = multipart.get("contentType", "text/markdown")
            request_kwargs["files"] = {
                field: (filename, content.encode(), content_type)
            }
        elif spec.get("body") is not None:
            request_kwargs["json"] = resolve_value(spec["body"], ctx)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)
            ) as client:
                response = await client.request(method, url, headers=headers, **request_kwargs)
        except httpx.HTTPError as e:
            raise StepError(f"cortex {method} /{path} failed: {type(e).__name__}")
        parsed = _parse_body(response, None)
        if response.status_code >= 400:
            raise StepError(
                f"cortex {method} /{path.split('?')[0]} → {response.status_code}: "
                f"{str(parsed)[:200]}"
            )
        return {"status": response.status_code, "body": parsed}

    async def _exec_llm(self, app_id: str, spec: dict, ctx: dict, llm_state: dict) -> dict:
        if not self._apps.has_capability(app_id, "llm"):
            raise StepError('this app does not declare the "llm" capability')
        from app.services.llm_config import (
            build_chat_params,
            get_extraction_llm_config,
            make_async_openai_client,
        )

        # Extraction tier, not the chat model: llm task steps are bulk
        # mechanical work (transcript cleanup, summarization) — the chat
        # model is often a slow reasoning model. Falls back to the main
        # config automatically when no extraction model is configured.
        config = get_extraction_llm_config()
        if not config.api_key:
            raise StepError("no LLM is configured on this instance")
        if llm_state.get("client") is None:
            llm_state["client"] = make_async_openai_client(
                api_key=config.api_key, base_url=config.base_url
            )
        client = llm_state["client"]
        params = build_chat_params(
            config.model,
            max_tokens=spec.get("maxTokens"),
            temperature=spec.get("temperature"),
        )
        system = interpolate(spec["system"], ctx) if spec.get("system") else None

        async def complete(prompt: str) -> str:
            cap = self.settings.app_task_llm_calls_per_run
            if llm_state["calls"] >= cap:
                raise StepError(f"llm call cap reached ({cap} per run)")
            llm_state["calls"] += 1
            messages = ([{"role": "system", "content": system}] if system else []) + [
                {"role": "user", "content": prompt}
            ]
            response = await client.chat.completions.create(
                model=config.model, messages=messages, **params
            )
            return (response.choices[0].message.content or "").strip()

        validate = spec.get("validate") or {}
        min_ratio = float(validate.get("minLengthRatio", 0.5))
        min_overlap = float(validate.get("minWordOverlap", 0.6))
        on_fail = validate.get("onFail", "keepOriginal")

        chunk_spec = spec.get("chunk")
        if not chunk_spec:
            prompt = interpolate(spec["prompt"], ctx)
            text = await complete(prompt)
            if validate:
                original = resolve_ref(spec["input"], ctx)
                original = original if isinstance(original, str) else str(original or "")
                if not chunk_output_valid(
                    original, text, min_length_ratio=min_ratio, min_word_overlap=min_overlap
                ):
                    text = await complete(prompt)  # retry once
                    if not chunk_output_valid(
                        original, text, min_length_ratio=min_ratio, min_word_overlap=min_overlap
                    ):
                        if on_fail == "fail":
                            raise StepError("llm output failed validation twice")
                        text = original
            return {"text": text}

        source = resolve_ref(spec["input"], ctx)
        source = source if isinstance(source, str) else str(source or "")
        chunks = split_into_chunks(source, chunk_spec["words"])
        produced: List[str] = []
        kept_original = 0
        for chunk in chunks:
            chunk_ctx = {**ctx, "chunk": chunk}
            prompt = interpolate(spec["prompt"], chunk_ctx)
            text = await complete(prompt)
            if validate and not chunk_output_valid(
                chunk, text, min_length_ratio=min_ratio, min_word_overlap=min_overlap
            ):
                text = await complete(prompt)  # retry once, then policy
                if not chunk_output_valid(
                    chunk, text, min_length_ratio=min_ratio, min_word_overlap=min_overlap
                ):
                    if on_fail == "fail":
                        raise StepError("llm chunk output failed validation twice")
                    text = chunk
                    kept_original += 1
            produced.append(text)
        return {
            "text": "\n\n".join(produced),
            "chunksTotal": len(chunks),
            "chunksKeptOriginal": kept_original,
        }

    async def _exec_store(self, app_id: str, spec: dict, ctx: dict) -> dict:
        from app.services.app_storage_service import (
            AppStorageError,
            get_app_storage_service,
        )

        storage = get_app_storage_service()
        try:
            if "get" in spec:
                key = interpolate(spec["get"], ctx)
                value = await asyncio.to_thread(storage.get, app_id, key)
                found = value is not None or await asyncio.to_thread(
                    storage.exists, app_id, key
                )
                return {"found": found, "value": value}
            if "put" in spec:
                key = interpolate(spec["put"], ctx)
                value = resolve_value(spec["value"], ctx)
                await asyncio.to_thread(storage.put, app_id, key, value)
                return {"ok": True, "key": key}
            if "delete" in spec:
                key = interpolate(spec["delete"], ctx)
                deleted = await asyncio.to_thread(storage.delete, app_id, key)
                return {"ok": True, "deleted": deleted}
            prefix = interpolate(spec["list"], ctx)
            listing = await asyncio.to_thread(
                storage.list_keys, app_id, prefix, spec.get("limit", 100)
            )
            return listing
        except AppStorageError as e:
            raise StepError(f"store: {e}")

    @staticmethod
    def _exec_template(spec: dict, ctx: dict) -> dict:
        if "text" in spec:
            return {"text": interpolate(spec["text"], ctx)}
        rendered: List[str] = []
        for line in spec["lines"]:
            if isinstance(line, str):
                rendered.append(interpolate(line, ctx))
                continue
            if "when" in line and not eval_condition(line["when"], ctx):
                continue
            rendered.append(interpolate(line["text"], ctx))
        joiner = spec.get("joiner", "\n")
        return {"text": joiner.join(rendered)}


_DAV_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns"}


def _parse_multistatus(content: bytes, *, request_url: str) -> List[dict]:
    """Normalize a WebDAV multistatus into [{href, name, etag, lastModified,
    size, contentType, isDir, fileId?}]. The requested collection's own entry
    is dropped — callers want the children. Parsing is bounded by the 20 MB
    response cap upstream; libexpat's amplification limits cover entity
    tricks."""
    from email.utils import parsedate_to_datetime
    from urllib.parse import unquote, urlsplit
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as e:
        raise StepError(f"webdav response is not valid multistatus XML: {e}")

    self_path = unquote(urlsplit(request_url).path).rstrip("/")
    items: List[dict] = []
    for response in root.findall("d:response", _DAV_NS):
        href = unquote(response.findtext("d:href", "", _DAV_NS))
        prop = None
        for propstat in response.findall("d:propstat", _DAV_NS):
            status_line = propstat.findtext("d:status", "", _DAV_NS)
            if " 200 " in f"{status_line} ":
                prop = propstat.find("d:prop", _DAV_NS)
                break
        if prop is None or href.rstrip("/") == self_path:
            continue
        etag = (prop.findtext("d:getetag", "", _DAV_NS) or "").strip()
        if etag.startswith("W/"):
            etag = etag[2:]
        etag = etag.strip('"')
        modified_raw = prop.findtext("d:getlastmodified", "", _DAV_NS) or ""
        try:
            modified = parsedate_to_datetime(modified_raw).isoformat()
        except (TypeError, ValueError):
            modified = modified_raw
        resourcetype = prop.find("d:resourcetype", _DAV_NS)
        is_dir = resourcetype is not None and (
            resourcetype.find("d:collection", _DAV_NS) is not None
        )
        try:
            size = int(prop.findtext("d:getcontentlength", "", _DAV_NS) or 0)
        except ValueError:
            size = 0
        item = {
            "href": href,
            "name": href.rstrip("/").rsplit("/", 1)[-1],
            "etag": etag,
            "lastModified": modified,
            "size": size,
            "contentType": prop.findtext("d:getcontenttype", "", _DAV_NS) or "",
            "isDir": is_dir,
        }
        file_id = prop.findtext("oc:fileid", "", _DAV_NS)
        if file_id:
            item["fileId"] = file_id
        items.append(item)
    return items


def _dig(value: Any, path: Optional[str]) -> Any:
    """Dotted-path lookup into a parsed response body."""
    if not path:
        return None
    current = value
    for seg in path.split("."):
        if isinstance(current, dict):
            current = current.get(seg)
        elif isinstance(current, list):
            try:
                current = current[int(seg)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


_app_task_service: Optional[AppTaskService] = None


def get_app_task_service() -> AppTaskService:
    global _app_task_service
    if _app_task_service is None:
        _app_task_service = AppTaskService()
    return _app_task_service
