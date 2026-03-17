<div align="center">

![MOCA Library](frontend/public/banner.jpg)

# 🧠 MOCA Library

**The Agentic Knowledge Base for the AI Era**

![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?style=flat&logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-008CC1?style=flat&logo=neo4j&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-000000?style=flat&logo=next.js&logoColor=white)

</div>

## 🚀 What is MOCA Library?

In a world where AI evolves at breakneck speed and agent frameworks rise and fall overnight, your knowledge shouldn't be locked into any single system. **MOCA Library** is an agentic knowledge base that ingests your documents and analyzes their contents via LLM-assisted workflows, enabling bleeding-edge understanding of any content you throw at it.

The LLM-driven system automatically extracts entities and builds relationships between them, creating a **scalable knowledge graph** that grows smarter with every document. This graph is exposed via API, ready to be integrated into Q+A interfaces, enrich your agents' understanding, or serve as the long-term memory backbone for your entire AI stack.

### 💡 Why MOCA Library?

Think of the memory hierarchy in your AI systems:
- **Context** = Short-term memory
- **Agent Memory Stack** = Mid-term memory
- **MOCA Library** = Long-term memory (survives crashes, redeployments, and even framework migrations)

MOCA Library sits at the center of your setup. Curate your base knowledge in the default collection, continuously push short-term learnings into specialized buckets, and let the system rebuild the graph nightly to propagate updated knowledge across all your agents and apps. Every agent—whether prompted or autonomously executing—can selectively pull knowledge from available buckets to better serve itself and your users.

The beauty? Your data isn't trapped. When a hot new agent framework drops next month, just wait for an official plugin OR write a migration script and connect your existing knowledge graph to the new system. **Your agents' memories become portable.**

