# Scoped instruction map

Read the repository-root `CLAUDE.md` first if it has not reached your session.
Paths in the routing table are repository-relative. Links below are relative to
this guide. Read the affected domain guide before editing; these are explicit
read rules, not unconditional harness imports.

## Navigation Map

| File | Description |
|------|-------------|
| [`architecture.md`](architecture.md) | Tech stack, backend service map, frontend routes & components |
| [`environment.md`](environment.md) | Environment variables grouped by concern (DB, LLMs, features, skills, auth) |
| [`development.md`](development.md) | Docker/local dev commands, Neo4j setup, deployment (Coolify, Dokploy, standalone) |
| [`design-system.md`](design-system.md) | Design tokens, visual principles, `.impeccable.md` reference |
| [`maintenance.md`](maintenance.md) | Doc authority, validation and sync rules across all surfaces |
| [`frontend-patterns.md`](frontend-patterns.md) | Explore browsers, graph expansion, chat rendering, source modal, pagination |
| [`domain/document-pipeline.md`](domain/document-pipeline.md) | Upload → Docling → chunking → embedding → extraction → image analysis |
| [`domain/relationships.md`](domain/relationships.md) | Per-chunk extraction, batch analysis (Phase 1/2), ERR, multi-round, batching |
| [`domain/entities.md`](domain/entities.md) | Fuzzy resolution, dedup (rapidfuzz), merging, editing, search, type normalization |
| [`domain/communities.md`](domain/communities.md) | Leiden/Louvain detection, summarization, staleness tracking |
| [`domain/knowledge-graph-ui.md`](domain/knowledge-graph-ui.md) | 3-step pipeline page, staleness, regeneration flow, image awareness |
| [`domain/rag-pipeline.md`](domain/rag-pipeline.md) | Researcher/writer agents, tools, speed/quality modes, hybrid search, sessions/MCP |
| [`domain/skills.md`](domain/skills.md) | AgentSkills standard, auto-activation, http_request, config wizard |
| [`domain/admin-features.md`](domain/admin-features.md) | System reset, library import/export, bulk download, API key management |
| [`domain/git-integration.md`](domain/git-integration.md) | Git providers, incremental sync, provenance, write tool, polling |
| [`domain/web-crawl.md`](domain/web-crawl.md) | MDHarvest/crawl4ai client, Web Import endpoints/UI, multi-tenant privacy |
| [`domain/x402.md`](domain/x402.md) | Pay-per-query, monetized keys, settle-before-serve, facilitator, earnings |
| [`domain/apps.md`](domain/apps.md) | App hosting, minted keys, sandbox/proxy, grants, task and storage owners |
| [`domain/observability.md`](domain/observability.md) | Langfuse/OpenAI factory, trace grouping, vision/embedding records, GlitchTip/Sentry |
| [`bench.md`](bench.md) | LLM-stack bench, model registry, backup, heuristics. **Internal; keep changes scoped.** |
| [`qa.md`](qa.md) | Test commands, isolation boundaries, contract records, live E2E, QA inventory |
| [`regeneration.md`](regeneration.md) | Cross-repo capability inventory, contracts, evidence and next slice |
| [`upgrade-recovery.md`](upgrade-recovery.md) | State owners, backup coverage, compatibility and disposable restore gate |

## File-Path Routing

When editing files in these paths, read the corresponding `.claude/` file(s):

