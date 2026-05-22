# Chapter 4: Configuration Reference

All configuration is done through environment variables, set either in your `.env` file or through your deployment platform's environment management. This chapter documents every available option.

The Library uses Pydantic BaseSettings for configuration. Environment variables are case-insensitive. Empty string values fall back to field defaults. The settings loader searches for `.env` files in multiple locations: the current directory, `backend/`, the project root, and `/app/`.

## Database Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI. Use `bolt://neo4j:7687` in Docker. |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password123` | Neo4j password. **Change for production.** |

## Primary LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | API key for the primary LLM provider. Required for Q&A, research, and graph operations. |
| `OPENAI_API_BASE` | `https://api.openai.com/v1` | Base URL for the LLM API. Change for LiteLLM, Azure, or local providers. |
| `OPENAI_MODEL` | `openai/minimax-m21` | Model name for Q&A, research, and chat. Recommended: powerful reasoning models (e.g. Minimax M2.7, GLM5, Kimi K2.5). |
| `OPENAI_MODEL_FAST_MODE` | Same as `OPENAI_MODEL` | Optional faster/cheaper model for the "Fast Mode" search in Ask AI. |
| `OPENAI_MAX_OUTPUT_TOKENS` | `8000` | Floor of the output-token budget chain. All sub-tier `*_MAX_OUTPUT_TOKENS` knobs inherit from here when set to 0. 8000 covers Qwen3-family verbose XML; tighter models simply finish under cap. See [Budget Fallback Chain](#budget-fallback-chain). |
| `OPENAI_MAX_CONTEXT` | `32768` | Floor of the input-context budget chain. `GRAPH_EXTRACTION_MAX_CONTEXT` and `RELATIONSHIP_MAX_CONTEXT` inherit when 0. |

## Graph Extraction Configuration

These settings control the LLM used for entity extraction (Phase A) and can point to a different model/provider than the primary LLM.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_GRAPH_EXTRACTION` | `true` | Enable LLM-powered entity extraction during document ingestion. Set `false` to skip extraction entirely. |
| `GRAPH_EXTRACTION_MODEL` | Same as `OPENAI_MODEL` | Dedicated model for entity extraction and community summarization. Recommended: instruction-following models (e.g. Mistral Small 24B, Ministral 14B). |
| `GRAPH_EXTRACTION_API_BASE` | Same as `OPENAI_API_BASE` | API base URL for the extraction model. |
| `GRAPH_EXTRACTION_API_KEY` | Same as `OPENAI_API_KEY` | API key for the extraction model. |
| `GRAPH_EXTRACTION_MAX_CONTEXT` | `0` (=inherit `OPENAI_MAX_CONTEXT`) | Max context window tokens for entity extraction batching. Set explicitly when your extraction model has a bigger window than the primary. Renamed from `EXTRACTION_MAX_CONTEXT` (deprecated alias still honored). |
| `EXTRACTION_MAX_OUTPUT_TOKENS` | `0` (=inherit `OPENAI_MAX_OUTPUT_TOKENS`) | Max output tokens for entity-extraction LLM calls. Bump to 3500–4000 for Qwen3-family models that emit verbose XML. |
| `CONCURRENT_EXTRACTIONS` | `3` | Number of chunks processed in parallel during entity extraction (thread pool size). |

## Relationship Analysis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RELATIONSHIP_EXTRACTION_MODEL` | Same as `GRAPH_EXTRACTION_MODEL` | Model for relationship extraction (per-chunk and cross-document). Recommended: instruction-following models (e.g. OpenAI GPT OSS 120B). |
| `RELATIONSHIP_MAX_CONTEXT` | `0` (=inherit `GRAPH_EXTRACTION_MAX_CONTEXT` → primary) | Max input context window tokens for Phase 2 batch relationship analysis. Set when relationship model has bigger window than extraction. |
| `RELATIONSHIP_MAX_OUTPUT_TOKENS` | `0` (=inherit `EXTRACTION_MAX_OUTPUT_TOKENS` → primary) | Output budget for **per-chunk + candidate-pair scan** (in the chain). **Migrated semantics** — see migration note below. |
| `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS` | `16000` | Output budget for **Phase 2 batch** relationship analysis. Standalone (NOT in chain) — batch processes hundreds of pairs per call and genuinely needs ~16k. |
| `RELATIONSHIP_MAX_PER_ENTITY` | `50` | Soft cap on relationships per entity. Prevents hub entities from accumulating disproportionate connections. 0 = no cap. |
| `PARALLEL_RELATIONSHIP_BATCHES` | `0` | Number of relationship batches to process in parallel. 0 = use `CONCURRENT_EXTRACTIONS`. **Most impactful lever for relationship analysis speed.** |

## Reasoning Control (ingestion pipelines)

Reasoning hurts structured extraction (drift, hidden-token cost, latency, malformed JSON). These knobs let reasoning-capable models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1, GLM-4.6, Kimi K2, MiniMax M2) be used for ingestion while forcing their thinking OFF. Provider is auto-detected from `base_url`; model family is parsed from the model name. Works for OpenAI, OpenRouter, Venice, Anthropic, and vLLM/Compute3. Accepted values: `off | minimal | auto | low | medium | high` (`none`/`disabled` are aliases for `off`).

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACTION_REASONING_MODE` | `off` | Reasoning mode for entity extraction, document summaries, community summarization, community naming, entity enrichment, and query-side entity extraction. |
| `RELATIONSHIP_REASONING_MODE` | `off` | Reasoning mode for candidate-pair scan (Phase 1), gleaning pass, per-chunk relationship extraction, and batch relationship analysis (Phase 2). |
| `VISION_REASONING_MODE` | `off` | Reasoning mode for the vision-model image-description call. Lets a reasoning multimodal model (e.g. Qwen3-VL-27B) be used as `VISION_MODEL` without `<think>` tokens leaking into descriptions. |
| `DEFAULT_REASONING_MODE` | `auto` | Reasoning mode for the Q&A path. Researcher agent stays on AUTO because `reasoning_effort=minimal` disables parallel tool calls on OpenAI. |
| `REASONING_MODEL_OVERRIDES` | empty | Per-model override for novel models the heuristics get wrong. Format: `model1:mode1,model2:mode2`. Example: `gpt-5.8:none,custom-llm:minimal`. |

### Handling new model releases

The regex parser handles same-family minor releases automatically (e.g. `gpt-5.8` routes the same as `gpt-5.1`). For new majors or models the heuristic misclassifies, set `REASONING_MODEL_OVERRIDES`. If the API rejects the param at runtime, the wrapper strips it on retry, logs a warning, and caches the (base_url, model) pair so subsequent calls skip the param upfront — one wasted call per model on first run, then nothing.

### Caveats

- `gpt-5-pro` is hard-pinned to `reasoning_effort=high` by OpenAI. OFF is silently ignored; a one-time WARN is logged.
- `gpt-5-codex` doesn't accept `minimal`; auto-downgraded to `low`.
- Anthropic Opus 4.7+ uses adaptive thinking — manual `thinking` returns 400, so the helper omits the param. Reasoning may still occur regardless of mode.
- OpenRouter `exclude:true` does NOT save tokens (model still reasons and bills you); we use `effort:"none"`/`"minimal"` instead.

## Budget Fallback Chain

Output-token and input-context budgets cascade through a parent chain when sub-tier knobs are left at `0`. This mirrors the existing string-fallback pattern (`extraction_model` → `openai_model`) for ints, using `0` as the inherit sentinel.

```
OUTPUT TOKENS:                          INPUT CONTEXT:
  OPENAI_MAX_OUTPUT_TOKENS=8000           OPENAI_MAX_CONTEXT=32768
       ↓                                       ↓
  EXTRACTION_MAX_OUTPUT_TOKENS            GRAPH_EXTRACTION_MAX_CONTEXT
       ↓                                       ↓
  RELATIONSHIP_MAX_OUTPUT_TOKENS          RELATIONSHIP_MAX_CONTEXT
       ↓
  VISION_MAX_OUTPUT_TOKENS

  RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000   (standalone, Phase 2 only)
