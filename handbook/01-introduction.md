# Chapter 1: Introduction

## What is Cortex?

Cortex is the managed, API-first Knowledge Graph System at the heart of MOCA's (The Museum of Crypto Art) Agent as a Service platform. It transforms your documents into intelligent, queryable knowledge — the persistent memory layer for AI agents, applications, and teams.

**Cortex** is an open-source agentic knowledge base that ingests documents, extracts entities and relationships via LLM-assisted workflows, and builds a traversable knowledge graph stored in Neo4j. This graph is exposed through a comprehensive REST API with 100+ endpoints, ready to power Q&A interfaces, enrich your agents' understanding, or serve as the long-term memory backbone for your entire AI stack.

Cortex is designed for a world where AI evolves at breakneck speed and agent frameworks rise and fall overnight. Your knowledge shouldn't be locked into any single system. The beauty of Cortex is that your data isn't trapped — when a hot new agent framework drops next month, just connect your existing knowledge graph to the new system. **Your agents' memories become portable.**

## The Memory Hierarchy

Think of the memory hierarchy in your AI systems:

| Layer | Role | Persistence | Example |
|-------|------|-------------|---------|
| **Context Window** | Short-term memory | Conversation-scoped | Current chat messages |
| **Agent Memory Stack** | Mid-term memory | Session-scoped | In-flight task state |
| **Cortex Library** | Long-term memory | Permanent | Survives crashes, redeployments, and framework migrations |

Cortex Library sits at the center of your setup. Curate your base knowledge in the default collection, continuously push short-term learnings into specialized buckets, and let the system rebuild the graph to propagate updated knowledge across all your agents and applications. Every agent — whether prompted or autonomously executing — can selectively pull knowledge from available collections to better serve itself and your users.

## The Cortex Ecosystem

Cortex is more than the Library alone. It is an expanding ecosystem of products and integrations:

### Cortex Library (This Handbook)

The knowledge graph engine. Ingest, connect, and query everything your team knows. The Library handles:

- **Document ingestion** — Upload PDFs, Word docs, Markdown, text files, Excel, PowerPoint, HTML, images, and more
- **Knowledge graph construction** — Automatically extract entities, relationships, and concepts using LLM-powered GraphRAG
- **Hybrid search** — Vector similarity, keyword matching, and graph traversal unified with cross-encoder re-ranking
- **AI-powered Q&A** — Natural language questions with accurate, cited answers via an agentic researcher/writer pipeline
- **Community detection** — Automatic topic clustering with LLM-generated summaries
- **Collection management** — Organize knowledge by team, project, or use case with scoped access control

### Integrations

Connectors that extend the Library's capabilities today:

- **Git Connector** — Sync GitHub, GitLab, or Gitea repositories (docs, wikis, code) into the knowledge graph with incremental updates
- **Web Import** — Harvest websites into clean Markdown and ingest them, with link discovery for multi-page imports
- **Agent Skills** — Teach the researcher new abilities (including outbound `http_request` calls to external APIs) via the AgentSkills standard
- **REST API** — Build custom importers and integrations against the full API (see [Integrations](16-integrations.md) for Slack, Notion, and other DIY patterns)

A curated app marketplace (one-click YouTube, Slack, and Notion importers) is on the roadmap.

### Cortex API

100+ REST endpoints with full OpenAPI documentation. Build anything on top of Cortex — from chatbots and copilots to internal tools and customer-facing applications.

### MOCA Community Library

For Cortex Launch the MOCA Community is able to aggregate documents into the official museum library to ensure their legacy is being included in the system that can be queried by humans and agents.

## Deployment Options

Cortex is available in two forms:

| Option | Best For | Features |
|--------|----------|----------|
| **Cortex Cloud** (Managed) | Teams who want zero-ops | Hosted by MOCA, automatic updates, priority support, dedicated single-tenant available |
| **Self-Hosted** (Open Source) | Teams who need full control | Deploy on your own infrastructure, full source code access, community support |

This handbook covers both perspectives. Cloud users will find the end-user chapters (6-14) most relevant. Self-hosted administrators should start with chapters 3-5 for deployment and configuration.

