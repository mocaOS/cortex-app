# Bench harness (`bench/`)

Autonomous LLM-stack benchmark orchestrator. Lives at the repo root in `bench/`. Drives the **local** `cortex-backend` container through the full ingestion pipeline (Phase A → Phase B → Step 3) for each model combination defined in YAML, captures quantitative + heuristic + LLM-generated qualitative results, and aggregates them into `bench/logs/llm-config-results.ods` plus a markdown findings report.

> **Public status (important).** This subsystem is an **isolated commit, not yet publicly documented**. The main `README.md`, anything under `documentation/`, `handbook/`, and the public-facing layers of the project deliberately do NOT mention `bench/`. When making changes here, do **not** add references to the bench in those docs. Update this file and `bench/README.md` only.

## What it is, in one paragraph

Operator drops `.md` docs into `bench/files/`, picks combos in `bench/combos.yaml` (each combo references three model entries from `bench/models.yaml` — primary / extraction / relationship tiers), runs `python bench/run_bench.py`. The orchestrator iterates combos sequentially: rewrites `.env` with per-tier model/base_url/key/context env vars, `docker compose up -d backend --force-recreate`, resets the DB via `/api/admin/reset`, uploads files, drives the pipeline through the existing FastAPI endpoints, parses `docker logs` for signal counts and phase timings, applies a heuristic decision tree for verdict + failure_patterns, writes a per-combo JSON, appends a row to the master `.ods`. After all combos finish, one Claude API call reads the batch and writes the free-form `observations` / `vs_previous_run` / `code_optimisation_findings` fields back into each row.

## Two-file YAML config split