```

**Recommended minimal stack** — configure two models + two context windows; everything else inherits:

```bash
OPENAI_MODEL=deepseek-v4-flash        # primary / agentic (1M window)
OPENAI_MAX_CONTEXT=1000000                 # unlock DeepSeek-V4-Flash's full input window

GRAPH_EXTRACTION_MODEL=qwen3-6-27b    # extraction + (inherited) relationship (256K window)
GRAPH_EXTRACTION_MAX_CONTEXT=256000        # unlock Qwen3.7-27B's full input window; relationship_max_context inherits

VISION_MODEL=qwen3-6-27b              # image analysis (does NOT inherit from extraction)

EMBEDDING_MODEL=text-embedding-qwen3-8b    # text embedding model (native 4096, MRL 32–4096)
EMBEDDING_DIMENSION=4096                   # Native; Neo4j 5.26 (default) supports up to 4096-dim vector indexes
# Output budgets + all other knobs cascade through defaults. EMBEDDING_MAX_INPUT_TOKENS stays at
# default 8192 — Venice and OpenAI cap embed inputs at 8192 at the API gateway regardless of model.
# Self-hosted vLLM users running Qwen3-Embedding-8B can lift to 32768.
```

Both `*_MAX_CONTEXT` overrides are required because the conservative default (32768) does not match either model's actual input window — without them you'd be limiting DeepSeek-V4-Flash and Qwen3.7-27B to a fraction of their real capability. The embedding model uses the primary `OPENAI_API_BASE` + `OPENAI_API_KEY` unless `EMBEDDING_API_BASE`/`EMBEDDING_API_KEY` overrides are set. `EMBEDDING_SEND_DIMENSIONS=true` (default) works because Qwen3-Embedding-8B is MRL-aware. `EMBEDDING_MAX_INPUT_TOKENS` defaults to 8192 to match the cap Venice/OpenAI enforce at the API gateway (regardless of the underlying model's native window) — oversized inputs are char-truncated client-side to avoid `HTTP 400 "Input text exceeds the maximum token limit"` rejections. On self-hosted vLLM you can lift to the model's native context (e.g. 32768 for Qwen3-Embedding-8B).

**Performance tuning (Venice-validated)** — bench-validated against Venice as the LLM provider, paired with the recommended stack above. Cranks ingestion throughput at the cost of much higher peak concurrency:

```bash
BATCH_PROCESSING_CONCURRENCY=3    # docs processed in parallel (default 2)
CONCURRENT_EXTRACTIONS=4          # entity-extraction threads per doc (default 3 — biggest multiplier)
CONCURRENT_RELATIONS=4            # per-chunk relationship threads per doc (default 3)
VISION_MAX_CONCURRENT=4           # system-wide vision-API semaphore (default 3)
```

**Compounding behavior.** `BATCH_PROCESSING_CONCURRENCY` compounds with the two `CONCURRENT_*` knobs because they're *per-document* limits — each in-flight document can run its own pool of extraction / relationship threads. `VISION_MAX_CONCURRENT` is a global semaphore and does *not* compound. The pipeline staggers extraction, per-chunk relationships, and vision across each doc's lifecycle, so actual concurrent in-flight calls stays meaningfully below the worst-case theoretical product — you won't see the full multiplication hit a single provider at one moment.

Safe on Venice, Compute3, or a large self-hosted vLLM endpoint. On stock OpenAI or a small box you'll still want to dial `CONCURRENT_EXTRACTIONS` down first (biggest multiplier, heaviest call). `VISION_MAX_CONCURRENT` is independent and safe to keep at 5 even on smaller stacks.

**Targeted overrides:**
- Constrain extraction tier output independently → `EXTRACTION_MAX_OUTPUT_TOKENS=2000` (the inherited 8000 covers Qwen3-family verbose XML by default — only override to tighten or further loosen this specific tier)
- Big-context primary → `OPENAI_MAX_CONTEXT=131072` (lifts both extraction + relationship context)
- Phase 2 batch tuning → `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=24000` (standalone, doesn't touch other tiers)

### Migration: `RELATIONSHIP_MAX_OUTPUT_TOKENS`

This env var's meaning changed:
- **Before:** controlled Phase 2 batch budget (default 16000).
- **After:** controls per-chunk + candidate-pair scan (in the inheritance chain, default 0 = inherit).

The Phase 2 batch budget moved to `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000`. Users who never set the legacy var see no change (defaults are equivalent). Users who explicitly set `RELATIONSHIP_MAX_OUTPUT_TOKENS=16000` will get a per-chunk cap of 16000 (harmless overkill — the model finishes well below cap on chunk-sized work). Migrate at convenience by renaming the var.

### Migration: `EXTRACTION_MAX_CONTEXT` → `GRAPH_EXTRACTION_MAX_CONTEXT`

This env var was renamed to match the `GRAPH_EXTRACTION_MODEL` / `GRAPH_EXTRACTION_API_BASE` / `GRAPH_EXTRACTION_API_KEY` prefix convention — the tier's env vars are now consistent.

The legacy name `EXTRACTION_MAX_CONTEXT` is honored as a deprecated alias for one release. Backend logs a one-shot `WARN` at startup if your `.env` still uses it. Rename to `GRAPH_EXTRACTION_MAX_CONTEXT` whenever convenient — value semantics are identical.

## Embedding Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model name. |
| `EMBEDDING_DIMENSION` | `1536` | Embedding vector dimension. Must match the model's output dimension. |
| `EMBEDDING_SEND_DIMENSIONS` | `true` | Send `dimensions` parameter to the embedding API. Set `false` for models with fixed output dimensions (e.g., `qwen3-vl-embedding-2b`). |
| `USE_OPENAI_EMBEDDINGS` | `true` | Controls embedding *transport*, not provider. `true` = call `EMBEDDING_MODEL` via the OpenAI-compatible HTTP endpoint (works for OpenAI, OpenRouter, vLLM, Compute3, any `/v1/embeddings` server). `false` = ignore `EMBEDDING_MODEL` entirely and run `sentence-transformers/all-MiniLM-L6-v2` locally inside the container. Keep `true` for Qwen3-Embedding-8B and any remote embedding model. |
| `EMBEDDING_API_BASE` | Same as `OPENAI_API_BASE` | Optional separate endpoint for embeddings. |
| `EMBEDDING_API_KEY` | Same as `OPENAI_API_KEY` | Optional separate API key for embeddings. |

## Document Processing Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `UPLOAD_DIR` | `./uploads` | Directory for uploaded files. Use `/app/uploads` in Docker. |
| `CUSTOM_INPUTS_DIR` | `./custom_inputs` | Directory for custom input files. Use `/app/custom_inputs` in Docker. |
| `MAX_FILE_SIZE_MB` | `50` | Maximum upload file size in megabytes. |
| `CHUNK_SIZE` | `500` | Words per chunk when using word-based chunking. |
| `CHUNK_OVERLAP` | `50` | Overlap between chunks in words. |
| `CHUNK_BY` | `sentence` | Chunking strategy: `word` or `sentence`. |
| `SENTENCES_PER_CHUNK` | `5` | Sentences per chunk when using sentence-based chunking. |

## Performance Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `BATCH_PROCESSING_CONCURRENCY` | `2` | Documents processed simultaneously during batch operations. |
| `CONCURRENT_EXTRACTIONS` | `3` | Entity extraction thread pool size per document. |
| `PROCESSING_THREAD_WORKERS` | `4` | Thread pool workers for CPU-bound operations. |
| `VISION_MAX_CONCURRENT` | `3` | Max concurrent vision API calls system-wide (controls semaphore + thread pool). |
| `PARALLEL_RELATIONSHIP_BATCHES` | `2` | Relationship analysis batches in parallel (1 = sequential). |

## Search and RAG Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_RERANKING` | `true` | Enable cross-encoder re-ranking for improved precision. |
| `RERANKING_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model for re-ranking. |
| `ENABLE_HYBRID_SEARCH` | `true` | Enable hybrid (vector + keyword + graph) search. |
| `VECTOR_WEIGHT` | `0.5` | Weight for vector search in RRF fusion. |
| `KEYWORD_WEIGHT` | `0.3` | Weight for keyword search in RRF fusion. |
| `GRAPH_WEIGHT` | `0.2` | Weight for graph context in RRF fusion. |
| `MAX_GRAPH_HOPS` | `2` | Max hops for graph traversal during search. |
| `MAX_CONVERSATION_HISTORY` | `6` | Max messages retained in conversation context. |
| `ENABLE_AGENTIC_RAG` | `true` | Enable multi-step agentic RAG. |
| `MAX_AGENTIC_STEPS` | `3` | Maximum steps in legacy agentic RAG pipeline. |

## Agent Research Pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_AGENT_RESEARCH` | `true` | Use agent pipeline for Deep Research mode. Set `false` for legacy fixed-step pipeline. |
| `ENABLE_AGENT_CHAT` | `true` | Use agent pipeline for standard Chat mode (required for skills in chat). |
| `RESEARCHER_MAX_ITERATIONS_SPEED` | `5` | Max agent loop iterations for Chat mode (speed). |
| `RESEARCHER_MAX_ITERATIONS_QUALITY` | `8` | Max agent loop iterations for Deep Research (quality). |
| `WRITER_MAX_TOKENS_SPEED` | `1200` | Max output tokens for Chat answers. |
| `WRITER_MAX_TOKENS_QUALITY` | `4000` | Max output tokens for Deep Research answers. |

