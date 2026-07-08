# Relationships

All relationship extraction and analysis logic. See [`.claude/environment.md`](../environment.md#relationship-llm) for env var reference.

> **Output format (2026-07-08):** entity + per-chunk relationship extraction prompts emit compact `ENT|Name|Type|Description` / `REL|Source|Target|TYPE|weight|confidence|Description` lines instead of XML — the XML scaffolding cost 32%/44% of output tokens, and extraction latency is decode-bound (~70 tok/s on Venice qwen3), so this directly cuts wall time and timeout-splits. Parsers: `_parse_entities_output` / `_parse_relationships_output` in `graph_extractor.py` (compact-first, XML fallback — old models/prompt drift still parse). The chunk-batched path keeps its `<chunk index>` grouping wrapper with REL| lines inside. Phase B prompts remain XML. A prompt-version tag in `_reprocess_config_hash` forces re-extraction of existing docs. Format adherence live-verified on qwen3-6-27b. Tests: `backend/tests/test_compact_extraction_format.py`.

> **Reasoning control:** all relationship-tier LLM calls (Phase 1 candidate scan, gleaning pass, per-chunk extraction, Phase 2 batch analysis) run under `RELATIONSHIP_REASONING_MODE` (default `off`). The helper at `backend/app/services/reasoning_config.py` suppresses thinking on reasoning models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1) via provider-correct kwargs (`reasoning_effort` for OpenAI, `extra_body.reasoning` for OpenRouter, `extra_body.chat_template_kwargs.enable_thinking=false` for vLLM, `venice_parameters` for Venice, `thinking={type:disabled}` for Anthropic — Opus 4.7+ omits the param). No-op for pure instruct models. Runtime fallback strips the param and caches the model on `BadRequestError`. Override knob: `REASONING_MODEL_OVERRIDES=model:mode`.

## Three-Tier LLM Configuration

Graph extraction uses `get_extraction_llm_config()` from `llm_config.py` (separate from Q&A model). Relationship extraction uses `get_relationship_llm_config()` (separate from extraction model, with fallback chain: relationship model -> extraction model -> main model). This three-tier LLM separation allows running entity extraction and relationship discovery on independent API rate limits.

Both phases of batch analysis use the relationship model (defaults to extraction model) because it's instruction-following and produces clean structured output. The main model tends to over-reason and output plaintext instead of XML. The dedicated relationship model config allows running relationship discovery on a separate API rate limit from entity extraction.

## Per-Chunk Extraction

During Phase A (document processing), after entity extraction and chunk linking, chunks with 2+ entities get an LLM call (using the relationship model via `get_relationship_llm_config()`) to extract relationships using the chunk text as direct evidence.

- Tracks original-to-canonical entity name mapping during entity storage and remaps relationship source/target to canonical names before storing, preventing silent storage failures when entity names were merged during fuzzy resolution
- Stored with `extraction_method='per_chunk'`
- Concurrency controlled by `CONCURRENT_RELATIONS` (default 3), separate from entity extraction concurrency
- Uses tenacity retry with exponential backoff (4 attempts, 2-30s wait) for rate limit errors
- This provides high-confidence, evidence-grounded relationships before Phase B runs
- Both single-chunk and batched user prompts include an explicit one-line `<relationship>` format example. Added 2026-07-03: without it, models frequently omitted `<description>` entirely — 99.5% of per-chunk relationships in a prod-scale graph had empty descriptions. With the example, description coverage is 100% in live A/Bs.

### Streaming Storage & Progress

Results are consumed via `asyncio.as_completed`, not `asyncio.gather`. Each chunk's relationships land in Neo4j (and the live `relationship_count` stat ticks up) the moment its LLM call returns, instead of bulk-committing after the entire batch finishes. The storage call itself is offloaded via `loop.run_in_executor(...)` so it doesn't block the event loop while other concurrent LLM tasks are still in flight. The progress message updates ~10× across the phase (`"Extracting per-chunk relationships: 44/442 chunks (118 found)..."`) so the UI no longer sits silently at 90% for minutes.

## Phase B (Step 2) — Discovery Modes

`RELATIONSHIP_DISCOVERY_MODE` selects the Step 2 engine (dispatch at the top of `document_processor.analyze_collection_relationships`):

### Targeted mode (default, `targeted`)

`_analyze_relationships_targeted` (document_processor) — candidate pairs are generated **without the LLM**, then the LLM only verifies/classifies ranked pairs. On a 28k-entity graph this replaces ~750 near-context-window batch scans (tens of hours) with a few hundred small verification calls (minutes).