## Architecture at a Glance

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│                     │     │                     │     │                     │
│   Next.js 16 UI     │────▶│   FastAPI Backend    │────▶│      Neo4j 5.x      │
│   React 19          │     │   Haystack 2.0       │     │   Graph + Vector     │
│   TypeScript        │     │   Python 3.11+       │     │   Full-Text Search   │
│   Tailwind CSS      │     │                     │     │                     │
│   Port :3000        │     │   Port :8000         │     │   Port :7687         │
│                     │     │                     │     │                     │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
         ▲                           │                           │
         │                           ▼                           │
    End Users               ┌─────────────────┐                 │
    & Agents                │   LLM Provider   │                 │
                            │   (OpenAI, etc.) │◀────────────────┘
                            └─────────────────┘       Embeddings
```

### Component Breakdown

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Frontend** | Next.js 16, React 19, TypeScript, Tailwind CSS, Framer Motion | Document management, graph visualization, Q&A interface, admin dashboard |
| **Backend** | FastAPI, Haystack 2.0, Python 3.11+, Pydantic | Document processing, embeddings, RAG pipelines, entity extraction, API |
| **Database** | Neo4j 5.x with APOC plugin | Graph storage, vector similarity search, full-text search, community detection |
| **Embeddings** | OpenAI / sentence-transformers | Convert text to semantic vectors for similarity search |
| **LLM** | Any OpenAI-compatible API | Entity extraction, relationship analysis, Q&A, summarization, research |
| **Vision** | OpenAI GPT-4o / Claude / LLaVA | Image analysis and OCR during document ingestion |

### Backend Service Architecture

The backend is organized as a monolithic FastAPI application with specialized service modules:

| Service | File | Responsibility |
|---------|------|---------------|
| **Neo4j Service** | `neo4j_service.py` | All graph database operations — entity storage, search, community detection, deduplication, cleanup |
| **Document Processor** | `document_processor.py` | Ingestion pipeline — Docling conversion, chunking, embedding, entity extraction, image analysis |
| **Graph Extractor** | `graph_extractor.py` | LLM-based entity and relationship extraction with XML parsing |
| **Researcher Agent** | `researcher_agent.py` | Agentic research pipeline — researcher loop with tool-calling, writer synthesis |
| **Vision Analyzer** | `vision_analyzer.py` | Image extraction and analysis with vision model integration |
| **Auth Service** | `auth_service.py` | Admin JWT authentication and API key validation |
| **Prompt Security** | `prompt_security.py` | Prompt injection detection and output filtering |
| **LLM Config** | `llm_config.py` | LLM provider configuration |

## Use Cases

### AI Agent Memory

Cortex serves as persistent, queryable memory for chatbots, copilots, and autonomous agents. Each agent can have its own collection, storing learnings and context that survive across sessions, restarts, and even framework migrations.

### Internal Knowledge Base

Transform scattered documents — policy manuals, onboarding guides, technical documentation — into instant, searchable answers. The knowledge graph discovers connections between documents that simple search would miss.

### Customer Support

Ground support responses in your actual documentation. Every answer comes with source citations, ensuring accuracy and reducing hallucination. The hybrid search finds relevant content even when customers use different terminology.

### Sales Enablement

Give sales teams instant access to product specs, competitive battlecards, and case studies. Collection-scoped queries let different teams access different knowledge.

### Research and Compliance

Navigate thousands of pages of regulatory documents, track obligations, and ensure compliance. Community detection automatically groups related regulations and requirements.

### Multi-Agent Orchestration

Use Cortex as a shared memory layer for agent swarms. Multiple agents read from and write to the same knowledge graph, building collective intelligence over time.

### Virtual Curators

Create AI personas with depth, personality, and domain expertise — powered by curated knowledge collections that give each persona a unique perspective and knowledge base.

## Who This Handbook Is For

This handbook serves two primary audiences:

**Administrators** who deploy and manage a self-hosted Cortex Library instance:
- Installation and deployment (Docker, Coolify, or bare metal)
- Environment configuration (LLM providers, embeddings, security)
- Performance tuning and scaling
- Backup, recovery, and maintenance
- Security hardening and API key management

**End Users** who interact with the Library through its web interface or API:
- Uploading and managing documents
- Building and exploring the knowledge graph
- Searching and asking questions
- Organizing knowledge into collections
- Integrating Cortex into applications and agent workflows

## What's Next

- **New to Cortex?** Continue to [Chapter 2: Core Concepts](02-core-concepts.md) for foundational knowledge
- **Ready to deploy?** Jump to [Chapter 3: Getting Started](03-getting-started.md) for installation
- **Ready to use?** Jump to [Chapter 6: The Web Interface](06-web-interface.md) for a UI walkthrough
- **Building an integration?** Jump to [Chapter 15: API Reference](15-api-reference.md)
