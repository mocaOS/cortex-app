# Document Processing Pipeline

Full pipeline from upload to graph storage. See [`.claude/domain/relationships.md`](relationships.md) for relationship extraction details and [`.claude/domain/entities.md`](entities.md) for entity resolution.

## Upload

- Modal closes immediately on file selection; upload progress shown inline in document list
- Duplicate detection by filename + filesize
- `source` field tracks document origin — defaults to `"upload"` for UI uploads, `"custom_input"` for custom inputs, or any custom string set via API `source` parameter
- Progress tracked per-document in the frontend

## Docling Conversion

Documents are converted via Docling (PDF, DOCX, PPTX, etc.). The `docling_worker.py` runs as a separate process for CPU-bound ML inference, OCR, and table structure recognition with memory optimizations for large documents. Images are extracted during this phase for later vision analysis.

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

## Per-Chunk Relationship Extraction (during Phase A)

Chunks with 2+ entities get an LLM call via the relationship model to extract relationships using chunk text as direct evidence. See [`.claude/domain/relationships.md`](relationships.md#per-chunk-extraction) for full details.

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
- Graph extraction runs on image content if enabled

### Frontend Image Analysis Awareness

The Knowledge Graph page (Step 1) tracks documents with background image analysis in progress (completed text processing but `image_progress_current < image_progress_total`):
- These docs are shown in a separate "Analyzing Images" tile with an aggregate progress bar
- Step 1 stays "In Progress" until all images are analyzed
- Auto-refresh polls every 5 seconds to keep progress updated
- Step 2/3 remain blocked until image analysis completes
- The "Processed" count in the summary grid only includes `fullyCompletedDocs` (completed AND images done)

See [`.claude/domain/knowledge-graph-ui.md`](knowledge-graph-ui.md) for the full 3-step pipeline UI.

## Subsequent Pipeline Steps

After Phase A completes:
- **Step 2**: Two-phase relationship analysis (batch analysis). See [`.claude/domain/relationships.md`](relationships.md#batch-analysis)
- **Step 3**: Community detection and summarization. See [`.claude/domain/communities.md`](communities.md)
