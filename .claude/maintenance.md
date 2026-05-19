# Documentation & Maintenance Rules

When making changes to the codebase, keep all documentation layers in sync. Each layer serves a different audience and purpose.

## Documentation Layers

### `documentation/` — API & Feature Docs (Zudoku)
When adding, modifying, or removing API endpoints, features, or configuration options, update the corresponding pages in `documentation/` (Zudoku-based docs site with pages in `documentation/pages/` and API specs in `documentation/apis/`).

### `handbook/` — End-User Handbook
The handbook contains 21 chapters (`01-introduction.md` through `21-glossary.md`) covering features end-to-end from a user perspective. When adding or changing user-facing features, update the relevant handbook chapter(s):
- `07-documents.md` — document upload, management, filtering
- `08-knowledge-graph.md` — the 3-step extraction pipeline
- `09-search.md` — search functionality
- `10-ask-ai.md` — chat and deep research
- `11-collections.md` — collection management
- `12-communities.md` — community detection and browsing
- `13-deduplication.md` — entity deduplication
- `14-image-analysis.md` — image processing pipeline
- `15-turbo-mode.md` — GPU-accelerated inference
- `18-administration.md` — admin settings, system reset, import/export
- `19-skills.md` — Agent Skills system
- Other chapters as relevant (see `handbook/README.md` for full TOC)

### `README.md` — Project Overview
When making changes that affect the project overview, features, API endpoints, environment variables, architecture, or setup instructions, update `README.md` accordingly.

### `design-system/` — Visual Design
When making global design changes (color tokens, typography, spacing scale, animation defaults, new component patterns, or glass morphism treatment), update `design-system/MASTER.md`, `tokens.css`, and `tailwind.preset.ts` accordingly. For page-specific design changes, update or create the corresponding `design-system/pages/<page>.md` override. See [`.claude/design-system.md`](design-system.md).

### `.claude/` — This Handbook (Claude Code Context)
When changes affect the architecture, domain logic, key patterns, environment variables, or development/deployment instructions:
1. Update the relevant `.claude/` subfile(s) — see the routing table in the root `CLAUDE.md` to find which file(s) to update
2. If you add a new subfile, add it to the navigation map and file-path routing in root `CLAUDE.md`
3. If you remove or rename a subfile, update all cross-references in other `.claude/` files and root `CLAUDE.md`
4. Keep subfiles between 50–300 lines; split if they grow beyond that

### Root `CLAUDE.md` — Index File
Keep the root `CLAUDE.md` under 80 lines. It is an index, not a content file. Only update it when adding/removing/renaming `.claude/` subfiles or when the file-path routing table needs new entries.
