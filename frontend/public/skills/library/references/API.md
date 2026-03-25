# Cortex Library API Reference

Complete API reference for Cortex Library. All requests require `X-API-Key` header unless noted.

**Base URL:** Configured in `~/.openclaw/skills/library/state/credentials.json` as `base_url`.

---

## Health & Stats

### Health Check
```bash
GET /health
# No auth required
```
Response: `{"status": "healthy", "neo4j_connected": true, "version": "2.0.0"}`

### Statistics
```bash
GET /api/stats
```
Response includes: documents, chunks, entities, relationships, communities, collections counts.

---

## Documents

### Upload Document
```bash
POST /api/upload?collection_id={id}&start_processing={true|false}
Content-Type: multipart/form-data
-F "file=@/path/to/file"
```
**CRITICAL:** `collection_id` and `start_processing` MUST be URL query parameters, NOT form fields.

Supported formats: PDF, DOCX, TXT, MD, XLSX, PPTX, images. Max size: 50MB (configurable).

Response:
```json
{"document_id": "doc_xxx", "filename": "file.md", "status": "processing"}
```

### List Documents
```bash
GET /api/documents?skip=0&limit=20&collection_id={id}
```

### Get Document Details
```bash
GET /api/documents/{document_id}
```
Status values: `pending`, `processing`, `extracting`, `completed`, `failed`

### Get Document Content (with chunks)
```bash
GET /api/documents/{document_id}/content
```

### Download Original File
```bash
GET /api/documents/{document_id}/file
```

### Delete Document
```bash
DELETE /api/documents/{document_id}
```

### Reprocess Document
```bash
POST /api/documents/{document_id}/reprocess
```

### Delete All Documents
```bash
DELETE /api/documents
```

---

## Bulk Document Operations

### List Pending Documents
```bash
GET /api/documents/pending
```

### Process All Pending
```bash
POST /api/documents/process-pending
```
Response:
```json
{"task_id": "task_xxx", "status": "running", "pending_count": 5}
```

### Bulk Delete
```bash
POST /api/documents/delete
Content-Type: application/json
{"document_ids": ["doc_xxx", "doc_yyy"]}
```

### Bulk Reprocess
```bash
POST /api/documents/reprocess
Content-Type: application/json
{"document_ids": ["doc_xxx", "doc_yyy"]}
```

### Move Documents to Collection
```bash
POST /api/documents/move
Content-Type: application/json
{"document_ids": ["doc_xxx"], "collection_id": "col_xxx"}
```

---

## Custom Inputs

### Add Custom Input
```bash
POST /api/custom-input
Content-Type: application/json
```
Types: `qa` (Q&A pair), `text` (plain text), `markdown`

```json
{"type": "qa", "question": "What is X?", "answer": "X is...", "topic": "optional topic"}
{"type": "text", "content": "Some knowledge...", "topic": "optional topic"}
{"type": "markdown", "content": "# Markdown content", "topic": "optional topic"}
```

### List Custom Inputs
```bash
GET /api/custom-inputs
```

### Get Custom Input
```bash
GET /api/custom-inputs/{input_id}
```

### Delete Custom Input
```bash
DELETE /api/custom-inputs/{input_id}
```

### Generate Topic
```bash
POST /api/custom-input/generate-topic
Content-Type: application/json
{"content": "Some text to generate a topic for"}
```

---

## Search

### Hybrid Search
```bash
POST /api/search
Content-Type: application/json
{
  "query": "search terms",
  "top_k": 10,
  "collection_id": "optional_col_id"
}
```
Combines vector (weight 0.5), keyword (weight 0.3), and graph traversal (weight 0.2) with cross-encoder reranking.

### Graph Search
```bash
GET /api/graph/search?query=entity_name
```

### Community Search
```bash
POST /api/graph/communities/search
Content-Type: application/json
{"query": "topic"}
```

---

## Ask AI (RAG)

### Non-Streaming
```bash
POST /api/ask
Content-Type: application/json
{
  "question": "What do I know about X?",
  "mode": "speed",
  "top_k": 5,
  "use_graph": true,
  "collection_id": "optional_col_id",
  "conversation_history": []
}
```
Modes: `speed` (2 iterations, 1200 tokens), `quality` (10 iterations, 4000 tokens)

Response:
```json
{
  "question": "...",
  "answer": "...",
  "sources": [...],
  "graph_context": {...}
}
```

### Streaming (SSE)
```bash
POST /api/ask/stream
POST /api/ask/stream/thinking
```
Same request body. Returns Server-Sent Events:
- `content` - Answer tokens
- `sources` - Source documents
- `graph_context` - Related entities/relationships
- `thinking` - Reasoning steps (thinking endpoint only)
- `sub_questions` - Research sub-questions
- `retrieval` - Retrieved chunks
- `retrieval_stats` - Search statistics
- `done` - Stream complete
- `error` - Error occurred

---

## Knowledge Graph

### Graph Visualization
```bash
GET /api/graph/visualization?limit=100
```

