# Architecture

Cortex is an agentic knowledge base that ingests documents, extracts entities/relationships via LLMs, builds a Neo4j knowledge graph, and exposes it through a FastAPI REST API for RAG applications. It has a Next.js frontend for search, Q&A, graph exploration, and document management.

## Tech Stack

```
Next.js 15 (React 19, TypeScript)  →  FastAPI (Python 3.11+)  →  Neo4j 5.x (graph + vector)
         :3000                              :8000                    :7474/:7687
```

## Backend (`backend/app/`)

- `main.py` — FastAPI app with 40+ endpoints (monolithic router, no separate router modules)
- `config.py` — Pydantic BaseSettings, all env vars with defaults (see [`.claude/environment.md`](environment.md))
- `models.py` — Pydantic request/response models
- `services/neo4j_service.py` — Graph DB operations, search, entity extraction, community detection, `delete_all_entities()` (batched `CALL {} IN TRANSACTIONS` DETACH DELETE). See [`.claude/domain/entities.md`](domain/entities.md), [`.claude/domain/communities.md`](domain/communities.md), [`.claude/domain/relationships.md`](domain/relationships.md)
- `services/document_processor.py` — Ingestion pipeline. See [`.claude/domain/document-pipeline.md`](domain/document-pipeline.md)
- `services/graph_extractor.py` — LLM-based entity/relationship extraction (`async_relationship_client` and `relationship_model_name` properties for dedicated relationship model). See [`.claude/domain/relationships.md`](domain/relationships.md), [`.claude/domain/entities.md`](domain/entities.md)
- `services/vision_analyzer.py` — Image analysis and OCR. See [`.claude/domain/document-pipeline.md`](domain/document-pipeline.md)
- `services/docling_worker.py` — Standalone Docling conversion worker (separate process for CPU-bound document conversion, memory optimizations). See [`.claude/domain/document-pipeline.md`](domain/document-pipeline.md)
- `services/auth_service.py` — Admin JWT auth
- `services/api_key_service.py` — API key management CRUD (create/list/retrieve/update/delete with permissions)
- `services/api_usage_service.py` — API usage tracking (request logging per key, endpoint categorization, statistics)
- `services/prompt_security.py` — Prompt injection detection (regex-based detection of system prompt extraction, instruction bypass, encoding obfuscation)
- `services/researcher_agent.py` — Agent-based research pipeline. See [`.claude/domain/rag-pipeline.md`](domain/rag-pipeline.md)
- `services/research_prompts.py` — Prompt templates and tool definitions for researcher/writer agents. See [`.claude/domain/rag-pipeline.md`](domain/rag-pipeline.md)
- `services/library_transfer_service.py` — Full library export/import. See [`.claude/domain/admin-features.md`](domain/admin-features.md)
- `services/skill_service.py` — Agent Skills integration. See [`.claude/domain/skills.md`](domain/skills.md)
- `services/git_connector_service.py` + `services/git_providers/` — Git connector (GitHub/GitLab/Gitea): incremental clone+diff sync into the pipeline, provider abstraction, agent `git_repo` write tool. See [`.claude/domain/git-integration.md`](domain/git-integration.md)
- `services/llm_config.py` — LLM configuration utility (extraction/relationship model config) **and the OpenAI client factory** (`make_openai_client` / `make_async_openai_client`) — single decision point for Langfuse-wrapped vs plain clients. See [`.claude/domain/relationships.md`](domain/relationships.md), [`.claude/domain/observability.md`](domain/observability.md)
- `services/observability.py` — Langfuse wiring: client lifecycle (`init_langfuse`/`shutdown_langfuse`), agentic-trace grouping (`observed_trace`/`traced_sse`), manual generation records for non-SDK calls (`record_generation`). Env-driven; no-op when unconfigured. See [`.claude/domain/observability.md`](domain/observability.md)
- `services/helper_client.py` — transport layer for cortex-helper calls: shared HTTP client, retries with backoff, circuit breaker, `HELPER_STRICT_REMOTE`, `X-Tenant-ID`/`X-Request-ID` headers
- `services/rate_limiter.py` — opt-in per-API-key token bucket (`RATE_LIMIT_QPM`) on ask/upload endpoints
- `services/usage_meter.py` — instance-wide LLM-completion metering for the unit-denominated `MAX_QUERIES_PER_MONTH` quota (in-memory accumulator → batched `LLMUsageDay` flushes; counted at the client-factory wrap in `llm_config`). See [`.claude/domain/admin-features.md`](domain/admin-features.md#monthly-usage-metering-unit-denominated-quota)
- `logging_setup.py` — `LOG_FORMAT=plain|json` + `X-Request-ID` correlation (contextvar stamped on every log line, echoed on responses, forwarded to cortex-helper)
- `metrics.py` — Prometheus metrics (no-op without `prometheus-client`); `GET /metrics` is admin-key protected

## Frontend (`frontend/src/`)

Next.js App Router with unified navigation structure:

### Routes
- **Manage** section: Documents (`/documents`, default — "Generate Graph" button navigates to `/extract`), Knowledge Graph (`/extract`), Deduplicate (`/deduplicate`), Collections (`/collections`), Add (`/add`)
- **Explore** section: Knowledge Graph, Entities, Relationships, Communities, Deep Research, Chat (all tab-based on `/explore` with `?tab=graph|entities|relationships|communities|research|chat`)
- **Settings** (`/admin`): Statistics dashboard, system configuration (LLM Configuration with 5 sub-areas: Primary Model, Extraction Model, Relationship Model, Vision Model, Embeddings via `GET /api/admin/config`; no API keys exposed), API key management, data management, danger zone. Stats bar hidden on this page. The config panel includes a **Privacy** section surfacing the Langfuse content-masking state (`langfuse_tracing_active` + `langfuse_log_extended`) so an operator can prove prompt/completion text is redacted before export — see [`.claude/domain/observability.md`](domain/observability.md#content-masking).
- `/` redirects to `/documents`
- `/entities`, `/relationships`, `/communities` redirect to their Explore tabs
- `/login` — Authentication page

### Key Component Directories
- `lib/api.ts` — API client with auth headers
- `lib/session.ts` — JWT session management
- `components/layout/` — Header (top nav with Manage/Explore), SubMenu (contextual tabs), StatsBar (4 KPI cards)
- `components/upload/` — UploadModal (drag-and-drop + collection selector), UploadZone, UploadProgress, UploadFileItem
- `components/documents/` — DocumentCard, DocumentFilters, DocumentBulkActions
- `components/explore/` — EntitiesBrowser, RelationshipsBrowser, CommunitiesBrowser, KnowledgeGraph, DeduplicationView
- `components/ask/` — AskInput, AskSettings, ChatMessage, EmptyChat
- `components/admin/` — SkillsManager, SkillConfigModal, SystemResetModal, LibraryTransferSection, ApiKeyManager, ApiKeyCard, ApiKeyAnalytics, UsageChart
- `components/collections/` — CollectionCard, CommunitySection, CreateCollectionForm

## Cross-Cutting Patterns

- Backend uses singleton service instances (Neo4jService, DocumentProcessor, etc.)
- Background tasks via FastAPI's `BackgroundTasks` for document processing
- **Task-store persistence**: the in-memory `_task_store` is write-through shadowed to Neo4j `TaskRecord` nodes (dirty-set + 3s flusher `_task_persist_loop` in `main.py`; hourly prune, 7-day retention). Startup marks persisted pending/running records failed ("interrupted by server restart"); `GET /api/tasks/{id}` and `/result` fall back to the record when the id isn't in memory — restart no longer means 404.
- Streaming responses for `/api/ask/stream` and `/api/ask/stream/thinking` endpoints
- Frontend uses `"use client"` directive for interactive components; API calls go through `lib/api.ts`
- All API endpoints are in `main.py` (no separate router modules)
- **Security defaults**: CORS is allowlist-driven (`CORS_ALLOWED_ORIGINS`; wildcard disables credentials). `ENVIRONMENT=production` fails fast on weak/default secrets via `config.py:_enforce_production_secrets`. See [`environment.md`](environment.md).
- **Per-instance footprint**: the heavy models (cross-encoder reranker, docling) are lazy-loaded and can be offloaded to a shared per-host service (`cortex-helper` repo) via `RERANKER_SERVICE_URL`/`DOCLING_SERVICE_URL` — key for packing many tenant stacks per machine. The **slim image** (`Dockerfile.prod` build arg `INSTALL_LOCAL_ML=false`) drops torch/docling entirely (~1.2GB vs full image) for helper-backed deployments. See [`domain/rag-pipeline.md`](domain/rag-pipeline.md), [`domain/document-pipeline.md`](domain/document-pipeline.md).
- **Efficiency flags (v-next)**: batched KG writes, chunk-batched relationship extraction (**default ON** since 2026-07-03, live-validated), Phase B checkpointing, reprocess delta, prompt-cache discipline — the rest default-off behind env flags, gated on `bench/BASELINE.md` A/B runs. See [`environment.md`](environment.md#efficiency-flags-v-next--default-off-until-bench-validated-see-benchbaselinemd).
- **Graceful shutdown**: uvicorn `--timeout-graceful-shutdown` + compose `stop_grace_period` drain in-flight requests; SSE streams get a terminal `event: shutdown` frame (clients reconnect) and the lifespan awaits task cancellation before closing Neo4j.
- **CI**: `.github/workflows/ci.yml` runs backend pytest + ruff (error-only), a slim-image build/import smoke test, and frontend `tsc --noEmit` + lint on PRs.
