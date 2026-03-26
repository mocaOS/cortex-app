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
- `services/neo4j_service.py` — Graph DB operations, search, entity extraction, community detection, `delete_all_entities()` (DETACH DELETE all entities)
- `services/document_processor.py` — Ingestion pipeline: Docling conversion → chunking → embedding → entity extraction → graph storage
- `services/graph_extractor.py` — LLM-based entity/relationship extraction
- `services/compute3_service.py` — GPU-accelerated inference (Turbo Mode)
- `services/vision_analyzer.py` — Image analysis and OCR
- `services/auth_service.py` — Admin JWT auth
- `services/prompt_security.py` — Prompt injection detection
- `services/researcher_agent.py` — Agent-based research pipeline (researcher loop + writer streaming)
- `services/research_prompts.py` — Prompt templates and tool definitions for researcher/writer agents

**Frontend** (`frontend/src/`):
- Next.js App Router with unified navigation structure:
  - **Manage** section: Documents (`/documents`, default — "Generate Graph" button navigates to `/extract` instead of starting processing directly), Knowledge Graph (`/extract` — 3-step pipeline: entity extraction → relationship analysis → community detection with staleness tracking; "Generate Graph" button when no entities exist as primary CTA, "Regenerate Graph" button when entities exist runs full pipeline from scratch: first calls `deleteAllCommunities()` → `deleteAllRelationships()` → `deleteAllEntities()` to wipe all graph data, then reprocesses all documents via `api.reprocessDocuments` → relationship rebuild → community detection), Deduplicate (`/deduplicate` — entity deduplication with rapidfuzz similarity scanning, merge/dismiss flow, merge history), Collections, Add
  - **Explore** section: Knowledge Graph, Entities (read-only), Relationships (read-only), Communities (read-only), Deep Research, Chat (all tab-based on `/explore` with `?tab=graph|entities|relationships|communities|research|chat`)
  - **Settings** (`/admin`): Statistics dashboard, system configuration (LLM Configuration section with 4 sub-areas: Primary Model, Extraction Model, Vision Model, Embeddings — showing model names, API base URLs, context windows, and concurrency settings via `GET /api/admin/config`; no API keys exposed), API key management, danger zone (system reset). Stats bar hidden on this page.
  - `/` redirects to `/documents`
  - `/entities`, `/relationships`, `/communities` redirect to their Explore tabs
- `lib/api.ts` — API client with auth headers
- `lib/session.ts` — JWT session management
- `components/layout/` — Header (top nav with Manage/Explore), SubMenu (contextual tabs), StatsBar (4 KPI cards: Documents, Entities, Relations, Communities)
- `components/upload/UploadModal.tsx` — Upload modal (drag-and-drop + collection selector), closes immediately on file selection; upload progress shown inline in document list
- `components/documents/DocumentBulkActions.tsx` — Bulk action toolbar: Select All, Reprocess, Download (ZIP), Move to Collection, Delete. Download streams a ZIP archive of selected documents' original files via `POST /api/documents/download-zip` (ZIP64, handles 1000+ files).
- `components/documents/DocumentCard.tsx` — Document row with view button: `.md` files open in an in-app Markdown viewer modal; all other file types open in a new browser tab via `/api/documents/{id}/file` (browser decides to display or download)
- `components/explore/` — Paginated browsers for entities, relationships, communities (with search, type filters, detail modals) + KnowledgeGraph visualization (force-graph 2D, default 100 nodes) with dynamic graph expansion (clicking unloaded related entities fetches them + 1-hop neighbors + bridge subgraph, adds to canvas with edges, navigates and centers view) + DeduplicationView (entity merge/dedup with rapidfuzz similarity scanning, entity search to add to groups, merge/dismiss flow, merge history modal with search, community re-detection notice)
- `app/extract/page.tsx` — Knowledge Graph page: 3-step pipeline with status tracking, staleness detection via `SystemMeta` Neo4j nodes (`last_relationship_analysis_at`, `last_community_detection_at`, `last_entity_merge_at`), cascading blocked states, per-step Inspect buttons linking to Explore tabs. "Generate Graph" / "Regenerate Graph" button runs full pipeline. Regeneration deletes all communities, relationships, AND entities (`deleteAllCommunities()` → `deleteAllRelationships()` → `deleteAllEntities()`) before reprocessing documents. Flow persisted to `sessionStorage` with a `regenerateTaskId` for the active step's backend task. Resume logic on mount checks the saved task's status: running → resume polling, completed → advance to next step, failed → abort, not found → start fresh. Entity extraction has proper task polling with backend progress messages; running tasks detected on mount. Fresh instance warning on "Extract Entities" (0 entities) recommends "Generate Graph" instead. **Image analysis awareness**: Step 1 tracks documents with background image analysis in progress (completed text processing but `image_progress_current < image_progress_total`); these docs are shown in a separate "Analyzing Images" tile with an aggregate progress bar, Step 1 stays "In Progress" until all images are analyzed, and auto-refresh polls every 5 seconds to keep progress updated. Step 2/3 remain blocked until image analysis completes.
- `components/` — UI components organized by feature

