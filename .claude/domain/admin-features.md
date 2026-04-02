# Admin Features

System administration: reset, library transfer, bulk download, and API key management.

## System Reset

`POST /api/admin/reset` — Admin-only endpoint with selective deletion options (documents, uploaded files, custom inputs, collections, API keys).

When documents are deleted, also cleans up:
- `MergeHistory` nodes (dedup audit trail)
- `SystemMeta` nodes (staleness timestamps)
- Frontend clears client-side cached data (`dedup_dismissed` and `moca_community_detection_task` from localStorage, `regenerateStep`/`regenerateStartedAt`/`regenerateTaskId` from sessionStorage)

Accessible via Settings page → Danger Zone → System Reset modal with "DELETE" confirmation.

## Library Import/Export

Full instance migration via Settings page → Data Management section.

### Export
`POST /api/admin/export` runs as a background task (`library_export` task type) building a ZIP64 archive containing:
- 12 NDJSON data files (documents, chunks with embeddings, entities, relationships, communities, community members, collections, collection members, chunk mentions, merge history, system meta)
- Original document files in `files/` directory
- Manifest recording export version, embedding model/dimension, and item counts

Download via `GET /api/admin/export/{task_id}/download` streams the ZIP in 1MB chunks.

### Import
`POST /api/admin/import` accepts multipart ZIP upload with `mode` query param:
- `clean` — requires empty instance (default)
- `replace` — auto-wipes via system reset first

Runs as background task (`library_import` task type). Validates manifest, checks embedding model/dimension compatibility (warns on mismatch), remaps file paths to target instance directories, restores all nodes and edges including dynamic APOC relationship types.

### Concurrency Guard
Prevents simultaneous export/import operations (409 if one already running).

### Frontend
`LibraryTransferSection` component shows two cards:
- **Export**: stats summary + progress bar + download button
- **Import**: mode selector + drag-and-drop ZIP upload + DELETE confirmation for replace mode + progress bar + result summary with warnings

On import completion, clears client-side caches (same as system reset).

## Bulk Download

`POST /api/documents/download-zip` — Accepts `{ "document_ids": [...] }`, fetches file paths via `get_documents_file_paths()` batch query, builds a ZIP64-enabled archive with duplicate filename disambiguation, and streams the response in 1MB chunks via `StreamingResponse`. Frontend triggers browser download via blob URL. No auth required (matches existing file endpoint). Accessible via Download button in bulk actions toolbar on Documents page.

## API Key Management

- `services/api_key_service.py` — CRUD operations for API keys with permissions (READ, MANAGE, ADMIN)
- `services/api_usage_service.py` — Request logging per key, endpoint categorization, error tracking, statistics aggregation
- `services/auth_service.py` — Admin API key validation, generated API key validation against Neo4j, permission checking

### Frontend
- `ApiKeyManager` — manage API keys on Settings page
- `ApiKeyCard` — individual key display
- `ApiKeyAnalytics` — usage statistics
- `UsageChart` — visual usage data
