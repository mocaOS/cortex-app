# Chapter 19: Troubleshooting

This chapter covers common issues and their solutions.

## Deployment Issues

### Port Conflicts

**Symptom:** Services fail to start with "address already in use" errors.

**Solution:**
```bash
# Check what's using port 3000
lsof -i :3000
# or
ss -tlnp | grep 3000

# Kill the process or change the Library's port in docker-compose.yml
```

### Neo4j Won't Start

**Symptom:** Neo4j container restarts repeatedly or fails with authentication errors.

**Solutions:**

| Cause | Fix |
|-------|-----|
| Password mismatch with existing data | Delete the Neo4j volume: `docker volume rm library_neo4j_data` and restart |
| Coolify: `NEO4J_USER` set manually | Remove `NEO4J_USER` — Coolify uses the default `neo4j` user with auto-generated password |
| Insufficient memory | Increase Docker memory allocation; check `NEO4J_server_memory_heap_max__size` |
| APOC plugin not loaded | Verify `NEO4J_PLUGINS='["apoc"]'` and `NEO4J_dbms_security_procedures_unrestricted=apoc.*` |

### Backend Can't Connect to Neo4j

**Symptom:** Backend logs show "Failed to connect to Neo4j" or health check shows `neo4j_connected: false`.

**Solutions:**
- Ensure `NEO4J_URI` uses the Docker service name: `bolt://neo4j:7687` (not `localhost` in Docker)
- Wait for Neo4j health check to pass before starting backend (`depends_on` with `condition: service_healthy`)
- Check Neo4j logs: `docker compose logs neo4j`

### Frontend Shows "Network Error"

**Symptom:** Frontend can't reach the backend API.

**Solutions:**
- Check `NEXT_PUBLIC_API_URL` is set correctly — it is the backend URL as seen
  from the **browser**, not from inside Docker
  - Development: `http://localhost:8000`
  - LAN self-host (dashboard opened from another machine): the host's reachable
    address, e.g. `http://192.168.68.113:8000` — `localhost` would point at the
    *viewer's* machine and fail with `ERR_CONNECTION_REFUSED`
  - Production: `https://api.yourdomain.com`
  - Coolify: Use `BACKEND_URL` or `SERVICE_FQDN_BACKEND_8000`
- After changing it in `.env`, recreate the frontend container:
  `docker compose up -d --force-recreate --no-deps frontend` (a plain `restart`
  does not re-read `.env`)
- Ensure the backend is running: `curl http://localhost:8000/health`
- Check CORS configuration if frontend and backend are on different domains

### 504 Gateway Timeout (Coolify)

**Symptom:** Requests time out with 504 errors.

**Solutions:**
- Ensure services with `SERVICE_FQDN_*` labels have `traefik.docker.network=coolify`
- Ensure services join the external `coolify` network
- Increase Traefik timeout settings for long-running operations
- Check backend health: the backend may be overloaded

### 502 Bad Gateway on Large Uploads (Coolify)

**Symptom:** A large file upload fails with HTTP 502 after roughly a minute, while the same upload works on a local instance. Backend logs show no access line for the request (it died in transit).

**Cause:** On Coolify, the browser uploads directly to the backend FQDN through Traefik. Traefik v3 sets `respondingTimeouts.readTimeout` to **60 seconds by default** — it covers reading the entire request body, so any single request whose body takes longer than a minute over the wire is cut off mid-transfer.

**Library import is not affected anymore:** the web UI uploads export archives in small chunks (`/api/admin/import/upload/*`), so each request completes in seconds regardless of archive size. If you see this on library import, update to a version with chunked upload.

**For other large single-request uploads** (e.g. `curl` against the single-shot `/api/admin/import`), either raise the Traefik timeout in Coolify → Servers → your server → Proxy (then restart the proxy):

```
--entrypoints.https.transport.respondingTimeouts.readTimeout=1800
```

or upload from the Coolify host itself, bypassing Traefik:

```bash
curl -H "X-API-Key: <admin-key>" -F "file=@export.zip" \
  "http://<backend-container-ip>:8000/api/admin/import?mode=clean"
```

## LLM and API Issues

### Entity Extraction Fails

**Symptom:** Documents process but no entities are extracted.

