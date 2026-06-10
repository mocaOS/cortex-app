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
- `services/neo4j_service.py` — Graph DB operations, search, entity extraction, community detection, `delete_all_entities()` (DETACH DELETE all entities). See [`.claude/domain/entities.md`](domain/entities.md), [`.claude/domain/communities.md`](domain/communities.md), [`.claude/domain/relationships.md`](domain/relationships.md)
- `services/document_processor.py` — Ingestion pipeline. See [`.claude/domain/document-pipeline.md`](domain/document-pipeline.md)
- `services/graph_extractor.py` — LLM-based entity/relationship extraction (`async_relationship_client` and `relationship_model_name` properties for dedicated relationship model). See [`.claude/domain/relationships.md`](domain/relationships.md), [`.claude/domain/entities.md`](domain/entities.md)
- `services/compute3_service.py` — GPU-accelerated inference (Turbo Mode)
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
- `services/llm_config.py` — LLM configuration utility (Turbo Mode support, extraction/relationship model config). See [`.claude/domain/relationships.md`](domain/relationships.md)

## Frontend (`frontend/src/`)

Next.js App Router with unified navigation structure:

### Routes
- **Manage** section: Documents (`/documents`, default — "Generate Graph" button navigates to `/extract`), Knowledge Graph (`/extract`), Deduplicate (`/deduplicate`), Collections (`/collections`), Add (`/add`)
- **Explore** section: Knowledge Graph, Entities, Relationships, Communities, Deep Research, Chat (all tab-based on `/explore` with `?tab=graph|entities|relationships|communities|research|chat`)
- **Settings** (`/admin`): Statistics dashboard, system configuration (LLM Configuration with 5 sub-areas: Primary Model, Extraction Model, Relationship Model, Vision Model, Embeddings via `GET /api/admin/config`; no API keys exposed), API key management, data management, danger zone. Stats bar hidden on this page.
- `/` redirects to `/documents`
- `/entities`, `/relationships`, `/communities` redirect to their Explore tabs
- `/login` — Authentication page
- `/turbo` — Turbo Mode page

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
- Streaming responses for `/api/ask/stream` and `/api/ask/stream/thinking` endpoints
- Frontend uses `"use client"` directive for interactive components; API calls go through `lib/api.ts`
- All API endpoints are in `main.py` (no separate router modules)
- Turbo mode overrides both extraction and main model configs
- **Security defaults**: CORS is allowlist-driven (`CORS_ALLOWED_ORIGINS`; wildcard disables credentials). `ENVIRONMENT=production` fails fast on weak/default secrets via `config.py:_enforce_production_secrets`. See [`environment.md`](environment.md).
- **Per-instance footprint**: the heavy models (cross-encoder reranker, docling) are lazy-loaded and can be offloaded to a shared per-host service (`cortex-helper` repo) via `RERANKER_SERVICE_URL`/`DOCLING_SERVICE_URL` — key for packing many tenant stacks per machine. See [`domain/rag-pipeline.md`](domain/rag-pipeline.md), [`domain/document-pipeline.md`](domain/document-pipeline.md).
- **CI**: `.github/workflows/ci.yml` runs backend pytest + ruff (error-only) and frontend `tsc --noEmit` + lint on PRs.
