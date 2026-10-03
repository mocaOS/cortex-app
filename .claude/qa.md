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
- `_isolate_env` (autouse) — mutates the cached `Settings` to safe test defaults (quotas 0, blank keys, `admin_api_key="test-admin-key"`, temp dirs incl. `git_work_dir` — a developer `.env` with `ENABLE_GIT_INTEGRATION=true` + the container path `GIT_WORK_DIR=/app/…` used to make every TestClient lifespan die with `PermissionError: '/app'` outside Docker); never reads the real `.env`.
- `mock_llm` (autouse) — raises on any real LLM-client construction / LLM-shaped POST; opt in to a fake completion via `mock_llm.set_chat_response(...)`.
- `mock_neo4j`, `mock_processors` — MagicMock singletons; `client` — FastAPI `TestClient` with Neo4j/processors mocked and the three auth deps overridden to a fake admin.

The shared `client` cannot establish real authentication or cross-key isolation.
Use the no-bypass clients in `test_auth_enforcement_http.py` and
`test_scoped_principal_journeys.py` for those claims. The latter validates two
synthetic keys through the real auth chain and follows session/MCP→REST→SSE
journeys; its store remains a fake, so Cypher isolation is still a live-test gap.

`test_library_transfer_roundtrip.py` exercises the real ZIP writer/importer against
a scripted graph store: record/identity/byte continuity, nonempty-target refusal,
incomplete archives, and fake-store call-signature parity. This is library-transfer
evidence, not a Neo4j or whole-stack backup/restore rehearsal. Obligations and
assertions are in [`../qa/QA_CONTRACT_RECORDS.md`](../qa/QA_CONTRACT_RECORDS.md).

