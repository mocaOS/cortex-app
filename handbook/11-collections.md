# Chapter 11: Collections

Collections are organizational containers for your documents. They enable team separation, project isolation, multi-tenancy, and focused queries.

## Why Use Collections?

| Use Case | Example |
|----------|---------|
| **Team separation** | Marketing content vs. Engineering documentation |
| **Project isolation** | "Project Alpha" knowledge vs. "Project Beta" knowledge |
| **Client isolation** | Separate knowledge bases per client (multi-tenancy) |
| **Topic grouping** | "Legal Compliance", "Product Specs", "Meeting Notes" |
| **Agent personas** | Different collections for different AI agent personalities |
| **Temporal organization** | "Q1 Reports", "Q2 Reports" |

## How Collections Work

Each collection maintains its own scoped view of the knowledge graph:

```
Collection: "Research Papers"
  ├─ Document: paper1.pdf
  │    ├─ Chunks (embedded, searchable within this collection)
  │    └─ Entities (extracted from this document)
  ├─ Document: paper2.pdf
  │    ├─ Chunks
  │    └─ Entities
  └─ Relationships (between entities in this collection)
```

When you scope a query to a collection, only its documents, chunks, and entities are considered. This improves both accuracy and speed.

## Managing Collections

### Create

```bash
curl -X POST http://localhost:8000/api/collections \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "Research Papers", "description": "ML research papers"}'
```

### List

```bash
curl http://localhost:8000/api/collections \
  -H "X-API-Key: your-api-key"
```

Returns each collection with: id, name, description, created_at, document_count, entity_count.

### Get Details

```bash
curl http://localhost:8000/api/collections/{id} \
  -H "X-API-Key: your-api-key"
```

### Delete

```bash
curl -X DELETE http://localhost:8000/api/collections/{id} \
  -H "X-API-Key: your-api-key"
```

When a collection is deleted, its documents are moved to the default collection — they are not deleted.

## Document Operations with Collections

### Upload to a Collection

```bash
curl -X POST "http://localhost:8000/api/upload?collection_id=my-collection-id" \
  -H "X-API-Key: your-api-key" \
  -F "file=@paper.pdf"
```

### Move Documents Between Collections

```bash
curl -X POST http://localhost:8000/api/documents/move \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_ids": ["doc1", "doc2"], "collection_id": "target-collection"}'
```

### Get Collection Entities

```bash
curl "http://localhost:8000/api/collections/{id}/entities?limit=100" \
  -H "X-API-Key: your-api-key"
```

## Collection-Scoped Operations

### Search

```bash
curl -X POST http://localhost:8000/api/search \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "key findings", "collection_id": "research-papers"}'
```

### Ask AI

```bash
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the main findings?",
    "collection_id": "research-papers",
    "use_graph": true
  }'
```

Collection scoping works with all modes: Chat, Deep Research, streaming, extended thinking, and fast search.

### Relationship Analysis

```bash
curl -X POST "http://localhost:8000/api/graph/relationships/analyze?collection_id=my-collection" \
  -H "X-API-Key: your-api-key"
```

### Community Detection

```bash
curl -X POST "http://localhost:8000/api/graph/communities/detect?collection_id=my-collection" \
  -H "X-API-Key: your-api-key"
```

## The Default Collection

Documents uploaded without specifying a collection go into the default collection (configurable via `DEFAULT_COLLECTION`, default: `"default"`).

Queries without a `collection_id` parameter search across **all** collections.

## Configuration

```env
ENABLE_COLLECTIONS=true       # Enable collection features
DEFAULT_COLLECTION=default     # Default collection name
MAX_COLLECTIONS=0              # Max collections (0 = unlimited)
```

## Collection-Scoped API Keys

You can lock an API key to specific collections so it can only read or write within those collections. This enables true multi-tenancy on a single Cortex instance — each tenant, agent, or application gets its own key scoped to its own data.

### Create a Collection-Scoped Key

```bash
# Read-only key restricted to one collection
curl -X POST http://localhost:8000/api/admin/api-keys \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tenant A - Read Only",
    "permissions": ["read"],
    "collection_scope": "restricted",
    "allowed_collections": ["coll_abc123"]
  }'
```

```bash
# Read/write key restricted to two collections
curl -X POST http://localhost:8000/api/admin/api-keys \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tenant B - Read/Write",
    "permissions": ["read", "manage"],
    "collection_scope": "restricted",
    "allowed_collections": ["coll_def456", "coll_ghi789"]
  }'
```

### Update Collection Access

```bash
curl -X PATCH http://localhost:8000/api/admin/api-keys/{id} \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_scope": "restricted",
    "allowed_collections": ["coll_abc123", "coll_new"]
  }'
```

### How Enforcement Works

When a restricted key calls an endpoint, the system:
- **Filters lists** — `/api/collections` and `/api/documents` only return items from allowed collections
- **Blocks access** — 403 if a single-resource endpoint targets a disallowed collection
- **Blocks writes** — 403 if an upload, delete, or move targets a disallowed collection

New collections created after the key is issued are **not** automatically accessible — you must explicitly add them to the key's `allowed_collections`.

### UI

In **Settings > API Key Management**, click **New Key** and choose *Specific Collections* to open a multi-select picker. The key card shows an amber "N Collections" badge for restricted keys and lists the collection names in the expanded details panel.

## Best Practices

1. **Use meaningful names and descriptions** — "Q1 2026 Financial Reports" is better than "collection1"
2. **Group by logical boundary** — Organize by project, team, client, or topic
3. **Don't over-segment** — Too many small collections fragments the knowledge graph and reduces cross-document relationship discovery
4. **Scope queries when appropriate** — Use collection scoping for precision, omit it for breadth
5. **Review periodically** — Remove empty or outdated collections
6. **Use for agent memory** — Create a collection per agent for isolated long-term memory
7. **Pair with collection-scoped keys** — Give each tenant or agent a key restricted to its own collection for clean data isolation
