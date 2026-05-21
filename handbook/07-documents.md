# Chapter 7: Document Management

This chapter covers everything about uploading, processing, viewing, and managing documents in the Library.

## Supported File Formats

The Library supports 30+ file formats via the Docling conversion engine:

| Category | Extensions | Notes |
|----------|-----------|-------|
| **PDF** | `.pdf` | Full text extraction, table recognition (TableFormer), image extraction |
| **Word** | `.docx` | Structure preservation, image extraction |
| **PowerPoint** | `.pptx` | Slide text and embedded images |
| **Excel** | `.xlsx` | Tabular data conversion |
| **HTML** | `.html` | Markup stripping, content preservation |
| **Markdown** | `.md`, `.markdown` | Native support |
| **Plain Text** | `.txt` | Direct ingestion |
| **reStructuredText** | `.rst` | Technical documentation |
| **LaTeX** | `.tex` | Academic papers |
| **XML** | `.xml` | Structured data |
| **Images** | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp` | Via vision model analysis |
| **Audio** | `.wav`, `.mp3`, `.webvtt` | Transcription |

Maximum file size: 50 MB by default (configurable via `MAX_FILE_SIZE_MB`).

## Uploading Documents

### Via the Web Interface

1. Navigate to the **Documents** page (`/documents`)
2. Click the **Upload** button in the top-right
3. The upload modal appears with:
   - **Drag-and-drop zone** — Drop files directly onto the modal
   - **File browser** — Click to select files
   - **Collection selector** — Choose which collection to add the document to (optional)
4. Select your files — the modal closes immediately
5. Upload progress appears **inline in the document list** for each file

**Duplicate detection**: The Library checks filename + file size to prevent accidental re-uploads. If a duplicate is detected, you'll be notified.

### Via the API

**Single file upload:**

```bash
curl -X POST http://localhost:8000/api/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@document.pdf"
```

**Upload to a specific collection:**

```bash
curl -X POST "http://localhost:8000/api/upload?collection_id=my-collection" \
  -H "X-API-Key: your-api-key" \
  -F "file=@document.pdf"
```

**Upload with a custom source** (for API integrations):

```bash
curl -X POST "http://localhost:8000/api/upload?source=youtube-transcriber" \
  -H "X-API-Key: your-api-key" \
  -F "file=@transcript.md"
```

The `source` parameter identifies where the document came from. UI uploads default to `"upload"`. When building custom apps that interface with the Library API, set this to your app's identifier (e.g. `"slack-bot"`, `"notion-sync"`, `"youtube-transcriber"`) to categorize documents by origin. The source is displayed in the document list and can be filtered on.

**Upload without immediate processing** (for bulk uploads):

```bash
curl -X POST "http://localhost:8000/api/upload?start_processing=false" \
  -H "X-API-Key: your-api-key" \
  -F "file=@document.pdf"
