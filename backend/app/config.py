from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Neo4j Configuration
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"
    
    # OpenAI / LiteLLM Configuration
    openai_api_key: str = ""
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "openai/minimax-m21"
    
    # Upload Configuration
    upload_dir: str = "./uploads"
    max_file_size_mb: int = 50
    allowed_extensions: list[str] = [".pdf", ".txt", ".md", ".docx", ".xlsx"]
    
    # Embedding Configuration
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimension: int = 1536  # text-embedding-3-small native dimension
    use_openai_embeddings: bool = True
    
    # Chunking Configuration
    chunk_size: int = 500
    chunk_overlap: int = 50
    
    # GraphRAG Configuration
    enable_graph_extraction: bool = True  # Enable LLM-based entity/relationship extraction
    graph_extraction_model: str = ""  # Model for extraction (defaults to openai_model if empty)
    max_graph_hops: int = 2  # Maximum hops for graph traversal in queries
    concurrent_extractions: int = 20  # Number of chunks to process concurrently for graph extraction
    
    # Enhanced RAG Configuration
    enable_reranking: bool = True  # Enable cross-encoder reranking
    reranking_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # Cross-encoder model
    enable_hybrid_search: bool = True  # Enable hybrid (vector + keyword) search
    vector_weight: float = 0.5  # Weight for vector search in hybrid
    keyword_weight: float = 0.3  # Weight for keyword search in hybrid
    graph_weight: float = 0.2  # Weight for graph context in hybrid
    max_conversation_history: int = 6  # Max messages to include from conversation
    enable_agentic_rag: bool = True  # Enable multi-step agentic RAG
    max_agentic_steps: int = 3  # Maximum steps in agentic RAG
    
    # Chunking Configuration (enhanced)
    chunk_by: str = "sentence"  # "word" or "sentence" based splitting
    sentences_per_chunk: int = 5  # Sentences per chunk when using sentence splitting
    
    @property
    def extraction_model(self) -> str:
        """Get the model to use for graph extraction."""
        return self.graph_extraction_model or self.openai_model
    
    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
