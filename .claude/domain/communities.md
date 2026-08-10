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

## Consolidation Scheduler (unattended refresh)

`_maybe_run_consolidation` (main.py, called from the hourly maintenance loop) keeps the community layer fresh on long-running instances without an operator. Env-gated `ENABLE_CONSOLIDATION_SCHEDULER` (default **off**). Fires only when idle (no in-flight queries — `_active_query_count`; no pipelines; no backlog; SystemMeta `last_query_at` older than `CONSOLIDATION_IDLE_MINUTES`), past the `CONSOLIDATION_COOLDOWN_HOURS` cooldown, and there is work: the standard staleness rules above, or completed-doc growth ≥ `CONSOLIDATION_MIN_NEW_DOCUMENTS` vs the SystemMeta `consolidation_doc_baseline` (first check records the baseline). It launches the normal community-detection task (summarization included) — visible in /api/tasks, emits the task.completed webhook, abortable. Deliberately does NOT auto-merge entities (dedup stays a reviewed operation). Tests: `tests/test_consolidation.py`.
