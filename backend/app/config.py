import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Module-level guard so the deprecation WARN fires exactly once per process,
# not on every Settings() construction (e.g. test fixtures building fresh
# instances). Reset via _reset_deprecation_warnings_for_tests().
_warned_legacy_env_aliases: set[str] = set()


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

    # Deployment environment. Set ENVIRONMENT=production to enforce secret
    # hardening at startup (see _enforce_production_secrets).
    environment: str = Field(default="development")

    # Interactive API docs (/docs, /redoc, /openapi.json). "auto" (default)
    # enables them in development and disables them in production to avoid
    # unauthenticated API-schema disclosure on a directly-exposed backend.
    # Set EXPOSE_API_DOCS=true/false to force either way.
    expose_api_docs: str = Field(default="auto")

    # Append-only JSONL audit trail for compliance review: authentication
    # failures, key-attributed mutating requests (uploads, deletions, config
    # changes, key CRUD), search/ask activity. Metadata only — never document
    # content or query text. See services/audit_log.py.
    enable_audit_log: bool = Field(default=False)
    audit_log_path: str = Field(default="./logs/audit.log")

    # Comma-separated list of allowed CORS origins (e.g.
    # "https://app.example.com,https://admin.example.com"). The default "*"
    # allows any origin but, per the CORS spec, only without credentials —
    # which is safe here because all auth is header-based (X-API-Key).
    cors_allowed_origins: str = Field(default="*")

    # Neo4j Configuration
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="password123")
    # Driver pool tuning. Defaults match the neo4j-python-driver defaults
    # (pool size 100, acquisition timeout 60s) except connection_timeout,
    # which the driver leaves at 30s — 10s fails faster when the DB is down.
    neo4j_max_pool_size: int = Field(default=100)
    neo4j_connection_timeout: float = Field(default=10.0)
    neo4j_connection_acquisition_timeout: float = Field(default=60.0)

    # OpenAI / LiteLLM Configuration
    openai_api_key: str = Field(default="")
    openai_api_base: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="google-gemma-4-26b-a4b-it")
    openai_model_fast_mode: str = Field(
        default=""
    )  # Model for "Fast Mode" in Ask AI (defaults to openai_model if empty)

    # Primary token budgets — sub-tier fields default to 0 (=inherit from primary).
    # This lets users configure a 3-model stack with just OPENAI_MODEL +
    # GRAPH_EXTRACTION_MODEL set and inherit context/output budgets downwards.
    # Default 8000 is generous enough that verbose-XML models (Qwen3-family)
    # don't truncate their <relationship> output. Models that finish much
    # earlier (Mistral, GPT-OSS) simply use less of the cap — no cost penalty.
    openai_max_output_tokens: int = Field(default=8000)
    # 256k matches the recommended primary model (Gemma4 26B A4B), which is
    # the retrieval agent's working context. Extraction does NOT get this:
    # `extraction_max_context` clamps the inherited value at 48k (2026-07-09,
    # decided with Rene — raised from 32768 where it silently under-used the
    # chat model's window).
    openai_max_context: int = Field(default=256000)

    # Transport limits for every LLM client built by the llm_config factories.
    # The OpenAI SDK's default timeout is 600s — one hung provider connection
    # pins an extraction slot for 10 minutes. 360s still clears the slowest
    # legit ingestion call (a 16k-token relationship batch on a slow model);
    # for streaming the read component applies between chunks, so long chat
    # answers are unaffected. 0 restores the SDK default.
    llm_request_timeout_seconds: int = Field(default=360)
    llm_max_retries: int = Field(default=2)  # SDK default; -1 = leave unset

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
        default=2
    )  # Max concurrent vision API calls system-wide (controls semaphore + thread pool
    # sizing). Default 2: each in-flight image occupies up to 4 provider slots via its
    # call chain (vision → extraction → entity embed → chunk embed), and Venice-class
    # gateways cap ~20 concurrent requests — 3 caused 429 bursts alongside multi-doc
    # extraction (measured 2026-07-08).
    # Raw field: 0 = inherit through the chain (relationship → extraction → primary).
    # Property `vision_max_output_tokens` resolves the inheritance.
    vision_max_output_tokens_raw: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "vision_max_output_tokens_raw", "VISION_MAX_OUTPUT_TOKENS"
        ),
    )
    vision_min_image_side: int = Field(
        default=64
    )  # Skip vision-model analysis for images where min(width, height) is below this many pixels. PDFs often expose bullets/icons/separators as PictureItems; Venice (and most hosted vision APIs) reject sub-64px images with HTTP 400 "did not pass validation checks". Set 0 to disable the pre-filter and let the API decide.
    vision_max_image_side: int = Field(
        default=1568
    )  # Downscale images so the longer side fits this many pixels before sending to the vision model. Cortex extracts PDF pages at 2× DPI (2400×1700 typical) — without downscaling the base64 payload bloats into hundreds of KB. Customers on providers that tokenize the base64 payload as text (some LiteLLM/vLLM wrappers) saw 184K-token vision inputs blow past 192K context windows. 1568 matches Claude's recommended max side: high enough for OCR-grade text legibility, low enough to keep payloads under ~700 KB JPEG. Set 0 to disable downscaling.
    vision_jpeg_quality: int = Field(
        default=85
    )  # JPEG quality (1-95) used when encoding non-transparent images for the vision API. 85 is the standard quality/size sweet spot — visually near-lossless for documents while ~5–10× smaller than PNG. PNG is still used automatically for images with an alpha channel (RGBA mode).

    # Upload Configuration
    upload_dir: str = Field(default="./uploads")
    custom_inputs_dir: str = Field(
        default="./custom_inputs"
    )  # Separate folder for manually entered content
    max_file_size_mb: int = Field(default=50)
    max_request_body_mb: int = Field(
        default=32
    )  # Global request-body ceiling for all endpoints that are not file-upload routes. Enforced by BodySizeLimitMiddleware on both Content-Length and streamed bodies, so an oversized POST is rejected with 413 before it can pressure the container's memory. File-upload routes (/api/upload, reprocess) get max_file_size_mb + slack; library import gets max_import_body_mb. Set 0 to disable the middleware entirely.
    max_import_body_mb: int = Field(
        default=2048
    )  # Body ceiling for the library-import routes (/api/admin/import*). Import ZIPs stream to disk (not RAM), so this can be far above max_request_body_mb — it exists to stop a runaway/abusive upload from filling the disk. Set 0 for unlimited.
    min_free_disk_mb: int = Field(
        default=500
    )  # Free-space floor for the uploads filesystem. Uploads and library-import sessions are rejected with 507 when accepting them would leave less than this free — disk-full corrupts Neo4j checkpoints, so refusing new data early is strictly safer. 0 disables the guard.
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
    max_entities: int = Field(
        default=0
    )  # Max total entities (global). 0 = unlimited.
    max_queries_per_month: int = Field(
        default=0
    )  # Monthly quota in internal LLM COMPLETIONS (Q&A loop calls + document
    # processing/extraction calls; embeddings excluded), UTC calendar month,
    # instance-wide. Aligns quota consumption with inference cost. 0 = unlimited

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
    embedding_max_input_tokens: int = Field(
        default=5400
    )  # Per-input token cap before sending to embeddings endpoint. Default sits
    # under the nominal 8192 because gateways (Venice) validate the cap with
    # their OWN tokenizer, counting punctuation-heavy text ~1.2-1.4x higher
    # than cl100k (measured 2026-07-08: a 5,795-cl100k-token chunk rejected as
    # >8192). Raise explicitly for direct-OpenAI or long-context embed models
    # (e.g. text-embedding-qwen3-8b supports 32768). Oversized inputs are
    # sub-split/truncated client-side to avoid 400 "Input text exceeds the
    # maximum token limit" errors.

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
    relationship_extraction_model: str = Field(
        default=""
    )  # Model for per-chunk relationship extraction (defaults to extraction model if empty)
    relationship_extraction_api_base: str = Field(
        default=""
    )  # API base for relationship extraction model (defaults to extraction API base if empty)
    relationship_extraction_api_key: str = Field(
        default=""
    )  # API key for relationship extraction model (defaults to extraction API key if empty)

    # Observability (Langfuse) — optional. When public+secret+base_url are all
    # set, every LLM/embedding/vision call is traced and costed in Langfuse and
    # agentic flows are grouped into one trace per request. Leave empty to run
    # the exact same image untraced (no keys → tracing_active is False). In
    # multi-tenant deployments these are injected per-tenant by the control
    # plane. See .claude/domain/observability.md.
    langfuse_public_key: str = Field(default="")  # LANGFUSE_PUBLIC_KEY
    langfuse_secret_key: str = Field(default="")  # LANGFUSE_SECRET_KEY
    langfuse_base_url: str = Field(default="")  # LANGFUSE_BASE_URL e.g. https://langfuse.example.com
    langfuse_tracing_enabled: bool = Field(default=True)  # master off-switch even when keys are set
    langfuse_sample_rate: float = Field(default=1.0)  # 0.0–1.0; dial down on hot tenants
    # Langfuse "environment" segmentation. The control plane injects
    # LANGFUSE_TRACING_ENVIRONMENT=<tenant-slug> so each tenant's traces are
    # filterable by environment in a shared Langfuse project. Empty → fall back to
    # `environment` (production/development) at init time. Must be lowercase
    # alphanumeric with hyphens/underscores and not start with "langfuse" (the SDK
    # warns + ignores invalid values); the control plane sanitizes slugs to match.
    langfuse_tracing_environment: str = Field(default="")  # LANGFUSE_TRACING_ENVIRONMENT
    # Content logging mode. Default False → redact ALL user/model authored text
    # (prompts, completions, tool args/results, embedding inputs, vision text,
    # extraction text) before export via a client-side `mask` hook; only the
    # structure (roles, model/params, tool names + arg keys, tokens, cost,
    # latency, tags) reaches Langfuse. Set True to log full content for local
    # debugging. See .claude/domain/observability.md (content masking).
    langfuse_log_extended: bool = Field(default=False)  # LANGFUSE_LOG_EXTENDED

    # Error tracking (GlitchTip/Sentry) — optional. When SENTRY_DSN is set,
    # sentry-sdk initializes at startup (see services/error_tracking.py):
    # unhandled endpoint exceptions and ERROR-level logs from any code path
    # (API, background pipeline, docling worker) become GlitchTip events with
    # source context and a request_id tag. Leave empty to run the exact same
    # image untracked. GlitchTip speaks the Sentry protocol, hence the SENTRY_*
    # names.
    sentry_dsn: str = Field(default="")  # SENTRY_DSN e.g. https://<key>@glitchtip.example.com/2
    sentry_environment: str = Field(default="")  # SENTRY_ENVIRONMENT; empty → `environment`
    sentry_release: str = Field(default="")  # SENTRY_RELEASE (e.g. git SHA stamped at deploy)
    sentry_traces_sample_rate: float = Field(default=0.0)  # 0 = errors only; >0 samples perf transactions
    # Privacy (deny-by-default, mirroring LANGFUSE_LOG_EXTENDED): request
    # bodies carry authored content (ask questions, document text) and are
    # never attached unless raised to small/medium/always; PII (IPs, cookies,
    # user ids) requires the explicit opt-in.
    sentry_max_request_body_size: str = Field(default="never")  # never|small|medium|always
    sentry_send_default_pii: bool = Field(default=False)  # SENTRY_SEND_DEFAULT_PII

    # Reasoning Control for ingestion pipelines
    # Values: off | minimal | auto | low | medium | high
    # Defaults: extraction/relationship/vision OFF (reasoning hurts structured
    # extraction and image-description tasks). The chat/answer path (speed-mode
    # researcher loop + writer) also defaults OFF: on reasoning-capable models
    # (esp. Venice) hidden chain-of-thought streams in a separate
    # `reasoning_content` channel and adds 3–14s before the first answer token —
    # often blowing the request budget into empty/timeout answers. OFF (Venice
    # `disable_thinking`) cuts time-to-first-token to <1s. Deep-research
    # (quality) mode is unaffected and keeps reasoning. Set to `auto` to restore
    # provider-default thinking on chat.
    default_reasoning_mode: str = Field(default="off")
    extraction_reasoning_mode: str = Field(default="off")
    relationship_reasoning_mode: str = Field(default="off")
    vision_reasoning_mode: str = Field(default="off")
    # Per-model override escape hatch for novel models the heuristics get wrong.
    # Format: "model1:mode1,model2:mode2". Example: "gpt-5.8:none,custom-llm:minimal"
    reasoning_model_overrides: str = Field(default="")

    concurrent_relations: int = Field(
        default=3
    )  # Number of per-chunk relationship extractions to run concurrently per document
    max_graph_hops: int = Field(
        default=2
    )  # Maximum hops for graph traversal in queries
    concurrent_extractions: int = Field(
        default=3
    )  # Number of chunks to process concurrently for graph extraction

    # Extraction Context Window Configuration
    # Raw fields default to 0 (=inherit). Resolved values exposed via the
    # @property accessors at the bottom of the class.
    # Renamed from EXTRACTION_MAX_CONTEXT → GRAPH_EXTRACTION_MAX_CONTEXT to
    # match the `GRAPH_EXTRACTION_MODEL` env-var prefix convention. The legacy
    # name is honored as a deprecated alias for one release; a startup-time
    # WARN nudges users to migrate (see _warn_deprecated_env_aliases below).
    graph_extraction_max_context_raw: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "graph_extraction_max_context_raw",
            "GRAPH_EXTRACTION_MAX_CONTEXT",
            "EXTRACTION_MAX_CONTEXT",
        ),
    )
    relationship_max_context_raw: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "relationship_max_context_raw", "RELATIONSHIP_MAX_CONTEXT"
        ),
    )
    # Output-token budgets — chain: vision → relationship → extraction → primary.
    # Extraction ships a real default instead of 0-inherit (2026-07-09, decided
    # with Rene): extraction re-emits every entity so output scales with input,
    # and the inherited 8000 truncate-split ~10x per entity-dense book while
    # 12000 measured zero. Set 0 explicitly to restore inherit.
    extraction_max_output_tokens_raw: int = Field(
        default=12000,
        validation_alias=AliasChoices(
            "extraction_max_output_tokens_raw", "EXTRACTION_MAX_OUTPUT_TOKENS"
        ),
    )
    relationship_max_output_tokens_raw: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "relationship_max_output_tokens_raw", "RELATIONSHIP_MAX_OUTPUT_TOKENS"
        ),
    )
    # Phase 2 batch relationship analysis runs OUTSIDE the chain — it processes
    # hundreds of entity pairs per call and genuinely needs ~16k output. The
    # legacy env var name was RELATIONSHIP_MAX_OUTPUT_TOKENS (which now belongs
    # to the per-chunk chained field). Migrate to RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS.
    relationship_batch_max_output_tokens: int = Field(default=16000)

    # Batch Processing Configuration
    batch_processing_concurrency: int = Field(
        default=2
    )  # Documents processed concurrently in batch mode. Default 2: live measurement
    # (2026-07-08) showed 3 concurrent docs drop per-call decode throughput
    # ~70 → ~23 tok/s and multiply request timeouts — 2 finishes builds faster.
    processing_thread_workers: int = Field(
        default=4
    )  # Thread pool workers for CPU-intensive operations
    auto_resume_pending_on_startup: bool = Field(
        default=True
    )  # When the startup orphan-reset finds documents stranded mid-processing by the previous shutdown, automatically restart batch processing (quota-guarded). Only triggers on reset documents — bulk uploads parked with start_processing=false stay parked. Set false to require a manual "Generate Graph" after every redeploy.

    # Relationship Analysis (Phase B - cross-document relationship discovery)
    parallel_relationship_batches: int = Field(
        default=5
    )  # Number of relationship analysis batches to process in parallel
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

    # Targeted Phase B discovery (Step 2 v2). Candidates are generated WITHOUT
    # the LLM (entity-embedding kNN + document co-mention), then the LLM only
    # verifies/classifies ranked pairs in small batched calls. Orders of
    # magnitude fewer/cheaper LLM calls than the legacy full-batch scan.
    relationship_discovery_mode: str = Field(
        default="targeted"
    )  # 'targeted' (kNN + co-mention candidates, LLM verifies pairs) | 'llm_scan' (legacy two-phase batch scan)
    relationship_knn_k: int = Field(
        default=8
    )  # Nearest neighbors per entity in the vector-index candidate scan
    relationship_knn_min_similarity: float = Field(
        default=0.80
    )  # Min vector-index score (Neo4j cosine index score, 0-1) for a kNN candidate pair
    relationship_min_shared_docs: int = Field(
        default=2
    )  # Min distinct documents co-mentioning a pair for the doc-co-mention generator. 0 = disable generator.
    relationship_doc_freq_cap: int = Field(
        default=30
    )  # Skip entities mentioned in more than this many documents in the co-mention generator (hub guard)
    relationship_max_candidate_pairs: int = Field(
        default=15000
    )  # Total candidate-pair budget per analysis run (top-ranked pairs kept)
    relationship_candidates_per_entity: int = Field(
        default=10
    )  # Max candidate pairs any single entity may appear in (hub guard)
    relationship_pairs_per_call: int = Field(
        default=40
    )  # Candidate pairs verified per LLM call in targeted mode
    relationship_pair_context_tokens: int = Field(
        default=3000
    )  # Chunk-context token budget per verification call (0 = descriptions only, no chunk context)

    # Enhanced RAG Configuration
    enable_reranking: bool = Field(default=True)  # Enable cross-encoder reranking
    reranking_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2"
    )  # Cross-encoder model
    # Eagerly load the cross-encoder at startup. The local reranker pulls
    # torch + sentence-transformers (~780 MB resident) into the process; with
    # remote embeddings it is the ONLY thing that does. Defaulting this OFF
    # keeps idle instances lean (~250 MB vs ~1 GB) — important for packing many
    # tenant stacks per host — at the cost of a one-time cold start (~10–30 s)
    # on the first reranked query. Set true for latency-sensitive single-tenant
    # deployments. Has no effect when enable_reranking is false.
    reranker_preload: bool = Field(default=False)
    # Idle TTL for the locally-loaded cross-encoder, in seconds. When > 0, the
    # model is unloaded after this much time with no rerank (reclaims ~1 GB) and
    # reloads (~7 s) on the next query. Default 0 = never unload: an idle-evicted
    # reranker re-adds its load time to the first question after every quiet
    # period, which is exactly the query users judge responsiveness by. Set a
    # TTL only on memory-pressed multi-tenant hosts that don't use the shared
    # helper. Ignored when a remote reranker service is configured.
    reranker_idle_ttl_seconds: int = Field(default=0)

    # ==========================================================================
    # Shared model services (cortex-helper) — offload heavy models to a service
    # hosted once per physical machine. Empty = use the built-in local path
    # (in-process reranker / subprocess docling). See cortex-helper/README.md.
    # ==========================================================================
    reranker_service_url: str = Field(default="")  # e.g. http://localhost:3030
    docling_service_url: str = Field(default="")   # e.g. http://localhost:3030
    prompt_guard_service_url: str = Field(default="")  # e.g. http://localhost:3030
    #   Shared cortex-helper /classify endpoint (prompt-injection gate). Empty =
    #   no remote guard. Falls back to the in-process model only if
    #   prompt_guard_local is on (below); otherwise the guard is disabled.
    prompt_guard_local: bool = Field(default=False)
    #   Load the prompt-guard classifier IN-PROCESS when no service URL is set
    #   (mirrors the local reranker fallback). Off by default: this pulls the
    #   model into every instance (~resident RAM) and runs trust_remote_code
    #   model code locally, against the shared-service footprint priority. Turn
    #   on for local dev / self-hosters without a cortex-helper. Ignored when
    #   prompt_guard_service_url is set (remote path wins). Needs torch +
    #   transformers (present in the full image, absent in INSTALL_LOCAL_ML=false).
    prompt_guard_threshold: float = Field(default=0.5)  # injection-prob cutoff
    #   HF model id + pinned revision for both the remote helper and the local
    #   fallback. PIGuard loads with trust_remote_code, so the revision is pinned.
    prompt_guard_model: str = Field(default="leolee99/PIGuard")
    prompt_guard_revision: str = Field(default="dd78b24e330193a22d2293ac66922dd4f982f563")
    helper_service_token: str = Field(default="")  # shared secret -> X-Helper-Token
    helper_strict_remote: bool = Field(
        default=False
    )  # When true and DOCLING_SERVICE_URL is set, a conversion that fails after
    #   retries marks the document failed instead of falling back to the local
    #   docling subprocess — protects tenant memory on densely packed hosts
    #   (the local fallback pulls the OCR/layout models into this container).
    instance_id: str = Field(
        default=""
    )  # Identifies this stack to the shared helper (X-Tenant-ID header, used
    #   for fair queuing). Empty = container hostname.
    docling_conversion_timeout: int = Field(
        default=600
    )  # Hard ceiling (seconds) on a single local docling subprocess conversion.
    #   A hung OCR/table parse on a large/corrupt file would otherwise block
    #   indefinitely with the document stuck in 'processing'. On timeout the
    #   worker is killed and the document is marked failed with a clear message.

    # ==========================================================================
    # MDHarvest powered by Crawl4ai — web → markdown harvesting.
    # cortex-app NEVER embeds a browser/crawler stack; it speaks crawl4ai's
    # native REST API (/md, /crawl) over HTTP. One code path, two deployments:
    #   - self-host : CRAWL_SERVICE_URL -> the user's own crawl4ai (:11235)
    #   - cloud     : CRAWL_SERVICE_URL -> the shared per-host crawl4ai (set by
    #                 the AaaS operator; one container per server, many tenants)
    # Empty URL => feature OFF (no in-process fallback — that browser stack is
    # exactly the per-tenant footprint we refuse to pay). See cortex-helper.
    # ==========================================================================
    enable_web_crawl: bool = Field(
        default=False
    )  # Master switch for web→markdown harvesting (Web Import UI + endpoints).
    #   Auto-treated as enabled by the UI only when crawl_service_url is also set.
    crawl_service_url: str = Field(
        default=""
    )  # Base URL of the crawl4ai service, e.g. http://crawl4ai:11235 (self-host)
    #   or http://<host>:11235 / the shared per-server instance (cloud). Empty
    #   = feature disabled.
    crawl_service_token: str = Field(
        default=""
    )  # Bearer token for crawl4ai (Authorization: Bearer <token>); must match
    #   crawl4ai's CRAWL4AI_API_TOKEN / security.api_token. Required in practice
    #   for crawl4ai >= 0.9.0: with no token set, crawl4ai serves its API only on
    #   127.0.0.1 (unreachable from another container), so a cross-container or
    #   shared deployment MUST set this. Empty only works if crawl4ai itself runs
    #   tokenless (older versions, or same-host loopback). A startup WARN fires
    #   when web crawl is enabled with a URL but no token (see main.py lifespan).
    crawl_http_timeout: int = Field(
        default=60
    )  # Per-request timeout (s) for crawl4ai calls. Browser rendering of a slow
    #   page can take tens of seconds; keep this generous.
    crawl_content_filter: str = Field(
        default="fit"
    )  # crawl4ai /md filter strategy: "fit" (readability — clean main content,
    #   the Trafilatura replacement), "raw" (full DOM→markdown), or "bm25"
    #   (query-relevance ranked; needs a query). Default fit for KB ingestion.
    crawl_concurrency: int = Field(
        default=5
    )  # Max URLs crawled concurrently within one Web Import job. The shared
    #   crawl4ai enforces its own global browser-pool limits; this just bounds
    #   how hard a single tenant pushes it.
    crawl_max_urls_per_job: int = Field(
        default=100
    )  # Hard cap on URLs accepted per Web Import job (plan-limit lever — the
    #   AaaS operator lowers this per tenant via env). 0 = unlimited.
    crawl_discover_max_links: int = Field(
        default=200
    )  # Cap on candidate links returned by /api/web-import/discover.

    enable_hybrid_search: bool = Field(
        default=True
    )  # Enable hybrid (vector + keyword) search
    enable_batched_query_extraction: bool = Field(
        default=True
    )  # Batch a knowledge_search's queries into ONE entity-extraction + ONE embedding call
    vector_weight: float = Field(default=0.5)  # Weight for vector search in hybrid
    keyword_weight: float = Field(default=0.3)  # Weight for keyword search in hybrid
    graph_weight: float = Field(default=0.2)  # Weight for graph context in hybrid
    max_conversation_history: int = Field(
        default=6
    )  # Max messages to include from conversation (legacy truncation; used when no conversation_memory blob)

    # Conversation memory / multi-bucket context curator (see context_curator.py).
    # Only active when the client sends a `conversation_memory` blob in the request;
    # absent blob => legacy max_conversation_history truncation (byte-identical behavior).
    enable_conversation_memory: bool = Field(
        default=True
    )  # Backend kill-switch for the context curator (client opt-in still required via the blob)
    conversation_memory_window: int = Field(
        default=6
    )  # Recent messages kept verbatim; older ones fold into the rolling summary
    conversation_memory_compaction_model: str = Field(
        default=""
    )  # Model for post-stream compaction; empty => fast-mode model (or primary if unset)
    conversation_memory_max_ledger: int = Field(
        default=50
    )  # Max source_ledger entries retained in the memory blob (most recent kept)
    enable_memory_fast_path: bool = Field(
        default=True
    )  # Allow memory-answerable follow-ups (e.g. "summarize that", "in German") to skip retrieval

    enable_agentic_rag: bool = Field(default=True)  # Enable multi-step agentic RAG
    max_agentic_steps: int = Field(default=3)  # Maximum steps in agentic RAG (legacy)

    # Agent-based research pipeline (researcher/writer architecture)
    enable_agent_research: bool = Field(
        default=True
    )  # Use agent pipeline for research mode (vs legacy fixed pipeline)
    enable_agent_chat: bool = Field(
        default=True
    )  # Use agent pipeline for standard chat mode (required for skills in chat)
    researcher_max_iterations_speed: int = Field(
        default=3
    )  # Max agent loop iterations in speed/chat mode. Kept low so chat stays
    #   snappy: with reasoning suppressed each call is sub-second, so the agent
    #   loop itself is the dominant latency. 3 rounds leave room for a skill
    #   flow (activate → call → answer) without truncating it, while plain
    #   Q&A still finishes in one search + done. Deep research uses the quality cap.
    researcher_max_iterations_quality: int = Field(
        default=8
    )  # Max agent loop iterations in quality/research mode
    writer_max_tokens_speed: int = Field(
        default=1200
    )  # Max output tokens for writer in speed mode
    writer_max_tokens_quality: int = Field(
        default=4000
    )  # Max output tokens for writer in quality/research mode
    researcher_wall_clock_seconds: int = Field(
        default=0
    )  # Wall-clock budget for the researcher loop (0 = unlimited). On expiry
    #   the loop stops gathering and the writer synthesizes from what it has.
    researcher_speed_early_write: bool = Field(
        default=True
    )  # Speed mode: after a knowledge_search iteration that produced sources
    #   (and no skill/git action in flight), break straight to the writer
    #   instead of spending one more full LLM round-trip on the model calling
    #   `done` — whose summary the speed writer prompt never reads anyway.
    researcher_parallel_tool_calls: bool = Field(
        default=True
    )  # Execute read-only tool calls (knowledge_search / community_search /
    #   entity_lookup) from one assistant message concurrently instead of
    #   serially. Side-effecting tools (http_request, git_repo) stay sequential.
    researcher_tool_entity_hints: bool = Field(
        default=True
    )  # Let the researcher pass an `entities` array on knowledge_search calls;
    #   when present the query-side entity-extraction LLM call is skipped
    #   (the researcher just wrote the queries — it knows the entities).
    researcher_search_dedup: bool = Field(
        default=True
    )  # Return a cached tool result (with a "try a different angle" nudge)
    #   when the researcher re-issues an identical knowledge_search within one
    #   run, instead of paying the full retrieval pipeline again.
    researcher_force_grounding: bool = Field(
        default=True
    )  # When the researcher loop ends with ZERO searches performed and zero
    #   sources gathered (the model answered from parametric memory — observed
    #   stochastically), run one knowledge_search with the raw question before
    #   the writer so answers are grounded in the knowledge base. Skipped on
    #   the memory fast-path (which intentionally answers without retrieval).
    emit_done_before_memory: bool = Field(
        default=True
    )  # Emit the SSE `done` frame BEFORE the post-answer memory compaction LLM
    #   call, then `memory_update`, then close. The UI can finalize the turn
    #   1-4s earlier; clients must keep reading until stream end to receive
    #   `memory_update`. Set false to restore the legacy order (memory_update
    #   then done) for clients that stop consuming at `done`.
    rerank_top_k: int = Field(
        default=15
    )  # Candidates kept per knowledge_search after pooling the parallel
    #   queries; also the rerank input size. Lower it on remote rerankers
    #   (RERANKER_SERVICE_URL) to trade recall for latency.
    ask_deadline_seconds: int = Field(
        default=28
    )  # Hard wall-clock deadline for the non-streaming POST /api/ask handler.
    #   On expiry the request returns a clean 504 JSON {detail} instead of the
    #   edge proxy (Traefik) cutting the silent socket and emitting a bare
    #   plain-text 500. Keep this just BELOW the edge proxy read timeout
    #   (~30s by default); raise it in lockstep when the Traefik timeout is
    #   raised. Does NOT apply to /api/ask/stream — SSE heartbeats keep that
    #   connection alive. 0 = no app-level deadline.

    # ==========================================================================
    # Agent Skills (agentskills.io standard)
    # ==========================================================================
    skills_dir: str = Field(
        default=".agents/skills"
    )  # Directory for skill discovery (relative to project root or absolute)
    enable_skills: bool = Field(
        default=True
    )  # Master switch for AgentSkills integration
    enable_skill_scripts: bool = Field(
        default=False
    )  # Allow skills to execute local scripts (security-sensitive, opt-in)
    skill_script_timeout: int = Field(
        default=30
    )  # Timeout in seconds for skill script execution
    skill_http_timeout: int = Field(
        default=15
    )  # Timeout in seconds for skill HTTP tool calls
    skill_http_insecure_hosts: str = Field(
        default=""
    )  # Comma-separated hostnames for which skill http_request skips TLS
    # verification (opt-in, for self-signed certs on self-hosted skill APIs).
    # Empty = verify all hosts (secure default). Scoped per-host, never global.
    max_skill_tools: int = Field(
        default=10
    )  # Max total skill-provided tools injected into researcher agent
    max_skill_instructions_tokens: int = Field(
        default=4000
    )  # Approx token budget for skill instruction injection into prompt

    # ==========================================================================
    # Git Integration (GitHub / GitLab / Gitea repo connector)
    # ==========================================================================
    enable_git_integration: bool = Field(
        default=False
    )  # Master switch for the git-repo connector (ingestion + agent git_repo tool)
    git_work_dir: str = Field(
        default="./git_repos"
    )  # Directory holding per-connection clone working copies (cache, not source of truth)
    git_clone_depth: int = Field(
        default=1
    )  # Shallow-clone depth. 1 = latest commit only (cheapest); raise if older history is needed for diffs
    git_max_repo_size_mb: int = Field(
        default=500
    )  # Abort a sync if the cloned repo exceeds this size. 0 = unlimited
    git_sync_max_file_size_mb: int = Field(
        default=5
    )  # Skip individual files larger than this during sync (binaries/assets). 0 = no per-file limit
    git_sync_poll_interval: int = Field(
        default=5
    )  # Minutes between scheduler ticks that check connections due for a scheduled sync
    git_http_timeout: int = Field(
        default=30
    )  # Timeout in seconds for git provider REST API calls (verify, list_repos, write ops)
    git_http_insecure_hosts: str = Field(
        default=""
    )  # Comma-separated hostnames for which git REST calls AND clone TLS verification are skipped
    # (opt-in, for self-signed certs on self-hosted GitLab/Gitea). Empty = verify all (secure default).

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
    )  # Generate LLM summaries of communities (runs on the extraction tier)

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
    entity_dedup_prefilter: bool = Field(
        default=True
    )  # Prefilter Levenshtein dedup with the entity fulltext index: scores only
    #   the top-50 fulltext candidates instead of scanning every Entity node.
    #   Big win on 10k+ entity graphs. Default ON since 2026-07-03; set false
    #   to restore full-scan Levenshtein (recall can differ on extreme typo
    #   variants the fulltext analyzer misses).
    enable_batched_kg_writes: bool = Field(
        default=True
    )  # Store entities/links/relationships via UNWIND batches (a handful of
    #   Neo4j round trips per document instead of one per item). Preserves the
    #   per-item dedup semantics (parity tests in test_batched_writes.py).
    #   Default ON since 2026-07-03; set false to restore per-item writes.
    enable_batched_chunk_relationships: bool = Field(
        default=True
    )  # Pack several chunks into one per-chunk relationship-extraction LLM
    #   call (÷~relationship_chunks_per_call total calls). Falls back to the
    #   single-chunk path per batch on error or empty parse. Default ON since
    #   2026-07-03 (live-validated: ÷4 calls at parity yield — see
    #   bench/STEP1_RESEARCH.md); set false to restore one call per chunk.
    relationship_chunks_per_call: int = Field(
        default=4
    )  # Max chunks per batched relationship-extraction call (token budget
    #   may pack fewer).
    enable_prompt_cache_control: bool = Field(
        default=False
    )  # Send Anthropic cache_control breakpoints on the system prompt when
    #   routed via OpenRouter to anthropic/* models (cache-read pricing on the
    #   shared extraction/researcher prefix). No-op for other backends.
    researcher_stable_prompt: bool = Field(
        default=True
    )  # Keep the researcher system prompt byte-stable across loop iterations
    #   (iteration counter moves to a trailing system note) so provider prefix
    #   caches hit from iteration 2 on. Set false to restore the legacy
    #   per-iteration prompt rebuild.
    rate_limit_qpm: int = Field(
        default=0
    )  # Per-API-key requests/minute on the expensive endpoints (ask/upload).
    #   0 = disabled. A burst guardrail, not billing — billing stays
    #   MAX_QUERIES_PER_MONTH. Exceeding returns 429 + Retry-After.
    rate_limit_burst: int = Field(
        default=10
    )  # Token-bucket burst capacity for RATE_LIMIT_QPM.
    enable_phaseb_checkpointing: bool = Field(
        default=False
    )  # Persist Phase B batch progress (PhaseBCheckpoint nodes): a crash or
    #   redeploy mid-analysis resumes from completed batches, and rounds 2+
    #   reuse round 1's Phase 1 candidate pairs (~50% fewer LLM calls on
    #   multi-round runs).
    enable_reprocess_delta: bool = Field(
        default=False
    )  # Skip reprocessing when a document's chunked content is unchanged
    #   (chunk content hashes + extraction config hash match), and reuse
    #   embeddings of unchanged chunks on partial edits. Git re-syncs and
    #   re-uploads of unchanged files drop to ~zero LLM/embedding cost.

    # ==========================================================================
    # Observability
    # ==========================================================================
    log_format: str = Field(
        default="plain"
    )  # "plain" = legacy human-readable format (byte-identical to before);
    #   "json" = one JSON object per line with request_id correlation.
    metrics_enabled: bool = Field(
        default=True
    )  # Serve Prometheus metrics at GET /metrics (admin-key protected; not
    #   exposed through the prod nginx). Requires prometheus-client.

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
    display_full_system_config: bool = Field(
        default=False
    )  # Show advanced tuning knobs in the admin System Config panel (off = curated view)

    # ==========================================================================
    # Prompt Security (protection against prompt injection attacks)
    # ==========================================================================
    prompt_security: bool = Field(
        default=True
    )  # Enable prompt injection detection and protection

    # Ingestion-time prompt-injection scan: flag (never block) documents whose
    # content carries injection attempts planted for a downstream AI assistant.
    # The free heuristic always runs; this flag is the DEFAULT for the extra LLM
    # classifier and is admin-overridable at runtime (SystemMeta key
    # "ingestion_injection_scan"). Disable to save queries.
    ingestion_injection_scan: bool = Field(default=True)

    # Query-time prompt-guard gate: run the user's question through the shared
    # cortex-helper classifier (PROMPT_GUARD_SERVICE_URL) before retrieval and
    # refuse flagged injections. DEFAULT for the runtime toggle (SystemMeta key
    # "prompt_guard"); admin-overridable. Only active when a service URL is set.
    # Each guarded query costs one extra query-unit — disable to save it.
    prompt_guard: bool = Field(default=True)

    # ==========================================================================
    # Admin Authentication
    # ==========================================================================
    # NOTE: ADMIN_EMAIL is consumed by the Next.js frontend (lib/auth.ts), not
    # the backend — no Settings field for it here.
    admin_password: str = Field(default="")  # Admin login password (required for auth)
    admin_api_key: str = Field(default="")  # Admin API key for full backend access
    session_secret: str = Field(
        default=""
    )  # Secret for JWT session encryption (min 32 chars)
    track_admin_api_key_usage: bool = Field(
        default=False
    )  # Track usage analytics for admin API key
    api_key_cache_ttl_seconds: int = Field(
        default=30
    )  # In-process TTL cache for successful API-key validations (0 disables).
    # Bounds how long a revoked/edited key stays usable in workers that didn't
    # handle the CRUD call (single-worker deployments invalidate instantly).
    encryption_key: str = Field(
        default=""
    )  # Comma-separated Fernet keys for at-rest secret encryption (git PATs, skill
    # secret config). First key encrypts, all keys decrypt (rotation support).
    # Empty = encryption disabled (plaintext fallback). Generate a key with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

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
    def langfuse_tracing_active(self) -> bool:
        """True when Langfuse tracing should be wired up.

        Requires all three credentials AND the master switch. When False the
        OpenAI client factory returns the plain (untraced) client, so the same
        image runs identically with or without observability configured.
        """
        return bool(
            self.langfuse_public_key
            and self.langfuse_secret_key
            and self.langfuse_base_url
            and self.langfuse_tracing_enabled
        )

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
    def rel_extraction_model(self) -> str:
        """Get the model for per-chunk relationship extraction."""
        return self.relationship_extraction_model or self.extraction_model

    @property
    def rel_extraction_api_base(self) -> str:
        """Get the API base URL for relationship extraction."""
        return self.relationship_extraction_api_base or self.extraction_api_base

    @property
    def rel_extraction_api_key(self) -> str:
        """Get the API key for relationship extraction."""
        return self.relationship_extraction_api_key or self.extraction_api_key

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

    # ----- Token & context budget fallback chain ----------------------------
    # Each property resolves: raw_field if explicitly set (>0), else inherit
    # from the next tier up. Same idiom as extraction_model → openai_model
    # for strings, but with `0` as the "inherit" sentinel for ints.

    @property
    def extraction_max_context(self) -> int:
        """Resolved input context budget for entity extraction.

        Reads `GRAPH_EXTRACTION_MAX_CONTEXT` (or the deprecated
        `EXTRACTION_MAX_CONTEXT` alias) and falls back to `OPENAI_MAX_CONTEXT` —
        but the INHERITED value is clamped: a chat model's context (e.g.
        256000) leaking into extraction packs prompts no extraction tier
        actually answers within a request timeout (measured 2026-07-08:
        every 200k-token batch timed out; entities silently lost before the
        split-retry existed). An explicitly set env value is honored as-is.
        """
        if self.graph_extraction_max_context_raw > 0:
            return self.graph_extraction_max_context_raw
        return min(self.openai_max_context, 48_000)

    @property
    def relationship_max_context(self) -> int:
        """Resolved input context budget for relationship analysis batching."""
        return self.relationship_max_context_raw or self.extraction_max_context

    @property
    def extraction_max_output_tokens(self) -> int:
        """Resolved output budget for entity extraction calls."""
        return self.extraction_max_output_tokens_raw or self.openai_max_output_tokens

    @property
    def relationship_max_output_tokens(self) -> int:
        """Resolved output budget for per-chunk + candidate-scan relationship calls.

        Note: Phase 2 batch analysis uses `relationship_batch_max_output_tokens`
        directly (not in this chain) — see config field for migration guidance.
        """
        return (
            self.relationship_max_output_tokens_raw
            or self.extraction_max_output_tokens
        )

    @property
    def vision_max_output_tokens(self) -> int:
        """Resolved output budget for vision-model image analysis."""
        return (
            self.vision_max_output_tokens_raw
            or self.relationship_max_output_tokens
        )

    @property
    def parsed_reasoning_overrides(self) -> dict:
        """Parse REASONING_MODEL_OVERRIDES once and cache on the instance.

        Returns a dict mapping lowercased model name -> ReasoningMode.
        """
        cached = self.__dict__.get("_parsed_reasoning_overrides")
        if cached is not None:
            return cached
        # Import here to avoid circular import at module load
        from app.services.reasoning_config import parse_overrides

        parsed = parse_overrides(self.reasoning_model_overrides)
        self.__dict__["_parsed_reasoning_overrides"] = parsed
        return parsed

    @property
    def is_production(self) -> bool:
        """True when running with ENVIRONMENT=production (or "prod")."""
        return self.environment.strip().lower() in ("production", "prod")

    @property
    def docs_enabled(self) -> bool:
        """Whether to serve interactive API docs (/docs, /redoc, /openapi.json).

        Default "auto": on in development, off in production (avoids exposing the
        full API schema unauthenticated on a directly-reachable backend). An
        explicit EXPOSE_API_DOCS=true/false overrides the auto behaviour.
        """
        raw = self.expose_api_docs.strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return not self.is_production

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse cors_allowed_origins into a list. ["*"] means allow any."""
        raw = self.cors_allowed_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @model_validator(mode="after")
    def _enforce_production_secrets(self):
        """Refuse to boot an insecure instance in production.

        Auth is header-based (X-API-Key), but a default/empty Neo4j password
        or a weak JWT signing secret is a direct compromise path, so we fail
        fast rather than start with insecure defaults. Only enforced when
        ENVIRONMENT=production; development keeps convenient defaults.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if self.neo4j_password in ("", "password123"):
            problems.append(
                "NEO4J_PASSWORD must be set to a strong, non-default value"
            )

        # session_secret signs admin JWTs; it is only needed when admin login
        # is enabled, which is the case whenever an admin password is set.
        if self.admin_password and len(self.session_secret) < 32:
            problems.append(
                "SESSION_SECRET must be at least 32 characters when "
                "ADMIN_PASSWORD is set"
            )

        if problems:
            raise ValueError(
                "Insecure configuration for ENVIRONMENT=production:\n  - "
                + "\n  - ".join(problems)
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _empty_str_to_default(cls, values):
        """Drop empty-string env vars so field defaults apply."""
        if isinstance(values, dict):
            return {k: v for k, v in values.items() if v != ""}
        return values

    @model_validator(mode="after")
    def _warn_deprecated_env_aliases(self):
        """One-shot deprecation WARN when only the legacy env name is set.

        Detection inspects `os.environ` directly because Pydantic's
        AliasChoices doesn't surface which alias matched.
        """
        deprecations = [
            # (legacy_name, new_name)
            ("EXTRACTION_MAX_CONTEXT", "GRAPH_EXTRACTION_MAX_CONTEXT"),
        ]
        for legacy, canonical in deprecations:
            if legacy in _warned_legacy_env_aliases:
                continue
            # Require non-empty values — docker-compose env passthroughs like
            # `${EXTRACTION_MAX_CONTEXT:-}` set empty strings in os.environ
            # which would falsely trip the warning otherwise.
            legacy_val = os.environ.get(legacy, "").strip()
            canonical_val = os.environ.get(canonical, "").strip()
            if legacy_val and not canonical_val:
                logger.warning(
                    "%s is a deprecated env-var name; please rename it to %s "
                    "in your .env. The old name is honored for now but will "
                    "be removed in a future release.",
                    legacy, canonical,
                )
                _warned_legacy_env_aliases.add(legacy)
        return self

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


def _reset_deprecation_warnings_for_tests() -> None:
    """Test-only: clear the once-per-process deprecation guard."""
    _warned_legacy_env_aliases.clear()
