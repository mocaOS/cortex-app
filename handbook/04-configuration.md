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
| `OPENAI_MODEL` | `google-gemma-4-26b-a4b-it` | Model name for Q&A, research, and chat. Recommended: Gemma4 26B A4B — a blazing-fast 26B/4B-active MoE benched faster than MiniMax-M3 at similar quality, ideal for retrieval. MiniMax M3 can give slightly better results but costs the system its snappiness — not a worthwhile tradeoff. |
| `OPENAI_MODEL_FAST_MODE` | Same as `OPENAI_MODEL` | Optional faster/cheaper model for the "Fast Mode" search in Ask AI. |
| `OPENAI_MAX_OUTPUT_TOKENS` | `8000` | Floor of the output-token budget chain. All sub-tier `*_MAX_OUTPUT_TOKENS` knobs inherit from here when set to 0. 8000 comfortably covers the compact `ENT\|`/`REL\|` extraction output; tighter models simply finish under cap. See [Budget Fallback Chain](#budget-fallback-chain). |
| `OPENAI_MAX_CONTEXT` | `256000` | Floor of the input-context budget chain, sized to the recommended long-context primary (the retrieval agent's working window). `GRAPH_EXTRACTION_MAX_CONTEXT` and `RELATIONSHIP_MAX_CONTEXT` inherit when 0 — the value extraction inherits is clamped at 48K, so the large floor never leaks into extraction batch sizing. |

## Graph Extraction Configuration

These settings control the LLM used for entity extraction (Phase A) and can point to a different model/provider than the primary LLM.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_GRAPH_EXTRACTION` | `true` | Enable LLM-powered entity extraction during document ingestion. Set `false` to skip extraction entirely. |
| `GRAPH_EXTRACTION_MODEL` | Same as `OPENAI_MODEL` | Dedicated model for entity extraction and community summarization. Recommended: Qwen3.6 27B with reasoning suppressed, so it behaves like a fast instruct model that solves the task without overthinking. |
| `GRAPH_EXTRACTION_API_BASE` | Same as `OPENAI_API_BASE` | API base URL for the extraction model. |
| `GRAPH_EXTRACTION_API_KEY` | Same as `OPENAI_API_KEY` | API key for the extraction model. |
| `GRAPH_EXTRACTION_MAX_CONTEXT` | `16000` (set `0` to inherit `min(OPENAI_MAX_CONTEXT, 48000)` — the inherited value is clamped at 48K) | Max context window tokens for entity extraction batching. The default is production-validated — a batch-size / graph-density dial, **not** a "match the model's context window" setting (see [Budget Fallback Chain](#budget-fallback-chain)). Gateway-dependent: slower self-hosted gateways favor a smaller value to avoid request timeouts. Renamed from `EXTRACTION_MAX_CONTEXT` (deprecated alias still honored). |
| `EXTRACTION_MAX_OUTPUT_TOKENS` | `16000` (set `0` to inherit `OPENAI_MAX_OUTPUT_TOKENS`, 8000) | Max output tokens for entity-extraction LLM calls. A generous CEILING matched to the context, **not** a ½-of-input ratio. Dense docs are kept under it by the terse-description extraction prompt (bounds output-per-entity; enrichment restores depth), validated zero-truncation at 16000/16000. The backend logs a one-shot "output budget looks too small" warning if overflows repeat. Caveat: the cap must decode inside the request window (`LLM_REQUEST_TIMEOUT_SECONDS`) — on very slow gateways lower `GRAPH_EXTRACTION_MAX_CONTEXT` rather than raising this, or truncate-splits become slower timeout-splits. |
| `CONCURRENT_EXTRACTIONS` | `3` | Number of chunks processed in parallel during entity extraction (thread pool size). |

## Relationship Analysis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RELATIONSHIP_EXTRACTION_MODEL` | Same as `GRAPH_EXTRACTION_MODEL` | Model for relationship extraction (per-chunk and cross-document). Recommended: Qwen3.6 27B with reasoning suppressed, so it behaves like a fast instruct model that solves the task without overthinking. |
| `RELATIONSHIP_MAX_CONTEXT` | `0` (=inherit `GRAPH_EXTRACTION_MAX_CONTEXT` → primary) | Max input context window tokens for Phase 2 batch relationship analysis (legacy `llm_scan` mode; the default `targeted` mode sizes its verification calls with `RELATIONSHIP_PAIR_CONTEXT_TOKENS` instead). Recommended: leave `0` (=inherit). Bounded output does **not** bound wall time: a full-window prompt still has to be *read*, and on self-hosted GPUs a 256K prefill takes minutes and times out. Only set wide (e.g. `256000`) on fast-prefill hosted endpoints running `llm_scan` mode. |
| `RELATIONSHIP_MAX_OUTPUT_TOKENS` | `0` (=inherit `EXTRACTION_MAX_OUTPUT_TOKENS` → primary) | Output budget for **per-chunk + candidate-pair scan** (in the chain). **Migrated semantics** — see migration note below. |
| `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS` | `16000` | Output budget for **Phase 2 batch** relationship analysis. Standalone (NOT in chain) — batch processes hundreds of pairs per call and genuinely needs ~16k. |
| `RELATIONSHIP_MAX_PER_ENTITY` | `50` | Soft cap on relationships per entity. Prevents hub entities from accumulating disproportionate connections. 0 = no cap. |
| `PARALLEL_RELATIONSHIP_BATCHES` | `0` | Number of relationship batches / verification calls to process in parallel. 0 = use `CONCURRENT_EXTRACTIONS`. **Most impactful lever for relationship analysis speed.** |
| `RELATIONSHIP_DISCOVERY_MODE` | `targeted` | Step 2 (cross-document) engine: `targeted` (kNN + co-mention candidate pairs, LLM verification in small calls — minutes on large graphs) or `llm_scan` (legacy two-phase full-batch scan with multi-round discovery; `RELATIONSHIP_TARGET_RATIO`/`RELATIONSHIP_MAX_ROUNDS` only apply there). |
| `RELATIONSHIP_KNN_K` | `8` | Targeted mode: nearest neighbors per entity in the vector-index candidate scan. |
| `RELATIONSHIP_KNN_MIN_SIMILARITY` | `0.80` | Targeted mode: min Neo4j vector-index score for a kNN candidate pair. |
| `RELATIONSHIP_MIN_SHARED_DOCS` | `2` | Targeted mode: min distinct documents co-mentioning a pair (0 = disable the co-mention generator). |
| `RELATIONSHIP_DOC_FREQ_CAP` | `30` | Targeted mode: hub guard — entities mentioned in more documents than this are skipped as co-mention anchors. |
| `RELATIONSHIP_MAX_CANDIDATE_PAIRS` | `15000` | Targeted mode: total candidate-pair budget per run (top-ranked kept). |
| `RELATIONSHIP_CANDIDATES_PER_ENTITY` | `10` | Targeted mode: max candidate pairs any entity may appear in (hub guard). |
| `RELATIONSHIP_PAIRS_PER_CALL` | `40` | Targeted mode: candidate pairs verified per LLM call. |
| `RELATIONSHIP_PAIR_CONTEXT_TOKENS` | `3000` | Targeted mode: chunk-context token budget per verification call (0 = entity descriptions only). |

## Reasoning Control (ingestion pipelines)

Reasoning hurts structured extraction (drift, hidden-token cost, latency, malformed JSON). These knobs let reasoning-capable models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1, MiniMax M3) be used for ingestion while forcing their thinking OFF. Provider is auto-detected from `base_url`; model family is parsed from the model name. Works for OpenAI, OpenRouter, Venice, Anthropic, and vLLM. Accepted values: `off | minimal | auto | low | medium | high` (`none`/`disabled` are aliases for `off`).

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACTION_REASONING_MODE` | `off` | Reasoning mode for entity extraction, document summaries, community summarization, community naming, entity enrichment, and query-side entity extraction. |
| `RELATIONSHIP_REASONING_MODE` | `off` | Reasoning mode for candidate-pair scan (Phase 1), gleaning pass, per-chunk relationship extraction, and batch relationship analysis (Phase 2). |
| `VISION_REASONING_MODE` | `off` | Reasoning mode for the vision-model image-description call. Lets a reasoning multimodal model (e.g. Qwen3-VL-27B) be used as `VISION_MODEL` without `<think>` tokens leaking into descriptions. |
| `DEFAULT_REASONING_MODE` | `off` | Reasoning mode for the chat/answer path (speed researcher loop + writer + non-agentic/fast streaming). `off` suppresses hidden reasoning (Venice `disable_thinking`) for a sub-second first token and to avoid empty/timeout answers; deep-research (quality) stays AUTO. On OpenAI GPT-5/o-series, `off` can disable parallel tool calls — set `auto` there. |
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
  OPENAI_MAX_OUTPUT_TOKENS=8000           OPENAI_MAX_CONTEXT=256000
  (EXTRACTION_MAX_OUTPUT_TOKENS ships     (GRAPH_EXTRACTION_MAX_CONTEXT ships a real
   a real default of 16000; 0 = inherit)   default of 16000; explicit-0 inherit clamped at 48K)
       ↓                                       ↓
  EXTRACTION_MAX_OUTPUT_TOKENS            GRAPH_EXTRACTION_MAX_CONTEXT
       ↓                                       ↓
  RELATIONSHIP_MAX_OUTPUT_TOKENS          RELATIONSHIP_MAX_CONTEXT
       ↓
  VISION_MAX_OUTPUT_TOKENS

  RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000   (standalone, Phase 2 only)
```

**Recommended minimal stack** — configure two models + the context/budget lines below; everything else inherits:

```bash
OPENAI_MODEL=google-gemma-4-26b-a4b-it   # primary / agentic (256K window)
OPENAI_MAX_CONTEXT=256000                  # Gemma4 26B A4B's full input window (= code default since 2026-07-09)
OPENAI_MAX_OUTPUT_TOKENS=8000              # floor of the output chain (code default)

GRAPH_EXTRACTION_MODEL=qwen3-6-27b    # extraction + (inherited) relationship (256K window)
# GRAPH_EXTRACTION_MAX_CONTEXT=16000 and EXTRACTION_MAX_OUTPUT_TOKENS=16000 are the
# code defaults since 2026-07-10 (validated zero-truncation with the terse prompt) —
# only set them to deviate. RELATIONSHIP_MAX_CONTEXT stays unset — inherits 16000.
# See its table entry before widening.

VISION_MODEL=qwen3-6-27b              # image analysis — set explicitly (model name does NOT inherit; empty disables vision → Docling fallback). api_base/api_key inherit from OPENAI_*.

EMBEDDING_MODEL=text-embedding-3-small     # text embedding model (1536-dim)
EMBEDDING_DIMENSION=1536
EMBEDDING_MAX_INPUT_TOKENS=5400            # stays safely under every 8192-cap provider — see below
# Venice-only alternative: EMBEDDING_MODEL=text-embedding-qwen3-8b + EMBEDDING_DIMENSION=4096
# (native 4096, MRL 32–4096; Neo4j 5.26 supports up to 4096-dim vector indexes).
# Output budgets + all other knobs cascade through defaults.
```

`OPENAI_MAX_CONTEXT=256000` is the primary's full input window and the code default since 2026-07-09 (the old conservative default, 32768, capped Gemma4 26B A4B at a fraction of its real capability). `GRAPH_EXTRACTION_MAX_CONTEXT=16000` (the code default since 2026-07-10), by contrast, is **a batch-size / graph-density dial**, not the model's window: extraction is decode-bound — output scales with input (the model re-emits every entity/relation), and at slow decode speeds (~70 tok/s) batches sized to the full context window can't finish inside the request window (timeouts, retries, silently lost entities). So it's gateway-dependent — slower gateways favor smaller. (Set explicitly to 0, it inherits `min(OPENAI_MAX_CONTEXT, 48000)` — the inherited value is clamped at 48K for the same reason.) `EXTRACTION_MAX_OUTPUT_TOKENS=16000` (also the code default) is a generous ceiling matched to that input budget — **not** a ½-of-input ratio. The real fix for entity-dense docs is the **terse-description prompt** (bounds output-per-entity; enrichment restores depth); with it, 16000/16000 is validated zero-truncation, zero-entity-loss even on dense docs. Overflows self-heal — the batch splits and retries — but each roughly doubles that batch's wall time, and from the UI the retry churn reads as a hang. `RELATIONSHIP_MAX_CONTEXT` stays **unset**: bounded output per call does not bound wall time — the prompt still has to be prefilled, and on self-hosted GPUs a 256K read takes minutes and times out. The default `targeted` discovery mode doesn't consume this budget for verification calls anyway; only widen it (e.g. `256000`) for legacy `llm_scan` mode on fast-prefill hosted endpoints. The embedding model uses the primary `OPENAI_API_BASE` + `OPENAI_API_KEY` unless `EMBEDDING_API_BASE`/`EMBEDDING_API_KEY` overrides are set. `EMBEDDING_MAX_INPUT_TOKENS` defaults to 5400 (not 8192): the client counts tokens with cl100k, but providers validate with their **own** tokenizer, which can count 1.2–1.4× higher on punctuation-heavy text — chunks that pass an 8192 client-side check get 400-rejected upstream. 5400 × ~1.4 ≈ 7500 stays safely under every 8192-cap provider, including text-embedding-3-small (8191 cap); smaller chunks also embed more precisely into 1536-dim vectors. On self-hosted vLLM with a long-context embed model you can lift it (e.g. 32768 for Qwen3-Embedding-8B).

The default concurrency (`BATCH_PROCESSING_CONCURRENCY=2`, `CONCURRENT_EXTRACTIONS=3`, `CONCURRENT_RELATIONS=3`, `VISION_MAX_CONCURRENT=2`) is the recommended, measured setting — leave it as-is unless you have a specific reason to change it. `BATCH_PROCESSING_CONCURRENCY=2` is not a compromise: 3 concurrent documents drop per-call decode throughput (~70 → ~23 tok/s measured) and multiply timeouts, so 2 finishes multi-document builds *faster* than 3. `VISION_MAX_CONCURRENT=2` because each in-flight image spawns a multi-call chain and provider concurrent-request slots (~20/key) are the binding limit.

**Compounding behavior.** `BATCH_PROCESSING_CONCURRENCY` compounds with the two `CONCURRENT_*` knobs because they're *per-document* limits — each in-flight document can run its own pool of extraction / relationship threads. `VISION_MAX_CONCURRENT` is a global semaphore and does *not* compound. The pipeline staggers extraction, per-chunk relationships, and vision across each doc's lifecycle, so actual concurrent in-flight calls stays meaningfully below the worst-case theoretical product. Because the knobs compound, raise them cautiously — the defaults already saturate most providers.

**Targeted overrides:**
- Adjust extraction tier output independently → `EXTRACTION_MAX_OUTPUT_TOKENS=16000` (recommended; a generous ceiling matched to `GRAPH_EXTRACTION_MAX_CONTEXT` — the terse-description prompt keeps dense-doc output well under it)
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
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name. |
| `EMBEDDING_DIMENSION` | `1536` | Embedding vector dimension. Must match the model's output dimension. |
| `EMBEDDING_SEND_DIMENSIONS` | `true` | Send `dimensions` parameter to the embedding API. Models with fixed output dimensions — e.g. `Qwen/Qwen3-VL-2B-Instruct` — reject the parameter and need this set to `false`. |
| `USE_OPENAI_EMBEDDINGS` | `true` | Controls embedding *transport*, not provider. `true` = call `EMBEDDING_MODEL` via the OpenAI-compatible HTTP endpoint (works for OpenAI, OpenRouter, vLLM, any `/v1/embeddings` server). `false` = ignore `EMBEDDING_MODEL` entirely and run `sentence-transformers/all-MiniLM-L6-v2` locally inside the container. Keep `true` for Qwen3-Embedding-8B and any remote embedding model. |
| `EMBEDDING_MAX_INPUT_TOKENS` | `5400` | Per-input token cap before sending to the embeddings endpoint. Sits under the nominal 8192 because providers validate with their own tokenizer (can count 1.2–1.4× higher than the client's cl100k on punctuation-heavy text); oversized inputs are truncated client-side to avoid HTTP 400 rejections. |
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
| `BATCH_PROCESSING_CONCURRENCY` | `2` | Documents processed simultaneously during batch operations. 2 measured faster than 3 for multi-document builds (more in-flight docs drop per-call decode throughput and multiply timeouts). |
| `CONCURRENT_EXTRACTIONS` | `3` | Entity extraction thread pool size per document. |
| `PROCESSING_THREAD_WORKERS` | `4` | Thread pool workers for CPU-bound operations. |
| `VISION_MAX_CONCURRENT` | `2` | Max concurrent vision API calls system-wide (controls semaphore + thread pool). Each in-flight image spawns a multi-call chain; provider concurrent-request slots (~20/key) are the binding limit. |
| `PARALLEL_RELATIONSHIP_BATCHES` | `2` | Relationship analysis batches in parallel (1 = sequential). |

## Search and RAG Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_RERANKING` | `true` | Enable cross-encoder re-ranking for improved precision. |
| `RERANKING_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model for re-ranking. |
| `RERANKER_PRELOAD` | `false` | Eager-load the cross-encoder at startup. Off keeps idle instances lean (the first query's load is hidden behind the preceding search/LLM work). |
| `RERANKER_IDLE_TTL_SECONDS` | `1800` | Unload the idle local cross-encoder after this many seconds to reclaim ~1 GB (reloads on next query). `0` = never unload. |
| `ENABLE_HYBRID_SEARCH` | `true` | Enable hybrid (vector + keyword + graph) search. |
| `ENABLE_BATCHED_QUERY_EXTRACTION` | `true` | Batch a search's queries into one entity-extraction call + one embedding call (instead of one each per query) to cut LLM/embedding round-trips during research. |
| `VECTOR_WEIGHT` | `0.5` | Weight for vector search in RRF fusion. |
| `KEYWORD_WEIGHT` | `0.3` | Weight for keyword search in RRF fusion. |
| `GRAPH_WEIGHT` | `0.2` | Weight for graph context in RRF fusion. |
| `MAX_GRAPH_HOPS` | `2` | Max hops for graph traversal during search. |
| `MAX_CONVERSATION_HISTORY` | `6` | Max messages retained in conversation context. |
| `ENABLE_AGENTIC_RAG` | `true` | Enable multi-step agentic RAG. |
| `MAX_AGENTIC_STEPS` | `3` | Maximum steps in legacy agentic RAG pipeline. |

## Shared Model Services (cortex-helper)

For multi-instance deployments (e.g. many isolated customer stacks on one host), the heavy ML models can be hosted **once per machine** by the companion `cortex-helper` service and shared by all stacks, instead of each instance loading its own copy. Leave these unset to use the built-in local path; both fall back to local automatically if the service is unreachable.

| Variable | Default | Description |
|----------|---------|-------------|
| `RERANKER_SERVICE_URL` | — | Reranker service base URL (e.g. `http://cortex-helper:3030`). Set = no local cross-encoder is loaded. |
| `DOCLING_SERVICE_URL` | — | Docling service base URL. Set = documents convert via the warm shared service instead of a local subprocess. |
| `HELPER_SERVICE_TOKEN` | — | Shared secret sent as `X-Helper-Token`; must match the helper's `HELPER_TOKEN`. |

## Agent Research Pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_AGENT_RESEARCH` | `true` | Use agent pipeline for Deep Research mode. Set `false` for legacy fixed-step pipeline. |
| `ENABLE_AGENT_CHAT` | `true` | Use agent pipeline for standard Chat mode (required for skills in chat). |
| `RESEARCHER_MAX_ITERATIONS_SPEED` | `3` | Max agent loop iterations for Chat mode (speed). |
| `RESEARCHER_MAX_ITERATIONS_QUALITY` | `8` | Max agent loop iterations for Deep Research (quality). |
| `WRITER_MAX_TOKENS_SPEED` | `1200` | Max output tokens for Chat answers. |
| `WRITER_MAX_TOKENS_QUALITY` | `4000` | Max output tokens for Deep Research answers. |
| `RESEARCHER_SPEED_EARLY_WRITE` | `true` | Chat mode skips the agent's final "research complete" confirmation call once a search has produced sources (and no skill/git action ran) — one full LLM round-trip less per chat turn. |
| `RESEARCHER_PARALLEL_TOOL_CALLS` | `true` | Run read-only tool calls (knowledge/community/entity searches) issued in one agent turn concurrently instead of one after another. Skill and git actions always stay sequential. |
| `RESEARCHER_TOOL_ENTITY_HINTS` | `true` | Let the agent pass entity names directly on its search calls, skipping the separate query entity-extraction LLM call. |
| `RESEARCHER_SEARCH_DEDUP` | `true` | Answer an identical repeated search from a per-question cache (with a nudge to try a different angle) instead of re-running retrieval. |
| `EMIT_DONE_BEFORE_MEMORY` | `true` | Emit the SSE `done` event (with `pending_memory: true`) before the post-answer memory compaction, so the UI finalizes 1–4 s earlier; `memory_update` follows before the stream closes. Set `false` for clients that stop reading at `done`. |

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

See [Chapter 18: Agent Skills](18-skills.md) for full documentation on installing, configuring, and creating skills.

## Git Integration Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_GIT_INTEGRATION` | `false` | Master switch for the git repo connector. When disabled, the Git Integration card, endpoints, scheduler, and agent `git_repo` tool are all inactive. |
| `GIT_WORK_DIR` | `./git_repos` | Directory holding per-connection clone working copies (a cache; the graph is the source of truth). Must be writable — mount a volume in production. |
| `GIT_CLONE_DEPTH` | `1` | Shallow-clone depth. Raise only if you need deeper history; sync self-heals via full-tree reconcile regardless. |
| `GIT_MAX_REPO_SIZE_MB` | `500` | Abort a sync if the cloned repo exceeds this size. `0` = unlimited. |
| `GIT_SYNC_MAX_FILE_SIZE_MB` | `5` | Skip individual files larger than this during sync. `0` = no per-file limit. |
| `GIT_SYNC_POLL_INTERVAL` | `5` | Minutes between scheduler ticks that check connections due for a scheduled sync. |
| `GIT_HTTP_TIMEOUT` | `30` | Timeout in seconds for git provider REST API calls. |
| `GIT_HTTP_INSECURE_HOSTS` | _(empty)_ | Comma-separated hostnames for which git REST calls and clone TLS verification are skipped (opt-in, for self-hosted GitLab/Gitea with self-signed certs). Empty = verify all hosts. |

The backend image bundles the `git` binary. See [Chapter 21: Git Integration](21-git-integration.md) for the full feature guide.

## Web Import Configuration (MDHarvest powered by Crawl4ai)

Web→markdown harvesting. Cortex calls a [crawl4ai](https://github.com/unclecode/crawl4ai) service over HTTP — it never runs a browser itself. See [Chapter 23: Web Import](23-web-import.md) for the full guide.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_WEB_CRAWL` | `false` | Master switch for Web Import. The UI appears only when this is true **and** `CRAWL_SERVICE_URL` is set. |
| `CRAWL_SERVICE_URL` | _(empty)_ | Base URL of the crawl4ai service, e.g. `http://crawl4ai:11235`. Empty = feature off (there is no built-in crawler fallback). |
| `CRAWL_SERVICE_TOKEN` | _(empty)_ | Bearer token sent as `Authorization: Bearer …`; must match crawl4ai's `CRAWL4AI_API_TOKEN` (`security.api_token`). **Required for crawl4ai ≥ 0.9.0** — without a token crawl4ai serves its API only on `127.0.0.1`, unreachable from the Cortex container. |
| `CRAWL_HTTP_TIMEOUT` | `60` | Per-page crawl timeout in seconds (browser rendering of slow pages can take a while). |
| `CRAWL_CONTENT_FILTER` | `fit` | Default content filter: `fit` (readable main content), `raw` (full page), or `bm25` (relevance-ranked; needs a query). |
| `CRAWL_CONCURRENCY` | `5` | How many URLs in one import job are crawled at once. |
| `CRAWL_MAX_URLS_PER_JOB` | `100` | Maximum URLs accepted per import. `0` = unlimited. |
| `CRAWL_DISCOVER_MAX_LINKS` | `200` | Cap on candidate links returned by the Discover sub-flow. |

## x402 Payments Configuration

Pay-per-query monetization of the retrieval endpoints via the open [x402 standard](https://github.com/x402-foundation/x402). See [Chapter 17: Administration](17-administration.md#x402-payments-monetization) for the full guide.

| Variable | Default | Description |
|----------|---------|-------------|
| `X402_ENABLED` | `false` | Master switch — and deliberately the **only** x402 environment variable. When true, the **Settings → x402 Payments** section appears; recipient wallet, facilitator URL, network and asset are configured there at runtime (stored in Neo4j, survive redeploys, excluded from export/reset). Priced API keys activate once that config passes the built-in verification. |

## Community Detection Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_COMMUNITY_DETECTION` | `true` | Enable entity community detection. |
| `MIN_COMMUNITY_SIZE` | `3` | Minimum entities for a valid community. |
| `MAX_COMMUNITIES` | `50` | Maximum number of communities to track. |
| `ENABLE_GRAPH_SUMMARIZATION` | `true` | Generate LLM summaries of communities (always runs on the extraction model). |

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
| `VISION_MAX_CONCURRENT` | `2` | Max concurrent vision API calls system-wide. Controls the global semaphore. Each in-flight image spawns a multi-call chain; provider concurrent-request slots (~20/key) are the binding limit. |
| `VISION_REASONING_MODE` | `off` | Reasoning mode applied to the vision-model call (see [Reasoning Control](#reasoning-control-ingestion-pipelines) for the value set). Off by default so reasoning multimodal models (Qwen3-VL, GLM-V) don't emit `<think>` blocks in image descriptions. |
| `VISION_MAX_OUTPUT_TOKENS` | `0` (=inherit `RELATIONSHIP_MAX_OUTPUT_TOKENS` → extraction → primary) | Output budget for the vision-model image-description call. Bump when verbose multimodal models hit the inherited cap on complex images. |

## Reasoning and UX Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STREAM_REASONING_STEPS` | `true` | Stream reasoning steps in agentic mode (visible thinking). |
| `SHOW_RETRIEVAL_STATS` | `true` | Show retrieval statistics in responses. |
| `DISPLAY_FULL_SYSTEM_CONFIG` | `false` | Show advanced tuning knobs (output-token budgets, concurrency counts, chunking params, hybrid-search weights, graph hops, community sizes, similarity thresholds) in the admin **System Configuration** panel. When `false`, a curated view is shown — models, API bases, context windows, dimensions, and feature toggles stay visible. Display-only; the underlying settings are unchanged. |

## Observability (Langfuse)

Optional LLM tracing and cost tracking via a self-hosted [Langfuse](https://langfuse.com) instance. When configured, every LLM, embedding, and vision call is traced (cost, tokens, latency, errors) and each agentic Q&A is grouped into a single trace you can open and inspect — works across Venice, OpenRouter, and any OpenAI-compatible provider. Leave these blank to run without tracing; Cortex behaves identically either way (no keys = no tracing, no overhead).

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGFUSE_BASE_URL` | — | URL of your Langfuse instance (e.g. `https://langfuse.example.com`). Tracing activates only when this **and** both keys are set. |
| `LANGFUSE_PUBLIC_KEY` | — | Langfuse **project** public key (Langfuse → Project Settings → API Keys). |
| `LANGFUSE_SECRET_KEY` | — | Langfuse project secret key (paired with the public key). |
| `LANGFUSE_TRACING_ENABLED` | `true` | Master off-switch — set `false` to disable tracing even when keys are present. |
| `LANGFUSE_SAMPLE_RATE` | `1.0` | Fraction of requests traced (`0.0`–`1.0`). Lower it on high-traffic instances to reduce volume. |
| `LANGFUSE_LOG_EXTENDED` | `false` | Content logging mode. `false` (default) redacts **all** prompt/completion/tool/embedding/vision text before it leaves the app — only structure (roles, model, tool names + arg keys, tokens, cost, latency) reaches Langfuse. Set `true` to log full content for local debugging. |

> **Accurate cost:** Langfuse prices a call by matching the model name against price definitions in your project. Venice/OpenRouter models aren't in Langfuse's built-in catalog, so add them under the project's **Models** settings to get USD cost (token counts are tracked regardless).

## Error Tracking (GlitchTip)

Optional crash and error reporting via a self-hosted [GlitchTip](https://glitchtip.com) instance (Sentry-protocol compatible, hence the `SENTRY_*` names). Backend and frontend report to **separate GlitchTip projects**: create one project per app in GlitchTip and use each project's DSN. Leave the DSNs blank to run without error tracking; Cortex behaves identically either way.

Backend variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTRY_DSN` | — | DSN of the backend's GlitchTip project. When set, unhandled API exceptions and ERROR-level logs (including background ingestion and the document-conversion worker) are reported with source-context lines and a `request_id` tag that matches the `X-Request-ID` in logs and responses. |
| `SENTRY_ENVIRONMENT` | — | Environment label on issues (e.g. `production`, a tenant slug). Empty falls back to `ENVIRONMENT`. |
| `SENTRY_RELEASE` | — | Optional deploy tag (e.g. git SHA). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` | `0` = errors only. Set `0.0`–`1.0` to sample performance transactions (GlitchTip supports them). |
| `SENTRY_MAX_REQUEST_BODY_SIZE` | `never` | Whether request bodies are attached to events (`never`/`small`/`medium`/`always`). Bodies can contain question text and document content — raise only while debugging. |
| `SENTRY_SEND_DEFAULT_PII` | `false` | Attach IPs/cookies/user context to events. |

Frontend variables (the compose files map a single `SENTRY_DSN_FRONTEND` value onto both):

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_SENTRY_DSN` | — | Frontend project DSN, inlined into the browser bundle at **build time**. |
| `SENTRY_DSN` (frontend container) | — | Same DSN, read at runtime by the Next.js server side (Server Components / proxy errors). |
| `NEXT_PUBLIC_SENTRY_ENVIRONMENT` / `SENTRY_ENVIRONMENT` | — | Environment label, as backend. |
| `SENTRY_URL`, `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` | — | **Build-time only.** When all set, `next build` uploads source maps to GlitchTip (debug-ID artifact bundles, GlitchTip ≥ 4.2) so production stack traces show your original TypeScript instead of minified chunks; the `.map` files are deleted after upload and never served. The token needs the `project:releases` scope. Unset = build unchanged. |

## Security Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | Set to `production` to fail fast at startup on weak/default secrets (`NEO4J_PASSWORD` empty/`password123`, or `SESSION_SECRET` < 32 chars when `ADMIN_PASSWORD` is set). |
| `CORS_ALLOWED_ORIGINS` | `*` | Comma-separated allowed origins. `*` allows any origin with credentials disabled (auth is header-based); set an explicit allowlist for production. |
| `EXPOSE_API_DOCS` | `auto` | Interactive API docs (`/docs`, `/redoc`, `/openapi.json`). `auto` = on in development, off in production (prevents unauthenticated API-schema disclosure). Set `true`/`false` to force. See [Chapter 5](05-security.md#api-documentation-exposure). |
| `PROMPT_SECURITY` | `true` | Enable prompt injection detection and protection. |
| `ADMIN_EMAIL` | `admin@example.com` | Admin login email for the web interface. |
| `ADMIN_PASSWORD` | — | Admin login password. **Required.** |
| `ADMIN_API_KEY` | — | Admin API key for full backend access. **Required.** |
| `SESSION_SECRET` | — | JWT session encryption secret. Minimum 32 characters. **Required.** |
| `TRACK_ADMIN_API_KEY_USAGE` | `false` | Track usage analytics for the admin API key. |
| `ENCRYPTION_KEY` | — | At-rest encryption key(s) for user-supplied secrets: git connector PATs and secret-typed skill config fields. Comma-separated Fernet keys — the first encrypts, all decrypt (rotation support). **Strongly recommended.** Without it, these secrets are stored in plaintext and a warning is logged at startup. |

### Secret Encryption (`ENCRYPTION_KEY`)

Cortex encrypts user-supplied secrets at rest when `ENCRYPTION_KEY` is set:

- **Git connector PATs** — stored encrypted on the connection record in Neo4j.
- **Skill secrets** — secret-typed config fields (API keys etc.) entered in the skill config wizard, stored encrypted in the skill's `config.json`.

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Behavior:

- **Enabling later is safe.** Existing plaintext secrets are encrypted automatically on the next startup (idempotent migration).
- **Key rotation (zero downtime):** prepend the new key while keeping the old one — `ENCRYPTION_KEY=<new-key>,<old-key>` — and restart. Startup re-encrypts all secrets with the new key; afterwards drop the old key.
- **Lost/changed key:** affected secrets cannot be recovered. Git syncs and skill activations fail with a clear "re-enter the credential" error (ciphertext is never used as a credential); re-enter the PAT / skill secret in the admin UI.
- **Library exports never contain secrets:** secret-typed skill config fields are stripped from export archives regardless of encryption, and git connections are never exported. After importing a library, re-enter skill secrets via the config wizard.

## Frontend Customization

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL (used by the frontend to make API calls). |
| `NEXT_PUBLIC_LOGO_URL` | Cortex logo | Custom logo image URL. |
| `ACCENT_COLOR` | Cortex theme color | Custom accent color. Accepts any CSS color value: hex (`#ff6600`), rgb, hsl, or oklch (`oklch(0.79 0.18 70.67)`). Read server-side at runtime — deliberately **not** `NEXT_PUBLIC_`-prefixed, so it applies without a frontend rebuild. |

## Resource Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_FILES` | `0` (unlimited) | Maximum number of files allowed in the system. |
| `MAX_COLLECTIONS` | `0` (unlimited) | Maximum number of collections allowed. |
| `MAX_QUERIES_PER_MONTH` | `0` (unlimited) | Monthly usage quota in **units** (internal LLM completions). Both questions (each Q&A turn uses a handful of units) and document imports/graph builds (a few units per file) draw from the same pool; embeddings are free. When the quota is reached, new questions and new document processing return `429` until the next UTC month — work already in flight always finishes. Usage appears as a meter bar on the Settings page (Statistics panel). |

## Docling Configuration (Advanced)

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCLING_PAGE_CHUNK_SIZE` | `50` | Pages per processing chunk for large PDFs. |
| `DOCLING_MAX_PAGES_PER_CHUNK` | `50` | Threshold for triggering chunked PDF processing. |
| `DOCLING_USE_PYPDFIUM_FOR_LARGE_MB` | `0` | Use memory-efficient PyPdfium backend for files larger than this size (MB). 0 = disabled. |
| `DOCLING_CONVERSION_TIMEOUT` | `600` | Hard ceiling in seconds on a single **local** Docling conversion. On timeout the worker subprocess is killed and the document is marked failed (instead of hanging in "processing" on a large/corrupt file). Does not apply to the remote `DOCLING_SERVICE_URL` path. |

## Efficiency Flags & Hardening (v-next)

Performance flags (each can be disabled per stack):

| Variable | Default | Description |
|----------|---------|-------------|
| `ENTITY_DEDUP_PREFILTER` | `true` | Faster entity dedup on large graphs (fulltext prefilter). |
| `ENABLE_BATCHED_KG_WRITES` | `true` | UNWIND-batched graph writes (~10 DB round trips per document instead of hundreds). |
| `ENABLE_BATCHED_CHUNK_RELATIONSHIPS` | `true` | Several chunks per relationship-extraction LLM call (÷~4 calls). |
| `RELATIONSHIP_CHUNKS_PER_CALL` | `4` | Chunks per batched call. |
| `ENABLE_PHASEB_CHECKPOINTING` | `false` | Resume cross-document analysis after a crash; reuse candidates across rounds. |
| `ENABLE_REPROCESS_DELTA` | `false` | Skip reprocessing of unchanged documents (git re-syncs become free). |
| `ENABLE_INGEST_RESUME` | `true` | Mid-document checkpoint: an interrupted processing run (restart, LLM outage) resumes from stored chunks + extraction watermark instead of restarting from zero. |
| `LLM_OUTAGE_MAX_WAIT_SECONDS` | `900` | How long processing waits (with backoff probes) for an unreachable LLM endpoint before failing the document — with its checkpoint intact. |
| `RESEARCHER_STABLE_PROMPT` | `true` | Prompt-cache-friendly researcher loop. |
| `ENABLE_PROMPT_CACHE_CONTROL` | `false` | Anthropic prompt caching via OpenRouter. |

Hardening & operations:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_FORMAT` | `plain` | `json` for structured logs with request-ID correlation. |
| `METRICS_ENABLED` | `true` | Prometheus `GET /metrics` (admin key required). |
| `RATE_LIMIT_QPM` / `RATE_LIMIT_BURST` | `0` / `10` | Per-API-key burst guardrail on ask/upload (429 + Retry-After). |
| `MAX_REQUEST_BODY_MB` / `MAX_IMPORT_BODY_MB` | `32` / `2048` | Request-body ceilings (413 on excess); upload routes use `MAX_FILE_SIZE_MB` + slack. |
| `MIN_FREE_DISK_MB` | `500` | Refuse uploads/imports with 507 when the disk would drop below this floor; disk gauges in `/metrics` + `/api/stats`. |
| `LLM_REQUEST_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `360` / `2` | Transport limits for every LLM call (0 timeout = SDK default). |
| `AUTO_RESUME_PENDING_ON_STARTUP` | `true` | Resume a pipeline run killed by a restart (quota-guarded) — stranded documents, a queued batch that never started, or an interrupted Step 2/3; the Generate Graph chain is persisted, so a resumed Step 1 still continues into Steps 2 and 3. |
| `AUTO_RESUME_IMAGE_ANALYSIS` | `true` | Resume image analysis killed by a restart. A restart leaves completed documents with unfinished image analysis stuck forever (the counters freeze at `current < total`); on boot Cortex re-extracts their images via local Docling re-conversion (no LLM cost) and analyzes **only** the images not yet stored — already-analyzed images are never re-paid for. Set `false` to require a manual reprocess instead. |
| `ENABLE_AUDIT_LOG` / `AUDIT_LOG_PATH` | `false` / `./logs/audit.log` | Append-only JSONL audit trail (metadata only, never content). |
| `RESEARCHER_WALL_CLOCK_SECONDS` | `0` | Time budget for deep research (0 = unlimited). |
| `RERANK_TOP_K` | `15` | Rerank candidate pool size. |
| `HELPER_STRICT_REMOTE` | `false` | Never fall back to local docling when the shared helper is configured. |
| `INSTANCE_ID` | hostname | Tenant identity for helper fair-queuing. |
| `NEO4J_MAX_POOL_SIZE` / `NEO4J_CONNECTION_TIMEOUT` / `NEO4J_CONNECTION_ACQUISITION_TIMEOUT` | `100` / `10` / `60` | Database driver pool tuning. |
| `CORTEX_NEO4J_MEM_LIMIT`, `CORTEX_NEO4J_HEAP_MAX`, `FRONTEND_MEM_LIMIT` | `4g`, `2G`, `1g` | Compose memory caps per service. The neo4j caps are deliberately **not** `NEO4J_`-prefixed: PaaS platforms inject all env vars into every container, and neo4j parses any `NEO4J_*` var as a config setting (strict_validation then refuses to boot). |
| `BACKUP_INTERVAL_SECONDS`, `BACKUP_RETENTION_DAYS`, `BACKUP_INITIAL_DELAY_SECONDS` | `86400`, `7`, `120` | Backup sidecar (overlay `docker-compose.backup.yml`; also in the Coolify/Dokploy composes). Verified server-side APOC export — retention rotates only after a verified success. |