| Source path | Read |
|---|---|
| `backend/app/main.py` | `architecture.md` + relevant `domain/*.md` for the endpoint area |
| `backend/app/config.py`, `.env*` | `environment.md` |
| `backend/app/models.py` | `architecture.md` |
| `backend/app/services/document_processor.py`, `docling_worker.py`, `anydoc_converter.py`, `vision_analyzer.py` | `domain/document-pipeline.md`, `domain/observability.md` (LLM/embedding/vision tracing) |
| `backend/app/services/observability.py`, `error_tracking.py` | `domain/observability.md` |
| `frontend/src/instrumentation*.ts`, `frontend/sentry.*.config.ts`, `frontend/next.config.mjs` | `domain/observability.md` (error tracking) |
| `backend/app/services/graph_extractor.py` | `domain/relationships.md`, `domain/entities.md` |
| `backend/app/services/neo4j_service.py` | `domain/entities.md`, `domain/communities.md`, `domain/relationships.md` |
| `backend/app/services/researcher_agent.py`, `research_prompts.py` | `domain/rag-pipeline.md`, `domain/skills.md`, `domain/git-integration.md`, `domain/observability.md` (trace grouping) |
| `backend/app/services/skill_service.py` | `domain/skills.md` |
| `backend/app/services/git_connector_service.py`, `git_providers/**` | `domain/git-integration.md` |
| `backend/app/services/crawl_client.py` | `domain/web-crawl.md` |
| `backend/app/services/x402_service.py` | `domain/x402.md`, `domain/admin-features.md` (API key management) |
| `backend/app/services/app_service.py`, `app_task_service.py`, `app_task_dsl.py`, `app_storage_service.py`, `app_registry_service.py` | `domain/apps.md`, `domain/admin-features.md` (API key management) |
| `frontend/src/app/apps/**`, `components/admin/Apps*.tsx`, `components/admin/AppConfigModal.tsx`, `components/admin/AppGrantsModal.tsx` | `domain/apps.md`, `frontend-patterns.md` |
| `backend/app/services/llm_config.py` | `environment.md`, `domain/relationships.md`, `domain/observability.md` (OpenAI client factory) |
| `backend/app/services/library_transfer_service.py` | `domain/admin-features.md` |
| `backend/app/services/webhook_service.py` | `domain/document-pipeline.md` (ingestion status & webhooks) |
| `backend/app/services/remote_mcp.py` | `architecture.md`, `domain/rag-pipeline.md` |
| `backend/app/services/session_service.py` | `domain/rag-pipeline.md` (server-side sessions) |
| `backend/app/services/auth_service.py`, `api_key_service.py`, `api_usage_service.py` | `domain/admin-features.md`, `domain/x402.md` (monetized keys) |
| `backend/app/services/prompt_security.py` | `architecture.md` |
| `frontend/src/app/extract/**` | `domain/knowledge-graph-ui.md` |
| `frontend/src/app/documents/**`, `components/documents/**`, `components/upload/**` | `domain/document-pipeline.md`, `frontend-patterns.md`, `domain/web-crawl.md` (Web Import modal) |
| `frontend/src/app/deduplicate/**` | `domain/entities.md` |
| `frontend/src/app/explore/**`, `components/explore/**` | `frontend-patterns.md`, `domain/entities.md` |
| `frontend/src/app/ask/**`, `components/ask/**` | `domain/rag-pipeline.md`, `frontend-patterns.md` |
| `frontend/src/app/admin/**`, `components/admin/**` | `domain/admin-features.md`, `domain/skills.md`, `domain/git-integration.md` |
| `frontend/src/app/collections/**`, `components/collections/**` | `frontend-patterns.md` |
| `frontend/src/app/add/**` | `domain/document-pipeline.md` |
| `frontend/src/components/layout/**` | `architecture.md`, `frontend-patterns.md` |
| `frontend/src/lib/**` | `architecture.md` |
| `design-system/**` | `design-system.md` |
| `documentation/**`, `handbook/**` | `maintenance.md` |
| `coolify/**`, `nginx/**`, `docker-compose*.yml` | `development.md` |
| `selfhost/**`, `scripts/build-stack-json.mjs`, `scripts/check-version-sync.mjs`, installer changes (external repo `mocaOS/cortex-installer`) | `development.md` (self-host section), `environment.md` |
| `.github/workflows/release.yml` | `development.md` (self-host section) |
| `bench/**` | `bench.md` |
| `backend/tests/**`, `qa/**` | `qa.md` |
| `ops/backup/**`, persistence formats, migration/recovery changes | `upgrade-recovery.md`, `development.md` + owning domain guide |
| Agent instructions, cross-repo contracts or maintenance campaign | `regeneration.md`, `maintenance.md` |
| `.github/workflows/ci.yml`, documentation validation scripts | `qa.md`, `maintenance.md`, `development.md` |

## Priority

**Always read**: `architecture.md`; **read on demand**: the affected rows above.
For cross-repository work, load each repository's root instructions before edits.
