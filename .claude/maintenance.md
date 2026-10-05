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

For error/scoping prose, trace endpoint dependencies as well as the handler and
all retrieval/enrichment consumers. An auth/payment dependency can return 503
without a search-index readiness contract; scope validation is not an existence
lookup. Collection and community identifiers have different producer/type/label
owners. Preserve the distinction between no scope (`None`) and an empty scope
(`[]`) through optional enrichment, rather than deriving authorization from
truthiness or one mocked HTTP response.

Trace each named retrieval entry to its actual fusion and score producer before
copying hybrid-search prose between REST, Ask AI/context, MCP and plugins. REST
search and researcher retrieval have distinct legs, flag consumers and scoring;
their canonical distinction lives in `domain/rag-pipeline.md`. ANN over-fetch
factors apply to the receiving leg's fetch depth, which can differ from the
request's `top_k`. Audit client score thresholds against the actual producer's
scale rather than assuming cosine or cross-encoder semantics.

Generated-artifact ownership is determined by the generator's actual outputs:
Skills `public/index.json` is generated; its excluded `public/llms.txt` is static
source. Zudoku's mirror includes its configured pages, not the separate handbook.
If post-build judgment fails with unchanged build inputs and retained output,
repair and rejudge that output read-only before considering another build.

The flag-off repair's exact local claim is scope forwarding and chunk-query
assembly; global graph entity/relationship metadata is a distinct open finding
in `domain/rag-pipeline.md`. Keep result/probe names and producer provenance
accurate in structured receipts too. Specify the measured units, root-inclusive
counts and executed timestamps separately from report-assembly stamps; sampled
installed binaries do not establish a whole installed graph's identity.

For legacy ask prose, bind transport→function→flag→deadline→no-key behavior→public
projection before generalizing a fix. SSE agentic_rag_stream and nonstream
_agentic_rag_query are separate implementations; only the latter recurses without
an LLM key. Their owning gate/limits live in rag-pipeline.md. Public guidance keeps
current use/version limits; rejected source conflations and test totals belong in
the slice record. Attribute this round against its before snapshot, not HEAD's
cumulative dirty diff. Final receipt hashes belong in a successor manifest, with
actual command/observation times separate from report-assembly approximations.
When reusing an executed docs-build recipe, read its accepted judgment/supplements:
the preserved raw script can predate a probe correction. Carry the corrected needle
semantics into the new judge; a flag-off phrase does not prove a flag-conditional400.

Nonstream call budgets are literal arguments in rag_query (1200) and
_agentic_rag_query (2000); WRITER_MAX_TOKENS_SPEED configures a different streamed
speed writer (both agent and legacy standard chat). Bind completion reason to the
answer synthesis and its public
projection, not decomposition or a sibling's settings. The owning v3 gate and
acceptance delta are linked from qa.md and rag-pipeline.md.

Legacy streaming's machine truncated flag does not add the agent writer's visible
notice or expose finish_reason on SSE. Read terminal chunks independently of content
and keep these distinctions in public prose. Serialize required healthy observations
as well as failures, and keep producer/finalizer receipts stage-specific. Portable
guidance is now2.19.0; this round's executed gates retain2.18.0 and their original
identities. A portable-only harvest does not justify replaying product gates.

Standard streaming has its own flag-off branch and configurable cap; its local
done-flag repair is documented separately from legacy deep research and the agent's
notice. Keep branch-specific source facts and evidence limits at their owners above.
Missing retained command evidence justifies receipt-bearing re-execution of those
stages, not rebuilding an unchanged retained docs mirror. Preserve transcript-only
claims as such and correct dangling receipt links additively.

Fast completion uses a separate branch/model and literal600-token output cap;
its 600-character context slices have different units. Keep branch qualifications
in table rows as well as prose. Required command stages must retain logs/exit/input
receipts as they run, and idempotency retains both outputs. Portable guidance2.20.0
harvests these stage/selection/identity lessons; completed fast/standard executions
keep2.19.0. Existing root policies already route this through QA/RAG/maintenance.

Optional REST metadata prose must distinguish the actual helper's typed values
from schema-tolerance seam probes, and pre-retrieval input-screen refusals from
model refusals after retrieval. The latter can carry populated research metadata
with refusal flags. Bind nonstream stats keys to `_agentic_rag_query`, rather
than transferring SSE keys or a similarly named configuration flag. Keep release
claims bounded to inspected versions/capabilities. Owning source correction and
unreleased projection evidence: `output/legacy-agentic-optional-projection-20261005/`.

Portable2.21 harvest adds typed optional-value/failure-phase, actual writer-scope
and stage-input lessons. When a source-stage record gains a later closeout,
preserve its executed bytes/receipt and identify the addendum's actual consumers;
an unused preflight record is not automatically a build input. Verify any claimed
exact prefix rather than equating harmless formatting with byte preservation.
Current method guidance and a stage's historical execution basis remain separate.

Full OpenAPI drift work starts with the real runtime generator and complete
routes/models, isolated from ambient configuration and service startup. Retain
the complete schemas and exhaustive differences; identify tool versions and
normalization limits. Count unique operations separately from facet differences,
and distinguish structure equivalence from equivalence after ignoring prose.
`operationId` changes are consumer-visible even if a diff labels them cosmetic.
The current capture/comparison and additive count corrections are at
`output/claims-openapi-20261004/openapi/`; schema alignment remains a separate
authorized change.

For a syntax-only check, prefer in-memory `compile(...)` or a run-owned bytecode
destination. Never use recursive/time-based cache deletion to clean a newly
generated artifact: directory mtimes do not identify ownership of pre-existing
contents. Retain a failed runner or mistaken human summary and correct it
additively; a different selected test set cannot disprove another run's count.
