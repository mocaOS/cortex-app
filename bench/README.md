# bench/ — autonomous LLM-stack benchmark harness for Cortex

Cycle a fixed dataset through arbitrary LLM-model combinations against the
Cortex ingestion pipeline (Phase A entity extraction → Phase B relationship
analysis → Step 3 community detection) and capture quantitative + qualitative
results in a side-by-side spreadsheet.

The harness is **provider-agnostic**. Each model in the registry carries its
own `base_url`, `api_key`, and `context_length`, so a single combo can mix
OpenAI + Anthropic + a self-hosted vLLM endpoint + any OpenAI-compatible
gateway freely. The provider-specific reasoning-suppression dispatch (which
flag goes where for OpenAI's `reasoning_effort`, Anthropic's `thinking`,
Venice's `venice_parameters`, vLLM's `chat_template_kwargs`, etc.) is handled
inside Cortex itself by `backend/app/services/reasoning_config.py` — the
bench harness just sets the env vars; the backend picks the right kwargs
based on the `base_url` it sees.

## What you actually need to do

1. **Have Docker running with the Cortex stack up locally.** The harness
   drives the LOCAL `cortex-backend` container — it does NOT spin one up
   itself. Confirm with `docker compose ps` (you should see `cortex-backend`
   and `cortex-neo4j` both healthy). If not yet up: `docker compose up -d`.

2. **`.env` at repo root has the right keys.** `ADMIN_API_KEY` is required
   (the harness authenticates every Cortex API call with it). Whatever
   provider API keys your `bench/models.yaml` references via `api_key_env`
   must also be set — either in your shell environment or in `.env`. The
   default registry uses `OPENAI_API_KEY` (which on the dev machine holds
   the Venice key).

3. **Drop your benchmark documents into `bench/files/`.** Any `.md` files
   in that folder become the dataset for every combo in the batch. No
   count requirement; the harness uses whatever's present at run time.

4. **First-run bootstrap (automatic).** Run any bench command once — e.g.
   `python bench/run_bench.py --dry-run` or `python bench/combo_resolver.py` —
   and the harness auto-copies `*.yaml.example` → `*.yaml` if the local
   copies are missing. Both `.yaml` files are gitignored so you can edit
   them freely (including pasting literal API keys into `models.yaml`).

5. **Edit `bench/combos.yaml`** to pick the combos you want to run. The
   shipped local copy has 4 combos (3 session replays + a qwen3-6-27b test);
   the committed `bench/combos.yaml.example` has a 12-combo curated matrix
   for reference.

6. **Dry-run to verify the plan** — no env changes, no API calls:

   ```bash
   python bench/run_bench.py --dry-run
   ```

7. **Smoke-test a single combo** (~10–20 min for the 15-doc dataset):

   ```bash
   python bench/run_bench.py --only 01-minimax
   ```

8. **Full batch**:

   ```bash
   python bench/run_bench.py
   ```

Each combo: rewrites `.env`, recreates `cortex-backend`, resets the DB,
ingests every `.md` in `bench/files/`, runs Phase A → B → Step 3, snapshots
stats + logs, writes a row to `bench/logs/llm-config-results.ods`. 30-min
wall-clock cap per combo. `.env` is auto-backed-up at batch start and
restored on exit.

9. **Open the dashboard** — `bench/index.html` is a static page that
   visualises the runs so far. Open it directly in a browser at any time
   (double-click the file, or `open bench/index.html`). The dashboard
   regenerates automatically at batch start, after every combo, and after
   the end-of-batch LLM review — just refresh the browser tab to see the
   current state. No server required.

## Viewing results

The bench harness ships with a static dashboard at `bench/index.html` that
visualises every run: latest-batch summary, verdict distribution donut,
ERR comparison bars, phase-timing stacked bars, failure-signal heatmap,
per-combo detail cards (with LLM observations), and the cross-run findings
markdown.

```bash
# Open directly (no server required — works under file://)
open bench/index.html        # macOS
xdg-open bench/index.html    # Linux
start bench\index.html       # Windows

# Or serve it locally if your browser is strict about file:// script loads
python -m http.server --directory bench 8080
# then visit http://localhost:8080/
```

The orchestrator rebuilds the underlying data file
(`bench/logs/dashboard-data.js`) automatically:

- at batch start (so a refresh after kickoff reflects the upcoming batch)
- after every combo finishes (so progress shows up live)
- after the end-of-batch LLM review (so observations + findings appear)

Manual refresh: `python bench/build_dashboard.py` regenerates the data file
from current `bench/logs/runs/*.json` + the latest `findings_*.md`. The
dashboard itself never auto-refreshes — just reload the browser tab.

