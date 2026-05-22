# Chapter 14: Image Analysis

The Library automatically extracts and analyzes images embedded in your documents, making visual content searchable and queryable alongside text.

## How It Works

When you upload a document containing images:

```
Upload → Docling Conversion → Text Processing (immediate)
                │
                └──▶ Image Extraction
                        │
                        ▼
              Background Image Analysis (async)
                ├─ Vision model analysis (per image)
                ├─ Text description generation
                ├─ Embedding creation
                ├─ Neo4j storage as image chunks
                └─ Entity extraction on image content
```

**Key principle**: Image analysis runs **asynchronously** in the background. Your document becomes searchable for text content immediately. Image-derived knowledge is added progressively as each image is analyzed.

## Configuration

### Vision Model Setup

```env
# OpenAI GPT-4o (recommended)
VISION_MODEL=gpt-4o
VISION_MODEL_API_BASE=https://api.openai.com/v1
VISION_MODEL_API_KEY=sk-your-key

# Anthropic Claude 3.5 Sonnet (via LiteLLM)
VISION_MODEL=anthropic/claude-3-5-sonnet-20241022
VISION_MODEL_API_BASE=http://your-litellm-proxy

# Local model via Ollama
VISION_MODEL=llava
VISION_MODEL_API_BASE=http://localhost:11434/v1

# No vision model (Docling fallback only)
VISION_MODEL=
```

If `VISION_MODEL` is empty, the system uses Docling's built-in image descriptions (basic classification, free, no API calls).

### Concurrency Control

```env
VISION_MAX_CONCURRENT=3   # Max concurrent vision API calls system-wide
```

This controls a global asyncio Semaphore shared across all documents. Increase for faster throughput; decrease to respect API rate limits.

### Small-Image Pre-Filter

```env
VISION_MIN_IMAGE_SIDE=64   # Skip vision-API call below this many pixels (set 0 to disable)
```

PDFs commonly expose ~10–40 px bullets, icons, dingbats, and separator glyphs as Docling `PictureItem`s. Hosted vision APIs reject these as too small — Venice returns `HTTP 400 "Supplied image did not pass validation checks"` for anything below ~64 px on either side, and our retry policy then burns three doomed requests per tiny image. Cortex skips the API call when `min(width, height) < VISION_MIN_IMAGE_SIDE` and falls back to Docling's description (or "no description available"), saving API spend without losing meaningful content — these sub-threshold images carry no semantic value.

### Image Downscaling & Compression

```env
VISION_MAX_IMAGE_SIDE=1568   # Cap on the longer side before base64 encode (set 0 to disable)
VISION_JPEG_QUALITY=85       # JPEG quality for opaque images (PNG kept for alpha)
```

Cortex renders PDF pages at 2× DPI for OCR-grade text legibility (`images_scale=2.0` in Docling). A typical page becomes a 2400×1700 PIL image — encoded as PNG, that's several MB. Some hosted vision deployments (especially LiteLLM-wrapped or custom vLLM endpoints) tokenize the base64 data-URL payload as *text*, so a multi-MB image counts as hundreds of thousands of input tokens and blows past the model's context window. One customer instance hit 184K input tokens against a 192K cap from a single image.

