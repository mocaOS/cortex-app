# Communities

Community detection, summarization, and staleness tracking. This is Step 3 of the Knowledge Graph pipeline (see [`.claude/domain/knowledge-graph-ui.md`](knowledge-graph-ui.md)).

## Detection Algorithm

Tries algorithms in order:
1. **Leiden** (GDS) — preferred
2. **Louvain** — fallback if Leiden unavailable
3. **BFS** — last resort

Configuration:
- Uses relationship weights (`relationshipWeightProperty`)
- Undirected projection (UNION both directions)
- Co-mention edges: entities sharing a chunk get implicit weight-2.0 edge (helps detect communities in sparse graphs)
- Old communities cleaned up before re-detection

## Summarization

Uses the extraction model (not the primary model) for reliable structured output.

- Assistant prefill `{"` forces JSON output
- Double-brace dedup
- 5-strategy parsing fallback: direct parse, strip-to-first-brace, code fence, regex object, regex fields
- Fallback names from top entity names when LLM output can't be parsed

## Staleness Tracking

`SystemMeta` Neo4j nodes store timestamps for staleness detection:
- `last_relationship_analysis_at` — when Step 2 last ran
- `last_community_detection_at` — when Step 3 last ran
- `last_entity_merge_at` — when entities were last merged

Communities become stale when:
- Relationships have been re-analyzed since last detection (`last_relationship_analysis_at` > `last_community_detection_at`)
- Entities have been merged since last detection (`last_entity_merge_at` > `last_community_detection_at`)

See [`.claude/domain/knowledge-graph-ui.md`](knowledge-graph-ui.md) for how staleness drives the frontend cascade.
