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


class RAGRequest(BaseModel):
    """Request model for RAG-based question answering."""
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    use_graph: bool = Field(default=True, description="Whether to use graph-enhanced retrieval")
    max_hops: int = Field(default=2, ge=1, le=3, description="Max hops for graph traversal")


class RAGResponse(BaseModel):
    """Response model for RAG-based question answering."""
    question: str
    answer: str
    sources: list[SearchResult]
    graph_context: Optional[GraphContext] = None


class GraphStatsResponse(BaseModel):
    """Response model for graph statistics."""
    document_count: int
    chunk_count: int
    entity_count: int
    relationship_count: int
    total_size: int


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