> **💡 Pro Tip:** Use our lightweight scraper kit [**mdharvest**](https://github.com/mocaOS/mdharvest) (built on Crawlee) to turn any URL into beautifully formatted Markdown files ready for ingestion.

## ✨ Features

### Core Features
- **📁 Document Upload**: Support for PDF, TXT, Markdown, DOCX, and XLSX files
- **✏️ Custom Inputs**: Manually add Q&A pairs, text, or markdown without file uploads
- **🔍 Hybrid Search**: Semantic + keyword search with Reciprocal Rank Fusion (RRF)
- **💬 AI Q&A**: Ask questions and get AI-generated answers with sources
- **🔗 Graph Storage**: Documents stored as interconnected nodes in Neo4j
- **⚡ Vector Search**: Fast similarity search using Neo4j's vector index
- **🎨 Modern UI**: Beautiful, responsive interface with unified navigation:
  - **Manage**: Upload, Documents, Collections, Entities, Relationships, Communities, Add
  - **Explore**: Knowledge Graph, Deep Research, Chat

### GraphRAG Features
- **🧠 GraphRAG**: LLM-powered entity and relationship extraction for knowledge graph construction
- **🔄 Hybrid Retrieval**: Combines vector similarity, keyword search, and graph traversal
- **🎯 Re-ranking**: Cross-encoder re-ranking for improved precision
- **💭 Conversation Memory**: Multi-turn conversations with context retention
- **🚀 Streaming Responses**: Real-time answer generation with SSE
- **🔬 Deep Research Mode**: Agentic multi-step RAG for complex questions

### Advanced Features
- **🌐 Community Detection**: Automatic grouping of related entities using graph algorithms
- **📝 Graph Summarization**: LLM-generated summaries for entity communities
- **🔮 Extended Thinking**: Visible reasoning chains during agentic RAG (stream thinking)
- **📂 Collection-Level Graphs**: Organize documents into collections with scoped knowledge graphs
- **🎯 Semantic Entity Resolution**: Embedding-based entity deduplication for cleaner graphs

### Security & Performance Features
- **🛡️ Prompt Security**: Protection against prompt injection attacks with configurable detection
- **🚀 Turbo Mode**: GPU-accelerated inference with Compute3 for faster processing
- **📦 Bulk Upload**: Upload hundreds of files with batch processing and progress tracking
- **📊 Background Tasks**: Long-running operations with real-time progress polling
- **🧹 Smart Cleanup**: Automatic task cancellation and complete graph cleanup on document deletion

## 🏗️ Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│   Next.js UI    │────▶│  FastAPI +      │────▶│     Neo4j       │
│   (TypeScript)  │     │  Haystack       │     │   (Graph + Vec) │
│                 │     │  (Python)       │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Components

| Component | Technology | Purpose |
|-----------|------------|---------|
| Frontend | Next.js 15 + React 19 + TypeScript | Document management, graph exploration, Q&A interface |
| Backend | FastAPI + Haystack 2.0 | Document processing, embeddings, RAG |
| Database | Neo4j 5.x | Graph storage + vector similarity search |
| Embeddings | OpenAI / sentence-transformers | Convert text to semantic vectors |

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 20+ (for local development)
- Python 3.11+ (for local development)

### Development Mode

1. **Clone and setup environment**

```bash
git clone <your-repo>
cd moca-neo4j-haystack

# Copy environment template
cp .env.example .env
```

2. **Configure environment variables**

Edit `.env` with your settings:

```env
NEO4J_USER=neo4j
NEO4J_PASSWORD=password123
OPENAI_API_KEY=sk-your-key-here  # Required for AI answers

# Admin Authentication
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=your-secure-password
ADMIN_API_KEY=moca_admin_your-secret-key
SESSION_SECRET=at-least-32-characters-secret
```

3. **Start with Docker Compose**

```bash
docker compose up -d
```

4. **Access the application**

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Neo4j Browser: http://localhost:7474

### Local Development (without Docker)

**Backend:**

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Start Neo4j (via Docker)
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  -e NEO4J_ACCEPT_LICENSE_AGREEMENT=yes \
  neo4j:5.15.0-enterprise

# Run the API
uvicorn app.main:app --reload --port 8000
```

**Frontend:**

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

## 📖 API Endpoints

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/stats` | Knowledge base statistics (includes entity/relationship counts) |
| POST | `/api/upload` | Upload a document (supports `start_processing` and `collection_id` params) |
| GET | `/api/documents` | List all documents |
| GET | `/api/documents/{id}` | Get document details |
| GET | `/api/documents/{id}/content` | Get document with full chunk content |
| DELETE | `/api/documents/{id}` | Delete a document (cancels processing, cleans up graph) |
| POST | `/api/documents/delete` | Bulk delete multiple documents (cancels all processing) |
| DELETE | `/api/documents` | Delete ALL documents (cancels all tasks, cleans entire graph) |
| POST | `/api/search` | Semantic search |
| POST | `/api/ask` | Enhanced GraphRAG Q&A (hybrid search, reranking, agentic mode) |
| POST | `/api/ask/stream` | Streaming GraphRAG Q&A with SSE |

### Custom Input Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/custom-input` | Create a custom input (Q&A, text, or markdown) |
| POST | `/api/custom-input/generate-topic` | Generate a topic/title hint from content using LLM |
| GET | `/api/custom-inputs` | List all custom inputs |
| GET | `/api/custom-inputs/{id}` | Get custom input details |

### Bulk Upload & Batch Processing Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/documents/pending` | List documents waiting to be processed |
| POST | `/api/documents/process-pending` | Start batch processing of pending documents |
| POST | `/api/documents/{id}/reprocess` | Reprocess a single document |
| POST | `/api/documents/reprocess` | Bulk reprocess multiple documents |
| POST | `/api/documents/move` | Move documents to a different collection |
| POST | `/api/cleanup/orphaned-entities` | Clean up orphaned entities and communities from graph |

### Background Task Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tasks` | List all background tasks |
| GET | `/api/tasks/{id}` | Get task status and progress |
| GET | `/api/tasks/{id}/result` | Get completed task results |
| DELETE | `/api/tasks/{id}` | Cancel/remove a task |
| POST | `/api/tasks/cleanup` | Remove old completed tasks |

### GraphRAG Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/graph/status` | GraphRAG system status |
| GET | `/api/graph/visualization` | Get graph data for visualization (supports `limit`, `include_neighbors`) |
| GET | `/api/graph/entities` | List entities in the knowledge graph |
| GET | `/api/graph/entity/{name}` | Get entity details and relationships |
| GET | `/api/graph/entity/{name}/relationships` | Get entity relationships up to N hops |
| POST | `/api/graph/subgraph` | Get subgraph for specific entities |
| GET | `/api/graph/search` | Search entities by name |

### Collection Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/collections` | List all collections |
| POST | `/api/collections` | Create a new collection |
| GET | `/api/collections/{id}` | Get collection details with stats |
| DELETE | `/api/collections/{id}` | Delete a collection |
| POST | `/api/collections/{id}/documents/{doc_id}` | Add document to collection |
| GET | `/api/collections/{id}/entities` | Get entities in collection's graph |

### Relationship Analysis Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/graph/relationships/analyze` | Analyze cross-document relationships (Phase B) |

### Community Detection Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/graph/communities` | List detected communities |
| POST | `/api/graph/communities/detect` | Run community detection algorithm |
| GET | `/api/graph/communities/{id}` | Get community details |
| DELETE | `/api/graph/communities/{id}` | Delete a specific community (unlinks entities) |
| DELETE | `/api/graph/communities` | Delete ALL communities (unlinks entities) |
| POST | `/api/graph/communities/summarize` | Generate community summaries |
| GET | `/api/graph/communities/search` | Search communities by content |

### Extended Thinking Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ask/stream/thinking` | Streaming RAG with visible reasoning |

### Turbo Mode Endpoints (Compute3)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/turbo/status` | Get Turbo Mode availability and GPU job status |
| GET | `/api/turbo/balance` | Get Compute3 account balance |
| POST | `/api/turbo/start` | Start a GPU job for accelerated inference |
| POST | `/api/turbo/stop` | Stop an active GPU job |
| POST | `/api/turbo/extend` | Extend runtime of an active GPU job |
| GET | `/api/turbo/jobs` | List all GPU jobs |
| GET | `/api/turbo/jobs/{id}` | Get details of a specific job |
| GET | `/api/turbo/jobs/{id}/logs` | Get logs from a GPU job |

### Admin Endpoints (API Key Management)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/admin/api-keys` | List all API keys | Admin |
| POST | `/api/admin/api-keys` | Create new API key | Admin |
| GET | `/api/admin/api-keys/{id}` | Get API key details | Admin |
| PATCH | `/api/admin/api-keys/{id}` | Update API key | Admin |
| DELETE | `/api/admin/api-keys/{id}` | Delete API key | Admin |
| POST | `/api/admin/api-keys/{id}/revoke` | Revoke API key | Admin |
| POST | `/api/admin/api-keys/{id}/activate` | Reactivate API key | Admin |

> **Authentication**: All endpoints except `/health` require an `X-API-Key` header. The admin API key has full access. Generated API keys can have `read` (Ask AI, search) or `manage` (upload, delete) permissions.

### Example: Search

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"query": "What is machine learning?", "top_k": 5}'
```

### Example: GraphRAG Ask

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Explain the main concepts from the documents",
    "use_graph": true,
    "max_hops": 2,
    "use_reranking": true,
    "use_agentic": false
  }'
```