### Graph Status
```bash
GET /api/graph/status
```
Returns staleness info: pending documents, timestamps for last extraction/analysis/detection.

### List Entities
```bash
GET /api/graph/entities?skip=0&limit=50&search=query&entity_type=Person
```

### Entity Details
```bash
GET /api/graph/entity/{name}
```

### Entity Relationships
```bash
GET /api/graph/entity/{name}/relationships
```

### Entity Types
```bash
GET /api/graph/entity-types
```

### Relationship Types
```bash
GET /api/graph/relationship-types
```

### Graph Subgraph
```bash
POST /api/graph/subgraph
Content-Type: application/json
{"entity_names": ["Entity A", "Entity B"], "include_bridge": true}
```

### Delete All Entities
```bash
DELETE /api/graph/entities
```

### Delete All Relationships
```bash
DELETE /api/graph/relationships
```

---

## Entity Deduplication

### Find Duplicates
```bash
GET /api/graph/entities/duplicates?threshold=0.8&limit=50
```

### Merge Entities
```bash
POST /api/entities/merge
Content-Type: application/json
{
  "canonical_name": "Primary Entity",
  "merge_names": ["Duplicate 1", "Duplicate 2"],
  "merged_description": "Combined description"
}
```

### Merge History
```bash
GET /api/entities/merge-history
```

---

## Relationship Analysis

### Analyze Relationships (Background Task)
```bash
POST /api/graph/relationships/analyze
Content-Type: application/json
{"rebuild": false}
```
`rebuild: true` deletes all existing relationships first. Default is incremental.

---

## Communities

### Detect Communities (Background Task)
```bash
POST /api/graph/communities/detect
```

### List Communities
```bash
GET /api/graph/communities?skip=0&limit=25&search=query
```

### Community Details
```bash
GET /api/graph/communities/{community_id}
```

### Community Documents
```bash
GET /api/graph/communities/{community_id}/documents
```

### Summarize Community
```bash
POST /api/graph/communities/{community_id}/summarize
```

### Delete Community
```bash
DELETE /api/graph/communities/{community_id}
```

### Delete All Communities
```bash
DELETE /api/graph/communities
```

---

## Collections

### Create Collection
```bash
POST /api/collections
Content-Type: application/json
{"name": "Collection Name", "description": "Optional description"}
```

### List Collections
```bash
GET /api/collections
```

### Get Collection Details
```bash
GET /api/collections/{collection_id}
```

### Update Collection
```bash
PUT /api/collections/{collection_id}
Content-Type: application/json
{"name": "New Name", "description": "New description"}
```

### Delete Collection
```bash
DELETE /api/collections/{collection_id}
```

### Collection Documents
```bash
GET /api/collections/{collection_id}/documents
```

### Collection Entities
```bash
GET /api/collections/{collection_id}/entities
```

---

## Tasks

### List Tasks
```bash
GET /api/tasks
```

### Task Status
```bash
GET /api/tasks/{task_id}
```
Response:
```json
{
  "task_id": "task_xxx",
  "status": "running",
  "progress_percent": 45,
  "message": "Processing document 5 of 10"
}
```

### Task Result
```bash
GET /api/tasks/{task_id}/result
```

### Cleanup Old Tasks
```bash
POST /api/tasks/cleanup
```

---

## Admin

### API Keys

```bash
POST /api/admin/api-keys              # Create key (permissions: read, write, delete, admin)
GET /api/admin/api-keys               # List keys
PUT /api/admin/api-keys/{key_id}      # Update key
POST /api/admin/api-keys/{key_id}/revoke    # Revoke key
POST /api/admin/api-keys/{key_id}/activate  # Reactivate key
DELETE /api/admin/api-keys/{key_id}   # Delete key
```

### System Config
```bash
GET /api/admin/config
```
Returns LLM models, context windows, concurrency settings (no API keys exposed).

### System Reset
```bash
POST /api/admin/reset
Content-Type: application/json
{
  "delete_documents": true,
  "delete_uploaded_files": true,
  "delete_custom_inputs": true,
  "delete_collections": true,
  "delete_api_keys": false
}
```

---

## Turbo Mode (GPU Acceleration)

```bash
POST /api/turbo/start                 # Start GPU job
GET /api/turbo/status                 # Check status
GET /api/turbo/jobs                   # List jobs
GET /api/turbo/jobs/{job_id}          # Job details
GET /api/turbo/jobs/{job_id}/logs     # Job logs
POST /api/turbo/stop                  # Stop job
POST /api/turbo/extend                # Extend runtime
GET /api/turbo/balance                # Check balance
```

Supported models: MiniMax-M2.1, Llama-3.1-70B, Llama-3.1-8B, Mistral-7B on H100/A100 GPUs.

---

## Cleanup

### Remove Orphaned Entities
```bash
POST /api/cleanup/orphaned-entities
```

---

## Rate Limits

- Standard operations: 100+ requests/second
- File uploads: No specific limit, but be respectful
- Large batch uploads: Use `start_processing=false` and batch process
