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

**Deep Research toggle** (`AskPanel.tsx`): `useAgentic` is in-session state (default Chat; initialized from `initialMode` so `?tab=research` deep-links still open in research). An Erlenmeyer-flask (`FlaskConical`) button flips it any time mid-conversation; the SubMenu "Deep Research" entry was removed in favor of this toggle. A mode chip shows in the input's status line.

**ThinkingIndicator** (`ChatMessage.tsx`): shown while an assistant message streams before any content arrives. Blinking `live-dot` (CSS in `globals.css`) + a staged label + live elapsed-seconds counter + a reassurance line after 12 s. The label prefers the backend `status` event's `message` (stored on the message as `statusMessage`, set from the `status` SSE event in `AskPanel`), falling back to a heuristic over `sources`/`subQuestions`/`thinkingSteps`. See [`domain/rag-pipeline.md`](domain/rag-pipeline.md#streaming-feedback-status--heartbeat).

**Stream resilience** (`AskPanel.tsx` + `api.ts`): the streaming loop finalizes the assistant message on *any* loop exit, not only on a `done` event — a stream that ends from a dropped connection, proxy idle-timeout, or a graceful server redeploy no longer leaves a permanent typing cursor. `api.askStream`/`askStreamWithThinking` parse SSE `event:` names and turn the backend's terminal `event: shutdown` frame into a clear "server is restarting, resend" error event. Backend error frames carry a sanitized message (the raw exception is never sent — `sse_error_frame` in `main.py`), and `AskPanel` shows that `error.message` instead of a generic line. The composer is a multi-line `<textarea>` (Enter sends, Shift+Enter newline, auto-grow) and the send button becomes a **Stop** button while streaming, backed by an `AbortController` (also aborted on unmount, so navigating away stops backend generation). Auto-scroll only fires when the user is already near the bottom.

## Citation Rendering

`MarkdownRenderer` turns `[src_N]` references in the answer into clickable `CitationBadge`s (only when `onCitationClick` is passed — chat/research via `ChatMessage`; `SearchPanel` leaves them as text). The parser matches the **whole bracket group**, so grouped citations the writer often emits — `[src_1, src_3, src_6]` or `[src_1, 3, 6]` — render one badge per number instead of falling through as raw literal text. This is model-agnostic, so both Chat (smaller model) and Deep Research render citations reliably regardless of whether the model groups them.

## Source Modal Highlighting

Cited chunk is highlighted within the full document text. Uses `indexOf()` to split into three parts: before (60% opacity), cited chunk (full opacity with 3px accent left border), after (60% opacity). Auto-scrolls to highlighted chunk on load.

## Upload Modal

Upload modal (drag-and-drop + collection selector) closes immediately on file selection; upload progress shown inline in document list via `UploadProgress` component.

## Document Card

Document row with view button: `.md` files open in an in-app Markdown viewer modal; all other file types open in a new browser tab via `/api/documents/{id}/file` (browser decides to display or download). Shows `source` label when not default `"upload"`.

## Document Filters