**Checklist:**
- [ ] `ENABLE_GRAPH_EXTRACTION=true`
- [ ] `OPENAI_API_KEY` (or extraction key) is valid
- [ ] `GRAPH_EXTRACTION_API_BASE` points to the correct endpoint
- [ ] `GRAPH_EXTRACTION_MAX_CONTEXT` isn't oversized (recommended 16000) — extraction output scales with input, so batches sized to the model's full window time out at real decode speeds and silently lose entities
- [ ] Check backend logs for LLM API error responses

### Extraction Looks Stuck / Progress Frozen

**Symptom:** A document sits at the same "Finding entities" percentage for many minutes; from the outside the whole system appears hung.

**What's usually happening:** nothing is hung — a single extraction batch on a slow local model runs for minutes, and when the model's output overflows `EXTRACTION_MAX_OUTPUT_TOKENS`, the batch automatically splits and retries its halves. That retry churn roughly doubles the batch's wall time while the completed-chunks count stands still.

**How to confirm from the backend logs:**
- The progress message now updates at the start of every batch call (`Finding entities: 120/741 chunks (batch 8/22)...`) — if the batch counter keeps ticking, the run is healthy, just slow. A growing denominator (`8/22` → `9/23`) means batches are being split and retried.
- Each batch logs a telemetry line: `Entity extraction batch 8/22 (65 chunks): extracted 143 entities in 87.3s (in≈19234 tok, out=8000 tok, finish=length)`. `finish=length` means the output cap was hit.
- A one-shot warning — `output budget looks too small for the input batch size` — is logged when overflows repeat. That's the config diagnosis: raise `EXTRACTION_MAX_OUTPUT_TOKENS` (recommended 16000, a ceiling matched to `GRAPH_EXTRACTION_MAX_CONTEXT`) or lower `GRAPH_EXTRACTION_MAX_CONTEXT`. Persistent overflow on entity-dense docs usually means the extraction prompt's terse-description instruction isn't being honored — the model is emitting long descriptions.
- The mirror case: repeated `timed out — splitting and retrying halves` warnings with a one-shot `batches keep timing out` diagnosis mean the endpoint can't answer full-size batches inside `LLM_REQUEST_TIMEOUT_SECONDS`. The batch budget auto-shrinks for the rest of the process (and later documents), but fix the source: lower `GRAPH_EXTRACTION_MAX_CONTEXT`, reduce `BATCH_PROCESSING_CONCURRENCY`, or raise the timeout.
- When extraction settles, a health summary is logged: `20 planned batches -> 34 LLM calls (8 truncation splits, ...)` — planned-vs-actual calls quantifies how much work the ratio mismatch cost. The same counters are stored on the document node as `extraction_stats` for post-hoc inspection.
- A `model repetition loop suspected` warning means the model degenerated and flooded the batch with duplicate entities (they are collapsed by dedup); if it recurs on a specific document, reprocess it — a bigger output cap will not fix a looping model.

### Relationship Analysis Fails

**Symptom:** Relationship analysis task fails or produces 0 relationships.

**Checklist:**
- [ ] Entities exist (run Step 1 first)
- [ ] `RELATIONSHIP_MAX_CONTEXT` is left at 0 (inherit) — a full-window value (e.g. 256000) causes multi-minute prefills and timeouts on self-hosted GPUs; only widen it for legacy `llm_scan` mode on fast-prefill hosted endpoints
- [ ] `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS` is sufficient for Phase 2 batch (default: 16000)
- [ ] `RELATIONSHIP_MAX_OUTPUT_TOKENS` is sufficient for per-chunk + candidate scan (0 = inherit `EXTRACTION_MAX_OUTPUT_TOKENS`, default 12000 since 2026-07-09). The inherited budget already comfortably covers the compact `ENT|`/`REL|` output format; only override if you've explicitly tightened the chain elsewhere and need to relax it.
- [ ] In the default `targeted` mode with no embedding API key configured, candidates come from document co-mention only — pairs need at least `RELATIONSHIP_MIN_SHARED_DOCS` (default 2) shared documents, so very small or single-document libraries may yield few candidates
- [ ] LLM API key has sufficient credits/quota
- [ ] Check task status for error messages: `GET /api/tasks/{task_id}`

### Agent Pipeline Fails (Deep Research)

**Symptom:** Deep Research mode fails, returns empty answers, or errors.

**Root cause:** The agent pipeline requires a model that supports **function calling / tool use** (OpenAI `tools` parameter).

**Models that support function calling:**
- GPT-4o, GPT-4o-mini, GPT-4 Turbo
- Claude 3/3.5 (via LiteLLM)
- Mistral Large
- Command R+