When no runs exist yet, the dashboard shows a friendly empty state with a
link back to this README.

## Will the bench nuke my personal Cortex data?

The orchestrator resets the local Cortex DB between combos so every model
combination starts from a clean slate. **If your local Cortex has any
documents or entities at batch start, the harness automatically exports the
entire library** (documents, chunks, entities, relationships, communities,
collections, embeddings — everything `POST /api/admin/export` produces) to
a gitignored ZIP at `bench/backups/cortex-state-<batch_id>.zip` BEFORE the
first reset.

If something goes wrong, restore via:

```bash
curl -F "file=@bench/backups/cortex-state-<batch_id>.zip" \
     -H "X-API-Key: $ADMIN_API_KEY" \
     "http://localhost:8000/api/admin/import?mode=replace"
```

Skip the safety export (already-empty Cortex, or you genuinely don't need
to preserve current state):

```bash
python bench/run_bench.py --no-safety-backup
```

## Layout

```
bench/
├── README.md                       # you are here (committed)
├── run_bench.py                    # main orchestrator (committed)
├── combo_resolver.py               # YAML → resolved combo loader (committed)
├── cortex_client.py                # async HTTP wrapper for Cortex API (committed)
├── log_parser.py                   # docker logs → signal tally (committed)
├── heuristics.py                   # rule-based verdict / failure_patterns (committed)
├── llm_review.py                   # end-of-batch review pass — uses primary model (committed)
├── qa_evaluator.py                 # Q+A retrieval eval — generate / run / judge (committed)
├── _llm_io.py                      # shared chat_completion helper (committed)
├── test_heuristics.py              # sanity test for the heuristic decision tree (committed)
├── build_results_ods.py            # spreadsheet writer (committed)
│
├── models.yaml.example             # full model registry (committed template)
├── models.yaml                     # local copy — gitignored (edit freely)
├── combos.yaml.example             # 12-combo curated matrix (committed template)
├── combos.yaml                     # local copy — gitignored (your active list)
│
├── files/                          # input documents
│   ├── .gitkeep                    # keeps the dir tracked
│   └── *.md                        # GITIGNORED — drop your dataset here
│
└── logs/                           # machine-written outputs (all gitignored)
    ├── .gitkeep
    ├── runs/<batch_id>_<combo_id>.json
    ├── qa-runs/<batch_id>_<combo_id>.json  # raw Q+A answers (speed + quality)
    ├── qa-bank-<batch_id>.json     # generated question bank (reused per batch)
    ├── qa-findings-<batch_id>.md   # Q+A cross-run summary
    ├── batch_log_<batch_id>.jsonl
    ├── findings_<batch_id>.md      # LLM cross-run analysis
    ├── .env.bak.<batch_id>         # env backup
    └── llm-config-results.ods      # the master spreadsheet
```

## What "example" vs local YAML means

Two YAML files configure every batch:

- `models.yaml` — registry of every model the harness knows about (model_id,
  base_url, api_key, context length, optional family/reasoning metadata)
- `combos.yaml` — the matrix of stacks to test; each combo references three
  model IDs from `models.yaml` (primary / extraction / relationship tier)

Each is split into:

- **`*.yaml.example`** — committed to git. The public-facing template. Safe
  to edit and PR.
- **`*.yaml`** — gitignored. Your local copy. Edit freely: add literal
  API keys, prune combos, add new test stacks. Stays out of source control.

On first run, the orchestrator auto-copies the `.example` files to the local
versions if missing and logs `[init] Created bench/...yaml from .example`.

## Setup checklist

Install Python deps (host, not container):

```bash
pip install httpx pyyaml odfpy
```

Confirm:

- Docker is running and `docker compose ps` shows `cortex-backend`.
- The repo-root `.env` has `ADMIN_API_KEY` set (used by the harness for all
  Cortex API calls).
- For every `api_key_env` your `models.yaml` references, that env var is set
  either in your shell or in the repo-root `.env`.
- `OPENAI_MODEL`, `OPENAI_API_BASE`, `OPENAI_API_KEY` in `.env` are set —
  these define the **primary model** that runs the end-of-batch review.
  In the default Venice setup that's MiniMax-M27. Skip the review with
  `--skip-llm-review` if you don't want any model call after the batch.
- `bench/files/` has the `.md` documents you want to ingest.

## CLI

