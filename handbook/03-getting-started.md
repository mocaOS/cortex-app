# Chapter 3: Getting Started (Administrators)

This chapter covers deploying your own Cortex Library instance. Whether you're setting up a development environment or a production deployment, this guide walks you through every step.

## Prerequisites

| Requirement | Development | Production |
|-------------|-------------|------------|
| **Docker & Docker Compose** | Required | Required |
| **RAM** | 4 GB minimum | 8 GB+ recommended |
| **LLM API Key** | Required | Required |
| **Domain Name** | Not needed | Required (for SSL) |
| **Node.js 20+** | Only for local dev (no Docker) | Not needed |
| **Python 3.11+** | Only for local dev (no Docker) | Not needed |

**LLM Provider**: You need an API key from OpenAI, Anthropic (via LiteLLM), Azure OpenAI, or any OpenAI-compatible API provider. The Library is LLM-agnostic — any provider that exposes an OpenAI-compatible `/v1/chat/completions` endpoint will work.

## Quick Start (5 Minutes with Docker)

### Step 1: Clone and Configure

```bash
git clone https://github.com/mocaOS/cortex-app.git
cd cortex-app
cp .env.example .env
```

> ⚡ **Recommended Stack Shortcut.** If you want the bench-validated 2-model stack (Gemma4 26B A4B primary + Qwen3.6 27B extraction), paste this block instead of building your LLM config tier-by-tier in Step 2 below. Everything else (relationship, vision, output budgets) inherits automatically. `OPENAI_MAX_CONTEXT` unlocks the primary's full input window; `GRAPH_EXTRACTION_MAX_CONTEXT` is deliberately *small* — extraction is decode-bound, so batches sized to the model's full window time out (see Chapter 4 § "Budget Fallback Chain").
>
> ```env
> # Primary — agentic Q&A / researcher (Gemma4 26B A4B: fast MoE, 256K context window)
> OPENAI_API_KEY=
> OPENAI_API_BASE=https://api.venice.ai/api/v1
> OPENAI_MODEL=google-gemma-4-26b-a4b-it
> OPENAI_MAX_CONTEXT=256000
>
> # Extraction — deliberately small context: extraction output scales with input, so
> # full-window batches can't decode inside the request window. 16000 completes reliably
> # (it's a graph-density/cost dial, NOT a match-the-model's-window setting).
> GRAPH_EXTRACTION_MODEL=qwen3-6-27b
> GRAPH_EXTRACTION_MAX_CONTEXT=16000
> EXTRACTION_MAX_OUTPUT_TOKENS=16000  # generous ceiling matched to context; terse prompt keeps dense docs under it
> # RELATIONSHIP_MAX_CONTEXT: leave unset (inherits 16000). Widening only helps legacy
> # llm_scan mode on fast-prefill hosted endpoints — see Chapter 4.
>
> # Vision — image analysis. VISION_MODEL must be set explicitly (the model name does NOT
> # inherit) — leave it empty and vision analysis is disabled (falls back to Docling's
> # built-in capabilities). Only api_base/api_key inherit from OPENAI_* when unset.
> VISION_MODEL=qwen3-6-27b
>
> # Embeddings — text-embedding-3-small (1536-dim)
> EMBEDDING_MODEL=text-embedding-3-small
> EMBEDDING_DIMENSION=1536
> EMBEDDING_MAX_INPUT_TOKENS=5400     # default — providers validate with their OWN tokenizer,
> # which can count 1.2–1.4× higher than the client's cl100k; 5400 stays safely under every
> # 8192-cap provider. Venice-only alternative: EMBEDDING_MODEL=text-embedding-qwen3-8b +
> # EMBEDDING_DIMENSION=4096 (Neo4j 5.26 supports up to 4096-dim vector indexes).
> ```
>
> You still need to set `NEO4J_PASSWORD` and the `ADMIN_*` / `SESSION_SECRET` block from Step 2 (those are infrastructure, not part of the LLM stack choice). Requires a provider hosting both models (OpenRouter, self-hosted vLLM, etc.).