**Agent vs. Legacy pipeline comparison:**

| Aspect | Agent Pipeline | Legacy Pipeline |
|--------|---------------|----------------|
| LLM calls | 4-8 per query | 2 per query |
| Token usage | 3-5x more | Baseline |
| Answer quality | Higher (multi-angle research) | Good (fixed decompose → search → synthesize) |
| Latency | Higher (iterative) | Lower (fixed steps) |
| Requires | Function calling support | Any LLM |
| Behavior | Dynamic (LLM decides what to search) | Deterministic (fixed path) |

## Agent Skills Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SKILLS` | `true` | Master switch for the Agent Skills system. When disabled, no skill catalog or activation tools appear in the researcher agent. |
| `SKILLS_DIR` | `.agents/skills` | Directory for skill discovery. Relative paths resolve from the project root. Use an absolute path or Docker volume mount for persistence. |
| `ENABLE_SKILL_SCRIPTS` | `false` | Allow skills to execute local scripts. **Security-sensitive** — only enable if you trust all installed skills. |
| `SKILL_SCRIPT_TIMEOUT` | `30` | Timeout in seconds for skill script execution. |
| `SKILL_HTTP_TIMEOUT` | `15` | Timeout in seconds for skill HTTP tool calls. |
| `MAX_SKILL_TOOLS` | `10` | Maximum total skill-provided tools injected into the researcher agent's tool list. |
| `MAX_SKILL_INSTRUCTIONS_TOKENS` | `4000` | Approximate token budget for activated skill instruction bodies in the system prompt. |