```bash
# Full batch (all combos in bench/combos.yaml)
python bench/run_bench.py

# Single combo by ID substring
python bench/run_bench.py --only 02-mistral-gptoss

# Dry run — print plan, no env changes, no API calls
python bench/run_bench.py --dry-run

# Skip the Claude review pass at the end
python bench/run_bench.py --skip-llm-review

# Skip the Q+A retrieval evaluation (faster — ingestion only)
python bench/run_bench.py --skip-qa-eval

# Customise the Q+A bank (default 15 questions, both modes)
python bench/run_bench.py --qa-count 20 --qa-modes speed,quality

# Smoke-test the question generator alone (one LLM call, no Cortex needed)
python bench/qa_evaluator.py --self-test

# Validate models.yaml + combos.yaml + key resolution (no side effects)
python bench/combo_resolver.py

# Re-run heuristic sanity tests against historical runs
python bench/test_heuristics.py
```

## What it does per combo

1. **Backs up `.env`** once at batch start to `bench/logs/.env.bak.<batch_id>`.
   Restores on normal exit, Ctrl-C, or SIGTERM.
2. **Rewrites `.env`** with 13 env vars derived from the combo's resolved
   per-tier model configs:
   - Primary tier: `OPENAI_MODEL`, `OPENAI_API_BASE`, `OPENAI_API_KEY`
   - Extraction tier: `GRAPH_EXTRACTION_MODEL`, `GRAPH_EXTRACTION_API_BASE`,
     `GRAPH_EXTRACTION_API_KEY`, `GRAPH_EXTRACTION_MAX_CONTEXT`
   - Relationship tier: `RELATIONSHIP_EXTRACTION_MODEL`,
     `RELATIONSHIP_EXTRACTION_API_BASE`, `RELATIONSHIP_EXTRACTION_API_KEY`,
     `RELATIONSHIP_MAX_CONTEXT`
   - Reasoning modes: `EXTRACTION_REASONING_MODE`,
     `RELATIONSHIP_REASONING_MODE`, `DEFAULT_REASONING_MODE`
   - Optional: `RELATIONSHIP_MAX_OUTPUT_TOKENS` if the relationship-tier
     model defines `max_output_tokens`.
3. **Recreates `cortex-backend`** with `docker compose up -d backend --force-recreate`
   (`docker compose restart` does NOT re-read `env_file`).
4. **Resets the DB** via `POST /api/admin/reset`.
5. **Uploads all `.md`** from `bench/files/` via `POST /api/upload`.
6. **Drives Phase A** → polls `/api/stats` until done.
7. **Drives Phase B** → polls `/api/tasks/{id}`.
8. **Drives Step 3** → polls task.
9. **Snapshots stats** via `/api/stats`.
10. **Q+A speed mode** → asks the shared question bank via `/api/ask`
    (chat mode, `use_agentic=false`); logs answers, sources, latency.
11. **Q+A quality mode** → same bank via `/api/ask` (`use_agentic=true`,
    deep-research path); logs answers, sources, latency.
12. **Parses `docker logs`** over the combo's wall-clock window for signal
    counts + phase timestamps.
13. **Applies heuristic analysis** — verdict, failure_patterns,
    performance_notes, recommendation derived from `heuristics.py`.
14. **Writes** `bench/logs/runs/<batch_id>_<combo_id>.json` plus raw Q+A
    answers at `bench/logs/qa-runs/<batch_id>_<combo_id>.json`.
15. **Appends a row to** `bench/logs/llm-config-results.ods` via `build_results_ods.py`.

Hard cap: **75 min wall-clock per combo** (15-20 min ingestion + ~4 min
qa_speed at 10 questions + ~10 min qa_quality at the 5-question subset,
plus headroom). Stalls and partial runs get a persisted `verdict=TIMED_OUT`
row plus whatever per-mode Q+A answers landed on disk before the cap.

## End-of-batch LLM review (one call total)

After all combos finish, the orchestrator runs **one** chat-completion call
against the **primary model** configured in `.env` (`OPENAI_MODEL` /
`OPENAI_API_BASE` / `OPENAI_API_KEY`). In the default Venice setup that's
MiniMax-M27. The orchestrator reads those values from the **pre-batch**
`.env` backup, so a combo's temporary rewrite doesn't leak through. No
Anthropic / Claude / extra-SDK dependency — pure `httpx` against the
OpenAI-compatible `/chat/completions` endpoint your primary model already
sits behind.

It's given:

- The full set of run records (compact stats + heuristic fields)
- A tail of each combo's docker log (last 200 lines)

It returns:

