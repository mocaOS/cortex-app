# Documentation & Maintenance Rules

When making changes to the codebase, keep all documentation layers in sync. Each layer serves a different audience and purpose.

## Documentation Layers

### `documentation/` — API & Feature Docs (Zudoku)
When adding, modifying, or removing API endpoints, features, or configuration options, update the corresponding pages in `documentation/` (Zudoku-based docs site with pages in `documentation/pages/` and API specs in `documentation/apis/`).

#### Changelog structure (`documentation/pages/changelog.mdx`)

**When to write an entry:** only for new product features or significant changes to existing code. Hotfixes, documentation-only updates, and internal tooling tweaks (e.g. bench) get **no** changelog entry.

Newest entries first. **Exactly one `##` heading per calendar day** — never two `##` entries for the same date. The `##` heading is the **date only** — no parenthetical theme, no suffix (per explicit owner preference; a themed-header format was tried June 10–July 20, 2026 and rolled back). Each day follows this shape:

```markdown
## <Month> <D>, <YYYY>

One intro paragraph summarizing what the day's changes accomplish and why they matter.

### <Topic section>

Optional context paragraph, then bullets (`- **Bold lead-in** — detail.`).

### <Next topic section>
...
```

- If a day covers several unrelated ships, each becomes its own `###` section under the single day heading; merge their intros into one paragraph.
- Never use bold pseudo-headers (`**Skills**`) as section dividers — use `###`. Sub-structure inside a `###` section uses `####`.
- Separate days with `---`; no `---` between sections within a day.
- Keep operator-relevant details in bullets: env vars in backticks with defaults, behavior changes and revert flags called out, and a "No API or schema breakage" note (or explicit breakage warning) where relevant.

### `handbook/` — End-User Handbook
The handbook covers features end-to-end from a user perspective; `handbook/README.md`
is its chapter index. When adding or changing user-facing features, update the
relevant handbook chapter(s):
- `07-documents.md` — document upload, management, filtering
- `08-knowledge-graph.md` — the 3-step extraction pipeline
- `09-search.md` — search functionality
- `10-ask-ai.md` — chat and deep research
- `11-collections.md` — collection management
- `12-communities.md` — community detection and browsing
- `13-deduplication.md` — entity deduplication
- `14-image-analysis.md` — image processing pipeline
- `17-administration.md` — admin settings, system reset, import/export
- `18-skills.md` — Agent Skills system
- Other chapters as relevant (see `handbook/README.md` for full TOC)

### `README.md` — Project Overview
When making changes that affect the project overview, features, API endpoints, environment variables, architecture, or setup instructions, update `README.md` accordingly.

### `design-system/` — Visual Design
When making global design changes (color tokens, typography, spacing scale, animation defaults, new component patterns, or glass morphism treatment), update `design-system/MASTER.md`, `tokens.css`, and `tailwind.preset.ts` accordingly. For page-specific design changes, update or create the corresponding `design-system/pages/<page>.md` override. See [`.claude/design-system.md`](design-system.md).

### `.claude/` — This Handbook (Claude Code Context)
When changes affect the architecture, domain logic, key patterns, environment variables, or development/deployment instructions:
1. Update the relevant `.claude/` subfile(s) — see the routing table in `.claude/navigation.md` (linked by root `CLAUDE.md`)
2. If you add a new subfile, add it to the navigation map and file-path routing in `.claude/navigation.md`
3. If you remove or rename a subfile, update the scoped navigation map and all affected cross-references
4. Keep subfiles between 50–300 lines; split if they grow beyond that

### Root `CLAUDE.md` — Index File
Keep the root `CLAUDE.md` under 80 lines: actionable operating contract plus entry
points. Detailed path routing belongs in `navigation.md`; campaign evidence in
`regeneration.md` and `qa/`. `AGENTS.md` is only a read adapter. Repair the owning
root/scoped rule when a fresh session exposes a missing instruction; ordinary links
are navigation, not harness imports.

## Authority and publication map

| Surface / steward | Canonical source | Validation / version basis |
|---|---|---|
| Developer/API docs — affected domain maintainer | `documentation/pages/`, `documentation/apis/openapi.yaml`, `zudoku.config.tsx` | `npm run validate`, `npm test`, `npm run build` from `documentation/`; published checkout can lag released images |
| LLM docs mirror — docs maintainer | Zudoku-generated `llms.txt` / `llms-full.txt` from those pages | `npm run validate:live` after publication: nonempty mirror, page inclusion, examples; not semantic equivalence |
| User handbook — feature maintainer | `handbook/` | compare affected journey with feature docs and implementation; no independent API truth |
| App developer knowledge — unit maintainer | root `CLAUDE.md` → `navigation.md` → owning guide | explicit read/link check plus sampled fresh-session task; loader evidence separate |
| Chat developer knowledge — chat maintainer | `cortex-chat/CLAUDE.md` + `.claude/guides/` | chat gates; user/operator docs remain here in `features/cortex-chat.mdx` and handbook chapter 25 |
| Agent skills/SDK/MCP — skills maintainer | `cortex-skills/public/`, `sdk/`, `mcp-server/` | skill manifest + SDK/MCP consumer checks in that repo; references back to this API |

The zero-dependency validator checks **names/shapes**: method+path tables, env
names, required skill/chat wiring coverage and page links. It does not validate
defaults, permission semantics or every prose example. External component env-name
allowlists need source review when those components change. Its self-tests run
healthy and broken fixtures with all network targets on loopback. CI runs the
offline validator/self-tests; live publication checks are a separate stage.

When changing a boundary, inspect both producer and consumers: an SDK test using a
fake backend does not prove the selected backend/client release combination.
Record actual image/revision sets. Keep `guides/versions.mdx` explicit about known
version skew; an unchanged version string on unreleased `main` is not release evidence.
