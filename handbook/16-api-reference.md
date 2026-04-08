# Chapter 16: API Reference

The Library exposes 70+ REST API endpoints. All endpoints except `/health` require an `X-API-Key` header. This chapter provides a complete endpoint reference.

For interactive API documentation, visit `/docs` (Swagger UI) or `/redoc` (ReDoc) on your running instance. The full OpenAPI 3.0.3 specification is also available in `documentation/apis/openapi.yaml`.

## Authentication

All requests must include the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/stats
```

Permission levels per endpoint are noted as: **Public** (no auth), **Read**, **Manage**, or **Admin**.

## Health and Status

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/health` | Public | Health check. Returns `{status, neo4j_connected, version}` |
| `GET` | `/api/stats` | Read | Knowledge base statistics: document/entity/relationship/community counts, processing status breakdown, entity type distribution, staleness timestamps |
| `GET` | `/api/admin/config` | Admin | System configuration (model names, API bases, context windows, feature flags — no secrets) |

## Documents

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/upload` | Manage | Upload a file. Query params: `collection_id`, `start_processing` (bool, default false), `source` (string, default "upload") |
| `GET` | `/api/documents` | Read | List all documents with metadata |
| `GET` | `/api/documents/{id}` | Read | Get document details |
| `GET` | `/api/documents/{id}/content` | Read | Document + all chunks (ordered) + concatenated full_content |
| `GET` | `/api/documents/{id}/file` | Read | Serve original uploaded file (inline for PDF, download for others) |
| `DELETE` | `/api/documents/{id}` | Manage | Delete document (cancels tasks, cleans graph). Returns orphan cleanup stats |
| `POST` | `/api/documents/delete` | Manage | Bulk delete. Body: `{document_ids: []}` |
| `DELETE` | `/api/documents` | Manage | Delete ALL documents |
| `POST` | `/api/documents/{id}/reprocess` | Manage | Reprocess single document. Optional file upload to replace original |
| `POST` | `/api/documents/reprocess` | Manage | Bulk reprocess. Body: `{document_ids: []}`. Query: `concurrency` |
| `GET` | `/api/documents/pending` | Read | List pending (unprocessed) documents |
| `POST` | `/api/documents/process-pending` | Manage | Start batch processing. Query: `concurrency`. Returns `task_id` |
| `POST` | `/api/documents/move` | Manage | Move documents. Body: `{document_ids: [], target_collection_id}` |

## Custom Inputs

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/custom-input` | Manage | Create custom input. Body: `{input_type, content, answer?, title?, collection_id?, start_processing?, source?}` |
| `POST` | `/api/custom-input/generate-topic` | Manage | Generate topic hint. Body: `{content, answer?, input_type}`. Returns `{topic_hint, existing_similar}` |
| `GET` | `/api/custom-inputs` | Read | List custom inputs. Query: `search`, `limit` |
| `GET` | `/api/custom-inputs/{id}` | Read | Get custom input details for editing |

## Search

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/search` | Read | Hybrid search. Body: `{query, top_k, collection_id?}` |

## Ask AI

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/ask` | Read | Non-streaming Q&A. Body: `RAGRequest` |
| `POST` | `/api/ask/stream` | Read | SSE streaming Q&A. Body: `RAGRequest`. Supports chat, agentic, and fast modes |
| `POST` | `/api/ask/stream/thinking` | Read | SSE streaming with visible reasoning. Body: `RAGRequest` |

**RAGRequest body:**

```json
{
  "question": "string (required)",
  "top_k": 5,
  "use_graph": true,
  "max_hops": 2,
  "use_reranking": true,
  "use_agentic": false,
  "use_fast_search": false,
  "collection_id": "optional",
  "conversation_history": [{"role": "user", "content": "..."}]
}
```

