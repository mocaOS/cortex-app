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

### Raw-text/code fast path

`_process_document` branches **before** the Docling step: files whose extension is in `RAW_TEXT_EXTENSIONS` (code like `.py/.ts/.go` + markup `.md/.rst/.txt`) are read directly via `_read_raw_text_file` and skip Docling entirely — running Docling on source code is wasteful. Code is wrapped in a fenced block with a language hint (`_LANG_BY_EXT`) plus a filename heading; markdown/text pass through verbatim. Everything after (splitter, embedding, graph extraction) is shared. This path is what the [git connector](git-integration.md) uses for repo files and wiki pages. `store_file_only`/`process_file` accept an optional `git_provenance` dict that sets the document's git provenance fields.

## Chunking

Converted text is split into chunks controlled by:
- `CHUNK_SIZE` — target chunk size
- `CHUNK_OVERLAP` — overlap between chunks
- `CHUNK_BY` — word or sentence-level chunking

## Embedding

Chunks are embedded using the configured embedding model. See [`.claude/environment.md`](../environment.md#embeddings) for embedding configuration.

## Entity Extraction (Phase A — per-document)

Triggered via "Extract Entities" on Knowledge Graph page or "Generate Graph" button on Documents/Knowledge Graph page.

- LLM entity extraction with fuzzy entity resolution (Levenshtein 85% dedup + optional embedding-based semantic matching via `ENABLE_SEMANTIC_ENTITY_RESOLUTION`). See [`.claude/domain/entities.md`](entities.md#fuzzy-resolution) for resolution details.
- Entity type normalization (10 allowed types, fuzzy matched). See [`.claude/domain/entities.md`](entities.md#type-normalization).
- Fuzzy entity-to-chunk linking

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
- Gated by a configurable semaphore — `VISION_MAX_CONCURRENT` (default 3, controls semaphore + thread pool sizing)
- Progress tracked per-document via `image_progress_current`/`image_progress_total`/`image_progress_message` properties
- Image chunks created with type `image_analysis` and `chunk_index` 1000+
- Graph extraction runs on image content if enabled. When `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true`, each image's extracted entities are batch-embedded (one `generate_entity_embeddings_batch_async()` call per image) before `store_graph_extraction()`, so they flow through `store_entity_with_embedding()` and land in the same `entity_embedding` vector index that text entities populate. The image and text surfaces now share one dedup signal — see [`entities.md`](entities.md#fuzzy-resolution).
- Reasoning is suppressed on capable multimodal models via `VISION_REASONING_MODE` (default `off`). `vision_analyzer.py` uses raw httpx, so it merges `flatten_reasoning_body()` output into the `/chat/completions` JSON body and falls back once on 400, marking the model via `mark_reasoning_unsupported`. Lets you point both `GRAPH_EXTRACTION_MODEL` and `VISION_MODEL` at e.g. Qwen3-VL-27B with one endpoint. See [`.claude/environment.md`](../environment.md#reasoning-control-ingestion).

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