### Example: Deep Research Mode

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Compare the different approaches and their trade-offs",
    "use_agentic": true,
    "conversation_history": [
      {"role": "user", "content": "What is machine learning?"},
      {"role": "assistant", "content": "Machine learning is..."}
    ]
  }'
```

### Example: Collection-Scoped Ask

Scope your question to a specific collection so only its documents, chunks, and entities are searched:

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Summarize the key findings",
    "collection_id": "research-papers",
    "use_graph": true,
    "use_reranking": true
  }'
```

Collection scoping works with all modes — streaming, deep research, and fast search.

### Example: Streaming Response

```bash
curl -X POST http://localhost:8000/api/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the key points"}'
```

### Example: Fast Search Mode

Optimized for speed with simple vector search (no hybrid/reranking):

```bash
curl -X POST http://localhost:8000/api/ask/stream \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the main topic?",
    "use_fast_search": true
  }'
```

### Example: Extended Thinking Stream

Stream the agent's reasoning process in real-time:

```bash
curl -X POST http://localhost:8000/api/ask/stream/thinking \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the relationships between the main concepts?",
    "use_agentic": true
  }'
```

Response events:
```json
{"thinking": "Analyzing question complexity..."}
{"thinking": "Identified 2 research areas"}
{"sub_questions": ["What are the main concepts?", "How are they related?"]}
{"thinking": "Searching knowledge graph communities..."}
{"thinking": "Researching (1/2): What are the main concepts?..."}
{"retrieval": "Found 5 sources for sub-question 1"}
{"sources": [...]}
{"graph_context": {"entities": [...], "communities": [...]}}
{"content": "Based on the analysis..."}
{"done": true, "communities_used": [1, 3]}
```

### Example: Create and Use Collections

