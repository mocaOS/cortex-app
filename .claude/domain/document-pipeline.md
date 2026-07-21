# Document Processing Pipeline

Full pipeline from upload to graph storage. See [`.claude/domain/relationships.md`](relationships.md) for relationship extraction details and [`.claude/domain/entities.md`](entities.md) for entity resolution.

## Upload

- Modal closes immediately on file selection; upload progress shown inline in document list
- Duplicate detection by filename + filesize
- `source` field tracks document origin — defaults to `"upload"` for UI uploads, `"custom_input"` for custom inputs, or any custom string set via API `source` parameter
- Progress tracked per-document in the frontend

**Concurrency cap on auto-started pipelines**: every individually-started pipeline (API upload with `start_processing=true`, text ingestion, single reprocess) funnels through `_process_document_with_cleanup`, gated by a global semaphore sized `BATCH_PROCESSING_CONCURRENCY` (`_get_processing_slots`). A burst of API ingests therefore queues instead of launching one pipeline per document. A doc that has to wait is marked `processing` with progress message "Queued — waiting for a free processing slot" — this keeps `process_pending_documents` and the stranded-doc sweep from grabbing it (it has a live task), and on restart the boot resume resets/resumes it like any stranded `processing` doc. Batch processing (`process_pending_documents`) keeps its own semaphore over the same setting (explicit `concurrency` query param can override it per call). Tests: `backend/tests/test_processing_slots.py`.

## Docling Conversion

Documents are converted via Docling (PDF, EPUB, DOCX, PPTX, etc. — EPUB parses natively as XHTML with no per-page layout ML, so prefer it over a PDF rendering of the same book). The `docling_worker.py` runs as a separate process for CPU-bound ML inference, OCR, and table structure recognition with memory optimizations for large documents. Images are extracted during this phase for later vision analysis.