1. **Embedding backfill**: entities without `e.embedding` — or with a wrong-dimension one (stale vectors survive an embedding-model switch on entities merged into from the old graph) — are embedded via `generate_entity_embeddings_batch_async` and bulk-written (`set_entity_embeddings_bulk`) so the `entity_embedding` vector index covers the whole set. Skipped when no embed API key (falls back to co-mention only).
2. **Candidate generation** (Neo4j, no LLM):
   - kNN: `get_knn_candidate_pairs` — `db.index.vector.queryNodes` per entity (`RELATIONSHIP_KNN_K`, `RELATIONSHIP_KNN_MIN_SIMILARITY`), already-connected pairs excluded in-query. Guards since 2026-07-04: query filters `size(e.embedding) = $dim` and per-batch failures are skipped with a warning — previously one stale wrong-dimension vector threw and silently disabled the entire kNN phase (observed on the 2026-07-03 rebuild: Step 2 ran co-mention-only).
   - Doc co-mention: `get_doc_cooccurrence_pairs` — unconnected pairs mentioned together in ≥ `RELATIONSHIP_MIN_SHARED_DOCS` documents; entities in > `RELATIONSHIP_DOC_FREQ_CAP` docs skipped as anchors (hub guard).
3. **Rank & cap** (`relationship_candidates.py`, pure/unit-tested): score = 0.6·knn + 0.4·min(1, shared_docs/4); dedup across directions/sources; greedy `RELATIONSHIP_CANDIDATES_PER_ENTITY` and `RELATIONSHIP_MAX_CANDIDATE_PAIRS` caps.
4. **LLM verification**: pairs grouped `RELATIONSHIP_PAIRS_PER_CALL` per call (name-sorted so shared entities cluster), each call = existing `analyze_relationships_async` candidate-pair mode with `RELATIONSHIP_PAIR_CONTEXT_TOKENS` of chunk context. Concurrency via `PARALLEL_RELATIONSHIP_BATCHES`; stored incrementally with the same confidence (≥0.5) + `RELATIONSHIP_MAX_PER_ENTITY` degree caps, `extraction_method='cross_collection'`.

Single pass (no multi-round); `RELATIONSHIP_MAX_HOURS` still enforced between verification batches. Result dict adds `discovery_mode` and `candidate_pairs`. Phase B checkpointing does not apply (runs are short); tests: `test_relationship_candidates.py`, `test_targeted_relationship_discovery.py`.

### Legacy mode (`llm_scan`) — Batch Analysis

Per-collection (Phase B) with two-phase per-batch processing:

### Phase 1 — Candidate Scan

Relationship model scans all entities in the batch + chunk context to identify candidate entity pairs that may be related. Output: simple `EntityA | EntityB` pairs. Uses `GRAPH_EXTRACTION_MAX_CONTEXT` token budget (larger context window for scanning). Includes few-shot good/bad examples to guide the LLM and anti-hub negative instructions ("If no clear relationship exists, do not create one") with bad examples showing co-occurrence pairs to avoid.

### Phase 2 — Relationship Extraction

Relationship model takes only the candidate pairs + their descriptions + chunk context and outputs structured XML relationships with type, description, weight, and confidence (0.0-1.0). Relationships with confidence < 0.5 are filtered before storage. Uses `RELATIONSHIP_MAX_CONTEXT` token budget. Batches with 0 candidates skip Phase 2 entirely.

## Batching Algorithm

- 120 entities/batch hard cap, 5% overlap (degree-aware: excludes entities already in 2+ batches, prefers low-connection entities)
- Parallel execution (`PARALLEL_RELATIONSHIP_BATCHES`, default 5)
- Token budget split 60/40 between entities and chunk context (dynamic filling via `get_chunk_context_for_entities()` with greedy entity-coverage-diversity selection)
- Co-occurrence-based entity ordering via Union-Find clustering groups entities sharing chunks into the same batch with high/low connection count interleaving to prevent hub concentration
- Scales to 100k+ entities in O(n * avg_chunks)

## Multi-Round Discovery (legacy `llm_scan` mode)

- Initial analysis (0 existing relationships) runs up to `RELATIONSHIP_MAX_ROUNDS` (default 3) rounds
- "Find more" (relationships already exist) always runs 1 round
- Each round fetches existing relationships with per-entity cap (top 20 by weight per entity) to prevent hub reinforcement
- Per-entity storage cap (`RELATIONSHIP_MAX_PER_ENTITY`, default 50): skips relationships where both endpoints are saturated
- Stops early if `RELATIONSHIP_TARGET_RATIO` reached or `RELATIONSHIP_MAX_HOURS` exhausted

## ERR Metric (Entity-Relationship Ratio)