- `runs.<run_id>.observations` — 2-3 sentences per run
- `runs.<run_id>.vs_previous_run` — 2-3 sentences vs the previous combo
- `code_optimisation_findings` — synthesised markdown with concrete code
  recommendations (gleaning thresholds, per-chunk token budgets, reasoning
  suppression edge cases, etc.)

These get merged back into each run's JSON, the `.ods` is rebuilt clean,
and the markdown findings are written to `bench/logs/findings_<batch_id>.md`.

Skip with `--skip-llm-review` (the heuristic fields stay populated). The
orchestrator also gracefully skips with a warning if the response can't be
parsed as JSON — useful when your primary model is a reasoning model that
doesn't always honour the JSON-output instruction.

## Q+A retrieval evaluation

Ingestion stats alone (entities, relationships, ERR, communities) don't tell
you which combo produces a graph that actually **answers questions well**.
The Q+A evaluation phase closes that loop.

How it works:

1. **Pre-batch question generation** — one LLM call against the operator's
   primary model (same config source as the LLM review) reads `bench/files/`
   and produces ~10 questions split across three buckets: factoid (single
   doc), synthesis (multi-doc reasoning), thematic (broad concepts). Cached
   at `bench/logs/qa-bank-<batch_id>.json` — re-running a batch with the
   same `batch_id` reuses the cached bank.
2. **Per-combo: speed mode** — every question is posted to `/api/ask` with
   `use_agentic=false`. This is the chat path: hybrid search + reranking +
   one writer pass. 90 s per-question timeout.
3. **Per-combo: quality mode** — same bank, `use_agentic=true`. The full
   researcher agent loop with tool calls (`knowledge_search`,
   `community_search`, `entity_lookup`, `reasoning`). 300 s per-question
   timeout (bumped from 180 s after a minimax-m27 run showed the researcher
   loop genuinely needs >180 s on harder questions).
4. **End-of-batch judge pass** — one LLM call across all (run × mode ×
   question) tuples scores every answer on **faithfulness**, **completeness**,
   **groundedness**, **conciseness** (1–5 each) plus a 2-sentence summary
   per (run × mode). Aggregates land in the run JSON / `.ods`; raw per-
   question detail stays in `bench/logs/qa-runs/<run_id>.json`. Cross-run
   findings are written to `bench/logs/qa-findings-<batch_id>.md`.

Flags:

- `--skip-qa-eval` — skip the whole Q+A subsystem (no question gen, no
  per-combo phases, no judge pass).
- `--qa-count N` — override bank size (default 10, clamped to 5–30).
- `--qa-modes speed,quality` — comma-separated modes to run.
  Use `--qa-modes speed` to skip the slow deep-research arm.
- `--qa-quality-count N` — quality (deep research) mode runs only the first
  N of the bank (default 5). Quality mode is the slow arm at ~60-300 s/q;
  subsetting keeps a 2-combo batch under ~90 min wall clock. Pass `0` to
  run quality on the full bank.

Same robustness rules as the LLM review: per-question errors get recorded
and the loop continues; a whole-Q+A failure for a combo just zeroes that
combo's `qa_*` fields without poisoning the ingestion data captured
upstream.

## Q+A chat benchmark (snappiness — `run_qa_bench.py`)

A separate, lighter benchmark from the ingestion harness above. It answers:
**"which chat model is snappiest without sacrificing answer quality?"** It holds
your already-ingested graph FIXED and swaps only the answer model, so there's no
re-ingestion and your graph is never touched.

```bash
# All models in bench/qa_models.yaml, against the live local graph:
python bench/run_qa_bench.py --count 10 --budget 30 --hard-cap 90

# Just a couple of models:
python bench/run_qa_bench.py --models minimax-m3,qwen3-5-35b-a3b

# Print the plan (no docker, no LLM calls):
python bench/run_qa_bench.py --dry-run

# Wiring test against the already-running backend (no per-model recreate):
python bench/run_qa_bench.py --models minimax-m3 --count 2 --no-recreate
```

Prereqs: the local stack up with a **non-empty graph** (it queries
`/api/stats`; aborts if `entity_count==0`), `ADMIN_API_KEY` + `OPENAI_API_*` in
`.env`. Edit `bench/qa_models.yaml` (auto-created from `.example`) to pick
candidates — a flat list; `context` is pinned the same for all (override with
`--context`); `base_url`/`api_key` default to your `.env` provider.

**What it measures**, streaming `POST /api/ask/stream` (`use_agentic=false`, the
snappy graph-chat path), per model × question:

- **TTFT** — time to the first answer token. The snappiness signal a user feels,
  and the overthinking proxy (a model that reasons forever delays its first
  visible token).
