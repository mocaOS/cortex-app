# Chapter 8: The Knowledge Graph

The Knowledge Graph is the Library's most powerful feature. It transforms your documents from isolated files into a connected web of knowledge — enabling multi-hop reasoning, relationship discovery, and more accurate answers than simple vector search alone.

## Using Reasoning Models for Ingestion

You can point `GRAPH_EXTRACTION_MODEL` and `RELATIONSHIP_EXTRACTION_MODEL` at modern reasoning models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1, GLM-4.6, Kimi K2, MiniMax M2) without their reasoning hurting extraction quality. Cortex ships with `EXTRACTION_REASONING_MODE=off` and `RELATIONSHIP_REASONING_MODE=off` by default, which forces thinking OFF on these models via provider-correct request shapes (OpenAI, OpenRouter, Venice, Anthropic, vLLM/Compute3 all handled). Pure instruct models like Mistral Small 24B see no change. To revert to provider defaults, set the modes to `auto`. See [Chapter 4 — Reasoning Control (ingestion pipelines)](04-configuration.md#reasoning-control-ingestion-pipelines) for the full env-var set, override hatch, and caveats.

## The Three-Step Pipeline

Building the knowledge graph is a three-step process, managed from the **Knowledge Graph** page (Manage > Knowledge Graph at `/extract`).

### Step 1: Entity Extraction (Phase A — Per-Document)

Extracts entities from each document's chunks using an LLM. The UI shows entity and within-document (per-chunk) relationship counts.

**What happens:**

1. Each document's chunks are grouped into batches sized to fit within `EXTRACTION_MAX_CONTEXT`
2. Consecutive batches share 1 chunk of overlap for context continuity
3. The LLM receives a system prompt requesting entities in strict XML format:
   ```xml
   <entity name="OpenAI">
     <type>Organization</type>
     <description>An AI research company founded in 2015...</description>
   </entity>
   ```
4. The response is parsed, entity types are normalized to the 10 allowed types (rapidfuzz matching, 75% threshold, fallback to "Concept")
5. Entity resolution merges similar names using embedding-based vector similarity (when `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true`) to catch semantic matches like "Museum of Crypto Art" / "MOCA", with Levenshtein 85% as fallback. "OpenAI" and "Open AI" become a single entity with aliases.
6. Entities are linked to the chunks that mention them via fuzzy substring matching (`partial_ratio >= 85%`)
7. **Per-chunk relationship extraction**: Chunks with 2+ linked entities get an LLM call to extract relationships using the chunk text as direct evidence. Entity names in per-chunk relationships are automatically mapped to their canonical (dedup-resolved) names before storage, ensuring relationships reference the correct merged entities. Self-referential relationships (where source and target are the same entity) are automatically filtered out. These relationships are stored with `extraction_method='per_chunk'` and provide high-confidence, evidence-grounded connections before Phase B runs.
8. Entity provenance is tracked — each entity records which documents it was extracted from

**Token budget calculation:**

```
available_tokens = (EXTRACTION_MAX_CONTEXT × 0.8) − system_prompt_tokens − template_tokens − 1500 (output reserve)
```

**When to run:** After uploading new documents. The Knowledge Graph page automatically detects pending documents.

### Step 2: Relationship Analysis (Phase B — Cross-Document)

Discovers relationships between entities across your entire collection. The UI shows only cross-document relationship counts (excludes per-chunk). The "Find more" button runs an additional round of incremental analysis. The ERR indicator is displayed to 2 decimal places.

**What happens:**

1. All entities in the collection are fetched
2. Entities are **interleaved by type** (round-robin merge) to ensure cross-type relationship discovery
3. Entities are grouped into batches of up to 120, with 5% degree-aware overlap between batches (entities in 2+ batches excluded from overlap, low-connection entities preferred)
4. For each batch, the system:
   - Fetches **co-mention chunks** with greedy entity-coverage-diversity selection (maximizes coverage of different entities rather than always picking hub-dominated chunks)
   - Fetches existing relationships involving batch entities (capped at 20 per entity, highest weight first) to avoid rediscovery without reinforcing hub patterns
   - Sends entities + source context + existing relationships to the LLM. Phase 1 (candidate scan) includes few-shot good/bad examples to guide the LLM and anti-hub negative instructions ("If no clear relationship exists, do not create one") with bad examples showing co-occurrence pairs to avoid.
5. The LLM returns relationships in XML format with confidence scores:
   ```xml
   <relationship>
     <source>Vitalik Buterin</source>
     <target>Ethereum</target>
     <type>CREATED_BY</type>
     <description>Vitalik Buterin co-founded Ethereum in 2015.</description>
     <weight>9.5</weight>
     <confidence>0.95</confidence>
   </relationship>
   ```
6. Relationships with confidence < 0.5 are filtered before storage
7. Self-referential relationships (where source and target are the same entity) are automatically filtered out at both the extraction and storage levels
8. Non-standard relationship types are fuzzy-matched to the 14 standard types (80% threshold)
9. Results are deduplicated across batches using the key `(source.lower(), target.lower(), type)`

**Two modes:**
- **Incremental** (default) — Builds on existing relationships, only analyzing gaps
- **Rebuild** (`rebuild=true`) — Deletes only batch-analysis relationships (preserving per-chunk relationships from Step 1) and re-analyzes from scratch

**Parallel execution:**

When `PARALLEL_RELATIONSHIP_BATCHES > 1`, batches are processed concurrently using an asyncio Semaphore. Results are collected in batch order for deterministic deduplication.

**Progress tracking:**

The frontend shows real-time progress: "Batch X/Y — N relationships found". ETA is calculated from the observed average batch duration.

**API:**

```bash
# Incremental analysis
curl -X POST http://localhost:8000/api/graph/relationships/analyze \
  -H "X-API-Key: your-api-key"

# Full rebuild
curl -X POST "http://localhost:8000/api/graph/relationships/analyze?rebuild=true" \
  -H "X-API-Key: your-api-key"

# Collection-scoped
curl -X POST "http://localhost:8000/api/graph/relationships/analyze?collection_id=my-collection" \
  -H "X-API-Key: your-api-key"
```

### Step 3: Community Detection

Groups related entities into thematic communities using graph algorithms.

**What happens:**

1. Old communities are cleaned up (deleted and entity `community_id` fields cleared)
2. The entity graph is projected with:
   - **Relationship edges** — Bidirectional (UNION), weighted by the `weight` property
   - **Co-mention edges** — Entities sharing a chunk get an implicit edge with weight 2.0
3. The Leiden algorithm detects communities (preferred for guaranteed connected communities)
   - Falls back to Louvain if Leiden unavailable
   - Falls back to BFS connected components if neither is available
4. Communities below `MIN_COMMUNITY_SIZE` (default: 3) are discarded
5. Distribution monitoring logs warnings for pathological distributions (mega-communities, all-minimum-size)
6. Each community is stored with member entities linked via `HAS_MEMBER` relationships

**Community summarization:**

After detection, the **extraction model** generates names and summaries (configurable via `COMMUNITY_SUMMARY_MODEL`, defaults to `GRAPH_EXTRACTION_MODEL`):

1. For each community, up to 30 entities and 40 relationships are collected
2. The LLM is asked to return `{"name": "...", "summary": "..."}`
3. An **assistant prefill technique** (`{"`) forces JSON output from the model
4. A 5-strategy parsing fallback handles various LLM output quirks:
   - Direct JSON parse
   - Strip to first `{` (handles chain-of-thought preamble)
   - Extract from markdown code fences
   - Regex JSON object extraction
   - Regex individual field extraction
5. If all parsing fails, the name defaults to top entity names and the summary uses raw text

**API:**

```bash
# Detect communities
curl -X POST "http://localhost:8000/api/graph/communities/detect?min_size=3" \
  -H "X-API-Key: your-api-key"

# Generate summaries
curl -X POST http://localhost:8000/api/graph/communities/summarize \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"force_regenerate": false}'
```

## Generate Graph / Regenerate Graph

The Knowledge Graph page provides two convenience buttons:

**Generate Graph** (shown when no entities exist):
- Runs all 3 steps from scratch as a single flow

**Regenerate Graph** (shown when entities exist):
- Performs a complete from-scratch rebuild:
  1. Delete all communities
  2. Delete all relationships
  3. Delete all entities
  4. Reprocess all documents (entity extraction)
  5. Relationship analysis (rebuild mode)
  6. Community detection
- Requires confirmation dialog
- Flow state persisted to sessionStorage with task IDs for resume on page reload

## Staleness Detection

The Knowledge Graph page automatically detects when steps are out of date:

| Condition | Which Step Shows "Needs Update" |
|-----------|---------------------------------|
| New documents uploaded since last extraction | Step 1 |
| New entities since last relationship analysis | Step 2 |
| Relationships analyzed after last community detection | Step 3 |
| Entities merged since last community detection | Step 3 (with specific message) |

Steps cascade — if Step 1 needs an update, Steps 2 and 3 are blocked (greyed out) until it completes.

Staleness is tracked via `SystemMeta` Neo4j nodes storing `last_relationship_analysis_at`, `last_community_detection_at`, and `last_entity_merge_at` timestamps.

## Graph Visualization

Navigate to **Explore > Knowledge Graph** for an interactive graph.

**API for visualization data:**

```bash
# Get graph visualization data (default 100 nodes)
curl "http://localhost:8000/api/graph/visualization?limit=100&include_neighbors=true" \
  -H "X-API-Key: your-api-key"
```

Returns `{nodes, edges, stats}` where nodes are sorted by mention_count and edges are all relationships involving those nodes.

## Exploring the Graph via API

```bash
# Entity details + relationships (entity-only paths)
curl "http://localhost:8000/api/graph/entity/OpenAI?max_hops=2" \
  -H "X-API-Key: your-api-key"

# Entity relationships for graph expansion
curl "http://localhost:8000/api/graph/entity/OpenAI/relationships?max_depth=2&limit=50" \
  -H "X-API-Key: your-api-key"

# Search entities by name (fulltext with wildcard prefix matching)
curl "http://localhost:8000/api/graph/search?query=open" \
  -H "X-API-Key: your-api-key"

# Get subgraph for specific entities + bridge connections
curl -X POST http://localhost:8000/api/graph/subgraph \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"entity_names": ["OpenAI", "GPT-4"], "include_connections": true}'

# Paginated entity listing with filters
curl "http://localhost:8000/api/graph/entities?skip=0&limit=50&entity_type=Organization&search=ai" \
  -H "X-API-Key: your-api-key"

# Update entity name and/or description
curl -X PATCH "http://localhost:8000/api/graph/entity/OpenAI" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "OpenAI, Inc.", "description": "AI research and deployment company"}'
# Old name ("OpenAI") is added to aliases; graph edges remain intact

# Distinct entity types (for filter dropdowns)
curl http://localhost:8000/api/graph/entity-types \
  -H "X-API-Key: your-api-key"

# Paginated relationship listing with filters
curl "http://localhost:8000/api/graph/relationships?skip=0&limit=50&rel_type=USES" \
  -H "X-API-Key: your-api-key"

# Distinct relationship types
curl http://localhost:8000/api/graph/relationship-types \
  -H "X-API-Key: your-api-key"
```

### Entity Traversal Constraint

The entity details endpoint (`/api/graph/entity/{name}`) uses `entity_paths_only=True` in its graph traversal. This means it only shows entities reachable via Entity→Entity relationships — not through Chunk or Document nodes. This ensures the entity panel and graph visualization only display navigable entity connections.

RAG queries use `entity_paths_only=False` for broader context retrieval.

## Graph Cleanup

```bash
# Clean up orphaned entities (no chunk mentions) and communities (no members)
curl -X POST http://localhost:8000/api/cleanup/orphaned-entities \
  -H "X-API-Key: your-api-key"

# Delete all entities (DETACH DELETE — removes all relationships too)
curl -X DELETE http://localhost:8000/api/graph/entities \
  -H "X-API-Key: your-api-key"

# Delete all relationships only
curl -X DELETE http://localhost:8000/api/graph/relationships \
  -H "X-API-Key: your-api-key"

# Delete all communities only (preserves entities and relationships)
curl -X DELETE http://localhost:8000/api/graph/communities \
  -H "X-API-Key: your-api-key"

# Delete a specific community
curl -X DELETE http://localhost:8000/api/graph/communities/{community_id} \
  -H "X-API-Key: your-api-key"
```

## Graph Statistics

```bash
curl http://localhost:8000/api/stats \
  -H "X-API-Key: your-api-key"
```

Returns comprehensive stats including:
- Document, chunk, entity, relationship, per-chunk relationship, community counts
- Processing status breakdown (pending, completed, failed, processing)
- Average chunks per document, entity type distribution, average entity mentions
- Staleness timestamps for relationship analysis, community detection, and entity merges
