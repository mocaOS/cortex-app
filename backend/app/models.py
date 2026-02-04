from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    EXTRACTING = "extracting"  # New status for graph extraction phase
    COMPLETED = "completed"
    FAILED = "failed"


# =============================================================================
# GraphRAG Models - Entity and Relationship Extraction
# =============================================================================

class Entity(BaseModel):
    """An entity extracted from document text."""
    name: str = Field(..., description="The name or identifier of the entity")
    type: str = Field(..., description="Entity type: Person, Organization, Concept, Location, Event, Technology, etc.")
    description: str = Field(default="", description="Brief description of the entity in context")
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Neo4j",
                "type": "Technology",
                "description": "A graph database management system"
            }
        }


class Relationship(BaseModel):
    """A relationship between two entities."""
    source: str = Field(..., description="The name of the source entity")
    target: str = Field(..., description="The name of the target entity")
    relationship_type: str = Field(..., description="Type of relationship: WORKS_FOR, LOCATED_IN, USES, RELATED_TO, PART_OF, etc.")
    description: str = Field(default="", description="Description of how the entities are related")
    weight: float = Field(default=5.0, ge=0.0, le=10.0, description="Relationship strength score (0-10)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "source": "Neo4j",
                "target": "Graph Database",
                "relationship_type": "IS_A",
                "description": "Neo4j is a type of graph database",
                "weight": 8.0
            }
        }


class ExtractionResult(BaseModel):
    """Result of entity and relationship extraction from text."""
    entities: List[Entity] = Field(default_factory=list, description="List of extracted entities")
    relationships: List[Relationship] = Field(default_factory=list, description="List of extracted relationships")


class GraphContext(BaseModel):
    """Context retrieved from the knowledge graph for RAG."""
    entities: List[dict] = Field(default_factory=list, description="Relevant entities from the graph")
    relationships: List[dict] = Field(default_factory=list, description="Relationships connecting the entities")
    chunks: List[dict] = Field(default_factory=list, description="Text chunks that mention these entities")
    communities: List[dict] = Field(default_factory=list, description="Relevant entity communities with summaries")


# =============================================================================
# Collection-Level Knowledge Graphs
# =============================================================================

class Collection(BaseModel):
    """A collection of documents with a unified knowledge graph."""
    id: str = Field(..., description="Unique collection identifier")
    name: str = Field(..., description="Human-readable collection name")
    description: Optional[str] = Field(default=None, description="Collection description")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    document_count: int = Field(default=0, description="Number of documents in collection")
    entity_count: int = Field(default=0, description="Number of entities in collection graph")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "research-papers",
                "name": "Research Papers",
                "description": "Academic research papers on AI/ML",
                "document_count": 15,
                "entity_count": 234
            }
        }


class CollectionCreate(BaseModel):
    """Request model for creating a collection."""
    name: str = Field(..., min_length=1, max_length=100, description="Collection name")
    description: Optional[str] = Field(default=None, max_length=500, description="Collection description")


