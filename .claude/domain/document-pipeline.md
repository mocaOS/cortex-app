# Document Processing Pipeline

Full pipeline from upload to graph storage. See [`.claude/domain/relationships.md`](relationships.md) for relationship extraction details and [`.claude/domain/entities.md`](entities.md) for entity resolution.

## Upload

- Modal closes immediately on file selection; upload progress shown inline in document list
- Duplicate detection by filename + filesize
- `source` field tracks document origin — defaults to `"upload"` for UI uploads, `"custom_input"` for custom inputs, or any custom string set via API `source` parameter
- Progress tracked per-document in the frontend

## Docling Conversion

Documents are converted via Docling (PDF, DOCX, PPTX, etc.). The `docling_worker.py` runs as a separate process for CPU-bound ML inference, OCR, and table structure recognition with memory optimizations for large documents. Images are extracted during this phase for later vision analysis.

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
- **Timeout split-retry (2026-07-08):** a multi-chunk batch that times out is also split-retried via the same deque. Rationale: `GRAPH_EXTRACTION_MAX_CONTEXT=256000` packed ~200k-token prompts that Venice qwen3 never answered within the client timeout — whole batches of entities were silently dropped (`entity_count=0` on 300+ page books). Sizing is decode-bound: Venice serves qwen3 at ~70 tok/s (p50; 23 tok/s under load) with a ~120s effective attempt window, so `.env` sets `GRAPH_EXTRACTION_MAX_CONTEXT=24000` (outputs 2-5k tokens, finish first-try) and keeps `EXTRACTION_MAX_OUTPUT_TOKENS=8000` (≈ what 70 tok/s can decode inside the window; 16000 was tried and just converts bounded truncate-splits into 3x-longer timeout-splits). The `extraction_max_context` config property also clamps the *inherited* `OPENAI_MAX_CONTEXT` fallback to 48000 — a chat model's 256k context must not leak into extraction batch sizing (explicit env values are honored as-is). Token estimation also falls back to tiktoken `cl100k_base` for non-OpenAI models instead of `len//4` (chars/4 undercounts dense index/bibliography text at ~2.4 chars/token).

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

### Ingestion prompt-injection scan

After the full text is available in `_process_document` (post-convert,
pre-chunk) the document is scanned once for planted prompt-injection instructions
(`injection_scanner.scan_document`, hooked in a fully-guarded `try/except` so a
scanner failure never fails ingestion). Two layers:

- **Free heuristic** (`prompt_security.scan_untrusted_content`) — always runs;
  a hit short-circuits (no LLM query spent).
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
`Document.injection_flagged`/`injection_reason` (always written, so a clean
reprocess clears a stale flag). `get_all_documents`/`get_document` return both,
surfaced as an "Injection Flagged" badge/filter in the document UI.

**Runtime toggle** (first runtime-editable setting): effective value =
`INGESTION_INJECTION_SCAN` env default overlaid with the `SystemMeta`
override read via `neo4j.get_runtime_setting("ingestion_injection_scan", ...)`.
Admin flips it through `PATCH /api/admin/config` (Admin → Features & Security);
takes effect for subsequent ingestions without a restart. Off = heuristic only
(zero queries). Tests: `backend/tests/test_injection_scanner.py`,
`test_runtime_settings.py`. See `.claude/domain/admin-features.md` (runtime
settings) and `handbook/05-security.md`.

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