```bash
# Create a collection
curl -X POST http://localhost:8000/api/collections \
  -H "Content-Type: application/json" \
  -d '{"name": "Research Papers", "description": "ML research papers"}'

# Upload document to collection
curl -X POST "http://localhost:8000/api/upload?collection_id=<collection-id>" \
  -F "file=@paper.pdf"

# Get collection entities
curl http://localhost:8000/api/collections/<collection-id>/entities

# Ask AI scoped to this collection
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the main findings?",
    "collection_id": "<collection-id>"
  }'
```

### Example: Community Detection

```bash
# Detect communities in the knowledge graph
curl -X POST "http://localhost:8000/api/graph/communities/detect?min_size=3"

# Generate summaries for communities
curl -X POST http://localhost:8000/api/graph/communities/summarize \
  -H "Content-Type: application/json" \
  -d '{"force_regenerate": false}'

# Search communities
curl "http://localhost:8000/api/graph/communities/search?query=machine+learning"
```

### Example: Bulk Upload (100+ files)

For large uploads, disable immediate processing and batch process later:

```bash
# Upload files without processing
for file in ./documents/*.pdf; do
  curl -X POST "http://localhost:8000/api/upload?start_processing=false" \
    -F "file=@$file"
done

# Start batch processing with concurrency control
curl -X POST "http://localhost:8000/api/documents/process-pending?concurrency=5"

# Poll for progress
curl http://localhost:8000/api/tasks/{task_id}
```

### Example: Create Custom Input

Add knowledge manually without uploading a file:

```bash
# Add a Q&A pair
curl -X POST http://localhost:8000/api/custom-input \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "qa",
    "content": "What is the capital of France?",
    "answer": "Paris is the capital of France.",
    "collection_id": "<collection-id>"
  }'

# Add freeform text or markdown
curl -X POST http://localhost:8000/api/custom-input \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "text",
    "content": "# Project Overview\n\nThis is a markdown document explaining...",
    "collection_id": "<collection-id>"
  }'
```

### Example: Turbo Mode (GPU-Accelerated Inference)

```bash
# Check Turbo Mode status
curl http://localhost:8000/api/turbo/status

# Start a GPU job
curl -X POST http://localhost:8000/api/turbo/start \
  -H "Content-Type: application/json" \
  -d '{"runtime_seconds": 3600}'

# Check balance
curl http://localhost:8000/api/turbo/balance
```

### Example: Get Graph Visualization

```bash
curl http://localhost:8000/api/graph/visualization?limit=100
```

## 🚢 Production Deployment

### Option 1: Docker Compose (Standalone)

```bash
# Build production images
docker compose -f docker-compose.prod.yml build

# Start services
docker compose -f docker-compose.prod.yml up -d
```

### Option 2: Coolify Deployment

Coolify is a self-hostable Heroku/Netlify alternative. See the [Coolify deployment guide](coolify/README.md).

**Quick steps:**

1. Create a new Docker Compose project in Coolify
2. Point to your git repository
3. Set compose file: `coolify/docker-compose.coolify.yml`
4. Add environment variables:
   - `OPENAI_API_KEY`
   - `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_API_KEY`, `SESSION_SECRET`
   - `BACKEND_URL`, `FRONTEND_URL` (your domains)
