# QA & Testing

How Cortex is tested: the backend pytest suite, the live end-to-end harness, and the canonical QA feature/defect spreadsheet under `qa/`.

## Backend test suite (`backend/tests/`)

Pytest, fully hermetic by default — LLM, Neo4j, and the ML stack are mocked via `conftest.py`, so the suite runs with **no external services**. Config is `backend/pytest.ini` (`asyncio_mode=auto`, `--strict-markers`, `slow` marker).

### Running it
There is no committed virtualenv and the system/conda Python lacks pytest. Create a torch-free venv from the base requirements (everything the suite needs is mocked, so the ML stack is unnecessary):

```bash
cd backend
python3 -m venv .qa-venv
.qa-venv/bin/pip install -r requirements-base.txt   # torch-free; includes pytest + pytest-asyncio
.qa-venv/bin/python -m pytest -q
```

CI parity lint gate (error-class only): `.qa-venv/bin/python -m ruff check --select E9,F63,F7,F82 app/ tests/`.

### conftest fixtures (autouse + opt-in)
- `_isolate_env` (autouse) — mutates the cached `Settings` to safe test defaults (quotas 0, blank keys, `admin_api_key="test-admin-key"`, temp dirs); never reads the real `.env`.
- `mock_llm` (autouse) — raises on any real LLM-client construction / LLM-shaped POST; opt in to a fake completion via `mock_llm.set_chat_response(...)`.
- `mock_neo4j`, `mock_processors` — MagicMock singletons; `client` — FastAPI `TestClient` with Neo4j/processors mocked and the three auth deps overridden to a fake admin.

### Coverage map (unit/contract)
Dedicated suites cover: config/budget fallback, reasoning dispatch, prompt cache, graph-extractor XML parsing + chunk-batch + batched writes + checkpoint delta, **targeted Phase B discovery** (`test_relationship_candidates.py` — candidate merge/rank/caps/grouping; `test_targeted_relationship_discovery.py` — mode dispatch, embedding backfill, pair verification flow, confidence/degree-cap filters, generator-failure degradation), entity resolution, crypto, git providers + sync, **web crawl** (`test_web_crawl.py` — crawl4ai client link normalization/same-host filtering, title extraction, /md + /crawl response parsing with cache-bypass assertion), resilience/circuit-breaker, observability (logging/metrics), **Langfuse tracing** (`test_langfuse.py` — activation gating + the untraced no-op contract: factory returns the plain client, helpers inert; plus content-masking `_mask_content`: redacts message/tool/embedding/vision/metadata text while keeping structure, planted-secret leak check, fail-closed totality), quota caps, **auth_service** (hashing/generation/permission tiers/collection scope + real HTTP 401 enforcement via a no-bypass client), **api_usage** endpoint categorization, **vision** image-payload prep, **context_curator** memory helpers, **library_transfer** NDJSON round-trip, **researcher_agent** helpers (merge/dedup/truncate/substitute), **skill_service** parse/sanitize/env-boundary, **RRF** hybrid-search fusion, and FastAPI **endpoint contract** smoke tests (422/400/404).

## Live end-to-end harness (`backend/tests/test_live_e2e*.py`)

Real HTTP journeys against a running deployment (the docker-compose stack: `cortex-backend`, `cortex-neo4j`, `cortex-frontend`, `cortex-helper`). Both modules **auto-skip** when no stack/key is present, so the offline suite is unaffected.

- `test_live_e2e.py` — **unauthenticated** journeys: `/health`, the auth boundary (protected endpoints → 401), `/metrics` gate, frontend unauthenticated redirect → `/login`. Override target with `CORTEX_E2E_BASE` / `CORTEX_E2E_FRONTEND`.
- `test_live_e2e_authed.py` — **authenticated** journeys; the key is read from `CORTEX_E2E_API_KEY` (**never hard-coded**) and the module skips without it. Covers authed reads, collections CRUD round-trip, real hybrid search, the fast-path streaming chat journey, and the **full document ingestion → extraction → search → cleanup pipeline**. Only non-destructive writes (uniquely-named temp collection / doc, deleted in-test).

```bash
CORTEX_E2E_API_KEY=<key> .qa-venv/bin/python -m pytest tests/test_live_e2e_authed.py
```

Gotchas learned the hard way:
- Document status lives in the **`processing_status`** field (`pending|processing|extracting|completed|failed`), not `status`.
- Non-streaming `POST /api/ask` returns **504 `deadline_exceeded`** at `ASK_DEADLINE_SECONDS` (~28s) under a slow LLM — documented behavior; the streaming endpoint is the real chat journey.
- **Never trigger community detection against a live/shared graph** — Leiden/Louvain re-clusters every entity.
- The frontend `node_modules` may be root-owned (Docker build leftover) → `npm install` EACCES; run `tsc --noEmit`/eslint from a user-owned copy. CI runs the frontend gate regardless.

## Canonical QA spreadsheet (`qa/`)

- `qa/cortex_qa_master.ods` — source-of-truth feature/defect inventory: **Features**, **Defects**, **Summary** sheets. 50 feature rows across all backend domains, every HTTP endpoint, and all frontend screens; each row carries a test suite, status, defect count, severity, and last-tested date.
- `qa/features.json` — structured source; `qa/gen_ods.py` — generator (needs `odfpy`). Regenerate: `python qa/gen_ods.py qa/features.json qa/cortex_qa_master.ods`.
- `qa/QA_REPORT.md` — iteration log (coverage, defects, confidence).

The live journeys are codified as the `backend/tests/test_live_e2e*.py` pytest modules above (run them via `CORTEX_E2E_API_KEY=<key> .qa-venv/bin/python -m pytest tests/test_live_e2e_authed.py`).

## Defects found & fixed by the QA pass

| ID | Area | Severity | Fix |
|----|------|----------|-----|
| D-001 | `git_connector_service._supported()` | Medium | Read `DocumentProcessor.RAW_TEXT_EXTENSIONS` from the class instead of instantiating a full processor (broke test isolation when a real `.env` was present). |
| D-003 | `api_usage_service.categorize_endpoint` | Low | Longest-prefix-wins matching so `/api/custom-inputs/{id}` categorizes as `documents`, not `upload` (analytics data integrity). |
| D-004 | API docs exposure | Low | Interactive docs (`/docs`,`/redoc`,`/openapi.json`) now gated by `EXPOSE_API_DOCS` (off in production by default). See [`environment.md`](environment.md). |
