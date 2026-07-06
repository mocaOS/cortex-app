# Admin Features

System administration: reset, library transfer, bulk download, and API key management.

> Git connections (connect/sync/orphaned-review on the Settings page, `/api/integrations/git/*`) are documented separately in [`git-integration.md`](git-integration.md).

## Runtime Settings (admin-editable)

Almost all settings are env-only and read-only in the admin **System Config**
panel (`GET /api/admin/config` → `SystemConfigResponse`). The one exception is
the **runtime settings** layer: admin-editable overrides that persist and take
effect without a restart.

- **Storage**: reuses the generic `SystemMeta {key,value}` node via
  `neo4j.set_runtime_setting(key, bool)` / `get_runtime_setting(key, default)`
  (namespaced `setting:<key>`). Effective value = the env default overlaid with
  the override.
- **Endpoint**: `PATCH /api/admin/config` (`require_admin`, body
  `RuntimeSettingsUpdate` — only provided fields are written) → persists the
  override and returns the full updated `SystemConfigResponse`.
- **Frontend**: rendered as an interactive `ConfigToggle` (vs the read-only
  `ConfigItem`) in the Features & Security section (`app/admin/page.tsx`,
  `api.updateRuntimeSettings`); optimistic update, reverts on error.
- **First (currently only) setting**: `ingestion_injection_scan` — toggles the
  ingestion-time prompt-injection scan's LLM classifier (see
  [`document-pipeline.md`](document-pipeline.md)). Applies to subsequent
  ingestions. Note: `SystemMeta` is included in library export/import and cleared
  by System Reset, so the override travels with an export and resets to the env
  default on reset.

## System Reset

`POST /api/admin/reset` — Admin-only endpoint with selective deletion options (documents, uploaded files, custom inputs, collections, API keys).

When documents are deleted, also cleans up:
- `MergeHistory` nodes (dedup audit trail)
- `SystemMeta` nodes (staleness timestamps)
- Frontend clears client-side cached data (`dedup_dismissed` and `cortex_community_detection_task` from localStorage, `regenerateStep`/`regenerateStartedAt`/`regenerateTaskId` from sessionStorage)

Accessible via Settings page → Danger Zone → System Reset modal with "DELETE" confirmation.

**Memory-safe deletion**: `delete_all_documents` deletes communities/entities/chunks/documents via `CALL {} IN TRANSACTIONS` (10K rows; 2K for chunks, which carry embedding vectors) — a single whole-graph `DETACH DELETE` blows past `dbms.memory.transaction.total.max` (~70% of heap) on large knowledge bases. These queries must run as auto-commit (`session.run`), not inside an explicit transaction. Since 2026-07-04 the graph-management deletes (`delete_all_entities`, `delete_all_relationships`, `delete_batch_relationships`) use the same batched pattern — the old single-transaction forms 500'd with `MemoryPoolOutOfMemoryError` on a 29k-entity/53k-relationship graph.

**Timeout chain**: reset is a synchronous HTTP call routed through the Next.js rewrite proxy (`experimental.proxyTimeout`, 300s — the Next default of 30s returned a non-JSON 500 to the UI while the backend finished cleanup) and nginx (`proxy_read_timeout 300s`). A reset that outlives 300s would need conversion to a background task (like `library_import`).

## Library Import/Export

Full instance migration via Settings page → Data Management section.

### Export
`POST /api/admin/export` runs as a background task (`library_export` task type) building a ZIP64 archive containing:
- 12 NDJSON data files (documents, chunks with embeddings, entities, relationships, communities, community members, collections, collection members, chunk mentions, merge history, system meta)
- Original document files in `files/` directory
- Manifest recording export version, embedding model/dimension, and item counts

Download via `GET /api/admin/export/{task_id}/download` streams the ZIP in 1MB chunks.