5. Configure domain and SSL
6. Deploy!

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `NEO4J_URI` | Neo4j connection URI | Yes | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username | Yes | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | Yes | `password123` |
| `OPENAI_API_KEY` | OpenAI API key for AI answers & GraphRAG | **Yes for GraphRAG** | - |
| `OPENAI_API_BASE` | OpenAI API base URL (for proxies/LiteLLM) | No | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | LLM model for generation | No | `openai/minimax-m21` |
| `UPLOAD_DIR` | Directory for uploaded files | No | `./uploads` |
| `CUSTOM_INPUTS_DIR` | Directory for custom input files | No | `./custom_inputs` |
| `MAX_FILE_SIZE_MB` | Maximum upload file size in MB | No | `50` |
| `EMBEDDING_MODEL` | Embedding model name | No | `openai/text-embedding-3-small` |
| `EMBEDDING_DIMENSION` | Embedding vector dimension | No | `1536` |
| `EMBEDDING_SEND_DIMENSIONS` | Send `dimensions` param to embedding API. Set `false` for models with fixed output dim (e.g. qwen3-vl-embedding-2b) | No | `true` |
| `USE_OPENAI_EMBEDDINGS` | Use OpenAI API for embeddings | No | `true` |
| `EMBEDDING_API_BASE` | API base URL for embeddings (defaults to `OPENAI_API_BASE`) | No | - |
| `EMBEDDING_API_KEY` | API key for embeddings (defaults to `OPENAI_API_KEY`) | No | - |
| `ENABLE_GRAPH_EXTRACTION` | Enable GraphRAG entity extraction | No | `true` |
| `GRAPH_EXTRACTION_MODEL` | Model for extraction (defaults to `OPENAI_MODEL`) | No | - |
| `GRAPH_EXTRACTION_API_BASE` | API base for extraction model (defaults to `OPENAI_API_BASE`) | No | - |
| `GRAPH_EXTRACTION_API_KEY` | API key for extraction model (defaults to `OPENAI_API_KEY`) | No | - |
| `MAX_GRAPH_HOPS` | Max hops for graph traversal | No | `2` |
| `CONCURRENT_EXTRACTIONS` | Chunks to process concurrently for extraction | No | `20` |
| `EXTRACTION_MAX_CONTEXT` | Max context window tokens for entity extraction batching | No | `32768` |
| `CHUNK_SIZE` | Words per chunk (if word mode) | No | `500` |
| `CHUNK_OVERLAP` | Overlap between chunks | No | `50` |
| `CHUNK_BY` | Chunking strategy: `word` or `sentence` | No | `sentence` |
| `SENTENCES_PER_CHUNK` | Sentences per chunk (if sentence mode) | No | `5` |
| `ENABLE_RERANKING` | Enable cross-encoder re-ranking | No | `true` |
| `RERANKING_MODEL` | Cross-encoder model for re-ranking | No | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `ENABLE_HYBRID_SEARCH` | Enable hybrid (vector + keyword) search | No | `true` |
| `VECTOR_WEIGHT` | Weight for vector search in RRF | No | `0.5` |
| `KEYWORD_WEIGHT` | Weight for keyword search in RRF | No | `0.3` |
| `GRAPH_WEIGHT` | Weight for graph context in RRF | No | `0.2` |
| `MAX_CONVERSATION_HISTORY` | Max messages in conversation context | No | `6` |
| `ENABLE_AGENTIC_RAG` | Enable multi-step agentic RAG | No | `true` |
| `MAX_AGENTIC_STEPS` | Maximum steps in agentic RAG | No | `3` |

#### Batch Processing

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `BATCH_PROCESSING_CONCURRENCY` | Documents to process concurrently in batch | No | `10` |
| `PROCESSING_THREAD_WORKERS` | Thread pool workers for CPU operations | No | `4` |

#### Relationship Analysis

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `RELATIONSHIP_MAX_CONTEXT` | Max INPUT context window tokens for relationship analysis batching | No | `65536` |
| `RELATIONSHIP_MAX_OUTPUT_TOKENS` | Max OUTPUT tokens for relationship analysis LLM responses | No | `8000` |
| `AUTO_RELATIONSHIP_ANALYSIS_AFTER_BATCH` | Auto-analyze after batch processing | No | `false` |
| `AUTO_COMMUNITY_DETECTION_AFTER_BATCH` | Auto-detect communities after analysis | No | `false` |

#### Community Detection & Graph Summarization

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ENABLE_COMMUNITY_DETECTION` | Enable entity community detection | No | `true` |
| `MIN_COMMUNITY_SIZE` | Minimum entities for a valid community | No | `3` |
| `MAX_COMMUNITIES` | Maximum number of communities to track | No | `50` |
| `ENABLE_GRAPH_SUMMARIZATION` | Generate LLM summaries of communities | No | `true` |

#### Semantic Entity Resolution

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ENABLE_SEMANTIC_ENTITY_RESOLUTION` | Use embeddings for entity matching | No | `true` |
| `ENTITY_SIMILARITY_THRESHOLD` | Threshold for entity deduplication | No | `0.85` |

#### Collection-Level Graphs

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ENABLE_COLLECTIONS` | Enable collection-based organization | No | `true` |
| `DEFAULT_COLLECTION` | Default collection name for documents | No | `default` |

#### Extended Thinking

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `STREAM_REASONING_STEPS` | Stream reasoning steps in agentic mode | No | `true` |
| `SHOW_RETRIEVAL_STATS` | Show retrieval statistics in responses | No | `true` |

#### Prompt Security

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `PROMPT_SECURITY` | Enable prompt injection detection and protection | No | `true` |

#### Admin Authentication

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ADMIN_EMAIL` | Admin login email for frontend | Yes | `admin@example.com` |
| `ADMIN_PASSWORD` | Admin login password | Yes | - |
| `ADMIN_API_KEY` | Admin API key for full backend access | Yes | - |
| `SESSION_SECRET` | JWT session encryption secret (min 32 chars) | Yes | - |
| `TRACK_ADMIN_API_KEY_USAGE` | Track usage analytics for admin API key | No | `false` |