class CollectionUpdate(BaseModel):
    """Request model for updating a collection."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)


# =============================================================================
# Community Detection & Summarization
# =============================================================================

class Community(BaseModel):
    """A community of related entities detected in the knowledge graph."""
    id: int = Field(..., description="Community identifier from detection algorithm")
    name: Optional[str] = Field(default=None, description="Auto-generated community name")
    summary: Optional[str] = Field(default=None, description="LLM-generated summary of the community")
    entity_count: int = Field(default=0, description="Number of entities in this community")
    entities: List[dict] = Field(default_factory=list, description="Entities belonging to this community")
    key_relationships: List[dict] = Field(default_factory=list, description="Important relationships in this community")
    collection_id: Optional[str] = Field(default=None, description="Collection this community belongs to")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "Machine Learning Frameworks",
                "summary": "This community centers around ML frameworks including TensorFlow, PyTorch, and JAX, connected through their shared use in deep learning research.",
                "entity_count": 8,
                "entities": [{"name": "TensorFlow", "type": "Technology"}]
            }
        }


class CommunitySummaryRequest(BaseModel):
    """Request to generate/regenerate community summaries."""
    community_ids: Optional[List[int]] = Field(default=None, description="Specific communities to summarize, or all if None")
    force_regenerate: bool = Field(default=False, description="Regenerate even if summary exists")


# =============================================================================
# Enhanced Entity with Embedding Support (Semantic Resolution)
# =============================================================================

class EntityWithEmbedding(Entity):
    """Entity with embedding for semantic similarity matching."""
    embedding: Optional[List[float]] = Field(default=None, description="Entity name/description embedding")
    aliases: List[str] = Field(default_factory=list, description="Alternative names for this entity")
    community_id: Optional[int] = Field(default=None, description="Community this entity belongs to")
    collection_id: Optional[str] = Field(default=None, description="Collection scope for this entity")


# =============================================================================
# Extended Thinking / Reasoning Visibility
# =============================================================================

class ReasoningStep(BaseModel):
    """A single step in the agentic reasoning process."""
    step_number: int = Field(..., description="Step number in the reasoning chain")
    action: str = Field(..., description="Action type: decompose, search, rerank, synthesize")
    description: str = Field(..., description="Human-readable description of the step")
    details: Optional[dict] = Field(default=None, description="Additional details about the step")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ThinkingEvent(BaseModel):
    """Event emitted during extended thinking/reasoning."""
    event_type: str = Field(..., description="Type: thinking, search, retrieval, synthesis, done, error")
    content: str = Field(..., description="Event content or message")
    metadata: Optional[dict] = Field(default=None, description="Additional event metadata")


class AgenticRAGResult(BaseModel):
    """Result of an agentic RAG query with full reasoning trace."""
    question: str
    answer: str
    sources: List[dict]
    graph_context: Optional[GraphContext] = None
    reasoning_steps: List[ReasoningStep] = Field(default_factory=list)
    sub_questions: List[str] = Field(default_factory=list, description="Decomposed sub-questions")
    search_method: str = Field(default="agentic_rag")
    total_sources_considered: int = Field(default=0)
    communities_used: List[int] = Field(default_factory=list, description="Community IDs used in retrieval")


class DocumentMetadata(BaseModel):
    """Metadata associated with a document."""
    filename: str
    file_type: str
    file_size: int
    file_path: Optional[str] = Field(default=None, description="Path to stored original file")
    upload_date: datetime = Field(default_factory=datetime.utcnow)
    chunk_count: int = 0
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    error_message: Optional[str] = None
    # Progress tracking fields
    progress_current: int = Field(default=0, description="Current step in processing")
    progress_total: int = Field(default=0, description="Total steps in processing")
    progress_message: str = Field(default="", description="Human-readable progress message")


class DocumentChunk(BaseModel):
    """A chunk of a processed document."""
    id: str
    document_id: str
    content: str
    embedding: Optional[list[float]] = None
    chunk_index: int
    metadata: dict = {}


class Document(BaseModel):
    """A document in the knowledge base."""
    id: str
    metadata: DocumentMetadata
    chunks: list[DocumentChunk] = []


class SearchRequest(BaseModel):
    """Request model for semantic search."""
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    filters: Optional[dict] = None


class SearchResult(BaseModel):
    """A single search result."""
    document_id: str
    chunk_id: str
    content: str
    score: float
    metadata: dict = {}


class SearchResponse(BaseModel):
    """Response model for semantic search."""
    query: str
    results: list[SearchResult]
    total_results: int


class ConversationMessage(BaseModel):
    """A message in a conversation."""
    role: str = Field(..., description="Role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class RAGRequest(BaseModel):
    """Request model for RAG-based question answering."""
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    use_graph: bool = Field(default=True, description="Whether to use graph-enhanced retrieval")
    max_hops: int = Field(default=2, ge=1, le=3, description="Max hops for graph traversal")
    conversation_history: Optional[List[ConversationMessage]] = Field(
        default=None, 
        description="Previous conversation messages for context"
    )
    use_reranking: bool = Field(default=True, description="Whether to use cross-encoder reranking")
    use_agentic: bool = Field(default=False, description="Whether to use agentic multi-step RAG for complex questions")
    use_fast_search: bool = Field(default=False, description="Use simple vector search for faster responses (disables hybrid/reranking)")


class RAGResponse(BaseModel):
    """Response model for RAG-based question answering."""
    question: str
    answer: str
    sources: list[SearchResult]
    graph_context: Optional[GraphContext] = None
    reasoning_steps: Optional[List[str]] = Field(default=None, description="Steps taken in agentic RAG")
    reranked: bool = Field(default=False, description="Whether results were reranked")
    # Extended thinking / reasoning visibility
    sub_questions: Optional[List[str]] = Field(default=None, description="Decomposed sub-questions in agentic mode")
    communities_used: Optional[List[int]] = Field(default=None, description="Community IDs used for context")
    retrieval_stats: Optional[dict] = Field(default=None, description="Search statistics")
    collection_id: Optional[str] = Field(default=None, description="Collection scope for the query")


class GraphStatsResponse(BaseModel):
    """Response model for graph statistics."""
    document_count: int
    chunk_count: int
    entity_count: int
    relationship_count: int
    total_size: int
    # Enhanced stats
    community_count: int = Field(default=0, description="Number of detected communities")
    collection_count: int = Field(default=0, description="Number of collections")
    pending_count: int = Field(default=0, description="Number of documents pending processing")


class UploadResponse(BaseModel):
    """Response model for file upload."""
    document_id: str
    filename: str
    status: ProcessingStatus
    message: str


class DocumentListResponse(BaseModel):
    """Response model for listing documents."""
    documents: list[Document]
    total: int


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    neo4j_connected: bool
    version: str


class ReprocessRequest(BaseModel):
    """Request model for reprocessing documents."""
    document_ids: List[str] = Field(..., description="List of document IDs to reprocess")


class DeleteRequest(BaseModel):
    """Request model for deleting multiple documents."""
    document_ids: List[str] = Field(..., description="List of document IDs to delete")


class MoveDocumentsRequest(BaseModel):
    """Request model for moving documents to a collection."""
    document_ids: List[str] = Field(..., description="List of document IDs to move")
    target_collection_id: str = Field(..., description="Target collection ID to move documents to")


# =============================================================================
# Custom Input (Manual Q&A, Text, Markdown)
# =============================================================================

class CustomInputType(str, Enum):
    """Type of custom input."""
    QA = "qa"          # Question and answer pair
    TEXT = "text"      # Plain text
    MARKDOWN = "markdown"  # Markdown formatted text


class CustomInputCreate(BaseModel):
    """Request model for creating a custom knowledge input."""
    input_type: CustomInputType = Field(..., description="Type of input: qa, text, or markdown")
    content: str = Field(..., min_length=10, description="Main content (or question for Q&A)")
    answer: Optional[str] = Field(default=None, description="Answer (only for Q&A type)")
    title: Optional[str] = Field(default=None, max_length=200, description="Optional title/topic hint for filename generation")
    collection_id: Optional[str] = Field(default=None, description="Collection to add this input to")
    start_processing: bool = Field(default=True, description="Start processing immediately after saving")
    
    class Config:
        json_schema_extra = {
            "example": {
                "input_type": "qa",
                "content": "What is GraphRAG?",
                "answer": "GraphRAG combines knowledge graphs with RAG for better retrieval and reasoning.",
                "title": "GraphRAG explanation",
                "collection_id": "default"
            }
        }


class CustomInputResponse(BaseModel):
    """Response model for custom input creation."""
    document_id: str
    filename: str
    status: ProcessingStatus
    message: str
    input_type: CustomInputType


# =============================================================================
# Background Task Tracking
# =============================================================================

class TaskStatus(str, Enum):
    """Status of a background task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskProgress(BaseModel):
    """Progress information for a background task."""
    task_id: str = Field(..., description="Unique task identifier")
    task_type: str = Field(..., description="Type of task: community_detection, summarization, etc.")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Current task status")
    progress_current: int = Field(default=0, description="Current step number")
    progress_total: int = Field(default=0, description="Total number of steps")
    progress_percent: float = Field(default=0.0, description="Completion percentage (0-100)")
    message: str = Field(default="", description="Human-readable progress message")
    started_at: Optional[datetime] = Field(default=None, description="When the task started")
    completed_at: Optional[datetime] = Field(default=None, description="When the task completed")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    result: Optional[dict] = Field(default=None, description="Task result when completed")
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "task_abc123",
                "task_type": "community_detection",
                "status": "running",
                "progress_current": 5,
                "progress_total": 10,
                "progress_percent": 50.0,
                "message": "Generating summaries for community 5/10..."
            }
        }


