# Chapter 18: Administration and Maintenance

This chapter covers day-to-day administration tasks for self-hosted Cortex Library deployments.

## Monitoring

### Health Checks

```bash
# Backend health (includes Neo4j connectivity)
curl http://localhost:8000/health
# Expected: {"status": "healthy", "neo4j_connected": true, "version": "1.0.0"}
```

In Docker Compose, the backend health check runs automatically:
- **Development**: Not configured (relies on Docker restart)
- **Production**: `curl http://localhost:8000/health` every 30 seconds, 10s timeout, 3 retries
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
- Average entity mentions
- Staleness timestamps (last relationship analysis, community detection, entity merge)

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

Long-running operations run as background tasks stored in an in-memory task store:

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

Tasks are stored in-memory and do not survive backend restarts. The frontend persists active task IDs in sessionStorage for resume-on-reload.

## Backup and Recovery

### Neo4j Database Backup

```bash
# Stop the application for consistent backup
docker compose stop backend frontend

# Create a database dump
docker compose exec neo4j neo4j-admin database dump neo4j --to-path=/backups

# Copy backup from container
docker cp $(docker compose ps -q neo4j):/backups/neo4j.dump ./backups/

# Restart services
docker compose start backend frontend
```

### Neo4j Database Restore

```bash
# Stop all services
docker compose stop

# Copy backup into container
docker cp ./backups/neo4j.dump $(docker compose ps -q neo4j):/backups/

# Restore (overwrites existing data)
docker compose exec neo4j neo4j-admin database load neo4j \
  --from-path=/backups --overwrite-destination=true

# Restart services
docker compose start
```

### File Backup

In addition to the Neo4j database, back up these directories:
- **`uploads/`** (or `UPLOAD_DIR`) — Original uploaded files
- **`custom_inputs/`** (or `CUSTOM_INPUTS_DIR`) — Custom input files
- **`.env`** — Your configuration (store securely!)

### Automated Backup Script

```bash
#!/bin/bash
# backup.sh — Run daily via cron
BACKUP_DIR="/path/to/backups/$(date +%Y-%m-%d)"
mkdir -p "$BACKUP_DIR"

# Neo4j dump
docker compose exec -T neo4j neo4j-admin database dump neo4j --to-path=/backups
docker cp $(docker compose ps -q neo4j):/backups/neo4j.dump "$BACKUP_DIR/"

# File backups
cp -r uploads/ "$BACKUP_DIR/uploads/"
cp -r custom_inputs/ "$BACKUP_DIR/custom_inputs/"

# Clean old backups (keep 30 days)
find /path/to/backups/ -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +

echo "Backup completed: $BACKUP_DIR"
```

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
- Frontend clears `dedup_dismissed` and `moca_community_detection_task` from localStorage, and `regenerateStep`/`regenerateStartedAt`/`regenerateTaskId` from sessionStorage

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