#### Frontend Customization

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `NEXT_PUBLIC_API_URL` | Backend API URL | Yes | `http://localhost:8000` |
| `NEXT_PUBLIC_LOGO_URL` | Custom logo image URL | No | MOCA logo |
| `NEXT_PUBLIC_ACCENT_COLOR` | Custom accent color (any CSS color value) | No | MOCA theme |

#### Compute3 Turbo Mode

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `COMPUTE3_API_KEY` | Compute3 API key for GPU inference | No | - |
| `COMPUTE3_API_BASE` | Compute3 API base URL | No | `https://api.compute3.ai` |
| `COMPUTE3_GPU_TYPE` | GPU type to use (e.g., `h100`, `a100`) | No | `h100` |
| `COMPUTE3_GPU_COUNT` | Number of GPUs to allocate | No | `4` |
| `COMPUTE3_MODEL` | Model to run on GPU | No | `MiniMaxAI/MiniMax-M2.1` |
| `COMPUTE3_DOCKER_IMAGE` | vLLM Docker image for inference | No | `vllm/vllm-openai:latest` |
| `COMPUTE3_DEFAULT_RUNTIME` | Default GPU job runtime in seconds | No | `3600` |

## 🔧 Configuration

### Document Processing

Edit `backend/app/config.py` to customize:

```python
# Chunking settings
chunk_size: int = 500        # Words per chunk
chunk_overlap: int = 50      # Overlap between chunks

# Embedding model
embedding_model: str = "openai/text-embedding-3-small"
embedding_dimension: int = 1536

# File limits
max_file_size_mb: int = 50
allowed_extensions: list[str] = [".pdf", ".txt", ".md", ".docx", ".xlsx"]
```

### Supported File Types

| Type | Extension | Converter |
|------|-----------|-----------|
| PDF | `.pdf` | PyPDFToDocument |
| Text | `.txt` | TextFileToDocument |
| Markdown | `.md`, `.markdown` | MarkdownToDocument |
| Word | `.docx` | python-docx |
| Excel | `.xlsx` | openpyxl |

### Custom Input Types

In addition to file uploads, you can manually add knowledge:

| Type | Description |
|------|-------------|
| Q&A | Question-answer pairs that become searchable knowledge |
| Text | Freeform text content |
| Markdown | Formatted markdown documents |

Custom inputs are processed through the same GraphRAG pipeline as uploaded documents, including entity extraction and graph building.

## 🧪 Testing

```bash
# Backend tests
cd backend
pytest

# Frontend tests
cd frontend
npm test
```

## 📊 Neo4j Schema

The knowledge base uses this graph structure with GraphRAG entities:

```
(:Document {
  id: string,
  filename: string,
  file_type: string,
  file_size: int,
  upload_date: datetime,
  processing_status: string
})

(:Chunk {
  id: string,
  content: string,
  embedding: vector,
  chunk_index: int
})

(:Entity {
  name: string,          # Unique entity name
  type: string,          # Person, Organization, Concept, Technology, etc.
  description: string,   # Context-aware description
  created_at: datetime
})

# Relationships
(:Document)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:MENTIONS]->(:Entity)
(:Entity)-[:RELATED_TO {type: string, description: string}]->(:Entity)
```

### Indexes

Vector index for semantic search:
```cypher
CREATE VECTOR INDEX chunk_embedding
FOR (c:Chunk) ON c.embedding
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' }}
```

Full-text index for entity search:
```cypher
CREATE FULLTEXT INDEX entity_name_fulltext
FOR (e:Entity) ON EACH [e.name, e.description]
```

## 🧠 GraphRAG Pipeline

When a document is uploaded (or custom input is added), the following pipeline executes:

