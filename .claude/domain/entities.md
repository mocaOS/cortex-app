# Entities

Entity extraction, resolution, deduplication, merging, editing, and search.

> **Reasoning control:** all entity-extraction-tier LLM calls (extraction, summaries, community names, entity enrichment) run under `EXTRACTION_REASONING_MODE` (default `off`). Helper at `backend/app/services/reasoning_config.py` suppresses thinking on reasoning models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1) via provider-correct kwargs. No-op for pure instruct models. See [`.claude/environment.md`](../environment.md).

## Fuzzy Resolution

Entity extraction runs in two surfaces — per-document text extraction (Phase A) and per-image extraction inside `process_single_image` — and both apply the same dedup strategy:

- When `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true` (default), entities are batch-embedded via `generate_entity_embeddings_batch_async()` and stored via `store_entity_with_embedding()`, which queries the `entity_embedding` vector index first and falls back to Levenshtein for typo variants. Catches semantic duplicates like "Museum of Crypto Art" / "MOCA" that string similarity misses.
- When the flag is off (or the embedding batch fails), entities fall through to `store_entity_with_resolution()` — Levenshtein 85% only.
- Image entities reach this path through `store_graph_extraction()` (`neo4j_service.py`), which accepts an optional `entity_embeddings` list aligned with `extraction.entities`. The image pipeline computes embeddings per image before calling it; text-entity batching happens one level up at the document scope.
- Provenance fields (`document_id`, `source_documents`, `extraction_count`, `last_extracted_at`) are tracked identically by both store paths.

## Type Normalization

10 allowed entity types, normalized via `_normalize_entity_type()` with rapidfuzz fallback to Concept.

## Deduplication Algorithm

`suggest_duplicate_entities()` fetches all entities and compares in Python using rapidfuzz:

### Similarity Scorers
- `ratio` — for typos
- `token_sort_ratio` — for word reordering
- `partial_ratio` with type-aware gating — restricted to same-type entities with length ratio >= 0.5, relaxed to 0.35 for Person type

### Person-Aware Partial Ratio Gating
For Person entities, `partial_ratio` is only allowed when the shorter name is a strict word-level prefix of the longer name (e.g., "Colborn" ↔ "Colborn Bell" allowed, "David Young" ↔ "David Hockney" blocked).

### Performance
- Same-word-count pairs suppressed via vectorized numpy masking; remaining sparse pairs checked individually
- CPU limited to half of available cores (`sched_getaffinity` for Docker cgroup awareness)
- 5-minute server-side timeout on the endpoint

### Clustering
Uses star clustering (not BFS) to prevent transitive chain explosions. **Single-word Person names excluded as star centers** to prevent hub groups (e.g., "Andrea" pulling all "Andrea X" into one group). Person-type entities sorted with priority.

## Merge Operation

`merge_entities()`:
- Retargets all relationships and chunk MENTIONS to canonical
- Deduplicates relationships (same source+target+type keeps highest weight)
- Adds aliases, merges source_documents
- Accepts LLM-generated `merged_description`
- Clears community_id (topology changed)
- Deletes merged nodes

`MergeHistory` nodes store merge audit trail (entity snapshots, stats). `SystemMeta` tracks `last_entity_merge_at` (also exposed in `GraphStatsResponse`).

### Endpoints
- `GET /api/entities/duplicates` (5-min timeout, 504 on expiry)
- `POST /api/entities/merge`
- `GET /api/entities/merge-history`

### Frontend (Deduplicate Page)
`/deduplicate` under Manage section with scan/merge/dismiss flow:
- **Entity-level access** via `?entity=` query param (auto-scans and filters to groups containing that entity, or creates standalone group for manual addition)
- **Inspect modal** (eye icon on each entity in a group shows full details: description, relationships, related entities, chunks)
- Entity search (inline) to add entities to groups
- Merge history modal with search
- Community re-detection notice after merges
- EntitiesBrowser has Deduplicate button (merge icon) on each entity card and in the detail modal footer
- Dismissed groups stored in localStorage

## Entity Editing

Entity detail modal (Explore > Entities tab) supports inline editing of name and description via `PATCH /api/graph/entity/{name}`. Name edits add the old name to the entity's `aliases` array for continued searchability; the endpoint validates against duplicate names. Graph edges (relationships, chunk MENTIONS) remain intact because Neo4j edges connect to nodes, not name strings. The fulltext index auto-updates.

## Entity Search

`find_entities_by_name()` uses fulltext index with wildcard prefix matching (e.g. "pol" finds "Polygon") via Lucene `*` suffix, sorted by connection count (highest first).

## Entity Traversal Constraint

`traverse_from_entities()` has an `entity_paths_only` flag (default `False`). When `True`, adds `WHERE ALL(n IN nodes(path) WHERE n:Entity)` to the Cypher traversal, preventing paths through Chunk/Document nodes. The entity details endpoint (`/api/graph/entity/{name}`) uses `entity_paths_only=True` so the panel only shows entities reachable via Entity→Entity relationships (navigable on the graph). RAG callers leave it `False` for broader context retrieval. `get_entity_relationships()` also constrains to Entity-only paths.

## v-Next Efficiency (flag-gated, default off)

- **Fulltext dedup prefilter** (`ENTITY_DEDUP_PREFILTER`): `find_similar_entities` scores only the top-50 `entity_name_fulltext` candidates with Levenshtein instead of scanning every Entity node — O(50) vs O(all entities) per stored entity. Falls back to the full scan if the fulltext query errors.
- **Batched entity storage** (`ENABLE_BATCHED_KG_WRITES`): `DocumentProcessor._store_entities_batched` replaces the per-entity loop with resolve → cluster → batch-write: (1) one UNWIND vector-index round trip + one batched Levenshtein round trip against the existing graph (same embedding-first/Levenshtein-backup order as `store_entity_with_embedding`); (2) within-batch union-find clustering in Python (cosine ≥ threshold or Levenshtein ≥ 0.85; first occurrence is canonical, matching the sequential "first stored wins"); (3) `store_entities_batch` / `apply_entity_merges_batch` / `link_entities_to_chunks_batch` UNWIND writes with the exact SET semantics of the per-item methods. Parity locked by `backend/tests/test_batched_writes.py` + `test_entity_resolution.py`.
- **Vector-index health**: schema init verifies `chunk_embedding`/`entity_embedding` are ONLINE (ERROR log otherwise); embedding-search failures count into `get_stats()["vector_search_failures"]` and warn once — silent Levenshtein degradation is now visible.
