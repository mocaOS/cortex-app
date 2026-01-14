# MOCA - Coolify Deployment

**Domain:** `https://kg.moca.qwellco.de`

## Quick Start

1. Create a **Docker Compose** resource in Coolify
2. Set compose path: `coolify/docker-compose.coolify.yml`
3. Paste environment variables (below)
4. Set domain `kg.moca.qwellco.de` → frontend service, port 3000
5. Deploy!

## Architecture

```
┌──────────────────────────────────────────────┐
│         Coolify / Traefik                    │
│        (SSL + Routing)                       │
│     kg.moca.qwellco.de                       │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│            Frontend (Next.js)                │
│              Port 3000                       │
│    Proxies /api/* → backend:8000             │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│           Backend (FastAPI)                  │
│             Port 8000                        │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│         Neo4j (Community)                    │
│           Port 7687                          │
└──────────────────────────────────────────────┘
```

## Environment Variables for Coolify

Copy this entire block into Coolify:

```env
# ==========================================================================
# MOCA - Coolify Environment Variables
# Domain: https://kg.moca.qwellco.de
# ==========================================================================

# -------------------------------------------------------------------------
# REQUIRED: OpenAI / LiteLLM Configuration
# -------------------------------------------------------------------------
OPENAI_API_KEY=sk-xwgMnWRwlJWtq75YIze7nQ
OPENAI_API_BASE=https://litellm.deploy.qwellco.de/v1
OPENAI_MODEL=openai/minimax-m21

# -------------------------------------------------------------------------
# REQUIRED: Neo4j Database Credentials
# -------------------------------------------------------------------------
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-secure-password-here

# -------------------------------------------------------------------------
# Embedding Configuration
# -------------------------------------------------------------------------
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMENSION=1536
USE_OPENAI_EMBEDDINGS=true

# -------------------------------------------------------------------------
# Upload Configuration
# -------------------------------------------------------------------------
MAX_FILE_SIZE_MB=50

# -------------------------------------------------------------------------
# Chunking Configuration
# -------------------------------------------------------------------------
CHUNK_SIZE=500
CHUNK_OVERLAP=50
CHUNK_BY=sentence
SENTENCES_PER_CHUNK=5

# -------------------------------------------------------------------------
# GraphRAG Configuration
# -------------------------------------------------------------------------
ENABLE_GRAPH_EXTRACTION=true
GRAPH_EXTRACTION_MODEL=
MAX_GRAPH_HOPS=2
CONCURRENT_EXTRACTIONS=20

# -------------------------------------------------------------------------
# Enhanced RAG Configuration
# -------------------------------------------------------------------------
ENABLE_RERANKING=true
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ENABLE_HYBRID_SEARCH=true
VECTOR_WEIGHT=0.5
KEYWORD_WEIGHT=0.3
GRAPH_WEIGHT=0.2
MAX_CONVERSATION_HISTORY=6
ENABLE_AGENTIC_RAG=true
MAX_AGENTIC_STEPS=3

# -------------------------------------------------------------------------
# Community Detection & Graph Summarization
# -------------------------------------------------------------------------
ENABLE_COMMUNITY_DETECTION=true
MIN_COMMUNITY_SIZE=3
MAX_COMMUNITIES=50
ENABLE_GRAPH_SUMMARIZATION=true
COMMUNITY_SUMMARY_MODEL=

# -------------------------------------------------------------------------
# Semantic Entity Resolution
# -------------------------------------------------------------------------
ENABLE_SEMANTIC_ENTITY_RESOLUTION=true
ENTITY_SIMILARITY_THRESHOLD=0.85
ENTITY_EMBEDDING_MODEL=

# -------------------------------------------------------------------------
# Collection-Level Graphs
# -------------------------------------------------------------------------
ENABLE_COLLECTIONS=true
DEFAULT_COLLECTION=default

# -------------------------------------------------------------------------
# Extended Thinking / Reasoning Visibility
# -------------------------------------------------------------------------
STREAM_REASONING_STEPS=true
SHOW_RETRIEVAL_STATS=true

# -------------------------------------------------------------------------
# Neo4j Memory Settings are hardcoded in docker-compose.coolify.yml
# Edit the compose file directly to adjust:
#   - NEO4J_server_memory_heap_initial__size=512m
#   - NEO4J_server_memory_heap_max__size=2G
#   - NEO4J_server_memory_pagecache_size=512m
# -------------------------------------------------------------------------
```

## Coolify Domain Configuration

In Coolify's domain settings, configure:

| Service | Port | Domain |
|---------|------|--------|
| frontend | 3000 | kg.moca.qwellco.de |

The frontend's Next.js rewrites will proxy `/api/*` requests to the backend internally.

## Volumes

| Volume | Purpose |
|--------|---------|
| `neo4j-data` | Database files |
| `neo4j-logs` | Log files |
| `neo4j-plugins` | APOC plugin |
| `uploads-data` | Uploaded documents |
