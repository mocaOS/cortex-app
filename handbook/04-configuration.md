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

## Graph Extraction Configuration

These settings control the LLM used for entity extraction (Phase A) and can point to a different model/provider than the primary LLM.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_GRAPH_EXTRACTION` | `true` | Enable LLM-powered entity extraction during document ingestion. Set `false` to skip extraction entirely. |
| `GRAPH_EXTRACTION_MODEL` | Same as `OPENAI_MODEL` | Dedicated model for entity extraction and community summarization. Recommended: instruction-following models (e.g. Mistral Small 24B, Ministral 14B). |
| `GRAPH_EXTRACTION_API_BASE` | Same as `OPENAI_API_BASE` | API base URL for the extraction model. |
| `GRAPH_EXTRACTION_API_KEY` | Same as `OPENAI_API_KEY` | API key for the extraction model. |
| `EXTRACTION_MAX_CONTEXT` | `32768` | Max context window tokens for entity extraction batching. Must match the extraction model's actual context window. |
| `CONCURRENT_EXTRACTIONS` | `3` | Number of chunks processed in parallel during entity extraction (thread pool size). |

## Relationship Analysis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RELATIONSHIP_EXTRACTION_MODEL` | Same as `GRAPH_EXTRACTION_MODEL` | Model for relationship extraction (per-chunk and cross-document). Recommended: instruction-following models (e.g. OpenAI GPT OSS 120B). |
| `RELATIONSHIP_MAX_CONTEXT` | `65536` | Max input context window tokens for relationship analysis batching. Must match the `RELATIONSHIP_EXTRACTION_MODEL` context window. |
| `RELATIONSHIP_MAX_OUTPUT_TOKENS` | `16000` | Max output tokens for relationship analysis LLM responses. |
| `RELATIONSHIP_MAX_PER_ENTITY` | `50` | Soft cap on relationships per entity. Prevents hub entities from accumulating disproportionate connections. 0 = no cap. |
| `PARALLEL_RELATIONSHIP_BATCHES` | `0` | Number of relationship batches to process in parallel. 0 = use `CONCURRENT_EXTRACTIONS`. **Most impactful lever for relationship analysis speed.** |
| `AUTO_RELATIONSHIP_ANALYSIS_AFTER_BATCH` | `false` | Automatically run relationship analysis after batch document processing completes. |
| `AUTO_COMMUNITY_DETECTION_AFTER_BATCH` | `false` | Automatically run community detection after relationship analysis completes. |

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

## Embedding Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model name. |
| `EMBEDDING_DIMENSION` | `1536` | Embedding vector dimension. Must match the model's output dimension. |
| `EMBEDDING_SEND_DIMENSIONS` | `true` | Send `dimensions` parameter to the embedding API. Set `false` for models with fixed output dimensions (e.g., `qwen3-vl-embedding-2b`). |
| `USE_OPENAI_EMBEDDINGS` | `true` | Use OpenAI-compatible API for embeddings. Set `false` to use local sentence-transformers. |
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
| `RESEARCHER_MAX_ITERATIONS_QUALITY` | `10` | Max agent loop iterations for Deep Research (quality). |
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
