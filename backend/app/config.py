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
    openai_model: str = "gpt-4o-mini"
    
    # Upload Configuration
    upload_dir: str = "./uploads"
    max_file_size_mb: int = 50
    allowed_extensions: list[str] = [".pdf", ".txt", ".md", ".docx", ".xlsx"]
    
    # Embedding Configuration (Neo4j max 2048 dimensions)
    embedding_model: str = "text-embedding-3-large"
    embedding_dimension: int = 2048  # text-embedding-3-large reduced to fit Neo4j limit
    use_openai_embeddings: bool = True
    
    # Chunking Configuration
    chunk_size: int = 500
    chunk_overlap: int = 50
    
    # GraphRAG Configuration
    enable_graph_extraction: bool = True  # Enable LLM-based entity/relationship extraction
    graph_extraction_model: str = ""  # Model for extraction (defaults to openai_model if empty)
    max_graph_hops: int = 2  # Maximum hops for graph traversal in queries
    
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