## Knowledge Graph

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/graph/status` | Read | GraphRAG system status (feature flags, counts) |
| `GET` | `/api/graph/visualization` | Read | Graph data for visualization. Query: `limit` (0-10000, default 100), `include_neighbors` |
| `GET` | `/api/graph/entities` | Read | Paginated entity listing. Query: `skip`, `limit`, `search`, `entity_type` |
| `GET` | `/api/graph/entity-types` | Read | Distinct entity types |
| `GET` | `/api/graph/entity/{name}` | Read | Entity details + relationships (entity-only paths) |
| `PATCH` | `/api/graph/entity/{name}` | Manage | Update entity name/description. Body: `{name?, description?}`. Old name preserved in aliases |
| `GET` | `/api/graph/entity/{name}/relationships` | Read | Entity relationships. Query: `max_depth` (1-3), `limit` (1-200) |
| `GET` | `/api/graph/search` | Read | Search entities by name (wildcard prefix). Query: `query` |
| `POST` | `/api/graph/subgraph` | Read | Subgraph for entities. Body: `{entity_names: []}`. Query: `include_connections` |
| `DELETE` | `/api/graph/entities` | Manage | Delete ALL entities (DETACH DELETE) |

## Entity Deduplication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/entities/duplicates` | Read | Scan for duplicates. Query: `threshold` (0.5-1.0, default 0.75), `limit` |
| `POST` | `/api/entities/merge` | Manage | Merge entities. Body: `{canonical, merge: []}` |
| `GET` | `/api/entities/merge-history` | Read | Merge audit trail. Query: `limit` |

## Relationships

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/graph/relationships` | Read | Paginated listing. Query: `skip`, `limit`, `search`, `rel_type` |
| `GET` | `/api/graph/relationship-types` | Read | Distinct relationship types |
| `POST` | `/api/graph/relationships/analyze` | Manage | Run relationship analysis. Query: `collection_id`, `scope`, `rebuild`. Returns `task_id` |
| `DELETE` | `/api/graph/relationships` | Manage | Delete ALL relationships |

## Communities

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/graph/communities` | Read | Paginated listing. Query: `skip`, `limit`, `search` |
| `POST` | `/api/graph/communities/detect` | Manage | Run detection. Query: `min_size`, `collection_id`. Returns `task_id` |
| `GET` | `/api/graph/communities/{id}` | Read | Community details + members + key relationships |
| `DELETE` | `/api/graph/communities/{id}` | Manage | Delete specific community (unlinks entities) |
| `DELETE` | `/api/graph/communities` | Manage | Delete ALL communities |
| `POST` | `/api/graph/communities/summarize` | Manage | Generate summaries. Body: `{community_ids?, force_regenerate}` |
| `GET` | `/api/graph/communities/search` | Read | Search communities. Query: `query`, `limit` |

## Collections

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/collections` | Read | List all collections with stats |
| `POST` | `/api/collections` | Manage | Create collection. Body: `{name, description?}` |
| `GET` | `/api/collections/{id}` | Read | Collection details + document/entity counts |
| `DELETE` | `/api/collections/{id}` | Manage | Delete collection (moves docs to default) |
| `POST` | `/api/collections/{id}/documents/{doc_id}` | Manage | Add document to collection |
| `GET` | `/api/collections/{id}/entities` | Read | Collection's entities. Query: `limit` |

## Background Tasks

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/tasks` | Read | List tasks. Query: `status`, `task_type` |
| `GET` | `/api/tasks/{id}` | Read | Task progress (status, percent, message, timestamps) |
| `GET` | `/api/tasks/{id}/result` | Read | Task result (202 if running, 200 on completion) |
| `DELETE` | `/api/tasks/{id}` | Manage | Cancel/remove task |
| `POST` | `/api/tasks/cleanup` | Manage | Remove completed tasks. Query: `max_age_hours` (1-168) |

