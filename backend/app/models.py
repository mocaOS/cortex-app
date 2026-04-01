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
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="LLM confidence that this relationship is real (0-1)")

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
# Entity Management
# =============================================================================

class UpdateEntityRequest(BaseModel):
    """Request model for updating an entity's name and/or description."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=500, description="New entity name")
    description: Optional[str] = Field(default=None, max_length=5000, description="New entity description")


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
    source: str = Field(default="upload", description="Origin of the document (e.g. 'upload', 'custom_input', or a custom source set via API)")
    # Progress tracking fields
    progress_current: int = Field(default=0, description="Current step in processing")
    progress_total: int = Field(default=0, description="Total steps in processing")
    progress_message: str = Field(default="", description="Human-readable progress message")
    # Image analysis progress
    image_progress_current: int = Field(default=0, description="Number of images analyzed so far")
    image_progress_total: int = Field(default=0, description="Total images to analyze")
    image_progress_message: str = Field(default="", description="Human-readable image analysis progress")


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
    collection_id: Optional[str] = Field(default=None, description="Collection ID to scope the search to (None = all collections)")


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
    per_chunk_relationship_count: int = Field(default=0, description="Relationships discovered within documents (Step 1)")
    total_size: int
    # Enhanced stats
    community_count: int = Field(default=0, description="Number of detected communities")
    collection_count: int = Field(default=0, description="Number of collections")
    pending_count: int = Field(default=0, description="Number of documents pending processing")
    # Additional KPIs
    completed_count: int = Field(default=0, description="Number of successfully processed documents")
    failed_count: int = Field(default=0, description="Number of failed documents")
    processing_count: int = Field(default=0, description="Number of currently processing documents")
    avg_chunks_per_doc: float = Field(default=0.0, description="Average chunks per completed document")
    entity_type_counts: dict = Field(default_factory=dict, description="Entity counts by type")
    avg_entity_mentions: float = Field(default=0.0, description="Average mention count per entity")
    last_relationship_analysis_at: Optional[str] = Field(default=None, description="ISO timestamp of last relationship analysis")
    last_community_detection_at: Optional[str] = Field(default=None, description="ISO timestamp of last community detection")
    last_entity_merge_at: Optional[str] = Field(default=None, description="ISO timestamp of last entity merge/deduplication")
    # Relationship health metrics
    entity_relationship_ratio: float = Field(default=0.0, description="Current relationships-per-entity ratio")
    relationship_target_ratio: float = Field(default=3.0, description="Target relationships-per-entity ratio from config")


class UploadResponse(BaseModel):
    """Response model for file upload."""
    document_id: str
    filename: str
    status: ProcessingStatus
    message: str
    source: str = Field(default="upload", description="Origin of the document")


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
    source: Optional[str] = Field(default=None, description="Custom source identifier (defaults to 'custom_input' if not set)")
    
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
    source: str = Field(default="custom_input", description="Origin of the document")


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


# =============================================================================
# API Key Usage Statistics
# =============================================================================

class APIKeyStats(BaseModel):
    """Usage statistics for an API key."""
    total_requests: int = Field(default=0, description="Total API calls made with this key")
    requests_today: int = Field(default=0, description="Requests in current day (UTC)")
    requests_this_week: int = Field(default=0, description="Requests in current week")
    requests_this_month: int = Field(default=0, description="Requests in current month")
    error_count: int = Field(default=0, description="Total errors encountered")
    last_error_at: Optional[datetime] = Field(default=None, description="When last error occurred")
    last_error_message: Optional[str] = Field(default=None, description="Last error message")
    endpoint_breakdown: dict = Field(default_factory=dict, description="Request counts by endpoint category")
    
    class Config:
        json_schema_extra = {
            "example": {
                "total_requests": 1234,
                "requests_today": 45,
                "requests_this_week": 312,
                "requests_this_month": 890,
                "error_count": 5,
                "last_error_at": "2024-01-15T10:30:00Z",
                "last_error_message": "Rate limit exceeded",
                "endpoint_breakdown": {
                    "ask": 500,
                    "search": 300,
                    "upload": 50,
                    "documents": 200,
                    "graph": 100,
                    "other": 84
                }
            }
        }


class APIKeyUsageDataPoint(BaseModel):
    """A single data point for usage history charts."""
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    requests: int = Field(default=0, description="Request count for this date")
    errors: int = Field(default=0, description="Error count for this date")
    
    class Config:
        json_schema_extra = {
            "example": {
                "date": "2024-01-15",
                "requests": 156,
                "errors": 2
            }
        }


class APIKeyWithStats(APIKeyListItem):
    """API key with usage statistics attached."""
    stats: Optional[APIKeyStats] = Field(default=None, description="Usage statistics")


class APIKeyUsageHistoryResponse(BaseModel):
    """Response model for API key usage history."""
    key_id: str = Field(..., description="API key ID")
    key_name: str = Field(..., description="API key name")
    history: List[APIKeyUsageDataPoint] = Field(default_factory=list, description="Daily usage data")
    period_days: int = Field(default=30, description="Number of days in history")


class AdminStatsOverview(BaseModel):
    """Aggregated statistics across all API keys for admin dashboard."""
    total_keys: int = Field(default=0, description="Total number of API keys")
    active_keys: int = Field(default=0, description="Number of active API keys")
    total_requests_all_time: int = Field(default=0, description="Total requests across all keys")
    total_requests_today: int = Field(default=0, description="Total requests today across all keys")
    total_requests_this_week: int = Field(default=0, description="Total requests this week across all keys")
    total_requests_this_month: int = Field(default=0, description="Total requests this month across all keys")
    total_errors: int = Field(default=0, description="Total errors across all keys")
    most_active_key: Optional[str] = Field(default=None, description="Name of the most active key")
    endpoint_breakdown: dict = Field(default_factory=dict, description="Aggregated endpoint usage")
    
    class Config:
        json_schema_extra = {
            "example": {
                "total_keys": 5,
                "active_keys": 4,
                "total_requests_all_time": 15000,
                "total_requests_today": 234,
                "total_requests_this_week": 1567,
                "total_requests_this_month": 5890,
                "total_errors": 45,
                "most_active_key": "Production API Key",
                "endpoint_breakdown": {
                    "ask": 8000,
                    "search": 4000,
                    "upload": 500,
                    "documents": 1500,
                    "graph": 800,
                    "other": 200
                }
            }
        }


# =============================================================================
# System Reset
# =============================================================================

# =============================================================================
# Agent Skills (agentskills.io standard)
# =============================================================================

class SkillInfo(BaseModel):
    """Skill metadata for API responses."""
    skill_id: str = Field(..., description="Unique skill identifier (directory name or namespace/name)")
    name: str = Field(..., description="Skill name from SKILL.md frontmatter")
    description: str = Field(..., description="What this skill does and when to use it")
    version: Optional[str] = Field(default=None, description="Skill version from metadata")
    author: Optional[str] = Field(default=None, description="Skill author from metadata")
    license: Optional[str] = Field(default=None, description="Skill license")
    source: str = Field(..., description="Installation source: local, registry, or url")
    source_url: Optional[str] = Field(default=None, description="URL the skill was installed from")
    skill_type: str = Field(..., description="Skill type: instruction or tool")
    enabled: bool = Field(default=False, description="Whether the skill is active")
    installed_at: str = Field(..., description="ISO 8601 installation timestamp")
    tool_count: int = Field(default=0, description="Number of tools this skill provides")
    tool_names: List[str] = Field(default_factory=list, description="Names of tools this skill provides")


class SkillDetail(SkillInfo):
    """Skill with full SKILL.md body and tools configuration."""
    body: str = Field(default="", description="Full SKILL.md markdown body (instructions)")
    tools_config: Optional[List[dict]] = Field(default=None, description="Parsed tools.json definitions")


class SkillInstallRequest(BaseModel):
    """Request to install a skill from URL or registry."""
    url: Optional[str] = Field(default=None, description="Direct URL to SKILL.md or ZIP archive")
    registry_id: Optional[str] = Field(default=None, description="Registry identifier: namespace/name from skills.sh")


class SkillUpdateRequest(BaseModel):
    """Request to update skill settings."""
    enabled: Optional[bool] = Field(default=None, description="Enable or disable the skill")


class SkillRegistryItem(BaseModel):
    """A skill from the skills.sh registry."""
    namespace: str = Field(..., description="Skill namespace/owner")
    name: str = Field(..., description="Skill name")
    description: str = Field(default="", description="Skill description")
    install_count: Optional[int] = Field(default=None, description="Installation count")
    download_url: str = Field(..., description="URL to fetch skill content")


class SystemResetRequest(BaseModel):
    """Request model for system reset with selective deletion options."""
    delete_documents: bool = Field(default=True, description="Delete all documents, chunks, entities, and communities")
    delete_uploaded_files: bool = Field(default=True, description="Delete uploaded files from disk")
    delete_custom_inputs: bool = Field(default=True, description="Delete custom input files from disk")
    delete_collections: bool = Field(default=True, description="Delete all non-default collections")
    delete_api_keys: bool = Field(default=False, description="Delete all API keys (dangerous - defaults to false)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "delete_documents": True,
                "delete_uploaded_files": True,
                "delete_custom_inputs": True,
                "delete_collections": True,
                "delete_api_keys": False
            }
        }


class SystemResetResponse(BaseModel):
    """Response model for system reset operation."""
    message: str = Field(..., description="Summary message")
    documents_deleted: int = Field(default=0, description="Number of documents deleted")
    entities_removed: int = Field(default=0, description="Number of entities removed")
    communities_removed: int = Field(default=0, description="Number of communities removed")
    merge_history_deleted: int = Field(default=0, description="Number of merge history records deleted")
    system_meta_deleted: int = Field(default=0, description="Number of system metadata records deleted")
    collections_deleted: int = Field(default=0, description="Number of collections deleted")
    api_keys_deleted: int = Field(default=0, description="Number of API keys deleted")
    uploaded_files_deleted: int = Field(default=0, description="Number of uploaded files deleted")
    custom_inputs_deleted: int = Field(default=0, description="Number of custom input files deleted")
    processing_cancelled: int = Field(default=0, description="Number of processing tasks cancelled")


# =============================================================================
# Library Import/Export
# =============================================================================

class LibraryExportManifest(BaseModel):
    """Manifest for a library export archive."""
    version: str = Field(default="1.0", description="Export format version")
    export_date: str = Field(..., description="ISO 8601 export timestamp")
    embedding_model: str = Field(..., description="Embedding model used")
    embedding_dimension: int = Field(..., description="Embedding vector dimension")
    stats: dict = Field(default_factory=dict, description="Counts of exported items")


class LibraryImportResult(BaseModel):
    """Result of a library import operation."""
    documents_imported: int = Field(default=0)
    chunks_imported: int = Field(default=0)
    entities_imported: int = Field(default=0)
    relationships_imported: int = Field(default=0)
    communities_imported: int = Field(default=0)
    collections_imported: int = Field(default=0)
    files_imported: int = Field(default=0)
    merge_history_imported: int = Field(default=0)
    embedding_compatible: bool = Field(default=True)
    warnings: List[str] = Field(default_factory=list)


# =============================================================================
# System Configuration (Safe to expose - no secrets)
# =============================================================================

class SystemConfigResponse(BaseModel):
    """System configuration response - excludes sensitive data like API keys and passwords."""
    
    # LLM Configuration
    openai_model: str = Field(..., description="Primary LLM model")
    openai_api_base: str = Field(..., description="Primary LLM API base URL")
    extraction_model: str = Field(..., description="Model used for entity/relationship extraction")
    extraction_api_base: str = Field(..., description="Extraction model API base URL")
    extraction_max_context: int = Field(..., description="Max context window tokens for entity extraction")
    relationship_max_context: int = Field(..., description="Max context window tokens for relationship analysis")
    parallel_relationship_batches: int = Field(..., description="Number of relationship batches processed in parallel")
    relationship_target_ratio: float = Field(default=3.0, description="Target relationships-per-entity ratio")
    relationship_max_rounds: int = Field(default=1, description="Max auto-discovery rounds per analysis run")
    relationship_max_hours: float = Field(default=0, description="Max hours for relationship generation (0 = no limit)")

    # Relationship Extraction Model
    relationship_model: str = Field(..., description="Model used for per-chunk relationship extraction")
    relationship_api_base: str = Field(..., description="Relationship extraction model API base URL")
    concurrent_relations: int = Field(..., description="Concurrent per-chunk relationship extractions per document")

    # Vision Model
    vision_model_available: bool = Field(..., description="Whether a vision model is configured")
    vision_model: str = Field(..., description="Vision model name for image analysis")
    vision_api_base: str = Field(..., description="Vision model API base URL")
    vision_max_concurrent: int = Field(..., description="Max concurrent vision API calls")

    # Embedding Configuration
    embedding_model: str = Field(..., description="Embedding model")
    embedding_dimension: int = Field(..., description="Embedding vector dimension")
    embedding_api_base: str = Field(..., description="Embedding API base URL")
    embedding_send_dimensions: bool = Field(..., description="Whether dimensions param is sent to embedding API")
    use_openai_embeddings: bool = Field(..., description="Whether OpenAI embeddings are enabled")

    # Upload Configuration
    max_file_size_mb: int = Field(..., description="Maximum file size in MB")
    allowed_extensions: List[str] = Field(..., description="Allowed file extensions")
    
    # Chunking Configuration
    chunk_size: int = Field(..., description="Chunk size in tokens/words")
    chunk_overlap: int = Field(..., description="Overlap between chunks")
    chunk_by: str = Field(..., description="Chunking method: word or sentence")
    sentences_per_chunk: int = Field(..., description="Sentences per chunk when using sentence splitting")
    
    # GraphRAG Configuration
    enable_graph_extraction: bool = Field(..., description="Whether graph extraction is enabled")
    max_graph_hops: int = Field(..., description="Maximum hops for graph traversal")
    concurrent_extractions: int = Field(..., description="Concurrent chunk extractions")
    
    # Batch Processing
    batch_processing_concurrency: int = Field(..., description="Batch processing concurrency limit")
    processing_thread_workers: int = Field(..., description="Thread pool workers")
    
    # Enhanced RAG Configuration
    enable_reranking: bool = Field(..., description="Whether reranking is enabled")
    reranking_model: str = Field(..., description="Cross-encoder reranking model")
    enable_hybrid_search: bool = Field(..., description="Whether hybrid search is enabled")
    vector_weight: float = Field(..., description="Vector search weight in hybrid")
    keyword_weight: float = Field(..., description="Keyword search weight in hybrid")
    graph_weight: float = Field(..., description="Graph context weight in hybrid")
    max_conversation_history: int = Field(..., description="Max conversation messages")
    enable_agentic_rag: bool = Field(..., description="Whether agentic RAG is enabled")
    max_agentic_steps: int = Field(..., description="Maximum agentic RAG steps")
    
    # Community Detection
    enable_community_detection: bool = Field(..., description="Whether community detection is enabled")
    min_community_size: int = Field(..., description="Minimum community size")
    max_communities: int = Field(..., description="Maximum number of communities")
    enable_graph_summarization: bool = Field(..., description="Whether graph summarization is enabled")
    
    # Entity Resolution
    enable_semantic_entity_resolution: bool = Field(..., description="Whether semantic entity resolution is enabled")
    entity_similarity_threshold: float = Field(..., description="Entity similarity threshold")
    
    # Collections
    enable_collections: bool = Field(..., description="Whether collections are enabled")
    default_collection: str = Field(..., description="Default collection name")
    
    # Visibility/UX
    stream_reasoning_steps: bool = Field(..., description="Whether to stream reasoning steps")
    show_retrieval_stats: bool = Field(..., description="Whether to show retrieval stats")
    
    # Security
    prompt_security: bool = Field(..., description="Whether prompt security is enabled")
    
    # Turbo Mode (Compute3)
    turbo_mode_available: bool = Field(..., description="Whether turbo mode is available")
    compute3_gpu_type: str = Field(..., description="GPU type for turbo mode")
    compute3_gpu_count: int = Field(..., description="Number of GPUs for turbo mode")
    compute3_model: str = Field(..., description="Model for turbo mode")
    compute3_default_runtime: int = Field(..., description="Default runtime in seconds")

    # Agent Skills
    enable_skills: bool = Field(default=False, description="Whether agent skills are enabled")
    enable_skill_scripts: bool = Field(default=False, description="Whether skill script execution is allowed")
    max_skill_tools: int = Field(default=10, description="Max skill tools in researcher agent")

    class Config:
        json_schema_extra = {
            "example": {
                "openai_model": "gpt-4o-mini",
                "extraction_model": "gpt-4o-mini",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 1536,
                "use_openai_embeddings": True,
                "max_file_size_mb": 50,
                "allowed_extensions": [".pdf", ".txt", ".md"],
                "chunk_size": 500,
                "chunk_overlap": 50,
                "chunk_by": "sentence",
                "sentences_per_chunk": 5,
                "enable_graph_extraction": True,
                "max_graph_hops": 2,
                "concurrent_extractions": 20,
                "batch_processing_concurrency": 10,
                "processing_thread_workers": 4,
                "enable_reranking": True,
                "reranking_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "enable_hybrid_search": True,
                "vector_weight": 0.5,
                "keyword_weight": 0.3,
                "graph_weight": 0.2,
                "max_conversation_history": 6,
                "enable_agentic_rag": True,
                "max_agentic_steps": 3,
                "enable_community_detection": True,
                "min_community_size": 3,
                "max_communities": 50,
                "enable_graph_summarization": True,
                "enable_semantic_entity_resolution": True,
                "entity_similarity_threshold": 0.85,
                "enable_collections": True,
                "default_collection": "default",
                "stream_reasoning_steps": True,
                "show_retrieval_stats": True,
                "prompt_security": True,
                "turbo_mode_available": False,
                "compute3_gpu_type": "h100",
                "compute3_gpu_count": 4,
                "compute3_model": "MiniMaxAI/MiniMax-M2.1",
                "compute3_default_runtime": 3600
            }
        }
