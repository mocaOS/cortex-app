# Admin Features

System administration: reset, library transfer, bulk download, and API key management.

## System Reset

`POST /api/admin/reset` — Admin-only endpoint with selective deletion options (documents, uploaded files, custom inputs, collections, API keys).

When documents are deleted, also cleans up:
- `MergeHistory` nodes (dedup audit trail)
- `SystemMeta` nodes (staleness timestamps)
- Frontend clears client-side cached data (`dedup_dismissed` and `cortex_community_detection_task` from localStorage, `regenerateStep`/`regenerateStartedAt`/`regenerateTaskId` from sessionStorage)

Accessible via Settings page → Danger Zone → System Reset modal with "DELETE" confirmation.

## Library Import/Export

Full instance migration via Settings page → Data Management section.

### Export
`POST /api/admin/export` runs as a background task (`library_export` task type) building a ZIP64 archive containing:
- 12 NDJSON data files (documents, chunks with embeddings, entities, relationships, communities, community members, collections, collection members, chunk mentions, merge history, system meta)
- Original document files in `files/` directory
- Manifest recording export version, embedding model/dimension, and item counts

Download via `GET /api/admin/export/{task_id}/download` streams the ZIP in 1MB chunks.

**Memory-safe streaming (`_write_ndjson`)**: each NDJSON entry is written one JSON line at a time via `zf.open(name, "w", force_zip64=True)` — nothing is buffered beyond a single line. The embedding-heavy payloads (chunks **and** entities both carry vectors) are pulled in 500-row batches from Neo4j and streamed straight into the zip, so peak RAM is ~one batch regardless of corpus size. Batched query methods: `export_chunk_count`/`export_all_chunks_batched` (ORDER BY `c.id`), `export_entity_count`/`export_all_entities_batched` (ORDER BY `e.name`, the unique key), `export_relationship_count`/`export_all_entity_relationships_batched` (ORDER BY `elementId(r)` for stable SKIP/LIMIT pagination). **Do not reintroduce the `lines = [...]; "\n".join(lines)` pattern** — it held the full payload twice and OOM-killed the container on large instances (the kernel OOM killer then took Neo4j and the redeploy down with it; no logs survive a SIGKILL). Documents stay a full list because the `files/` packaging step needs every doc's `file_path`/`id`, but they carry no embeddings.

### Import
`POST /api/admin/import` accepts multipart ZIP upload with `mode` query param:
- `clean` — requires empty instance (default)
- `replace` — auto-wipes via system reset first

Runs as background task (`library_import` task type). Validates manifest, checks embedding model/dimension compatibility (warns on mismatch), remaps file paths to target instance directories, restores all nodes and edges including dynamic APOC relationship types.

**Memory-safe streaming (mirror of export)**: the heavy NDJSON sections are streamed from the zip rather than `zf.read()`-ing the whole file. `_iter_ndjson` reads via an `io.TextIOWrapper` over the decompressed entry one line at a time; `_iter_ndjson_batches` groups those into batch lists feeding the existing `import_*_batch` inserts (chunks, entities, chunk mentions); relationships stream one-at-a-time through `_iter_ndjson`. Peak RAM is ~one batch. The pre-import plan-limit guards (`MAX_FILES`, `MAX_ENTITIES`) use `_count_ndjson`, which counts non-blank lines without parsing or buffering — previously these loaded the entire 20K-entity-with-embeddings file into RAM just to call `len()`. Progress totals come from `manifest.stats` so no extra pre-read pass is needed. **Documents stay materialized via the small-file `read_ndjson`** because step 6 mutates each doc (path remap) and step 7 reuses the list to copy files; documents carry no embeddings. Small sections (collections, communities, members, merge history, system meta, skills) also keep `read_ndjson` — they're tiny and some are reused.

### Concurrency Guard
Prevents simultaneous export/import operations (409 if one already running).

### Frontend
`LibraryTransferSection` component shows two cards:
- **Export**: stats summary + progress bar + download button
- **Import**: mode selector + drag-and-drop ZIP upload + DELETE confirmation for replace mode + progress bar + result summary with warnings

On import completion, clears client-side caches (same as system reset).

## Bulk Download

`POST /api/documents/download-zip` — Accepts `{ "document_ids": [...] }`, fetches file paths via `get_documents_file_paths()` batch query, builds a ZIP64-enabled archive with duplicate filename disambiguation, and streams the response in 1MB chunks via `StreamingResponse`. Frontend triggers browser download via blob URL. Requires `read` permission; restricted keys can only download documents from their allowed collections. Accessible via Download button in bulk actions toolbar on Documents page.

