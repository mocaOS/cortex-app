# Chapter 17: Administration and Maintenance

This chapter covers day-to-day administration tasks for self-hosted Cortex Library deployments.

## Monitoring

### Health Checks

```bash
# Backend health (includes Neo4j connectivity)
curl http://localhost:8000/health
# Expected: {"status": "healthy", "neo4j_connected": true,
#            "schema_initialized": true, "version": "1.0.0"}
```

A degraded instance (Neo4j unreachable or schema not yet confirmed) answers
**HTTP 503** with `"status": "degraded"`, so `curl -f`, compose healthchecks,
and health-aware proxies gate on the status code.

In Docker Compose, the backend health check runs automatically:
- **Backend**: `curl -f http://localhost:8000/health` every 30 seconds, 10s timeout, 3 retries, 60s start period (covers schema init at startup)
- **Neo4j**: HTTP check on port 7474 every 10 seconds, 5 retries

### System Statistics

```bash
curl http://localhost:8000/api/stats -H "X-API-Key: your-admin-key"
```

Returns comprehensive metrics:
- Document, chunk, entity, relationship, community, collection counts
- Processing status breakdown: pending, completed, failed, processing
- Average chunks per document
- Entity type distribution (counts per type)
- Monthly usage meter: `monthly_usage_used` / `monthly_usage_limit` (units = internal LLM completions), with a `monthly_usage_query` vs `monthly_usage_processing` breakdown. The Settings page's Statistics panel shows a "Monthly Usage" bar that turns amber at 80% and red when exhausted (or plain counters when no limit is configured). Both questions and document imports consume units — an instance can reach its limit through imports alone.
- Average entity mentions
- Staleness timestamps (last relationship analysis, community detection, entity merge)

### Redeploy Safety Status

Before restarting or upgrading an instance (e.g. a version rollout), check whether it is safe to redeploy without losing in-flight work:

```bash
curl http://localhost:8000/api/instance/status \
  -H "X-API-Key: your-admin-key"
```

Returns a single snapshot designed for deploy automation:
- `safe_to_redeploy` — `false` while any destructible work is in flight
- `reasons` — list of active blockers (empty when safe)
- `processing_count` — documents currently being processed/extracted (blocks redeploy)
- `pending_count` — documents queued; **informational only** — these persist in Neo4j and resume after a restart, so they never block
- `failed_count` — documents in failed state
- `running_task_count` / `running_tasks` — background jobs (batch processing, relationship analysis, community detection); a restart interrupts the work (the task record survives and is marked failed, but the job must be re-run), so this blocks redeploy
- `active_query_count` — in-flight AskAI/research queries; a restart kills the stream (blocks redeploy)
- `last_query_at` — timestamp of the most recent AskAI query
- `last_relationship_analysis_at`, `last_community_detection_at`, `last_entity_merge_at` — last pipeline operations (informational)
- `neo4j_connected`, `checked_at`

Poll this endpoint and wait for `safe_to_redeploy: true` before triggering a graceful shutdown. Requires an API key with `manage` permission.

### API Usage Analytics

Enable tracking with `TRACK_ADMIN_API_KEY_USAGE=true`, then:

```bash
# Aggregated overview across all API keys
curl http://localhost:8000/api/admin/stats/overview \
  -H "X-API-Key: your-admin-key"

# Per-key detailed stats
curl http://localhost:8000/api/admin/api-keys/{id}/stats \
  -H "X-API-Key: your-admin-key"

# Daily usage history (up to 365 days)
curl "http://localhost:8000/api/admin/api-keys/{id}/usage-history?days=30" \
  -H "X-API-Key: your-admin-key"

# All keys with embedded stats
curl http://localhost:8000/api/admin/api-keys/with-stats \
  -H "X-API-Key: your-admin-key"
```

The overview returns:
- Total keys, active keys
- Request counts: today, this week, this month, all-time
- Total errors
- Most active key
- Endpoint breakdown (requests per endpoint category)

Usage data is stored in Neo4j as `APIKeyUsageLog` nodes with daily granularity.

### Logs

```bash
# All service logs
docker compose logs -f

# Backend only
docker compose logs -f backend

# Neo4j only
docker compose logs -f neo4j

# Frontend only
docker compose logs -f frontend

# Last 100 lines
docker compose logs --tail=100 backend
```

### Resource Monitoring

```bash
# Container resource usage
docker stats

# Disk usage by volumes
docker system df -v
```

## Background Task Management

Long-running operations run as in-process background tasks. The live store is
in memory, with a write-through shadow persisted to Neo4j: task state survives
restarts (a task interrupted by a redeploy reports `failed` with
"interrupted by server restart" instead of 404ing), completed/failed records
are kept for 7 days, and stale records are pruned automatically every hour —
the manual cleanup endpoint below remains for forcing it early:

