<div align="center">

![Cortex](frontend/public/banner.jpg)

# Cortex

**Institutional memory for the agentic era**

![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?style=flat&logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-008CC1?style=flat&logo=neo4j&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-000000?style=flat&logo=next.js&logoColor=white)

[**Documentation**](https://docs.cortex.eco) · [**Skills for Agents**](https://cortexskills.org) · [**Quickstart**](https://docs.cortex.eco/quickstart) · [**LLM Templates**](https://docs.cortex.eco/llm-templates)

</div>

## What is Cortex?

**Cortex** is institutional memory for the agentic era — a shared knowledge base that both humans and agents can read, write, and reason over. It ingests your documents and analyzes their contents via LLM-assisted workflows, automatically extracting entities and building the relationships between them into a **scalable knowledge graph** that grows smarter with every source you add.

Point it at one enthusiast's notes and it becomes their personal long-term memory. Point it at a company or a community and it becomes *collective* memory: every member — and every agent they run — can enrich their understanding from the shared knowledge of the cortexes they have access to. The more people and agents that draw on it, the more valuable that memory compounds. And because the whole graph is exposed over a clean REST API — framework-agnostic and portable by design — it plugs into Q+A interfaces, enriches your agents' understanding, and serves as the long-term memory backbone for your entire AI stack, no matter which model or harness comes next.

### Why Cortex?

Think of the memory hierarchy in your AI systems:
- **Context** = Short-term memory
- **Agent Memory Stack** = Mid-term memory
- **Cortex** = Long-term memory (survives crashes, redeployments, and even framework migrations)

Cortex sits at the center of your setup. Curate your base knowledge in the default collection, continuously push short-term learnings into specialized buckets, and let the system rebuild the graph nightly to propagate updated knowledge across all your agents and apps. Every agent—whether prompted or autonomously executing—can selectively pull knowledge from available buckets to better serve itself and your users.

The beauty? Your data isn't trapped. When a hot new agent framework drops next month, just wait for an official plugin OR write a migration script and connect your existing knowledge graph to the new system. **Your agents' memories become portable.**

> **💡 Pro Tip:** Use the built-in **Web Import** feature (*MDHarvest powered by Crawl4ai*) to turn any URL into beautifully formatted Markdown and ingest it straight into your graph — point Cortex at a [crawl4ai](https://github.com/unclecode/crawl4ai) service and paste or discover the links you want. See the [Web Import guide](handbook/23-web-import.md).

## The Cortex Ecosystem

This repository is the core of Cortex — the backend, knowledge graph pipeline, and management UI. A family of companion projects builds on its REST API:

| Repository | What it is |
|------------|-----------|
| [**cortex-chat**](https://github.com/mocaOS/cortex-chat) | Lean, multi-tenant chat frontend for end users. Email/password auth, user & group management with group-scoped collection access, streaming Ask AI + Deep Research, inline citations, document upload, and runtime branding — all on top of any Cortex instance via scoped API keys. |
| [**cortex-skills**](https://github.com/mocaOS/cortex-skills) | The knowledge layer between AI agents and Cortex. Curated `SKILL.md` files (served at [cortexskills.org](https://cortexskills.org)) that agents fetch via HTTP to get ground-truth knowledge about the Cortex API — so they build correct integrations on the first try instead of hallucinating endpoints. |
| [**cortex-explorer**](https://github.com/mocaOS/cortex-explorer) | Standalone, iframe-ready knowledge graph visualizer. 2D/3D force-directed graph with entity search, click-to-expand neighborhood traversal, spaceship-style 3D flight, and an accent-derived palette — a single static bundle pointed at any Cortex backend with a read-only key. |

## Features

### Core Features
- **📁 Document Upload**: Broad format support via Docling — PDF, EPUB, Office (Word/Excel/PowerPoint), HTML/XML, Markdown/text/LaTeX, images (OCR), and audio (ASR) — with source tracking for API integrations
- **✏️ Custom Inputs**: Manually add Q&A pairs, text, or markdown without file uploads
- **🌐 Web Import** (*MDHarvest powered by Crawl4ai*): Harvest web pages into clean markdown and ingest them into the graph. Paste URLs or **discover** the links on a page and pick which to pull. Cortex never embeds a browser — it calls a self-hosted or shared [crawl4ai](https://github.com/unclecode/crawl4ai) service over HTTP, so one crawler instance serves many deployments. Off by default (`ENABLE_WEB_CRAWL=true` + `CRAWL_SERVICE_URL`).
- **🔍 Hybrid Search**: Semantic + keyword search with Reciprocal Rank Fusion (RRF)
- **💬 AI Q&A**: Ask questions and get AI-generated answers with sources
- **🔗 Graph Storage**: Documents stored as interconnected nodes in Neo4j
- **⚡ Vector Search**: Fast similarity search using Neo4j's vector index
- **🎨 Modern UI**: Beautiful, responsive interface with unified navigation:
  - **Manage**: Documents, Knowledge Graph (one-click "Generate Graph" pipeline: entity extraction & relation discovery → cross-document deep relationship analysis → detect communities; "Regenerate Graph" deletes all communities, cross-document relations, and entities for a from-scratch rebuild while preserving per-chunk relations during Step 2 rebuild), Entity Deduplication, Collections, Add
  - **Explore**: Knowledge Graph, Entities, Relationships, Communities, Deep Research, Chat

### GraphRAG Features
- **🧠 GraphRAG**: LLM-powered entity extraction with per-chunk relationship extraction during ingestion (with retry and exponential backoff for rate limits, canonical name remapping, and self-referential filtering), plus cross-document deep relationship analysis — default `targeted` mode generates candidate pairs without the LLM (entity-embedding kNN + document co-mention) and verifies them in small batched LLM calls; legacy `llm_scan` mode runs the two-phase full-batch scan (candidate scanning with few-shot examples → confidence-scored XML extraction) — for knowledge graph construction. Stats endpoint returns `per_chunk_relationship_count` separately so the UI can distinguish Step 1 relations vs cross-document relations. Dedicated relationship model with separate rate limiting from entity extraction (fallback: relationship → extraction → primary).
- **🔄 Hybrid Retrieval**: Combines vector similarity, keyword search, and graph traversal
- **🎯 Re-ranking**: Cross-encoder re-ranking for improved precision
- **💭 Conversation Memory**: Multi-turn conversations with context retention
- **🚀 Streaming Responses**: Real-time answer generation with SSE
- **🔬 Deep Research Mode**: Agentic multi-step RAG for complex questions

### Advanced Features
- **🌐 Community Detection**: Automatic grouping of related entities using Leiden/Louvain algorithms with weight-aware, undirected graph projection and co-mention edges
- **📝 Community Summarization**: LLM-generated summaries for entity communities using the extraction model, with assistant prefill for reliable JSON output
- **🔮 Extended Thinking**: Visible reasoning chains during agentic RAG (stream thinking)
- **📂 Collection-Level Graphs**: Organize documents into collections with scoped knowledge graphs
- **🎯 Semantic Entity Resolution**: Embedding-based vector similarity deduplication (with Levenshtein 85% fallback) during entity extraction with alias tracking and proper document provenance tracking (`source_documents`, `extraction_count`) — catches semantic matches like "Massachusetts Institute of Technology" / "MIT" that string similarity misses
- **🔀 Entity Deduplication**: Post-extraction duplicate scanning using multi-strategy fuzzy matching (rapidfuzz) with Person-aware name gating (word-prefix validation prevents false matches on shared first names), entity-level deduplicate button in Explore for quick access, inspect modal for reviewing entity details before merging, LLM-generated combined descriptions, review-and-merge UI, inline entity search, and full merge history with audit trail
- **🔄 Targeted Relationship Discovery**: Default Step 2 engine (`RELATIONSHIP_DISCOVERY_MODE=targeted`) generates candidate entity pairs without the LLM — entity-embedding kNN over a Neo4j vector index (missing embeddings backfilled automatically) plus document co-mention — then verifies them in small batched LLM calls (~40 pairs/call), scaling efficiently on large graphs. Legacy `llm_scan` mode keeps the multi-round full-batch scan (up to `RELATIONSHIP_MAX_ROUNDS` rounds, stopping early at the target Entity-Relationship Ratio). Anti-hub protections in both modes: per-entity relationship cap (`RELATIONSHIP_MAX_PER_ENTITY`), candidate caps and doc-frequency hub guard (targeted), degree-aware batching and evidence-based prompts (legacy). Supports incremental (build on existing) and rebuild (delete cross-document relations, preserving per-chunk relations) modes.
- **📈 ERR Metric**: Entity-Relationship Ratio displayed on the Knowledge Graph page (2 decimal places) with color-coded health indicator
- **📊 Explore Browsers**: Entities, relationships, and communities browsers load all items for full-dataset search, with type filters and detail modals
- **⏱️ Progress Tracking**: Real-time batch progress with ETA for relationship analysis and community detection
- **📤 Library Import/Export**: Export your entire library (documents, knowledge graph, embeddings, communities) as a portable ZIP archive and import it into another instance — no need to re-run the expensive knowledge graph pipeline
- **🧩 Agent Skills**: Extend Deep Research and Chat with live API connections from the open [AgentSkills](https://agentskills.io/) ecosystem. Install skills from [skills.sh](https://skills.sh) or direct URLs — a setup wizard auto-detects required configuration (API tokens, etc.) and prompts you to provide them. Enabled skills are automatically activated at the start of every session. The researcher agent uses the built-in `http_request` tool to call external APIs described in skill instructions, with authentication injected server-side from stored configuration.
- **🔗 Git Integration**: Connect **GitHub, GitLab, and Gitea** repositories (including self-hosted) as a living knowledge source. Cortex ingests a repo's files and wiki into the knowledge graph and keeps them in sync **incrementally** via git history (added / modified / deleted / renamed), with a curated `.pdf`/`.md`-only default and custom glob filters. The whole connector is **off by default** — an admin turns it on with `ENABLE_GIT_INTEGRATION=true`, which enables ingestion *and* the agent capability. Each connection is then **read-only (ingest)** unless you grant **read/write**, in which case the research agent gains a `git_repo` tool that opens **pull requests** for your review (never a direct push). Per-connection access tokens, manual or scheduled sync.
- **💸 x402 Payments**: Monetize your knowledge base with pay-per-query **agentic micropayments** via the open [x402 standard](https://github.com/x402-foundation/x402). Free member keys keep working as before; in parallel, mint **monetized public keys** that agents pay per retrieval query in stablecoins (e.g. USDC on Base or Solana), with revenue flowing straight to a wallet you control — subsidize your members' inference and amortize your infra. **Two-tier pricing built in**: quick asks and search bill the key's flat rate, agentic deep research bills `price × multiplier` (default 10×, set per key with a live preview, quoted in the 402 challenge before an agent signs). Vendor-agnostic (any spec-compliant x402 facilitator), configured and **verified** entirely in the admin UI behind a single `X402_ENABLED=true` flag. Monetized keys are read-only, retrieval-endpoints-only, and collection-scopable — sell exactly the slice of knowledge you choose. See the [x402 Payments guide](documentation/pages/features/x402-payments.mdx).
- **📦 Apps**: Install self-contained web apps that run **inside** your instance — build them from the [Cortex App Template](https://github.com/mocaOS/cortex-app-template), package as a zip, and upload in **Settings → Apps**. Each app is served sandboxed under `/apps/{id}/` and reaches the Cortex API only through a proxy that enforces the app's declared endpoint allowlist and attaches a **dedicated minted key** (read or read-write, collection-scopable) — the browser never holds a real credential. **Platform apps** add server-side capabilities: `http` calls to external software (e.g. paperless-ngx) with secrets injected server-side and **no CORS setup on the target**, a quota-capped per-app `storage` store, an `llm` capability metered like any other completion, and `tasks` — declarative step-queues that run server-side, survive a closed tab, and can repeat on a schedule (a paperless app becomes an hourly sync daemon with no browser open). Mint revocable **share links** to let non-Cortex users open an app without a login. Off by default (`ENABLE_APPS=true`). Building an app? Point your coding agent at [cortexskills.org/builder](https://cortexskills.org/builder/SKILL.md). See the [Apps guide](documentation/pages/features/apps.mdx).

### Security & Performance Features
- **🛡️ Prompt Security**: Layered prompt-injection defense — a query-time ML classifier (**Prompt Guard** / PIGuard), 25+ pattern detectors, untrusted-content fencing, output filtering, and an experimental opt-in ingestion-time scan (`ENABLE_INGESTION_INJECTION_SCAN`, off by default). See the [Security guide](documentation/pages/guides/security.mdx).
- **🔐 Collection-Scoped API Keys**: Restrict API keys to specific collections — one instance, multiple isolated tenants. Both `read` and `read+write` keys support collection scoping. Restricted keys automatically receive filtered results across all endpoints — documents, collections, graph entities, relationships, communities, stats, and search — using the 4-hop `Collection→Document→Chunk→Entity` pattern. Out-of-scope single-resource requests return 403. New collections require explicit access grants.
- **📦 Bulk Upload**: Upload hundreds of files with batch processing and progress tracking
- **📥 Bulk Download**: Download selected documents as a ZIP archive (ZIP64, supports 1000+ files)
- **📊 Background Tasks**: Long-running operations with real-time progress polling
- **🧹 Smart Cleanup**: Automatic task cancellation and complete graph cleanup on document deletion
- **⚡ Efficiency Flags**: chunk-batched relationship extraction (÷~4 LLM calls), UNWIND-batched graph writes, and fulltext dedup prefiltering are default-on (each revertible per stack); Phase-B crash-resume checkpointing, unchanged-document reprocess skip, and provider prompt caching remain opt-in — all bench-validated (see the [configuration docs](documentation/pages/configuration.mdx) and `bench/BASELINE.md`)
- **🩺 Production Operations**: Prometheus `GET /metrics` (admin-protected, incl. disk-headroom gauges), optional JSON logs with `X-Request-ID` correlation, per-key rate limiting, request-body ceilings + free-disk guard (413/507 before memory or disk pressure), background-task state that survives restarts, an optional JSONL audit trail (`ENABLE_AUDIT_LOG`), graceful shutdown with SSE drain, per-service memory caps, a nightly backup sidecar (standalone overlay + built into the Coolify/Dokploy composes), and a **slim torch-free image variant** (`INSTALL_LOCAL_ML=false`) for stacks backed by the shared `cortex-helper`
- **🔭 LLM Observability (optional)**: point `LANGFUSE_*` at a self-hosted [Langfuse](https://langfuse.com) instance to trace every LLM/embedding/vision call (cost, tokens, latency, errors) and group agentic Q&A flows into one trace per request — Venice/OpenRouter included. Env-driven; no keys = no tracing, identical image. Prompt/completion content is **redacted by default** (privacy-first); set `LANGFUSE_LOG_EXTENDED=true` to log full text for debugging. See [`.claude/domain/observability.md`](.claude/domain/observability.md)

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│   Next.js UI    │────▶│  FastAPI +      │────▶│     Neo4j       │
│   (TypeScript)  │     │  Haystack       │     │   (Graph + Vec) │
│                 │     │  (Python)       │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

| Component | Technology | Purpose |
|-----------|------------|---------|
| Frontend | Next.js 16 + React 19 + TypeScript | Document management, graph exploration, Q&A interface |
| Backend | FastAPI + Haystack 2.0 | Document processing, embeddings, RAG |
| Database | Neo4j 5.26 | Graph storage + vector similarity search (4096-dim indexes supported) |
| Embeddings | OpenAI-compatible / sentence-transformers | Convert text to semantic vectors |

## Quick Start

> **🤖 Let your agent do it:** want Claude, Hermes, or any other agent to install and run Cortex for you? Send it **[cortexskills.org](https://cortexskills.org)** — the `setup` skill contains everything an agent needs to self-host a Cortex from scratch (autonomous install, health checks, troubleshooting) and the feature skills teach it to drive the API correctly afterwards.

### Prerequisites

- Docker & Docker Compose
- An LLM API key (any OpenAI-compatible provider — Venice, OpenRouter, OpenAI, self-hosted vLLM/Ollama)

### Setup

```bash
git clone https://github.com/mocaOS/cortex-app.git
cd cortex-app

# The recommended config — fill in the secrets block + your API key, done
cp .env.recommended .env
nano .env

docker compose up -d
```

`.env.recommended` ships the bench-validated model stack — **Gemma4 26B A4B** as the primary agent model, **Qwen3.6 27B** for knowledge-graph generation and vision, `text-embedding-3-small` embeddings — and leaves everything else on production-tuned code defaults. Set `ENCRYPTION_KEY` so git tokens and skill secrets are encrypted at rest (guidance is in the file). Every other knob is documented in the [Configuration Reference](https://docs.cortex.eco/configuration).

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| Neo4j Browser | http://localhost:7474 |

**Using another provider or your own GPUs?** The [LLM Deployment Templates](https://docs.cortex.eco/llm-templates) have a tested stack for Venice, OpenRouter, and self-hosted setups — including fallback model recommendations and hardware-specific concurrency tuning.

**Local development without Docker** (backend venv + `npm run dev`) is covered in the [Getting Started handbook chapter](handbook/03-getting-started.md).

## Documentation

| Resource | What you'll find |
|----------|------------------|
| [**docs.cortex.eco**](https://docs.cortex.eco) | The documentation site — [Quickstart](https://docs.cortex.eco/quickstart), [LLM Deployment Templates](https://docs.cortex.eco/llm-templates), [Configuration Reference](https://docs.cortex.eco/configuration) (all 160+ env vars), feature guides, and the interactive API reference |
| [`handbook/`](handbook/) | In-repo deep-dive chapters: getting started, configuration, web interface, deployment, every subsystem |
| [`BACKEND_API_DOCUMENTATION.md`](BACKEND_API_DOCUMENTATION.md) | Complete backend API documentation in one file |
| [`documentation/apis/openapi.yaml`](documentation/apis/openapi.yaml) | The authoritative OpenAPI contract (also served at `/docs` and `/redoc` in dev) |
| [**cortexskills.org**](https://cortexskills.org) | `SKILL.md` files for AI agents — ground-truth API knowledge for building integrations, self-hosting (`setup`), search, auth, MCP, and more |

## API

Everything the UI does is available over the REST API. All endpoints except `/health` require an `X-API-Key` header; generated keys carry `read` or `manage` permissions and can be **restricted to specific collections** — enabling multi-tenant deployments from a single instance.

```bash
# Semantic search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"query": "What is machine learning?", "top_k": 5}'

# GraphRAG Q&A (add "use_agentic": true for Deep Research mode)
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"question": "Explain the main concepts", "use_graph": true, "use_reranking": true}'
```

The surface covers documents & bulk upload, custom inputs, streaming Q&A (SSE, with visible reasoning), graph exploration & visualization, entity dedup & merging, relationship analysis, community detection, collections, background tasks, admin (API keys, usage stats, library import/export, system reset, skills), git integration, and web import. See the [API reference](https://docs.cortex.eco/api), [`BACKEND_API_DOCUMENTATION.md`](BACKEND_API_DOCUMENTATION.md), or the OpenAPI spec for the full contract with examples.

## Production Deployment

```bash
docker compose -f docker-compose.prod.yml up -d
```

- **Coolify**: point a Docker Compose project at `coolify/docker-compose.coolify.yml` — see the [Coolify guide](coolify/README.md)
- **Dokploy**: use `dokploy/docker-compose.dokploy.yml` — see [`dokploy/`](dokploy/)
- **Hardening**: set `ENVIRONMENT=production` (fails fast on weak secrets, disables interactive API docs), an explicit `CORS_ALLOWED_ORIGINS`, strong `NEO4J_PASSWORD`/`SESSION_SECRET`/`ENCRYPTION_KEY`, HTTPS via reverse proxy, and block public access to Neo4j ports (7474/7687). No TLS yet (e.g. LAN-only self-host)? Set `SESSION_COOKIE_SECURE=false` or browsers will silently drop the login cookie over plain HTTP
- **Backups**: the prod overlay and both PaaS composes include a nightly backup sidecar with verified server-side graph export, retention that never deletes the newest complete backup, a staleness healthcheck, and a tested `/restore.sh <timestamp>` runbook

The [Deployment guide](https://docs.cortex.eco/guides/deployment) covers all of this in depth.

## Supported File Types

All formats are converted through **Docling** (locally or via the shared `cortex-helper` service), which unifies them into structured Markdown before chunking and extraction:

| Type | Extensions |
|------|-----------|
| PDF | `.pdf` |
| E-books | `.epub` |
| Office | `.docx`, `.doc`, `.xlsx`, `.xls`, `.pptx`, `.ppt` |
| Web / markup | `.html`, `.htm`, `.xml` |
| Text | `.txt`, `.md`, `.mdx`, `.markdown`, `.rst`, `.tex`, `.latex` |
| Images (OCR) | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp` |
| Audio (ASR) | `.wav`, `.mp3`, `.webvtt`, `.vtt` |

Knowledge can also be added without files — Q&A pairs, freeform text, and markdown go through the same GraphRAG pipeline as uploads.

## Testing

The backend suite is fully hermetic — LLM, Neo4j, and the ML stack are mocked in `conftest.py`, so it runs with no external services. The system Python has no pytest; create a torch-free venv from the base requirements:

```bash
# Backend unit/contract suite
cd backend
python3 -m venv .qa-venv
.qa-venv/bin/pip install -r requirements-base.txt    # torch-free; includes pytest
.qa-venv/bin/python -m pytest -q
.qa-venv/bin/python -m ruff check --select E9,F63,F7,F82 app/ tests/   # CI lint gate

# Frontend gate (no test runner — type-check + lint)
cd frontend
npm ci
npx tsc --noEmit
npm run lint
```

**Live end-to-end journeys** (`backend/tests/test_live_e2e*.py`) run real HTTP requests against a running stack and auto-skip when none is reachable. Authenticated journeys read the key from `CORTEX_E2E_API_KEY` (never hard-coded):

```bash
CORTEX_E2E_API_KEY=<key> .qa-venv/bin/python -m pytest tests/test_live_e2e_authed.py
```

The canonical QA feature/defect inventory lives in [`qa/cortex_qa_master.ods`](qa/) with a written summary in [`qa/QA_REPORT.md`](qa/QA_REPORT.md); see [`.claude/qa.md`](.claude/qa.md) for the full harness reference.

## Tech Stack

### Frontend
- **Next.js 16** - React framework with App Router
- **React 19** - Latest React with improved performance
- **TypeScript 5** - Type safety
- **Tailwind CSS 3** - Styling
- **Framer Motion** - Animations
- **Lucide Icons** - Icon library
- **react-force-graph-2d** - Knowledge graph visualization

### Backend
- **FastAPI** - High-performance Python web framework
- **Haystack 2.0** - AI/NLP pipeline framework
- **sentence-transformers** - Text embedding models (fallback)
- **OpenAI** - Embeddings and LLM generation
- **neo4j-driver 5.x** - Official Neo4j Python driver
- **cross-encoder** - Re-ranking for improved precision

### Database
- **Neo4j 5.26** - Graph database with vector search (Community or Enterprise) — 4096-dim vector indexes supported
- **APOC** - Neo4j procedures library

## License

Licensed under the [Apache License, Version 2.0](LICENSE). You may use, modify, and distribute this project freely — including commercially. The license includes an explicit patent grant from all contributors.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

---

<a href="https://museumofcryptoart.com/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="frontend/public/brand/moca_logo_white.svg" />
    <img src="frontend/public/brand/moca_logo_black.svg" alt="MOCA — Museum of Crypto Art" height="37" />
  </picture>
</a>

Built by [MOCA](https://museumofcryptoart.com/) · Follow us on [Twitter](https://twitter.com/MuseumofCrypto/)
