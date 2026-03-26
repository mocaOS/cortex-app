import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str | None:
    """Find .env file by checking multiple possible locations."""
    possible_paths = [
        Path(".env"),  # Current directory
        Path(__file__).parent.parent.parent / ".env",  # backend/.env
        Path(__file__).parent.parent.parent.parent / ".env",  # project root
        Path("/app/.env"),  # Docker container
    ]
    for path in possible_paths:
        if path.exists():
            return str(path)
    return None


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Environment variables take precedence over .env file values.
    All settings can be overridden via environment variables using
    uppercase names (e.g., NEO4J_URI, OPENAI_API_KEY).
    """

    # Neo4j Configuration
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="password123")

    # OpenAI / LiteLLM Configuration
    openai_api_key: str = Field(default="")
    openai_api_base: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="openai/minimax-m21")
    openai_model_fast_mode: str = Field(
        default=""
    )  # Model for "Fast Mode" in Ask AI (defaults to openai_model if empty)

    # Vision Model Configuration (for image analysis)
    vision_model: str = Field(
        default=""
    )  # Model for image analysis (e.g., "gpt-4o", "claude-3-5-sonnet"). If empty, uses docling's built-in capabilities.
    vision_model_api_base: str = Field(
        default=""
    )  # API base URL for vision model (defaults to openai_api_base if empty)
    vision_model_api_key: str = Field(
        default=""
    )  # API key for vision model (defaults to openai_api_key if empty)
    vision_max_concurrent: int = Field(
        default=3
    )  # Max concurrent vision API calls system-wide (controls semaphore + thread pool sizing)

    # Upload Configuration
    upload_dir: str = Field(default="./uploads")
    custom_inputs_dir: str = Field(
        default="./custom_inputs"
    )  # Separate folder for manually entered content
    max_file_size_mb: int = Field(default=50)
    allowed_extensions: list[str] = Field(
        default=[
            ".pdf",
            ".docx",
            ".doc",
            ".xlsx",
            ".xls",
            ".pptx",
            ".ppt",  # Office documents
            ".html",
            ".htm",  # Web pages
            ".txt",
            ".md",
            ".mdx",
            ".markdown",
            ".rst",  # Text files
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".bmp",  # Images (OCR)
            ".wav",
            ".mp3",
            ".webvtt",
            ".vtt",  # Audio (ASR)
            ".tex",
            ".latex",  # LaTeX
            ".xml",  # XML schemas (USPTO, JATS, XBRL)
        ]
    )

    # Resource Limits (0 = unlimited)
    max_files: int = Field(
        default=0
    )  # Max total documents (uploads + custom inputs). 0 = unlimited
    max_collections: int = Field(
        default=0
    )  # Max collections (default collection counts as 1). 0 = unlimited

    # Embedding Configuration
    embedding_model: str = Field(default="openai/text-embedding-3-small")
    embedding_dimension: int = Field(
        default=1536
    )  # text-embedding-3-small native dimension
    use_openai_embeddings: bool = Field(default=True)
    embedding_send_dimensions: bool = Field(
        default=True
    )  # Send dimensions param to embedding API. Set False for models with fixed output dim (e.g. qwen3-vl-embedding-2b)
    embedding_api_base: str = Field(
        default=""
    )  # API base URL for embeddings (defaults to openai_api_base if empty)
    embedding_api_key: str = Field(
        default=""
    )  # API key for embeddings (defaults to openai_api_key if empty)

    # Chunking Configuration
    chunk_size: int = Field(default=500)
    chunk_overlap: int = Field(default=50)

    # GraphRAG Configuration
    enable_graph_extraction: bool = Field(
        default=True
    )  # Enable LLM-based entity/relationship extraction
    graph_extraction_model: str = Field(
        default=""
    )  # Model for extraction (defaults to openai_model if empty)
    graph_extraction_api_base: str = Field(
        default=""
    )  # API base for extraction model (defaults to openai_api_base if empty)
    graph_extraction_api_key: str = Field(
        default=""
    )  # API key for extraction model (defaults to openai_api_key if empty)
    max_graph_hops: int = Field(
        default=2
    )  # Maximum hops for graph traversal in queries
    concurrent_extractions: int = Field(
        default=3
    )  # Number of chunks to process concurrently for graph extraction

    # Extraction Context Window Configuration
    extraction_max_context: int = Field(
        default=32768
    )  # Max context window tokens for entity extraction batching
    relationship_max_context: int = Field(
        default=65536
    )  # Max context window tokens for relationship analysis INPUT batching
    relationship_max_output_tokens: int = Field(
        default=16000
    )  # Max output tokens for relationship analysis LLM responses

    # Batch Processing Configuration
    batch_processing_concurrency: int = Field(
        default=2
    )  # Number of documents to process concurrently in batch mode
    processing_thread_workers: int = Field(
        default=4
    )  # Thread pool workers for CPU-intensive operations

    # Relationship Analysis (Phase B - cross-document relationship discovery)
    relationship_analysis_batch_size: int = Field(
        default=100
    )  # Max entities per relationship analysis LLM call
    parallel_relationship_batches: int = Field(
        default=0
    )  # Number of relationship analysis batches to process in parallel (0 = use CONCURRENT_EXTRACTIONS)
    auto_relationship_analysis_after_batch: bool = Field(
        default=False
    )  # Auto-trigger relationship analysis after batch processing completes
    auto_community_detection_after_batch: bool = Field(
        default=False
    )  # Auto-trigger community detection after relationship analysis
    relationship_target_ratio: float = Field(
        default=1.0
    )  # Target relationships-per-entity ratio. Admins can use this to gauge if more rounds are needed.
    relationship_max_rounds: int = Field(
        default=3
    )  # Max auto-discovery rounds for initial analysis (1 = single pass, 2+ = multi-round until target ratio or limit). Re-analyze always does 1 round.
    relationship_max_hours: float = Field(
        default=0
    )  # Max hours for relationship generation (0 = no time limit, completes all rounds)
    relationship_max_per_entity: int = Field(
        default=50
    )  # Soft cap on relationships per entity during analysis. 0 = no cap.

    # Enhanced RAG Configuration
    enable_reranking: bool = Field(default=True)  # Enable cross-encoder reranking
    reranking_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2"
    )  # Cross-encoder model
    enable_hybrid_search: bool = Field(
        default=True
    )  # Enable hybrid (vector + keyword) search
    vector_weight: float = Field(default=0.5)  # Weight for vector search in hybrid
    keyword_weight: float = Field(default=0.3)  # Weight for keyword search in hybrid
    graph_weight: float = Field(default=0.2)  # Weight for graph context in hybrid
    max_conversation_history: int = Field(
        default=6
    )  # Max messages to include from conversation
    enable_agentic_rag: bool = Field(default=True)  # Enable multi-step agentic RAG
    max_agentic_steps: int = Field(default=3)  # Maximum steps in agentic RAG (legacy)

    # Agent-based research pipeline (researcher/writer architecture)
    enable_agent_research: bool = Field(
        default=True
    )  # Use agent pipeline for research mode (vs legacy fixed pipeline)
    enable_agent_chat: bool = Field(
        default=False
    )  # Use agent pipeline for standard chat mode
    researcher_max_iterations_speed: int = Field(
        default=2
    )  # Max agent loop iterations in speed/chat mode
    researcher_max_iterations_quality: int = Field(
        default=10
    )  # Max agent loop iterations in quality/research mode
    writer_max_tokens_speed: int = Field(
        default=1200
    )  # Max output tokens for writer in speed mode
    writer_max_tokens_quality: int = Field(
        default=4000
    )  # Max output tokens for writer in quality/research mode

    # Chunking Configuration (enhanced)
    chunk_by: str = Field(default="sentence")  # "word" or "sentence" based splitting
    sentences_per_chunk: int = Field(
        default=5
    )  # Sentences per chunk when using sentence splitting

    # ==========================================================================
    # Community Detection & Graph Summarization
    # ==========================================================================
    enable_community_detection: bool = Field(
        default=True
    )  # Enable entity community detection
    min_community_size: int = Field(default=3)  # Minimum entities for a valid community
    max_communities: int = Field(default=50)  # Maximum number of communities to track
    enable_graph_summarization: bool = Field(
        default=True
    )  # Generate LLM summaries of communities
    community_summary_model: str = Field(
        default=""
    )  # Model for summaries (defaults to openai_model)

    # ==========================================================================
    # Enhanced Entity Resolution (Semantic Similarity)
    # ==========================================================================
    enable_semantic_entity_resolution: bool = Field(
        default=True
    )  # Use embeddings for entity matching
    entity_similarity_threshold: float = Field(
        default=0.85
    )  # Threshold for entity deduplication
    entity_embedding_model: str = Field(
        default=""
    )  # Model for entity embeddings (defaults to embedding_model)

    # ==========================================================================
    # Collection-Level Graphs
    # ==========================================================================
    enable_collections: bool = Field(
        default=True
    )  # Enable collection-based organization
    default_collection: str = Field(
        default="default"
    )  # Default collection name for documents

    # ==========================================================================
    # Extended Thinking / Reasoning Visibility
    # ==========================================================================
    stream_reasoning_steps: bool = Field(
        default=True
    )  # Stream reasoning steps in agentic mode
    show_retrieval_stats: bool = Field(
        default=True
    )  # Show retrieval statistics in responses

    # ==========================================================================
    # Prompt Security (protection against prompt injection attacks)
    # ==========================================================================
    prompt_security: bool = Field(
        default=True
    )  # Enable prompt injection detection and protection

    # ==========================================================================
    # Admin Authentication
    # ==========================================================================
    admin_email: str = Field(default="admin@example.com")  # Admin login email
    admin_password: str = Field(default="")  # Admin login password (required for auth)
    admin_api_key: str = Field(default="")  # Admin API key for full backend access
    session_secret: str = Field(
        default=""
    )  # Secret for JWT session encryption (min 32 chars)
    track_admin_api_key_usage: bool = Field(
        default=False
    )  # Track usage analytics for admin API key

    # ==========================================================================
    # Compute3 Turbo Mode Configuration
    # ==========================================================================
    compute3_api_key: str = Field(default="")  # Compute3 API key for turbo mode
    compute3_api_base: str = Field(
        default="https://api.compute3.ai"
    )  # Compute3 API base URL
    compute3_gpu_type: str = Field(default="h100")  # GPU type for turbo mode jobs
    compute3_gpu_count: int = Field(default=4)  # Number of GPUs for turbo mode
    compute3_model: str = Field(
        default="MiniMaxAI/MiniMax-M2.1"
    )  # Model to run on Compute3 (HuggingFace model ID)
    compute3_docker_image: str = Field(
        default="vllm/vllm-openai:nightly"
    )  # Docker image for vLLM (nightly required for MiniMax-M2.1)
    compute3_default_runtime: int = Field(
        default=3600
    )  # Default job runtime in seconds (1 hour)

    @property
    def turbo_mode_available(self) -> bool:
        """Check if turbo mode is available (Compute3 API key is set)."""
        return bool(self.compute3_api_key)

    @property
    def vision_model_available(self) -> bool:
        """Check if a vision model is configured."""
        return bool(self.vision_model)

    @property
    def vision_model_config(self) -> tuple[str, str, str]:
        """Get vision model configuration as (api_key, base_url, model).

        Falls back to default OpenAI settings if vision-specific settings are not configured.
        """
        api_key = self.vision_model_api_key or self.openai_api_key
        base_url = self.vision_model_api_base or self.openai_api_base
        model = self.vision_model
        return (api_key, base_url, model)

    @property
    def fast_mode_model(self) -> str:
        """Get the model to use for Fast Mode in Ask AI."""
        return self.openai_model_fast_mode or self.openai_model

    @property
    def extraction_model(self) -> str:
        """Get the model to use for graph extraction."""
        return self.graph_extraction_model or self.openai_model

    @property
    def extraction_api_base(self) -> str:
        """Get the API base URL for graph extraction."""
        return self.graph_extraction_api_base or self.openai_api_base

    @property
    def extraction_api_key(self) -> str:
        """Get the API key for graph extraction."""
        return self.graph_extraction_api_key or self.openai_api_key

    @property
    def summary_model(self) -> str:
        """Get the model to use for community summarization."""
        return self.community_summary_model or self.openai_model

    @property
    def embed_api_base(self) -> str:
        """Get the API base URL for embeddings."""
        return self.embedding_api_base or self.openai_api_base

    @property
    def embed_api_key(self) -> str:
        """Get the API key for embeddings."""
        return self.embedding_api_key or self.openai_api_key

    @property
    def entity_embed_model(self) -> str:
        """Get the model to use for entity embeddings."""
        return self.entity_embedding_model or self.embedding_model

    @model_validator(mode="before")
    @classmethod
    def _empty_str_to_default(cls, values):
        """Drop empty-string env vars so field defaults apply."""
        if isinstance(values, dict):
            return {k: v for k, v in values.items() if v != ""}
        return values

    # Pydantic v2 configuration
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,  # Allow both NEO4J_URI and neo4j_uri
        extra="ignore",  # Ignore extra env vars not in the model
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