**Models that typically do NOT support function calling:**
- Most Ollama/vLLM local models (unless specifically fine-tuned)
- Smaller quantized models
- Older GPT-3.5 variants

**Fix:** Set `ENABLE_AGENT_RESEARCH=false` to use the legacy fixed-step pipeline that works with any LLM.

### LLM Rate Limiting

**Symptom:** Intermittent errors during batch processing or relationship analysis.

**Solutions:**
- Reduce `PARALLEL_RELATIONSHIP_BATCHES` to lower concurrent LLM calls
- Reduce `CONCURRENT_EXTRACTIONS`
- Reduce `BATCH_PROCESSING_CONCURRENCY`
- Reduce `VISION_MAX_CONCURRENT`

## Knowledge Graph Issues

### No Entities After Processing

**Symptom:** Documents show "completed" but entity count is 0.

**Checklist:**
- [ ] `ENABLE_GRAPH_EXTRACTION=true`
- [ ] Documents have sufficient text content (very short documents may not yield entities)
- [ ] Check backend logs during processing for extraction errors
- [ ] Verify the extraction model API is reachable

### Communities Not Detected

**Symptom:** Community detection runs but finds 0 communities.

**Checklist:**
- [ ] Relationships exist (run Step 2 first — communities need connected entities)
- [ ] `MIN_COMMUNITY_SIZE` isn't too high for your graph
- [ ] Your graph has sufficient density — very sparse graphs with few relationships may not form communities
- [ ] Try lowering `MIN_COMMUNITY_SIZE` to 2

### Stale Knowledge Graph

**Symptom:** The Knowledge Graph page shows "needs update" on steps.

**Solution:** This is normal behavior. The system detects:
- New documents since last entity extraction → Re-run Step 1
- New entities since last relationship analysis → Re-run Step 2
- Changes since last community detection → Re-run Step 3

Run the indicated step to resolve staleness.

## Performance Issues

### Slow Document Processing

**Diagnosis and solutions:**

| Bottleneck | Symptom | Solution |
|-----------|---------|----------|
| Docling conversion | Stuck at 0% for large PDFs | Check `DOCLING_PAGE_CHUNK_SIZE`, try `DOCLING_USE_PYPDFIUM_FOR_LARGE_MB` |
| Embedding API | Slow after conversion | Check API latency, increase thread pool |
| Entity extraction | Slow at "extracting" stage | Increase `CONCURRENT_EXTRACTIONS`, check LLM API speed |
| Vision API | Image progress stuck | Increase `VISION_MAX_CONCURRENT`, check vision API |
| Overall | Everything slow | Tune concurrency settings, use a faster LLM endpoint |

### Documents Stuck in Processing

**Symptom:** Documents show **Processing** indefinitely and never complete.

**Solutions:**
- A server restart no longer leaves documents stuck. On startup, any documents left in **Processing** are automatically reset to **Pending** so you can process them again (via the process-pending endpoint or the **Generate Graph** flow).
- If a document repeatedly fails on a large or complex file, the local Docling conversion now times out (configurable via `DOCLING_CONVERSION_TIMEOUT`, default 600 seconds) and the document is marked **Failed** with a message instead of hanging.

### Slow Relationship Analysis

With the default `RELATIONSHIP_DISCOVERY_MODE=targeted`, Step 2 finishes in minutes even on large graphs — candidate pairs come from the entity-embedding index and document co-mention, and the LLM only verifies them in small calls. If it still takes hours:

1. **Check `RELATIONSHIP_DISCOVERY_MODE`** — if it is set to `llm_scan` (the legacy full-batch scan), switch to `targeted` (or unset it to get the default). This is the single biggest speedup on large graphs.
2. **Increase `PARALLEL_RELATIONSHIP_BATCHES`** — Parallel verification calls (targeted) or batches (legacy). Default is 2; try 4-8 if your LLM API can handle it.
3. **Use a faster model** — Smaller models process calls faster.
4. **Increase `RELATIONSHIP_MAX_CONTEXT`** (legacy `llm_scan` mode, fast-prefill hosted endpoints only) — Larger batches mean fewer total batches. On self-hosted GPUs this backfires: wide prompts prefill for minutes and time out.

### Slow Search/Q&A

| Symptom | Solution |
|---------|----------|
| Vector search slow | Check Neo4j memory settings; ensure vector index exists |
| Re-ranking slow | The cross-encoder model may need to be downloaded on first use |
| Agent iterations take long | Reduce `RESEARCHER_MAX_ITERATIONS_QUALITY` |
| Everything slow | Use `use_fast_search=true` for simple queries |