**Conversion routing** (`_convert_document_subprocess`): when `DOCLING_SERVICE_URL` is set, the file is POSTed to the shared `cortex-helper` `/convert` service (warm converter, ~0.04 s vs ~4.5 s for a cold subprocess that reloads OCR/layout models every call), with automatic fallback to the local subprocess if the service is unreachable. Otherwise it spawns the `docling_worker.py` subprocess. The in-process `DocumentProcessor` converter (`_build_docling_converter` / `_get_converter`) is **lazy** — docling is NOT imported at module scope (that would pull torch + docling-ibm-models ~244 MB into every backend at startup); it builds on first call, which the live subprocess/service path never triggers. See [`environment.md`](../environment.md#shared-model-services-cortex-helper).

**Large-PDF memory (glibc heap retention)**: the chunked page loop calls `_release_heap_to_os()` (`gc.collect()` + `malloc_trim(0)`) after every page chunk. Docling frees its page-image buffers per chunk, but glibc keeps the freed blocks in the arena — measured ~500 MB RSS growth per 25-page chunk (2026-07-08), which SIGKILLed (-9) the worker on 300+ page PDFs inside the 4 GiB container limit; with the trim, RSS plateaus at ~1.6 GB regardless of page count. Chunking knobs live in `.env` (`DOCLING_PAGE_CHUNK_SIZE`/`DOCLING_MAX_PAGES_PER_CHUNK`, `DOCLING_USE_PYPDFIUM_FOR_LARGE_MB`). Note the worker's own `result.input._backend` unload block is redundant — docling ≥2.x unloads page + input backends in `base_pipeline._unload` after each convert; the leak was never un-freed Python objects.

### Raw-text/code fast path

`_process_document` branches **before** the Docling step: files whose extension is in `RAW_TEXT_EXTENSIONS` (code like `.py/.ts/.go` + markup `.md/.rst/.txt`) are read directly via `_read_raw_text_file` and skip Docling entirely — running Docling on source code is wasteful. Code is wrapped in a fenced block with a language hint (`_LANG_BY_EXT`) plus a filename heading; markdown/text pass through verbatim. Everything after (splitter, embedding, graph extraction) is shared. This path is what the [git connector](git-integration.md) uses for repo files and wiki pages. `store_file_only`/`process_file` accept an optional `git_provenance` dict that sets the document's git provenance fields.

## Chunking

Converted text is split into chunks controlled by:
- `CHUNK_SIZE` — target chunk size
- `CHUNK_OVERLAP` — overlap between chunks
- `CHUNK_BY` — word or sentence-level chunking

## Embedding

Chunks are embedded using the configured embedding model. See [`.claude/environment.md`](../environment.md#embeddings) for embedding configuration.

**Input guards (2026-07-08)**: before the embed pass, `_drop_empty_chunks` removes empty/whitespace-only chunks, and `_enforce_embed_token_cap` is **token-accurate** — `_token_len` (lazy tiktoken `cl100k_base`, char-heuristic fallback) re-verifies every sub-split piece via `_split_to_token_budget`. Rationale: the 2.8 chars/token heuristic undercounts dense punctuation/number text (a book index page measured ~2.4 chars/token → a ~9.5k-token chunk passed a "8192-token" char cap). The upstream 400 that follows is wrapped by the Venice gateway in an **HTTP 200 envelope with `data: null`**, which the OpenAI SDK parses as success and the Langfuse instrumentation then kills with `TypeError: object of type 'NoneType' has no len()` — failing the whole document (Haystack's embedder only catches `APIError`). The embed call also gets one retry for transient variants of that failure. Tests: `backend/tests/test_embed_input_guards.py`.

**Post-batch recovery (`_recover_missing_embeddings`)**: a batched embed request is rejected wholesale when ANY input fails provider validation — Haystack skips the whole batch (`raise_on_failure=False`), silently stripping embeddings from all 32 docs. Venice also validates its 8192-token input cap with its **own tokenizer** (~1.2-1.4× cl100k on punctuation-heavy text; measured: a 5,795-cl100k-token chunk rejected as >8192), so a chunk can pass the client-side cap and still 400. After the batch pass, docs left without embeddings are re-embedded individually; a doc the provider still rejects is halved until accepted (or ≤512 chars → stored unembedded, surfacing via the degraded signal). `.env` sets `EMBEDDING_MAX_INPUT_TOKENS=5400` to keep client capping under Venice's counter. The capping/batching budgets are part of `_reprocess_config_hash`, so changing them forces a real reprocess instead of a delta skip.

## Entity Extraction (Phase A — per-document)

Triggered via "Extract Entities" on Knowledge Graph page or "Generate Graph" button on Documents/Knowledge Graph page.

- LLM entity extraction with fuzzy entity resolution (Levenshtein 85% dedup + optional embedding-based semantic matching via `ENABLE_SEMANTIC_ENTITY_RESOLUTION`). See [`.claude/domain/entities.md`](entities.md#fuzzy-resolution) for resolution details.
- Entity type normalization (10 allowed types, fuzzy matched). See [`.claude/domain/entities.md`](entities.md#type-normalization).
- Fuzzy entity-to-chunk linking

### Extraction Batching, Auto-Summary & Truncation Guard

`extract_entities_from_document_async` packs ALL of a document's chunks into as few LLM calls as fit the `GRAPH_EXTRACTION_MAX_CONTEXT` token budget (0.8× budget minus prompt overhead and a 1,500-token output reserve; 1-chunk overlap between batches). Most documents are single-batch.

- **Auto-summary (since 2026-07-03):** the per-document summary LLM call (`generate_document_summary_async`, ≤1,000 output tokens over the first 10k chars) is made **only for multi-batch documents**, where it provides cross-batch context. A single-batch prompt already contains the full document text, so the summary was provably redundant there — skipping it removes ~1 LLM call per document (~half of entity-phase call volume). Callers pass `document_summary=None` (auto); passing a string forces the legacy explicit-summary behavior.
- **Truncation split-retry:** a batch whose response hits the output cap (`finish_reason == "length"`, cap = `EXTRACTION_MAX_OUTPUT_TOKENS`) would silently lose its tail entities. The truncated output is discarded and the batch is split in half and retried (deque; halves may split again). A truncated single-chunk batch keeps whatever parsed and logs a warning to raise the cap. Tests: `backend/tests/test_entity_extraction_batching.py`.
- **Timeout split-retry (2026-07-08):** a multi-chunk batch that times out is also split-retried via the same deque. Rationale: `GRAPH_EXTRACTION_MAX_CONTEXT=256000` packed ~200k-token prompts that Venice qwen3 never answered within the client timeout — whole batches of entities were silently dropped (`entity_count=0` on 300+ page books). Sizing is decode-bound and gateway-dependent (at ~70 tok/s a full-window batch can't finish in the request window); the current recommended default is `GRAPH_EXTRACTION_MAX_CONTEXT=16000`, validated zero-truncation on Venice qwen3-6-27b. The `extraction_max_context` config property also clamps the *inherited* `OPENAI_MAX_CONTEXT` fallback to 48000 — a chat model's 256k context must not leak into extraction batch sizing (explicit env values are honored as-is). Token estimation also falls back to tiktoken `cl100k_base` for non-OpenAI models instead of `len//4` (chars/4 undercounts dense index/bibliography text at ~2.4 chars/token).
- **Output-cap sizing (2026-07-09):** recommended `EXTRACTION_MAX_OUTPUT_TOKENS=16000` — a generous CEILING matched to `GRAPH_EXTRACTION_MAX_CONTEXT=16000`, NOT a ½-ratio. The primary lever against entity-dense overflow is the **terse-description extraction prompt** (`ENTITY_EXTRACTION_SYSTEM_PROMPT` caps descriptions to a ~12-word clause; enrichment restores depth), which bounds output-per-entity; with it, 16000/16000 was validated zero-truncation / zero-entity-loss across dense docs on Venice qwen3-6-27b. The competing constraint is the decode window: the cap must also fit `decode tok/s × attempt window` (on Venice ~70 tok/s × ~120s ≈ 8400, which is why 16000 there just converted truncate-splits into 3×-longer timeout-splits, measured 2026-07-08). When the two conflict on a slow gateway, **lower `GRAPH_EXTRACTION_MAX_CONTEXT` instead of raising the output cap**. The telemetry (below) shows which side is binding: `finish=length` lines → raise output/shrink input; `timed out` warnings → shrink input.
- **Run telemetry (2026-07-09):** the extraction loop reports progress at the **start** of every batch call (`Finding entities: 120/741 chunks (batch 8/22)...` — the denominator grows by 2 per split: the burned call plus the two half-calls replacing one planned call, so the numerator can't overrun it), so multi-minute local-model calls no longer read as a hang in the UI. Each batch logs one INFO line with chunks, `in≈`/`out=` token usage (provider `usage` when reported, cl100k estimate otherwise), duration, and `finish_reason`. Repeated output-cap overflows (≥3 at ≥25% of calls) trigger a one-shot `output budget looks too small` WARNING naming both knobs; repeated timeout splits (same thresholds) trigger a one-shot `batches keep timing out` WARNING naming `GRAPH_EXTRACTION_MAX_CONTEXT` / `BATCH_PROCESSING_CONCURRENCY` / `LLM_REQUEST_TIMEOUT_SECONDS`. A ≥30-entity response with >50% duplicate names logs `model repetition loop suspected` (observed live: 288 "entities" from one 5-sentence chunk); dense-but-unique output (>40 entities/chunk, e.g. index/bibliography passages) is counted separately as `dense` at INFO — the 2026-07-09 field run showed the >40/chunk arm alone produced only false positives. On settle, a health summary logs planned batches vs actual LLM calls, split/truncation/error/rate-limit/suspect/dense counters, and token totals; the same counters are persisted as `d.extraction_stats` (JSON string, `set_document_extraction_stats`) for post-hoc tuning — nothing in the frontend reads it. Callers get the counters via the `run_stats` out-param. Tests: `backend/tests/test_entity_extraction_batching.py` (TestExtractionTelemetry, TestTimeoutHandling).
- **Timeout-adaptive batch sizing + one-shot transport (2026-07-09):** a timeout split halves the failed batch AND records `max(1500, batch_tokens // 2)` as a per-extraction-config batch-budget cap on the shared extractor (`_learned_entity_budget`), so later documents in the same process plan at a size the endpoint has proven it can answer instead of re-running the split cascade per document (field run: after 7h of ~30 timeouts/hour, each fresh doc still opened full-size; 15 planned batches ballooned to 77 calls / 4.8h on one doc). Config change or restart resets it. Batch calls also use a `max_retries=0` one-shot client (`_oneshot_async_client`) — the split IS the retry; the SDK used to re-send the same ~17k-token prompt twice more into the already-saturated endpoint (the field "361s timeouts" were 3×120s attempts against the then-hardcoded 120s client timeout, which also shadowed `LLM_REQUEST_TIMEOUT_SECONDS`). 429s are requeued whole (batch size isn't the problem) with bounded exponential backoff (`rate_limit_retries` counter). The per-chunk relationship paths get the same one-shot client and no longer tenacity-retry timeouts — a timed-out batched call falls straight to the single-chunk fallback.

### Entity Embedding & Storage

When `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true`, all entity embeddings for a document are generated in **one batched HTTP call** via `graph_extractor.generate_entity_embeddings_batch_async(entities, batch_size=64)` before the storage loop runs. Previous behavior was one HTTP call per entity (158 sequential calls observed at ~3-5s each → ~8-13 min); batched it's ~1-3 calls totalling a few seconds. Per-batch failures degrade to `None` in the aligned result list so the loop falls back to Levenshtein resolution for those entities — the document doesn't fail.

The storage loop then iterates entities and emits a progress message every ~10% (`"Storing entity N/total..."` across progress 70-84%), so the UI no longer freezes on the same `"Storing X entities..."` string for the whole loop.

## Per-Chunk Relationship Extraction (during Phase A)

Chunks with 2+ entities get an LLM call via the relationship model to extract relationships using chunk text as direct evidence. See [`.claude/domain/relationships.md`](relationships.md#per-chunk-extraction) for full details — including the streaming `asyncio.as_completed` pattern that stores each chunk's relationships live (counter ticks up in real time) instead of bulk-committing after the whole batch.

- Concurrency controlled by `CONCURRENT_RELATIONS` (default 3)
- Stored with `extraction_method='per_chunk'`
- Tenacity retry with exponential backoff (4 attempts, 2-30s wait) for rate limit errors

## Neo4j Storage

Entities, chunks, and per-chunk relationships are stored in Neo4j after extraction.

## Background Image Analysis

Runs asynchronously after text processing completes:

- Images extracted during Docling conversion are analyzed concurrently via vision model
- Gated by a configurable semaphore — `VISION_MAX_CONCURRENT` (default 2, controls semaphore + thread pool sizing)
- Progress tracked per-document via `image_progress_current`/`image_progress_total`/`image_progress_message` properties
- Image chunks created with type `image_analysis` and `chunk_index` 1000+
- Graph extraction runs on image content if enabled. When `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true`, each image's extracted entities are batch-embedded (one `generate_entity_embeddings_batch_async()` call per image) before `store_graph_extraction()`, so they flow through `store_entity_with_embedding()` and land in the same `entity_embedding` vector index that text entities populate. The image and text surfaces now share one dedup signal — see [`entities.md`](entities.md#fuzzy-resolution).
- Reasoning is suppressed on capable multimodal models via `VISION_REASONING_MODE` (default `off`). `vision_analyzer.py` uses raw httpx, so it merges `flatten_reasoning_body()` output into the `/chat/completions` JSON body and falls back once on 400, marking the model via `mark_reasoning_unsupported`. Lets you point both `GRAPH_EXTRACTION_MODEL` and `VISION_MODEL` at e.g. Qwen3-VL-27B with one endpoint. See [`.claude/environment.md`](../environment.md#reasoning-control-ingestion).

### Ingest checkpoint/resume + endpoint-outage pause

`ENABLE_INGEST_RESUME` (default **true**) makes Step 1 resumable at sub-document
granularity — before it, every recovery path (startup orphan reset, stranded
sweep, manual reprocess) restarted `_process_document` from zero, re-paying
Docling, a full embed pass, and every extraction batch even when 95% of the
work was already persisted (live incident 2026-07-20: a litellm restart at 95%
of a 3000-chunk document restarted it from scratch).

Three pieces of durable state, all keyed to a fingerprint (file SHA-256 +
`_reprocess_config_hash`) now written at the START of each run:

1. **Chunk reuse** — `_prepare_ingest_resume` (top of `_process_document`):
   when the fingerprint matches the stored chunks (`text_chunk_count > 0`, no
   unembedded chunks), conversion/splitting/embedding/storage are skipped and
   the chunk list is rebuilt from Neo4j (`get_text_chunks_for_document`). A
   mismatch (config or file changed) deletes the stale chunks first — which
   also fixes the old orphan-reset path that could mix chunkings.
   `queue_document_for_reprocessing`/`reprocess_document*` keep resumable
   chunks (`_cleanup_before_reprocess`) instead of unconditionally deleting.
2. **Entity-extraction watermark** — the extractor fires `on_batch_entities`
   after each settled batch; the processor stores that batch's entities
   immediately (`_store_entity_batch`, batched or sequential) and persists the
   covered chunk-index ranges as `Document.extraction_done_ranges` (JSON
   `[[lo,hi),...]`). On resume the ranges become `skip_chunk_indices` (batches
   pack only remaining chunks; progress continues at e.g. 2850/3000), and
   previously-stored entities are merged back for linking via
   `get_entities_for_document_provenance` (`e.source_documents` — the only
   entity↔doc link that exists before the MENTIONS phase).
3. **Relationship flags** — each per-chunk relationship unit that settles
   marks its chunks `Chunk.rels_extracted = true` (a chunk can legitimately
   yield 0 relationships, so this can't be inferred from stored edges); a
   resumed run skips flagged chunks.

The checkpoint is cleared on successful completion and when reusing chunks of
a doc that previously **completed** (deliberate re-run → extraction redone
fully). Resume state is only consulted when the interrupted run never
completed. On a resume-reuse run, unfinished image analysis is relaunched
through `resume_image_analysis` after the text pipeline completes.

**Endpoint-outage pause** (`LLM_OUTAGE_MAX_WAIT_SECONDS`, default 900):
`_is_connection_error` (graph_extractor) classifies endpoint-down failures —
connection refused/reset, `Connect*` class names, gateway 502/503/504 —
separately from timeouts (split-retry) and 429s (requeue). Name-based on
purpose: openai's `APITimeoutError` subclasses `APIConnectionError`. Before
this, a hard-down endpoint fell into the generic error arm, which **dropped
every remaining batch in seconds** (connection-refused fails in ms) and let
the document "complete" with massive silent entity loss. Now:

- **Entity batches**: the failed batch is requeued and retried with backoff
  (5s→60s, capped to remaining budget); the requeued batch IS the probe. Any
  response resets the outage clock. `connection_retries` counter in run
  telemetry.
- **Per-chunk relationships**: `extract_chunk_relationships_async`/`_batch_async`
  re-raise connection errors (excluded from their tenacity retries) so the
  processor's outage loop requeues the affected work units and sleeps between
  rounds, instead of every concurrent call spinning its own retries.
- Past the budget, `ExtractionEndpointUnavailable` fails the document with the
  checkpoint intact; the error message tells the operator a reprocess resumes
  from the checkpoint. Progress heartbeats during waits keep the stranded
  sweep from resetting the doc.

**User-visible paused state**: two additive Document fields drive the UI
(neither is a new `ProcessingStatus`):

- `processing_paused` + `paused_reason` — set on outage-wait TRANSITIONS
  (`set_document_pause_state`, which also bumps the heartbeat): the entity
  loop signals via the extractor's `on_outage_state` callback
  (transition-only, best-effort — a callback failure never affects the run);
  the relationship outage loop sets/clears it directly. Cleared at run start,
  on recovery, on completion, and on any failure; the orphan/stranded resets
  clear it too so a `pending` doc never shows a stale pause.
- `resume_available` — set (`set_document_resume_available`) when the outage
  budget fails the document; means "failed, but the checkpoint survives — a
  reprocess resumes". Cleared at the start of every run.

Both are returned by `get_all_documents`/`get_document` and rendered by the
frontend as amber "Paused"/"Interrupted" badges — see
[`frontend-patterns.md`](../frontend-patterns.md#paused--interrupted-documents-ui).

Tests: `backend/tests/test_ingest_resume.py`.

### Startup recovery of orphaned in-flight documents

Processing runs as in-process background tasks, so a backend restart (every
redeploy/upgrade in the per-tenant deploy model) orphans any document left in a
transient state (`processing`/`extracting`) — its spinner would never resolve
and `/api/instance/status` would report `safe_to_redeploy: false` forever. The
lifespan startup (`backend/app/main.py`) calls
`Neo4jService.reset_orphaned_processing_documents()` right after schema init,
which resets every transient-state document back to `pending` (truthful
"waiting to be processed" state, rejoins the queue) with an explanatory
`progress_message`. Safe because at startup no processing can legitimately be in
flight. A `WARNING` log line lists the reset ids. Separately,
`DOCLING_CONVERSION_TIMEOUT` (default 600s) prevents a *new* hang: a stuck local
docling subprocess is killed and the document marked `failed` instead of pinning
the status at `processing`.

### Startup resume of killed image analysis

The orphan-reset above never catches interrupted **image** analysis: it runs as
fire-and-forget futures *after* the text pipeline finishes, so the document is
already `completed` when a restart kills them — the counters freeze at
`image_progress_current < total`, Step 1 on `/extract` reads "finishing image
analysis" forever, and no LLM traffic flows (live incident 2026-07-10: 34 docs
stuck at 8/2023 images). Since 2026-07-10 the lifespan startup also calls
`Neo4jService.get_documents_with_incomplete_image_analysis()` and, when
`AUTO_RESUME_IMAGE_ANALYSIS=true` (default), resumes them sequentially under an
`image_analysis_resume` task (quota-guarded, 10s after boot):

- `DocumentProcessor.resume_image_analysis(doc)` re-extracts images via Docling
  re-conversion (`_convert_document_subprocess` — CPU only, no LLM cost; the
  serialized images only ever lived in the dead process's memory).
- Already-analyzed images are skipped via
  `get_existing_image_chunk_indices()` — image chunk ids are deterministic
  (`{doc_id}_image_{idx}`), so paid vision/extraction work is never redone and
  text chunks/entities/relations are untouched.
- Irrecoverable docs (source file missing / no vision model / re-conversion
  finds no images) get their counters **force-closed** with an explanatory
  `image_progress_message` so they stop reading as in-flight; conversion
  errors leave the counters stuck for retry on the next startup.
- Tests: `backend/tests/test_image_analysis_resume.py`.

### Degraded-document signals

A document can "complete" while being useless for retrieval (e.g. extraction
request timeouts → 0 entities; image-chunk embed step returns nothing →
chunks without embeddings). Two persisted signals make this visible:

- `Document.entity_count` — set by the completion `update_document_status`
  call, **only when graph extraction actually ran** (extractor available +
  `enable_graph_extraction`); otherwise unset, so 0-entities-by-design is
  never flagged. Reads coalesce to `-1` = unknown → never degraded.
- `Chunk.has_embedding` — boolean mirror written by `store_chunk`, so
  embedding coverage is queryable without streaming vectors.

**Degraded** = status `completed` AND (`entity_count == 0` OR any chunk with
`has_embedding = false`). `get_all_documents`/`get_document` return
`entity_count` + `unembedded_chunk_count` (only `false` counts — `NULL`
pre-backfill chunks don't false-positive).

**Startup backfill**: `Neo4jService.backfill_degraded_document_signals()`
runs as a non-blocking background task in the lifespan (after the orphaned-doc
reset): derives `has_embedding` for pre-existing chunks and computes
`entity_count` for completed docs via `(d)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(e)`
(entity part skipped when extraction is disabled). Batched `CALL {} IN
TRANSACTIONS` on an auto-commit session; idempotent (only NULL fields touched).

**Reprocess delta bypass**: `_reprocess_delta_skip` returns False for a
degraded document even when file hash + config hash match — the fingerprint
query (`get_document_fingerprint`) also returns both signals. Otherwise a
degraded doc's reprocess would be no-op'd as "Content unchanged".
Tests: `backend/tests/test_degraded_documents.py`.

### Ingestion prompt-injection scan (EXPERIMENTAL, off by default)

The whole feature is gated behind `ENABLE_INGESTION_INJECTION_SCAN`
(default **false**). While off, the feature is completely absent: the scan
block in `_process_document` is skipped (not even the free heuristic runs),
the admin toggle is hidden (`enable_ingestion_injection_scan=false` in
`SystemConfigResponse`), and `PATCH /api/admin/config` rejects the runtime
toggle with 400. Everything below applies only when the flag is set.

After the full text is available in `_process_document` (post-convert,
pre-chunk) the document is scanned once for planted prompt-injection instructions
(`injection_scanner.scan_document`, hooked in a fully-guarded `try/except` so a
scanner failure never fails ingestion). Two layers:

- **Free heuristic** (`prompt_security.locate_untrusted_injection`) — always
  runs, but its verdict is only final when the LLM layer is off or unreachable.
  With the classifier enabled, a heuristic hit **escalates instead of flags**:
  the classifier re-judges a `WINDOW_CHARS` excerpt centered on the match and
  the document is flagged only on confirmation (`method: "heuristic+llm"`);
  a refuted hit is logged and the normal windowed sweep continues. Rationale:
  the regexes are tuned for short user queries and over-match on long prose
  (a real document was flagged because "Danube canal — the modest…" matched
  the jailbreak pattern — since fixed with word boundaries, see
  `INJECTION_PATTERNS` comments).
- **LLM classifier** — runs only when the runtime toggle is on; scans the text
  in windows (`WINDOW_CHARS`/`MAX_WINDOWS`, head+tail when over the cap),
  short-circuits on the first positive. Uses the **extraction tier**
  (`get_extraction_llm_config` + `safe_chat_completion`, same as graph
  extraction) so it is auto-metered as processing usage (`KIND_PROCESSING`,
  already set at pipeline entry — counts toward the monthly LLM quota) and
  Langfuse-traced via the factory client. Reasoning is **forced OFF**
  (`ReasoningMode.OFF`, no overrides) — a binary classifier needs no think
  budget and thinking would slow every ingested doc. Prompted to distinguish
  content that *contains* an injection from content that *discusses* it.

Result is **flag-only** (never blocks): `set_document_injection_flag` persists
`Document.injection_flagged`/`injection_reason` (always written while the
feature is on, so a clean reprocess clears a stale flag; with the feature off
existing flags are left untouched). `get_all_documents`/`get_document` return
both, surfaced as an "Injection Flagged" badge/filter in the document UI —
the filter option only renders when at least one document is flagged, so a
default (disabled) instance never shows it.

**Runtime toggle** (first runtime-editable setting): effective value =
`INGESTION_INJECTION_SCAN` env default overlaid with the `SystemMeta`
override read via `neo4j.get_runtime_setting("ingestion_injection_scan", ...)`;
forced to false while `ENABLE_INGESTION_INJECTION_SCAN` is off. Admin flips it
through `PATCH /api/admin/config` (Admin → Features & Security — the toggle
only renders when the experimental flag is on); takes effect for subsequent
ingestions without a restart. Off = heuristic only (zero queries). Tests:
`backend/tests/test_injection_scanner.py`, `test_runtime_settings.py`. See
`.claude/domain/admin-features.md` (runtime settings) and
`handbook/05-security.md`.

### Step 1 Gate on Image Analysis

`_run_batch_processing_task` in `backend/app/main.py` calls `_wait_for_image_analysis_complete()` after text processing finishes. That helper polls Neo4j every 3 s for any document with `processing_status == "completed" AND image_progress_current < image_progress_total`, updating the task's progress message with `"... — analyzing images: N/total across K document(s)..."` until none remain. Only then does `complete_task` run, which lets the backend chain advance to Step 2.

Frontend display in Step 1:
- Documents still analyzing images are shown in a separate "Analyzing Images" tile with an aggregate progress bar
- The "Processed" count in the summary grid only includes `fullyCompletedDocs` (completed AND images done)
- Step 1's UI status follows the backend task state — "In Progress" until the task completes

See [`.claude/domain/knowledge-graph-ui.md`](knowledge-graph-ui.md) for the full 3-step pipeline UI and chain semantics.

## Subsequent Pipeline Steps

After Phase A completes:
- **Step 2**: Two-phase relationship analysis (batch analysis). See [`.claude/domain/relationships.md`](relationships.md#batch-analysis)
- **Step 3**: Community detection and summarization. See [`.claude/domain/communities.md`](communities.md)

## v-Next Efficiency & Hardening

- **Helper transport** (`services/helper_client.py`): all cortex-helper calls (convert + rerank) share one HTTP client with 3 retries (backoff+jitter, transient errors only) and a per-operation circuit breaker — a network blip no longer instantly degrades a tenant to its local fallback. `HELPER_STRICT_REMOTE=true` makes conversion failure mark the document failed instead of pulling docling into the tenant container. Rerank failure stays no-rerank (safe).
- **Reprocess delta** (`ENABLE_REPROCESS_DELTA`): on successful processing the Document gets a fingerprint (`file_sha256` + `reprocess_config_hash`); a reprocess whose file bytes and extraction config both match is skipped entirely ("Content unchanged"). Chunks also carry an additive `content_hash` property.
- **Image entity-embedding cache**: one per-document cache dict dedups entity embeddings across a document's images (same logo/diagram entity embedded once).
- **Conversion metrics**: `cortex_document_conversion_seconds{path=remote|local}` + helper request counters; documents-processed counters by outcome.
