# CLAUDE.md

Cortex is an agentic knowledge base that ingests documents, extracts entities/relationships via LLMs, builds a Neo4j knowledge graph, and exposes it through a FastAPI REST API for RAG applications. Next.js 15 frontend for search, Q&A, graph exploration, and document management.

## Navigation Map

| File | Description |
|------|-------------|
| [`.claude/architecture.md`](.claude/architecture.md) | Tech stack, backend service map, frontend routes & components |
| [`.claude/environment.md`](.claude/environment.md) | All 40+ env vars grouped by concern (DB, LLMs, features, skills, auth) |
| [`.claude/development.md`](.claude/development.md) | Docker/local dev commands, Neo4j setup, deployment (Coolify, Dokploy, standalone) |
| [`.claude/design-system.md`](.claude/design-system.md) | Design tokens, visual principles, `.impeccable.md` reference |
| [`.claude/maintenance.md`](.claude/maintenance.md) | Doc sync rules for README, documentation/, handbook/, design-system/, .claude/ |
| [`.claude/frontend-patterns.md`](.claude/frontend-patterns.md) | Explore browsers, graph expansion, chat rendering, source modal, pagination |
| [`.claude/domain/document-pipeline.md`](.claude/domain/document-pipeline.md) | Upload → Docling → chunking → embedding → extraction → image analysis |
| [`.claude/domain/relationships.md`](.claude/domain/relationships.md) | Per-chunk extraction, batch analysis (Phase 1/2), ERR, multi-round, batching |
| [`.claude/domain/entities.md`](.claude/domain/entities.md) | Fuzzy resolution, dedup (rapidfuzz), merging, editing, search, type normalization |
| [`.claude/domain/communities.md`](.claude/domain/communities.md) | Leiden/Louvain detection, summarization, staleness tracking |
| [`.claude/domain/knowledge-graph-ui.md`](.claude/domain/knowledge-graph-ui.md) | 3-step pipeline page, staleness, regeneration flow, image awareness |
| [`.claude/domain/rag-pipeline.md`](.claude/domain/rag-pipeline.md) | Researcher/writer agents, tools, speed/quality modes, hybrid search |
| [`.claude/domain/skills.md`](.claude/domain/skills.md) | AgentSkills standard, auto-activation, http_request, config wizard |
| [`.claude/domain/admin-features.md`](.claude/domain/admin-features.md) | System reset, library import/export, bulk download, API key management |
| [`.claude/domain/git-integration.md`](.claude/domain/git-integration.md) | Git connector (GitHub/GitLab/Gitea): provider abstraction, incremental sync engine, document provenance, `git_repo` write tool, scheduled polling |
| [`.claude/bench.md`](.claude/bench.md) | Bench harness (`bench/`) — LLM-stack benchmark orchestrator, model registry, safety backup, heuristics. **Not yet publicly documented — keep changes scoped.** |
| [`.claude/qa.md`](.claude/qa.md) | QA & testing — backend pytest suite (`.qa-venv`, conftest fixtures, coverage map), live E2E harness (`test_live_e2e*.py`), canonical QA spreadsheet (`qa/`), defect log |

## File-Path Routing

When editing files in these paths, read the corresponding `.claude/` file(s):

| Source path | Read |
|---|---|
| `backend/app/main.py` | `architecture.md` + relevant `domain/*.md` for the endpoint area |
| `backend/app/config.py`, `.env*` | `environment.md` |
| `backend/app/models.py` | `architecture.md` |
| `backend/app/services/document_processor.py`, `docling_worker.py`, `vision_analyzer.py` | `domain/document-pipeline.md` |
| `backend/app/services/graph_extractor.py` | `domain/relationships.md`, `domain/entities.md` |
| `backend/app/services/neo4j_service.py` | `domain/entities.md`, `domain/communities.md`, `domain/relationships.md` |
| `backend/app/services/researcher_agent.py`, `research_prompts.py` | `domain/rag-pipeline.md`, `domain/skills.md`, `domain/git-integration.md` |
| `backend/app/services/skill_service.py` | `domain/skills.md` |
| `backend/app/services/git_connector_service.py`, `git_providers/**` | `domain/git-integration.md` |
| `backend/app/services/llm_config.py`, `compute3_service.py` | `environment.md`, `domain/relationships.md` |
| `backend/app/services/library_transfer_service.py` | `domain/admin-features.md` |
| `backend/app/services/auth_service.py`, `api_key_service.py`, `api_usage_service.py` | `domain/admin-features.md` |
| `backend/app/services/prompt_security.py` | `architecture.md` |
| `frontend/src/app/extract/**` | `domain/knowledge-graph-ui.md` |
| `frontend/src/app/documents/**`, `components/documents/**`, `components/upload/**` | `domain/document-pipeline.md`, `frontend-patterns.md` |
| `frontend/src/app/deduplicate/**` | `domain/entities.md` |
| `frontend/src/app/explore/**`, `components/explore/**` | `frontend-patterns.md`, `domain/entities.md` |
| `frontend/src/app/ask/**`, `components/ask/**` | `domain/rag-pipeline.md`, `frontend-patterns.md` |
| `frontend/src/app/admin/**`, `components/admin/**` | `domain/admin-features.md`, `domain/skills.md`, `domain/git-integration.md` |
| `frontend/src/app/collections/**`, `components/collections/**` | `frontend-patterns.md` |
| `frontend/src/app/add/**` | `domain/document-pipeline.md` |
| `frontend/src/app/turbo/**` | `environment.md` |
| `frontend/src/components/layout/**` | `architecture.md`, `frontend-patterns.md` |
| `frontend/src/lib/**` | `architecture.md` |
| `design-system/**` | `design-system.md` |
| `documentation/**`, `handbook/**` | `maintenance.md` |
| `coolify/**`, `nginx/**`, `docker-compose*.yml` | `development.md` |
| `bench/**` | `bench.md` |
| `backend/tests/**`, `qa/**` | `qa.md` |

## Priority

**Always read**: `architecture.md` (gives you the lay of the land for any task)
**Read on demand**: All other files, based on the routing table above

## Meta: Maintaining These Docs

When making significant changes, update the relevant `.claude/` subfile(s) per the routing table. If adding a new subfile, add it to the Navigation Map and File-Path Routing above. Keep subfiles 50–300 lines. See [`.claude/maintenance.md`](.claude/maintenance.md) for full sync rules across all documentation layers (README, documentation/, handbook/, design-system/).