**Exports never contain secrets**: skill `config.json` files are sanitized in the bundle — secret-typed fields (per the skill's `config_schema`) are stripped, non-secret config retained — so archives are safe to share and portable across instances with different `ENCRYPTION_KEY`s. Git connections (and their PATs) are never exported. After import, admins re-enter skill secrets via the config wizard (the `config_schema` rides on the skill node, so masking/status work immediately).

**Memory-safe streaming (`_write_ndjson`)**: each NDJSON entry is written one JSON line at a time via `zf.open(name, "w", force_zip64=True)` — nothing is buffered beyond a single line. The embedding-heavy payloads (chunks **and** entities both carry vectors) are pulled in 500-row batches from Neo4j and streamed straight into the zip, so peak RAM is ~one batch regardless of corpus size. Batched query methods: `export_chunk_count`/`export_all_chunks_batched` (ORDER BY `c.id`), `export_entity_count`/`export_all_entities_batched` (ORDER BY `e.name`, the unique key), `export_relationship_count`/`export_all_entity_relationships_batched` (ORDER BY `elementId(r)` for stable SKIP/LIMIT pagination). **Do not reintroduce the `lines = [...]; "\n".join(lines)` pattern** — it held the full payload twice and OOM-killed the container on large instances (the kernel OOM killer then took Neo4j and the redeploy down with it; no logs survive a SIGKILL). Documents stay a full list because the `files/` packaging step needs every doc's `file_path`/`id`, but they carry no embeddings.

### Import
`POST /api/admin/import` accepts multipart ZIP upload with `mode` query param:
- `clean` — requires empty instance (default)
- `replace` — auto-wipes via system reset first

Runs as background task (`library_import` task type). Validates manifest, checks embedding model/dimension compatibility (warns on mismatch), remaps file paths to target instance directories, restores all nodes and edges including dynamic APOC relationship types.

**Chunked upload** (`/api/admin/import/upload/start|{id}/chunk|{id}/finish`, DELETE `{id}` to abort): the web UI uploads the ZIP in 8MB sequential PUTs instead of one multipart request — reverse proxies with body-read timeouts (Traefik v3 defaults `respondingTimeouts.readTimeout=60s` on Coolify) kill single-request uploads of large archives mid-body. Sessions live in `_import_upload_sessions` (main.py) with a 2h TTL purge; chunk offsets must be contiguous, and a retried chunk gets 409 + the server's byte count so the client resyncs (`api.ts startLibraryImport`). `finish` validates size then starts the same `library_import` task. The single-request `POST /api/admin/import` remains for curl/API use. Tests: `backend/tests/test_import_chunked_upload.py`.

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

## Monthly Usage Metering (unit-denominated quota)

`MAX_QUERIES_PER_MONTH` is denominated in **internal LLM completions** ("units"), not HTTP requests — quota consumption tracks inference cost. Pricing tiers are unit-based.

- **Counting** — `services/usage_meter.py`: thread-safe in-memory accumulator + a daemon flusher that batches increments to per-day `LLMUsageDay` nodes (`completions`, `completions_query`, `completions_processing`); the month's meter is the sum over the month's days. Increments fire from the OpenAI client-factory wrap (`llm_config._count_chat_completions` — every factory-built client's `chat.completions.create`, so wrapped/direct/streamed call sites all count, embeddings never do; only *successful* creates count) plus one manual `record_completion` in the raw-httpx vision path (`vision_analyzer.py`). Attribution rides a contextvar: `enforce_query_quota` stamps "query", processing task entries stamp "processing". Restart loses at most a few seconds of buffered counts (accepted undercount). NB: the Cypher parameters are `$query_n`/`$processing_n` — a kwarg literally named `query` collides with the Neo4j driver's `Session.run(query, ...)`.
- **Enforcement** — `enforce_query_quota` (search + ask endpoints; also stamps the contextvar and prewarms the reranker) and `enforce_processing_quota` (upload, custom-input, reprocess single/bulk, process-pending, web-import, git sync endpoint + scheduler skip, relationships/analyze, communities detect/summarize). Both 429 + `Retry-After` to next UTC month. **In-flight work always finishes**: `process_pending_documents` checks the meter between documents (`_monthly_quota_exhausted`) and leaves the remainder `pending` with a quota message (`quota_skipped` in the batch result).
- **Surfacing** — `GET /api/stats` carries `monthly_usage_used/limit/query/processing`; the **admin page's Statistics card** (`frontend/src/app/admin/page.tsx`, "Monthly Usage" block) renders a bar (accent < 80%, amber ≥ 80%, red at 100%) with a queries/processing breakdown, plus "no limit" mode when the quota is 0. Deliberately admin-only — the global StatsBar does NOT show quota mechanics. Tests: `test_usage_meter.py`, `test_max_queries_per_month.py`.

## Instance Status (redeploy safety)

`GET /api/instance/status` (`main.py`, `require_manage_permission`) — single-call operational snapshot for deploy automation to decide whether a customer instance can be safely restarted/upgraded. Returns `InstanceStatusResponse` with a `safe_to_redeploy` boolean plus a `reasons` list of active blockers.

`safe_to_redeploy` is **False** while destructible work is in flight:
- `processing_count > 0` — documents mid-processing/extraction (a restart strands them in `processing` state).
- running/pending tasks in the in-memory `_task_store` (`running_tasks`/`running_task_count`) — a restart loses them.
- `active_query_count > 0` — in-flight AskAI/research queries (a restart kills the stream).
- Neo4j unreachable — state can't be verified, so treated as unsafe.

`pending_count` is reported but **never** blocks: pending docs persist in Neo4j and resume after restart.

**Fleet orchestration fields** (2026-07-04): the response also carries library size (`document_count`, `entity_count`, `collection_count`) and the monthly unit meter (`monthly_usage_used/limit/query/processing`) so the meta-cortex control plane can read plan consumption and library growth from the same admin-key call it already makes for redeploy safety. Populated only when Neo4j is reachable (zeros otherwise).

AskAI activity is tracked via the `track_ask_activity` FastAPI `yield` dependency on `/api/ask`, `/api/ask/stream`, `/api/ask/stream/thinking` — it increments an in-memory `_active_query_count` (reset to 0 on restart, which is correct) and writes `last_query_at` to the `SystemMeta` node (`set_meta`). The dependency teardown runs after a streamed response is fully consumed (or the client disconnects), so the counter decrements reliably. `last_query_at` and the `last_relationship_analysis_at`/`last_community_detection_at`/`last_entity_merge_at` timestamps are surfaced for informational "when was it last active" checks.

## API Key Management

- `services/api_key_service.py` — CRUD operations for API keys with permissions (READ, MANAGE) and collection scope
- `services/api_usage_service.py` — Request logging per key, endpoint categorization, error tracking, statistics aggregation. `categorize_endpoint()` matches the **longest** (most-specific) prefix in `ENDPOINT_CATEGORIES` — required so `/api/custom-inputs/{id}` resolves to `documents` rather than being swallowed by the shorter `/api/custom-input` (`upload`) prefix.
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