### Step 2: Set Required Environment Variables

Open `.env` in your editor and set these minimum required variables:

```env
# ── Database ────────────────────────────────────────────────────
NEO4J_PASSWORD=your-secure-neo4j-password

# ── Admin Authentication ────────────────────────────────────────
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=your-secure-admin-password
ADMIN_API_KEY=cortex_admin_your-secret-key-here
SESSION_SECRET=at-least-32-characters-long-random-secret

# ── Primary LLM ─────────────────────────────────────────────────
OPENAI_API_KEY=sk-your-openai-key
OPENAI_API_BASE=https://api.openai.com/v1   # point at whichever OpenAI-compatible provider serves your model
OPENAI_MODEL=google-gemma-4-26b-a4b-it
```

**Generating secure secrets:**

```bash
# Generate a secure admin API key
echo "cortex_admin_$(openssl rand -hex 32)"

# Generate a session secret
openssl rand -hex 32
```

### Step 3: Start All Services

```bash
docker compose up -d
```

This starts three services:
- **Neo4j** (graph database) on port 7474 (browser) and 7687 (bolt)
- **Backend** (FastAPI) on port 8000
- **Frontend** (Next.js) on port 3000

> **After any `.env` change**, use `docker compose up -d --force-recreate backend` (not `docker compose restart backend`). Restart only restarts the process — it does NOT re-read the `env_file:`. See [Troubleshooting → Env Var Changes Don't Take Effect](19-troubleshooting.md#env-var-changes-dont-take-effect-after-docker-compose-restart) for details.

### Step 4: Verify the Deployment

```bash
# Check backend health
curl http://localhost:8000/health
# Expected: {"status": "healthy", "neo4j_connected": true, "version": "1.0.0"}

# Open the web interface
open http://localhost:3000
```

Sign in with the `ADMIN_EMAIL` and `ADMIN_PASSWORD` you configured.

### Step 5: Upload Your First Document

1. Click the **Upload** button on the Documents page
2. Drag and drop a PDF, Word doc, or text file
3. Watch the processing progress inline
4. Navigate to **Manage > Knowledge Graph** to build the graph
5. Click **Generate Graph** to run the full pipeline

Congratulations — you have a working Cortex Library instance!

## Deployment Options

### Option 1: Docker Compose — Development

Best for local development and testing. Includes hot reload for both frontend and backend.

```bash
docker compose up --build
```

**What's included:**
- Neo4j Community Edition (5.26) with APOC plugin — 4096-dim vector indexes supported
- Backend with live code mounting (changes reflect immediately)
- Frontend with live code mounting and `.next` cache
- Shared volumes for uploads and custom inputs

**Service URLs:**
| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| API Docs (ReDoc) | http://localhost:8000/redoc |
| Neo4j Browser | http://localhost:7474 |

### Option 2: Docker Compose — Production

Best for standalone production deployments. Includes Nginx reverse proxy with SSL support and optimized builds.

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

**What's included:**
- Neo4j Enterprise Edition (5.26) with optimized memory settings (512MB initial heap, 2GB max, 512MB page cache)
- Production-optimized Docker builds (multi-stage, smaller images)
- Nginx Alpine reverse proxy on ports 80/443
- SSL/TLS support (place certificates in `nginx/ssl/`)
- Named Docker volumes for persistent data
- Restart policies (`unless-stopped`)
- Health checks on all services

**Production environment variables:**

```env
# All variables from Step 2, plus:
NEXT_PUBLIC_API_URL=https://api.yourdomain.com

# Neo4j memory tuning
NEO4J_server_memory_heap_initial__size=512m
NEO4J_server_memory_heap_max__size=2G
NEO4J_server_memory_pagecache_size=512m
```

**Nginx configuration essentials:**
- `client_max_body_size 50M` — Matches `MAX_FILE_SIZE_MB` for uploads
- `proxy_buffering off` — Required for SSE streaming endpoints (`/api/ask/stream`)
- SSL termination with your domain certificates

### Option 3: Coolify (PaaS)

Best for teams already using Coolify as their self-hosting platform. Provides automatic domain routing, SSL certificates, and managed deployments.

**Setup steps:**

1. **Create a new resource** in your Coolify dashboard
2. **Select Docker Compose** as the project type
3. **Connect your git repository** (or use the Cortex repo URL)
4. **Set the compose file path** to `coolify/docker-compose.coolify.yml`
5. **Configure environment variables** in the Coolify UI:

```env
# Required
OPENAI_API_KEY=sk-your-key
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=your-secure-password
ADMIN_API_KEY=cortex_admin_$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)

# Domain configuration
BACKEND_URL=https://api.cortex.yourdomain.com
FRONTEND_URL=https://cortex.yourdomain.com
```

6. **Configure domains** in Coolify's domain settings
7. **Deploy**

**Critical Coolify notes:**

| Issue | Solution |
|-------|----------|
| Neo4j startup failure | Do NOT set `NEO4J_USER` — Coolify manages the default user. Use `SERVICE_PASSWORD_NEO4J` for the password. |
| 504 Gateway Timeout | Ensure services with `SERVICE_FQDN_*` have the `traefik.docker.network=coolify` label and join the external `coolify` network |
| Password management | Use Coolify's auto-generated `SERVICE_PASSWORD_NEO4J` — do not hardcode |

**Coolify magic variables:**

| Variable | Purpose |
|----------|---------|
| `SERVICE_FQDN_FRONTEND_3000` | Auto-generated frontend domain |
| `SERVICE_FQDN_BACKEND_8000` | Auto-generated backend domain |
| `SERVICE_PASSWORD_NEO4J` | Auto-generated secure Neo4j password |
| `SERVICE_URL_API_8000` | Internal backend URL for frontend-to-backend communication |

### Option 4: Local Development (No Docker)

For developers who prefer running services natively.

**Backend:**

```bash
cd backend
python -m venv venv
source venv/bin/activate    # or venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev    # Dev server on :3000
```

**Neo4j** (still via Docker, as it requires the APOC plugin):

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  -e NEO4J_PLUGINS='["apoc"]' \
  -e NEO4J_dbms_security_procedures_unrestricted=apoc.* \
  neo4j:5.26-community
```

Set environment variables:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=password123
export OPENAI_API_KEY=sk-your-key
```

## LLM Provider Configuration

The Library is LLM-agnostic. Each capability can point to a different model or provider:

### Separate Model Configuration

```env
# ── Primary LLM (Q&A, research, chat) ─────────────────────────
# Recommended: Gemma4 26B A4B (google-gemma-4-26b-a4b-it) — blazing-fast MoE, ideal for retrieval
# (MiniMax M3 can give slightly better results but costs the system its snappiness — not a worthwhile tradeoff)
OPENAI_API_KEY=sk-your-key
OPENAI_API_BASE=https://api.openai.com/v1   # point at whichever OpenAI-compatible provider serves these models
OPENAI_MODEL=google-gemma-4-26b-a4b-it
OPENAI_MODEL_FAST_MODE=google-gemma-4-26b-a4b-it    # Optional: faster model for "Fast Mode"

# ── Graph Extraction (entity discovery + community summarization) ─
# Recommended: Qwen3.6 27B — reasoning suppressed so it behaves like a fast instruct model (no overthinking)
# Defaults to Primary LLM if not set
GRAPH_EXTRACTION_MODEL=qwen3-6-27b     # Instruction-following model for extraction
GRAPH_EXTRACTION_API_BASE=https://api.openai.com/v1
GRAPH_EXTRACTION_API_KEY=sk-your-key

# ── Vision (image analysis during document ingestion) ──────────
# Defaults to Primary LLM if not set. Leave empty to use Docling's built-in.
VISION_MODEL=gpt-4o
VISION_MODEL_API_BASE=https://api.openai.com/v1
VISION_MODEL_API_KEY=sk-your-key

# ── Embeddings ─────────────────────────────────────────────────
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMENSION=1536
EMBEDDING_SEND_DIMENSIONS=true         # Set false for models with fixed output dim
EMBEDDING_API_BASE=https://api.openai.com/v1
EMBEDDING_API_KEY=sk-your-key
```

Community names and summaries run on the extraction model (`GRAPH_EXTRACTION_MODEL`) — no separate model knob.

### Using Alternative Providers

| Provider | `OPENAI_API_BASE` | Model Prefix | Notes |
|----------|-------------------|-------------|-------|
| **OpenAI** | `https://api.openai.com/v1` | None | Default; all models supported |
| **Anthropic** (via LiteLLM) | Your LiteLLM proxy URL | `anthropic/` | Requires LiteLLM proxy |
| **Azure OpenAI** | `https://your-resource.openai.azure.com/...` | None | Set API key to Azure key |
| **Ollama** (local) | `http://localhost:11434/v1` | None | Free; may lack function calling |
| **vLLM** (local GPU) | `http://localhost:8000/v1` | None | High performance, open models |
| **Together AI** | `https://api.together.xyz/v1` | None | Cloud-hosted open models |
| **Groq** | `https://api.groq.com/openai/v1` | None | Fast inference |

**Important**: The agent research pipeline requires a model that supports **function calling / tool use** (OpenAI `tools` parameter). Models like GPT-4o, GPT-4o-mini, Claude (via LiteLLM), and Mistral Large support this. Many local models do not — set `ENABLE_AGENT_RESEARCH=false` to use the legacy pipeline.

### Context Window Configuration

Match the primary tier to the model's actual context window. The ingestion tiers are the exception — keep them deliberately small:

```env
# Primary budgets — sub-tiers (extraction / relationship / vision) inherit
# unless overridden. See Chapter 4 § "Budget Fallback Chain" for the full diagram.
OPENAI_MAX_CONTEXT=256000              # Floor — all input context budgets inherit
OPENAI_MAX_OUTPUT_TOKENS=8000          # Floor — all output budgets inherit

# Per-tier overrides
GRAPH_EXTRACTION_MAX_CONTEXT=16000     # Recommended. 0 = inherit min(OPENAI_MAX_CONTEXT, 48000) — inherited value is clamped at 48K
EXTRACTION_MAX_OUTPUT_TOKENS=16000     # Recommended: generous ceiling matched to the context (terse prompt bounds per-entity output)
# RELATIONSHIP_MAX_CONTEXT: leave unset (inherits the extraction budget) — see below
RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000  # Phase 2 batch (standalone, NOT in chain)
```

Setting these values higher than the model's actual context window will cause errors. But don't chase the model's full window for extraction: extraction is decode-bound — output scales with input (the model re-emits every entity/relation), so at real provider decode speeds (~70 tok/s) full-window batches can't finish inside the request window (timeouts, retries, silently lost entities). Treat `GRAPH_EXTRACTION_MAX_CONTEXT=16000` as a graph-density/cost dial, and set `EXTRACTION_MAX_OUTPUT_TOKENS=16000` as a generous ceiling matched to it — the terse-description extraction prompt bounds output-per-entity so entity-dense documents stay under the cap (validated zero-truncation; the backend logs a one-shot "output budget looks too small" warning if overflows repeat). `RELATIONSHIP_MAX_CONTEXT` should stay unset: its bounded per-call output does not bound *prefill* time, so a full-window value that works on fast hosted endpoints produces multi-minute prompt reads and timeouts on self-hosted GPUs — and the default `targeted` discovery mode doesn't consume it for verification calls anyway.

Migration note: in earlier releases `RELATIONSHIP_MAX_OUTPUT_TOKENS` controlled Phase 2 batch (default 16000). It now feeds per-chunk + candidate scan in the inheritance chain, and the Phase 2 batch budget moved to `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS`. A stale `RELATIONSHIP_MAX_OUTPUT_TOKENS=16000` setting is harmless (per-chunk just gets unused headroom) — rename when convenient.

## Performance Tuning

### Concurrency Settings

```env
BATCH_PROCESSING_CONCURRENCY=2        # Documents processed in parallel (2 measured faster than 3 — see below)
CONCURRENT_EXTRACTIONS=3              # Entity extraction thread pool size
VISION_MAX_CONCURRENT=2               # Concurrent vision API calls (system-wide)
PARALLEL_RELATIONSHIP_BATCHES=2       # Relationship analysis batches in parallel
PROCESSING_THREAD_WORKERS=4           # Thread pool workers for CPU operations
```

**How they interact:**

```
Document Pipeline (controlled by BATCH_PROCESSING_CONCURRENCY):
  ├─ Document 1 ──────────────────────────────────────────────────
  │  ├─ Conversion (subprocess, semaphore=1)
  │  ├─ Chunking
  │  ├─ Embedding (thread pool)
  │  ├─ Entity Extraction (CONCURRENT_EXTRACTIONS threads)
  │  └─ Image Analysis (VISION_MAX_CONCURRENT semaphore, shared)
  │
  └─ Document 2 ──────────────────────────────────────────────────
     ├─ Conversion (waits for semaphore)
     ├─ Chunking
     ├─ Embedding (thread pool)
     ├─ Entity Extraction (CONCURRENT_EXTRACTIONS threads)
     └─ Image Analysis (shares VISION_MAX_CONCURRENT semaphore)
```

**Tuning guidance:**

| Setting | Increase When | Decrease When |
|---------|--------------|---------------|
| `BATCH_PROCESSING_CONCURRENCY` | Rarely — 2 measured *faster* than 3 for multi-doc builds (more in-flight docs drop per-call decode throughput ~70 → ~23 tok/s and multiply timeouts) | Limited RAM or hitting API rate limits |
| `CONCURRENT_EXTRACTIONS` | Entity extraction is the bottleneck | LLM API rate limits are being hit |
| `VISION_MAX_CONCURRENT` | Image-heavy documents need faster processing | Vision API rate limits are being hit |
| `PARALLEL_RELATIONSHIP_BATCHES` | Relationship analysis takes too long (**most impactful lever**) | LLM API costs need to be controlled |

### Memory Requirements

| Workload | Recommended RAM | Neo4j Heap | Neo4j Page Cache |
|----------|----------------|-----------|-----------------|
| Small (< 100 docs) | 4 GB | 512 MB | 256 MB |
| Medium (100-1000 docs) | 8 GB | 1 GB | 512 MB |
| Large (1000+ docs) | 16 GB+ | 2 GB | 1 GB |
| Enterprise (10000+ docs) | 32 GB+ | 4 GB | 4 GB |

## First Steps After Deployment

1. **Sign in** at http://localhost:3000 with your admin credentials
2. **Upload a test document** — Try a PDF or Markdown file
3. **Navigate to Knowledge Graph** — Click "Generate Graph" to run the full 3-step pipeline
4. **Explore the graph** — Go to Explore > Knowledge Graph to see your entities and relationships
5. **Ask a question** — Go to Explore > Chat or Deep Research
6. **Create an API key** — Go to Settings > API Key Management for programmatic access

## Common Setup Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Neo4j won't start | Password mismatch with existing data | Delete the `neo4j_data` volume: `docker volume rm library_neo4j_data` |
| Backend can't connect to Neo4j | Neo4j not ready yet | Backend waits for Neo4j health check; ensure `depends_on` is configured |
| LLM API errors during extraction | Invalid API key or wrong base URL | Verify `OPENAI_API_KEY` and `OPENAI_API_BASE` |
| Frontend shows "Network Error" | Backend URL misconfigured | Check `NEXT_PUBLIC_API_URL` points to the correct backend URL |
| Port 3000/8000 already in use | Another service on those ports | Stop the conflicting service or change ports in `docker-compose.yml` |

## What's Next

- **Configure all options** — Continue to [Chapter 4: Configuration Reference](04-configuration.md)
- **Secure your deployment** — Continue to [Chapter 5: Security and Authentication](05-security.md)
- **Start using the Library** — Jump to [Chapter 6: The Web Interface](06-web-interface.md)