```bash
# List all tasks (optionally filter by status/type)
curl "http://localhost:8000/api/tasks?status=running" \
  -H "X-API-Key: your-admin-key"

# Check specific task progress
curl http://localhost:8000/api/tasks/{task_id} \
  -H "X-API-Key: your-admin-key"
# Returns: task_id, task_type, status, progress_current, progress_total,
#          progress_percent, message, started_at, completed_at, error

# Get task result
curl http://localhost:8000/api/tasks/{task_id}/result \
  -H "X-API-Key: your-admin-key"
# Returns 202 if still running, 200 with result on completion

# Cancel a running task
curl -X DELETE http://localhost:8000/api/tasks/{task_id} \
  -H "X-API-Key: your-admin-key"

# Clean up old completed tasks (default: older than 24 hours)
curl -X POST "http://localhost:8000/api/tasks/cleanup?max_age_hours=24" \
  -H "X-API-Key: your-admin-key"
```

**Task types:**
- `batch_processing` — Processing pending documents
- `relationship_analysis` — Cross-document relationship discovery
- `community_detection` — Entity community detection
- `document_reprocessing` — Bulk document reprocessing
- `library_export` — Full library export to ZIP
- `library_import` — Library import from ZIP

The frontend persists active task IDs in sessionStorage for resume-on-reload.
A running task whose in-process coroutine dies without reporting completion is
reaped automatically: after 2 hours with no progress heartbeat it is marked
`failed` ("reaped as dead") so it can't block new syncs or keep the instance
reporting unsafe-to-redeploy forever.

## Backup and Recovery

### The backup sidecar (recommended)

Every deploy compose (Dokploy, Coolify, and the `docker-compose.backup.yml`
overlay for the standalone prod stack) ships a backup sidecar. Nightly
(`BACKUP_INTERVAL_SECONDS`, first run `BACKUP_INITIAL_DELAY_SECONDS` after
start) it takes an **online, verified** backup — no downtime:

- **Graph**: a server-side APOC logical export (`graph.cypher.gz`). The neo4j
  service writes the file itself (`NEO4J_apoc_export_file_enabled=true` + the
  `backups` volume mounted at its import directory), so there is no client
  quoting round-trip that could corrupt content. Works on Community and
  Enterprise.
- **Files**: a tar of `uploads`, `custom_inputs` (and `chat` where the chat
  service runs).
- **Verification**: exported row counts are checked against the live database,
  a `SHA256SUMS` manifest is written, and only then is the run stamped
  `.complete` + `LAST_SUCCESS`. Retention (`BACKUP_RETENTION_DAYS`, default 7)
  only rotates after a verified success and never deletes the newest complete
  backup — a string of failures cannot rotate away the last good copy.
- **Observability**: the sidecar's compose healthcheck goes unhealthy when the
  newest verified backup is older than 2× the interval, so a silently failing
  backup is visible in `docker ps` / the PaaS dashboard.

```bash
# Trigger a manual backup
docker compose exec backup /backup.sh

# List available backups
docker compose exec backup ls /backups
```

### Restore (sidecar backups)

```bash
# 1. Stop the backend
docker compose stop backend

# 2. Wipe + replay the graph from a chosen backup timestamp
docker compose exec -e RESTORE_WIPE=yes backup /restore.sh <timestamp>

# 3. Restore the file volumes (mounted read-only in the sidecar, so this runs
#    in a throwaway container — adjust volume names to your stack):
docker run --rm \
  -v <stack>_uploads_data:/data/uploads \
  -v <stack>_custom_inputs_data:/data/custom_inputs \
  -v <stack>_backups:/backups:ro \
  alpine tar -xzf /backups/<timestamp>/files.tar.gz -C /

# 4. Start the backend — startup recreates every constraint/index, including
#    the vector indexes the logical export doesn't carry.
docker compose start backend

# 5. Verify document/entity counts
curl http://localhost:8000/api/stats -H "X-API-Key: your-admin-key"
```

`restore.sh` verifies the backup's checksums before touching anything and
refuses incomplete backups. `RESTORE_WIPE=yes` is required because step 2
deletes the entire existing graph first.

### Manual physical dump (alternative, requires downtime)

`neo4j-admin database dump/load` remains available for stacks without the
sidecar. Note the two formats are **not interchangeable**: a `.dump` restores
only via `neo4j-admin database load`, a sidecar `graph.cypher.gz` only via
`restore.sh`.

```bash
docker compose stop backend frontend
docker compose exec neo4j neo4j-admin database dump neo4j --to-path=/backups
docker cp $(docker compose ps -q neo4j):/backups/neo4j.dump ./backups/
docker compose start backend frontend
```

