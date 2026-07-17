# Environment Configuration

Copy `.env.example` to `.env`. Variables are grouped by concern below.

## Deployment & CORS

- `ENVIRONMENT` (default: `development`; the `docker-compose.prod.yml`, coolify, and dokploy compose files default it to `production` via `${ENVIRONMENT:-production}`, overridable) — set to `production` to enforce secret hardening at startup. A Pydantic validator (`config.py:_enforce_production_secrets`) refuses to boot when `NEO4J_PASSWORD` is empty/`password123`/a known placeholder, when `SESSION_SECRET` is shorter than 32 chars or a known placeholder while `ADMIN_PASSWORD` is set, or when `ADMIN_PASSWORD`/`ADMIN_API_KEY` is a known placeholder. Development keeps the convenient defaults. The Next.js frontend applies the same posture to `SESSION_SECRET` (`lib/session.ts`): a missing/short/placeholder value throws at startup when `NODE_ENV=production`, and warns (with a dev fallback) otherwise.
- `CORS_ALLOWED_ORIGINS` (default: `*`) — comma-separated allowlist of origins (e.g. `https://app.example.com,https://admin.example.com`). Wildcard `*` allows any origin but, per spec, with **credentials disabled** — safe here because all auth is header-based (`X-API-Key`), never cookies. An explicit allowlist re-enables credentialed CORS. Parsed by `config.cors_origins_list`; applied in `main.py`. A startup WARN fires when running wildcard.

## Database

- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` — Neo4j connection (production refuses the default `password123`; see Deployment above)
- `CORTEX_NEO4J_TX_TIMEOUT` (compose-level, default: 300s) — maps to `NEO4J_db_transaction_timeout` in the prod/coolify/dokploy composes: server-side kill switch for runaway Cypher so one unbounded query can't pin a pool connection + API worker thread forever. (Uses the `CORTEX_NEO4J_` prefix pattern — never plain `NEO4J_*` operator vars on PaaS, which inject all env into the neo4j container and break boot.)
- Backend resilience: idempotent reads/upserts in `neo4j_service.py` are wrapped with `@retry_on_transient` (3 attempts, backoff) against `ServiceUnavailable`/`SessionExpired`/`TransientError`; schema init retries at startup with backoff, then in the background — `/health` reports `schema_initialized` and returns **HTTP 503** while degraded (Neo4j down or schema unconfirmed), so compose healthchecks/`depends_on` gates actually gate; backend healthchecks carry `start_period: 60s`.

## Primary LLM

- `OPENAI_API_KEY`, `OPENAI_MODEL` (default: google-gemma-4-26b-a4b-it) — Primary LLM for Q&A, research, and chat. Recommended: Gemma4 26B A4B — a blazing-fast 26B/4B-active MoE benched faster than MiniMax-M3 at similar quality, ideal for retrieval. MiniMax M3 can give slightly better results but costs the system its snappiness — not a worthwhile tradeoff
- `OPENAI_API_BASE` — for LiteLLM-compatible providers
- `LLM_REQUEST_TIMEOUT_SECONDS` (default: 360), `LLM_MAX_RETRIES` (default: 2) — transport limits applied by the `llm_config` client factories to every LLM client (unless the call site passes its own). Replaces the SDK's 600s default so a hung provider connection can't pin an extraction slot for 10 minutes. `0` timeout restores the SDK default; streaming reads are bounded between chunks, not whole-stream. Since 2026-07-09 the graph-extractor tier clients (main/extraction/relationship) no longer pass their own values — they used to hardcode 120s/2 retries, which shadowed this env: every "361s batch timeout" in the field was actually 3×120s SDK attempts re-sending the same large prompt into a saturated endpoint. Extraction calls whose caller owns the retry strategy (entity batch split-retry, per-chunk single-chunk fallback) now go through `max_retries=0` one-shot twins (`_oneshot_async_client`); 429s on the entity path are requeued whole with bounded exponential backoff instead of SDK-retried.
- `OPENAI_MAX_OUTPUT_TOKENS` (default: 8000) — floor of the output-token budget chain. Sub-tier `*_MAX_OUTPUT_TOKENS` knobs inherit when set to 0. 8000 is generous enough that verbose-XML models (Qwen3-family) don't truncate `<relationship>` output; tighter models simply finish under cap with no cost penalty. See [Budget Fallback Chain](#budget-fallback-chain).
- `OPENAI_MAX_CONTEXT` (default: 256000 since 2026-07-09, was 32768) — floor of the input-context budget chain, sized to the recommended primary (Gemma4 26B A4B, the retrieval agent's working context). `GRAPH_EXTRACTION_MAX_CONTEXT` ships its own real default (16000) and only inherits on explicit 0 — the inherited value is clamped at 48000. `RELATIONSHIP_MAX_CONTEXT` inherits the extraction budget when 0.

## Extraction LLM

- `GRAPH_EXTRACTION_MODEL`, `GRAPH_EXTRACTION_API_BASE`, `GRAPH_EXTRACTION_API_KEY` — Extraction model for entity extraction, community summarization, and query-side entity extraction (RAG search). Qwen3.6 27B recommended, with reasoning suppressed so it behaves like a fast instruct model that solves the task without overthinking. Defaults to primary model equivalents.
- `GRAPH_EXTRACTION_MAX_CONTEXT` (default: 16000 since 2026-07-10, was 0 = inherit; explicit 0 restores inherit of `OPENAI_MAX_CONTEXT`, **inherited value clamped at 48000**) — input context budget for entity extraction batching. A batch-size / graph-density dial, sized to the provider's decode speed, not the model window (2026-07-08: 256k-context prompts never completed on Venice qwen3 at ~70 tok/s). The 16000 default is validated zero-truncation / zero-entity-loss with the terse-description prompt; slower gateways favor smaller. Explicit values are honored unclamped.
- `EXTRACTION_MAX_OUTPUT_TOKENS` (default: 16000 since 2026-07-10, was 12000 since 2026-07-09, originally 0 = inherit `OPENAI_MAX_OUTPUT_TOKENS` = 8000; set 0 explicitly to restore inherit) — output budget for entity-extraction LLM calls (`graph_extractor.py:821/1014/1840`). **A generous CEILING matched to `GRAPH_EXTRACTION_MAX_CONTEXT` (16000/16000 is the validated default pairing), NOT a ½-ratio — the terse-description prompt in graph_extractor.py bounds output-per-entity so dense docs stay under it.** Extraction re-emits every entity, so without the terse prompt output scales with input: entity-dense docs overflowed an 8000 cap on 24k batches (live incident 2026-07-08: ~10 overflow split-retries per book, each ~doubling that batch's wall time; 12000 → zero). Competing constraint: the cap must also fit the provider's decode window (`tok/s × attempt window`; the attempt window is `LLM_REQUEST_TIMEOUT_SECONDS` since 2026-07-09 — it was the then-hardcoded 120s when the Venice numbers were measured: ~70 tok/s × ~120s ≈ 8400; 16000 there just converted truncate-splits into slower timeout-splits, 2026-07-08). If they conflict, lower `GRAPH_EXTRACTION_MAX_CONTEXT` instead. The extraction loop logs a one-shot "output budget looks too small" diagnosis when overflows repeat (≥3 with ≥25% rate), plus per-batch usage telemetry and an end-of-run health summary (also persisted as `extraction_stats` on the Document node).

## Relationship LLM

See [`.claude/domain/relationships.md`](domain/relationships.md) for how these are used in the two-phase pipeline.

- `RELATIONSHIP_EXTRACTION_MODEL`, `RELATIONSHIP_EXTRACTION_API_BASE`, `RELATIONSHIP_EXTRACTION_API_KEY` — dedicated LLM model for all relationship extraction work (per-chunk in Step 1 + batch analysis in Step 2). Qwen3.6 27B recommended, with reasoning suppressed so it behaves like a fast instruct model that solves the task without overthinking. Defaults to extraction model equivalents. Config properties: `rel_extraction_model`, `rel_extraction_api_base`, `rel_extraction_api_key` with fallback chain: relationship model -> extraction model -> main model. Uses `get_relationship_llm_config()` from `llm_config.py`.
- `CONCURRENT_RELATIONS` (default: 3) — concurrent per-chunk relationship extractions per document (separate rate limit from entity extraction)
- `RELATIONSHIP_MAX_CONTEXT` (default: 0 = inherit `GRAPH_EXTRACTION_MAX_CONTEXT` → primary) — input context budget for Phase 2 batch relationship analysis (legacy `llm_scan` mode; also feeds the Step-1 chunk-batch char budget at `document_processor.py` ~60%·4 chars/tok, dormant under the 4-chunks/call cap). **Leave at 0.** Bounded per-call output does not bound prefill time — a 256k value that works on fast hosted endpoints (Venice) produces multi-minute prefills + timeouts on self-hosted GPUs. Explicit values bypass the 48k inherit-clamp. The default `targeted` mode sizes verification calls with `RELATIONSHIP_PAIR_CONTEXT_TOKENS`, not this.
- `RELATIONSHIP_MAX_OUTPUT_TOKENS` (default: 0 = inherit `EXTRACTION_MAX_OUTPUT_TOKENS` → primary) — output budget for **per-chunk** + **candidate-pair scan** (lines 1962/2026/2068/2137). **NOTE: This env var changed meaning** — previously it was the Phase 2 batch budget (16000). Migrate that semantic to `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS`.
- `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS` (default: 16000) — output budget for **Phase 2 batch** analysis (`graph_extractor.py:2270`). Standalone, NOT in the inheritance chain (batches process hundreds of entity pairs per call and need ~16k).
- `PARALLEL_RELATIONSHIP_BATCHES` (default: 5) — number of relationship analysis batches to process in parallel
- `RELATIONSHIP_TARGET_RATIO` (default: 1.0) — target relationships-per-entity ratio (ERR) for admin monitoring
- `RELATIONSHIP_MAX_ROUNDS` (default: 3) — max auto-discovery rounds for initial analysis ("Find more" always does 1 round). Stops early if target ratio reached.
- `RELATIONSHIP_MAX_HOURS` (default: 0) — max hours for relationship generation (0 = no time limit, completes all rounds)
- `RELATIONSHIP_MAX_PER_ENTITY` (default: 50) — soft cap on relationships per entity during analysis (0 = no cap). When both endpoints are saturated, the relationship is skipped.

### Targeted Phase B discovery (Step 2 v2, default mode)

Candidates come from the entity-embedding vector index + document co-mention (no LLM); the LLM only verifies ranked pairs. See [`.claude/domain/relationships.md`](domain/relationships.md#phase-b-step-2--discovery-modes).

- `RELATIONSHIP_DISCOVERY_MODE` (default: `targeted`) — `targeted` (kNN + co-mention candidates, LLM pair verification) or `llm_scan` (legacy two-phase full-batch scan; `RELATIONSHIP_TARGET_RATIO`/`RELATIONSHIP_MAX_ROUNDS` only apply there)
- `RELATIONSHIP_KNN_K` (default: 8) — nearest neighbors per entity in the vector-index candidate scan
- `RELATIONSHIP_KNN_MIN_SIMILARITY` (default: 0.80) — min Neo4j vector-index score for a kNN candidate pair
- `RELATIONSHIP_MIN_SHARED_DOCS` (default: 2) — min distinct documents co-mentioning a pair for the co-mention generator (0 = disable generator)
- `RELATIONSHIP_DOC_FREQ_CAP` (default: 30) — hub guard: entities mentioned in more documents than this are skipped as co-mention anchors
- `RELATIONSHIP_MAX_CANDIDATE_PAIRS` (default: 15000) — total candidate-pair budget per run (top-ranked kept)
- `RELATIONSHIP_CANDIDATES_PER_ENTITY` (default: 10) — max candidate pairs any entity may appear in (hub guard)
- `RELATIONSHIP_PAIRS_PER_CALL` (default: 40) — candidate pairs verified per LLM call
- `RELATIONSHIP_PAIR_CONTEXT_TOKENS` (default: 3000) — chunk-context token budget per verification call (0 = entity descriptions only)

## Reasoning Control (ingestion)

Force reasoning OFF on capable models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1, MiniMax) so they can be used for structured extraction without the drift, hidden-token cost, latency, and malformed JSON that reasoning causes on these tasks. Implementation: `backend/app/services/reasoning_config.py`. Backend detected from `base_url`; model family by regex on the model string. Works for OpenAI, OpenRouter, Venice, Anthropic, and vLLM.

Accepted values for all three modes: `off | minimal | auto | low | medium | high` (also accepts `none`/`disabled` as aliases for OFF, and `default` as alias for AUTO).

- `EXTRACTION_REASONING_MODE` (default `off`) — applied to entity extraction, document summaries, community summarization, community naming, entity enrichment, query-side entity extraction.
- `RELATIONSHIP_REASONING_MODE` (default `off`) — applied to candidate-pair scan (Phase 1), gleaning pass, per-chunk relationship extraction, batch relationship analysis (Phase 2).
- `VISION_REASONING_MODE` (default `off`) — applied to the vision-model call in `vision_analyzer.py`. Lets you use the same reasoning-capable multimodal model (e.g. Qwen3-VL-27B) as both `GRAPH_EXTRACTION_MODEL` and `VISION_MODEL` without `<think>` tokens leaking into image descriptions. Vision uses raw httpx (not the OpenAI SDK), so the helper flattens `extra_body` into the top-level JSON body and runs its own one-shot 400-fallback (`is_reasoning_unsupported` / `mark_reasoning_unsupported`).
- `DEFAULT_REASONING_MODE` (default `off`) — the **chat/answer path**: the speed-mode researcher loop + the answer writer (`researcher_agent.py`) and the non-agentic streaming writer + fast-search path (`main.py`), all routed through `safe_chat_completion`. Default `off` because on reasoning-capable models (esp. Venice) hidden chain-of-thought streams in a separate `reasoning_content` channel — adding 3–14s before the first answer token and, across the multi-iteration agent loop, frequently exhausting the budget into **empty/timeout answers**. `off` (Venice `disable_thinking`) cuts model time-to-first-token to <1s. Deep-research (`quality`) mode is unaffected — it stays AUTO and keeps reasoning. Caveat: on **OpenAI** GPT-5/o-series, `off` maps to `reasoning_effort` `none`/`minimal`, which can disable parallel tool calls in the agent loop — OpenAI-backed operators who rely on that can set `DEFAULT_REASONING_MODE=auto` to restore provider-default thinking on chat.
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

- `VISION_MAX_CONCURRENT` (default: 2) — max concurrent vision API calls system-wide for image analysis (controls semaphore + thread pool sizing). Each in-flight image chains up to 4 provider calls; gateways with ~20 concurrent-slot limits (Venice) 429 at 3 alongside multi-doc extraction.
- `VISION_REASONING_MODE` (default: `off`) — see [Reasoning Control (ingestion)](#reasoning-control-ingestion). Suppresses `<think>` output on reasoning multimodal models (Qwen3-VL, GLM-V, etc.) so image descriptions stay clean.
- `VISION_MAX_OUTPUT_TOKENS` (default: 0 = inherit `RELATIONSHIP_MAX_OUTPUT_TOKENS` → `EXTRACTION_MAX_OUTPUT_TOKENS` → `OPENAI_MAX_OUTPUT_TOKENS`) — output budget for the vision-model image-description call (`vision_analyzer.py:304`).
- `VISION_MIN_IMAGE_SIDE` (default: 64) — minimum image side (pixels) before `analyze_image_with_vision_model` calls the API. PDFs expose bullets/icons/separators as `PictureItem`s; hosted vision APIs reject sub-64px images (Venice returns HTTP 400 *"Supplied image did not pass validation checks"*). Below the threshold Cortex skips the call and lets `process_single_image` fall back to Docling's description (or "no description available"). Set 0 to disable the pre-filter.
- `VISION_MAX_IMAGE_SIDE` (default: 1568) — downscale-cap on the longer side before the base64 data-URL encode in `vision_analyzer._pil_to_data_url`. Cortex renders PDF pages at 2× DPI (typical 2400×1700) — without downscaling the base64 blob bloats into hundreds of KB. Some providers (custom LiteLLM/vLLM deployments) tokenize the base64 payload as text and overflow context windows (one customer hit 184K input tokens against a 192K cap). 1568 matches Claude's recommended max side: OCR-grade legible while keeping JPEG payloads under ~700 KB. Resize uses Lanczos and preserves aspect ratio via `Image.thumbnail`. Set 0 to disable downscaling.
- `VISION_JPEG_QUALITY` (default: 85) — JPEG quality (1–95) used in `_pil_to_base64` for opaque images. 85 is the visually-near-lossless sweet spot at ~5–10× smaller than PNG. Images with alpha (mode `RGBA`) still use PNG.

## Budget Fallback Chain

`backend/app/config.py` exposes `@property` accessors that resolve token / context budgets through a parent chain when the raw env var equals `0`. Same idiom as the existing model-name fallback (`extraction_model` → `openai_model`) but for ints, with `0` as the inherit sentinel (consistent with `MAX_FILES=0` etc.).

**Output tokens chain:** `VISION_*` → `RELATIONSHIP_*` → `EXTRACTION_*` → `OPENAI_MAX_OUTPUT_TOKENS=8000`
**Input context chain:** `RELATIONSHIP_MAX_CONTEXT` → `GRAPH_EXTRACTION_MAX_CONTEXT=16000` → `OPENAI_MAX_CONTEXT=256000` (extraction ships a real 16000 default; explicit-0 inherit is clamped at 48000; `EXTRACTION_MAX_OUTPUT_TOKENS` ships 16000 instead of 0-inherit)
**Standalone:** `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000` (Phase 2 batch only — not in chain)

Recommended minimal config when running a 3-tier stack (= what `.env.recommended` ships):
```env
OPENAI_MODEL=google-gemma-4-26b-a4b-it   # primary / agentic (256K window)
OPENAI_MAX_CONTEXT=256000                # Gemma4 26B A4B full input window (= code default since 2026-07-09)
GRAPH_EXTRACTION_MODEL=qwen3-6-27b  # extraction + (inherited) relationship (256K window)
VISION_MODEL=qwen3-6-27b            # image analysis (does NOT inherit from extraction; api_base/api_key inherit from OPENAI_*)
EMBEDDING_MODEL=text-embedding-3-small   # 1536-dim; model + dimension are the code defaults
# Extraction budgets (GRAPH_EXTRACTION_MAX_CONTEXT / EXTRACTION_MAX_OUTPUT_TOKENS)
# ship tuned 16000/16000 defaults since 2026-07-10 — no need to set them.
# Output budgets cascade automatically. EMBEDDING_MAX_INPUT_TOKENS default (5400)
# stays under gateway-side token validators (Venice counts with its own tokenizer).
# Self-hosted vLLM users can lift to 32768.
# Venice-only higher-dim alternative: EMBEDDING_MODEL=text-embedding-qwen3-8b +
# EMBEDDING_DIMENSION=4096 (native; Neo4j 5.26 supports 4096-dim vector indexes).
```

Concurrency reference (all values ARE the shipped defaults — production-measured; listed so nobody "optimizes" them upward):
```env
BATCH_PROCESSING_CONCURRENCY=2    # docs in parallel (default 2 — 3 drops per-call decode ~70→~23 tok/s)
CONCURRENT_EXTRACTIONS=3          # entity-extraction threads per doc (default 3) — biggest multiplier
CONCURRENT_RELATIONS=3            # per-chunk relationship threads per doc (default 3)
VISION_MAX_CONCURRENT=2           # system-wide vision semaphore (default 2 — ~20 provider slots/key bind first)
```
`BATCH_PROCESSING_CONCURRENCY` compounds with the two `CONCURRENT_*` knobs (per-doc pools); `VISION_MAX_CONCURRENT` is a global semaphore and stays flat. The pipeline staggers extraction / relationships / vision across each doc's lifecycle, so actual in-flight concurrency stays below the worst-case product. Resist raising `BATCH_PROCESSING_CONCURRENCY` — 2 measured faster than 3 on Venice (shared decode rate); dial `CONCURRENT_EXTRACTIONS` down first on smaller providers.

**Migration:** the env var `RELATIONSHIP_MAX_OUTPUT_TOKENS` was previously the Phase 2 batch budget (16000). It now drives **per-chunk + candidate scan** instead, and the Phase 2 batch value lives in the new `RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS=16000`. Users who explicitly set `RELATIONSHIP_MAX_OUTPUT_TOKENS=16000` will see per-chunk extraction also get 16000 tokens (overkill but harmless — model finishes well below cap).

**Migration:** `EXTRACTION_MAX_CONTEXT` → `GRAPH_EXTRACTION_MAX_CONTEXT` — env var renamed to match the `GRAPH_EXTRACTION_MODEL`/`GRAPH_EXTRACTION_API_BASE`/`GRAPH_EXTRACTION_API_KEY` prefix convention. Legacy name is honored as a deprecated alias for one release; a one-shot startup `WARN` (from `app.config._warn_deprecated_env_aliases`) fires when only the old name is set. The Python property `settings.extraction_max_context` is unchanged.

## Embeddings

- `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `USE_OPENAI_EMBEDDINGS` — embedding config
- `EMBEDDING_API_BASE`, `EMBEDDING_API_KEY` — optional separate endpoint/key for embeddings (defaults to `OPENAI_API_BASE`/`OPENAI_API_KEY`)
- `EMBEDDING_MAX_INPUT_TOKENS` (default: **5400**) — per-input token cap before sending to the embeddings endpoint. Oversized inputs are token-accurately sub-split client-side (tiktoken cl100k with char fallback, zero content loss — `_enforce_embed_token_cap`/`_split_to_token_budget`) to avoid HTTP 400 *"Input text exceeds the maximum token limit"* errors; chunks the provider still rejects are re-embedded individually with automatic halving (`_recover_missing_embeddings`). Default sits under the nominal 8192 because gateways (Venice) validate with their OWN tokenizer, counting punctuation-dense text ~1.2-1.4× higher than cl100k (measured 2026-07-08). Lift explicitly for direct OpenAI or self-hosted vLLM (e.g. 32768 for Qwen3-Embedding-8B). Applied in `app/services/document_processor.py`.

