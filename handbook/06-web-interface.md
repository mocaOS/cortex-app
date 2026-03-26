# Chapter 6: The Web Interface

This chapter provides a complete walkthrough of the Library's web interface — every page, every feature, and every interaction.

## Interface Overview

The Library's web interface is built with Next.js 15 and React 19, featuring a dark-mode-first design with glass morphism surfaces, smooth Framer Motion animations, and a monochrome palette with a single configurable accent color.

### Layout Structure

```
┌─────────────────────────────────────────────────────────────────┐
│  Header                                                          │
│  [Logo]              [Manage] [Explore]          [Turbo] [⚙]   │
├─────────────────────────────────────────────────────────────────┤
│  SubMenu (contextual tabs)                                       │
│  [Documents] [Knowledge Graph] [Deduplicate] [Collections] [Add] │
├─────────────────────────────────────────────────────────────────┤
│  Stats Bar                                                       │
│  Documents: 42  │  Entities: 384  │  Relations: 1,207  │  ...   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Main Content Area                                               │
│  (page-specific content)                                         │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Header

The sticky top navigation bar contains:
- **Logo** (left) — Links to the Documents page. Customizable via `NEXT_PUBLIC_LOGO_URL`.
- **Primary Navigation** (center) — Glass morphism pills for Manage and Explore sections
- **Turbo Mode Indicator** (right, conditional) — Green dot when GPU active and ready, yellow pulsing when warming up. Only visible when Compute3 is configured.
- **Settings Icon** (right) — Gear icon linking to `/admin`

### SubMenu

A contextual tab bar that changes based on your current section:

**Manage Section:**

| Tab | Icon | Path | Description |
|-----|------|------|-------------|
| Documents | FileText | `/documents` | Upload, view, and manage documents |
| Knowledge Graph | Network | `/extract` | Build the graph (3-step pipeline) |
| Deduplicate | Merge | `/deduplicate` | Find and merge duplicate entities |
| Collections | Layers | `/collections` | Organize documents into collections |
| Add | — | `/add` | Manually add Q&A, text, or markdown |

**Explore Section:**

| Tab | Icon | Path | Description |
|-----|------|------|-------------|
| Knowledge Graph | Share2 | `/explore?tab=graph` | Interactive force-graph visualization |
| Entities | — | `/explore?tab=entities` | Paginated entity browser |
| Relationships | — | `/explore?tab=relationships` | Paginated relationship browser |
| Communities | Users | `/explore?tab=communities` | Community browser with summaries |
| Deep Research | Sparkles | `/explore?tab=research` | Multi-step agentic Q&A |
| Chat | MessageSquare | `/explore?tab=chat` | Quick conversational Q&A |

### Stats Bar

A persistent statistics bar showing four key metrics:

| Metric | Source | Update Frequency |
|--------|--------|-----------------|
| **Documents** | Total document count | Every 5 seconds |
| **Entities** | Total entity count | Every 5 seconds |
| **Relations** | Total relationship count | Every 5 seconds |
| **Communities** | Total community count | Every 5 seconds |

The Stats Bar is hidden on the Settings page.

## Manage Section

### Documents Page (`/documents`)

The default landing page. Shows all uploaded documents in a list with:

- **Document name** and file type icon
- **Processing status** badge (pending, processing, extracting, completed, failed)
- **Upload date** and file size
- **Collection** assignment
- **View button** — Opens `.md` files in an in-app Markdown viewer; other files open in a new browser tab
- **Upload progress** — Shown inline when a document is being uploaded or processed

**Actions:**
- **Upload** button — Opens the upload modal (drag-and-drop with collection selector)
- **Generate Graph** button — Navigates to the Knowledge Graph page (`/extract`)
- **Delete** — Delete individual or selected documents
- **Reprocess** — Re-run the processing pipeline on a document

### Knowledge Graph Page (`/extract`)

The control center for building and managing your knowledge graph. Features a 3-step pipeline with status tracking:

**Step 1: Entity Extraction**
- Shows document processing summary: Processed / Processing / Analyzing Images / Pending / Failed
- Image analysis awareness: Documents with ongoing background image analysis shown separately with aggregate progress bar
- Step remains "In Progress" until all image analysis completes
- CTA: "Extract Entities" button (or "Generate Graph" when starting fresh)

**Step 2: Relationship Analysis**
- Shows real-time relationship discovery count during analysis
- Two modes: "Analyze Relationships" (incremental) or "Rebuild" (from scratch, with confirmation dialog)
- "Skip" option to proceed to Step 3

**Step 3: Community Detection**
- Detects entity communities using graph algorithms
- Generates LLM summaries for each community
- Staleness detection: alerts when communities need re-detection (after merges or new relationships)

**Pipeline Controls:**
- **Generate Graph** (when no entities exist) — Runs all 3 steps from scratch
- **Regenerate Graph** (when entities exist) — Deletes everything (communities → relationships → entities), then reprocesses all documents through the full pipeline
- **Inspect** buttons — Quick links to the relevant Explore tab for each step
- **Progress persistence** — Pipeline state saved to sessionStorage; resumes on page reload

### Deduplicate Page (`/deduplicate`)

Entity deduplication interface for finding and merging similar entities:

- **Scan** — Automatically finds potential duplicate groups using fuzzy matching
- **Review** — Each group shows a suggested canonical entity and its duplicates
- **Add** — Inline entity search to manually add entities to a group
- **Merge** — Combines duplicates into the canonical entity with LLM-generated description
- **Dismiss** — Mark a group as "not duplicates" (stored in localStorage)
- **Merge History** — Modal showing all past merges with timestamps and entity snapshots

### Collections Page

Manage document collections:
- Create new collections with names and descriptions
- View collection statistics (document count, entity count)
- Delete collections (documents moved to default)

### Add Page (`/add`)

Manually add knowledge without file uploads:
- **Q&A** — Question and answer pairs
- **Text** — Freeform text content
- **Markdown** — Formatted markdown documents
- Collection assignment and topic generation

## Explore Section

### Knowledge Graph Visualization (`/explore?tab=graph`)

An interactive force-directed 2D graph visualization using react-force-graph-2d:

**Display:**
- Default 100 nodes, selected by diversity score (balances mention count against connection degree to prevent hub entities from dominating the view)
- Nodes colored by entity type (Person: orange, Organization: cyan, Concept: pink, etc.)
- Node size based on `log(mention_count)` for visual scaling
- Relationships shown as labeled edges

**Interaction:**
- **Click a node** → Entity Panel opens on the right side showing:
  - Entity name, type badge, mention count
  - Full description
  - Related entities (clickable, with "loaded" vs "unloaded" indicators)
  - Key relationships (up to 8, with type, target, and weight)
- **Click an unloaded entity** → Dynamic graph expansion:
  1. Fetches entity + 1-hop neighbors
  2. Fetches bridge subgraph (shared connections between selected and target)
  3. New nodes appear near the selected entity
  4. Force layout reheats for smooth positioning
  5. View auto-centers on the new entity
- **Pan, zoom, drag** — Standard graph navigation
- **Canvas click** — Deselects entity (closes panel)

### Entity Browser (`/explore?tab=entities`)

Paginated server-side browser for all entities:
- **Search** — 300ms debounced search in entity names and descriptions
- **Type filter** — Dropdown with all distinct entity types
- **Pagination** — 50 entities per page with skip/limit
- **Detail modal** — Click any entity to see full description, type, source documents, and connection count
- **Subtle opacity transition** during fetches for smooth UX

### Relationship Browser (`/explore?tab=relationships`)

Paginated browser for entity relationships:
- **Search** — Search in source/target entity names and descriptions
- **Type filter** — Dropdown with all distinct relationship types
- **Pagination** — 50 relationships per page
- **Detail modal** — Shows source, target, type, description, weight, and evidence

### Community Browser (`/explore?tab=communities`)

Paginated browser for detected communities:
- **Search** — Search in community names, summaries, and member entity names
- **Pagination** — 25 communities per page
- **Detail modal** — Shows name, summary (cleaned of JSON artifacts), member entities, and key relationships within the community

### Deep Research (`/explore?tab=research`)

Multi-step agentic Q&A for complex questions:

**Interface:**
- Text input for your question
- Streaming response display with research process visualization
- Research Process blocks (rendered above the answer): Sub-Questions, Thinking Steps, Reasoning Steps
- Auto-scrolling research process container
- Source citations with highlight modals

**How it works:**
1. Enter a question
2. The researcher agent conducts iterative research (up to 10 iterations)
3. Visible thinking events stream in real-time
4. Sources accumulate as the agent searches
5. The writer synthesizes a comprehensive answer
6. Sources are clickable — opening a modal that highlights the specific cited chunk within the full document text

### Chat (`/explore?tab=chat`)

Quick conversational Q&A with history:

**Interface:**
- Message bubbles for user and assistant
- Streaming responses
- Conversation history maintained across messages
- Collection selector for scoped queries

**How it works:**
1. Enter a question
2. The researcher agent runs 2 iterations (speed mode)
3. Answer streams in real-time with source citations
4. Follow-up questions include conversation history for context

### Source Citation Modals

When you click a source citation in Chat or Deep Research:

1. A modal opens showing the full document text
2. The specific cited chunk is **highlighted** with full opacity and a 3px accent-colored left border
3. Text before and after the citation is dimmed (60% opacity)
4. The view auto-scrolls to the highlighted section
5. The chunk is identified using `indexOf()` matching within the concatenated document content

## Settings (`/admin`)

### Statistics Dashboard

Overview of your knowledge base health:
- Document, entity, relationship, and community counts
- Processing status breakdown
- Entity type distribution

### LLM Configuration Display

View your current LLM setup (no secrets exposed):

| Sub-area | Shows |
|----------|-------|
| **Primary Model** | Model name, API base URL |
| **Extraction Model** | Model name, API base, context window |
| **Vision Model** | Model name, API base, concurrency |
| **Embeddings** | Model name, dimension, API base |

### API Key Management

Create, view, revoke, and delete API keys. See [Chapter 5: Security](05-security.md) for details.

### Danger Zone

**System Reset** — Selective deletion with confirmation:
- Delete documents (and all graph data)
- Delete uploaded files
- Delete custom inputs
- Delete collections
- Delete API keys

Type "DELETE" to confirm. The reset also cleans up:
- MergeHistory nodes (deduplication audit trail)
- SystemMeta nodes (staleness timestamps)
- Client-side cached data (localStorage and sessionStorage)

## Design System

The Library uses a portable design system with these characteristics:

- **Color**: Monochrome foundation with a single dynamic accent color (configurable via `NEXT_PUBLIC_ACCENT_COLOR`)
- **Mode**: Dark mode default, light mode supported
- **Surfaces**: Glass morphism (24px backdrop blur, semitransparent backgrounds)
- **Typography**: Inter (UI text) + JetBrains Mono (code/monospace)
- **Icons**: Lucide icon library exclusively
- **Animation**: Framer Motion with spring physics
- **Spacing**: Tailwind 4px grid system