See [Chapter 19: Agent Skills](19-skills.md) for full documentation on installing, configuring, and creating skills.

## Community Detection Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_COMMUNITY_DETECTION` | `true` | Enable entity community detection. |
| `MIN_COMMUNITY_SIZE` | `3` | Minimum entities for a valid community. |
| `MAX_COMMUNITIES` | `50` | Maximum number of communities to track. |
| `ENABLE_GRAPH_SUMMARIZATION` | `true` | Generate LLM summaries of communities. |
| `COMMUNITY_SUMMARY_MODEL` | Same as `GRAPH_EXTRACTION_MODEL` | Model for community name/summary generation. Uses the extraction model for consistent structured output. |

## Entity Resolution Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SEMANTIC_ENTITY_RESOLUTION` | `true` | Use embedding-based vector similarity for entity deduplication during extraction (catches semantic matches like "Museum of Crypto Art" / "MOCA"; falls back to Levenshtein when disabled). |
| `ENTITY_SIMILARITY_THRESHOLD` | `0.85` | Similarity threshold for automatic entity merging (applies to both embedding and Levenshtein modes). |

## Collections Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_COLLECTIONS` | `true` | Enable collection-based document organization. |
| `DEFAULT_COLLECTION` | `default` | Default collection name for documents uploaded without specifying a collection. |

