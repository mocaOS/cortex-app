# Knowledge Graph UI (3-Step Pipeline)

The Knowledge Graph page (`/extract`, `app/extract/page.tsx`) guides users through a 3-step pipeline with staleness detection and cascading blocked states.

## Three Steps

1. **Entity Extraction & Relationship Discovery** — Per-document processing (Phase A). See [`.claude/domain/document-pipeline.md`](document-pipeline.md)
2. **Deep Relationship Analysis** — Cross-document batch analysis (Phase B). See [`.claude/domain/relationships.md`](relationships.md#batch-analysis)
3. **Community Detection** — Graph clustering and summarization. See [`.claude/domain/communities.md`](communities.md)

## Staleness Detection

Uses `SystemMeta` Neo4j nodes with timestamps (`last_relationship_analysis_at`, `last_community_detection_at`, `last_entity_merge_at`):
- Pending docs → Step 1 needs update
- New entities since last relationship analysis → Step 2 needs update
- Entities merged since last community detection → Step 3 needs update
- Steps cascade: Step 2/3 grey out when a prior step needs update

Each step has an "Inspect" button linking to the relevant Explore tab.

## Generate / Regenerate Graph

- **"Generate Graph"** button (no entities exist) — primary CTA, runs full 3-step pipeline. Rendered right-aligned at the top of the page.
- **"Regenerate Graph"** button (entities exist) — runs full pipeline from scratch.

Cleanup order on click: `deleteAllCommunities()` → `deleteAllRelationships()` → `deleteAllEntities()` → `reprocessDocuments(ids, chain="relationship_analysis,community_detection")`. After the delete-and-kick-off, the **backend** drives the chain — see below.

### Cross-page auto-start (`?autostart=1`)

The Documents page's graph banner button (`DocumentList.tsx`) navigates to `/extract?autostart=1` instead of plain `/extract`. A one-shot `useEffect` on the Knowledge Graph page (`extract/page.tsx`) detects the param, waits for the initial data fetch + `documents.length > 0`, dispatches once, and `router.replace("/extract")`s the URL so a refresh won't re-fire. A `hasAutoStarted` ref guards against double-fires within the same mount.

The dispatch depends on graph state (2026-07-09 — previously it always ran the full rebuild, which surprised users who had just uploaded one document):

- **Entities exist** → `handleExtractEntities()` — incremental Step 1 over pending docs only, no confirm, nothing deleted. Steps 2/3 then show as stale per the staleness model. The banner button is labeled **"Update Graph"** (graph existence proxied client-side via `documents.some(d => d.entity_count > 0)`).
- **No entities (fresh instance)** → `handleRegenerateGraph()` — full 3-step chain; its confirm dialog is skipped by its own `hasExistingGraph` check anyway. Button labeled **"Generate Graph"**.

## Backend-Orchestrated Chain

The full pipeline is orchestrated server-side via a `chain` query param on `/api/documents/reprocess` (also accepted on `/api/documents/process-pending` and `/api/graph/relationships/analyze`). When `_run_batch_processing_task` finishes (text + per-chunk relationships + image analysis), it spawns `_run_relationship_analysis_task` with the remaining chain; that task in turn spawns `_run_community_detection_task`. Each step keeps its own `task_id` / `task_type` / progress message so the UI accurately shows "Step N in progress."

Why this design:
- **Survives navigation / browser close.** The frontend isn't the orchestrator — the user can leave `/extract`, close the tab, or refresh, and the chain still runs.
- **Each step is its own task.** Unlike the deleted `AUTO_*_AFTER_BATCH` flags that crammed all phases into the single Step 1 task, every step has a distinct task that the UI can label and progress against.
- **`chain` is opt-in per request.** Plain "Extract Entities" / "Analyze Relationships" / "Detect Communities" buttons never auto-advance; library imports also never chain. Only "Generate Graph" / "Regenerate Graph" sets the chain string.

Chain spawning uses `asyncio.create_task()` with a module-level strong-ref set (`_chain_tasks`) so the follow-up survives the parent task returning. Helpers: `_parse_chain()`, `_spawn_chain_task()`, `_wait_for_image_analysis_complete()` in `backend/app/main.py`.

### Chain survives server restarts (2026-07-11)

The chain lives in coroutine memory, so a restart mid-run (redeploy, dev `--reload`) used to kill it — the startup auto-resume restarted Step 1 *without* the chain and the pipeline visibly stopped after Step 1 (the original symptom: "Generate Graph runs Step 1 then stops"). Now every pipeline task persists a `resume_context` on its TaskRecord (`TaskProgress.resume_context` → `context_json`): batch tasks store `{concurrency, chain}`, relationship tasks `{collection_id, scope, rebuild, chain}`, community tasks `{min_size, collection_id}`. At boot, `fail_interrupted_task_records()` returns the interrupted records, `_pick_interrupted_pipeline_step()` selects the newest pipeline step among them, and `_auto_resume_after_restart` resumes **that step with its chain** — Step 1 (also covering runs killed between queueing and the first document, where docs sit at `pending` and the orphan-reset sees nothing), Step 2, or Step 3. If docs were stranded mid-processing while a Step 2/3 task was interrupted (concurrent upload), Step 1 resumes first, then the interrupted step. Gated by `AUTO_RESUME_PENDING_ON_STARTUP` + monthly quota. Tests: `backend/tests/test_pipeline_chain_resume.py`.

## Flow Persistence & Resume

The frontend persists only `regenerateActive=true` + `regenerateStep` (highest step seen) in `sessionStorage`. A **chain observer** useEffect polls `listTasks("running", task_type)` every 3 s for each pipeline task type:
- `batch_processing` / `reprocess_batch` running → Step 1, attach `pollEntityTask`
- `relationship_analysis` running → Step 2, attach `pollRelationshipTask`
- `community_detection` running → Step 3, attach `pollCommunityTask`
- none running and `highestSeenStep >= 3` → finish regen
- none running for 10 consecutive polls (~30 s) → backend likely lost state on reload, abort

No per-step `regenerateTaskId` is stored anymore; the observer rediscovers the active task on every mount. This is what makes the flow robust to closing the browser mid-Step-2 and coming back hours later.

## Step 1 Details

- Entity extraction has proper task polling with backend progress messages; running tasks detected on mount
- Fresh instance warning on "Extract Entities" (0 entities) recommends "Generate Graph" instead
- Displays "X entities and Y relations extracted" (using `per_chunk_relationship_count` from stats)
- Granular progress messages now tick inside the per-document work — see [`document-pipeline.md`](document-pipeline.md#entity-embedding--storage) for the batched-embedding + per-entity-storage messages, and [`relationships.md`](relationships.md#per-chunk-extraction) for the per-chunk streaming progress.

### Image Analysis Awareness

The **backend** holds Step 1's task in `running` state until all background image analysis finishes (`_wait_for_image_analysis_complete` polls Neo4j for documents where `image_progress_current < image_progress_total` and updates the task message with `done/total images across N document(s)`). The task only transitions to `completed` — and the chain only advances — once images are done.

Frontend display:
- Documents in the image-analysis phase are shown in a separate "Analyzing Images" tile with an aggregate progress bar
- Step 1 stays "In Progress" by virtue of its backend task still running
- Auto-refresh polls every 5 seconds to keep document state fresh
- The "Processed" count in the summary grid only includes `fullyCompletedDocs` (completed AND images done)
- **Per-document breakdown (2026-07-08):** while Step 1 runs, a panel lists every in-flight document with a compact `IngestionStepper` (Convert → Chunk & Embed → Store → Extract phase chips + live counts) and per-doc image-analysis bars — replacing the old aggregate "Processing N documents in parallel..." text. See [`frontend-patterns.md`](../frontend-patterns.md#ingestion-phase-stepper-2026-07-08).

The previous frontend-side `waitingForImagesBeforeStep2` flag was removed when the gate moved to the backend.

## Step 2 Details

- ERR (Entity-Relationship Ratio) indicator: color-coded (green >= 0.69, yellow >= 0.29, red < 0.29), displayed to 2 decimal places, tooltip explaining the metric
- Displays only cross-document relations (total minus `per_chunk_relationship_count`)
- Supports incremental mode (default) and rebuild mode
- "Find more" button does 1 additional round. See [`.claude/domain/relationships.md`](relationships.md#multi-round-discovery)

## Progress Tracking

- Relationship analysis: batch X/Y with ETA computed from observed batch duration
- Entity extraction: polls backend task status with progress messages
- Community detection: polls task status every 2 seconds
- Image analysis: polled via document data refresh every 5 seconds
