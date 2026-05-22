# Frontend Patterns

Cross-cutting frontend behaviors shared across multiple pages/components.

## Explore Browsers (Entities, Relationships, Communities)

Server-side pagination with search and filtering. Backend endpoints (`/api/graph/entities`, `/api/graph/relationships`, `/api/graph/communities`) accept `skip`, `limit`, and `search` query params; entities and relationships also accept type filters (`entity_type`, `rel_type`). Dedicated `/api/graph/entity-types` and `/api/graph/relationship-types` endpoints return distinct types for filter dropdowns. Frontend uses 300ms debounced search, fetches only the current page (50 items for entities/relationships, 25 for communities), and shows subtle opacity transition during fetches. Each item is clickable for a detail modal. Communities browser cleans up JSON artifacts in summaries for display.

## Dynamic Graph Expansion

KnowledgeGraph visualization (default 100 nodes, force-graph 2D) supports clicking unloaded related entities in the EntityPanel to grow the graph.

**Expansion flow**: `getEntityRelationships(target, 1, 50)` fetches the entity + 1-hop neighbors + edges; `getGraphSubgraph([selected, target], true)` fetches the bridge subgraph (all shared neighbors + edges between both entities' neighborhoods) in parallel. New nodes spawn near the selected entity; a `pendingNavigateRef` + `useEffect` on `graphData.nodes` handles navigation after React re-render; `d3ReheatSimulation()` wakes the force layout; a polling interval waits for x/y before calling `centerAt`/`zoom`.

**State management**: Expanded nodes/edges stored in component state (`expandedNodes`/`expandedEdges`) and merged into `graphData` via `useMemo`, reset when props change.

**Pointer events**: Geometric pointer events (`pointerdown`/`pointerup`) filter by `e.target.tagName === "CANVAS"` to avoid stealing clicks from the EntityPanel overlay.

## Chat/Research Message Rendering

Research process blocks (Sub-Questions, Thinking Steps, Reasoning Steps) render above the main content bubble. Order: research process → content → graph context → sources. Research Process container auto-scrolls to bottom as new steps stream in.

## Source Modal Highlighting

Cited chunk is highlighted within the full document text. Uses `indexOf()` to split into three parts: before (60% opacity), cited chunk (full opacity with 3px accent left border), after (60% opacity). Auto-scrolls to highlighted chunk on load.

## Upload Modal

Upload modal (drag-and-drop + collection selector) closes immediately on file selection; upload progress shown inline in document list via `UploadProgress` component.

## Document Card

Document row with view button: `.md` files open in an in-app Markdown viewer modal; all other file types open in a new browser tab via `/api/documents/{id}/file` (browser decides to display or download). Shows `source` label when not default `"upload"`.

## Document Filters

Filter dropdowns: Collection, Status, Source. Source filter auto-shown when documents have 2+ distinct sources.

## Document Bulk Actions

Bulk action toolbar: Select All, Reprocess, Download (ZIP), Move to Collection, Delete. See [`.claude/domain/admin-features.md`](domain/admin-features.md#bulk-download) for download implementation details.

## Cross-page Action Auto-Start (`?autostart=1`)

The Documents page's "Generate Graph" button uses a query-param handshake instead of duplicating the trigger logic. It calls `router.push("/extract?autostart=1")`; the target page reads `useSearchParams().get("autostart")` inside a one-shot `useEffect`, waits for its own data to load, calls the existing handler once, then `router.replace`s the URL clean so a refresh doesn't refire. Source of truth for the trigger stays on the destination page — easy to evolve without touching every caller. Used in `frontend/src/app/extract/page.tsx` (consumes) and `frontend/src/components/DocumentList.tsx` (emits). See [`.claude/domain/knowledge-graph-ui.md`](domain/knowledge-graph-ui.md#cross-page-auto-start-autostart1) for the destination-side guards.

## Stats Bar

4 KPI cards: Documents, Entities, Relations, Communities. Refreshes every 5 seconds. Hidden on Settings page (`/admin`).

## SystemMeta Timestamps

`SystemMeta` Neo4j nodes store `last_relationship_analysis_at`, `last_community_detection_at`, and `last_entity_merge_at` timestamps. Upload dates are naive (no timezone) — frontend appends `Z` for UTC comparison.

## Progress Tracking

- Relationship analysis: shows batch X/Y with ETA computed from observed batch duration
- Entity extraction: polls backend task status with progress messages; running tasks detected on mount
- Community detection: polls task status every 2 seconds
- Image analysis: progress polled via document data refresh every 5 seconds
- Stats bar: refreshes every 5 seconds