class CommunityDetectionTaskRequest(BaseModel):
    """Request to start a community detection task."""
    min_size: int = Field(default=3, ge=2, le=20, description="Minimum community size")
    collection_id: Optional[str] = Field(default=None, description="Scope to collection")


# =============================================================================
# API Key Management
# =============================================================================

class APIKeyPermission(str, Enum):
    """Permission levels for API keys."""
    READ = "read"      # Can use Ask AI, search, view graphs
    MANAGE = "manage"  # Can upload, edit, delete documents and collections


class APIKey(BaseModel):
    """An API key for accessing the backend."""
    id: str = Field(..., description="Unique API key identifier")
    name: str = Field(..., description="Human-readable name for the key")
    key_prefix: str = Field(..., description="First 8 characters of the key for identification")
    key_hash: str = Field(..., description="Hashed API key (bcrypt)")
    permissions: List[APIKeyPermission] = Field(default_factory=list, description="List of permissions")
    is_active: bool = Field(default=True, description="Whether the key is active")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When the key was created")
    last_used_at: Optional[datetime] = Field(default=None, description="When the key was last used")
    created_by: str = Field(default="admin", description="Who created this key")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "key_abc123",
                "name": "Production Read-Only",
                "key_prefix": "moca_ro_",
                "permissions": ["read"],
                "is_active": True,
                "created_by": "admin"
            }
        }