## API Key Management

- `services/api_key_service.py` — CRUD operations for API keys with permissions (READ, MANAGE) and collection scope
- `services/api_usage_service.py` — Request logging per key, endpoint categorization, error tracking, statistics aggregation
- `services/auth_service.py` — Admin API key validation, generated API key validation against Neo4j, permission + collection access checking

### Collection-Scoped API Keys

Keys can be restricted to specific collections via `collection_scope` + `allowed_collections`:

- **`collection_scope: "all"`** (default) — key can access all collections
- **`collection_scope: "restricted"`** — key can only access collections listed in `allowed_collections`

Storage: `APIKey` node has `collection_scope` property; `HAS_ACCESS_TO` relationships link the key node to permitted `Collection` nodes. `DETACH DELETE` on collection cleanup automatically removes stale access relationships.

`AuthResult` (auth_service.py) carries `collection_scope` and `allowed_collections` from the validated key. Helper methods:
- `can_access_collection(collection_id)` — returns True if allowed (admin/all-scope always True)
- `get_collection_filter()` — returns None (no filter) or list of allowed IDs for query-time filtering
- `validate_collection_access(auth, collection_id, action)` — raises 403 if access denied

Enforcement applied at every non-admin endpoint. Full scope:

**Read endpoints** — results filtered or 403 on out-of-scope access:
- `/api/stats`, `/api/graph/status` — counts scoped to allowed collections
- `/api/documents` (list), `/api/documents/{id}`, `/api/documents/{id}/content`, `/api/documents/{id}/file`, `/api/documents/download-zip`, `/api/documents/pending`
- `/api/custom-inputs` (list), `/api/custom-inputs/{id}`
- `/api/collections` (list filtered), `/api/collections/{id}`, `/api/collections/{id}/entities`
- `/api/graph/visualization`, `/api/graph/entities`, `/api/graph/entity/{name}`, `/api/graph/entity/{name}/relationships`, `/api/graph/search`, `/api/graph/subgraph`, `/api/graph/entity-types`, `/api/graph/relationship-types`, `/api/graph/relationships`
- `/api/entities/duplicates` (entities scoped), `/api/entities/merge-history` (requires all-scope)
- `/api/graph/status`, `/api/graph/communities` (list), `/api/graph/communities/{id}`, `/api/graph/communities/search`
- `/api/tasks`, `/api/tasks/{id}`, `/api/tasks/{id}/result`
- `/api/ask`, `/api/ask/stream`, `/api/ask/stream/thinking`, `/api/search`

**Manage endpoints** — 403 if target collection is not in the allowed list:
- `/api/upload`, `/api/custom-input`, `/api/custom-input/generate-topic`
- `/api/documents/{id}` DELETE, `/api/documents/delete`, `DELETE /api/documents`, `/api/documents/{id}/reprocess`, `/api/documents/reprocess` (per-document collection check), `/api/documents/process-pending`, `/api/documents/move`
- `/api/collections` (create), `/api/collections/{id}` (update/delete), `/api/collections/{id}/documents/{doc_id}`
- `/api/graph/entity/{name}` PATCH, `/api/entities/merge`
- `/api/graph/relationships/analyze` (collection_id validated if provided), `DELETE /api/graph/relationships`, `DELETE /api/graph/entities`
- `/api/graph/communities/detect` (collection_id validated if provided), `/api/graph/communities/summarize`, `DELETE /api/graph/communities/{id}`, `DELETE /api/graph/communities`
- `/api/cleanup/orphaned-entities`, `DELETE /api/tasks/{id}`, `/api/tasks/cleanup`

Validation on create/update: restricted scope requires ≥1 collection; all specified collection IDs must exist.

**Implementation notes / gotchas**

- `neo4j_service.py` has a single `get_stats(allowed_collection_ids=None)` method. A now-removed dead stub (no-arg version) previously existed earlier in the class and would have shadowed the scoped version in any Python version where the second definition wins. Keep only one definition.
- When checking collection access for an existing document, pass `doc.get("collection_id")` directly — do **not** fall back to a literal string like `"default"`. `can_access_collection(None)` correctly returns `True` for unrestricted queries (documents not assigned to any collection), so the fallback would wrongly block restricted keys from accessing uncollected documents.

### Frontend
- `ApiKeyManager` — manage API keys on Settings page; `CreateKeyModal` includes collection scope radio + multi-select picker
- `ApiKeyCard` — individual key display with collection scope badge (amber "N Collections" or muted "All Collections") and collection list in expanded details
- `ApiKeyAnalytics` — usage statistics
- `UsageChart` — visual usage data