**Document Processing Pipeline**: Upload (modal closes immediately, duplicate detection by filename+filesize, progress in document list) → Docling conversion → sentence/word chunking → OpenAI embeddings → LLM entity extraction with fuzzy entity resolution (Levenshtein 85% dedup, triggered via "Extract Entities" on Knowledge Graph page or "Generate Graph" button on Documents/Knowledge Graph page) → entity type normalization (10 allowed types, fuzzy matched) → fuzzy entity-to-chunk linking → Neo4j storage → **background image analysis** (runs asynchronously after text processing completes; images extracted during Docling conversion are analyzed concurrently via vision model, gated by a configurable semaphore — `VISION_MAX_CONCURRENT`, default 3; progress tracked per-document via `image_progress_current`/`image_progress_total`/`image_progress_message` properties; image chunks created with type `image_analysis` and `chunk_index` 1000+; graph extraction runs on image content if enabled) → (separate job via Knowledge Graph Step 2) two-phase relationship analysis: Phase 1 (extraction model) scans entity batches for candidate pairs, Phase 2 (extraction model) confirms and classifies with XML output. Co-occurrence-based batching via Union-Find clustering, 120 entities/batch, parallel execution, dynamic chunk context filling. Initial analysis runs up to 3 rounds; re-analyze does 1 round. ERR (Entity-Relationship Ratio) metric shown on Knowledge Graph page. → (Step 3) community detection (Leiden with Louvain fallback, weight-aware, co-mention edges for sparse graphs) → community summarization (assistant prefill for JSON output). Step 2 supports incremental mode (builds on existing) and rebuild mode (deletes all relationships first, triggers multi-round). Timestamps persisted in `SystemMeta` Neo4j nodes for staleness tracking.

