# Relationships

All relationship extraction and analysis logic. See [`.claude/environment.md`](../environment.md#relationship-llm) for env var reference.

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

## Batch Analysis

Per-collection (Phase B) with two-phase per-batch processing:

### Phase 1 — Candidate Scan

Relationship model scans all entities in the batch + chunk context to identify candidate entity pairs that may be related. Output: simple `EntityA | EntityB` pairs. Uses `EXTRACTION_MAX_CONTEXT` token budget (larger context window for scanning). Includes few-shot good/bad examples to guide the LLM and anti-hub negative instructions ("If no clear relationship exists, do not create one") with bad examples showing co-occurrence pairs to avoid.

### Phase 2 — Relationship Extraction

Relationship model takes only the candidate pairs + their descriptions + chunk context and outputs structured XML relationships with type, description, weight, and confidence (0.0-1.0). Relationships with confidence < 0.5 are filtered before storage. Uses `RELATIONSHIP_MAX_CONTEXT` token budget. Batches with 0 candidates skip Phase 2 entirely.

## Batching Algorithm

- 120 entities/batch hard cap, 5% overlap (degree-aware: excludes entities already in 2+ batches, prefers low-connection entities)
- Parallel execution (`PARALLEL_RELATIONSHIP_BATCHES`, default 5)
- Token budget split 60/40 between entities and chunk context (dynamic filling via `get_chunk_context_for_entities()` with greedy entity-coverage-diversity selection)
- Co-occurrence-based entity ordering via Union-Find clustering groups entities sharing chunks into the same batch with high/low connection count interleaving to prevent hub concentration
- Scales to 100k+ entities in O(n * avg_chunks)

## Multi-Round Discovery

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

`GraphStatsResponse` also exposes `per_chunk_relationship_count` (via `count(CASE WHEN r.extraction_method = 'per_chunk' THEN 1 END)`) separately from `relationship_count`, enabling the frontend to distinguish within-document vs cross-document relationships. Step 2 displays only cross-document relationships (total minus `per_chunk_relationship_count`).
