# Chapter 20: Troubleshooting

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
- Check `NEXT_PUBLIC_API_URL` is set correctly
  - Development: `http://localhost:8000`
  - Production: `https://api.yourdomain.com`
  - Coolify: Use `BACKEND_URL` or `SERVICE_FQDN_BACKEND_8000`
- Ensure the backend is running: `curl http://localhost:8000/health`
- Check CORS configuration if frontend and backend are on different domains

### 504 Gateway Timeout (Coolify)

**Symptom:** Requests time out with 504 errors.

**Solutions:**
- Ensure services with `SERVICE_FQDN_*` labels have `traefik.docker.network=coolify`
- Ensure services join the external `coolify` network
- Increase Traefik timeout settings for long-running operations
- Check backend health: the backend may be overloaded

## LLM and API Issues

### Entity Extraction Fails

**Symptom:** Documents process but no entities are extracted.

**Checklist:**
- [ ] `ENABLE_GRAPH_EXTRACTION=true`
- [ ] `OPENAI_API_KEY` (or extraction key) is valid
- [ ] `GRAPH_EXTRACTION_API_BASE` points to the correct endpoint
- [ ] `GRAPH_EXTRACTION_MAX_CONTEXT` doesn't exceed the model's actual context window
- [ ] Check backend logs for LLM API error responses

### Relationship Analysis Fails

**Symptom:** Relationship analysis task fails or produces 0 relationships.

**Checklist:**
- [ ] Entities exist (run Step 1 first)
- [ ] `RELATIONSHIP_MAX_CONTEXT` matches the model's context window (or leave 0 to inherit from `GRAPH_EXTRACTION_MAX_CONTEXT` / `OPENAI_MAX_CONTEXT`)
- [ ] `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS` is sufficient for Phase 2 batch (default: 16000)
- [ ] `RELATIONSHIP_MAX_OUTPUT_TOKENS` is sufficient for per-chunk + candidate scan (0 = inherit `EXTRACTION_MAX_OUTPUT_TOKENS` → primary, default 8000). The inherited 8000 already handles Qwen3-family verbose XML; only override if you've explicitly tightened the chain elsewhere and need to relax it.
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
- Use Turbo Mode for GPU-accelerated processing (no rate limits)

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
| Overall | Everything slow | Use Turbo Mode for GPU acceleration |

### Slow Relationship Analysis

Relationship analysis is the most compute-intensive step. Speed it up:

1. **Increase `PARALLEL_RELATIONSHIP_BATCHES`** — This is the most impactful setting. Default is 2; try 4-8 if your LLM API can handle it.
2. **Use Turbo Mode** — GPU-accelerated inference is 3-5x faster.
3. **Use a faster model** — Smaller models process batches faster.
4. **Increase `RELATIONSHIP_MAX_CONTEXT`** — Larger batches mean fewer total batches.

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

**Prevention:** Always back up before resetting. See [Chapter 18: Administration](18-administration.md).

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
