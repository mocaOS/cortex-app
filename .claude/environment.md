# Environment Configuration

Copy `.env.example` to `.env`. Variables are grouped by concern below.

## Database

- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` — Neo4j connection

## Primary LLM

- `OPENAI_API_KEY`, `OPENAI_MODEL` (default: gpt-4o-mini) — Primary LLM for Q&A, research, and chat. Powerful reasoning models recommended (e.g. Minimax M2.7, GLM5, Kimi K2.5)
- `OPENAI_API_BASE` — for LiteLLM-compatible providers
- `OPENAI_MAX_OUTPUT_TOKENS` (default: 8000) — floor of the output-token budget chain. Sub-tier `*_MAX_OUTPUT_TOKENS` knobs inherit when set to 0. 8000 is generous enough that verbose-XML models (Qwen3-family) don't truncate `<relationship>` output; tighter models simply finish under cap with no cost penalty. See [Budget Fallback Chain](#budget-fallback-chain).
- `OPENAI_MAX_CONTEXT` (default: 32768) — floor of the input-context budget chain. `GRAPH_EXTRACTION_MAX_CONTEXT` and `RELATIONSHIP_MAX_CONTEXT` inherit when 0.

## Extraction LLM

- `GRAPH_EXTRACTION_MODEL`, `GRAPH_EXTRACTION_API_BASE`, `GRAPH_EXTRACTION_API_KEY` — Extraction model for entity extraction and community summarization. Instruction-following models recommended (e.g. Mistral Small 24B, Ministral 14B). Defaults to primary model equivalents.
- `GRAPH_EXTRACTION_MAX_CONTEXT` (default: 0 = inherit `OPENAI_MAX_CONTEXT` = 32768) — input context budget for entity extraction batching. Override when extraction model has bigger window than primary.
- `EXTRACTION_MAX_OUTPUT_TOKENS` (default: 0 = inherit `OPENAI_MAX_OUTPUT_TOKENS` = 8000) — output budget for entity-extraction LLM calls (`graph_extractor.py:821/1014/1840`). The inherited 8000 already accommodates Qwen3-family verbose XML; override only if you want to constrain or expand that tier specifically.

## Relationship LLM

See [`.claude/domain/relationships.md`](domain/relationships.md) for how these are used in the two-phase pipeline.

- `RELATIONSHIP_EXTRACTION_MODEL`, `RELATIONSHIP_EXTRACTION_API_BASE`, `RELATIONSHIP_EXTRACTION_API_KEY` — dedicated LLM model for all relationship extraction work (per-chunk in Step 1 + batch analysis in Step 2). Instruction-following models recommended (e.g. OpenAI GPT OSS 120B). Defaults to extraction model equivalents. Config properties: `rel_extraction_model`, `rel_extraction_api_base`, `rel_extraction_api_key` with fallback chain: relationship model -> extraction model -> main model. Uses `get_relationship_llm_config()` from `llm_config.py`.
- `CONCURRENT_RELATIONS` (default: 3) — concurrent per-chunk relationship extractions per document (separate rate limit from entity extraction)
- `RELATIONSHIP_MAX_CONTEXT` (default: 0 = inherit `GRAPH_EXTRACTION_MAX_CONTEXT` → primary) — input context budget for Phase 2 batch relationship analysis.
- `RELATIONSHIP_MAX_OUTPUT_TOKENS` (default: 0 = inherit `EXTRACTION_MAX_OUTPUT_TOKENS` → primary) — output budget for **per-chunk** + **candidate-pair scan** (lines 1962/2026/2068/2137). **NOTE: This env var changed meaning** — previously it was the Phase 2 batch budget (16000). Migrate that semantic to `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS`.
- `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS` (default: 16000) — output budget for **Phase 2 batch** analysis (`graph_extractor.py:2270`). Standalone, NOT in the inheritance chain (batches process hundreds of entity pairs per call and need ~16k).
- `PARALLEL_RELATIONSHIP_BATCHES` (default: 5) — number of relationship analysis batches to process in parallel
- `RELATIONSHIP_TARGET_RATIO` (default: 1.0) — target relationships-per-entity ratio (ERR) for admin monitoring
- `RELATIONSHIP_MAX_ROUNDS` (default: 3) — max auto-discovery rounds for initial analysis ("Find more" always does 1 round). Stops early if target ratio reached.
- `RELATIONSHIP_MAX_HOURS` (default: 0) — max hours for relationship generation (0 = no time limit, completes all rounds)
- `RELATIONSHIP_MAX_PER_ENTITY` (default: 50) — soft cap on relationships per entity during analysis (0 = no cap). When both endpoints are saturated, the relationship is skipped.

## Reasoning Control (ingestion)

Force reasoning OFF on capable models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1, GLM, Kimi, MiniMax) so they can be used for structured extraction without the drift, hidden-token cost, latency, and malformed JSON that reasoning causes on these tasks. Implementation: `backend/app/services/reasoning_config.py`. Backend detected from `base_url`; model family by regex on the model string. Works for OpenAI, OpenRouter, Venice, Anthropic, and vLLM/Compute3.

Accepted values for all three modes: `off | minimal | auto | low | medium | high` (also accepts `none`/`disabled` as aliases for OFF, and `default` as alias for AUTO).

- `EXTRACTION_REASONING_MODE` (default `off`) — applied to entity extraction, document summaries, community summarization, community naming, entity enrichment, query-side entity extraction.
- `RELATIONSHIP_REASONING_MODE` (default `off`) — applied to candidate-pair scan (Phase 1), gleaning pass, per-chunk relationship extraction, batch relationship analysis (Phase 2).
- `VISION_REASONING_MODE` (default `off`) — applied to the vision-model call in `vision_analyzer.py`. Lets you use the same reasoning-capable multimodal model (e.g. Qwen3-VL-27B) as both `GRAPH_EXTRACTION_MODEL` and `VISION_MODEL` without `<think>` tokens leaking into image descriptions. Vision uses raw httpx (not the OpenAI SDK), so the helper flattens `extra_body` into the top-level JSON body and runs its own one-shot 400-fallback (`is_reasoning_unsupported` / `mark_reasoning_unsupported`).
- `DEFAULT_REASONING_MODE` (default `auto`) — used by `get_llm_config()` for the non-ingestion / Q&A path. Researcher agent stays on AUTO because `reasoning_effort=minimal` disables parallel tool calls on OpenAI.
- `REASONING_MODEL_OVERRIDES` (default empty) — escape hatch for novel models the heuristics get wrong. Format: `model1:mode1,model2:mode2`. Example: `gpt-5.8:none,custom-llm:minimal`. Applies to all four modes above (extraction, relationship, vision, default).

### New model releases

The regex parser handles same-family minor releases automatically (e.g. `gpt-5.8` routes the same as `gpt-5.1` — `reasoning_effort: "none"`). For new majors or models the heuristic misclassifies:

1. Set `REASONING_MODEL_OVERRIDES=<model>:<mode>` — no code change needed.
2. If the API rejects the reasoning param, the runtime fallback strips it, logs a warning, and caches the model as unsupported. One wasted call per model on first run; subsequent calls skip the params upfront.

### Caveats

- `gpt-5-pro` is hard-pinned to `reasoning_effort=high` by OpenAI — OFF is silently ignored, a one-time WARN is logged.
- `gpt-5-codex` doesn't accept `minimal` — auto-downgraded to `low`.
- Anthropic Opus 4.7+ uses adaptive thinking — manual `thinking` returns 400, so the helper omits the param. Reasoning may still occur regardless of mode.
- OpenRouter `exclude:true` does NOT save tokens (model still reasons and bills); we use `effort:"none"`/`"minimal"` instead.

## Vision

- `VISION_MAX_CONCURRENT` (default: 3) — max concurrent vision API calls system-wide for image analysis (controls semaphore + thread pool sizing)
- `VISION_REASONING_MODE` (default: `off`) — see [Reasoning Control (ingestion)](#reasoning-control-ingestion). Suppresses `<think>` output on reasoning multimodal models (Qwen3-VL, GLM-V, etc.) so image descriptions stay clean.
- `VISION_MAX_OUTPUT_TOKENS` (default: 0 = inherit `RELATIONSHIP_MAX_OUTPUT_TOKENS` → `EXTRACTION_MAX_OUTPUT_TOKENS` → `OPENAI_MAX_OUTPUT_TOKENS`) — output budget for the vision-model image-description call (`vision_analyzer.py:304`).

## Budget Fallback Chain

`backend/app/config.py` exposes `@property` accessors that resolve token / context budgets through a parent chain when the raw env var equals `0`. Same idiom as the existing model-name fallback (`extraction_model` → `openai_model`) but for ints, with `0` as the inherit sentinel (consistent with `MAX_FILES=0` etc.).

**Output tokens chain:** `VISION_*` → `RELATIONSHIP_*` → `EXTRACTION_*` → `OPENAI_MAX_OUTPUT_TOKENS=8000`
**Input context chain:** `RELATIONSHIP_MAX_CONTEXT` → `GRAPH_EXTRACTION_MAX_CONTEXT` → `OPENAI_MAX_CONTEXT=32768`
**Standalone:** `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000` (Phase 2 batch only — not in chain)

Recommended minimal config when running a 3-tier stack:
```env
OPENAI_MODEL=MiniMaxAI/MiniMax-M2.7      # primary / agentic (196K window)
OPENAI_MAX_CONTEXT=196608                # unlock MiniMax-M2.7 full input window
GRAPH_EXTRACTION_MODEL=qwen/qwen3-7-27b  # extraction + (inherited) relationship (256K window)
GRAPH_EXTRACTION_MAX_CONTEXT=256000      # unlock Qwen3.7-27B full input window; relationship_max_context inherits
VISION_MODEL=google-gemma-3-27b-it       # image analysis (does NOT inherit from extraction; api_base/api_key inherit from OPENAI_*)
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B  # text embedding (native 4096, MRL 32–4096)
EMBEDDING_DIMENSION=4096                 # native; Neo4j 5.26 (default) supports 4096-dim vector indexes
# Output budgets cascade automatically
```
Both `*_MAX_CONTEXT` overrides are required — the conservative default (32768) doesn't match either model's actual input window.

Companion performance-tuning block (Venice-validated; pair with the stack above to maximize ingestion throughput):
```env
BATCH_PROCESSING_CONCURRENCY=5    # docs in parallel (default 2)
CONCURRENT_EXTRACTIONS=10         # entity-extraction threads per doc (default 3) — biggest multiplier
CONCURRENT_RELATIONS=5            # per-chunk relationship threads per doc (default 3)
VISION_MAX_CONCURRENT=5           # system-wide vision semaphore (default 3)
```
`BATCH_PROCESSING_CONCURRENCY` compounds with the two `CONCURRENT_*` knobs (per-doc pools); `VISION_MAX_CONCURRENT` is a global semaphore and stays flat. The pipeline staggers extraction / relationships / vision across each doc's lifecycle, so actual in-flight concurrency stays below the worst-case product. Safe on Venice / Compute3 / large vLLM; dial `CONCURRENT_EXTRACTIONS` down first on smaller providers.

**Migration:** the env var `RELATIONSHIP_MAX_OUTPUT_TOKENS` was previously the Phase 2 batch budget (16000). It now drives **per-chunk + candidate scan** instead, and the Phase 2 batch value lives in the new `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000`. Users who explicitly set `RELATIONSHIP_MAX_OUTPUT_TOKENS=16000` will see per-chunk extraction also get 16000 tokens (overkill but harmless — model finishes well below cap).

**Migration:** `EXTRACTION_MAX_CONTEXT` → `GRAPH_EXTRACTION_MAX_CONTEXT` — env var renamed to match the `GRAPH_EXTRACTION_MODEL`/`GRAPH_EXTRACTION_API_BASE`/`GRAPH_EXTRACTION_API_KEY` prefix convention. Legacy name is honored as a deprecated alias for one release; a one-shot startup `WARN` (from `app.config._warn_deprecated_env_aliases`) fires when only the old name is set. The Python property `settings.extraction_max_context` is unchanged.

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
- `MAX_ENTITIES` (default: 0) — total entities across the graph. Enforced at upload-time and custom-input creation: new ingestion is rejected once `get_stats()["entity_count"]` is at or above the cap. A single in-flight document can push the post-extraction count slightly above the cap (accepted tradeoff per PRICING.md §4.2).
- `MAX_COLLECTIONS` (default: 0) — total collections (default counts as 1). Enforced at `POST /api/collections`.
- `MAX_QUERIES_PER_MONTH` (default: 0) — instance-wide cap on chat-style queries (sum of `ep_ask + ep_search` across all `APIKeyUsageLog` rows for the current UTC calendar month). Applies to `POST /api/search`, `POST /api/ask`, `POST /api/ask/stream`, `POST /api/ask/stream/thinking`. Other endpoints (admin, upload, document/collection/graph management) are NOT counted. Returns `429 Too Many Requests` with a `Retry-After` header (seconds until next UTC month) when exceeded.
