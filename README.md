# 🧠 MOCA Knowledge Base

A powerful knowledge base system powered by **Neo4j** graph database and **Haystack** AI framework. Upload documents, build a semantic knowledge graph, and query your data using natural language.

![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?style=flat&logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-008CC1?style=flat&logo=neo4j&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-000000?style=flat&logo=next.js&logoColor=white)

## ✨ Features

### Core Features
- **📁 Document Upload**: Support for PDF, TXT, Markdown, DOCX, and XLSX files
- **🔍 Hybrid Search**: Semantic + keyword search with Reciprocal Rank Fusion (RRF)
- **💬 AI Q&A**: Ask questions and get AI-generated answers with sources
- **🔗 Graph Storage**: Documents stored as interconnected nodes in Neo4j
- **⚡ Vector Search**: Fast similarity search using Neo4j's vector index
- **🎨 Modern UI**: Beautiful, responsive interface built with Next.js

### GraphRAG Features
- **🧠 GraphRAG**: LLM-powered entity and relationship extraction for knowledge graph construction
- **🔄 Hybrid Retrieval**: Combines vector similarity, keyword search, and graph traversal
- **🎯 Re-ranking**: Cross-encoder re-ranking for improved precision
- **💭 Conversation Memory**: Multi-turn conversations with context retention
- **🚀 Streaming Responses**: Real-time answer generation with SSE
- **🔬 Deep Research Mode**: Agentic multi-step RAG for complex questions

### R2R-Inspired Advanced Features (NEW)
- **🌐 Community Detection**: Automatic grouping of related entities using graph algorithms
- **📝 Graph Summarization**: LLM-generated summaries for entity communities
- **🔮 Extended Thinking**: Visible reasoning chains during agentic RAG (stream thinking)
- **📂 Collection-Level Graphs**: Organize documents into collections with scoped knowledge graphs
- **🎯 Semantic Entity Resolution**: Embedding-based entity deduplication for cleaner graphs

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
| Frontend | Next.js 14 + TypeScript | File upload, search, Q&A interface |
| Backend | FastAPI + Haystack | Document processing, embeddings, RAG |
| Database | Neo4j 5.x | Graph storage + vector similarity search |
| Embeddings | sentence-transformers | Convert text to semantic vectors |

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
OPENAI_API_KEY=sk-your-key-here  # Optional, for AI answers
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
| POST | `/api/upload` | Upload a document (triggers GraphRAG extraction) |
| GET | `/api/documents` | List all documents |
| GET | `/api/documents/{id}` | Get document details |
| DELETE | `/api/documents/{id}` | Delete a document |
| POST | `/api/search` | Semantic search |
| POST | `/api/ask` | Enhanced GraphRAG Q&A (hybrid search, reranking, agentic mode) |
| POST | `/api/ask/stream` | Streaming GraphRAG Q&A with SSE |

### GraphRAG Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/graph/status` | GraphRAG system status |
| GET | `/api/graph/visualization` | Get graph data for visualization |
| GET | `/api/graph/entities` | List entities in the knowledge graph |
| GET | `/api/graph/entity/{name}` | Get entity details and relationships |
| GET | `/api/graph/search` | Search entities by name |

### Collection Endpoints (NEW)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/collections` | List all collections |
| POST | `/api/collections` | Create a new collection |
| GET | `/api/collections/{id}` | Get collection details with stats |
| DELETE | `/api/collections/{id}` | Delete a collection |
| POST | `/api/collections/{id}/documents/{doc_id}` | Add document to collection |
| GET | `/api/collections/{id}/entities` | Get entities in collection's graph |

### Community Detection Endpoints (NEW)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/graph/communities` | List detected communities |
| POST | `/api/graph/communities/detect` | Run community detection algorithm |
| GET | `/api/graph/communities/{id}` | Get community details |
| POST | `/api/graph/communities/summarize` | Generate community summaries |
| GET | `/api/graph/communities/search` | Search communities by content |

### Extended Thinking Endpoints (NEW)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ask/stream/thinking` | Streaming RAG with visible reasoning |

### Example: Search

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
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

### Example: Streaming Response

```bash
curl -X POST http://localhost:8000/api/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the key points"}'
```

### Example: Extended Thinking Stream (NEW)

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