## Reranking

The cross-encoder reranker (`QueryProcessor.rerank_results`) is the precision pass after hybrid RRF. The local model drags torch + sentence-transformers (~780 MB beyond the ~650 MB haystack/torch floor) into the process, so its lifecycle is tuned for per-instance footprint (key for tenant density — see [`domain/rag-pipeline.md`](domain/rag-pipeline.md)).

- `ENABLE_RERANKING` (default: true) — enable cross-encoder reranking.
- `RERANKING_MODEL` (default: `cross-encoder/ms-marco-MiniLM-L-6-v2`) — local cross-encoder.
- `RERANKER_PRELOAD` (default: **false**) — eager-load the cross-encoder at startup. Off keeps idle instances lean and defers the ~7 s cold start to first use (which `prewarm_reranker()`, fired from `enforce_query_quota`, overlaps with the pre-rerank LLM/search work). Set true for latency-sensitive single-tenant deploys that want zero cold start. No effect when reranking is disabled or offloaded to a service.
- `RERANKER_IDLE_TTL_SECONDS` (default: **0** = never unload; changed 2026-07-03) — when > 0, unload the local cross-encoder after this much idle time to reclaim ~1 GB (reloads ~7 s on the next query). Default is never-unload because idle eviction re-adds the load time to the first question after every quiet period — the query users judge responsiveness by. Set a TTL only on memory-pressed multi-tenant hosts without the shared helper. A reaper task (`main.py:_reranker_idle_reaper`) enforces it. Ignored in remote mode.