## Turbo Mode (Compute3)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/turbo/status` | Read | Availability, active job, ready state |
| `GET` | `/api/turbo/balance` | Admin | Compute3 account balance |
| `POST` | `/api/turbo/start` | Admin | Start GPU job. Query: `runtime`, `gpu_type`, `gpu_count` |
| `POST` | `/api/turbo/stop` | Admin | Stop GPU job. Query: `job_id` |
| `POST` | `/api/turbo/extend` | Admin | Extend runtime. Query: `additional_seconds`, `job_id` |
| `GET` | `/api/turbo/jobs` | Admin | List GPU jobs. Query: `state` |
| `GET` | `/api/turbo/jobs/{id}` | Admin | Job details |
| `GET` | `/api/turbo/jobs/{id}/logs` | Admin | Job logs |

## Admin — API Keys

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/admin/api-keys` | Admin | List all API keys (includes `collection_scope` and `allowed_collections`) |
| `POST` | `/api/admin/api-keys` | Admin | Create key. Body: `{name, permissions, collection_scope?, allowed_collections?}` |
| `GET` | `/api/admin/api-keys/with-stats` | Admin | List keys with embedded usage statistics and collection scope |
| `GET` | `/api/admin/api-keys/{id}` | Admin | Key details including `allowed_collection_names` |
| `PATCH` | `/api/admin/api-keys/{id}` | Admin | Update key. Body: `{name?, permissions?, is_active?, collection_scope?, allowed_collections?}` |
| `DELETE` | `/api/admin/api-keys/{id}` | Admin | Delete key permanently (DETACH DELETE removes all relationships) |
| `POST` | `/api/admin/api-keys/{id}/revoke` | Admin | Revoke (deactivate) key |
| `POST` | `/api/admin/api-keys/{id}/activate` | Admin | Reactivate revoked key |
| `GET` | `/api/admin/api-keys/{id}/stats` | Admin | Key usage statistics |
| `GET` | `/api/admin/api-keys/{id}/usage-history` | Admin | Daily usage history. Query: `days` (1-365) |
| `GET` | `/api/admin/stats/overview` | Admin | Aggregated stats across all keys |

**`CreateAPIKeyRequest` body:**

```json
{
  "name": "Tenant A - Read Only",
  "permissions": ["read"],
  "collection_scope": "restricted",
  "allowed_collections": ["coll_abc123"]
}
```

**`UpdateAPIKeyRequest` body (all fields optional):**

```json
{
  "name": "New Name",
  "permissions": ["read", "manage"],
  "is_active": true,
  "collection_scope": "restricted",
  "allowed_collections": ["coll_abc123", "coll_def456"]
}
```

`collection_scope` values: `"all"` (default — unrestricted) or `"restricted"` (must supply `allowed_collections`). Validation: `restricted` scope requires at least one collection ID, and all IDs must exist. When a collection is deleted, its `HAS_ACCESS_TO` relationships are automatically removed via DETACH DELETE.

## Admin — Agent Skills

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/admin/skills` | Admin | List all installed skills with metadata and enabled state |
| `GET` | `/api/admin/skills/{skill_id}` | Admin | Skill details including SKILL.md body and tools.json config |
| `POST` | `/api/admin/skills/install` | Admin | Install skill. Body: `{url?}` (direct SKILL.md URL) or `{registry_id?}` (`owner/repo/skill-name`) |
| `PATCH` | `/api/admin/skills/{skill_id}` | Admin | Update skill. Body: `{enabled: true|false}` |
| `DELETE` | `/api/admin/skills/{skill_id}` | Admin | Uninstall skill and delete files |
| `GET` | `/api/admin/skills/registry/search` | Admin | Search skills.sh registry. Query: `q` (search term) |
| `POST` | `/api/admin/skills/discover` | Admin | Re-scan local skills directory for new skills |

## Admin — System

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/admin/reset` | Admin | System reset. Body: `{delete_documents, delete_uploaded_files, delete_custom_inputs, delete_collections, delete_api_keys}` |

## Cleanup

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/cleanup/orphaned-entities` | Manage | Remove orphaned entities (no mentions) and communities (no members) |