### Example: Create and Use Collections (NEW)

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
```

### Example: Community Detection (NEW)

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
   - `NEO4J_USER`
   - `NEO4J_PASSWORD`
   - `OPENAI_API_KEY` (optional)
   - `NEXT_PUBLIC_API_URL` (your domain)
5. Configure domain and SSL
6. Deploy!

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `NEO4J_URI` | Neo4j connection URI | Yes | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username | Yes | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | Yes | `password123` |
| `OPENAI_API_KEY` | OpenAI API key for AI answers & GraphRAG | **Yes for GraphRAG** | - |
| `OPENAI_API_BASE` | OpenAI API base URL (for proxies) | No | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | LLM model for generation | No | `openai/minimax-m21` |
| `UPLOAD_DIR` | Directory for uploaded files | No | `./uploads` |
| `EMBEDDING_MODEL` | Embedding model name | No | `openai/text-embedding-3-small` |
| `ENABLE_GRAPH_EXTRACTION` | Enable GraphRAG entity extraction | No | `true` |
| `MAX_GRAPH_HOPS` | Max hops for graph traversal | No | `2` |
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

#### Community Detection & Graph Summarization (NEW)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ENABLE_COMMUNITY_DETECTION` | Enable entity community detection | No | `true` |
| `MIN_COMMUNITY_SIZE` | Minimum entities for a valid community | No | `3` |
| `MAX_COMMUNITIES` | Maximum number of communities to track | No | `50` |
| `ENABLE_GRAPH_SUMMARIZATION` | Generate LLM summaries of communities | No | `true` |

#### Semantic Entity Resolution (NEW)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ENABLE_SEMANTIC_ENTITY_RESOLUTION` | Use embeddings for entity matching | No | `true` |
| `ENTITY_SIMILARITY_THRESHOLD` | Threshold for entity deduplication | No | `0.85` |

#### Collection-Level Graphs (NEW)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `ENABLE_COLLECTIONS` | Enable collection-based organization | No | `true` |
| `DEFAULT_COLLECTION` | Default collection name for documents | No | `default` |

#### Extended Thinking (NEW)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `STREAM_REASONING_STEPS` | Stream reasoning steps in agentic mode | No | `true` |
| `SHOW_RETRIEVAL_STATS` | Show retrieval statistics in responses | No | `true` |

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

When a document is uploaded, the following pipeline executes:

1. **Document Conversion** - Extract text from PDF/TXT/MD files
2. **Chunking** - Split into manageable chunks (default: 500 words)
3. **Embedding Generation** - Create vector embeddings for each chunk
4. **Entity Extraction** - LLM extracts entities (Person, Organization, Concept, etc.)
5. **Semantic Entity Resolution** - Match entities with similar embeddings to avoid duplicates
6. **Relationship Extraction** - LLM identifies relationships between entities
7. **Graph Storage** - Store chunks, entities, and relationships in Neo4j
8. **Collection Assignment** - Optionally add document to a collection scope

### Query Pipeline (Enhanced)

When you ask a question:

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

### Community Detection Pipeline (NEW)

The system can automatically detect communities of related entities:

1. **Graph Analysis** - Use Louvain algorithm (if Neo4j GDS available) or connected components
2. **Community Extraction** - Group entities that frequently co-occur or are connected
3. **Summary Generation** - LLM generates descriptive names and summaries for each community
4. **Context Enhancement** - Community summaries are used to enrich RAG answers

## 🛠️ Tech Stack

### Frontend
- **Next.js 14** - React framework with App Router
- **TypeScript** - Type safety
- **Tailwind CSS** - Styling
- **Framer Motion** - Animations
- **Lucide Icons** - Icon library

### Backend
- **FastAPI** - High-performance Python web framework
- **Haystack 2.0** - AI/NLP pipeline framework
- **sentence-transformers** - Text embedding models
- **neo4j-driver** - Official Neo4j Python driver
- **OpenAI** - GPT integration for RAG answers

### Database
- **Neo4j 5.x Enterprise** - Graph database with vector search
- **APOC** - Neo4j procedures library

## 📝 License

MIT License - feel free to use this project for any purpose.

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request.

---

Built with ❤️ using Neo4j + Haystack