class CreateAPIKeyRequest(BaseModel):
    """Request model for creating a new API key."""
    name: str = Field(..., min_length=1, max_length=100, description="Name for the API key")
    permissions: List[APIKeyPermission] = Field(..., min_length=1, description="Permissions to grant")
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Frontend Read-Only Key",
                "permissions": ["read"]
            }
        }


class CreateAPIKeyResponse(BaseModel):
    """Response model for API key creation - includes the actual key (shown only once)."""
    id: str = Field(..., description="API key ID")
    name: str = Field(..., description="API key name")
    key: str = Field(..., description="The actual API key - save this, it won't be shown again!")
    key_prefix: str = Field(..., description="Key prefix for identification")
    permissions: List[APIKeyPermission] = Field(..., description="Granted permissions")
    created_at: datetime = Field(..., description="Creation timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "key_abc123",
                "name": "Frontend Read-Only Key",
                "key": "moca_ro_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "key_prefix": "moca_ro_",
                "permissions": ["read"],
                "created_at": "2024-01-01T00:00:00Z"
            }
        }


class APIKeyListItem(BaseModel):
    """API key information for listing (without the actual key)."""
    id: str = Field(..., description="API key ID")
    name: str = Field(..., description="API key name")
    key_prefix: str = Field(..., description="Key prefix for identification")
    permissions: List[APIKeyPermission] = Field(..., description="Granted permissions")
    is_active: bool = Field(..., description="Whether the key is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    last_used_at: Optional[datetime] = Field(default=None, description="Last usage timestamp")
    created_by: str = Field(..., description="Who created this key")


class UpdateAPIKeyRequest(BaseModel):
    """Request model for updating an API key."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100, description="New name")
    permissions: Optional[List[APIKeyPermission]] = Field(default=None, description="New permissions")
    is_active: Optional[bool] = Field(default=None, description="Activate or deactivate the key")