## Vision Model Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VISION_MODEL` | — | Model for image analysis (e.g., `gpt-4o`, `claude-3-5-sonnet`, `llava`). If empty, Docling's built-in descriptions are used. |
| `VISION_MODEL_API_BASE` | Same as `OPENAI_API_BASE` | API base URL for the vision model. |
| `VISION_MODEL_API_KEY` | Same as `OPENAI_API_KEY` | API key for the vision model. |
| `VISION_MAX_CONCURRENT` | `3` | Max concurrent vision API calls system-wide. Controls the global semaphore. |
| `VISION_REASONING_MODE` | `off` | Reasoning mode applied to the vision-model call (see [Reasoning Control](#reasoning-control-ingestion-pipelines) for the value set). Off by default so reasoning multimodal models (Qwen3-VL, GLM-V) don't emit `<think>` blocks in image descriptions. |
| `VISION_MAX_OUTPUT_TOKENS` | `0` (=inherit `RELATIONSHIP_MAX_OUTPUT_TOKENS` → extraction → primary) | Output budget for the vision-model image-description call. Bump when verbose multimodal models hit the inherited cap on complex images. |

## Reasoning and UX Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STREAM_REASONING_STEPS` | `true` | Stream reasoning steps in agentic mode (visible thinking). |
| `SHOW_RETRIEVAL_STATS` | `true` | Show retrieval statistics in responses. |

## Security Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMPT_SECURITY` | `true` | Enable prompt injection detection and protection. |
| `ADMIN_EMAIL` | `admin@example.com` | Admin login email for the web interface. |
| `ADMIN_PASSWORD` | — | Admin login password. **Required.** |
| `ADMIN_API_KEY` | — | Admin API key for full backend access. **Required.** |
| `SESSION_SECRET` | — | JWT session encryption secret. Minimum 32 characters. **Required.** |
| `TRACK_ADMIN_API_KEY_USAGE` | `false` | Track usage analytics for the admin API key. |

## Frontend Customization

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL (used by the frontend to make API calls). |
| `NEXT_PUBLIC_LOGO_URL` | Cortex logo | Custom logo image URL. |
| `NEXT_PUBLIC_ACCENT_COLOR` | Cortex theme color | Custom accent color. Accepts any CSS color value: hex (`#ff6600`), rgb, hsl, or oklch (`oklch(0.79 0.18 70.67)`). |

## Compute3 Turbo Mode Configuration

> ⚠️ **On hold — not currently available.** Compute3 partnership prepared in 2025; their service is not yet in production. The variables below are kept in the codebase against future activation but have no runtime effect today. Setting `COMPUTE3_API_KEY` makes the UI toggle appear, but no live endpoint is reachable. Safe to leave all of these unset. See [Chapter 15: Turbo Mode](15-turbo-mode.md) for the full feature description (preserved for reference).

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPUTE3_API_KEY` | — | Compute3 API key. Presence of this key enables Turbo Mode in the UI. |
| `COMPUTE3_API_BASE` | `https://api.compute3.ai` | Compute3 API base URL. |
| `COMPUTE3_GPU_TYPE` | `h100` | GPU type: `h100` (recommended) or `a100`. |
| `COMPUTE3_GPU_COUNT` | `4` | Number of GPUs to allocate per job. |
| `COMPUTE3_MODEL` | `MiniMaxAI/MiniMax-M2.1` | Model to run on GPU. |
| `COMPUTE3_DOCKER_IMAGE` | `vllm/vllm-openai:latest` | vLLM Docker image for inference. |
| `COMPUTE3_DEFAULT_RUNTIME` | `3600` | Default GPU job runtime in seconds (1 hour). |

## Resource Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_FILES` | `0` (unlimited) | Maximum number of files allowed in the system. |
| `MAX_COLLECTIONS` | `0` (unlimited) | Maximum number of collections allowed. |

## Docling Configuration (Advanced)

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCLING_PAGE_CHUNK_SIZE` | `50` | Pages per processing chunk for large PDFs. |
| `DOCLING_MAX_PAGES_PER_CHUNK` | `50` | Threshold for triggering chunked PDF processing. |
| `DOCLING_USE_PYPDFIUM_FOR_LARGE_MB` | `0` | Use memory-efficient PyPdfium backend for files larger than this size (MB). 0 = disabled. |