1. **Document Conversion** - Extract text from PDF/TXT/MD files (or use custom input content directly)
2. **Chunking** - Split into manageable chunks (default: 500 words). URLs are protected from splitting.
3. **Embedding Generation** - Create vector embeddings for each chunk
4. **Entity Extraction** - LLM extracts entities (Person, Organization, Concept, etc.)
5. **Semantic Entity Resolution** - Match entities with similar embeddings to avoid duplicates
6. **Relationship Extraction** - LLM identifies relationships between entities
7. **Graph Storage** - Store chunks, entities, and relationships in Neo4j
8. **Collection Assignment** - Optionally add document to a collection scope
9. **Filename Generation** - For custom inputs, LLM generates a descriptive filename

### Query Pipeline (Enhanced)

When you ask a question (optionally scoped to a specific collection via `collection_id`):

1. **Query Embedding** - Convert question to vector
2. **Entity Extraction** - Extract entity names from the question
3. **Community Search** - Find relevant entity communities
4. **Hybrid Search with RRF** - Combine three search methods:
   - Vector similarity search (semantic matching)
   - Full-text keyword search (exact term matching)
   - Graph traversal (relationship-based retrieval)
   - Reciprocal Rank Fusion combines rankings
5. **Cross-Encoder Re-ranking** - Re-score results for precision
6. **Context Assembly** - Combine results + graph context + community summaries
7. **LLM Generation** - Generate answer with conversation history

### Deep Research Mode (Agentic RAG) with Extended Thinking

For complex questions, enable Deep Research mode with visible reasoning:

1. **Question Decomposition** - Break into sub-questions (streamed as thinking events)
2. **Community Context** - Search relevant entity communities for background
3. **Iterative Retrieval** - Research each sub-question (progress streamed)
4. **Result Aggregation** - Merge and deduplicate findings
5. **Comprehensive Synthesis** - Generate detailed answer with community insights

### Community Detection Pipeline

The system can automatically detect communities of related entities:

1. **Graph Analysis** - Use Louvain algorithm (if Neo4j GDS available) or connected components
2. **Community Extraction** - Group entities that frequently co-occur or are connected
3. **Summary Generation** - LLM generates descriptive names and summaries for each community
4. **Context Enhancement** - Community summaries are used to enrich RAG answers

### Document Deletion & Cleanup

When documents are deleted, MOCA ensures complete cleanup of the knowledge graph:

1. **Task Cancellation** - Any active processing tasks for the document are stopped immediately
2. **Chunk Removal** - All text chunks associated with the document are deleted
3. **Orphaned Entity Cleanup** - Entities that were only mentioned by this document are removed
4. **Relationship Cleanup** - All relationships to deleted entities are automatically removed
5. **Community Cleanup** - Communities with no remaining members are deleted

This ensures your knowledge graph stays clean and free of orphaned data, even when users delete documents during processing.

**Response includes cleanup stats:**
```json
{
  "message": "Document deleted successfully",
  "processing_cancelled": true,
  "orphaned_entities_removed": 15,
  "orphaned_communities_removed": 2
}
```

## 🛡️ Prompt Security

The system includes protection against prompt injection attacks that attempt to:
- Extract or leak system prompts
- Bypass safety instructions
- Manipulate model behavior through encoded instructions

**Features:**
- Pattern-based detection of common injection techniques
- Input sanitization to neutralize malicious content
- Output filtering to prevent system prompt leakage
- Configurable strict mode (block) vs soft mode (sanitize)

Disable with `PROMPT_SECURITY=false` if not needed.

## 🚀 Turbo Mode (Compute3)

Turbo Mode enables GPU-accelerated inference using [Compute3](https://compute3.ai), providing faster response times for complex queries.

**How it works:**
1. Configure your Compute3 API key in `.env`
2. Start a GPU job from the Turbo page or via API
3. The system spins up a vLLM instance on dedicated GPUs
4. All LLM requests are routed through the GPU-accelerated endpoint
5. Stop the job when done to save costs

**Requirements:**
- Compute3 account with API key
- Sufficient balance for GPU rental

**Supported GPUs:**
- H100 (recommended for larger models)
- A100

The header shows a Turbo Mode status indicator when configured (green when ready, yellow when warming up). Turbo Mode settings are accessible from the Settings page.

## 🛠️ Tech Stack

### Frontend
- **Next.js 15** - React framework with App Router
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
- **Neo4j 5.15** - Graph database with vector search (Community or Enterprise)
- **APOC** - Neo4j procedures library

## 📝 License

MIT License - feel free to use this project for any purpose.

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request.

---

Built with ❤️ using Neo4j + Haystack
