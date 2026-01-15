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
# Collection-Level Knowledge Graphs (R2R-style)
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
# Community Detection & Summarization (R2R-style)
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
# Extended Thinking / Reasoning Visibility (R2R-style)
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