### What else to back up

- **`.env`** — your configuration and secrets (store securely!). The sidecar
  backs up data volumes, never configuration.
- **Off-host copies** — backups land in the `backups` named volume **on the
  same host** as the data they protect. Ship them off-host (restic/rclone/
  object storage) for real disaster recovery.

## System Reset

For a complete or partial reset:

### Via the Web Interface

Settings > Danger Zone > System Reset

1. Select what to delete (checkboxes):
   - Documents (and all graph data)
   - Uploaded files
   - Custom inputs
   - Collections
   - API keys
2. Type "DELETE" to confirm
3. The system performs cleanup

### Via the API

```bash
curl -X POST http://localhost:8000/api/admin/reset \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "delete_documents": true,
    "delete_uploaded_files": true,
    "delete_custom_inputs": true,
    "delete_collections": true,
    "delete_api_keys": false
  }'
```

When documents are deleted, the system also cleans up:
- All entities, relationships, and communities
- `MergeHistory` nodes (deduplication audit trail)
- `SystemMeta` nodes (staleness timestamps)
- Frontend clears `dedup_dismissed` and `cortex_community_detection_task` from localStorage, and `regenerateStep`/`regenerateStartedAt`/`regenerateTaskId` from sessionStorage

## Data Import/Export

The library import/export feature allows you to migrate your entire knowledge base — documents, entities, relationships, communities, embeddings, and all graph data — between instances without re-running the expensive knowledge graph generation pipeline.

### Export

#### Via the Web Interface

Settings > Data Management > Export Library

1. Review the stats summary (documents, entities, relationships)
2. Click "Export Library"
3. Wait for the progress bar to complete (steps through documents, chunks, entities, relationships, communities, files)
4. Click "Download Export" to save the ZIP archive

#### Via the API

```bash
# Start export (returns a task ID)
curl -X POST http://localhost:8000/api/admin/export \
  -H "X-API-Key: your-admin-key"
# Returns: {"task_id": "task_abc123", "status": "pending", "message": "Export started"}

# Poll progress
curl http://localhost:8000/api/tasks/task_abc123 \
  -H "X-API-Key: your-admin-key"

# Download when complete
curl -OJ http://localhost:8000/api/admin/export/task_abc123/download \
  -H "X-API-Key: your-admin-key"
```

### Import

Two modes are available:

| Mode | Behavior | Use Case |
|------|----------|----------|
| **Clean** (default) | Requires the target instance to be empty. Fails with an error if data exists. | Importing into a fresh instance |
| **Replace** | Automatically wipes all existing data before importing. | Overwriting an existing instance |

#### Via the Web Interface

Settings > Data Management > Import Library

1. Select the import mode (Clean import or Replace all)
2. Drag and drop or browse for the export ZIP file
3. If using Replace mode, type "DELETE" to confirm
4. Click "Import Library" (or "Replace & Import")
5. Wait for the progress bar to complete
6. Review the result summary (imported counts and any warnings)

#### Via the API

```bash
# Clean import (target must be empty)
curl -X POST "http://localhost:8000/api/admin/import?mode=clean" \
  -H "X-API-Key: your-admin-key" \
  -F "file=@cortex-export-2026-03-27.zip"

# Replace import (wipes existing data first)
curl -X POST "http://localhost:8000/api/admin/import?mode=replace" \
  -H "X-API-Key: your-admin-key" \
  -F "file=@cortex-export-2026-03-27.zip"

# Poll progress
curl http://localhost:8000/api/tasks/{task_id} \
  -H "X-API-Key: your-admin-key"
```

> Behind a reverse proxy that cuts off long uploads (Traefik v3 defaults to a
> 60s body-read timeout), use the chunked upload endpoints the web UI uses:
> `POST /api/admin/import/upload/start` → sequential `PUT …/chunk?offset=N` →
> `POST …/finish?mode=clean`. See the API reference for the full contract.

### Export Archive Structure

The export is a ZIP64 archive containing:

```
cortex-export-YYYY-MM-DD.zip
├── manifest.json              # Version, date, embedding config, item counts
├── documents.ndjson           # Document nodes
├── chunks.ndjson              # Chunk nodes with embeddings
├── entities.ndjson            # Entity nodes with embeddings
├── relationships.ndjson       # Entity→Entity edges (type, weight, confidence)
├── communities.ndjson         # Community nodes
├── community_members.ndjson   # Community→Entity memberships
├── collections.ndjson         # Collection nodes
├── collection_members.ndjson  # Collection→Document memberships
├── chunk_mentions.ndjson      # Chunk→Entity links
├── merge_history.ndjson       # Deduplication audit trail
├── system_meta.ndjson         # Staleness timestamps
└── files/                     # Original document files
```