## Shared Model Services (cortex-helper)

Offload heavy models to a service hosted once per physical machine (see the `cortex-helper` repo). Empty = use the built-in local path (in-process reranker / subprocess docling). Both clients fall back to local automatically if the service is unreachable. Does NOT remove the ~650 MB torch/haystack floor — the win is eliminating the reranker load spike and per-document docling model reloads, and centralizing GPU.

- `RERANKER_SERVICE_URL` (default: empty) — e.g. `http://cortex-helper:3030`. When set, `rerank_results` POSTs `{query, passages}` to `/rerank` and no local cross-encoder is loaded.
- `DOCLING_SERVICE_URL` (default: empty) — e.g. `http://cortex-helper:3030`. When set, `_convert_document_subprocess` POSTs the file to `/convert` (warm converter, ~0.04 s vs ~4.5 s cold subprocess) instead of spawning a local docling subprocess.
- `PROMPT_GUARD_SERVICE_URL` (default: empty) — e.g. `http://cortex-helper:3030`. When set, the query-time prompt-guard gate (`prompt_guard_client.guard_user_question`) POSTs `{texts}` to `/classify` before retrieval on all three ask paths and refuses flagged questions. Empty ⇒ no remote guard (see `PROMPT_GUARD_LOCAL` below for the in-process fallback; both empty ⇒ guard disabled). Fail-open + own circuit breaker. Reuses `HELPER_SERVICE_TOKEN`. Each guarded ask = +1 `query` unit + a `prompt_guard.classify` Langfuse trace.
- `PROMPT_GUARD_LOCAL` (default: false) — load the classifier **in-process** when no `PROMPT_GUARD_SERVICE_URL` is set (mirrors the local reranker fallback via `prompt_guard_local.py`). Off by default: it adds resident RAM and runs `trust_remote_code` model code locally (against the shared-service footprint priority), and needs torch+transformers (absent in the slim `INSTALL_LOCAL_ML=false` image). For local dev / self-hosters without a cortex-helper. Ignored when the service URL is set (remote wins). Model id/pin: `PROMPT_GUARD_MODEL` (default `leolee99/PIGuard`) + `PROMPT_GUARD_REVISION` (pinned commit).
- `PROMPT_GUARD_THRESHOLD` (default: `0.5`) — injection-class probability at/above which a question is refused; lower = stricter.
- `HELPER_SERVICE_TOKEN` (default: empty) — shared secret sent as `X-Helper-Token`; must match the helper's `HELPER_TOKEN`. Covers reranker, docling, **and** prompt-guard calls.
- `HELPER_STRICT_REMOTE` (default: false) — when true (and `DOCLING_SERVICE_URL` set), a conversion that still fails after the helper client's retries marks the document failed instead of falling back to the local docling subprocess (protects tenant memory on packed hosts). All helper HTTP goes through `services/helper_client.py`: shared connection, 3 retries with backoff+jitter on transient failures, circuit breaker (5 failures → open 30s).
- `INSTANCE_ID` (default: empty ⇒ container hostname) — identifies this stack to the shared helper (`X-Tenant-ID`) for per-tenant fair queuing.
- `DOCLING_CONVERSION_TIMEOUT` (default: `600`) — hard ceiling in seconds on a single **local** docling subprocess conversion. On timeout the worker is killed and the document is marked `failed` with a clear message, instead of hanging in `processing` forever on a large/corrupt file. Does not apply to the remote `DOCLING_SERVICE_URL` path (the helper client has its own timeouts).

**Slim image**: `Dockerfile.prod` build args `INSTALL_LOCAL_ML=false` (+ optional `PREDOWNLOAD_MODELS=false`) build a torch-free backend (~800MB–1GB smaller; `requirements-base.txt` only). Slim requires OpenAI embeddings + the helper URLs; the local-model paths fail fast with actionable errors.

## MDHarvest powered by Crawl4ai (web → markdown)