`entity_relationship_ratio` tracked in `GraphStatsResponse` and returned in analysis task results. Shown on Knowledge Graph page (Step 2) with color-coded indicator (green >= 0.69, yellow >= 0.29, red < 0.29), displayed to 2 decimal places, and tooltip explaining the metric. `RELATIONSHIP_TARGET_RATIO` (default 1.0) configurable.

## Relationship Type Constraint

Prompt enforces 14 standard types (MENTIONS removed as it was a lazy co-occurrence catch-all). `_extract_xml_relationships()` fuzzy-matches non-standard types to `DEFAULT_RELATION_TYPES` via rapidfuzz (80% threshold, fallback to RELATED_TO). Includes plaintext fallback parser for `EntityA --[TYPE]--> EntityB` arrow format when XML parsing finds no results.

## Self-Referential Filtering

`store_relationship()` skips relationships where source == target. Also filtered in `extract_chunk_relationships_async` and `analyze_relationships_async` before storage attempts.

## Rebuild vs Incremental Mode

Relationship analysis supports `rebuild=true` mode (calls `delete_batch_relationships()` to delete only batch-analysis relationships where `extraction_method != 'per_chunk'`, preserving Step 1 per-chunk relationships, then triggers multi-round) alongside default incremental mode.

## Stats

`GraphStatsResponse` also exposes `per_chunk_relationship_count` (via `count(CASE WHEN r.extraction_method = 'per_chunk' THEN 1 END)`) separately from `relationship_count`, enabling the frontend to distinguish Step 1 relations vs cross-document relations. UI wording: Step 1 counts are labeled "relations", Step 2 counts are labeled "cross-document relations". Step 2 displays only cross-document relations (total minus `per_chunk_relationship_count`).

## v-Next Efficiency (flag-gated; chunk-batched extraction default ON since 2026-07-03, rest default off)

- **Chunk-batched per-chunk extraction** (`ENABLE_BATCHED_CHUNK_RELATIONSHIPS` + `RELATIONSHIP_CHUNKS_PER_CALL`, default 4): `extract_chunk_relationships_batch_async` packs several eligible chunks (2+ entities) into ONE LLM call — indexed `<source>` blocks in, `<chunk index="i">` blocks out, parsed by `_extract_xml_grouped_relationships` which delegates per-block to the existing `_extract_xml_relationships` (normalization/clamping/arrow-fallback stay single-sourced). Degradation per batch: grouped parse → flat parse (relationships attributed by per-source entity-set membership) → per-chunk re-dispatch through the untouched single-chunk path. Since 2026-07-04, an explicitly-present empty `<chunk index="i"></chunk>` block is trusted as a deliberate "no relationships" answer (measured 2026-07-03 rebuild: re-asking legitimately-empty batches wasted 1,172 single-chunk calls, ~23% of the relationship pass); only chunks whose block is *absent* from the response (truncated/partial output) are re-dispatched individually, and the full per-chunk fallback applies only to flat-parse zero-yield (`_extract_xml_grouped_relationships_with_coverage` reports which indices had explicit blocks). System prompt is byte-identical to the single-chunk path (one cacheable prefix). **Live-validated 2026-07-03 on qwen3-6-27b** (24-chunk A/B, `bench/STEP1_RESEARCH.md`): ÷4 calls at parity yield (0 lost chunks, 0.96–1.13×), same wall-clock at fixed concurrency — the win is request-count economy under provider rate limits. ×6/call also passed. Both per-chunk prompts now pin the exact `<relationship>` element format inline (without it, qwen emitted `<relation>`/no descriptions — every batched parse failed to the fallback), and `_extract_xml_relationships` accepts `<relation>` as a `<type>` alias.
- **Phase B checkpointing** (`ENABLE_PHASEB_CHECKPOINTING`): `PhaseBCheckpoint` nodes keyed by `(run_signature, batch_key)` — `run_signature` hashes the sorted entity set + models + target ratio; `batch_key` hashes the batch's sorted entity names (batching is deterministic). Crash/redeploy resume skips completed batches; rounds 2+ reuse round 1's persisted Phase 1 candidates (Phase 2 already avoids existing relationships). Hooks live in `PhaseBCheckpointHooks` (graph_extractor) and are wired by `analyze_collection_relationships`; checkpoints are cleared on completion, rebuild, or signature change.
- **Batched relationship writes**: with `ENABLE_BATCHED_KG_WRITES` (default on), per-chunk relationships are stored one UNWIND call per completed chunk (`store_relationships_batch`, APOC with per-item fallback) instead of one round trip per relationship. Both `store_relationship` and `store_relationships_batch` persist `r.confidence` since 2026-07-04 — previously only the library-import path did, so a prod-scale graph had `confidence` NULL on every natively-written relationship.
