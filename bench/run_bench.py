#!/usr/bin/env python3
"""Autonomous benchmark orchestrator for Cortex LLM-stack combinations.

Drives the full Cortex ingestion pipeline (Phase A → Phase B → Step 3) once per
combo in `combos.yaml`, captures stats + log signals, fills the heuristic
analysis columns, and appends a row to analysis/llm-config-results.ods.

After all combos finish, runs ONE Claude API call to fill the free-form
observations / vs_previous_run / code_optimisation_findings fields.

Usage:
    python run_bench.py                 # full batch
    python run_bench.py --only 01-mistral-x2
    python run_bench.py --dry-run       # print plan, no side effects
    python run_bench.py --skip-llm-review

All side effects (env changes, container recreates) are confined to the local
machine. The `analysis/` directory is gitignored.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

# Local imports — bench harness modules
sys.path.insert(0, str(Path(__file__).parent))
from build_dashboard import build as build_dashboard_data, write as write_dashboard_data  # noqa: E402
from combo_resolver import ResolverError, load_all  # noqa: E402
from cortex_client import CortexClient, CortexError  # noqa: E402
from heuristics import apply_heuristics  # noqa: E402
from log_parser import fetch_container_logs, parse_logs, tail_recent_events  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
BENCH_DIR = REPO_ROOT / "bench"
LOGS_DIR = BENCH_DIR / "logs"
RUNS_DIR = LOGS_DIR / "runs"
BACKUPS_DIR = BENCH_DIR / "backups"
BENCH_FILES_DIR = BENCH_DIR / "files"
ODS_BUILDER = BENCH_DIR / "build_results_ods.py"
ODS_PATH = LOGS_DIR / "llm-config-results.ods"
COMBOS_PATH = BENCH_DIR / "combos.yaml"
MODELS_PATH = BENCH_DIR / "models.yaml"
CONTAINER_NAME = "cortex-backend"

PER_COMBO_TIMEOUT_S = 75 * 60  # 75 min hard cap. Empirically: ingestion 15-20m
                                # + qa_speed 8-10m + qa_quality 25-35m at 15
                                # questions (mean ~120s/q in deep research mode).
                                # 45 min was too tight — combo timed out mid
                                # qa_quality and discarded the speed answers.
PHASE_TIMEOUT_S = 25 * 60     # generous per-phase cap
HEALTH_TIMEOUT_S = 120
QA_BANK_DEFAULT_COUNT = 10
QA_QUALITY_SUBSET_DEFAULT = 5  # Quality (deep research) mode is the slow arm
                                # at ~60-180s per question. Subsetting to the
                                # first N of the bank keeps total wall time
                                # manageable without giving up the chat-mode
                                # signal which is far cheaper per question.


# ---------------------------------------------------------------------------
# Concurrent-batch guard — `bench/logs/.bench-batch.lock`
# ---------------------------------------------------------------------------

class BatchLockError(RuntimeError):
    """Raised when another bench batch already owns the lock file."""


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists, we can't signal it (different user) — still alive.
        return True
    except (OSError, ProcessLookupError):
        return False


class BatchLock:
    """File-system lock that refuses concurrent bench batches.

    Two simultaneous `python bench/run_bench.py` invocations race on
    `docker compose up --force-recreate` and corrupt each other's runs
    (we saw this empirically: combos 01 + 02 of batch 08-19 both errored
    because batch 08-20 started a second later and stole the container).

    This guard writes a PID-stamped lock file at `bench/logs/.bench-batch.lock`
    on batch start (atomic O_CREAT|O_EXCL) and removes it on exit. If the file
    is found at start AND the owning PID is alive, we refuse with a clear
    BatchLockError. If the PID is dead (orphaned lock from a crashed run),
    we reclaim it.
    """

    PATH = LOGS_DIR / ".bench-batch.lock"

    def __init__(self, batch_id: str) -> None:
        self.batch_id = batch_id
        self._acquired = False

    def acquire(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Pre-check: any existing lock from a dead PID is stale — reclaim it.
        if self.PATH.exists():
            try:
                existing = json.loads(self.PATH.read_text(encoding="utf-8"))
                other_pid = existing.get("pid")
                other_batch = existing.get("batch_id")
                other_started = existing.get("started_at")
                if isinstance(other_pid, int) and _pid_alive(other_pid):
                    raise BatchLockError(
                        f"Another bench batch is already running.\n"
                        f"  PID:        {other_pid}\n"
                        f"  Batch ID:   {other_batch}\n"
                        f"  Started:    {other_started}\n"
                        f"\n"
                        f"Wait for it to finish, or kill PID {other_pid} "
                        f"and remove the stale lock file:\n"
                        f"  rm {self.PATH.relative_to(REPO_ROOT)}"
                    )
                print(
                    f"[lock] stale lock from dead PID {other_pid} "
                    f"(batch {other_batch}) — reclaiming",
                    file=sys.stderr,
                )
                self.PATH.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[lock] corrupt lock file ({exc}) — replacing", file=sys.stderr)
                try:
                    self.PATH.unlink(missing_ok=True)
                except OSError:
                    pass

        # Atomic create — closes the TOCTOU window between exists() and write().
        try:
            fd = os.open(str(self.PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise BatchLockError(
                f"Another bench batch raced us to the lock at {self.PATH}. "
                f"Try again in a few seconds."
            ) from exc

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({
                "pid": os.getpid(),
                "batch_id": self.batch_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

        self._acquired = True
        atexit.register(self.release)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass

    def _on_signal(self, signum, frame):  # noqa: ARG002 — signal handler shape
        self.release()
        raise SystemExit(128 + signum)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            if self.PATH.exists():
                existing = json.loads(self.PATH.read_text(encoding="utf-8"))
                # Only delete if WE own it — defensive in case a stale-reclaim
                # by another process wrote over our lock.
                if existing.get("pid") == os.getpid():
                    self.PATH.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            # If the file is unreadable, try the unlink anyway as a last resort.
            try:
                self.PATH.unlink(missing_ok=True)
            except OSError:
                pass
        self._acquired = False


# ---------------------------------------------------------------------------
# .env management — backup, swap, restore
# ---------------------------------------------------------------------------

@dataclass
class EnvFile:
    """Line-preserving editor for a KEY=VALUE .env file."""
    path: Path
    lines: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "EnvFile":
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return cls(path=path, lines=text.splitlines())

    def get(self, key: str) -> Optional[str]:
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            if k.strip() == key:
                return v.strip()
        return None

    def set(self, key: str, value: str) -> None:
        """Update an existing key or append if missing. Preserves surrounding lines."""
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for i, line in enumerate(self.lines):
            if pattern.match(line):
                self.lines[i] = f"{key}={value}"
                return
        self.lines.append(f"{key}={value}")

    def write(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        tmp.replace(self.path)


class EnvSwapper:
    """Backup .env at construction; restore on close() or process exit."""

    def __init__(self, env_path: Path, batch_id: str):
        self.env_path = env_path
        self.backup_path = LOGS_DIR / f".env.bak.{batch_id}"
        self._restored = False
        if not env_path.exists():
            raise RuntimeError(f".env not found at {env_path}")
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env_path, self.backup_path)
        atexit.register(self.restore)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass

    def _on_signal(self, signum, frame):
        self.restore()
        raise SystemExit(128 + signum)

    def restore(self) -> None:
        if self._restored:
            return
        if self.backup_path.exists():
            shutil.copy2(self.backup_path, self.env_path)
            # Keep the .bak file around as a paper trail; gitignored anyway.
            print(f"[env] Restored .env from {self.backup_path}", file=sys.stderr)
        self._restored = True

    def admin_key(self) -> str:
        """Read ADMIN_API_KEY from the backup (the original, pre-swap value)."""
        env = EnvFile.load(self.backup_path)
        key = env.get("ADMIN_API_KEY")
        if not key:
            raise RuntimeError("ADMIN_API_KEY missing from .env")
        return key

    def primary_model_config(self) -> dict:
        """Read the OPERATOR'S original primary model config from the backup .env.

        Returns {"model", "base_url", "api_key"}. Used by the end-of-batch LLM
        review so the operator's configured Q&A model does the summary — not
        whatever value the last combo's `apply_combo_to_env` left behind.
        """
        env = EnvFile.load(self.backup_path)
        return {
            "model": env.get("OPENAI_MODEL") or "",
            "base_url": env.get("OPENAI_API_BASE") or "",
            "api_key": env.get("OPENAI_API_KEY") or "",
        }


def apply_combo_to_env(combo: dict) -> None:
    """Write a new .env reflecting a RESOLVED combo's per-tier model configs.

    The combo argument must already be resolved (each of primary / extraction /
    relationship is a full model dict from combo_resolver.resolve_combo).
    """
    env = EnvFile.load(ENV_PATH)

    primary = combo["primary"]
    extraction = combo["extraction"]
    relationship = combo["relationship"]

    # Primary tier — Q&A / researcher
    env.set("OPENAI_MODEL", primary["model_id"])
    env.set("OPENAI_API_BASE", primary["base_url"])
    env.set("OPENAI_API_KEY", primary["api_key"])

    # Extraction tier — entity extraction + summaries + community naming
    env.set("GRAPH_EXTRACTION_MODEL", extraction["model_id"])
    env.set("GRAPH_EXTRACTION_API_BASE", extraction["base_url"])
    env.set("GRAPH_EXTRACTION_API_KEY", extraction["api_key"])
    env.set("GRAPH_EXTRACTION_MAX_CONTEXT", str(extraction["context_length"]))

    # Relationship tier — candidate scan + per-chunk + Phase 2 batch
    env.set("RELATIONSHIP_EXTRACTION_MODEL", relationship["model_id"])
    env.set("RELATIONSHIP_EXTRACTION_API_BASE", relationship["base_url"])
    env.set("RELATIONSHIP_EXTRACTION_API_KEY", relationship["api_key"])
    env.set("RELATIONSHIP_MAX_CONTEXT", str(relationship["context_length"]))

    # Optional: scale RELATIONSHIP_MAX_OUTPUT_TOKENS per model if defined
    if "max_output_tokens" in relationship:
        env.set(
            "RELATIONSHIP_MAX_OUTPUT_TOKENS",
            str(relationship["max_output_tokens"]),
        )

    # Reasoning modes: defaults are off/off/auto; combos can override.
    env.set("EXTRACTION_REASONING_MODE", combo.get("extraction_reasoning_mode", "off"))
    env.set("RELATIONSHIP_REASONING_MODE", combo.get("relationship_reasoning_mode", "off"))
    env.set("DEFAULT_REASONING_MODE", combo.get("default_reasoning_mode", "auto"))

    env.write()


# ---------------------------------------------------------------------------
# Docker control
# ---------------------------------------------------------------------------

def recreate_backend() -> None:
    """Force-recreate the backend container so it re-reads env_file."""
    cmd = ["docker", "compose", "up", "-d", "backend", "--force-recreate"]
    print(f"[docker] {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


# ---------------------------------------------------------------------------
# Per-combo run
# ---------------------------------------------------------------------------

async def run_combo(
    combo: dict,
    admin_key: str,
    batch_id: str,
    dry_run: bool = False,
    live: Optional[LiveState] = None,
    qa_questions: Optional[list[dict]] = None,
    qa_modes: tuple[str, ...] = ("speed", "quality"),
    qa_quality_count: Optional[int] = None,
) -> dict:
    """Execute one combo end-to-end. Returns the run dict (heuristics applied).

    If `live` is provided, the function updates `live.phase` at each pipeline
    transition (A → B → step_3 → qa_speed → qa_quality → done) so the periodic
    ticker reflects the correct current phase in `bench/logs/.bench-live.json`.

    If `qa_questions` is None or empty, the Q+A phases are skipped (qa_*
    fields populated with zeros). The Q+A step is wrapped in try/except —
    a Q+A failure never poisons the ingestion data already captured.
    """
    combo_id = combo["id"]
    run_id = f"{batch_id}_{combo_id}"

    # Per-tier resolved dicts (provided by combo_resolver.resolve_combo)
    primary = combo["primary"]
    extraction = combo["extraction"]
    relationship = combo["relationship"]

    base_run: dict = {
        "run_id": run_id,
        "batch_id": batch_id,
        "combo_id": combo_id,
        "primary_model": primary["model_id"],
        "extraction_model": extraction["model_id"],
        "relationship_model": relationship["model_id"],
        "primary_base": primary["base_url"],
        "extraction_base": extraction["base_url"],
        "relationship_base": relationship["base_url"],
        "primary_family": primary.get("family", ""),
        "extraction_family": extraction.get("family", ""),
        "relationship_family": relationship.get("family", ""),
        "primary_context_length": primary["context_length"],
        "extraction_context_length": extraction["context_length"],
        "relationship_context_length": relationship["context_length"],
        "extraction_reasoning_mode": combo.get("extraction_reasoning_mode", "off"),
        "relationship_reasoning_mode": combo.get("relationship_reasoning_mode", "off"),
        "default_reasoning_mode": combo.get("default_reasoning_mode", "auto"),
        "reasoning_model_overrides": "",
        "issue_notes": "",
    }

    if dry_run:
        print(f"[dry-run] would run combo {combo_id}", file=sys.stderr)
        for tier_name, m in (("primary", primary), ("extraction", extraction), ("relationship", relationship)):
            print(
                f"  {tier_name:13s} {m['model_id']:42s} base={m['base_url']:35s} ctx={m['context_length']:>7,d}",
                file=sys.stderr,
            )
        for k in ("extraction_reasoning_mode", "relationship_reasoning_mode", "default_reasoning_mode"):
            if k in combo:
                print(f"  override     {k}={combo[k]}", file=sys.stderr)
        print(f"  notes={combo.get('note', '')}", file=sys.stderr)
        return apply_heuristics(base_run)

    combo_start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    combo_start_t = time.monotonic()
    base_run["timestamp_start"] = combo_start_iso + "Z"

    errored = False
    timed_out = False
    # Survives every exception path so the post-loop aggregator can read it.
    qa_run_data: dict[str, list[dict]] = {"speed": [], "quality": []}

    try:
        # 1. Apply env, recreate container
        apply_combo_to_env(combo)
        recreate_backend()

        # 2. Wait for backend ready
        async with CortexClient(admin_key=admin_key) as cx:
            await cx.wait_until_ready(timeout_s=HEALTH_TIMEOUT_S)

            # 3. Reset DB
            await cx.reset()

            # 4. Upload bench files
            md_files = sorted(BENCH_FILES_DIR.glob("*.md"))
            if not md_files:
                raise RuntimeError(f"No .md files found in {BENCH_FILES_DIR}")
            uploads = await cx.upload_all(md_files, collection_id=None)
            doc_count = len(uploads)

            # 5. Trigger Phase A; wait for completion
            if live: live.set_phase("A")
            phase_a_task = await cx.trigger_phase_a()
            # process-pending returns immediately with a task_id;
            # poll stats until all docs leave PENDING/PROCESSING.
            await cx.wait_phase_a(expected_docs=doc_count, timeout_s=PHASE_TIMEOUT_S)

            # 6. Trigger Phase B; poll task
            if live: live.set_phase("B")
            phase_b_task = await cx.trigger_phase_b(rebuild=False, scope="full")
            if "task_id" in phase_b_task:
                await cx.wait_task(phase_b_task["task_id"], timeout_s=PHASE_TIMEOUT_S)

            # 7. Trigger Step 3; poll task
            if live: live.set_phase("step_3")
            step_3_task = await cx.trigger_step_3(min_size=3)
            if "task_id" in step_3_task:
                await cx.wait_task(step_3_task["task_id"], timeout_s=PHASE_TIMEOUT_S)

            # 8. Snapshot final stats BEFORE Q+A so ingestion data is captured
            # even if Q+A blows up.
            stats = await cx.stats()

            # 9. Q+A phases — speed/chat mode then quality/deep-research mode.
            # Wrapped in try/except: a Q+A failure must NOT discard the
            # ingestion stats we just captured. On exception, qa_run_data
            # stays empty and the run gets zeroed qa_* fields downstream.
            if qa_questions:
                try:
                    from qa_evaluator import run_question_set, write_qa_run
                    qa_runs_dir = LOGS_DIR / "qa-runs"
                    for mode in qa_modes:
                        if mode not in ("speed", "quality"):
                            continue
                        if live: live.set_phase(f"qa_{mode}")
                        # Quality (deep research) mode is the slow arm; an
                        # optional subset cap drops it to the first N of the
                        # bank so the bench wall time stays manageable.
                        mode_qs = qa_questions
                        if (
                            mode == "quality"
                            and qa_quality_count
                            and qa_quality_count > 0
                            and qa_quality_count < len(qa_questions)
                        ):
                            mode_qs = qa_questions[:qa_quality_count]
                            print(
                                f"[qa:{mode}] using subset: first "
                                f"{qa_quality_count}/{len(qa_questions)} questions.",
                                file=sys.stderr,
                            )
                        answers = await run_question_set(cx, mode_qs, mode=mode)
                        qa_run_data[mode] = answers
                        # Persist after EACH mode so a per-combo timeout mid
                        # qa_quality doesn't discard a fully-finished qa_speed.
                        # The file is overwritten — the second write contains
                        # the union of speed + quality.
                        write_qa_run(
                            run_id,
                            questions=qa_questions,
                            speed_answers=qa_run_data["speed"],
                            quality_answers=qa_run_data["quality"],
                            qa_runs_dir=qa_runs_dir,
                        )
                        _refresh_dashboard()
                except Exception as qa_exc:  # noqa: BLE001
                    print(
                        f"[qa] phase failed for {combo_id}: {qa_exc} — "
                        "ingestion data preserved, qa_* fields will be empty.",
                        file=sys.stderr,
                    )
                    base_run["issue_notes"] = (
                        (base_run.get("issue_notes") or "")
                        + f" | qa phase failed: {type(qa_exc).__name__}: {qa_exc}"
                    ).strip(" |")

            if live: live.set_phase("done")

    except asyncio.TimeoutError:
        timed_out = True
        base_run["issue_notes"] = (
            f"Per-combo wall-clock cap ({PER_COMBO_TIMEOUT_S}s) exceeded."
        )
        stats = {}
    except CortexError as exc:
        errored = True
        base_run["issue_notes"] = f"CortexError: {exc}"
        stats = {}
    except Exception as exc:
        errored = True
        base_run["issue_notes"] = (
            f"Unhandled {type(exc).__name__}: {exc}\n{traceback.format_exc()[:1000]}"
        )
        stats = {}

    combo_end_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    base_run["timestamp_end"] = combo_end_iso + "Z"
    elapsed = int(time.monotonic() - combo_start_t)

    # 9. Parse docker logs over the combo window
    try:
        log_text = fetch_container_logs(CONTAINER_NAME, combo_start_iso)
        log_signals = parse_logs(log_text)
    except Exception as exc:
        log_text = ""
        log_signals = {}
        base_run["issue_notes"] = (
            (base_run.get("issue_notes") or "")
            + f" | log fetch failed: {exc}"
        ).strip(" |")

    # 10. Compose the canonical run dict
    base_run.update({
        # Pipeline stats from /api/stats
        "documents": stats.get("document_count", 0),
        "chunks": stats.get("chunk_count", 0),
        "entities": stats.get("entity_count", 0),
        "relationships_total": stats.get("relationship_count", 0),
        "per_chunk_relationships": stats.get("per_chunk_relationship_count", 0),
        "cross_doc_relationships": (
            stats.get("relationship_count", 0)
            - stats.get("per_chunk_relationship_count", 0)
        ),
        "err": stats.get("entity_relationship_ratio", 0.0),
        "communities": stats.get("community_count", 0),
        # Entity type breakdown
        "type_concept": stats.get("entity_type_counts", {}).get("Concept", 0),
        "type_person": stats.get("entity_type_counts", {}).get("Person", 0),
        "type_product": stats.get("entity_type_counts", {}).get("Product", 0),
        "type_organization": stats.get("entity_type_counts", {}).get("Organization", 0),
        "type_technology": stats.get("entity_type_counts", {}).get("Technology", 0),
        "type_event": stats.get("entity_type_counts", {}).get("Event", 0),
        "type_process": stats.get("entity_type_counts", {}).get("Process", 0),
        "type_location": stats.get("entity_type_counts", {}).get("Location", 0),
        "type_system": stats.get("entity_type_counts", {}).get("System", 0),
        "type_document": stats.get("entity_type_counts", {}).get("Document", 0),
        # Log signals
        **{k: v for k, v in log_signals.items() if isinstance(v, (int, type(None)))},
        # Wall-clock fallback if log timestamps missing
        "duration_total_sec": log_signals.get("duration_total_sec") or elapsed,
        "phase_a_sec": log_signals.get("phase_a_sec") or 0,
        "phase_b_sec": log_signals.get("phase_b_sec") or 0,
        "step_3_sec": log_signals.get("step_3_sec") or 0,
    })

    # 10b. Q+A aggregates (latency + answered/errors). Score columns are
    # filled by the end-of-batch judge pass; populate them with 0.0 here so
    # the .ods row is well-formed even when --skip-llm-review is set.
    try:
        from qa_evaluator import summarise_answers
    except Exception:  # noqa: BLE001 — fallback for first-run import races
        summarise_answers = None  # type: ignore[assignment]
    base_run["qa_questions_count"] = len(qa_questions or [])
    for mode in ("speed", "quality"):
        answers = qa_run_data.get(mode, []) or []
        if summarise_answers is not None:
            agg = summarise_answers(answers)
        else:
            agg = {"answered": 0, "errors": 0, "avg_latency_ms": 0}
        base_run[f"qa_{mode}_answered"] = agg["answered"]
        base_run[f"qa_{mode}_errors"] = agg["errors"]
        base_run[f"qa_{mode}_avg_latency_ms"] = agg["avg_latency_ms"]
        for dim in ("faithfulness", "completeness", "groundedness", "conciseness"):
            base_run[f"qa_{mode}_{dim}"] = 0.0
        base_run[f"qa_{mode}_summary"] = ""

    # 11. Heuristic analysis fields
    apply_heuristics(base_run, errored=errored, timed_out=timed_out)

    # 12. Persist
    run_json_path = RUNS_DIR / f"{run_id}.json"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_json_path.write_text(json.dumps(base_run, indent=2), encoding="utf-8")
    print(
        f"[combo {combo_id}] verdict={base_run['verdict']} "
        f"entities={base_run['entities']} rels={base_run['relationships_total']} "
        f"err={base_run['err']:.2f} ({elapsed}s)",
        file=sys.stderr,
    )

    # 13. Append row to .ods
    try:
        subprocess.run(
            [sys.executable, str(ODS_BUILDER), str(run_json_path)],
            check=True,
            cwd=ODS_BUILDER.parent,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[ods] append failed for {run_id}: {exc}", file=sys.stderr)

    # 14. Refresh the static dashboard so a browser tab reload reflects this combo
    _refresh_dashboard()

    return base_run


# ---------------------------------------------------------------------------
# Batch entry-point
# ---------------------------------------------------------------------------

async def safety_backup_if_needed(
    cx: CortexClient,
    batch_id: str,
    *,
    skip: bool = False,
) -> Optional[Path]:
    """Pre-batch safety net: export the current Cortex state to a gitignored
    ZIP if any documents or entities exist.

    The orchestrator resets the DB between combos, which would otherwise
    silently destroy an operator's personal Cortex data. This runs ONCE per
    batch, before the first combo's reset.

    Returns the ZIP path on success, or None if skipped / nothing to back up.
    """
    if skip:
        print("[safety] --no-safety-backup set; skipping pre-reset export", file=sys.stderr)
        return None
    try:
        stats = await cx.stats()
    except CortexError as exc:
        print(
            f"[safety] Could not query /api/stats ({exc}); skipping backup.",
            file=sys.stderr,
        )
        return None
    docs = stats.get("document_count", 0) or 0
    ents = stats.get("entity_count", 0) or 0
    if docs == 0 and ents == 0:
        print(
            "[safety] Local Cortex has no documents/entities — backup skipped.",
            file=sys.stderr,
        )
        return None

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUPS_DIR / f"cortex-state-{batch_id}.zip"
    print(
        f"[safety] Existing state detected ({docs} docs, {ents} entities). "
        f"Exporting to {backup_path.relative_to(REPO_ROOT)} before reset…",
        file=sys.stderr,
    )
    try:
        result = await cx.export_library_to_zip(backup_path)
    except CortexError as exc:
        print(
            f"[safety] Export FAILED: {exc}. Aborting batch — fix the export "
            "endpoint or pass --no-safety-backup if you accept the data loss.",
            file=sys.stderr,
        )
        raise
    size_mb = (result.get("file_size") or 0) / (1024 * 1024)
    print(f"[safety] Backup complete: {size_mb:.1f} MB. Restore via:", file=sys.stderr)
    print(
        f"[safety]   curl -F 'file=@{backup_path}' "
        "-H \"X-API-Key: $ADMIN_API_KEY\" "
        "'http://localhost:8000/api/admin/import?mode=replace'",
        file=sys.stderr,
    )
    return backup_path


async def run_batch(
    combos: list[dict],
    *,
    dry_run: bool = False,
    skip_llm_review: bool = False,
    skip_safety_backup: bool = False,
    skip_live_progress: bool = False,
    skip_qa_eval: bool = False,
    qa_count: int = QA_BANK_DEFAULT_COUNT,
    qa_modes: tuple[str, ...] = ("speed", "quality"),
    qa_quality_count: int = QA_QUALITY_SUBSET_DEFAULT,
) -> None:
    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    print(f"[batch] starting batch {batch_id} with {len(combos)} combo(s)", file=sys.stderr)

    # Acquire the cross-process batch lock BEFORE touching .env or the container.
    # If another bench batch is already running, we want to refuse immediately
    # with a clean error — concurrent batches race on `docker compose recreate`
    # and corrupt each other's runs.
    batch_lock: Optional[BatchLock] = None
    if not dry_run:
        batch_lock = BatchLock(batch_id)
        batch_lock.acquire()  # raises BatchLockError → caught in main()

    # Backup .env once for the whole batch (no-op if dry_run, but harmless).
    if not dry_run:
        swapper = EnvSwapper(ENV_PATH, batch_id)
        admin_key = swapper.admin_key()
    else:
        swapper = None
        admin_key = "dry-run-key"

    # Pre-batch safety: if the local Cortex has existing data, export it to a
    # gitignored ZIP before any combo's reset can destroy it.
    if not dry_run:
        try:
            async with CortexClient(admin_key=admin_key) as preflight:
                await preflight.wait_until_ready(timeout_s=30.0)
                await safety_backup_if_needed(preflight, batch_id, skip=skip_safety_backup)
        except CortexError as exc:
            print(
                f"[safety] Preflight could not reach backend ({exc}). "
                "Backend may not be running yet. Skipping pre-batch backup.",
                file=sys.stderr,
            )

    batch_log = LOGS_DIR / f"batch_log_{batch_id}.jsonl"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-batch question generation — ONE LLM call against the operator's
    # primary model that produces a question bank reused across every combo.
    # Cached under bench/logs/qa-bank-<batch_id>.json so re-running the same
    # batch_id reuses the same questions. Failure here is non-fatal: we
    # continue with qa_questions=None and per-combo Q+A becomes a no-op.
    qa_questions: Optional[list[dict]] = None
    if not dry_run and not skip_qa_eval and swapper is not None:
        try:
            from qa_evaluator import generate_question_bank
            primary_cfg = swapper.primary_model_config()
            if not (primary_cfg["model"] and primary_cfg["base_url"] and primary_cfg["api_key"]):
                print(
                    "[qa] OPENAI_* primary config missing in .env backup; "
                    "skipping question generation.",
                    file=sys.stderr,
                )
            else:
                qa_bank_path = LOGS_DIR / f"qa-bank-{batch_id}.json"
                print(
                    f"[qa] generating question bank ({qa_count} questions) "
                    f"using {primary_cfg['model']!r}…",
                    file=sys.stderr,
                )
                qa_questions = await generate_question_bank(
                    BENCH_FILES_DIR,
                    primary_cfg,
                    count=qa_count,
                    cache_path=qa_bank_path,
                )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[qa] question generation failed: {exc} — "
                "Q+A phases will be skipped for this batch.",
                file=sys.stderr,
            )
            qa_questions = None

    # Refresh the dashboard once at batch start so a browser tab opened now
    # reflects the pre-batch state (or empty if nothing has ever run).
    _refresh_dashboard()

    runs: list[dict] = []
    total = len(combos)
    try:
        for idx, combo in enumerate(combos, start=1):
            print(f"\n[batch] === combo {combo['id']} ({idx}/{total}) ===", file=sys.stderr)

            # Per-combo live state (None when dry-run or operator skipped live)
            live: Optional[LiveState] = None
            if not dry_run and not skip_live_progress:
                now = datetime.now(timezone.utc).isoformat()
                live = LiveState(
                    batch_id=batch_id,
                    combo_id=combo["id"],
                    combo_index=idx,
                    combo_total=total,
                    started_at=now,
                    phase_started_at=now,
                    primary_model=combo["primary"]["model_id"],
                    extraction_model=combo["extraction"]["model_id"],
                    relationship_model=combo["relationship"]["model_id"],
                )

            try:
                async with _ticker_context(
                    live, admin_key, enabled=not dry_run and not skip_live_progress
                ):
                    run = await asyncio.wait_for(
                        run_combo(
                            combo,
                            admin_key=admin_key,
                            batch_id=batch_id,
                            dry_run=dry_run,
                            live=live,
                            qa_questions=qa_questions,
                            qa_modes=qa_modes,
                            qa_quality_count=qa_quality_count,
                        ),
                        timeout=PER_COMBO_TIMEOUT_S,
                    )
            except asyncio.TimeoutError:
                print(f"[combo {combo['id']}] TIMEOUT after {PER_COMBO_TIMEOUT_S}s", file=sys.stderr)
                # Write a stub timed-out row using the resolved combo's tier dicts
                primary = combo["primary"]
                extraction = combo["extraction"]
                relationship = combo["relationship"]
                stub = {
                    "run_id": f"{batch_id}_{combo['id']}",
                    "batch_id": batch_id,
                    "combo_id": combo["id"],
                    "primary_model": primary["model_id"],
                    "extraction_model": extraction["model_id"],
                    "relationship_model": relationship["model_id"],
                    "primary_base": primary["base_url"],
                    "extraction_base": extraction["base_url"],
                    "relationship_base": relationship["base_url"],
                    "primary_family": primary.get("family", ""),
                    "extraction_family": extraction.get("family", ""),
                    "relationship_family": relationship.get("family", ""),
                    "primary_context_length": primary["context_length"],
                    "extraction_context_length": extraction["context_length"],
                    "relationship_context_length": relationship["context_length"],
                    "extraction_reasoning_mode": combo.get("extraction_reasoning_mode", "off"),
                    "relationship_reasoning_mode": combo.get("relationship_reasoning_mode", "off"),
                    "default_reasoning_mode": combo.get("default_reasoning_mode", "auto"),
                    "reasoning_model_overrides": "",
                    "duration_total_sec": PER_COMBO_TIMEOUT_S,
                    "issue_notes": f"Combo timed out after {PER_COMBO_TIMEOUT_S}s.",
                }
                apply_heuristics(stub, timed_out=True)
                # Persist the stub to disk so the dashboard / .ods reflects
                # the timeout. Previously this row was in-memory only — the
                # only trace of a timed-out combo was the stderr line.
                try:
                    stub_path = RUNS_DIR / f"{stub['run_id']}.json"
                    RUNS_DIR.mkdir(parents=True, exist_ok=True)
                    stub_path.write_text(json.dumps(stub, indent=2), encoding="utf-8")
                    subprocess.run(
                        [sys.executable, str(ODS_BUILDER), str(stub_path)],
                        check=False,
                        cwd=ODS_BUILDER.parent,
                    )
                except Exception as persist_exc:  # noqa: BLE001
                    print(f"[combo {combo['id']}] failed to persist timeout stub: "
                          f"{persist_exc}", file=sys.stderr)
                runs.append(stub)
                continue
            runs.append(run)
            with batch_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"combo_id": combo["id"], "verdict": run["verdict"]}) + "\n")

        # End-of-batch LLM review
        if runs and not dry_run and not skip_llm_review:
            print(f"\n[batch] running LLM review pass over {len(runs)} run(s)…", file=sys.stderr)
            try:
                from llm_review import (
                    apply_review_to_runs,
                    review_batch,
                    write_findings_md,
                )

                # Build log windows (last 200 lines per run)
                log_windows: dict[str, str] = {}
                for run in runs:
                    try:
                        log_text = fetch_container_logs(
                            CONTAINER_NAME,
                            (run.get("timestamp_start") or "").replace("Z", ""),
                        )
                        log_windows[run["run_id"]] = "\n".join(
                            log_text.splitlines()[-200:]
                        )
                    except Exception:
                        log_windows[run["run_id"]] = ""

                # Use the operator's ORIGINAL primary-model config (from the
                # pre-batch .env backup) — not whatever value the last combo's
                # apply_combo_to_env left behind in the live .env.
                if swapper is None:
                    raise RuntimeError("LLM review needs the EnvSwapper backup; cannot run in dry-run mode.")
                primary_cfg = swapper.primary_model_config()
                print(
                    f"[review] using primary model {primary_cfg['model']!r} "
                    f"at {primary_cfg['base_url']}",
                    file=sys.stderr,
                )

                review = await review_batch(
                    runs,
                    log_windows=log_windows,
                    **primary_cfg,
                )
                runs = apply_review_to_runs(runs, review)

                # Re-write each run JSON with the merged review fields
                for run in runs:
                    run_path = RUNS_DIR / f"{run['run_id']}.json"
                    run_path.write_text(json.dumps(run, indent=2), encoding="utf-8")

                # Rebuild the .ods from all runs in RUNS_DIR (so updated fields land).
                _rebuild_ods()

                findings_path = LOGS_DIR / f"findings_{batch_id}.md"
                write_findings_md(review.get("code_optimisation_findings", ""), findings_path)
                print(f"[batch] findings written to {findings_path}", file=sys.stderr)

                # Final dashboard refresh — now includes LLM observations + findings
                _refresh_dashboard()
            except Exception as exc:
                print(f"[batch] LLM review failed: {exc}", file=sys.stderr)
                traceback.print_exc()

        # End-of-batch Q+A judge pass — independent of the LLM review above.
        # Scores every (run × mode × question), aggregates per (run × mode),
        # merges back into each run JSON, rebuilds .ods, writes findings md.
        if (
            runs
            and not dry_run
            and not skip_qa_eval
            and qa_questions
            and swapper is not None
        ):
            print(
                f"\n[batch] running Q+A judge pass over {len(runs)} run(s)…",
                file=sys.stderr,
            )
            try:
                from qa_evaluator import (
                    apply_qa_scores_to_runs,
                    judge_answers,
                    write_qa_findings_md,
                )

                # Reload raw answers from disk (the source of truth — written
                # by run_combo step 9). Skips runs that didn't reach Q+A.
                qa_runs_dir = LOGS_DIR / "qa-runs"
                qa_by_run: dict[str, dict] = {}
                for run in runs:
                    rid = run.get("run_id", "")
                    qa_file = qa_runs_dir / f"{rid}.json"
                    if not qa_file.exists():
                        continue
                    try:
                        qa_payload = json.loads(qa_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        print(
                            f"[qa] could not load {qa_file.name}: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    qa_by_run[rid] = {
                        "speed": qa_payload.get("speed", []) or [],
                        "quality": qa_payload.get("quality", []) or [],
                    }

                if not qa_by_run:
                    print("[qa] no qa-runs files found — skipping judge pass.",
                          file=sys.stderr)
                else:
                    primary_cfg = swapper.primary_model_config()
                    print(
                        f"[qa] judging with primary model {primary_cfg['model']!r} "
                        f"at {primary_cfg['base_url']}",
                        file=sys.stderr,
                    )
                    judge_output = await judge_answers(
                        qa_by_run, qa_questions, primary_cfg
                    )
                    runs = apply_qa_scores_to_runs(
                        runs,
                        qa_by_run,
                        judge_output,
                        questions_count=len(qa_questions),
                    )

                    # Re-write each run JSON with merged Q+A scores.
                    for run in runs:
                        run_path = RUNS_DIR / f"{run['run_id']}.json"
                        run_path.write_text(
                            json.dumps(run, indent=2), encoding="utf-8"
                        )

                    # Rebuild the .ods (it now has the Q+A section columns).
                    _rebuild_ods()

                    qa_findings_path = LOGS_DIR / f"qa-findings-{batch_id}.md"
                    write_qa_findings_md(runs, qa_findings_path)
                    print(f"[qa] findings written to {qa_findings_path}",
                          file=sys.stderr)

                    _refresh_dashboard()
            except Exception as exc:
                print(f"[qa] judge pass failed: {exc}", file=sys.stderr)
                traceback.print_exc()

    finally:
        if swapper:
            swapper.restore()
        # Mark live-state as idle so the dashboard hides the Live card.
        if not dry_run and not skip_live_progress:
            last_combo_id = runs[-1]["combo_id"] if runs else None
            _write_live_idle(last_combo_id=last_combo_id)
            _refresh_dashboard()
        # Release the cross-process batch lock LAST — everything else cleaned up.
        if batch_lock:
            batch_lock.release()
        print(f"\n[batch] {batch_id} complete. Runs: {len(runs)}", file=sys.stderr)


def _refresh_dashboard() -> None:
    """Rebuild bench/logs/dashboard-data.js for the static index.html viewer.

    Silently swallows errors — a dashboard glitch must never abort a bench
    batch. Operators refresh `bench/index.html` in the browser to see the
    latest state; this function makes sure the data file reflects current
    run JSONs + findings.
    """
    try:
        write_dashboard_data(build_dashboard_data())
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"[dashboard] refresh skipped: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Live progress — `bench/logs/.bench-live.json` + periodic ticker
# ---------------------------------------------------------------------------

LIVE_PATH = LOGS_DIR / ".bench-live.json"
LIVE_TICK_INTERVAL_S = 15.0


@dataclass
class LiveState:
    """Mutable per-combo state shared with the live ticker."""
    batch_id: str
    combo_id: str
    combo_index: int
    combo_total: int
    started_at: str
    phase: str = "A"                 # A | B | step_3 | qa_speed | qa_quality | done
    phase_started_at: str = ""
    primary_model: str = ""
    extraction_model: str = ""
    relationship_model: str = ""
    warnings: list = field(default_factory=list)

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.phase_started_at = datetime.now(timezone.utc).isoformat()


def _write_live_state_payload(payload: dict) -> None:
    """Atomically write the live-state JSON file. Errors swallowed."""
    LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = LIVE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(LIVE_PATH)
    except OSError as exc:
        print(f"[live] could not write {LIVE_PATH.name}: {exc}", file=sys.stderr)


async def _write_live_state(live: LiveState, cx: CortexClient) -> None:
    """Snapshot the live state — stats + recent docker events — into the JSON file."""
    try:
        stats = await cx.stats()
    except CortexError as exc:
        stats = {}
        live.warnings.append(f"stats unreachable: {exc}")
    try:
        recent = tail_recent_events(CONTAINER_NAME, live.started_at, max_events=30)
    except Exception as exc:  # noqa: BLE001
        recent = []
        live.warnings.append(f"log tail failed: {exc}")
    payload = {
        "active": True,
        "batch_id": live.batch_id,
        "combo_id": live.combo_id,
        "combo_index": live.combo_index,
        "combo_total": live.combo_total,
        "started_at": live.started_at,
        "phase": live.phase,
        "phase_started_at": live.phase_started_at or live.started_at,
        "primary_model": live.primary_model,
        "extraction_model": live.extraction_model,
        "relationship_model": live.relationship_model,
        "stats": stats,
        "recent_events": recent,
        "warnings": list(live.warnings),
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_live_state_payload(payload)


def _write_live_idle(last_combo_id: Optional[str] = None) -> None:
    """Mark the live-state file as idle so the dashboard hides the Live card."""
    _write_live_state_payload({
        "active": False,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "last_combo_id": last_combo_id,
    })


async def _live_ticker(
    live: LiveState, cx: CortexClient, stop_event: asyncio.Event
) -> None:
    """Tick every LIVE_TICK_INTERVAL_S seconds: write live state + refresh dashboard.

    Exits cleanly when `stop_event` is set. Never raises — a tick failure must
    not abort the bench batch.
    """
    while not stop_event.is_set():
        try:
            await _write_live_state(live, cx)
            _refresh_dashboard()
        except Exception as exc:  # noqa: BLE001
            print(f"[live] tick failed (continuing): {exc}", file=sys.stderr)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LIVE_TICK_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def _ticker_context(
    live: Optional[LiveState], admin_key: str, *, enabled: bool
) -> AsyncIterator[None]:
    """Run the live-ticker task alongside the body of the context.

    No-op if `live is None` or `enabled is False`. Always cleans up the
    ticker + the dedicated CortexClient, even on exceptions / cancellation.
    """
    if not (live and enabled):
        yield
        return
    stop_event = asyncio.Event()
    cx = CortexClient(admin_key=admin_key)
    # Tick once immediately so the live card shows up before the first 15s interval.
    try:
        await _write_live_state(live, cx)
        _refresh_dashboard()
    except Exception as exc:  # noqa: BLE001
        print(f"[live] first tick failed (continuing): {exc}", file=sys.stderr)
    task = asyncio.create_task(_live_ticker(live, cx, stop_event))
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        try:
            await cx.aclose()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def _rebuild_ods() -> None:
    """Wipe and rebuild logs/llm-config-results.ods from every run JSON in RUNS_DIR."""
    if ODS_PATH.exists():
        ODS_PATH.unlink()
    for jf in sorted(RUNS_DIR.glob("*.json")):
        subprocess.run(
            [sys.executable, str(ODS_BUILDER), str(jf)],
            check=False,
            cwd=ODS_BUILDER.parent,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def ensure_local_configs() -> None:
    """Copy *.yaml.example → *.yaml on first run so a fresh clone has a working config.

    The .example files are committed to git; the .yaml files are local-only
    (gitignored) so operators can edit freely — add literal API keys, prune
    combos, etc. — without leaking secrets into the repo.
    """
    for name in ("models.yaml", "combos.yaml"):
        local = BENCH_DIR / name
        example = BENCH_DIR / f"{name}.example"
        if not local.exists() and example.exists():
            shutil.copy2(example, local)
            print(
                f"[init] Created {local.relative_to(REPO_ROOT)} "
                f"from {example.name}. Review/edit before next run.",
                file=sys.stderr,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--combos", type=Path, default=COMBOS_PATH, help="Path to combos.yaml"
    )
    parser.add_argument(
        "--models",
        type=Path,
        default=BENCH_DIR / "models.yaml",
        help="Path to models.yaml (the registry)",
    )
    parser.add_argument(
        "--only",
        help="Run only the combo whose id contains this substring.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan; do not change env, recreate container, or call APIs.",
    )
    parser.add_argument(
        "--skip-llm-review",
        action="store_true",
        help=(
            "Skip the end-of-batch LLM review pass (which uses the primary "
            "model from .env to fill observations + cross-run findings)."
        ),
    )
    parser.add_argument(
        "--no-safety-backup",
        action="store_true",
        help=(
            "Skip the pre-batch Cortex state export. Use only when you're "
            "certain you don't need to preserve current Cortex data "
            "(e.g. you just ran a bench and the DB is already empty)."
        ),
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help=(
            "Disable the periodic live-progress writer. The dashboard still "
            "refreshes at batch start / after each combo / after LLM review, "
            "but the in-flight 'Live now' card won't update during a combo."
        ),
    )
    parser.add_argument(
        "--skip-qa-eval",
        action="store_true",
        help=(
            "Skip the Q+A retrieval evaluation entirely — no question bank "
            "generated, no per-combo qa_speed/qa_quality phases, no judge "
            "pass. The qa_* columns stay zeroed."
        ),
    )
    parser.add_argument(
        "--qa-count",
        type=int,
        default=QA_BANK_DEFAULT_COUNT,
        help=(
            f"Number of questions in the bank (default {QA_BANK_DEFAULT_COUNT}, "
            "range 5–30). Ignored when --skip-qa-eval is set."
        ),
    )
    parser.add_argument(
        "--qa-modes",
        default="speed,quality",
        help=(
            "Comma-separated list of Q+A modes to run per combo. "
            "Choices: speed, quality. Default: speed,quality."
        ),
    )
    parser.add_argument(
        "--qa-quality-count",
        type=int,
        default=QA_QUALITY_SUBSET_DEFAULT,
        help=(
            f"Quality (deep research) mode runs on the first N questions of "
            f"the bank instead of all of them (default {QA_QUALITY_SUBSET_DEFAULT}). "
            "Quality mode is the slow arm (~60-180s/q); subsetting keeps wall "
            "time bounded. Pass 0 to run quality on the full bank."
        ),
    )
    args = parser.parse_args()

    qa_count = max(5, min(30, args.qa_count))
    qa_quality_count = max(0, min(qa_count, args.qa_quality_count))
    qa_modes = tuple(
        m.strip() for m in args.qa_modes.split(",")
        if m.strip() in ("speed", "quality")
    ) or ("speed", "quality")

    # First-run UX: seed local YAMLs from .example templates if missing.
    ensure_local_configs()

    # Resolve combos UP-FRONT — fails fast on unknown model IDs or missing keys
    # BEFORE we ever touch .env or the container.
    try:
        resolved_combos = load_all(
            models_path=args.models,
            combos_path=args.combos,
            only=args.only,
        )
    except ResolverError as e:
        sys.exit(f"Configuration error: {e}")

    try:
        asyncio.run(
            run_batch(
                resolved_combos,
                dry_run=args.dry_run,
                skip_llm_review=args.skip_llm_review,
                skip_safety_backup=args.no_safety_backup,
                skip_live_progress=args.no_live,
                skip_qa_eval=args.skip_qa_eval,
                qa_count=qa_count,
                qa_modes=qa_modes,
                qa_quality_count=qa_quality_count,
            )
        )
    except BatchLockError as exc:
        # Clean exit (no traceback) when another batch is already running.
        sys.exit(f"[batch] aborted — {exc}")


if __name__ == "__main__":
    main()