Web→markdown harvesting (the "Web Import" feature; supersedes the deprecated standalone `mdharvest` tool). cortex-app never embeds a browser — it calls a [crawl4ai](https://github.com/unclecode/crawl4ai) service over HTTP via `services/crawl_client.py`. Self-host points at the user's own crawl4ai; cloud points at the shared per-host crawl4ai (hosted in `cortex-helper`). See [`domain/web-crawl.md`](domain/web-crawl.md) and `cortex-helper/README.md`. There is **no** local crawl fallback — empty URL ⇒ feature off (no in-process browser stack, by design).

- `ENABLE_WEB_CRAWL` (default: false) — master switch for the Web Import endpoints + UI. The UI is shown only when this is true **and** `CRAWL_SERVICE_URL` is set (the `/api/features` flag AND-s both).
- `CRAWL_SERVICE_URL` (default: empty) — base URL of the crawl4ai service, e.g. `http://crawl4ai:11235` (self-host) or `http://<host>:11235` (the shared per-server instance). Empty = disabled.
- `CRAWL_SERVICE_TOKEN` (default: empty) — bearer token sent as `Authorization: Bearer <token>`; must match crawl4ai's `CRAWL4AI_API_TOKEN` (`security.api_token`). **Required for crawl4ai ≥ 0.9.0**: without a token crawl4ai serves its API only on `127.0.0.1`, so any cross-container/shared deployment is unreachable without it. A startup WARN fires when `ENABLE_WEB_CRAWL` + `CRAWL_SERVICE_URL` are set but this is empty (see `main.py` lifespan). Empty only works for an older tokenless crawl4ai or a same-host loopback URL.
- `CRAWL_HTTP_TIMEOUT` (default: 60) — per-request timeout (s) for crawl4ai calls (browser rendering of a slow page can take tens of seconds).
- `CRAWL_CONTENT_FILTER` (default: `fit`) — crawl4ai `/md` filter: `fit` (readability — clean main content), `raw` (full DOM→markdown), or `bm25` (query-ranked; needs a query). Per-request override via the API.
- `CRAWL_CONCURRENCY` (default: 5) — max URLs crawled concurrently within one Web Import job (the shared crawl4ai enforces its own browser-pool limits).
- `CRAWL_MAX_URLS_PER_JOB` (default: 100) — hard cap on URLs per job; **the per-tenant plan lever** (the AaaS operator lowers it via env). 0 = unlimited.
- `CRAWL_DISCOVER_MAX_LINKS` (default: 200) — cap on candidate links returned by `/api/web-import/discover`.

All crawl HTTP goes through `services/crawl_client.py`: shared connection, 3 retries with backoff+jitter, its own circuit breaker (op `crawl` in `/metrics`), and cache-bypass (`c="0"`) per request. Only the synchronous `/md` + `/crawl` endpoints are used (never the addressable async `/crawl/job` API) so nothing is retained or cross-tenant-visible.

## Efficiency Flags (v-next)

- `ENTITY_DEDUP_PREFILTER` (default: **true** since 2026-07-03) — Levenshtein entity dedup scores only the top-50 fulltext-index candidates instead of scanning every Entity node (O(50) vs O(all) per stored entity). Set false to restore the full scan (recall can differ on extreme typo variants the fulltext analyzer misses).
- `ENABLE_BATCHED_KG_WRITES` (default: **true** since 2026-07-03) — entities/chunk-links/relationships are written via UNWIND batches (a handful of Neo4j round trips per document instead of one per item) through a resolve → cluster → batch-write pipeline that preserves the per-item dedup semantics (`test_batched_writes.py` locks the parity contract).
- `ENABLE_BATCHED_CHUNK_RELATIONSHIPS` (default: **true** since 2026-07-03 — live-validated, see `bench/STEP1_RESEARCH.md`) + `RELATIONSHIP_CHUNKS_PER_CALL` (default: 4; 6 also A/B-passed) — pack several chunks into one per-chunk relationship-extraction LLM call (grouped `<chunk index>` XML; same system prompt as the single-chunk path). ÷4 Step 1 relationship calls at parity yield — the key lever under provider request-rate limits. Degrades per batch: grouped parse → flat parse → per-chunk re-dispatch.
- `ENABLE_PHASEB_CHECKPOINTING` (default: false) — persist Phase B batch progress (`PhaseBCheckpoint` nodes): crash/redeploy resumes from completed batches; rounds 2+ reuse round 1's Phase 1 candidates.
- `ENABLE_REPROCESS_DELTA` (default: false) — skip reprocessing when the file bytes + extraction config are unchanged since the last successful run (fingerprint on the Document node). Git re-syncs of unchanged files cost ~zero.
- `ENABLE_PROMPT_CACHE_CONTROL` (default: false) — send Anthropic `cache_control` breakpoints on the system prompt when routed via OpenRouter to `anthropic/*` models (cache-read pricing on the stable prefix). No-op elsewhere.
- `RESEARCHER_STABLE_PROMPT` (default: true) — keep the researcher system prompt byte-stable across loop iterations (counter rides as a trailing system note) so provider prefix caches hit from iteration 2 on. `false` restores the legacy per-iteration rebuild.

## Observability & Limits

- `LOG_FORMAT` (default: `plain`) — `plain` keeps the legacy log format byte-identical; `json` emits one JSON object/line with `request_id` (read from / echoed as `X-Request-ID`, forwarded to cortex-helper).
- `METRICS_ENABLED` (default: true) — Prometheus metrics at `GET /metrics` (admin-key protected, not routed through the prod nginx). Requires `prometheus-client` (in requirements; older images degrade to 501).
- `EXPOSE_API_DOCS` (default: `auto`) — interactive API docs (`/docs`, `/redoc`, `/openapi.json`). `auto` enables them in development and **disables them in production** (resolved via `config.docs_enabled`, keyed off `ENVIRONMENT`) so a directly-reachable backend doesn't disclose its full API schema to anonymous callers — the prod nginx routes root paths to the frontend, but the per-tenant container model exposes the backend directly. Set `EXPOSE_API_DOCS=true`/`false` to force either way. Wired into `FastAPI(docs_url/redoc_url/openapi_url)` in `main.py`.
- `RATE_LIMIT_QPM` (default: 0 = off) + `RATE_LIMIT_BURST` (default: 10) — per-API-key token-bucket guardrail on ask/upload endpoints (429 + `Retry-After`). Billing remains `MAX_QUERIES_PER_MONTH`.
- `RESEARCHER_WALL_CLOCK_SECONDS` (default: 0 = unlimited) — wall-clock budget for the researcher loop; on expiry the writer synthesizes from what was gathered.
- `RERANK_TOP_K` (default: 15) — candidates kept/reranked per knowledge_search; lower on remote rerankers to trade recall for latency.
- `ASK_DEADLINE_SECONDS` (default: 28) — app-level wall-clock deadline for the **non-streaming** `POST /api/ask`. On expiry returns a clean `504` JSON `{detail}` instead of letting the edge proxy (Traefik) cut the silent socket and emit a bare plain-text 500. Keep it just **below** the proxy read timeout (~30s); raise both in lockstep. Does not apply to `/api/ask/stream` (SSE heartbeats keep that alive). `0` = no deadline. (Agentic on non-streaming `/api/ask` is rejected with a `400` pointing to `/api/ask/stream`.)
- `NEO4J_MAX_POOL_SIZE` (default: 100), `NEO4J_CONNECTION_TIMEOUT` (default: 10), `NEO4J_CONNECTION_ACQUISITION_TIMEOUT` (default: 60) — driver pool tuning.
- Compose-level: `CORTEX_NEO4J_MEM_LIMIT` (4g), `CORTEX_NEO4J_HEAP_INITIAL/MAX`, `CORTEX_NEO4J_PAGECACHE`, `FRONTEND_MEM_LIMIT` (1g), `stop_grace_period` — every service is memory-capped so one tenant's blowup can't OOM another stack's container. The neo4j caps are deliberately **not** `NEO4J_`-prefixed: Coolify/Dokploy inject every env var into every container, and neo4j's entrypoint parses any `NEO4J_*` var as a config setting (`NEO4J_HEAP_INITIAL` → `HEAP.INITIAL`), which `strict_validation` rejects → neo4j won't boot. Opt-in backups: `docker-compose.backup.yml` overlay (`BACKUP_INTERVAL_SECONDS`, `BACKUP_RETENTION_DAYS`, `BACKUP_INITIAL_DELAY_SECONDS`, `NEO4J_ENTERPRISE_BACKUP`; restore runbook in `ops/backup/restore.sh`). The export is written **server-side** — neo4j needs `NEO4J_apoc_export_file_enabled=true` + the backups volume at its import dir (wired in all deploy composes).

## Feature Flags

- `ENABLE_GRAPH_EXTRACTION`, `ENABLE_COMMUNITY_DETECTION`, `ENABLE_AGENTIC_RAG` — feature flags
- `ENABLE_SEMANTIC_ENTITY_RESOLUTION` (default: true) — use embedding-based vector similarity for entity dedup during storage (catches semantic matches like "Museum of Crypto Art" / "MOCA" that Levenshtein misses; falls back to Levenshtein)
- `DEDUP_SCAN_WAIT_SECONDS` (default: 25) — how long `GET /api/entities/duplicates` waits inline before answering `202 {status: running, progress}` for the client to poll (the scan continues server-side, single-flight). Keep just **below** the edge proxy read timeout (~30s Traefik default), same rule as `ASK_DEADLINE_SECONDS`.
- `DEDUP_SCAN_CACHE_TTL_SECONDS` (default: 600) — server-side cache lifetime for completed duplicate-scan results per (threshold, limit, collection-scope). Entity merges invalidate immediately; `refresh=true` forces a rescan.
- `ENABLE_AGENT_RESEARCH` (default: true), `ENABLE_AGENT_CHAT` (default: true) — agent-based research pipeline flags
- `ENABLE_BATCHED_QUERY_EXTRACTION` (default: true) — in `_execute_knowledge_search`, collapse a `knowledge_search`'s up-to-3 queries into ONE batched entity-extraction LLM call + ONE batched embedding call (instead of one each per query). Off → legacy per-query path (still extraction-tier). Query-side entity extraction always runs on the extraction tier (`GRAPH_EXTRACTION_MODEL` + minimized reasoning), not the primary model.
- `PROMPT_SECURITY` (default: true) — master flag for prompt-injection defenses (input detection, output filtering, untrusted-content delimiting).
- `ENABLE_INGESTION_INJECTION_SCAN` (default: **false**) — **EXPERIMENTAL** master flag for the ingestion-time prompt-injection scan. Off (the default) = the feature is completely absent: no scan runs (not even the free heuristic), the admin Features & Security toggle is hidden, and `PATCH /api/admin/config` rejects the runtime toggle with 400. See `.claude/domain/document-pipeline.md`.
- `INGESTION_INJECTION_SCAN` (default: true) — **default** for the scan's LLM classifier once the experimental feature is enabled; admin-overridable at runtime (persisted as `SystemMeta` key `setting:ingestion_injection_scan`, read via `neo4j.get_runtime_setting`). Off → free heuristic only (no per-doc query). Inert while `ENABLE_INGESTION_INJECTION_SCAN` is false.
- `PROMPT_GUARD` (default: true) — **default** for the query-time prompt-guard gate; admin-overridable at runtime (persisted as `SystemMeta` key `setting:prompt_guard`). Only takes effect when `PROMPT_GUARD_SERVICE_URL` is also set. Off → skip the guard (saves the extra query). See the "Shared model services" block above and `handbook/05-security.md`.
- `DISPLAY_FULL_SYSTEM_CONFIG` (default: **false**) — display-only flag surfaced in `SystemConfigResponse`. When false the admin **System Config** panel hides advanced tuning knobs (output-token budgets, concurrency counts, chunking params, hybrid-search weights, graph hops, community sizes, similarity thresholds) and shows a curated view; models, API bases, context windows, dimensions, and feature toggles stay visible. The frontend gates rows via a `DisplayFullConfigContext` + `advanced` prop on `ConfigItem` (`frontend/src/app/admin/page.tsx`). Cloudflare-style model names / gateway API bases are also cleaned for display (`formatModelName`/`formatApiBase` in `lib/utils.ts`).

## Agent Configuration

- `RESEARCHER_MAX_ITERATIONS_SPEED` (default: 3), `RESEARCHER_MAX_ITERATIONS_QUALITY` (default: 8) — agent loop iteration caps
- `WRITER_MAX_TOKENS_SPEED` (default: 1200), `WRITER_MAX_TOKENS_QUALITY` (default: 4000) — writer output token limits
- `MAX_CONVERSATION_HISTORY` (default: 6) — legacy message-count cap; used only when no `conversation_memory` blob is sent
- `RESEARCHER_SPEED_EARLY_WRITE` (default: true) — speed mode breaks straight to the writer after a search iteration that produced sources (and no skill/git tool ran), skipping the model's `done` confirmation round-trip (whose summary the speed writer never reads). One full LLM call saved per plain chat turn.
- `RESEARCHER_PARALLEL_TOOL_CALLS` (default: true) — read-only tool calls (`knowledge_search`/`community_search`/`entity_lookup`) emitted in one assistant message execute concurrently via `asyncio.gather`; side-effecting tools (`http_request`, `git_repo`, skill tools) stay sequential. Big quality-mode win (the prompt encourages several searches per turn).
- `RESEARCHER_TOOL_ENTITY_HINTS` (default: true) — the `knowledge_search` tool accepts an optional `entities` array; when the researcher supplies it, the query-side entity-extraction LLM call is skipped entirely (one LLM round-trip saved per search).
- `RESEARCHER_SEARCH_DEDUP` (default: true) — identical repeat `knowledge_search` calls within one run return the cached tool text instantly with a "try a different angle" nudge instead of re-running retrieval.
- `RESEARCHER_FORCE_GROUNDING` (default: true) — grounding guard: when the researcher loop ends with zero searches performed and zero sources (the model answered from parametric memory — observed stochastically on gemma), the pipeline runs one `knowledge_search` with the raw question before the writer. Exempt: memory fast-path, skill-answered questions (skill responses land in `sources`).
- `EMIT_DONE_BEFORE_MEMORY` (default: true) — the SSE `done` frame (with `pending_memory: true`) is emitted **before** the post-answer memory-compaction LLM call; `memory_update` follows before stream end. UI finalizes 1–4s earlier. Clients must consume the stream to its end, not stop at `done`; set false to restore the legacy order (memory_update → done).

### Conversation Memory (Context Curator)

Multi-bucket, client-carried conversation memory — see [`domain/rag-pipeline.md`](domain/rag-pipeline.md#conversation-memory--context-curator). Active only when the client sends a `conversation_memory` blob; absent ⇒ legacy `MAX_CONVERSATION_HISTORY` truncation.

- `ENABLE_CONVERSATION_MEMORY` (default: true) — backend kill-switch (client opt-in via the blob still required)
- `CONVERSATION_MEMORY_WINDOW` (default: 6) — recent messages kept verbatim; older ones fold into the rolling summary
- `CONVERSATION_MEMORY_COMPACTION_MODEL` (default: empty ⇒ fast-mode model) — model for post-stream compaction
- `CONVERSATION_MEMORY_MAX_LEDGER` (default: 50) — max `source_ledger` entries retained in the blob (most recent kept); each source carries a stable `sid` for citation continuity
- `ENABLE_MEMORY_FAST_PATH` (default: true) — let memory-answerable follow-ups ("summarize that", "why?", "in German") skip the researcher loop and retrieval entirely (a cheap classifier decides; KG grounding is rehydrated from the blob's stored `kg_context`)

## Skills Configuration

See [`.claude/domain/skills.md`](domain/skills.md) for the full Agent Skills system.

- `ENABLE_SKILLS` (default: true) — master switch for Agent Skills integration
- `SKILLS_DIR` (default: `.agents/skills`) — directory for skill discovery (relative to project root or absolute). Persisted via Docker volume (`skills_data`) in production compose
- `ENABLE_SKILL_SCRIPTS` (default: false) — allow skills to execute local scripts (security-sensitive, opt-in)
- `SKILL_SCRIPT_TIMEOUT` (default: 30) — timeout in seconds for skill script execution
- `SKILL_HTTP_TIMEOUT` (default: 15) — timeout in seconds for skill HTTP tool calls
- `SKILL_HTTP_INSECURE_HOSTS` (default: empty) — comma-separated hostnames for which the skill `http_request` tool skips TLS verification (opt-in, for self-hosted skill APIs with self-signed certs, e.g. `zammad.internal,helpdesk.local`). Empty = verify all hosts (secure default). Scoped per-host, never global. (Hosts listed here are also SSRF-allowlisted for the `http_request` tool — see below.)
- `SKILL_HTTP_ALLOW_PRIVATE` (default: `false`) — SSRF policy for the agent `http_request` tool. The tool's URL is LLM-chosen and steerable via prompt injection in ingested content, so by default the target is resolved and requests to private/loopback/link-local/metadata (`169.254.169.254`) addresses are blocked (`services/ssrf_guard.py`, validated on every redirect hop via an httpx event hook). Set `true` only if skills legitimately call APIs on private IPs.
- `SKILL_HTTP_ALLOWED_HOSTS` (default: empty) — comma-separated hostnames exempt from the `http_request` SSRF check (explicit opt-in for a specific self-hosted skill API on an internal host). Union'd with `SKILL_HTTP_INSECURE_HOSTS`.
- `WEB_IMPORT_ALLOW_PRIVATE` (default: `true`) — SSRF policy for Web Import URLs. Loopback/link-local/metadata are always blocked; private (RFC1918/ULA) ranges are allowed by default so intranet crawls keep working. Set `false` to also block private targets. Git provider `base_url` uses the same guard (private allowed — self-hosted git on an internal IP is legitimate — with `GIT_HTTP_INSECURE_HOSTS` as its allowlist).
- `MAX_SKILL_TOOLS` (default: 10) — max total skill-provided tools injected into researcher agent
- `MAX_SKILL_INSTRUCTIONS_TOKENS` (default: 4000) — approximate token budget for skill instruction injection

## Git Integration

See [`.claude/domain/git-integration.md`](domain/git-integration.md) for the full connector. Requires `git` in the backend image and `pathspec` (both included).

- `ENABLE_GIT_INTEGRATION` (default: false) — master switch for the git repo connector (ingestion endpoints, scheduled poller, agent `git_repo` tool)
- `GIT_WORK_DIR` (default: `./git_repos`) — directory holding per-connection clone working copies (a cache; Neo4j provenance is the source of truth). Must be writable; mount a volume in production
- `GIT_CLONE_DEPTH` (default: 1) — shallow-clone depth. Raise if older history is needed for cheap diffs (sync self-heals via full-tree reconcile otherwise)
- `GIT_MAX_REPO_SIZE_MB` (default: 500) — abort a sync if the cloned repo exceeds this. 0 = unlimited
- `GIT_SYNC_MAX_FILE_SIZE_MB` (default: 5) — skip individual files larger than this (binaries/assets). 0 = no per-file limit
- `GIT_SYNC_POLL_INTERVAL` (default: 5) — minutes between scheduler ticks checking connections due for a scheduled sync (per-connection interval is `sync_interval_minutes`, 0 = manual only)
- `GIT_HTTP_TIMEOUT` (default: 30) — timeout in seconds for git provider REST calls
- `GIT_HTTP_INSECURE_HOSTS` (default: empty) — comma-separated hostnames for which git REST calls AND clone TLS verification are skipped (opt-in, for self-hosted GitLab/Gitea with self-signed certs). Empty = verify all hosts (secure default)

## Audit Log

- `ENABLE_AUDIT_LOG` (default: false), `AUDIT_LOG_PATH` (default: ./logs/audit.log) — append-only JSONL audit trail (`services/audit_log.py`): auth failures (`auth.key_rejected`, `auth.unauthorized`), key-attributed mutating requests + search/ask (`api.request` with method/path/status/actor). Metadata only — never document content or query text (consistent with Langfuse masking). Fail-open, 50MB rotation to `.1`, hooked in `APIUsageMiddleware`.

## Auth

- `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_API_KEY`, `SESSION_SECRET` — admin auth. Login validation happens in the Next.js frontend (`lib/auth.ts` consumes `ADMIN_EMAIL`/`ADMIN_PASSWORD`/`SESSION_SECRET`); the backend consumes `ADMIN_API_KEY` (and checks `ADMIN_PASSWORD`/`SESSION_SECRET` only in the production-hardening validator). In `ENVIRONMENT=production`, startup fails fast if `SESSION_SECRET` is < 32 chars while `ADMIN_PASSWORD` is set (see [Deployment & CORS](#deployment--cors)).
- `API_KEY_CACHE_TTL_SECONDS` (default: 30, 0 = disabled) — in-process TTL cache for **successful** generated-key validations (`auth_service`). Collapses the per-request double validation (usage middleware + route dependency) and page-load bursts to one Neo4j read; invalidated on any key CRUD/system-reset in-process, so revocation is immediate on single-worker deployments and bounded by the TTL with `UVICORN_WORKERS>1`. Invalid keys are never cached. Note: auth-store outages now answer **503 + Retry-After** (transient, retriable), never 401 — 401 is reserved for missing/rejected keys.

## Secret Encryption

- `ENCRYPTION_KEY` (default: empty = disabled) — comma-separated Fernet keys for at-rest encryption of user-supplied secrets: git connector PATs (Neo4j `GitConnection.pat`) and secret-typed skill config fields (`config.json`). First key encrypts, all keys decrypt (MultiFernet). Ciphertext is `enc:`-prefixed; plaintext values pass through reads, so enabling the key later is safe — an idempotent startup migration encrypts existing plaintext (and re-encrypts rotated-key values with the primary key). Unset → loud startup warning + plaintext storage. Malformed key → startup fails fast. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Rotation: prepend new key (`ENCRYPTION_KEY=<new>,<old>`), restart, then drop the old key.

## x402 Payments

- `X402_ENABLED` (default: false) — master switch for x402 pay-per-query monetization. Deliberately the **only** x402 env var: when true, the "x402 Payments" section appears on the admin Settings page where the owner configures recipient wallet, facilitator, network and asset at **runtime** (stored on the Neo4j `X402Config` node — survives redeploys, excluded from library export and System Reset). Priced API keys and the 402 payment gate only activate once that config passes the admin verify suite. See [`domain/x402.md`](domain/x402.md).

## Observability (Langfuse)

Optional LLM tracing/cost. All empty = disabled; the same image runs identically traced or untraced. See [`.claude/domain/observability.md`](domain/observability.md) for the instrumentation map.

- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (default: empty) — a Langfuse **project** key pair (Project Settings → API Keys). Both required to activate.
- `LANGFUSE_BASE_URL` (default: empty) — Langfuse instance URL, e.g. `https://langfuse.example.com`. The SDK reads this name natively. All three of key/secret/base_url must be set for `Settings.langfuse_tracing_active` to be True; otherwise the OpenAI client factory (`llm_config.make_*_openai_client`) returns the plain, untraced client.
- `LANGFUSE_TRACING_ENABLED` (default: `true`) — master off-switch; set `false` to disable tracing even when keys are present.
- `LANGFUSE_SAMPLE_RATE` (default: `1.0`) — 0.0–1.0 trace sampling; lower on high-traffic instances. Passed to the SDK at init (`observability.init_langfuse`).
- `LANGFUSE_LOG_EXTENDED` (default: `false`) — content logging mode. When `false` (default) a client-side `mask` hook redacts **all** user/model authored text (prompts, completions, tool-call arg values, tool descriptions, embedding inputs, vision text, extraction text) before export → only structure reaches the server (roles, model + params, tool names + arg/param keys, allow-listed metadata, tokens, cost, latency, tags). Set `true` to log full content for local debugging. See [`.claude/domain/observability.md`](domain/observability.md#content-masking) for the redaction policy.

## Error Tracking (GlitchTip)

Optional crash/error reporting via the Sentry protocol (`services/error_tracking.py` backend, `@sentry/nextjs` frontend). Empty DSN = disabled; same image runs tracked or untracked. Backend and frontend report to **separate GlitchTip projects**. See [`.claude/domain/observability.md`](domain/observability.md#error-tracking-glitchtip).

Backend (also inherited by the docling worker subprocess):

- `SENTRY_DSN` (default: empty) — backend project DSN. When set, `init_sentry()` runs before app construction: unhandled endpoint exceptions and any `logger.error/exception` (API, background pipeline, worker) become events, tagged `service` + `request_id` (correlates with `X-Request-ID` in logs/responses). Python events carry source-context lines read from the container's files — no upload step needed.
- `SENTRY_ENVIRONMENT` (default: empty) — issue segmentation; empty falls back to `ENVIRONMENT`.
- `SENTRY_RELEASE` (default: empty) — optional deploy tag (e.g. git SHA); auto-detected from git in local dev.
- `SENTRY_TRACES_SAMPLE_RATE` (default: `0`) — `0` = errors only (tracing fully off); `>0` samples performance transactions (GlitchTip supports them).
- `SENTRY_MAX_REQUEST_BODY_SIZE` (default: `never`) — request bodies carry authored content (ask questions, document text); privacy deny-by-default mirrors `LANGFUSE_LOG_EXTENDED`. Raise to `small`/`medium` only to debug.
- `SENTRY_SEND_DEFAULT_PII` (default: `false`) — IPs/cookies/user context off unless opted in.

Frontend (compose files map `SENTRY_DSN_FRONTEND` onto these so the container never inherits the backend DSN via `env_file`):

- `NEXT_PUBLIC_SENTRY_DSN` (build-time, inlined into the client bundle) + `SENTRY_DSN` (runtime, Node server side) — frontend project DSN.
- `NEXT_PUBLIC_SENTRY_ENVIRONMENT` / `SENTRY_ENVIRONMENT` — segmentation, as above.
- `SENTRY_URL`, `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` (build-time only) — enable the source-map upload during `next build` (debug-ID artifact bundles; GlitchTip ≥ 4.2) so production stack traces show original TypeScript. Token needs `project:releases` scope; it stays in the Docker builder stage. Without them the build is unchanged and stack traces stay minified.

**Shipped defaults:** the committed compose files (dev/prod/Dokploy/Coolify) default `SENTRY_DSN_BACKEND`, `SENTRY_DSN_FRONTEND`, `SENTRY_URL`, `SENTRY_ORG`, and `SENTRY_PROJECT` to this project's own GlitchTip instance via the no-colon `${VAR-default}` form, so tracking is on out of the box — **unset → default, empty string → disabled** (a stack/tenant opts out by setting the var to `""`). Only `SENTRY_AUTH_TOKEN` is not defaulted (it's a secret): set it where the frontend image is built to enable source-map upload.

## Document Processing

- `CHUNK_SIZE`, `CHUNK_OVERLAP`, `CHUNK_BY` (word/sentence) — document processing
- `AUTO_RESUME_PENDING_ON_STARTUP` (default: true) — when the previous shutdown killed a pipeline run, auto-resume the interrupted step (quota-guarded). Triggers on documents stranded mid-processing (orphan-reset), a queued batch that never started its first doc, or an interrupted Step 2/3 task; each pipeline task persists a `resume_context` (chain, concurrency, scope/rebuild) on its TaskRecord so a Generate Graph run resumes INTO its remaining Steps 2/3 instead of stopping after Step 1 (fixed 2026-07-11). `start_processing=false` uploads stay parked — they have no pipeline task record.
- `AUTO_RESUME_IMAGE_ANALYSIS` (default: true, since 2026-07-10) — image analysis runs as fire-and-forget futures AFTER a document completes, so a restart kills them while the doc stays `completed` with `image_progress_current < total` (the orphan-reset never matches it — Step 1 reads stuck forever, zero LLM traffic). On startup, such docs are detected and their images re-extracted via Docling re-conversion (CPU only, no LLM cost); images whose chunk (`{doc_id}_image_{idx}`) already exists are skipped, so paid vision/extraction work is never redone and text chunks/entities are untouched. Docs run sequentially under an `image_analysis_resume` task (quota-guarded, 10s after boot). Irrecoverable docs (source file missing, no vision model, re-conversion finds no images) get their counters force-closed with an explanatory message; conversion errors leave counters stuck for retry on next startup.
- `MAX_FILE_SIZE_MB` (default: 50) — per-file upload cap; enforced with a chunked read on both `/api/upload` and the reprocess path (413 mid-stream, never fully buffered first)
- `MAX_REQUEST_BODY_MB` (default: 32) — global request-body ceiling enforced by `app/body_limit.py:BodySizeLimitMiddleware` (Content-Length precheck + streamed-byte cap → 413). Upload routes get `MAX_FILE_SIZE_MB` + 8MB multipart slack instead; import routes use `MAX_IMPORT_BODY_MB`. `0` disables the middleware.
- `MAX_IMPORT_BODY_MB` (default: 2048) — body ceiling for `/api/admin/import*` (ZIPs stream to disk, so this is a disk guard, not a RAM guard). `0` = unlimited.
- `MIN_FREE_DISK_MB` (default: 500) — free-space floor for the uploads filesystem: uploads, reprocess-with-file, and import sessions are rejected with **507** when accepting the payload would leave less free (`main._ensure_disk_space`; disk-full corrupts Neo4j checkpoints). `0` disables. Observability: `cortex_disk_free_bytes`/`cortex_disk_total_bytes` gauges + `cortex_uploads_rejected_disk_total` counter in `/metrics`; `disk_free_mb`/`disk_total_mb` on `GET /api/stats`.

## Instance Limits

Sentinel `0` means "unlimited" on every cap below.

- `MAX_FILES` (default: 0) — total documents (uploads + custom inputs). Enforced at upload time and at library import.
- `MAX_ENTITIES` (default: 0) — total entities across the graph. Enforced at upload-time and custom-input creation: new ingestion is rejected once `get_stats()["entity_count"]` is at or above the cap. A single in-flight document can push the post-extraction count slightly above the cap (accepted tradeoff).
- `MAX_COLLECTIONS` (default: 0) — total collections (default counts as 1). Enforced at `POST /api/collections`.
- `MAX_QUERIES_PER_MONTH` (default: 0) — instance-wide monthly quota denominated in **internal LLM completions** ("units"), aligning quota consumption with inference cost. Every successful chat-completion call counts one unit: each Q&A-loop iteration + writer (~6/question), entity extraction, per-chunk relationships, Phase B pair verification, community summarization, vision analysis (~5.4/file measured). Embeddings never count. Counted at the OpenAI client-factory choke point (`llm_config._count_chat_completions`, plus one manual increment in the raw-httpx vision path), buffered in `services/usage_meter.py`, and flushed to per-day `LLMUsageDay` nodes (sum for the UTC month = the meter). Enforcement: `enforce_query_quota` on `POST /api/search` + the three `/api/ask*` endpoints, and `enforce_processing_quota` on the processing entry points (upload, custom-input, reprocess single+bulk, process-pending, web-import, git sync incl. the scheduler, relationships/analyze, communities detect+summarize). In-flight answers/documents always finish; batch processing checks the meter between documents and leaves the rest `pending`. Returns `429 Too Many Requests` + `Retry-After` (seconds until next UTC month) when exceeded. Usage is surfaced on `GET /api/stats` (`monthly_usage_used/limit/query/processing`) and rendered as a meter bar in the **admin Settings → Statistics panel** (warn ≥80%, red at 100%; deliberately NOT in the global StatsBar — quota mechanics stay off end-user pages).