## Image Analysis Issues

### No Images Extracted

**Checklist:**
- [ ] Document actually contains embedded images (not linked/referenced)
- [ ] Docling successfully converted the document (check backend logs)
- [ ] File format supports image extraction (PDF, DOCX, PPTX)

### Vision API Errors

**Checklist:**
- [ ] `VISION_MODEL` is set (empty = Docling fallback only)
- [ ] `VISION_MODEL_API_BASE` and `VISION_MODEL_API_KEY` are correct
- [ ] The vision model supports image input
- [ ] Check backend logs for specific API error messages
- [ ] Retry logic: the system retries 3 times with exponential backoff

### Image Analysis Blocking Step 2

**Symptom:** Step 2 (Relationship Analysis) stays blocked even though documents show "completed".

**Explanation:** Step 1 tracks image analysis separately. Documents with `processing_status === "completed"` but `image_progress_current < image_progress_total` are treated as still in progress.

**Solution:** Wait for all image analysis to complete (auto-refreshes every 5 seconds) or check individual document progress via the API.

## Authentication Issues

### Can't Log In to Web Interface

**Checklist:**
- [ ] `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set in `.env`
- [ ] Backend is running and healthy
- [ ] Frontend `NEXT_PUBLIC_API_URL` points to the correct backend
- [ ] Try clearing browser cookies and cache

### API Key Returns 401

**Checklist:**
- [ ] Key is included in the `X-API-Key` header
- [ ] Key hasn't been revoked (check in Settings)
- [ ] Key hasn't expired
- [ ] For admin operations, use the admin key (not a generated key)

A 401 is authoritative: the key was checked against the store and rejected. When the auth store itself can't be consulted, the API answers 503 instead (below) — clients should never see a 401 just because Neo4j blinked.

### API Key Returns 503 (`Retry-After` header present)

**Cause:** The key could not be validated because Neo4j was unreachable (restart, OOM recovery, network blip). The key itself may be perfectly valid.

**Fix:** Retry after the `Retry-After` interval. If it persists, check Neo4j health and the backend logs around "Error validating API key".

### API Key Returns 403

**Cause:** The key doesn't have sufficient permissions.

**Fix:** The key has `read` permission but the endpoint requires `manage` (or admin). Update the key's permissions or use a different key.

## Data Issues

### Orphaned Entities in Graph

**Symptom:** Entities exist without any chunk mentions or relationships.

**Solution:**
```bash
curl -X POST http://localhost:8000/api/cleanup/orphaned-entities \
  -H "X-API-Key: your-admin-key"
```

### Duplicate Entities

**Symptom:** Multiple entities for the same real-world thing (e.g., "OpenAI" and "Open AI").

**Solution:** Use the Deduplication feature:
1. Navigate to Manage > Deduplicate
2. Scan for duplicates
3. Review and merge groups
4. Re-run community detection after merging

### Lost Data After Reset

**Prevention:** Always back up before resetting. See [Chapter 17: Administration](17-administration.md).

**If no backup exists:** Data deleted by system reset cannot be recovered. Re-upload documents and rebuild the graph.

## Env Var Changes Don't Take Effect After `docker compose restart`

**Symptom:** You update `.env`, run `docker compose restart backend`, and the backend still behaves as if the old values are in place. The admin Settings page still shows the old model / endpoint / dimension.

**Root cause:** `docker compose restart` restarts the container's *process* but does NOT re-read the `env_file:`. Environment variables are baked into the container at *create* time. A restart preserves them.

**Fix:** Use `up --force-recreate` (or down + up):

```bash
docker compose up -d backend --force-recreate
# or
docker compose down backend && docker compose up -d backend
```

**Verify the new env reached the container:**

```bash
docker compose exec backend printenv | grep YOUR_VAR_NAME
```

This is a common foot-gun whenever the `.env`-file approach is used. Aliasing `dcr() { docker compose up -d --force-recreate "$1"; }` is a popular workaround.

## Getting Help

- **Check backend logs** — Most errors are logged with details: `docker compose logs -f backend`
- **Check the API docs** — Visit `/docs` on your instance for Swagger UI
- **Technical documentation** — See the `documentation/` directory for detailed guides
- **File an issue** — Report bugs at the project's issue tracker
- **Follow MOCA** — [x.com/MuseumofCrypto](https://x.com/MuseumofCrypto)
