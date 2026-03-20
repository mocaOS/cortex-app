# Chapter 13: Entity Deduplication

Despite automatic fuzzy resolution during entity extraction (85% Levenshtein threshold), some duplicates slip through — entities with different name formats, abbreviations, or multilingual variants. The Deduplication feature provides a post-extraction review-and-merge workflow.

## Why Deduplication Matters

Duplicate entities fragment your knowledge graph. If "Machine Learning" and "ML" exist as separate entities, their relationships are split, community detection is less effective, and search results may miss relevant connections.

After deduplication: "Machine Learning" (with alias "ML") has all relationships, mentions, and connections unified.

## The Deduplication Workflow

### Step 1: Scan for Duplicates

Navigate to **Manage > Deduplicate** or use the API:

```bash
curl "http://localhost:8000/api/entities/duplicates?threshold=0.75&limit=100" \
  -H "X-API-Key: your-api-key"
```

### How Scanning Works

The system fetches all entities and compares them in Python using three rapidfuzz strategies:

| Strategy | What It Catches | Gate |
|----------|----------------|------|
| **`ratio`** | Typos, minor spelling differences ("OpenAl" vs "OpenAI") | Always applied |
| **`token_sort_ratio`** | Word reordering ("John Smith" vs "Smith, John") | Always applied |
| **`partial_ratio`** | Abbreviations, substrings ("ML" vs "Machine Learning") | Same entity type only, with length ratio gating |

**Partial ratio gating** (prevents false positives):
- Restricted to same-type entities
- General entities: `length_ratio >= 0.5` (shorter name must be at least half the length of the longer)
- Person entities: relaxed to `length_ratio >= 0.35` (allows "Vitalik" matching "Vitalik Buterin")

### Clustering

Results are grouped using **star clustering** (not BFS transitive closure):
- Each group has one central "canonical" entity (suggested based on highest connectivity)
- Prevents transitive chain explosions where A≈B and B≈C would incorrectly merge A and C
- Person-type entities are sorted with priority

### Step 2: Review Groups

Each duplicate group in the UI shows:
- **Suggested canonical entity** — The entity with the most connections (relationships + mentions)
- **Duplicate candidates** — Entities the system thinks might be the same
- **Similarity scores** — How similar each candidate is to the canonical
- **Connection counts** — Mentions and relationships for each entity

**Actions per group:**
- **Merge** — Combine duplicates into the canonical entity
- **Dismiss** — Mark as "not duplicates" (persisted in localStorage)
- **Add entities** — Use inline search to manually add entities the scan missed

### Step 3: Merge

```bash
curl -X POST http://localhost:8000/api/entities/merge \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "canonical": "OpenAI",
    "merge": ["Open AI", "Open-AI"]
  }'
```

### What Happens During Merge

1. **Retarget inbound relationships** — All relationships pointing to duplicate entities are redirected to the canonical entity. When duplicate relationships exist (same source + target + type), only the one with the highest weight is kept.

2. **Retarget outbound relationships** — Same process for relationships originating from duplicate entities.

3. **Transfer chunk MENTIONS** — All chunk references are redirected to the canonical entity.

4. **Transfer metadata** — Aliases from duplicates are added to the canonical entity. Source documents lists are merged.

5. **Accept merged description** — The LLM-generated combined description (if provided) replaces the canonical entity's description.

6. **Clear community membership** — The canonical entity's `community_id` is cleared because the graph topology has changed. Communities should be re-detected.

7. **Delete duplicate nodes** — Merged entity nodes are removed via DETACH DELETE.

8. **Record audit trail** — A `MergeHistory` node stores pre-merge entity snapshots, relationship/chunk counts, and the timestamp.

9. **Update staleness** — `SystemMeta.last_entity_merge_at` is updated, triggering staleness detection on the Knowledge Graph page.

## Merge History

View the complete audit trail:

```bash
curl "http://localhost:8000/api/entities/merge-history?limit=50" \
  -H "X-API-Key: your-api-key"
```

Returns a list of merge events, each containing:
- Canonical entity name
- Merged entity names
- Pre-merge entity snapshots (name, type, description for each entity)
- Relationship and chunk retargeting counts
- Timestamp

In the web interface, the **Merge History** modal shows a searchable list with detail views for each merge.

## Post-Merge Actions

After merging entities:

1. The Knowledge Graph page detects staleness: "Entities have been deduplicated since communities were last detected"
2. Re-run **Community Detection** (Step 3) to update community membership
3. Relationship analysis does not need re-running — relationships were preserved during merge

## Configuration

```env
ENABLE_SEMANTIC_ENTITY_RESOLUTION=true   # Automatic during extraction (85% threshold)
ENTITY_SIMILARITY_THRESHOLD=0.85          # Extraction-time dedup threshold
```

The post-extraction dedup scan threshold is controlled per-request via the `threshold` query parameter (default: 0.75).

## Best Practices

1. **Run deduplication after large ingestion batches** — New documents may introduce variant names for existing entities
2. **Start with a moderate threshold** (0.75) — Lower values find more potential duplicates but include more false positives
3. **Review before merging** — The scan is suggestive, not definitive. Always review groups before merging.
4. **Use inline entity search** — If you know two entities should be merged but they weren't auto-detected, use the search feature to manually add them to a group
5. **Re-detect communities after merging** — Graph topology changes invalidate existing community assignments
6. **Check merge history** — If something looks wrong after merging, the audit trail shows exactly what happened