### Embedding Compatibility

The export manifest records the embedding model and dimension. On import, the system checks compatibility:
- **Same model + dimension**: Embeddings imported as-is, vector search works immediately
- **Different model or dimension**: Import proceeds with a warning. Vector search may not work correctly until documents are reprocessed

### Notes

- Only one export or import can run at a time (concurrent requests return HTTP 409)
- API keys are **not** included in the export (they are instance-specific)
- Export files are stored in a temp directory and cleaned up automatically
- The import remaps file paths to match the target instance's upload directory

## Orphaned Data Cleanup

If the knowledge graph accumulates orphaned data:

```bash
curl -X POST http://localhost:8000/api/cleanup/orphaned-entities \
  -H "X-API-Key: your-admin-key"
```

This removes:
- **Orphaned entities** — Entities with no MENTIONS relationships (not linked to any chunk)
- **Orphaned communities** — Communities with no HAS_MEMBER relationships

## Scaling

### Neo4j Memory Tuning

For production workloads, tune Neo4j memory in your Docker Compose:

```yaml
environment:
  NEO4J_server_memory_heap_initial__size: 1G
  NEO4J_server_memory_heap_max__size: 4G
  NEO4J_server_memory_pagecache_size: 2G
```

| Knowledge Base Size | Initial Heap | Max Heap | Page Cache |
|--------------------|-------------|---------|-----------|
| Small (< 100 docs) | 512 MB | 1 GB | 256 MB |
| Medium (100-1000) | 1 GB | 2 GB | 512 MB |
| Large (1000-10000) | 2 GB | 4 GB | 2 GB |
| Enterprise (10000+) | 4 GB | 8 GB | 4 GB |

### Backend Scaling

For horizontal scaling with multiple backend replicas:

```yaml
# docker-compose.prod.yml
backend:
  deploy:
    replicas: 3
    resources:
      limits:
        cpus: '2'
        memory: 4G
```

Place a load balancer (Nginx, Traefik) in front of backend replicas. Note that:
- Background tasks are stored in-memory per instance
- SSE streaming connections are long-lived — configure appropriate timeouts
- The cross-encoder model is loaded per instance (memory overhead)

### Uvicorn Workers

For Coolify or single-instance deployments, increase Uvicorn workers:

```env
UVICORN_WORKERS=2   # 1-2 GB RAM per worker
```

## System Configuration Viewing

View the current system configuration (no secrets exposed):

```bash
curl http://localhost:8000/api/admin/config \
  -H "X-API-Key: your-admin-key"
```

Returns all model names, API base URLs, context windows, feature flags, concurrency settings, and search weights — useful for verifying configuration after deployment.

### System Config panel: curated vs. full view

The **Admin → System Configuration** panel renders the same settings in the UI. By default it shows a **curated view**: models, API bases, context windows, embedding dimensions, and feature toggles. Advanced tuning knobs — output-token budgets, concurrency counts (extractions/relations/batches/vision), chunking parameters, hybrid-search weights, graph hops, community sizes, and similarity thresholds — are hidden to keep the panel readable for operators who don't tune internals.

Set `DISPLAY_FULL_SYSTEM_CONFIG=true` to reveal every knob in the panel. This is **display-only** — it changes what the admin UI renders, not the values themselves or what the `/api/admin/config` endpoint returns.

The panel also includes a **Privacy** section (always shown, even in the curated view) reporting the LLM-tracing content policy:

- **Prompt & Content Redaction** — *Enabled* means all prompts, completions, tool calls, embeddings, and image-analysis text are stripped to `[REDACTED]` before any trace leaves the instance (only structure — models, roles, token counts, cost, latency — is exported). This is the default; it is *Disabled* only when the operator sets `LANGFUSE_LOG_EXTENDED=true` for debugging.
- **LLM Tracing (Langfuse)** — whether tracing is active at all (`LANGFUSE_*` configured). When *Disabled*, nothing is exported anywhere.

Together these let an operator — or a customer auditing a hosted instance — confirm at a glance that the host is not logging prompt or completion content. See [Configuration → Observability](04-configuration.md) and `.claude/domain/observability.md` for the masking policy.

## Maintenance Checklist

### Weekly
- [ ] Check `/api/stats` for processing status (any stuck/failed documents?)
- [ ] Review API key usage statistics for anomalies
- [ ] Check disk space (`docker system df -v`)

### Monthly
- [ ] Run orphaned entity cleanup
- [ ] Review and rotate API keys
- [ ] Clean up old completed tasks
- [ ] Verify backups are running and restorable

### After Major Ingestion
- [ ] Run relationship analysis (Step 2)
- [ ] Run community detection (Step 3)
- [ ] Run entity deduplication scan
- [ ] Check for orphaned entities
