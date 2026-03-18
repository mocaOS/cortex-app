# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MOCA Library is an agentic knowledge base that ingests documents, extracts entities/relationships via LLMs, builds a Neo4j knowledge graph, and exposes it through a FastAPI REST API for RAG applications. It has a Next.js frontend for search, Q&A, graph exploration, and document management.

## Architecture

```
Next.js 15 (React 19, TypeScript)  →  FastAPI (Python 3.11+)  →  Neo4j 5.x (graph + vector)
         :3000                              :8000                    :7474/:7687
```

**Backend** (`backend/app/`):
- `main.py` — FastAPI app with 40+ endpoints (monolithic router)
- `config.py` — Pydantic BaseSettings, all env vars with defaults
- `models.py` — Pydantic request/response models
- `services/neo4j_service.py` — Graph DB operations, search, entity extraction, community detection
- `services/document_processor.py` — Ingestion pipeline: Docling conversion → chunking → embedding → entity extraction → graph storage
- `services/graph_extractor.py` — LLM-based entity/relationship extraction
- `services/compute3_service.py` — GPU-accelerated inference (Turbo Mode)
- `services/vision_analyzer.py` — Image analysis and OCR
- `services/auth_service.py` — Admin JWT auth
- `services/prompt_security.py` — Prompt injection detection

**Frontend** (`frontend/src/`):
- Next.js App Router with unified navigation structure:
  - **Manage** section: Documents (`/documents`, default), Generate Graph (`/extract` — 3-step pipeline: entity extraction → relationship analysis → community detection with staleness tracking), Collections, Add
  - **Explore** section: Knowledge Graph, Entities (read-only), Relationships (read-only), Communities (read-only), Deep Research, Chat (all tab-based on `/explore` with `?tab=graph|entities|relationships|communities|research|chat`)
  - **Settings** (`/admin`): Statistics dashboard, system configuration, API key management, danger zone. Stats bar hidden on this page.
  - `/` redirects to `/documents`
  - `/entities`, `/relationships`, `/communities` redirect to their Explore tabs
- `lib/api.ts` — API client with auth headers
- `lib/session.ts` — JWT session management
- `components/layout/` — Header (top nav with Manage/Explore), SubMenu (contextual tabs), StatsBar (4 KPI cards: Documents, Entities, Relations, Communities)
- `components/upload/UploadModal.tsx` — Upload modal (drag-and-drop + collection selector), closes immediately on file selection; upload progress shown inline in document list
- `components/documents/DocumentCard.tsx` — Document row with view button: `.md` files open in an in-app Markdown viewer modal; all other file types open in a new browser tab via `/api/documents/{id}/file` (browser decides to display or download)
- `components/explore/` — Paginated browsers for entities, relationships, communities (with search, type filters, detail modals) + KnowledgeGraph visualization (force-graph 2D, default 2000 nodes)
- `app/extract/page.tsx` — Generate Graph page: 3-step pipeline with status tracking, staleness detection via `SystemMeta` Neo4j nodes (`last_relationship_analysis_at`, `last_community_detection_at`), cascading blocked states, per-step Inspect buttons linking to Explore tabs
- `components/` — UI components organized by feature

**Document Processing Pipeline**: Upload (modal closes immediately, progress in document list) → Docling conversion → sentence/word chunking → OpenAI embeddings → LLM entity extraction with fuzzy entity resolution (Levenshtein 85% dedup, triggered via "Extract Entities" button on Documents or Generate Graph page) → entity type normalization (10 allowed types, fuzzy matched) → fuzzy entity-to-chunk linking → Neo4j storage → (separate job via Generate Graph Step 2) relationship analysis with source text context (chunk co-mentions fed to LLM per batch, 120 entities/batch, sequential, with ETA tracking) → (Step 3) community detection (Leiden with Louvain fallback, weight-aware, co-mention edges for sparse graphs) → community summarization (assistant prefill for JSON output). Step 2 supports incremental mode (builds on existing) and rebuild mode (deletes all relationships first). Timestamps persisted in `SystemMeta` Neo4j nodes for staleness tracking.

**RAG Query Pipeline**: Query embedding → entity extraction → community search → hybrid search (vector 0.5 + fulltext 0.3 + graph 0.2, RRF) → cross-encoder reranking → context assembly → LLM generation. Agentic mode adds multi-step decomposition.

## Development Commands

### Docker (primary development method)
```bash
# Dev environment (all services with hot reload)
docker compose up --build

# Production
docker compose -f docker-compose.prod.yml up --build
```

### Local development (without Docker)
```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev        # Dev server on :3000
npm run build      # Production build
npm run lint       # ESLint
```