- **Total latency** (p50/p95), **tokens/sec**, **answer length**.
- **status** — `ok` / `over_budget` (completed but slower than `--budget`) /
  `timeout` (blew the `--hard-cap` wall clock — the chat "gave up" / lost the
  user) / `error`.
- **Quality** — faithfulness/completeness/groundedness/conciseness (1–5), judged
  by your baseline model (reuses the Q+A judge).

**Question bank** is generated once from the graph itself (samples entities +
community summaries; no `bench/files/` needed) and cached so every model answers
the identical set.

**Outputs** (gitignored): `bench/logs/qa-chat-report-<batch>.md` — a leaderboard
ranking models on a speed×quality blend with overthinkers flagged 🚩 — and
`bench/logs/qa-chat-results-<batch>.json`. The report marks your current model as
the baseline so you can see who beats it. `.env` is restored and the backend
recreated back onto your model when it finishes (or on Ctrl-C).

## Adding a new model

Edit `bench/models.yaml` (your local, gitignored copy):

```yaml
my-new-model:
  model_id: actual-string-the-api-expects
  base_url: https://provider.example.com/v1
  api_key_env: PROVIDER_API_KEY      # name of env var holding the key
  context_length: 128000
  family: qwen                       # optional — see "Family tags" below
  reasoning_capable: false           # optional
  max_output_tokens: 16000           # optional; sets RELATIONSHIP_MAX_OUTPUT_TOKENS when used at relationship tier
  note: "Why this model?"
```

`api_key` resolution:
- `api_key_env: NAME` — preferred. Read `NAME` from your shell env, falling
  back to the repo-root `.env`.
- `api_key: "literal-string"` — inline; safe to use because `bench/models.yaml`
  is gitignored. **Do NOT add literal keys to `bench/models.yaml.example`** —
  that file IS committed.

Providers supported by Cortex's reasoning-suppression dispatch (auto-detected
from `base_url`): OpenAI direct, OpenRouter, Venice, Anthropic, and any
OpenAI-compatible vLLM/Compute3 endpoint. Other providers still work — the
backend just won't inject reasoning-control kwargs for them.

## Family tags

Used by `heuristics.py` for family-specific failure-pattern hints. Known
values surfaced in the analysis row:

| Family | Triggers |
|---|---|
| `qwen` | Format-adherence variance hint when zero-pair scans observed |
| `minimax` | "Inlines thinking" warning if used at relationship tier |
| `openai_gpt_oss` | No-response-schema warning if drift observed |
| `mistral`, `llama`, `gemma`, `nvidia`, `arcee`, `aion`, `mercury`, `glm`, `deepseek`, `kimi` | No specific hint; tagged for filtering / grouping |
| `openai_gpt5`, `openai_gpt4`, `anthropic_claude`, `google_gemini`, `xai_grok` | No specific hint; tagged as proprietary |

You can use any string — unknown families just don't get the extra hint.

## Adding a new combo

Edit `bench/combos.yaml`:

```yaml
- id: 13-my-new-combo
  primary: my-new-model              # references a key from models.yaml
  extraction: some-other-model
  relationship: my-new-model
  note: "Why this combo?"
```

Optional per-combo overrides:

- `extraction_reasoning_mode`, `relationship_reasoning_mode`,
  `default_reasoning_mode` — defaults are `off`/`off`/`auto`. Useful to keep
  thinking ON for a stress test (`relationship_reasoning_mode: auto`).

The `--only` flag accepts a substring of `id`, so `--only 13-my-new` matches.

## When a combo errors

- **Model not found** — provider returns 404; `CortexError` is captured;
  `verdict=ERROR`, `issue_notes` shows the error. Orchestrator moves on.
- **Auth failure** — typically a 401/403 from the provider; same handling.
- **Per-combo timeout** — `verdict=TIMED_OUT`. Stub row written.
- **Container won't recreate** — `subprocess.run` raises; orchestrator aborts
  the whole batch with `.env` restored.

The `.env` backup at `bench/logs/.env.bak.<batch_id>` is your safety net.
If anything looks wrong post-batch:

```bash
cp bench/logs/.env.bak.<batch_id> .env
```

## Notes on cost

Each model entry can carry pricing context in its `note` field (the seeded
registry includes `$<in>/$<out> per million tokens` for Venice models). Be
mindful with high-cost proprietary passthroughs (Claude Opus, GPT-5.5,
Grok 4): a single 15-doc benchmark run can cost $10-50 on those tiers.
Restrict combos using them to `--only` runs and check the provider's billing
dashboard before kicking off a batch.
