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

- **"Generate Graph"** button (no entities exist) — primary CTA, runs full 3-step pipeline
- **"Regenerate Graph"** button (entities exist) — runs full pipeline from scratch

Regeneration cleanup order: `deleteAllCommunities()` → `deleteAllRelationships()` → `deleteAllEntities()` → `reprocessDocuments()` → relationship analysis (rebuild) → community detection — a true from-scratch rebuild.

## Flow Persistence & Resume

Flow state persisted in `sessionStorage` with a `regenerateTaskId` for the active step's backend task. Resume logic on mount checks the saved task's status:
- running → resume polling
- completed → advance to next step
- failed → abort
- not found → start fresh

This eliminates heuristic-based step-skipping.

## Step 1 Details

- Entity extraction has proper task polling with backend progress messages; running tasks detected on mount
- Fresh instance warning on "Extract Entities" (0 entities) recommends "Generate Graph" instead
- Displays "X entities and Y within-document relationships extracted" (using `per_chunk_relationship_count` from stats)

### Image Analysis Awareness

Step 1 tracks documents with background image analysis in progress (completed text processing but `image_progress_current < image_progress_total`):
- These docs are shown in a separate "Analyzing Images" tile with an aggregate progress bar
- Step 1 stays "In Progress" until all images are analyzed
- Auto-refresh polls every 5 seconds to keep progress updated
- Step 2/3 remain blocked until image analysis completes
- The "Processed" count in the summary grid only includes `fullyCompletedDocs` (completed AND images done)

## Step 2 Details

- ERR (Entity-Relationship Ratio) indicator: color-coded (green >= 0.69, yellow >= 0.29, red < 0.29), displayed to 2 decimal places, tooltip explaining the metric
- Displays only cross-document relationships (total minus `per_chunk_relationship_count`)
- Supports incremental mode (default) and rebuild mode
- "Find more" button does 1 additional round. See [`.claude/domain/relationships.md`](relationships.md#multi-round-discovery)

## Progress Tracking

- Relationship analysis: batch X/Y with ETA computed from observed batch duration
- Entity extraction: polls backend task status with progress messages
- Community detection: polls task status every 2 seconds
- Image analysis: polled via document data refresh every 5 seconds