### Neo4j
Requires Neo4j 5.15+ with APOC plugin. In Docker this is preconfigured. For local dev, set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` env vars.

## Environment Configuration

Copy `.env.example` to `.env`. Key variables:
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` — database connection
- `OPENAI_API_KEY`, `OPENAI_MODEL` (default: gpt-4o-mini) — LLM provider
- `OPENAI_API_BASE` — for LiteLLM-compatible providers
- `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `USE_OPENAI_EMBEDDINGS` — embedding config
- `EMBEDDING_API_BASE`, `EMBEDDING_API_KEY` — optional separate endpoint/key for embeddings (defaults to `OPENAI_API_BASE`/`OPENAI_API_KEY`)
- `ENABLE_GRAPH_EXTRACTION`, `ENABLE_COMMUNITY_DETECTION`, `ENABLE_AGENTIC_RAG` — feature flags
- `EXTRACTION_MAX_CONTEXT` (default: 32768), `RELATIONSHIP_MAX_CONTEXT` (default: 65536), `RELATIONSHIP_MAX_OUTPUT_TOKENS` (default: 8000) — context window and output budgets for graph extraction
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SESSION_SECRET` — auth
- `CHUNK_SIZE`, `CHUNK_OVERLAP`, `CHUNK_BY` (word/sentence) — document processing

## Key Patterns

- Graph extraction uses `get_extraction_llm_config()` from `llm_config.py` (separate from Q&A model)
- Turbo mode overrides both extraction and main model configs
- Entity extraction is per-document (Phase A) with fuzzy resolution (`store_entity_with_resolution()`, Levenshtein 85%), entity type normalization (10 allowed types via `_normalize_entity_type()` with rapidfuzz fallback to Concept)
- Relationship analysis is per-collection (Phase B) with source text context: `get_chunk_context_for_entities()` fetches co-mention chunks per batch, `get_batch_context` callback wired through `analyze_relationships_batched_async()`
- Relationship batching: 120 entities/batch hard cap, 15% overlap, sequential execution. Token budget: entities formatted at 500-char description length. Existing relationships (up to 400) filtered per-batch to avoid rediscovery.
- Relationship type constraint: prompt enforces standard types, `_extract_xml_relationships()` fuzzy-matches non-standard types to `DEFAULT_RELATION_TYPES` via rapidfuzz (80% threshold, fallback to RELATED_TO)
- Relationship analysis supports `rebuild=true` mode (deletes all relationships before re-analysis) alongside default incremental mode
- Community detection: tries Leiden first (GDS), falls back to Louvain, then BFS. Uses relationship weights (`relationshipWeightProperty`), undirected projection (UNION both directions), and co-mention edges (entities sharing a chunk get implicit weight-2.0 edge). Old communities cleaned up before re-detection.
- Community summarization: assistant prefill `{"` forces JSON output, double-brace dedup, 5-strategy parsing fallback (direct parse, strip-to-first-brace, code fence, regex object, regex fields), fallback names from top entity names
- Generate Graph page guides users through the 3-step pipeline with staleness detection: pending docs → needs relationship re-analysis → needs community re-detection. Steps cascade (Step 2/3 grey out when prior step needs update). Each step has an "Inspect" button linking to the relevant Explore tab.
- Progress tracking: relationship analysis shows batch X/Y with ETA computed from observed batch duration. Community detection polls task status every 2 seconds. Stats bar refreshes every 5 seconds.
- `SystemMeta` Neo4j nodes store `last_relationship_analysis_at` and `last_community_detection_at` timestamps. Upload dates are naive (no timezone) — frontend appends `Z` for UTC comparison.
- Backend uses singleton service instances (Neo4jService, DocumentProcessor, etc.)
- Background tasks via FastAPI's `BackgroundTasks` for document processing
- Streaming responses for `/api/ask/stream` and `/api/ask/stream/thinking` endpoints
- Frontend uses `"use client"` directive for interactive components; API calls go through `lib/api.ts`
- Explore browsers (entities, relationships, communities) use client-side pagination (50 items/page) with search/filter across full dataset. Each item is clickable for a detail modal. Relationships browser has type dropdown filter. Communities browser cleans up JSON artifacts in summaries for display.
- All API endpoints are in `main.py` (no separate router modules)

## Documentation & Maintenance Rules

- **Keep `documentation/` in sync**: When adding, modifying, or removing API endpoints, features, or configuration options, update the corresponding pages in `documentation/` (Zudoku-based docs site with pages in `documentation/pages/` and API specs in `documentation/apis/`).
- **Keep `README.md` in sync**: When making changes that affect the project overview, features, API endpoints, environment variables, architecture, or setup instructions, update `README.md` accordingly.
- **Keep this `CLAUDE.md` in sync**: When changes affect the architecture, key patterns, development commands, or deployment instructions documented here, update this file.

## Deployment

- **Coolify**: Use `coolify/docker-compose.coolify.yml`. Important: services with `SERVICE_FQDN_*` must have `traefik.docker.network=coolify` label and join the external `coolify` network to avoid 504 timeouts.
- **Standalone Docker**: `docker-compose.prod.yml` with Nginx reverse proxy