**RAG Query Pipeline (Agent Architecture)**: Two-stage researcher/writer pipeline. Researcher agent uses OpenAI function-calling to iteratively gather information via tools: `knowledge_search` (hybrid RRF: vector 0.5 + fulltext 0.3 + graph 0.2, with cross-encoder reranking), `community_search`, `entity_lookup`, `reasoning` (quality mode only), `done`. Writer then synthesizes all gathered context into a streamed answer. Speed mode (chat): 2 iterations, knowledge_search + done. Quality mode (deep research): up to 10 iterations, all tools with reasoning transparency. Legacy fixed pipeline available as fallback via `ENABLE_AGENT_RESEARCH=false`.

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
- `ENABLE_AGENT_RESEARCH` (default: true), `ENABLE_AGENT_CHAT` (default: false) — agent-based research pipeline flags
- `RESEARCHER_MAX_ITERATIONS_SPEED` (default: 2), `RESEARCHER_MAX_ITERATIONS_QUALITY` (default: 10) — agent loop iteration caps
- `WRITER_MAX_TOKENS_SPEED` (default: 1200), `WRITER_MAX_TOKENS_QUALITY` (default: 4000) — writer output token limits
- `EXTRACTION_MAX_CONTEXT` (default: 32768), `RELATIONSHIP_MAX_CONTEXT` (default: 65536), `RELATIONSHIP_MAX_OUTPUT_TOKENS` (default: 16000) — context window and output budgets for graph extraction
- `PARALLEL_RELATIONSHIP_BATCHES` (default: 0) — number of relationship analysis batches to process in parallel (0 = use `CONCURRENT_EXTRACTIONS`)
- `RELATIONSHIP_TARGET_RATIO` (default: 1.0) — target relationships-per-entity ratio (ERR) for admin monitoring
- `RELATIONSHIP_MAX_ROUNDS` (default: 3) — max auto-discovery rounds for initial analysis (re-analyze always does 1 round). Stops early if target ratio reached.
- `RELATIONSHIP_MAX_HOURS` (default: 0) — max hours for relationship generation (0 = no time limit, completes all rounds)
- `VISION_MAX_CONCURRENT` (default: 3) — max concurrent vision API calls system-wide for image analysis (controls semaphore + thread pool sizing)
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SESSION_SECRET` — auth
- `CHUNK_SIZE`, `CHUNK_OVERLAP`, `CHUNK_BY` (word/sentence) — document processing

## Key Patterns

- Graph extraction uses `get_extraction_llm_config()` from `llm_config.py` (separate from Q&A model)
- Turbo mode overrides both extraction and main model configs
- Entity extraction is per-document (Phase A) with fuzzy resolution (`store_entity_with_resolution()`, Levenshtein 85%), entity type normalization (10 allowed types via `_normalize_entity_type()` with rapidfuzz fallback to Concept)
- Relationship analysis is per-collection (Phase B) with two-phase per-batch processing, both using the extraction model:
  - **Phase 1 — Candidate Scan**: Extraction model scans all entities in the batch + chunk context to identify candidate entity pairs that may be related. Output: simple `EntityA | EntityB` pairs. Uses `EXTRACTION_MAX_CONTEXT` token budget (larger context window for scanning).
  - **Phase 2 — Relationship Extraction**: Extraction model takes only the candidate pairs + their descriptions + chunk context and outputs structured XML relationships with type, description, weight. Uses `RELATIONSHIP_MAX_CONTEXT` token budget. Batches with 0 candidates skip Phase 2 entirely.
  - Both phases use the extraction model because it's instruction-following and produces clean structured output. The main model tends to over-reason and output plaintext instead of XML.
- Relationship batching: 120 entities/batch hard cap, 15% overlap, parallel execution (defaults to `CONCURRENT_EXTRACTIONS` threads, overridable via `PARALLEL_RELATIONSHIP_BATCHES`). Token budget split 60/40 between entities and chunk context (dynamic filling via `get_chunk_context_for_entities()` with token budget). Co-occurrence-based entity ordering via Union-Find clustering groups entities sharing chunks into the same batch. `get_entity_co_occurrence()` builds entity→chunk_id map, Union-Find clusters entities by shared chunks, output sorted largest-cluster-first. Scales to 100k+ entities in O(n * avg_chunks).
- Multi-round relationship discovery: Initial analysis (0 existing relationships) runs up to `RELATIONSHIP_MAX_ROUNDS` (default 3) rounds. Re-analyze (relationships already exist) always runs 1 round. Each round fetches ALL existing relationships (no limit) and passes them to the LLM to avoid rediscovery. Stops early if `RELATIONSHIP_TARGET_RATIO` reached or `RELATIONSHIP_MAX_HOURS` exhausted.
- Entity-Relationship Ratio (ERR): `entity_relationship_ratio` tracked in `GraphStatsResponse` and returned in analysis task results. Shown on Knowledge Graph page (Step 2) with color-coded indicator (green >= 0.69, yellow >= 0.29, red < 0.29) and tooltip explaining the metric. `RELATIONSHIP_TARGET_RATIO` (default 1.0) configurable.
- Relationship type constraint: prompt enforces standard types, `_extract_xml_relationships()` fuzzy-matches non-standard types to `DEFAULT_RELATION_TYPES` via rapidfuzz (80% threshold, fallback to RELATED_TO). Includes plaintext fallback parser for `EntityA --[TYPE]--> EntityB` arrow format when XML parsing finds no results.
- Relationship analysis supports `rebuild=true` mode (deletes all relationships before re-analysis, triggers multi-round) alongside default incremental mode
- Entity deduplication: `suggest_duplicate_entities()` fetches all entities and compares in Python using rapidfuzz (`ratio` for typos, `token_sort_ratio` for word reordering, `partial_ratio` with type-aware gating — restricted to same-type entities with length ratio >= 0.5, relaxed to 0.35 for Person type). Uses star clustering (not BFS) to prevent transitive chain explosions. Person-type entities sorted with priority. `merge_entities()` retargets all relationships and chunk MENTIONS to canonical, deduplicates relationships (same source+target+type keeps highest weight), adds aliases, merges source_documents, accepts LLM-generated `merged_description`, clears community_id (topology changed), then deletes merged nodes. `MergeHistory` nodes store merge audit trail (entity snapshots, stats). `SystemMeta` tracks `last_entity_merge_at` (also exposed in `GraphStatsResponse`). Endpoints: `GET /api/entities/duplicates`, `POST /api/entities/merge`, `GET /api/entities/merge-history`. Frontend: Deduplicate page (`/deduplicate`) under Manage section with scan/merge/dismiss flow, entity search (inline) to add entities to groups, merge history modal with search, community re-detection notice after merges. Dismissed groups stored in localStorage.
- **Dynamic graph expansion**: KnowledgeGraph visualization (default 100 nodes) supports clicking unloaded related entities in the EntityPanel to grow the graph. Expansion flow: `getEntityRelationships(target, 1, 50)` fetches the entity + 1-hop neighbors + edges; `getGraphSubgraph([selected, target], true)` fetches the bridge subgraph (all shared neighbors + edges between both entities' neighborhoods) in parallel. New nodes spawn near the selected entity; a `pendingNavigateRef` + `useEffect` on `graphData.nodes` handles navigation after React re-render; `d3ReheatSimulation()` wakes the force layout; a polling interval waits for x/y before calling `centerAt`/`zoom`. Expanded nodes/edges are stored in component state (`expandedNodes`/`expandedEdges`) and merged into `graphData` via `useMemo`, reset when props change. Geometric pointer events (`pointerdown`/`pointerup`) filter by `e.target.tagName === "CANVAS"` to avoid stealing clicks from the EntityPanel overlay.
- **Entity traversal constraint**: `traverse_from_entities()` has an `entity_paths_only` flag (default `False`). When `True`, adds `WHERE ALL(n IN nodes(path) WHERE n:Entity)` to the Cypher traversal, preventing paths through Chunk/Document nodes. The entity details endpoint (`/api/graph/entity/{name}`) uses `entity_paths_only=True` so the panel only shows entities reachable via Entity→Entity relationships (navigable on the graph). RAG callers leave it `False` for broader context retrieval. `get_entity_relationships()` also constrains to Entity-only paths.
- Entity search (`find_entities_by_name()`) uses fulltext index with wildcard prefix matching (e.g. "pol" finds "Polygon") via Lucene `*` suffix, sorted by connection count (highest first)
- Community detection: tries Leiden first (GDS), falls back to Louvain, then BFS. Uses relationship weights (`relationshipWeightProperty`), undirected projection (UNION both directions), and co-mention edges (entities sharing a chunk get implicit weight-2.0 edge). Old communities cleaned up before re-detection.
- Community summarization: assistant prefill `{"` forces JSON output, double-brace dedup, 5-strategy parsing fallback (direct parse, strip-to-first-brace, code fence, regex object, regex fields), fallback names from top entity names
- Knowledge Graph page guides users through the 3-step pipeline with staleness detection: pending docs → needs relationship re-analysis → needs community re-detection. Also detects staleness when entities have been merged since last community detection (`last_entity_merge_at` > `last_community_detection_at`). Steps cascade (Step 2/3 grey out when prior step needs update). Each step has an "Inspect" button linking to the relevant Explore tab. "Generate Graph" (no entities) / "Regenerate Graph" (entities exist) button runs full 3-step pipeline. Regeneration cleanup order: `deleteAllCommunities()` → `deleteAllRelationships()` → `deleteAllEntities()` → `reprocessDocuments()` → relationship analysis (rebuild) → community detection — a true from-scratch rebuild. Flow state persisted in `sessionStorage` with a `regenerateTaskId` for the active step's backend task; resume logic checks the saved task's status (running → resume polling, completed → advance, failed → abort, not found → start fresh), eliminating heuristic-based step-skipping.
- Knowledge Graph Step 1 is **image-analysis-aware**: documents with `processing_status === "completed"` but `image_progress_current < image_progress_total` are treated as still in-progress. These appear in a dedicated "Analyzing Images" summary tile and a blue progress banner with aggregate image count (X/Y images across N documents). Step 1 status remains "in_progress" until all image analysis completes, blocking Step 2/3. Auto-refresh polls every 5 seconds when image analysis is detected. The "Processed" count in the summary grid only includes `fullyCompletedDocs` (completed AND images done).
- Progress tracking: relationship analysis shows batch X/Y with ETA computed from observed batch duration. Entity extraction polls backend task status with progress messages; running tasks detected on mount. Community detection polls task status every 2 seconds. Image analysis progress polled via document data refresh every 5 seconds. Stats bar refreshes every 5 seconds.
- Chat/Research message rendering: research process blocks (Sub-Questions, Thinking Steps, Reasoning Steps) render above the main content bubble. Order: research process → content → graph context → sources. Research Process container auto-scrolls to bottom as new steps stream in.
- Source modal highlighting: cited chunk is highlighted within the full document text. Uses `indexOf()` to split into three parts: before (60% opacity), cited chunk (full opacity with 3px accent left border), after (60% opacity). Auto-scrolls to highlighted chunk on load.
- `SystemMeta` Neo4j nodes store `last_relationship_analysis_at`, `last_community_detection_at`, and `last_entity_merge_at` timestamps. Upload dates are naive (no timezone) — frontend appends `Z` for UTC comparison.
- Backend uses singleton service instances (Neo4jService, DocumentProcessor, etc.)
- Background tasks via FastAPI's `BackgroundTasks` for document processing
- Streaming responses for `/api/ask/stream` and `/api/ask/stream/thinking` endpoints
- Frontend uses `"use client"` directive for interactive components; API calls go through `lib/api.ts`
- Explore browsers (entities, relationships, communities) use server-side pagination with search and filtering. Backend endpoints (`/api/graph/entities`, `/api/graph/relationships`, `/api/graph/communities`) accept `skip`, `limit`, and `search` query params; entities and relationships also accept type filters (`entity_type`, `rel_type`). Dedicated `/api/graph/entity-types` and `/api/graph/relationship-types` endpoints return distinct types for filter dropdowns. Frontend uses 300ms debounced search, fetches only the current page (50 items for entities/relationships, 25 for communities), and shows subtle opacity transition during fetches. Each item is clickable for a detail modal. Communities browser cleans up JSON artifacts in summaries for display.
- All API endpoints are in `main.py` (no separate router modules)
- Bulk Download (`POST /api/documents/download-zip`): Accepts `{ "document_ids": [...] }`, fetches file paths via `get_documents_file_paths()` batch query, builds a ZIP64-enabled archive with duplicate filename disambiguation, and streams the response in 1MB chunks via `StreamingResponse`. Frontend triggers browser download via blob URL. No auth required (matches existing file endpoint). Accessible via Download button in bulk actions toolbar on Documents page.
- System Reset (`POST /api/admin/reset`): Admin-only endpoint with selective deletion options (documents, uploaded files, custom inputs, collections, API keys). When documents are deleted, also cleans up `MergeHistory` nodes (dedup audit trail), `SystemMeta` nodes (staleness timestamps), and frontend clears client-side cached data (`dedup_dismissed` and `moca_community_detection_task` from localStorage, `regenerateStep`/`regenerateStartedAt`/`regenerateTaskId` from sessionStorage). Accessible via Settings page → Danger Zone → System Reset modal with "DELETE" confirmation.

## Design System

The project has a portable design system at `design-system/moca-cortex/`:
- `MASTER.md` — Complete design spec: colors (OKLCh), typography (Inter + JetBrains Mono), spacing, glass morphism, animation tokens, component patterns, accessibility checklist, z-index scale, and anti-patterns. This is the source of truth for all visual decisions.
- `tokens.css` — Drop-in CSS file with all custom properties (light + dark themes), glass/glow/shimmer classes. Import this into any new project to reuse the design system.
- `tailwind.preset.ts` — Tailwind preset with all color/font/radius tokens. Use via `presets: [mocaPreset]` in other projects.
- `pages/*.md` — Page-specific overrides (dashboard, ask, explore, documents) that take precedence over MASTER.md for those pages.

Design context and principles are also documented in `.impeccable.md` at the repo root.

Key design characteristics: monochrome foundation with a single dynamic accent color (`oklch(0.79 0.18 70.67)`, configurable via `NEXT_PUBLIC_ACCENT_COLOR`), dark mode default, glass morphism surfaces (24px blur), Framer Motion animations, Lucide icons only.

## Documentation & Maintenance Rules

- **Keep `documentation/` in sync**: When adding, modifying, or removing API endpoints, features, or configuration options, update the corresponding pages in `documentation/` (Zudoku-based docs site with pages in `documentation/pages/` and API specs in `documentation/apis/`).
- **Keep `README.md` in sync**: When making changes that affect the project overview, features, API endpoints, environment variables, architecture, or setup instructions, update `README.md` accordingly.
- **Keep this `CLAUDE.md` in sync**: When changes affect the architecture, key patterns, development commands, or deployment instructions documented here, update this file.
- **Keep `design-system/` in sync**: When making global design changes (color tokens, typography, spacing scale, animation defaults, new component patterns, or glass morphism treatment), update `design-system/moca-cortex/MASTER.md`, `tokens.css`, and `tailwind.preset.ts` accordingly. For page-specific design changes, update or create the corresponding `design-system/moca-cortex/pages/<page>.md` override.

## Deployment

- **Coolify**: Use `coolify/docker-compose.coolify.yml`. Important: services with `SERVICE_FQDN_*` must have `traefik.docker.network=coolify` label and join the external `coolify` network to avoid 504 timeouts.
- **Standalone Docker**: `docker-compose.prod.yml` with Nginx reverse proxy
