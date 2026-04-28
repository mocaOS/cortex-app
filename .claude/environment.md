# Environment Configuration

Copy `.env.example` to `.env`. Variables are grouped by concern below.

## Database

- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` — Neo4j connection

## Primary LLM

- `OPENAI_API_KEY`, `OPENAI_MODEL` (default: gpt-4o-mini) — Primary LLM for Q&A, research, and chat. Powerful reasoning models recommended (e.g. Minimax M2.7, GLM5, Kimi K2.5)
- `OPENAI_API_BASE` — for LiteLLM-compatible providers

## Extraction LLM

- `GRAPH_EXTRACTION_MODEL`, `GRAPH_EXTRACTION_API_BASE`, `GRAPH_EXTRACTION_API_KEY` — Extraction model for entity extraction and community summarization. Instruction-following models recommended (e.g. Mistral Small 24B, Ministral 14B). Defaults to primary model equivalents.
- `EXTRACTION_MAX_CONTEXT` (default: 32768) — context window budget for entity extraction (must match `GRAPH_EXTRACTION_MODEL` context window)

## Relationship LLM

See [`.claude/domain/relationships.md`](domain/relationships.md) for how these are used in the two-phase pipeline.

- `RELATIONSHIP_EXTRACTION_MODEL`, `RELATIONSHIP_EXTRACTION_API_BASE`, `RELATIONSHIP_EXTRACTION_API_KEY` — dedicated LLM model for all relationship extraction work (per-chunk in Step 1 + batch analysis in Step 2). Instruction-following models recommended (e.g. OpenAI GPT OSS 120B). Defaults to extraction model equivalents. Config properties: `rel_extraction_model`, `rel_extraction_api_base`, `rel_extraction_api_key` with fallback chain: relationship model -> extraction model -> main model. Uses `get_relationship_llm_config()` from `llm_config.py`.
- `CONCURRENT_RELATIONS` (default: 3) — concurrent per-chunk relationship extractions per document (separate rate limit from entity extraction)
- `RELATIONSHIP_MAX_CONTEXT` (default: 65536), `RELATIONSHIP_MAX_OUTPUT_TOKENS` (default: 16000) — context window and output budgets for relationship analysis (must match `RELATIONSHIP_EXTRACTION_MODEL` context window)
- `PARALLEL_RELATIONSHIP_BATCHES` (default: 5) — number of relationship analysis batches to process in parallel
- `RELATIONSHIP_TARGET_RATIO` (default: 1.0) — target relationships-per-entity ratio (ERR) for admin monitoring
- `RELATIONSHIP_MAX_ROUNDS` (default: 3) — max auto-discovery rounds for initial analysis ("Find more" always does 1 round). Stops early if target ratio reached.
- `RELATIONSHIP_MAX_HOURS` (default: 0) — max hours for relationship generation (0 = no time limit, completes all rounds)
- `RELATIONSHIP_MAX_PER_ENTITY` (default: 50) — soft cap on relationships per entity during analysis (0 = no cap). When both endpoints are saturated, the relationship is skipped.

## Vision

- `VISION_MAX_CONCURRENT` (default: 3) — max concurrent vision API calls system-wide for image analysis (controls semaphore + thread pool sizing)

## Embeddings

- `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `USE_OPENAI_EMBEDDINGS` — embedding config
- `EMBEDDING_API_BASE`, `EMBEDDING_API_KEY` — optional separate endpoint/key for embeddings (defaults to `OPENAI_API_BASE`/`OPENAI_API_KEY`)

## Feature Flags

- `ENABLE_GRAPH_EXTRACTION`, `ENABLE_COMMUNITY_DETECTION`, `ENABLE_AGENTIC_RAG` — feature flags
- `ENABLE_SEMANTIC_ENTITY_RESOLUTION` (default: true) — use embedding-based vector similarity for entity dedup during storage (catches semantic matches like "Museum of Crypto Art" / "MOCA" that Levenshtein misses; falls back to Levenshtein)
- `ENABLE_AGENT_RESEARCH` (default: true), `ENABLE_AGENT_CHAT` (default: true) — agent-based research pipeline flags

## Agent Configuration

- `RESEARCHER_MAX_ITERATIONS_SPEED` (default: 5), `RESEARCHER_MAX_ITERATIONS_QUALITY` (default: 10) — agent loop iteration caps
- `WRITER_MAX_TOKENS_SPEED` (default: 1200), `WRITER_MAX_TOKENS_QUALITY` (default: 4000) — writer output token limits

## Skills Configuration

See [`.claude/domain/skills.md`](domain/skills.md) for the full Agent Skills system.

- `ENABLE_SKILLS` (default: true) — master switch for Agent Skills integration
- `SKILLS_DIR` (default: `.agents/skills`) — directory for skill discovery (relative to project root or absolute). Persisted via Docker volume (`skills_data`) in production compose
- `ENABLE_SKILL_SCRIPTS` (default: false) — allow skills to execute local scripts (security-sensitive, opt-in)
- `SKILL_SCRIPT_TIMEOUT` (default: 30) — timeout in seconds for skill script execution
- `SKILL_HTTP_TIMEOUT` (default: 15) — timeout in seconds for skill HTTP tool calls
- `MAX_SKILL_TOOLS` (default: 10) — max total skill-provided tools injected into researcher agent
- `MAX_SKILL_INSTRUCTIONS_TOKENS` (default: 4000) — approximate token budget for skill instruction injection

## Auth

- `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SESSION_SECRET` — auth

## Document Processing

- `CHUNK_SIZE`, `CHUNK_OVERLAP`, `CHUNK_BY` (word/sentence) — document processing

## Pricing Limits

See [`PRICING.md`](../PRICING.md) for the per-tier value matrix. Sentinel `0` means "unlimited" on every cap below.

- `MAX_FILES` (default: 0) — total documents (uploads + custom inputs). Enforced at upload time and at library import.
- `MAX_COLLECTIONS` (default: 0) — total collections (default counts as 1). Declared but enforcement is wired separately per PRICING.md §4.4.
- `MAX_QUERIES_PER_MONTH` (default: 0) — instance-wide cap on chat-style queries (sum of `ep_ask + ep_search` across all `APIKeyUsageLog` rows for the current UTC calendar month). Applies to `POST /api/search`, `POST /api/ask`, `POST /api/ask/stream`, `POST /api/ask/stream/thinking`. Other endpoints (admin, upload, document/collection/graph management) are NOT counted. Returns `429 Too Many Requests` with a `Retry-After` header (seconds until next UTC month) when exceeded.