Filter dropdowns: Collection, Status, Source. Source filter auto-shown when documents have 2+ distinct sources. Status includes a virtual **Degraded** option (not a backend status): `isDegraded(doc)` in `DocumentList.tsx` = effective status `completed` (so docs still analyzing images are excluded) AND (`entity_count === 0` — exactly 0, backend sends `-1` for unknown — OR `unembedded_chunk_count > 0`). See [`domain/document-pipeline.md`](domain/document-pipeline.md#degraded-document-signals) for the backend signals.

## Degraded Documents UI

`DocumentCard` shows an amber `AlertTriangle` "Degraded" badge (tooltip + inline reason line: "0 entities extracted" / "N chunks missing embeddings — reprocess to retry"); the existing completed-doc reprocess button is the one-click fix. `DocumentList` renders a single combined needs-attention banner ("N failed / M degraded documents") with a **Select all** button that selects failed + degraded for bulk reprocess; `selectAllDegraded` mirrors `selectAllFailed`.

## Document Bulk Actions

Bulk action toolbar: Select All, Reprocess, Download (ZIP), Move to Collection, Delete. See [`.claude/domain/admin-features.md`](domain/admin-features.md#bulk-download) for download implementation details.

## Cross-page Action Auto-Start (`?autostart=1`)

The Documents page's "Generate Graph" button uses a query-param handshake instead of duplicating the trigger logic. It calls `router.push("/extract?autostart=1")`; the target page reads `useSearchParams().get("autostart")` inside a one-shot `useEffect`, waits for its own data to load, calls the existing handler once, then `router.replace`s the URL clean so a refresh doesn't refire. Source of truth for the trigger stays on the destination page — easy to evolve without touching every caller. Used in `frontend/src/app/extract/page.tsx` (consumes) and `frontend/src/components/DocumentList.tsx` (emits). See [`.claude/domain/knowledge-graph-ui.md`](domain/knowledge-graph-ui.md#cross-page-auto-start-autostart1) for the destination-side guards.

## Stats Bar

4 KPI cards: Documents, Entities, Relations, Communities. Refreshes every 5 seconds. Hidden on Settings page (`/admin`). The loading shimmer only shows on the **first** load (tracked via an `initial` flag); the 5 s background poll updates the numbers silently so the bar doesn't flash a skeleton every cycle.

## SystemMeta Timestamps

`SystemMeta` Neo4j nodes store `last_relationship_analysis_at`, `last_community_detection_at`, and `last_entity_merge_at` timestamps. Upload dates are naive (no timezone) — frontend appends `Z` for UTC comparison.

## Progress Tracking

- Relationship analysis: shows batch X/Y with ETA computed from observed batch duration
- Entity extraction: polls backend task status with progress messages; running tasks detected on mount
- Community detection: polls task status every 2 seconds
- Image analysis: progress polled via document data refresh every 5 seconds
- Stats bar: refreshes every 5 seconds

### Ingestion phase stepper (2026-07-08)

`lib/ingestionPhases.ts` maps the backend's raw `progress_message` strings onto the stable text-pipeline phases **Convert → Chunk & Embed → Store → Extract** (`deriveIngestionPhases`), extracting live counts (chunks stored X/Y, per-chunk relationships X/Y, entities N/total) as within-phase fractions. Unknown messages degrade gracefully via `processing_status`. Image analysis is deliberately NOT a linear step — it runs concurrently with extraction, exposed separately via `deriveImageProgress`.

`components/documents/IngestionStepper.tsx` renders the phase chips + overall bar + within-phase fraction bar + a parallel image row; `compact` prop for dense lists. Used in `DocumentCard` (replaces the old bare message+percent bar) and the Knowledge Graph Step 1 panel (per-document breakdown replacing the aggregate "Processing N documents..." banner). Pending docs distinguish "queued by the system" (`progress_message` present → "… — waiting for a processing slot") from plain "Unprocessed". When adding new backend `progress_message` strings, extend `parseMessage` in `ingestionPhases.ts`.

## Shared Resilience Hooks (`lib/hooks.ts`)

Two reusable hooks back the app-wide robustness conventions:
- `useIsMounted()` → a ref whose `.current` flips false on unmount. Pollers and async callbacks check `if (!mounted.current) return;` before `setState` so a request resolving after navigation can't update an unmounted component (used in `explore/page.tsx`, `CollectionPanel`, `GitIntegrations`, `LibraryTransferSection`, `SkillsManager`, `ApiKeyAnalytics`).
- `useModalDismiss(onClose)` → returns a ref for the dialog container; closes on Escape, focuses the dialog/first control on open, traps Tab focus, and restores focus on close. Attach with `ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true"`. Only for **mount-on-open** modals (conditionally rendered); always-mounted/`isOpen`-toggled modals keep their own handlers. Applied to SystemResetModal, the API-key create modal, the dedup HistoryModal, ApiKeyAnalytics, and the entity/community detail modals.

**Error & stale-response conventions:** data panels render an explicit error+retry state (not a silent `console.error` that masquerades as an empty result) — see `DocumentList`, `SearchPanel`, `CollectionPanel`, `CollectionSelector`, and the explore detail modals. Fetches that can race (graph load, debounced entity search, analytics range switch) capture a monotonic request-id ref at call start and discard a response if a newer request has started. The API client (`api.ts`) centralizes failure handling: FastAPI 422 arrays are flattened to readable text, a 401 clears the stored key and redirects to `/login`, and non-JSON gateway bodies don't leak into the UI.
