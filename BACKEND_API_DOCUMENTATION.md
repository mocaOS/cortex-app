# Cortex Backend API Documentation

## Overview

Cortex (Neo4j + Haystack powered GraphRAG) is a knowledge base system that combines:
- **Document Processing**: PDF, TXT, MD, DOCX, XLSX file upload and processing
- **GraphRAG**: Entity and relationship extraction from documents using LLM
- **Semantic Search**: Vector embeddings with hybrid search (vector + keyword + graph)
- **Knowledge Graph**: Neo4j-based graph storage with community detection
- **Collections**: Organization of documents into collections with separate knowledge graphs
- **API Key Management**: Admin-controlled API key system with permissions

---

## Table of Contents

1. [API Endpoints](#api-endpoints)
2. [Request/Response Models](#requestresponse-models)
3. [Service Classes](#service-classes)
4. [Authentication](#authentication)
5. [Configuration](#configuration)

---

## API Endpoints

### Health & Status

#### `GET /health`
**Description**: Health check endpoint  
**Authentication**: None  
**Response**: `HealthResponse`
- `status`: "healthy" | "degraded"
- `neo4j_connected`: bool
- `version`: str

#### `GET /api/stats`
**Description**: Get knowledge base and knowledge graph statistics  
**Authentication**: `require_read_permission`  
**Response**: `GraphStatsResponse`
- `document_count`: int
- `chunk_count`: int
- `entity_count`: int
- `relationship_count`: int
- `total_size`: int
- `community_count`: int
- `collection_count`: int
- `pending_count`: int
- `entity_relationship_ratio`: float
- `relationship_target_ratio`: float

---

### Document Management

#### `POST /api/upload`
**Description**: Upload a file to the knowledge base  
**Authentication**: `require_manage_permission`  
**Parameters**:
- `file`: UploadFile (multipart/form-data)
- `collection_id`: Optional[str] (query param)
- `start_processing`: bool (query param, default: false)

**Response**: `UploadResponse`
- `document_id`: str
- `filename`: str
- `status`: ProcessingStatus
- `message`: str

**Notes**: For bulk uploads, set `start_processing=false` and call `/api/documents/process-pending` later.

#### `GET /api/documents`
**Description**: List all documents in the knowledge base  
**Authentication**: `require_read_permission`  
**Response**: `{"documents": List[dict], "total": int}`

#### `GET /api/documents/{document_id}`
**Description**: Get a specific document  
**Authentication**: `require_read_permission`  
**Response**: Document metadata dict

#### `GET /api/documents/{document_id}/content`
**Description**: Get document with full content (all chunks concatenated)  
**Authentication**: None  
**Response**: Document with `chunks` array and `full_content` string

#### `DELETE /api/documents/{document_id}`
**Description**: Delete a document and clean up orphaned entities  
**Authentication**: `require_manage_permission`  
**Response**: `{"message": str, "orphaned_entities_removed": int, "orphaned_communities_removed": int}`

#### `POST /api/documents/delete`
**Description**: Delete multiple documents  
**Authentication**: `require_manage_permission`  
**Request**: `DeleteRequest`
- `document_ids`: List[str]

**Response**: `{"message": str, "deleted_count": int, "orphaned_entities_removed": int, "orphaned_communities_removed": int}`

#### `DELETE /api/documents`
**Description**: Delete all documents (destructive operation)  
**Authentication**: `require_manage_permission`  
**Response**: `{"message": str, "deleted_count": int, "entities_removed": int, "communities_removed": int}`

#### `POST /api/documents/{document_id}/reprocess`
**Description**: Reprocess a single document  
**Authentication**: None  
**Parameters**:
- `file`: Optional[UploadFile] - If provided, updates stored file and reprocesses

**Response**: `{"document_id": str, "filename": str, "status": ProcessingStatus, "message": str}`

#### `POST /api/documents/reprocess`
**Description**: Reprocess multiple documents using stored files  
**Authentication**: None  
**Request**: `ReprocessRequest`
- `document_ids`: List[str]

**Parameters**:
- `concurrency`: Optional[int] (query param, 1-50)

**Response**: `{"results": List[dict], "total_queued": int, "task_id": str, "concurrency": int, "message": str}`

#### `GET /api/documents/pending`
**Description**: Get all documents with 'pending' status  
**Authentication**: None  
**Response**: `{"pending_count": int, "documents": List[dict]}`

#### `POST /api/documents/process-pending`
**Description**: Start processing all pending documents as background task  
**Authentication**: None  
**Parameters**:
- `concurrency`: Optional[int] (query param, 1-50, defaults to config)

**Response**: `{"task_id": str, "status": TaskStatus, "pending_count": int, "concurrency": int, "message": str}`

---

### Custom Input (Manual Q&A, Text, Markdown)

#### `POST /api/custom-input/generate-topic`
**Description**: Generate a topic hint for custom content using LLM  
**Authentication**: None  
**Request**: `TopicHintRequest`
- `content`: str (min 10 chars)
- `answer`: Optional[str]
- `input_type`: str ("qa" | "text" | "markdown")

**Response**: `TopicHintResponse`
- `topic_hint`: str
- `existing_similar`: List[str]

#### `POST /api/custom-input`
**Description**: Create a custom knowledge input (Q&A, text, or markdown)  
**Authentication**: `require_manage_permission`  
**Request**: `CustomInputCreate`
- `input_type`: CustomInputType ("qa" | "text" | "markdown")
- `content`: str (min 10 chars)
- `answer`: Optional[str] (required for Q&A type)
- `title`: Optional[str] (max 200 chars)
- `collection_id`: Optional[str]
- `start_processing`: bool (default: true)

**Response**: `CustomInputResponse`
- `document_id`: str
- `filename`: str
- `status`: ProcessingStatus
- `message`: str
- `input_type`: CustomInputType

#### `GET /api/custom-inputs`
**Description**: List all custom inputs with optional search  
**Authentication**: None  
**Parameters**:
- `search`: Optional[str] (query param)
- `limit`: int (query param, default: 50, max: 200)

**Response**: `{"custom_inputs": List[dict], "total": int}`

#### `GET /api/custom-inputs/{document_id}`
**Description**: Get a custom input's full data for editing  
**Authentication**: None  
**Response**: Custom input dict with `content`, `answer`, `input_type`, `topic_hint`

---

### Search & RAG

#### `POST /api/search`
**Description**: Perform hybrid search (vector + keyword + metadata)  
**Authentication**: `require_read_permission`  
**Request**: `SearchRequest`
- `query`: str
- `top_k`: int (default: 5, min: 1, max: 50)
- `filters`: Optional[dict]

**Response**: `SearchResponse`
- `query`: str
- `results`: List[SearchResult]
- `total_results`: int

#### `POST /api/ask`
**Description**: Ask a question using enhanced GraphRAG  
**Authentication**: `require_read_permission`  
**Request**: `RAGRequest`
- `question`: str
- `top_k`: int (default: 5, min: 1, max: 20)
- `use_graph`: bool (default: true)
- `max_hops`: int (default: 2, min: 1, max: 3)
- `conversation_history`: Optional[List[ConversationMessage]]
- `use_reranking`: bool (default: true)
- `use_agentic`: bool (default: false)
- `use_fast_search`: bool (default: false)

**Response**: `RAGResponse`
- `question`: str
- `answer`: str
- `sources`: List[SearchResult]
- `graph_context`: Optional[GraphContext]
- `reranked`: bool
- `reasoning_steps`: Optional[List[str]]
- `sub_questions`: Optional[List[str]]
- `communities_used`: Optional[List[int]]
- `retrieval_stats`: Optional[dict]
- `collection_id`: Optional[str]

#### `POST /api/ask/stream`
**Description**: Stream RAG response (Server-Sent Events)  
**Authentication**: `require_read_permission`  
**Request**: `RAGRequest` (same as `/api/ask`)

**Response**: SSE stream with events:
- `content`: Streamed answer tokens
- `sources`: Retrieved sources (at end)
- `graph_context`: Graph context (at end)
- `thinking`: Reasoning steps (if agentic mode)
- `sub_questions`: Decomposed questions (if agentic mode)
- `retrieval`: Source retrieval progress
- `retrieval_stats`: Final statistics
- `done`: Completion signal

**Notes**: 
- When `use_agentic=true`: Includes extended thinking visibility
- When `use_fast_search=true`: Uses simple vector search only (fastest)

#### `POST /api/ask/stream/thinking`
**Description**: Stream RAG with extended thinking visibility  
**Authentication**: None  
**Request**: `RAGRequest`

**Response**: SSE stream with thinking events (same as agentic mode in `/api/ask/stream`)

---

### Knowledge Graph

#### `GET /api/graph/visualization`
**Description**: Get knowledge graph data for visualization  
**Authentication**: None  
**Parameters**:
- `limit`: int (query param, default: 100, max: 10000, 0 = all)
- `include_neighbors`: bool (query param, default: true)

**Response**: `{"nodes": List[dict], "edges": List[dict], "stats": dict}`

#### `GET /api/graph/entity/{entity_name}/relationships`
**Description**: Get entity and all relationships up to max_depth hops  
**Authentication**: None  
**Parameters**:
- `max_depth`: int (query param, default: 2, min: 1, max: 3)
- `limit`: int (query param, default: 50, max: 200)

**Response**: `{"entity": dict, "relationships": List[dict], "related_entities": List[dict]}`

#### `POST /api/graph/subgraph`
**Description**: Get subgraph containing specified entities  
**Authentication**: None  
**Request**: List[str] (entity names)  
**Parameters**:
- `include_connections`: bool (query param, default: true)

**Response**: `{"nodes": List[dict], "edges": List[dict]}`

#### `GET /api/graph/entities`
**Description**: List entities in the knowledge graph  
**Authentication**: None  
**Parameters**:
- `entity_type`: Optional[str] (query param, filter by type)
- `limit`: int (query param, default: 50, max: 200)

**Response**: `{"entities": List[dict], "total": int}`

#### `GET /api/graph/entity/{entity_name}`
**Description**: Get details about a specific entity  
**Authentication**: None  
**Parameters**:
- `max_hops`: int (query param, default: 2, min: 1, max: 3)

**Response**: `GraphContext` with entities and relationships

#### `GET /api/graph/search`
**Description**: Search for entities by name  
**Authentication**: None  
**Parameters**:
- `query`: str (query param, min length: 1)

**Response**: `{"query": str, "results": List[dict]}`

#### `GET /api/graph/status`
**Description**: Get GraphRAG system status  
**Authentication**: None  
**Response**: `{
  "graph_extraction_enabled": bool,
  "llm_available": bool,
  "model": Optional[str],
  "entity_count": int,
  "relationship_count": int,
  "community_count": int,
  "collection_count": int,
  "community_detection_enabled": bool,
  "graph_summarization_enabled": bool,
  "semantic_entity_resolution_enabled": bool,
  "collections_enabled": bool
}`

---

### Collections

#### `GET /api/collections`
**Description**: List all collections  
**Authentication**: None  
**Response**: `{"collections": List[Collection], "total": int}`

#### `POST /api/collections`
**Description**: Create a new collection  
**Authentication**: `require_manage_permission`  
**Request**: `CollectionCreate`
- `name`: str (min: 1, max: 100)
- `description`: Optional[str] (max: 500)

**Response**: `Collection`

#### `GET /api/collections/{collection_id}`
**Description**: Get a specific collection with stats  
**Authentication**: None  
**Response**: `Collection`

#### `DELETE /api/collections/{collection_id}`
**Description**: Delete a collection (moves documents to default collection)  
**Authentication**: `require_manage_permission`  
**Response**: `{"message": str, "documents_moved": int}`

**Notes**: Cannot delete the "default" collection.

#### `POST /api/collections/{collection_id}/documents/{document_id}`
**Description**: Add a document to a collection  
**Authentication**: None  
**Response**: `{"message": str}`

#### `POST /api/documents/move`
**Description**: Move multiple documents to a collection  
**Authentication**: None  
**Request**: `MoveDocumentsRequest`
- `document_ids`: List[str]
- `target_collection_id`: str

**Response**: `{"message": str, "moved_count": int}`

#### `GET /api/collections/{collection_id}/entities`
**Description**: Get entities in a collection's knowledge graph  
**Authentication**: None  
**Parameters**:
- `limit`: int (query param, default: 100, max: 500)

**Response**: `{"entities": List[dict], "total": int}`

---

### Community Detection

#### `GET /api/graph/communities`
**Description**: List all detected communities  
**Authentication**: None  
**Parameters**:
- `limit`: int (query param, default: 50, max: 200)

**Response**: `{"communities": List[Community], "total": int}`

#### `POST /api/graph/communities/detect`
**Description**: Start community detection as background task  
**Authentication**: None  
**Parameters**:
- `min_size`: int (query param, default: 3, min: 2, max: 20)
- `collection_id`: Optional[str] (query param)

**Response**: `{"task_id": str, "status": TaskStatus, "message": str}`

**Notes**: Poll `/api/tasks/{task_id}` for progress.

#### `GET /api/graph/communities/{community_id}`
**Description**: Get a specific community with entities and relationships  
**Authentication**: None  
**Response**: `Community`

#### `POST /api/graph/communities/summarize`
**Description**: Generate or regenerate summaries for communities  
**Authentication**: None  
**Request**: `CommunitySummaryRequest`
- `community_ids`: Optional[List[int]] (if None, summarizes all)
- `force_regenerate`: bool (default: false)

**Response**: `{"results": List[dict], "total_processed": int}`

#### `GET /api/graph/communities/search`
**Description**: Search communities by summary content  
**Authentication**: None  
**Parameters**:
- `query`: str (query param, min length: 1)
- `limit`: int (query param, default: 5, max: 20)

**Response**: `{"query": str, "results": List[dict]}`

---

### Background Tasks

#### `GET /api/tasks/{task_id}`
**Description**: Get task status and progress  
**Authentication**: None  
**Response**: `TaskProgress`

#### `GET /api/tasks/{task_id}/result`
**Description**: Get task result (returns 202 if still running)  
**Authentication**: None  
**Response**: Task result dict or 202 status

#### `GET /api/tasks`
**Description**: List all active tasks  
**Authentication**: None  
**Parameters**:
- `status`: Optional[str] (query param)
- `task_type`: Optional[str] (query param)

**Response**: `{"tasks": List[TaskProgress], "total": int}`

#### `DELETE /api/tasks/{task_id}`
**Description**: Cancel/remove a task  
**Authentication**: None  
**Response**: `{"message": str}`

#### `POST /api/tasks/cleanup`
**Description**: Remove old completed/failed tasks  
**Authentication**: None  
**Parameters**:
- `max_age_hours`: int (query param, default: 24, min: 1, max: 168)

**Response**: `{"removed": int, "remaining": int}`

---

### Cleanup

#### `POST /api/cleanup/orphaned-entities`
**Description**: Clean up orphaned entities from knowledge graph  
**Authentication**: None  
**Response**: `{"message": str, "orphaned_entities_removed": int}`

---

### System Configuration

#### `GET /api/admin/config`
**Description**: Get system configuration (read-only, no secrets exposed)
**Authentication**: `require_admin`
**Response**: `SystemConfigResponse`

Returns current system settings grouped into:
- **LLM**: `openai_model`, `openai_api_base`, `relationship_max_context`, `parallel_relationship_batches`, `relationship_target_ratio`, `relationship_max_rounds`, `relationship_max_hours`
- **Extraction**: `extraction_model`, `extraction_api_base`, `extraction_max_context`, `batch_processing_concurrency`
- **Vision**: `vision_model`, `vision_api_base`, `vision_max_concurrent`, `vision_model_available`
- **Embeddings**: `embedding_model`, `embedding_dimension`, `embedding_api_base`, `embedding_send_dimensions`
- Plus: chunking, search/RAG, graph, community detection, entity resolution, collections, features settings

**Note**: API keys, passwords, and secrets are never included in the response.

---

### Admin API Key Management

#### `GET /api/admin/api-keys`
**Description**: List all API keys  
**Authentication**: `require_admin`  
**Response**: `List[APIKeyListItem]`

#### `POST /api/admin/api-keys`
**Description**: Create a new API key  
**Authentication**: `require_admin`  
**Request**: `CreateAPIKeyRequest`
- `name`: str (min: 1, max: 100)
- `permissions`: List[APIKeyPermission] (min: 1)

**Response**: `CreateAPIKeyResponse` (includes actual key - shown only once!)

#### `GET /api/admin/api-keys/{key_id}`
**Description**: Get a specific API key by ID  
**Authentication**: `require_admin`  
**Response**: `APIKeyListItem`

#### `PATCH /api/admin/api-keys/{key_id}`
**Description**: Update an API key  
**Authentication**: `require_admin`  
**Request**: `UpdateAPIKeyRequest`
- `name`: Optional[str]
- `permissions`: Optional[List[APIKeyPermission]]
- `is_active`: Optional[bool]

**Response**: `APIKeyListItem`

#### `DELETE /api/admin/api-keys/{key_id}`
**Description**: Delete an API key permanently  
**Authentication**: `require_admin`  
**Response**: `{"message": str}`

#### `POST /api/admin/api-keys/{key_id}/revoke`
**Description**: Revoke an API key (deactivate)  
**Authentication**: `require_admin`  
**Response**: `APIKeyListItem`

#### `POST /api/admin/api-keys/{key_id}/activate`
**Description**: Reactivate a revoked API key  
**Authentication**: `require_admin`  
**Response**: `APIKeyListItem`

---

## Request/Response Models

### Core Models

#### `ProcessingStatus` (Enum)
- `PENDING`
- `PROCESSING`
- `EXTRACTING`
- `COMPLETED`
- `FAILED`

#### `DocumentMetadata`
- `filename`: str
- `file_type`: str
- `file_size`: int
- `file_path`: Optional[str]
- `upload_date`: datetime
- `chunk_count`: int
- `processing_status`: ProcessingStatus
- `error_message`: Optional[str]
- `progress_current`: int
- `progress_total`: int
- `progress_message`: str

#### `DocumentChunk`
- `id`: str
- `document_id`: str
- `content`: str
- `embedding`: Optional[List[float]]
- `chunk_index`: int
- `metadata`: dict

#### `Document`
- `id`: str
- `metadata`: DocumentMetadata
- `chunks`: List[DocumentChunk]

### GraphRAG Models

#### `Entity`
- `name`: str
- `type`: str (Person, Organization, Concept, Location, Event, Technology, etc.)
- `description`: str

#### `Relationship`
- `source`: str
- `target`: str
- `relationship_type`: str (WORKS_FOR, LOCATED_IN, USES, RELATED_TO, etc. — 14 standard types; MENTIONS removed)
- `description`: str
- `weight`: float (0-10)
- `confidence`: float (0.0-1.0, relationships below 0.5 are filtered before storage)

#### `ExtractionResult`
- `entities`: List[Entity]
- `relationships`: List[Relationship]

#### `GraphContext`
- `entities`: List[dict]
- `relationships`: List[dict]
- `chunks`: List[dict]
- `communities`: List[dict]

### Collection Models

#### `Collection`
- `id`: str
- `name`: str
- `description`: Optional[str]
- `created_at`: datetime
- `document_count`: int
- `entity_count`: int

#### `CollectionCreate`
- `name`: str (min: 1, max: 100)
- `description`: Optional[str] (max: 500)

#### `CollectionUpdate`
- `name`: Optional[str] (min: 1, max: 100)
- `description`: Optional[str] (max: 500)

### Community Models

#### `Community`
- `id`: int
- `name`: Optional[str]
- `summary`: Optional[str]
- `entity_count`: int
- `entities`: List[dict]
- `key_relationships`: List[dict]
- `collection_id`: Optional[str]

#### `CommunitySummaryRequest`
- `community_ids`: Optional[List[int]]
- `force_regenerate`: bool

### Search & RAG Models

#### `SearchRequest`
- `query`: str
- `top_k`: int (default: 5, min: 1, max: 50)
- `filters`: Optional[dict]

#### `SearchResult`
- `document_id`: str
- `chunk_id`: str
- `content`: str
- `score`: float
- `metadata`: dict

#### `SearchResponse`
- `query`: str
- `results`: List[SearchResult]
- `total_results`: int

#### `ConversationMessage`
- `role`: str ("user" | "assistant")
- `content`: str

#### `RAGRequest`
- `question`: str
- `top_k`: int (default: 5, min: 1, max: 20)
- `use_graph`: bool (default: true)
- `max_hops`: int (default: 2, min: 1, max: 3)
- `conversation_history`: Optional[List[ConversationMessage]]
- `use_reranking`: bool (default: true)
- `use_agentic`: bool (default: false)
- `use_fast_search`: bool (default: false)

#### `RAGResponse`
- `question`: str
- `answer`: str
- `sources`: List[SearchResult]
- `graph_context`: Optional[GraphContext]
- `reranked`: bool
- `reasoning_steps`: Optional[List[str]]
- `sub_questions`: Optional[List[str]]
- `communities_used`: Optional[List[int]]
- `retrieval_stats`: Optional[dict]
- `collection_id`: Optional[str]

### Custom Input Models

#### `CustomInputType` (Enum)
- `QA`
- `TEXT`
- `MARKDOWN`

#### `CustomInputCreate`
- `input_type`: CustomInputType
- `content`: str (min: 10)
- `answer`: Optional[str]
- `title`: Optional[str] (max: 200)
- `collection_id`: Optional[str]
- `start_processing`: bool (default: true)

#### `CustomInputResponse`
- `document_id`: str
- `filename`: str
- `status`: ProcessingStatus
- `message`: str
- `input_type`: CustomInputType

### Task Models

#### `TaskStatus` (Enum)
- `PENDING`
- `RUNNING`
- `COMPLETED`
- `FAILED`

#### `TaskProgress`
- `task_id`: str
- `task_type`: str
- `status`: TaskStatus
- `progress_current`: int
- `progress_total`: int
- `progress_percent`: float
- `message`: str
- `started_at`: Optional[datetime]
- `completed_at`: Optional[datetime]
- `error`: Optional[str]
- `result`: Optional[dict]

### API Key Models

#### `APIKeyPermission` (Enum)
- `READ` - Can use Ask AI, search, view graphs
- `MANAGE` - Can upload, edit, delete documents and collections

#### `APIKey`
- `id`: str
- `name`: str
- `key_prefix`: str
- `key_hash`: str
- `permissions`: List[APIKeyPermission]
- `is_active`: bool
- `created_at`: datetime
- `last_used_at`: Optional[datetime]
- `created_by`: str

#### `CreateAPIKeyRequest`
- `name`: str (min: 1, max: 100)
- `permissions`: List[APIKeyPermission] (min: 1)

#### `CreateAPIKeyResponse`
- `id`: str
- `name`: str
- `key`: str (actual API key - shown only once!)
- `key_prefix`: str
- `permissions`: List[APIKeyPermission]
- `created_at`: datetime

#### `APIKeyListItem`
- `id`: str
- `name`: str
- `key_prefix`: str
- `permissions`: List[APIKeyPermission]
- `is_active`: bool
- `created_at`: datetime
- `last_used_at`: Optional[datetime]
- `created_by`: str

#### `UpdateAPIKeyRequest`
- `name`: Optional[str] (min: 1, max: 100)
- `permissions`: Optional[List[APIKeyPermission]]
- `is_active`: Optional[bool]

### Other Models

#### `UploadResponse`
- `document_id`: str
- `filename`: str
- `status`: ProcessingStatus
- `message`: str

#### `ReprocessRequest`
- `document_ids`: List[str]

#### `DeleteRequest`
- `document_ids`: List[str]

#### `MoveDocumentsRequest`
- `document_ids`: List[str]
- `target_collection_id`: str

#### `HealthResponse`
- `status`: str
- `neo4j_connected`: bool
- `version`: str

#### `GraphStatsResponse`
- `document_count`: int
- `chunk_count`: int
- `entity_count`: int
- `relationship_count`: int
- `total_size`: int
- `community_count`: int
- `collection_count`: int
- `pending_count`: int
- `entity_relationship_ratio`: float
- `relationship_target_ratio`: float

---

## Service Classes

### DocumentProcessor

**Location**: `app/services/document_processor.py`

**Key Methods**:
- `process_file(file_path, filename, file_size, collection_id) -> str`: Process a file and store it
- `store_file_only(file_path, filename, file_size, collection_id) -> str`: Store file without processing
- `get_pending_documents() -> List[dict]`: Get all pending documents
- `process_pending_documents(concurrency, progress_callback) -> dict`: Process pending documents with concurrency control
- `queue_document_for_reprocessing(doc_id) -> bool`: Queue document for reprocessing
- `reprocess_document(doc_id) -> bool`: Reprocess a document immediately
- `reprocess_document_from_file(doc_id, file_path, file_type) -> bool`: Reprocess from file

**Features**:
- URL protection during chunking (prevents URLs from being split)
- Sentence-based or word-based chunking
- OpenAI or SentenceTransformers embeddings
- Graph extraction with concurrent processing
- Document summary generation for extraction context

### QueryProcessor

**Location**: `app/services/document_processor.py`

**Key Methods**:
- `search(query, top_k, filters) -> List[dict]`: Semantic vector search
- `hybrid_search(query, top_k, vector_weight, keyword_weight, metadata_weight) -> List[dict]`: Hybrid search with RRF
- `graph_search_async(query, top_k, max_hops, use_hybrid_rrf) -> dict`: Graph-enhanced search
- `rerank_results(query, results, top_k) -> List[dict]`: Cross-encoder reranking
- `rag_query(question, top_k, use_graph, max_hops, conversation_history, use_reranking, use_agentic) -> dict`: RAG query
- `agentic_rag_stream(question, top_k, max_hops, conversation_history, collection_id) -> AsyncGenerator`: Streaming agentic RAG (legacy)
- `agent_rag_stream(question, mode, conversation_history, collection_id) -> AsyncGenerator`: Agent-based research pipeline (researcher/writer)
- `agent_rag_query(question, mode, conversation_history, collection_id) -> dict`: Non-streaming agent RAG query

**Features**:
- Hybrid search combining vector, keyword, and graph traversal (RRF)
- Cross-encoder reranking for precision
- Conversation memory support
- Agent-based research pipeline with LLM-driven tool-calling (researcher/writer architecture)
- Community-aware retrieval via `community_search` tool
- Entity exploration via `entity_lookup` tool
- Reasoning transparency via `reasoning` tool (quality mode)

### GraphExtractor

**Location**: `app/services/graph_extractor.py`

**Key Methods**:
- `extract_from_text(text, document_summary, entity_types, relation_types) -> ExtractionResult`: Extract entities and relationships
- `extract_from_text_async(...) -> ExtractionResult`: Async version for concurrent processing
- `extract_entities_from_query(query) -> List[str]`: Extract entities from user query
- `extract_entities_from_query_async(query) -> List[str]`: Async version
- `generate_document_summary(document) -> str`: Generate document summary
- `generate_document_summary_async(document) -> str`: Async version
- `generate_community_summary(entities, relationships) -> dict`: Generate community summary
- `generate_community_summary_async(entities, relationships) -> dict`: Async version
- `generate_entity_embedding(entity_name, entity_type, description) -> Optional[List[float]]`: Generate entity embedding

**Features**:
- XML-formatted prompts for better parsing
- Entity and relationship extraction with weights
- Document summary generation for context
- Community summarization
- Entity embedding generation for semantic resolution

### Neo4jService

**Location**: `app/services/neo4j_service.py`

**Key Methods**:
- `initialize_schema()`: Create indexes and constraints
- `store_document(doc_id, metadata) -> str`: Store document node
- `store_chunk(chunk) -> str`: Store chunk with embedding
- `vector_search(query_embedding, top_k, filters) -> List[dict]`: Vector similarity search
- `hybrid_search_rrf(query_embedding, query_text, entity_names, top_k, max_hops, vector_weight, keyword_weight, graph_weight) -> dict`: Hybrid search with RRF
- `store_graph_extraction(chunk_id, extraction) -> dict`: Store entities and relationships
- `traverse_from_entities(entity_names, max_hops) -> dict`: Graph traversal
- `detect_communities(min_size, collection_id) -> List[dict]`: Community detection
- `store_community(community_id, entity_names, summary, name)`: Store community
- `search_communities_by_content(query, limit) -> List[dict]`: Search communities
- `create_collection(name, description) -> Optional[Collection]`: Create collection
- `list_collections() -> List[Collection]`: List collections
- `add_document_to_collection(doc_id, collection_id) -> bool`: Add document to collection
- `delete_document(doc_id) -> dict`: Delete document and cleanup
- `cleanup_orphaned_entities() -> int`: Cleanup orphaned entities

**Features**:
- Vector indexes for embeddings
- Full-text indexes for content search
- Community detection using graph algorithms
- Collection-level knowledge graphs
- Semantic entity resolution with embeddings
- Graph visualization data generation

### AuthService

**Location**: `app/services/auth_service.py`

**Key Functions**:
- `validate_api_key(api_key) -> AuthResult`: Validate API key
- `hash_api_key(api_key) -> str`: Hash API key for storage
- `verify_api_key_hash(api_key, stored_hash) -> bool`: Verify API key hash
- `generate_api_key(prefix) -> Tuple[str, str]`: Generate new API key

**Dependencies** (FastAPI):
- `require_api_key`: Requires any valid API key
- `require_read_permission`: Requires READ permission
- `require_manage_permission`: Requires MANAGE permission
- `require_admin`: Requires admin API key

**AuthResult**:
- `is_authenticated`: bool
- `is_admin`: bool
- `permissions`: List[APIKeyPermission]
- `key_id`: Optional[str]
- `error`: Optional[str]
- `has_permission(permission) -> bool`: Check permission

### APIKeyService

**Location**: `app/services/api_key_service.py`

**Key Methods**:
- `create_api_key(name, permissions, created_by) -> Optional[CreateAPIKeyResponse]`: Create API key
- `list_api_keys() -> List[APIKeyListItem]`: List all API keys
- `get_api_key(key_id) -> Optional[APIKeyListItem]`: Get API key by ID
- `update_api_key(key_id, request) -> Optional[APIKeyListItem]`: Update API key
- `delete_api_key(key_id) -> bool`: Delete API key
- `revoke_api_key(key_id) -> Optional[APIKeyListItem]`: Revoke API key
- `activate_api_key(key_id) -> Optional[APIKeyListItem]`: Activate API key

### PromptSecurity

**Location**: `app/services/prompt_security.py`

**Key Functions**:
- `detect_injection_attempt(user_input) -> Tuple[bool, Optional[str]]`: Detect prompt injection
- `sanitize_user_input(user_input) -> str`: Sanitize user input
- `filter_output(response, system_prompt) -> str`: Filter output for prompt leakage
- `get_anti_injection_instruction(enabled) -> str`: Get anti-injection instruction for prompts
- `get_safe_refusal_message() -> str`: Get safe refusal message
- `validate_and_process_input(user_input, strict_mode, enabled) -> Tuple[str, bool, Optional[str]]`: Main validation entry point

**Features**:
- Pattern-based injection detection
- Character replacement attack detection
- Output filtering for prompt leakage
- Configurable strict/sanitize modes

---

## Authentication

### Authentication Mechanism

The API uses **API Key authentication** via the `X-API-Key` header.

### Authentication Flow

1. **Admin API Key** (from environment):
   - Set via `ADMIN_API_KEY` environment variable
   - Has full access (READ + MANAGE permissions)
   - Checked first

2. **Generated API Keys** (stored in Neo4j):
   - Created via `/api/admin/api-keys` endpoint
   - Stored with SHA-256 hash
   - Has assigned permissions (READ or MANAGE)
   - Validated against stored hash

### Permission Levels

- **READ**: Can use Ask AI, search, view graphs
- **MANAGE**: Can upload, edit, delete documents and collections
- **ADMIN**: Full access (only admin API key)

### API Key Format

- Read-only keys: `cortex_ro_<64 hex chars>`
- Read-write keys: `cortex_rw_<64 hex chars>`
- Prefix (first 12 chars) used for lookup

### Usage Example

```bash
curl -H "X-API-Key: cortex_ro_xxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  https://api.example.com/api/ask
```

### Security Features

- API keys are hashed with SHA-256 before storage
- Constant-time comparison for hash verification
- Last used timestamp tracking
- Revocation support (deactivate without deletion)
- Admin-only key management endpoints

---

## Configuration

### Configuration File

**Location**: `app/config.py`

### Key Configuration Options

#### Neo4j Configuration
- `NEO4J_URI`: Neo4j connection URI (default: `bolt://localhost:7687`)
- `NEO4J_USER`: Neo4j username (default: `neo4j`)
- `NEO4J_PASSWORD`: Neo4j password (default: `password123`)

#### OpenAI / LiteLLM Configuration
- `OPENAI_API_KEY`: API key for LLM calls
- `OPENAI_API_BASE`: API base URL (default: `https://api.openai.com/v1`)
- `OPENAI_MODEL`: Model for LLM calls (default: `openai/minimax-m3`)
- `OPENAI_MODEL_FAST_MODE`: Model for Fast Mode (defaults to `OPENAI_MODEL`)

#### Upload Configuration
- `UPLOAD_DIR`: Upload directory (default: `./uploads`)
- `CUSTOM_INPUTS_DIR`: Custom inputs directory (default: `./custom_inputs`)
- `MAX_FILE_SIZE_MB`: Maximum file size in MB (default: `50`)
- `ALLOWED_EXTENSIONS`: List of allowed extensions (default: `[".pdf", ".txt", ".md", ".docx", ".xlsx"]`)

#### Embedding Configuration
- `EMBEDDING_MODEL`: Embedding model (default: `openai/text-embedding-3-small`)
- `EMBEDDING_DIMENSION`: Embedding dimension (default: `1536`)
- `USE_OPENAI_EMBEDDINGS`: Use OpenAI embeddings (default: `True`)

#### Chunking Configuration
- `CHUNK_SIZE`: Chunk size in words (default: `500`)
- `CHUNK_OVERLAP`: Chunk overlap in words (default: `50`)
- `CHUNK_BY`: Chunking method - "word" or "sentence" (default: `"sentence"`)
- `SENTENCES_PER_CHUNK`: Sentences per chunk when using sentence splitting (default: `5`)

#### GraphRAG Configuration
- `ENABLE_GRAPH_EXTRACTION`: Enable LLM-based extraction (default: `True`)
- `GRAPH_EXTRACTION_MODEL`: Model for extraction (defaults to `OPENAI_MODEL`)
- `MAX_GRAPH_HOPS`: Maximum hops for graph traversal (default: `2`)
- `CONCURRENT_EXTRACTIONS`: Concurrent chunks for extraction (default: `20`)

#### Batch Processing Configuration
- `BATCH_PROCESSING_CONCURRENCY`: Concurrent documents in batch mode (default: `10`)
- `PROCESSING_THREAD_WORKERS`: Thread pool workers for CPU operations (default: `4`)

#### Enhanced RAG Configuration
- `ENABLE_RERANKING`: Enable cross-encoder reranking (default: `True`)
- `RERANKING_MODEL`: Cross-encoder model (default: `cross-encoder/ms-marco-MiniLM-L-6-v2`)
- `ENABLE_HYBRID_SEARCH`: Enable hybrid search (default: `True`)
- `VECTOR_WEIGHT`: Weight for vector search (default: `0.5`)
- `KEYWORD_WEIGHT`: Weight for keyword search (default: `0.3`)
- `GRAPH_WEIGHT`: Weight for graph context (default: `0.2`)
- `MAX_CONVERSATION_HISTORY`: Max messages in conversation (default: `6`)
- `ENABLE_AGENTIC_RAG`: Enable multi-step agentic RAG (default: `True`)
- `MAX_AGENTIC_STEPS`: Maximum steps in agentic RAG — legacy (default: `3`)

#### Agent-Based Research Pipeline
- `ENABLE_AGENT_RESEARCH`: Use agent pipeline for deep research mode (default: `True`)
- `ENABLE_AGENT_CHAT`: Use agent pipeline for standard chat mode (default: `True`)
- `RESEARCHER_MAX_ITERATIONS_SPEED`: Max agent iterations for chat (default: `3`)
- `RESEARCHER_MAX_ITERATIONS_QUALITY`: Max agent iterations for deep research (default: `8`)
- `WRITER_MAX_TOKENS_SPEED`: Max output tokens for chat answers (default: `1200`)
- `WRITER_MAX_TOKENS_QUALITY`: Max output tokens for deep research answers (default: `4000`)

#### Relationship Analysis
- `PARALLEL_RELATIONSHIP_BATCHES`: Batches to process in parallel, 0 = auto (default: `0`)
- `RELATIONSHIP_TARGET_RATIO`: Target entity-to-relationship ratio (default: `1.0`)
- `RELATIONSHIP_MAX_ROUNDS`: Maximum analysis rounds (default: `3`)
- `RELATIONSHIP_MAX_HOURS`: Maximum hours for relationship analysis (default: not set)
- `RELATIONSHIP_MAX_PER_ENTITY`: Soft cap on relationships per entity (default: `50`, 0 = no cap)
- `RELATIONSHIP_MAX_OUTPUT_TOKENS`: Max output tokens per relationship batch (default: `16000`)

#### Community Detection & Graph Summarization
- `ENABLE_COMMUNITY_DETECTION`: Enable community detection (default: `True`)
- `MIN_COMMUNITY_SIZE`: Minimum entities for community (default: `3`)
- `MAX_COMMUNITIES`: Maximum communities to track (default: `50`)
- `ENABLE_GRAPH_SUMMARIZATION`: Generate LLM summaries (default: `True`)
- `COMMUNITY_SUMMARY_MODEL`: Model for summaries (defaults to `OPENAI_MODEL`)

#### Enhanced Entity Resolution
- `ENABLE_SEMANTIC_ENTITY_RESOLUTION`: Use embeddings for entity matching (default: `True`)
- `ENTITY_SIMILARITY_THRESHOLD`: Threshold for deduplication (default: `0.85`)
- `ENTITY_EMBEDDING_MODEL`: Model for entity embeddings (defaults to `EMBEDDING_MODEL`)

#### Collection Configuration
- `ENABLE_COLLECTIONS`: Enable collections (default: `True`)
- `DEFAULT_COLLECTION`: Default collection name (default: `"default"`)

#### Extended Thinking / Reasoning Visibility
- `STREAM_REASONING_STEPS`: Stream reasoning steps (default: `True`)
- `SHOW_RETRIEVAL_STATS`: Show retrieval statistics (default: `True`)

#### Prompt Security
- `PROMPT_SECURITY`: Enable prompt injection protection (default: `True`)

#### Admin Authentication
- `ADMIN_EMAIL`: Admin login email (default: `admin@example.com`)
- `ADMIN_PASSWORD`: Admin login password (required for auth)
- `ADMIN_API_KEY`: Admin API key for full backend access
- `SESSION_SECRET`: Secret for JWT session encryption (min 32 chars)

### Environment Variable Loading

Configuration is loaded from:
1. Environment variables (uppercase, e.g., `NEO4J_URI`)
2. `.env` file (checked in multiple locations)
3. Default values (as shown above)

### Property Helpers

- `fast_mode_model`: Get model for Fast Mode
- `extraction_model`: Get model for graph extraction
- `summary_model`: Get model for community summarization
- `entity_embed_model`: Get model for entity embeddings

---

## Additional Notes

### GraphRAG Features

- **Entity Extraction**: Extracts entities (Person, Organization, Technology, etc.) from documents
- **Relationship Extraction**: Extracts relationships between entities with weights
- **Community Detection**: Detects communities of related entities using graph algorithms
- **Community Summarization**: Generates LLM summaries for communities
- **Semantic Entity Resolution**: Uses embeddings to deduplicate similar entities
- **Collection-Level Graphs**: Separate knowledge graphs per collection

### Search Features

- **Hybrid Search**: Combines vector similarity, keyword matching, and metadata search
- **Reciprocal Rank Fusion (RRF)**: Merges results from multiple search methods
- **Cross-Encoder Reranking**: Improves precision with cross-encoder models
- **Graph-Enhanced Retrieval**: Uses knowledge graph for context-aware search
- **Agentic RAG**: Multi-step reasoning for complex questions

### Processing Features

- **Batch Processing**: Process multiple documents with controlled concurrency
- **Progress Tracking**: Real-time progress updates for document processing
- **Background Tasks**: Long-running operations (community detection, batch processing)
- **URL Protection**: Prevents URLs from being split during chunking
- **Permanent File Storage**: Original files kept for reprocessing

---

## Error Handling

All endpoints return standard HTTP status codes:
- `200`: Success
- `400`: Bad Request (validation errors)
- `401`: Unauthorized (invalid/missing API key)
- `403`: Forbidden (insufficient permissions)
- `404`: Not Found
- `500`: Internal Server Error

Error responses include a `detail` field with the error message.

---

## Rate Limiting

Currently, there is no rate limiting implemented. Consider implementing rate limiting for production deployments.

---

## CORS

CORS is enabled for all origins (`allow_origins=["*"]`). Configure appropriately for production.

---

## Version

API Version: **2.0.0**

---

*Generated from backend codebase analysis*