Cortex resizes images so the longer side fits `VISION_MAX_IMAGE_SIDE` (default 1568, matching Claude's recommended max side) using Lanczos resampling — preserves aspect ratio and stays sharp enough for text OCR. Opaque images go out as JPEG at `VISION_JPEG_QUALITY=85` (5–10× smaller than PNG, visually near-lossless for documents); images with alpha channels stay PNG. The resize is non-destructive: Cortex stores the original unmodified.

## Image Extraction

Images are extracted during Docling conversion from:
- **PDF files** — Embedded images, charts, diagrams
- **Word documents** (.docx) — Embedded images
- **PowerPoint** (.pptx) — Slide images and embedded media
- **Image files** (.png, .jpg, etc.) — The entire file is the image

Each extracted image includes metadata:
- Page number (from document provenance)
- Bounding box coordinates (position on page)
- Caption (if available from the document)
- Any existing description from Docling's built-in analysis

## Analysis Methods

The system uses a fallback chain for each image:

| Priority | Method | When Used | Quality | Cost |
|----------|--------|-----------|---------|------|
| 1 | **Vision Model** | `VISION_MODEL` is configured | Excellent — detailed descriptions, OCR, chart interpretation | API cost per image |
| 2 | **Docling Description** | Vision model fails or not configured | Basic — simple classification | Free |
| 3 | **Fallback** | Both above fail | Minimal — page number and caption only | Free |

### Vision Model Analysis

The vision model receives each image with a structured prompt:

```
Analyze this image in detail for document retrieval purposes.
Output ONLY the description, without conversational filler.

Provide structured description:
- Summary: 1-2 sentence overview
- Visual Elements: main objects, layout
- Text content (OCR): transcribe visible text
- Data representation: for charts/graphs
- Context: overall purpose and details
```

**Retry logic**: Up to 3 attempts with exponential backoff (1s, 2s, 4s) on failure.

### Vision Model Comparison

| Provider | Model | Quality | Cost per Image | Speed |
|----------|-------|---------|---------------|-------|
| OpenAI | `gpt-4o` | Excellent (detailed OCR, chart analysis) | ~$0.01-0.03 | 2-4s |
| Anthropic | `claude-3-5-sonnet` | Excellent | ~$0.003-0.015 | 2-5s |
| Local | `llava` (Ollama) | Good | Free | 3-8s |
| Docling | Built-in | Basic classification | Free | <1s |

## Image Chunks

Analyzed images become searchable chunks in Neo4j:

- **Chunk ID**: `{document_id}_image_{index}`
- **Chunk Index**: 1000+ (high index separates image chunks from text chunks)
- **Metadata type**: `image_analysis`
- **Content format**: `[Image Analysis (Vision Model)]\n{description}`

These chunks are embedded and stored alongside text chunks, making them searchable via the same hybrid search system.

## Entity Extraction from Images

If `ENABLE_GRAPH_EXTRACTION=true`, the Library runs entity extraction on image descriptions. This means:

- Charts showing company logos extract Organization entities
- Photos of people extract Person entities
- Diagrams mentioning technologies extract Technology entities
- All image-derived entities are linked to the image chunk and integrated into the knowledge graph

### Embedding-based dedup across text & images

When `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true` (default), image-derived entities are batch-embedded and stored through the same embedding-first dedup path as text entities. This means an "MOCA" entity extracted from an image caption can merge with an existing "Museum of Crypto Art" entity already in the graph from text content — even though string similarity (Levenshtein 85%) would not catch that match. Both surfaces populate the single `entity_embedding` vector index, so cross-source deduplication "just works" at write time. With the flag off, both surfaces fall back to Levenshtein 85% only.

## Progress Tracking

### Per-Document Progress

Each document tracks image analysis progress via three properties:
- `image_progress_current` — Number of images analyzed so far
- `image_progress_total` — Total images to analyze
- `image_progress_message` — Current status message

### In the Knowledge Graph Pipeline

Step 1 (Entity Extraction) is image-analysis-aware:

- Documents with `processing_status === "completed"` but `image_progress_current < image_progress_total` are treated as still in progress
- These appear in a dedicated "Analyzing Images" tile with an aggregate progress bar
- Step 1 status remains "in_progress" until all image analysis completes
- Auto-refresh polls every 5 seconds when image analysis is detected
- Steps 2 and 3 remain blocked until all images are analyzed

This ensures the knowledge graph includes image-derived knowledge before relationship analysis begins.

## Querying Image Content

Image-derived knowledge is automatically included in all search and Q&A operations:

```bash
# Search for image content
curl -X POST http://localhost:8000/api/search \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "organization chart showing team structure"}'

# Ask about images
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"question": "What does the revenue chart show?"}'
```

Answers citing image content include the image analysis description in source citations.

## Performance Considerations

- **Processing time**: 2-5 seconds per image with cloud vision models
- **Concurrency**: Controlled via `VISION_MAX_CONCURRENT`. All images within a document are launched concurrently, gated by the semaphore.
- **Background processing**: Does not block text processing or document availability
- **Thread safety**: Progress updates use asyncio.Lock for atomic increments
- **Thread pools**: Each thread gets its own httpx.AsyncClient and event loop to avoid "event loop closed" errors

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No images extracted | Check that the document contains embedded images (not linked/referenced images) |
| Vision API errors | Verify `VISION_MODEL`, `VISION_MODEL_API_BASE`, and `VISION_MODEL_API_KEY` |
| Poor image descriptions | Use a more capable vision model (GPT-4o or Claude 3.5 Sonnet) |
| Slow image processing | Increase `VISION_MAX_CONCURRENT` (respecting API rate limits) |
| Image analysis not starting | Ensure `VISION_MODEL` is set (empty = Docling fallback only) |