For focused replay from `backend/` (use your QA environment's Python):

```bash
.qa-venv/bin/python -m pytest -q tests/test_scoped_principal_journeys.py tests/test_library_transfer_roundtrip.py
```

Check the filesystem used by pytest's temporary uploads has at least the configured
`MIN_FREE_DISK_MB` free (default 500). A small `/tmp` tmpfs can yield valid 507s and
fail upload tests even when the checkout filesystem has space. Set `TMPDIR` to an
existing disposable directory on a sufficiently large filesystem; keep the guard.
If system `venv`/pip is unavailable, `uv venv .qa-venv` then
`uv pip install --python .qa-venv/bin/python -r requirements-base.txt` is equivalent.

## Verification stages

- **Local deterministic:** backend pytest/error-only ruff; docs `npm run validate`
  + `npm test` (from `documentation/`); script tests/version sync; frontend typecheck/lint
  when its inputs change. Zero selection, setup errors or skipped required cases
  do not count as passing evidence.
- **Consumer:** chat `npm test`/typecheck and skills SDK/MCP suites for boundary
  changes. Link the three input revisions from [regeneration](regeneration.md).
- **Release/live:** browser and live API journeys, actual store/provider/recovery
  combinations. Auto-skipped live tests are unverified, not green integrations.
- **Quality:** hard auth/state/tool invariants cannot be traded for fluent answers.
  `UNRESOLVED-QUALITY-001` in the contract record describes the missing calibrated
  acceptance baseline. Existing internal `bench/` heuristics are separate evidence;
  consult `bench.md` before using that stateful/paid harness.

### Coverage map (unit/contract)
Dedicated suites cover: config/budget fallback, reasoning dispatch, prompt cache, graph-extractor XML parsing + chunk-batch + batched writes + checkpoint delta, **targeted Phase B discovery** (`test_relationship_candidates.py` — candidate merge/rank/caps/grouping; `test_targeted_relationship_discovery.py` — mode dispatch, embedding backfill, pair verification flow, confidence/degree-cap filters, generator-failure degradation), entity resolution, crypto, git providers + sync, **web crawl** (`test_web_crawl.py` — crawl4ai client link normalization/same-host filtering, title extraction, /md + /crawl response parsing with cache-bypass assertion), resilience/circuit-breaker, observability (logging/metrics), **Langfuse tracing** (`test_langfuse.py` — activation gating + the untraced no-op contract: factory returns the plain client, helpers inert; plus content-masking `_mask_content`: redacts message/tool/embedding/vision/metadata text while keeping structure, planted-secret leak check, fail-closed totality), quota caps, **auth_service** (hashing/generation/permission tiers/collection scope + real HTTP 401 enforcement via a no-bypass client), **api_usage** endpoint categorization, **vision** image-payload prep, **context_curator** memory helpers, **library_transfer** NDJSON round-trip, **researcher_agent** helpers (merge/dedup/truncate/substitute), **skill_service** parse/sanitize/env-boundary, **RRF** hybrid-search fusion, **x402 payments** (`test_x402.py` — protocol codecs/amounts, pinned Keccak-256/EIP-55 + base58 address vectors, config-hash verification binding, every `enforce_x402_payment` path against a faked facilitator, two-tier research pricing: multiplier math + body-driven mode detection + multiplier-0 403 + pre-payment 422/400 guards, monetized-key hardening: MANAGE strip + endpoint allowlist, admin config/verify/earnings endpoints, key-CRUD price/multiplier guards), FastAPI **endpoint contract** smoke tests (422/400/404), and **API ergonomics** (`test_api_ergonomics.py` — `/api/documents` query params incl. pre-pagination `total` + 400 on unknown sort, additive field aliases (`total`/`id`/`document_title`, ask `collection_id` echo), the SSE `type` discriminator priority table, uniform `collection_id` placement (top-level search body + upload form fields, disagreement → 400), and `response_format` validation/routing + `_parse_structured_answer`; TestDepthParam covers the unified depth dial; TestAskAnswerFlags covers `truncated`/`refused`/`finish_reason` on `/api/ask`, the structured `ask_failed` 500 in production vs development, and `refused` on SSE frames), **ingestion/webhooks** (`test_ingestion_webhooks.py` — /api/ingestion/status aggregation, webhook signing/envelope/gating/retry/4xx-short-circuit, admin CRUD incl. secret-shown-once), the **consolidation scheduler** (`test_consolidation.py` — every trigger and skip path), **context assembly** (`test_context_endpoint.py` — budget allocation, enrichment cap, at-least-one-chunk floor, scoping, degradation), **remote MCP** (`test_remote_mcp.py` — full JSON-RPC handshake, tool dispatch through the in-process ASGI client, SSE response mode without the Cortex `type` stamp, 404-when-disabled), and **server-side sessions** (`test_sessions.py` — trim math incl. summarized_count preservation, CRUD + quota + ownership, session_conflict/fast-search/foreign-404 contract, turn persistence on both paths incl. the post-done memory_update capture).

## Disposable storage/consumer recovery gate (`qa/restore/`)

`qa/restore/rehearsal.sh --run-id <new-id>` executes actual APOC backup/restore
against private fixture stores and all five file roots, including the companion
Chat real-migration/encryption fixture. Exact graph/schema/SQLite/byte comparisons,
post-snapshot exclusions, valid-checksum missing-avatar and pre-wipe refusals are
required. Current scoped result: [`../qa/restore/RESULTS.md`](../qa/restore/RESULTS.md).
Adding `--consumers` exercises real backend/Chat HTTP boot/login/read on copies
before destructive controls, with exact boot-delta allowlist and original-state
isolation. The accepted run passed 20 stages, 15 backend probes and 27 Chat checks.
This remains quiesced/HTTP evidence: browser, production runtime parity, online
snapshots and mixed-version recovery are separate.

`python3 qa/restore/oracle.py selftest` from repo root is the dependency-free CI
control gate. Full execution needs Docker/Podman images and companion dependencies
per `qa/restore/README.md`; required prerequisites fail rather than auto-skip.

Recovery evaluation rules:
- Freeze runner/fixture/coordination inputs and hold writes while a run is active;
  input identities are checked at start and finalize. Use a new run ID for a full rerun.
- Judge success from structured receipts **and** exit status, including finalize
  and cleanup. Missing/false/wrong-typed observations fail; truncated console output
  does not establish an outcome. Retain raw structured results before scratch removal.
- Derive consumer checks from supported routes/public types. Wrong gates need
  recorded, versioned corrections with healthy/negative controls; never alter
  product permissions or silently accept stale check IDs to obtain a pass.
- For oracle/finalizer repairs, replay read-only against retained evidence first;
  that diagnoses machinery, not a new integrated execution.
- Preflight engine health before image availability and capacity on actual temp,
  install/cache and container-storage filesystems. Isolated locked installs do not
  establish parity with a different CI/production interpreter or image.
- Shared runtime resources have separate owners. Do not perform global migrate,
  reset or prune for this campaign. Private direct-IP transport is opt-in and
  provides no DNS-discovery evidence. Exact host-specific replay/hazards live in
  `qa/restore/RESULTS.md`, rather than being assumed prerequisites on every host.

## Companion Chat ask/memory HTTP gate

From `../cortex-chat`, use an existing adequately sized scratch directory and a
fresh evidence/run ID:

```bash
TMPDIR=/var/tmp/cortex-qa-tmp node --import tsx scripts/chat-journey-checks.ts ../cortex-app/output/<new-id> <new-id>
```

Real Next dev request context, synthetic SQLite/users, controlled loopback SSE,
private existing-dependency copies; no installs/shared stores. Required repo
`npm test`/`npm run typecheck` plus evaluation script typecheck:
`node node_modules/typescript/bin/tsc --noEmit -p scripts/tsconfig.chat-journey.json`.
Historical baseline owner: Chat's `docs/regeneration/records/2026-10-02-ask-memory-journey.md`.
Current positive-gate/UI fix: `docs/regeneration/records/2026-10-02-turn-bound-ui.md`.
Judge `claim-verdicts.json` as well as execution/cleanup: retained-defect rejection
is sensitivity, not product acceptance. HTTP reload is not browser reload.

Actual UI uses the same isolated runtime with existing external Playwright/Chromium:
`node --import tsx scripts/chat-browser-journey.ts ../cortex-app/output/<fresh-id> <fresh-id> --mode candidate`
from Chat with root-backed `TMPDIR`; typecheck with `scripts/tsconfig.chat-browser.json`.
Missing tooling fails preflight; it never becomes a browser pass. Required gates
include every selected race/control, page errors, unhandled rejections and complete
selection. Captured PATCH is not commit: durable assertions wait for exact HTTP +
SQLite state. Both browser routing and Node tripwires lack OS/native-socket coverage.

Shared project lifecycle uses the same runtime, separate HTTP/browser verdicts:
`node --import tsx scripts/chat-project-journey.ts ../cortex-app/output/<fresh-id> <fresh-id> http|browser`
from Chat with owned root-backed TMPDIR; typecheck `scripts/tsconfig.chat-project.json`.
Owning receipt: Chat `docs/regeneration/records/2026-10-02-project-lifecycle.md`.
Actual browser gates cover two-user overlap, fresh rebase, same-ID content/feedback
and opaque-memory adoption, held GET navigation, and returning to own live turn.
HTTP characterizes coherent acknowledged LWW and relay late-join ownership; neither
gate implies merging concurrent turns or a server CAS/revision contract. Required
feed connection/selection/error/acknowledgment controls are distinct from product
value assertions. Do not count target crashes or bad selectors as behavioral
rejections; retain failed attempts and changed-gate rationale.

Project follow-up: `node --import tsx scripts/chat-project-followup-journey.ts
../cortex-app/output/<fresh-id> <fresh-id> http|browser` from Chat, owned root-backed
TMPDIR; typecheck `scripts/tsconfig.chat-project-followup.json`. Receipt:
Chat `docs/regeneration/records/2026-10-02-project-followup.md`. Positive gates
cover remote-adopted regenerate/edit forks, reordered genuine adoption GETs and
missed idle/live completion after a native feed error/reopen. Its owned loopback
forwarder drops actual SSE sockets and flushes genuine response headers; browser
offline emulation alone does not prove disconnection. Feedback observes adopted
refs without normal send's repairing re-fetch, and waits for exact metadata/memory
commit in HTTP + SQLite. Resource-bounded browser launcher and retained evaluator
failures are recorded separately from product rejection and accepted final runs.

Still-live/late-memory continuity: `node --import tsx scripts/chat-project-continuity-journey.ts
../cortex-app/output/<fresh-id> <fresh-id> http|browser`, from Chat with owned TMPDIR;
typecheck `scripts/tsconfig.chat-project-continuity.json`. It reuses genuine feed
barriers and exports existing fixture helpers; executed gate bytes are retained
with each receipt. Own compaction may win full-snapshot LWW after remote adoption,
but visible feedback must carry one coherent history/memory pair. Browser gates
also require repeated reconnect's live replay plus fresh missed settled prefix.
Receipt: Chat `docs/regeneration/records/2026-10-02-project-continuity.md`.

Held-read/terminal navigation: `node --import tsx scripts/chat-project-selection-journey.ts
../cortex-app/output/<fresh-id> <fresh-id> http|browser` from Chat with owned TMPDIR;
typecheck `scripts/tsconfig.chat-project-selection.json`. Gate retains genuine GET
bytes across relay replacement/token arrival and checks terminal away/back via
non-repairing feedback/regenerate, immutable local snapshots and coherent LWW.
Receipt: Chat `docs/regeneration/records/2026-10-02-project-selection.md`. Preserve
failed readiness bounds and crashes separately from positive product rejections;
inspect actual route compilation/header timing before attributing a transport fault.
Reuse passed stages after a diagnosed failed stage only when their relevant inputs
still match; keep halted batch exits and enumerate final accepted invocations/receipts.

Terminal edit/fallback: from Chat, `node --import tsx scripts/chat-project-terminal-journey.ts
../cortex-app/output/<fresh-output> <fresh-id> http|browser`, owned root-backed TMPDIR
and the recorded external Chromium launcher; typecheck `scripts/tsconfig.chat-project-terminal.json`.
Owning receipt: Chat `docs/regeneration/records/2026-10-02-project-terminal.md`.
Current **v1.4 HTTP6/6, Chromium47/47 and suite132/132 pass** after user-expanded
capacity. a..e failed attempts stay historical: selector timing, target crash, then
v1.3's Playwright request quota consumed by PATCH fallthrough. The terminal-local
adapter counts only consumer-document GETs and holds both current-page refreshes
before feedback. The fixture's2GiB scratch floor also applies to `npm test`
prepare/failure controls; earlier122/132 capacity outcome remains a failure receipt.
Retain guards/diagnostics and recheck owned capacity with headroom on continuation.
No repairing GET applies before direct-consumer dispatch/held redo, not legitimate
post-completion feed adoption. Match feedback to a stabilized actual selected snapshot;
the driver's current-page two-read schedule is not a public protocol requirement.
When a prerequisite leaves held work pending, label subsequent count/drain failures
as dependent contamination, retain their failed verdicts, and stop or isolate that
case before another independent probe. A driver correction accepting unchanged
runtime is verified evaluation work; it does not justify a product patch.

Ask shutdown/resubmit: from Chat, `node --import tsx scripts/chat-shutdown-journey.ts
../cortex-app/output/<fresh-output> <fresh-id> http|browser`, owned root-backed TMPDIR
and recorded external Chromium launcher; types `scripts/tsconfig.chat-shutdown.json`.
Owning receipt: Chat `docs/regeneration/records/2026-10-02-ask-shutdown.md`.
Frozen v1 accepts unchanged runtime with HTTP7/Chromium31 and suite136. Genuine raw
shutdown follows observed partial content; each retry is held content-free to check
reset before progress. Release an ID-keyed held response before the next same-ID
attempt; record sequence numbers and stable browser/upstream request bodies/headers.
Judge exact durable history/opaque recall and direct no-GET feedback/regenerate,
specific metadata commits and every PATCH200, with healthy settled/legacy controls.
Comparator self-controls reject faulty observations; they do not establish browser
fault-injection sensitivity. This schedule covers pre-done recovery/exhaustion;
post-done compaction shutdown and real deployment restart remain separate.

Project sharing: from Chat, `node --import tsx scripts/chat-project-sharing-journey.ts
../cortex-app/output/<fresh-output> <fresh-id> http|browser`, owned root-backed TMPDIR,
recorded Chromium launcher; types `scripts/tsconfig.chat-project-sharing.json`.
Receipt: Chat `docs/regeneration/records/2026-10-02-project-sharing.md`. Frozen direct/
group grants, partial/full revoke/regain, owner-only controls, member continuation,
exact raw chat state/opaque memory and new admission gates pass HTTP11/Chromium25.
Existing subscriptions retain connect-time admission; do not claim instant active-
feed revocation from fresh404. A no-refetch consumer probe counts its own actor's
reads, not legitimate peer adoption triggered by its write. Already-selected thumbs
are no-ops: change rating and require a new matching PATCH/200 and selected metadata
in HTTP/raw SQLite. v1.1 retains failed v1 and reuses independent HTTP only after
byte-comparing the complete relevant HTTP runner/helper prefix and wrapper delta.

Project move: Chat `node --import tsx scripts/chat-project-move-journey.ts <fresh-output> <fresh-id> http|browser`,
owned TMPDIR/launcher; types `scripts/tsconfig.chat-project-move.json`; receipt `records/2026-10-02-project-move.md`.
Native consent/full-row preservation/held memory/direct consumers/membership/late-ack controls pass8/23 v1.3,
after two unchanged-page context rejections. Correlate active browser/upstream IDs, not released predecessors;
capture intercepted PATCH once before fetch/fulfill so every real ack matches an attempt. Changed runtime
requires fresh integrated old gates, preserving historical receipts rather than stale whole-manifest reuse.
Overlap/reverse: `scripts/chat-project-move-{overlap,reverse}-journey.ts <fresh-output> <fresh-id>`,
types `scripts/tsconfig.chat-project-move-{overlap,reverse}.json`; Chat records `2026-10-03-project-move-{overlap,reverse}.md`.
Overlap commits A/B, acks B/A rejects last-ack binding; reverse holds A pre-forward, B commits/acks then A commits/acks.
Both retain accepted writes/LWW, full move-only state, valid compaction and direct immutable no-chat-GET consumers.
Positive request/view-owned membership selects context; gesture/ack target alone is not authority. Close native drawer first.
No-ack checks bind chat AND move body: earlier history saves are separate accepted writes. Preserve failed phase snapshots.

Project delete: from Chat, `node --import tsx scripts/chat-project-delete-journey.ts
../cortex-app/output/<fresh-output> <fresh-id> http|browser`, owned root-backed TMPDIR,
recorded Chromium launcher; types `scripts/tsconfig.chat-project-delete.json`.
Receipt: Chat `docs/regeneration/records/2026-10-03-project-delete.md`. Native confirm
cancel/accept, owner-only deletion, complete raw detach and all authors' flat lists,
fresh admission, held compaction, immutable direct consumers and held genuine DELETE
ack after project navigation are positive gates. Raw full-state comparison also
protects unrelated projects/chats. Read-only external observations never feed browser
state. Bilingual controls need the actual locale's selector; do not widen readiness
bounds for an English-only helper. Preserve the separate legacy-FK detach control;
its pattern check is not an actual legacy-schema Next route execution.

Delete rejection: from Chat, `node --import tsx scripts/chat-project-delete-rejection-journey.ts
../cortex-app/output/<fresh-output> <fresh-id>`, owned TMPDIR/recorded launcher;
types `scripts/tsconfig.chat-project-delete-rejection.json`. Receipt: Chat
`docs/regeneration/records/2026-10-03-project-delete-rejection.md`. Genuine Chromium
abort before server dispatch must yield requestfailed/no acknowledgment, no server
DELETE log and exact unchanged state, with cancel/actual-success controls. Direct
regenerate/edit with held origin compaction expose swallowed mutation rejection.
Identify the two expected transport failures separately; unknown missing responses
or page/unhandled errors still fail. Pre-dispatch no-commit does not cover uncertain
outcomes after dispatch. Client rejection has its own gate; `scripts/chat-project-delete-response-loss-journey.ts` with `scripts/tsconfig.chat-project-delete-response-loss.json` freezes actual post-commit delivery loss, reconciled personal context/direct immutable consumers/late memory/no automatic replay (Chat receipt `2026-10-03-project-delete-response-loss.md`).

Remote delete: Chat `scripts/chat-project-remote-delete-journey.ts <fresh-output> <fresh-id>
http|browser`, owned TMPDIR/launcher, types `scripts/tsconfig.chat-project-remote-delete.json`;
receipt `records/2026-10-03-project-remote-delete.md`. Member-owned selected held compaction,
exact detach/author admission/late memory and direct immutable consumers have positive gates.
Non-author fresh404 differs from admitted answer/feed continuation; actual late-save404
must preserve state. Optional event kind is supported; correlate actual author/write time.
Association-list: `scripts/chat-project-association-list-journey.ts <fresh-output> <fresh-id>`,
types `scripts/tsconfig.chat-project-association-list.json`, receipt `records/2026-10-03-project-association-list.md`.
Hold genuine GETs across acknowledged move/newer refresh and navigation without a newer list;
direct context/snapshots need request and view ownership. Auxiliary messages+memory (not
memory-only) publishes the sidebar event; keep failed triggers, healthy controls and diagnostics.

## Live end-to-end harness (`backend/tests/test_live_e2e*.py`)

Real HTTP journeys against a running deployment (the docker-compose stack: `cortex-backend`, `cortex-neo4j`, `cortex-frontend`, `cortex-helper`). Both modules **auto-skip** when no stack/key is present, so the offline suite is unaffected.

- `test_live_e2e.py` — **unauthenticated** journeys: `/health`, the auth boundary (protected endpoints → 401), `/metrics` gate, frontend unauthenticated redirect → `/login`. Override target with `CORTEX_E2E_BASE` / `CORTEX_E2E_FRONTEND`.
- `test_live_e2e_authed.py` — **authenticated** journeys; the key is read from `CORTEX_E2E_API_KEY` (**never hard-coded**) and the module skips without it. Covers authed reads, collections CRUD round-trip, real hybrid search, the fast-path streaming chat journey, and the **full document ingestion → extraction → search → cleanup pipeline**. Only non-destructive writes (uniquely-named temp collection / doc, deleted in-test).

```bash
CORTEX_E2E_API_KEY=<key> .qa-venv/bin/python -m pytest tests/test_live_e2e_authed.py
```

Gotchas learned the hard way:
- Document status lives in the **`processing_status`** field (`pending|processing|extracting|completed|failed`), not `status`.
- Non-streaming `POST /api/ask` returns **504 `deadline_exceeded`** at `ASK_DEADLINE_SECONDS` (~28s) under a slow LLM, and a structured **500 `ask_failed`** on any other failure — documented behavior (both bodies survive production sanitization); the streaming endpoint is the real chat journey.
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