| File | Status | Content |
|---|---|---|
| `bench/models.yaml.example` | **committed** | Public template — the curated model registry (currently 58 entries spanning Venice's open-weight matrix + proprietary passthroughs). Safe to edit and PR. |
| `bench/models.yaml` | **gitignored** | Operator's local copy. Edit freely — including pasting literal `api_key:` values. Auto-created from `.example` on first run. |
| `bench/combos.yaml.example` | **committed** | 12-combo curated matrix from the Venice catalog audit. |
| `bench/combos.yaml` | **gitignored** | Operator's active combo list. Seeded with 3 session replays + a qwen3-6-27b test. |

`bench/run_bench.py:ensure_local_configs()` (called from `main()`) copies any missing `*.yaml` from its `.example` sibling on first run and logs `[init] Created bench/<name>.yaml from <name>.yaml.example`.

Each model entry must define: `model_id`, `base_url`, `context_length`, plus exactly one of `api_key` (literal) or `api_key_env` (env var name). Optional metadata: `family`, `reasoning_capable`, `max_output_tokens`, `note`. See `bench/combo_resolver.py` for full validation rules.

## Provider-agnostic dispatch

Each model entry's `base_url` determines which reasoning-suppression strategy `backend/app/services/reasoning_config.py:build_reasoning_kwargs()` applies inside Cortex. The bench harness only rewrites env vars — it does NOT inject reasoning flags itself.

| Detected backend | Reasoning-OFF dispatch |
|---|---|
| `api.openai.com` | top-level `reasoning_effort` (`"none"` for GPT-5.1+, `"minimal"` for GPT-5.0, `"low"` for o-series) |
| `openrouter.ai` | `extra_body.reasoning.effort = "none"` (or `"minimal"` for original GPT-5) |
| `venice.ai` | `extra_body.venice_parameters.disable_thinking = true` |
| `api.anthropic.com` | `extra_body.thinking = {"type": "disabled"}` — omit for Opus 4.7+ (returns 400) |
| anything else (vLLM, self-host) | `extra_body.chat_template_kwargs.{enable_thinking,thinking} = false` |

This is the same dispatch table that backs Cortex's `EXTRACTION_REASONING_MODE` / `RELATIONSHIP_REASONING_MODE` env knobs; the bench just exercises it across many combos.

## Module map

| File | Purpose |
|---|---|
| `bench/run_bench.py` | Main orchestrator + CLI. `EnvSwapper` for `.env` lifecycle, `safety_backup_if_needed` preflight, per-combo `run_combo`, end-of-batch LLM review trigger. |
| `bench/combo_resolver.py` | Loads `models.yaml` + `combos.yaml`, validates schema, resolves combo tier refs to full model dicts, resolves `api_key_env` from shell env / `.env`. Fails fast on malformed config BEFORE any side effects. |
| `bench/cortex_client.py` | Async `httpx`-based HTTP wrapper for every Cortex endpoint the harness drives: `/health`, `/api/admin/reset`, `/api/upload`, `/api/documents/process-pending`, `/api/graph/relationships/analyze`, `/api/graph/communities/detect`, `/api/tasks/{id}`, `/api/stats`, `/api/admin/export` (+ task download). Raises `CortexError` on non-2xx. |
| `bench/log_parser.py` | Pure function: `parse_logs(docker_log_text) → dict` with signal counts (empty_content_length, candidate_scan_empty, gleaning_passes, etc.) + phase A/B/3 timestamps. |
| `bench/heuristics.py` | Pure function: `apply_heuristics(run_dict, errored=False, timed_out=False)` fills `verdict`, `failure_patterns`, `performance_notes`, `recommendation`. Family-aware (qwen / minimax / openai_gpt_oss get specific hints). |
| `bench/llm_review.py` | One-shot Anthropic SDK call at end of batch. System prompt is `cache_control:ephemeral` for prompt caching. Returns per-run `observations`/`vs_previous_run` + a single top-level `code_optimisation_findings` markdown block. |
| `bench/qa_evaluator.py` | Q+A retrieval evaluation. `generate_question_bank()` runs once per batch from `bench/files/`. `run_question_set(cx, q, mode=…)` posts to `/api/ask` (speed=non-agentic, quality=agentic). `judge_answers()` is one end-of-batch LLM call scoring every (run × mode × question) on faithfulness/completeness/groundedness/conciseness 1–5. `apply_qa_scores_to_runs()` merges aggregates back. |
| `bench/_llm_io.py` | Shared `chat_completion()` helper + `parse_json_response()`. Lifted out of `llm_review.py` so `qa_evaluator.py` doesn't duplicate the `response_format` 400-retry + `<think>` block stripping. |
| `bench/run_qa_bench.py` | **Standalone Q+A *chat* benchmark** (separate entrypoint from `run_bench.py`). Holds the graph FIXED, swaps only the chat model (`OPENAI_MODEL` + `OPENAI_MAX_CONTEXT`) per candidate, and scores snappiness + quality. Reuses `EnvSwapper`/`BatchLock`/`recreate_backend` from `run_bench.py` and `judge_answers` from `qa_evaluator.py`. See the dedicated section below. |
| `bench/qa_snappiness.py` | Engine for `run_qa_bench.py`: graph-sourced question generation (`generate_question_bank_from_graph`), streaming latency capture (`stream_question` → TTFT/total/timeout/overthinking), aggregation, scoring/flagging, and the leaderboard markdown report. |
| `bench/qa_models.yaml(.example)` | Q+A-benchmark model list (committed `.example`, gitignored local). A flat list of chat-model candidates; `context` pinned the same for all; `base_url`/`api_key` default to the operator's live `OPENAI_API_BASE`/`OPENAI_API_KEY`. NOT the 3-tier `models.yaml`. |
| `bench/build_results_ods.py` | `odfpy`-based spreadsheet writer. Reads a flat-dict JSON, appends a row to `bench/logs/llm-config-results.ods` (or `$BENCH_ODS_PATH`). |
| `bench/build_dashboard.py` | Idempotent aggregator. Scans `bench/logs/runs/*.json` + latest `findings_*.md` → emits `bench/logs/dashboard-data.js` declaring `window.BENCH_DATA = {…}`. Called by the orchestrator 3× per batch and runnable manually. |
| `bench/index.html` | Static dashboard page. Cortex-branded inline CSS, Chart.js + marked.js via CDN. Loads `logs/dashboard-data.js` via a relative `<script>` tag (works under `file://` and `http://`). Empty state when no runs; otherwise renders hero, verdict donut, ERR bars, phase-timing stacks, failure heatmap, per-combo cards (with LLM observations), and cross-run findings markdown. |
| `bench/test_heuristics.py` | Sanity test against the three historical session run JSONs at `bench/logs/runs/run_2026-05-20_*.json`. The only automated test in the harness. |

## Per-combo execution loop

In `bench/run_bench.py:run_combo()`:

1. Apply combo's resolved model dicts to `.env` (13 env vars: 3 tiers × 4 fields + 3 reasoning modes + optional `RELATIONSHIP_MAX_OUTPUT_TOKENS`). See `apply_combo_to_env()`.
2. `docker compose up -d backend --force-recreate`. **Critical:** `restart` does NOT re-read `env_file`; only `up --force-recreate` does.
3. Poll `GET /health` until `status=="healthy"` and `neo4j_connected==true`. 120 s cap.
4. `POST /api/admin/reset` with `delete_documents/uploaded_files/custom_inputs/collections=true`.
5. Upload every `.md` in `bench/files/` via `POST /api/upload?start_processing=false`.
6. `POST /api/documents/process-pending` (Phase A) → poll `/api/stats` until all docs leave PENDING/PROCESSING.
7. `POST /api/graph/relationships/analyze` (Phase B) → poll `/api/tasks/{id}` to completion.
8. `POST /api/graph/communities/detect` (Step 3) → poll task.
9. Snapshot `/api/stats` **before Q+A** so ingestion data is captured even if Q+A blows up.
10. Q+A speed mode (if `qa_questions` available) → loop the bank through `cx.ask(use_agentic=False)`; 90 s per-question timeout. Wrapped in try/except — Q+A failures NEVER discard the ingestion data just captured.
11. Q+A quality mode → same bank through `cx.ask(use_agentic=True)`; 300 s per-question timeout (bumped from 180 s; minimax-m27 quality runs empirically need >180 s on harder questions when the researcher loop iterates several times).
12. `docker logs cortex-backend --since <combo_start_iso>` → `log_parser.parse_logs()` → signal counts + phase timings.
13. `heuristics.apply_heuristics()` → verdict / failure_patterns / performance_notes / recommendation.
14. Write `bench/logs/runs/<batch_id>_<combo_id>.json` + raw Q+A answers at `bench/logs/qa-runs/<batch_id>_<combo_id>.json`.
15. Subprocess `build_results_ods.py <json>` to append a row to the master `.ods` (now includes the Q+A Retrieval section — 17 columns).

Hard cap: 75 min per combo (`asyncio.wait_for`). Empirically: ingestion 15-20 min + qa_speed 3-4 min (10 questions) + qa_quality 8-12 min (5-question subset). The earlier 45-min cap timed out mid-qa_quality and discarded the speed-mode answers; bumping the cap + persisting qa-runs after EACH mode fixed both. Timeout stubs are now persisted to disk (previously in-memory only). Any exception inside the combo sets `verdict=ERROR` with the traceback in `issue_notes`, restores `.env`, continues.

## Dashboard data flow

The static dashboard at `bench/index.html` is a pure browser-side page; it consumes a data file written by `bench/build_dashboard.py`. The orchestrator calls `_refresh_dashboard()` at three points so a manual browser refresh always shows the current state:

```
bench/logs/runs/*.json   ─┐
bench/logs/findings_*.md ─┼─→ build_dashboard.py ─→ bench/logs/dashboard-data.js
                          │                                       │
                          │                                       ▼
                          └────────────────────►  bench/index.html (loads via <script>)
```

Refresh trigger points in `run_bench.py`:

1. **`run_batch` start** — before the per-combo loop. Reflects pre-batch state (or empty if first run).
2. **End of `run_combo`** — after the JSON write + `.ods` row append. The combo's new row + verdict show up immediately on next browser refresh.
3. **After the LLM review pass** — after `apply_review_to_runs` rewrites the JSONs with `observations` + `vs_previous_run` + the new `findings_<batch_id>.md` is written. Final state.
4. **Periodic during a running combo** — the live ticker (see below) calls `_refresh_dashboard()` every 15 s, so a manual reload during a 10-20 min combo shows live progress instead of stale state.

`_refresh_dashboard()` swallows all exceptions — a dashboard glitch must never abort a bench batch. The build is a few-millisecond JSON scan; safe to call frequently. Manual rebuild: `python bench/build_dashboard.py`.

The dashboard is intentionally **not auto-refreshing in the browser**. Operator reloads the tab when they want fresh data (the Refresh button in the header just calls `location.reload()`). This keeps the data flow strictly pull-based and avoids any WebSocket / SSE / polling surface area.

### Live-progress state — `bench/logs/.bench-live.json`

During a running combo, `run_bench.py` writes a small JSON file capturing the current state. `build_dashboard.py` reads it into `BENCH_DATA.live`; `bench/index.html` renders the "● LIVE" card at the top when `live.active === true` and hides it otherwise.

```json
{
  "active": true,
  "batch_id": "2026-05-21_08-02",
  "combo_id": "01-minimax-all-tiers",
  "combo_index": 1, "combo_total": 4,
  "started_at": "2026-05-21T08:02:00Z",
  "phase": "A",                      // A | B | step_3 | done
  "phase_started_at": "2026-05-21T08:02:30Z",
  "primary_model": "minimax-m27",
  "extraction_model": "minimax-m27",
  "relationship_model": "minimax-m27",
  "stats": { /* /api/stats snapshot */ },
  "recent_events": [ /* last 30, newest first */ ],
  "warnings": []
}
```

Components:

- **`LiveState` dataclass** (run_bench.py) — mutable holder; `set_phase()` updates `phase` + `phase_started_at` whenever `run_combo` transitions.
- **`_live_ticker`** — async task that calls `_write_live_state()` then `_refresh_dashboard()` every `LIVE_TICK_INTERVAL_S` (15 s default). Driven by a dedicated `CortexClient` so it doesn't contend with `run_combo`'s client.
- **`_ticker_context`** — async context manager wrapping `run_combo`. Starts the ticker (with one immediate tick so the card appears before the first 15 s interval), guarantees cleanup on exception / timeout / Ctrl-C.
- **`tail_recent_events` in `log_parser.py`** — sibling to `parse_logs`. Reuses the same regexes but emits `{ts, kind, summary}` events instead of tallies. Patterns covered: `doc_summary`, `entity_batch`, `candidate_scan`, `candidate_scan_zero`, `rel_batch`, `community`, `gleaning`, `warning`, `error`, `retry`.
- **Idle state on batch end** — `run_batch`'s `finally` block writes `{"active": false, ...}` and refreshes the dashboard one last time, so the Live card disappears cleanly.

Disable with `--no-live` (no ticker, no `.bench-live.json` writes; existing 3 refresh points still fire).

## Concurrent-batch guard

`bench/run_bench.py:BatchLock` writes a PID-stamped lock file at `bench/logs/.bench-batch.lock` on batch start (atomic `O_CREAT|O_EXCL`) and removes it on exit / SIGINT / SIGTERM. If another batch's PID owns the lock AND that PID is alive, the new invocation refuses with a clean `[batch] aborted — Another bench batch is already running.` message and `sys.exit(2)`. Stale locks from dead PIDs are auto-reclaimed.

Why: two concurrent `python bench/run_bench.py` invocations race on `docker compose up --force-recreate` and the backend's HTTP socket. Empirically observed: batch 08-19 combos 01+02 errored because batch 08-20 started a second later — `RemoteProtocolError: Server disconnected` on one, `CalledProcessError: exit 1` on the other. The lock prevents this race entirely.

Acquired BEFORE `EnvSwapper` and the safety backup, so a refused lock leaves `.env` and the live Cortex state untouched. Released LAST in the `run_batch` `finally` block, after env restore and the live-state idle write.

## Safety backup mechanism

Implemented in `bench/run_bench.py:safety_backup_if_needed()` and `bench/cortex_client.py:export_library_to_zip()`.

Runs **once per batch**, before the first combo:

1. Connect with the admin key; `GET /api/stats`.
2. If `document_count == 0 AND entity_count == 0` → log skip, return.
3. Else: `POST /api/admin/export` → poll task → `GET /api/admin/export/{task_id}/download` (1 MB streamed chunks) → write to `bench/backups/cortex-state-<batch_id>.zip`.
4. Log the restore command the operator can run if needed: `curl -F file=@... -H 'X-API-Key: $ADMIN_API_KEY' '<host>/api/admin/import?mode=replace'`.

The export endpoint is the same one used by the admin UI's "Export library" button (see `.claude/domain/admin-features.md`). ZIP contains: documents (raw), chunks + embeddings, entities, relationships, communities + members, collections + members, chunk-entity mentions, merge history, system meta, skills, manifest.

`--no-safety-backup` flag skips the preflight entirely. If the export endpoint itself fails (Cortex misconfigured, dead container, etc.), the batch aborts unless `--no-safety-backup` is set — better to refuse to proceed than silently lose data.

## End-of-batch LLM review (one call to the primary model)

After all combos finish, `bench/llm_review.py:review_batch()` posts a single message to the **primary OpenAI-compatible model** — whatever the operator has configured via `OPENAI_MODEL` / `OPENAI_API_BASE` / `OPENAI_API_KEY` in `.env`. In the default Venice setup that's MiniMax-M27. On a self-hosted vLLM it might be GPT-OSS-120B. Anywhere with an `/chat/completions` endpoint works.

**Why the primary model, not Claude?** Originally used the Anthropic SDK, but that's a hard install requirement that not every operator has. Switching to the primary tier removed an external dependency: the bench now uses zero extra SDKs beyond `httpx` / `pyyaml` / `odfpy`.

**Where the config comes from:** `EnvSwapper.primary_model_config()` reads the **pre-batch** `.env` backup, NOT the live `.env`. This matters because mid-batch the live file has whatever the last combo's `apply_combo_to_env` wrote (e.g. `OPENAI_MODEL=qwen3-6-27b`). The review explicitly wants the OPERATOR's configured Q&A model, not the bench's transient combo override.

**Robustness measures inside `review_batch`:**
- `response_format: {"type": "json_object"}` requested; if the provider 400s (some don't recognise it), retried without.
- `<think>...</think>` blocks stripped from the response so reasoning models can be used.
- Code-fence wrappers (` ```json ... ``` `) stripped before JSON parse.
- Any malformed response raises `RuntimeError` with the first 500 chars of the response — orchestrator's `except Exception` in `run_batch` logs it as `[batch] LLM review failed: …` and continues; heuristic fields stay populated, free-form fields stay as `<pending LLM review>` placeholders.

Returns JSON: `{ "runs": { "<run_id>": { "observations": "...", "vs_previous_run": "..." } }, "code_optimisation_findings": "<markdown>" }`. The orchestrator merges this back into each run's JSON, rebuilds the `.ods` clean (so analysis fields update in place), writes `bench/logs/findings_<batch_id>.md`.

Skip with `--skip-llm-review` — heuristic fields stay populated, free-form fields stay as placeholders.

## Q+A retrieval evaluation

Lives in `bench/qa_evaluator.py`. Closes the loop on ingestion-only scoring by exercising the full RAG path against the same corpus from each combo.

**Three phases:**

1. **Pre-batch question generation** (`generate_question_bank`) — one LLM call against the operator's primary model (same source as `llm_review.py`: `EnvSwapper.primary_model_config()` reading from the pre-batch `.env` backup). Reads every `.md` under `bench/files/` and emits 15 questions split into factoid / synthesis / thematic buckets. Cached at `bench/logs/qa-bank-<batch_id>.json`; re-running the same batch_id reuses it.

2. **Per-combo evaluation** (`run_question_set`) — runs after Step 3 inside `run_combo`, in two phases:
   - `qa_speed`: every question → `POST /api/ask` with `use_agentic=False`. Chat path. 90 s per-question timeout.
   - `qa_quality`: same bank → `POST /api/ask` with `use_agentic=True`. Researcher agent loop. 300 s per-question timeout.
   - Per-question errors are recorded (`error` field on the answer dict) and the loop continues. A whole-Q+A failure for a combo just zeroes its `qa_*` fields — ingestion stats already captured in step 9 are preserved.

3. **End-of-batch judge pass** (`judge_answers`) — one LLM call across every (run × mode × question) tuple scoring each answer on **faithfulness / completeness / groundedness / conciseness** (1–5 each), plus a 2-sentence summary per (run × mode). `apply_qa_scores_to_runs()` merges aggregates back into each run JSON; raw per-question detail stays at `bench/logs/qa-runs/<run_id>.json`. Cross-run summary at `bench/logs/qa-findings-<batch_id>.md` highlights the top combo per dimension.

**CLI:**

| Flag | Effect |
|---|---|
| `--skip-qa-eval` | Skip all three phases entirely. `qa_*` columns stay zeroed. |
| `--qa-count N` | Override bank size (default 10, clamped to 5–30). |
| `--qa-modes speed,quality` | Comma-list of modes. Use `--qa-modes speed` to drop the slow deep-research arm. |
| `--qa-quality-count N` | Quality mode runs only the first N of the bank (default 5). Quality is the slow arm at ~60-300 s/q. Pass 0 to run quality on the full bank. |

**Run-JSON additions (17 columns surfaced in `.ods`):** `qa_questions_count`, `qa_speed_{answered,errors,avg_latency_ms,faithfulness,completeness,groundedness,conciseness,summary}`, and the matching `qa_quality_*` set.

**Live-state phase names extended:** `A | B | step_3 | qa_speed | qa_quality | done` — the dashboard ticker picks these up automatically once `LiveState.phase` is updated by `run_combo`.

## Q+A chat benchmark (snappiness — standalone, `run_qa_bench.py`)

A SECOND, lighter benchmark answering a different question than the main harness.
`run_bench.py` re-ingests a corpus per combo to score INGESTION; this one holds
the **already-ingested graph fixed** and swaps only the answer model to score the
CHAT experience: latency/snappiness (and overthinking → timeouts) + answer
quality. No re-ingestion, no DB reset — it never mutates the graph.

**Per-model loop** (`run()` in `run_qa_bench.py`):
1. `apply_model_to_env` rewrites only `OPENAI_MODEL`, `OPENAI_API_BASE`,
   `OPENAI_API_KEY`, `OPENAI_MAX_CONTEXT` (context pinned identical for all
   models so the comparison is fair). `--reasoning-mode` optionally sets
   `DEFAULT_REASONING_MODE`; default `leave` keeps the operator's prod setting.
2. `recreate_backend()` + `wait_until_ready` (skip both with `--no-recreate` for
   wiring tests against the running backend).
3. `qa_snappiness.run_snappiness_set` streams the fixed bank through
   `POST /api/ask/stream` (`use_agentic=false`).

**Metrics per (model × question)** — see `qa_snappiness.stream_question`:
`ttft_ms` (request → first `{"content"}` event; the snappiness/overthinking
proxy), `gen_ttft_ms` (generating-status → first content, isolating pure model
TTFT when `STREAM_REASONING_STEPS=true`), `total_ms`, `tokens_per_sec`,
`answer_chars`, and `status` ∈ {ok, over_budget, timeout, incomplete, error}.
`over_budget` = completed but slower than `--budget` (snappy target);
`timeout` = blew the `--hard-cap` wall clock (the deepseek-v4-flash failure).

**Quality** reuses `qa_evaluator.judge_answers` (faithfulness/completeness/
groundedness/conciseness, 1–5), judged by the operator's baseline model.
`score_and_flag` ranks on `combined_score = 0.5·speed_index +
0.5·quality% − timeout_penalty` and flags overthinkers (any timeouts, p95 TTFT
≫ fleet median, or mean total over budget).

**Question bank** is generated ONCE from the live graph
(`generate_question_bank_from_graph`: samples `/api/graph/entities` +
`/api/graph/communities` → the baseline model composes factoid/synthesis/
thematic questions), cached at `logs/qa-chat-bank-<batch>.json`, reused so every
model answers the identical set. (No `bench/files/` corpus needed — the graph IS
the corpus here.)

**Outputs:** `logs/qa-chat-report-<batch>.md` (leaderboard) +
`logs/qa-chat-results-<batch>.json` (per-model rows + raw answers, written
incrementally after each model). `.env` restored and backend recreated back onto
the operator's model in the `finally` block.

**CLI:** `--models a,b,c` (overrides `qa_models.yaml`) · `--count` (bank size,
default 12) · `--budget` (snappy seconds, default 60) · `--hard-cap` (per-Q wall
clock, default 180) · `--context` (default from yaml/198000) · `--reasoning-mode
{leave,off,auto,on}` · `--skip-judge` · `--no-recreate` · `--dry-run`.

**Chat-path snappiness learnings (from the first QA-bench batches).** The
initial batch surfaced widespread *empty* answers (e.g. gemma 8/10, minimax-m27
10/10) and 30–60s+ latencies. Root cause was NOT model quality:
- **Reasoning models stream chain-of-thought in a separate `reasoning_content`
  channel** the chat stream loop ignores. A direct probe (`_probe_thinking.py`)
  showed gemma at TTFT-to-content **13.6s plain → 0.57s with
  `venice_parameters.disable_thinking`** (reasoning_chars 4865 → 0). Across the
  multi-iteration researcher loop this compounded into budget-exhausting
  empties/timeouts.
- **The chat path never suppressed reasoning.** `build_chat_params`
  (`llm_config.py`) only sets tokens/temperature; the suppression machinery
  (`safe_chat_completion` / `build_reasoning_kwargs`) was wired into ingestion
  only. Fix: route the speed-researcher loop + writer (`researcher_agent.py`)
  and the non-agentic + fast streaming writers (`main.py`) through
  `safe_chat_completion` with `DEFAULT_REASONING_MODE` (now default **off** →
  Venice `disable_thinking`). Quality/deep-research stays AUTO. See
  `.claude/environment.md` → `DEFAULT_REASONING_MODE`.
- **Residual snappiness lever = the agent loop, not the model.** With thinking
  off a single model call is <1s, but the speed chat still runs
  `agent_rag_stream(speed)` for up to `RESEARCHER_MAX_ITERATIONS_SPEED` (default
  5) tool-loop rounds + retrieval, so TTFT stays ~15–20s. Lowering that, or
  bypassing the loop when no skills are active, is the next lever.

⚠️ **Heartbeats defeat read timeouts (key gotcha).** `with_sse_heartbeat`
(backend `main.py`) emits `: ping` every 8s, so the httpx read timeout can NEVER
bound a silently-reasoning model — bytes keep arriving at the transport. The
per-question cap MUST be a wall-clock `asyncio.wait_for(_consume(), hard_cap_s)`,
not the transport timeout. `qa_snappiness.stream_question` already does this;
don't regress it to an in-loop elapsed check (that only ticks on yielded events,
and heartbeat lines are filtered out before yielding).

## Output paths

All gitignored under `bench/logs/` or `bench/backups/`:

| Path | Content |
|---|---|
| `bench/logs/runs/<batch_id>_<combo_id>.json` | Per-combo run record (stats + signals + heuristic + LLM review + Q+A aggregate fields) |
| `bench/logs/qa-runs/<batch_id>_<combo_id>.json` | Per-combo raw Q+A answers (speed + quality) with per-question scores after the judge pass |
| `bench/logs/qa-bank-<batch_id>.json` | Generated question bank — one per batch, reused across all combos |
| `bench/logs/qa-findings-<batch_id>.md` | Q+A cross-run summary (top combo per dimension + per-run summaries) |
| `bench/logs/batch_log_<batch_id>.jsonl` | Per-event log (combo_started/completed events) |
| `bench/logs/findings_<batch_id>.md` | LLM-generated cross-run findings (one per batch) |
| `bench/logs/.env.bak.<batch_id>` | `.env` snapshot from batch start; auto-restored on exit |
| `bench/logs/llm-config-results.ods` | Master spreadsheet — all rows from all batches |
| `bench/backups/cortex-state-<batch_id>.zip` | Pre-batch safety export of the operator's Cortex state |

## Heuristic decision tree (canonical reference)

From `bench/heuristics.py`. Cite this here so debugging a verdict doesn't require re-reading the module.

**`verdict`:**

```
if errored OR extraction_timeouts > 5:    verdict = "ERROR"
elif timed_out:                            verdict = "TIMED_OUT"
elif err >= 1.0 AND empty_content_length == 0 AND candidate_scan_empty == 0:
                                           verdict = "GOOD"
elif err >= 0.4:                           verdict = "MIXED"
else:                                      verdict = "POOR"
```

**`failure_patterns`** (multi-line string; each rule appends a line if triggered):

| Trigger | Pattern emitted |
|---|---|
| `empty_content_length / total_calls > 0.10` | "Token-burn: model exhausted max_tokens on suppressed-but-inlined reasoning." |
| `zero_pair_scans / candidate_scans_ok > 0.40` | "Format-adherence: model responded but emitted unparseable output." |
| `0 < per_chunk_relationships < 50` (and total > 0) | "Per-chunk collapse: 2000-token budget insufficient." |
| `extraction_timeouts > 0` | "Transport timeouts on entity extraction." |
| `community_parse_fallback > 1` | "JSON output instability on community-summary calls." |
| `gleaning_passes > 0` | "Gleaning safety net engaged." |
| `relationship_family == "qwen"` AND format-adherence triggered | family hint: "Qwen3-family models show known first-pass format-adherence variance under venice_parameters.disable_thinking." |
| `relationship_family == "minimax"` | family hint: "MiniMax inlines `<think>` despite disable_thinking; AVOID for relationship tier." |
| `relationship_family == "openai_gpt_oss"` | family hint: "gpt-oss does not support response_schema — XML via prompt only." |

**`recommendation`** is a lookup of `(verdict, has_failure_patterns)` → string. See `_RECOMMENDATION_TABLE` in `bench/heuristics.py`.

## Known model quirks (empirically observed)

- **MiniMax-M2.7 on Venice** — inlines `<think>` tokens directly into `content` despite `venice_parameters.disable_thinking=true`. Empty-content rate ~10-20% on relationship-tier calls; per-chunk relationship extraction collapses. AVOID for ingestion tiers.
- **Qwen3-A3B family on Venice** — MoE with very small active param count (3B). Format-adherence on the first pass is wobbly (zero-pair scans common), but the gleaning safety net in `backend/app/services/graph_extractor.py` recovers most of the pairs. ERR is variable.
- **GPT-5.0 vs 5.1 enum split** — OpenAI's `reasoning_effort` accepts `"minimal"` on GPT-5.0 but rejects it on GPT-5.1+ (which requires `"none"`). Cortex's `reasoning_config.py` dispatch already handles this; do NOT regress that.
- **Anthropic Opus 4.7+** — returns 400 if `thinking` parameter is passed manually. Dispatch must OMIT the param entirely for these models.
- **OpenRouter `reasoning.exclude=true`** — does NOT save tokens. Model still reasons and bills; the field only hides the chain-of-thought from the response. Use `reasoning.effort="none"` (or `"minimal"` for GPT-5.0) instead.

## CI / test surface

Only `bench/test_heuristics.py` is automated. It runs `apply_heuristics()` against the three historical session JSONs at `bench/logs/runs/run_2026-05-20_*.json` and asserts the heuristic verdicts match expected (MIXED / GOOD / POOR-or-MIXED). Catches drift in the decision tree.

No integration tests — the harness IS the integration test. To smoke-test the wiring after any change:

```bash
python bench/combo_resolver.py        # validates YAMLs + key resolution
python bench/test_heuristics.py       # validates heuristics
python bench/run_bench.py --dry-run   # validates orchestrator without side effects
```

## Gotchas for future Claude sessions

- **`docker compose restart` does NOT re-read `env_file`.** Use `docker compose up -d backend --force-recreate`. This is wired into `recreate_backend()` already; don't change it.
- **`.env` is sacred during a batch.** The `EnvSwapper` backs it up at batch start and restores on normal exit, `Ctrl-C`, or `SIGTERM`. Do not add code that writes to `.env` outside the swapper's try/finally.
- **`bench/` is at the repo root**, not under `analysis/`. The legacy `analysis/` nesting was removed in the restructure commit.
- **Per-chunk relationship extraction logs nothing on success** — only on failure (`Per-chunk relationship extraction failed after retries`). Don't add success logs; the `/api/stats` `per_chunk_relationship_count` is the canonical post-run count.
- **The `.ods` writer (`build_results_ods.py`) uses `odfpy`.** When adding columns to a run JSON, update both the section/column list in `build_results_ods.py:SECTIONS` AND the schema-aware fields in `bench/heuristics.py` / `bench/llm_review.py`.
- **Adding a new combo with a model that's not in `models.yaml.example` yet?** Add the model to `models.yaml.example` first (committed), THEN add the combo. Otherwise new clones can't reproduce the combo.
- **Q+A generation + judge use the operator's `OPENAI_*` config, NOT a combo's transient model.** Both calls go through `EnvSwapper.primary_model_config()` which reads from the pre-batch `.env` backup. This is intentional — the question bank must be FIXED across all combos for fair comparison, and the judge needs to score every combo on the same axis. Don't switch either call to read the live `.env` (it gets rewritten per combo).
- **The Q+A phase runs AFTER stats snapshot, not before.** `run_combo` snapshots `/api/stats` at step 9 then runs Q+A at steps 10–11. This ordering is deliberate: ingestion data must persist even if Q+A blows up. Don't reorder.
- **Do NOT mention `bench/` in `README.md`, `documentation/`, or `handbook/`.** Public docs are deferred. Keep changes scoped to `bench/`, this file, and the routing entry in `CLAUDE.md`.

## Cross-references

- Server-side library export/import: `.claude/domain/admin-features.md`
- Reasoning-suppression dispatch (the brain behind the bench's per-provider behaviour): `backend/app/services/reasoning_config.py`
- Cortex pipeline phases that the bench drives: `.claude/domain/document-pipeline.md`, `.claude/domain/relationships.md`, `.claude/domain/communities.md`
- Env-var reference (the bench rewrites a subset of these per combo): `.claude/environment.md`