```

### Bulk Upload Workflow

For large uploads (100+ files), the recommended workflow is:

1. **Upload all files without processing:**

```bash
for file in ./documents/*.pdf; do
  curl -X POST "http://localhost:8000/api/upload?start_processing=false" \
    -H "X-API-Key: your-api-key" \
    -F "file=@$file"
done
```

2. **Start batch processing:**

```bash
curl -X POST "http://localhost:8000/api/documents/process-pending?concurrency=5" \
  -H "X-API-Key: your-api-key"
```

This returns a `task_id` for monitoring.

3. **Monitor progress:**

```bash
curl http://localhost:8000/api/tasks/{task_id} \
  -H "X-API-Key: your-api-key"
```

The batch processing concurrency is controlled by `BATCH_PROCESSING_CONCURRENCY` (default: 2).

## The Processing Pipeline

When a document is processed, it passes through these stages:

### Stage 1: Document Conversion (Docling)

The document is converted to markdown text using Docling in a subprocess (to avoid Python GIL contention). A conversion semaphore limits this to one document at a time to prevent memory overload.

**Docling capabilities:**
- Table structure recognition using TableFormer (ACCURATE mode)
- EasyOCR for text in images (English + German, GPU-accelerated when available)
- Image extraction from PDFs, Word docs, and presentations
- 8-thread acceleration with auto-detected compute device (CUDA/MPS/CPU)

### Stage 2: URL Protection and Cleanup

Before chunking:
- URLs are replaced with placeholders (`§§URL_PLACEHOLDER_N§§`) to prevent splitting across chunks
- HTML image comments from Docling are removed
- Excessive newlines (3+) are collapsed to 2

### Stage 3: Chunking

Text is split into chunks using the configured strategy:

| Strategy | Config | Default | Overlap |
|----------|--------|---------|---------|
| **Sentence** | `CHUNK_BY=sentence` | 5 sentences/chunk | 1 sentence |
| **Word** | `CHUNK_BY=word` | 500 words/chunk | 50 words |

After chunking, URL placeholders are restored.

### Stage 4: Embedding

Each chunk is embedded using the configured embedding model. The Library creates embeddings via the OpenAI-compatible API endpoint.

### Stage 5: Entity Extraction (Phase A)

If `ENABLE_GRAPH_EXTRACTION=true`, each document's chunks are analyzed by an LLM to extract entities:

1. Chunks are batched by token budget (fitting within `GRAPH_EXTRACTION_MAX_CONTEXT`)
2. Each batch includes 1-chunk overlap with the previous batch for continuity
3. The LLM returns entities in XML format
4. Entities are stored with fuzzy resolution (85% Levenshtein threshold)
5. Entity types are normalized to the 10 allowed types
6. Entities are linked to their mention chunks via fuzzy substring matching

### Stage 6: Neo4j Storage

Documents, chunks (with embeddings), and entities are stored in Neo4j as graph nodes with appropriate relationships.

### Stage 7: Background Image Analysis (Async)

Images extracted during Docling conversion are analyzed concurrently in the background:

1. All images are launched for analysis via `asyncio.gather()`
2. A global semaphore (`VISION_MAX_CONCURRENT`) limits concurrent vision API calls
3. Each image is analyzed by the vision model (or Docling fallback)
4. Image descriptions become searchable chunks (type `image_analysis`, chunk_index 1000+)
5. Entity extraction runs on image content if enabled
6. Per-document progress tracked via `image_progress_current` / `image_progress_total`

Image analysis does **not** block text processing — your document becomes searchable immediately, and image-derived knowledge is added asynchronously.

### Stage 8: Collection Assignment

If a `collection_id` was specified during upload, the document is added to that collection.

## Custom Inputs

You can add knowledge directly without uploading files. Three types are supported:

### Q&A Pairs

Question-and-answer pairs that become searchable knowledge:

```bash
curl -X POST http://localhost:8000/api/custom-input \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "qa",
    "content": "What is the capital of France?",
    "answer": "Paris is the capital of France.",
    "collection_id": "general-knowledge"
  }'
```

Custom inputs default to `source: "custom_input"`. You can override this with a custom source to categorize programmatically-created content:

```bash
curl -X POST http://localhost:8000/api/custom-input \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "text",
    "content": "Meeting notes from Q1 planning session...",
    "source": "meeting-bot"
  }'
```

### Text

Freeform text content:

```bash
curl -X POST http://localhost:8000/api/custom-input \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "text",
    "content": "Key takeaway: We are migrating to Kubernetes by Q3 2026."
  }'
```

### Markdown

Formatted markdown documents:

```bash
curl -X POST http://localhost:8000/api/custom-input \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "text",
    "content": "# Project Overview\n\nThis is a markdown document explaining..."
  }'
```

Custom inputs go through the same processing pipeline as uploaded documents — chunking, embedding, entity extraction, and graph building. An LLM generates a descriptive filename for each custom input.

**Topic generation**: You can ask the LLM to suggest a topic/title:

```bash
curl -X POST http://localhost:8000/api/custom-input/generate-topic \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"content": "Your content here", "input_type": "text"}'
```

This returns a `topic_hint` and a list of `existing_similar` topics to avoid duplication.

## Document Viewing

- **Markdown files** (`.md`) open in an in-app Markdown viewer modal — rendered with full formatting
- **PDF files** and all other types open in a new browser tab via the file serving endpoint (`/api/documents/{id}/file`). The browser decides whether to display (PDF, images) or download (Word, Excel) the file.

## Document Operations

### Listing Documents

```bash
# List all documents
curl http://localhost:8000/api/documents \
  -H "X-API-Key: your-api-key"
```

Returns metadata for each document including filename, file type, size, upload date, processing status, chunk count, source, and progress fields.

### Getting Document Details

```bash
# Get metadata only
curl http://localhost:8000/api/documents/{id} \
  -H "X-API-Key: your-api-key"

# Get metadata + full chunk content
curl http://localhost:8000/api/documents/{id}/content \
  -H "X-API-Key: your-api-key"
```

The `/content` endpoint returns all chunks ordered by chunk_index, plus a concatenated `full_content` field.

### Reprocessing Documents

Re-run the processing pipeline on existing documents:

```bash
# Reprocess a single document
curl -X POST http://localhost:8000/api/documents/{id}/reprocess \
  -H "X-API-Key: your-api-key"

# Reprocess with a new file (replaces the original)
curl -X POST http://localhost:8000/api/documents/{id}/reprocess \
  -H "X-API-Key: your-api-key" \
  -F "file=@updated-document.pdf"

# Bulk reprocess multiple documents
curl -X POST http://localhost:8000/api/documents/reprocess \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_ids": ["id1", "id2", "id3"]}'
```

Reprocessing deletes existing chunks and entities for the document, then re-runs the full pipeline.

### Moving Documents

```bash
curl -X POST http://localhost:8000/api/documents/move \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_ids": ["id1", "id2"], "collection_id": "target-collection"}'
```

### Deleting Documents

```bash
# Delete a single document
curl -X DELETE http://localhost:8000/api/documents/{id} \
  -H "X-API-Key: your-api-key"

# Bulk delete
curl -X POST http://localhost:8000/api/documents/delete \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_ids": ["id1", "id2", "id3"]}'

# Delete ALL documents (caution!)
curl -X DELETE http://localhost:8000/api/documents \
  -H "X-API-Key: your-api-key"
```

## Document Deletion and Cleanup

When a document is deleted, the Library performs thorough cleanup:

1. **Task Cancellation** — Active processing tasks are stopped immediately (cancellation flag + asyncio.Task.cancel with 10s timeout)
2. **Chunk Removal** — All text and image chunks are deleted
3. **Orphaned Entity Cleanup** — Entities only mentioned by this document are removed (entities shared with other documents are preserved)
4. **Relationship Cleanup** — Relationships to deleted entities are automatically removed (via DETACH DELETE)
5. **Community Cleanup** — Communities with no remaining member entities are deleted

The response includes cleanup statistics:

```json
{
  "message": "Document deleted successfully",
  "processing_cancelled": true,
  "orphaned_entities_removed": 15,
  "orphaned_communities_removed": 2
}
```

## Background Task Management

Long-running operations (batch processing, relationship analysis, community detection) run as background tasks:

```bash
# List all tasks
curl http://localhost:8000/api/tasks \
  -H "X-API-Key: your-api-key"

# Filter by status or type
curl "http://localhost:8000/api/tasks?status=running&task_type=batch_processing" \
  -H "X-API-Key: your-api-key"

# Check specific task progress
curl http://localhost:8000/api/tasks/{task_id} \
  -H "X-API-Key: your-api-key"

# Get task result (202 if still running, 200 with result on completion)
curl http://localhost:8000/api/tasks/{task_id}/result \
  -H "X-API-Key: your-api-key"

# Cancel a running task
curl -X DELETE http://localhost:8000/api/tasks/{task_id} \
  -H "X-API-Key: your-api-key"

# Clean up old completed tasks (default: older than 24 hours)
curl -X POST "http://localhost:8000/api/tasks/cleanup?max_age_hours=24" \
  -H "X-API-Key: your-api-key"
```

Tasks are stored in-memory and cleaned up automatically after 24 hours.
