# Regenerative adoption receipt — 2026-10-01

## Engagement and baseline

User requested product-wide application of playbook v2.3.0 to three repositories,
including public docs, with production compatibility and parallel agents. Mode
`translate`; enabling slices across instructions, behavioral evaluations, docs
and upgrade knowledge. Production implementation retained deliberately: meaningful
gates/navigation earn their cost without a speculative rewrite.

| Repository | Original revision (`main`, clean working tree) |
|---|---|
| cortex-app | `bd6c1e035b3e6ece7741a0b733bfd8735145f846` |
| cortex-chat | `40fe79799c4729c4b695890566ef3a51746b3b5e` |
| cortex-skills | `1156aded6726d6105cb13a72ded93efaf55f5bef` |

Candidate is the uncommitted three-repo patch on these bases, identified by
`qa/regenerative-candidate.json` (aggregate of named file SHA-256s; receipt/manifest excluded to avoid
self-reference). Input contracts are the owning domain guides, public API/client
declarations, existing tests and new explicit obligation records linked from
[the campaign index](../.claude/regeneration.md). No hidden user changes were
stashed/reset. Portable playbook unchanged. Source was available to all writers.

Known harness: OpenCode, lead model `venice/openai-gpt-6-astra`; specialist model
revisions unavailable. Local Node v22.22.3/npm 10.9.8; backend torch-free Python
environment at `/tmp/opencode/qa-venv`. Preserve toolchain declarations/locks;
temporary venvs/build outputs/logs are not durable recovery artifacts.

Writes were delegated by repository (chat, skills) and app surface (backend tests/
QA, public docs, upgrade guide/handbook). Lead owns root/scoped navigation,
cross-repo packet, integration and final review. Independent read-only reviewer
reran first gates and challenged the evidence; follow-up corrections recorded below.
No deploy, live datastore mutation, paid product-model run, publication or commit.
Public documentation/manifest/image metadata were fetched read-only.

## Slice results and meaningful verification

| Slice / gate | Exact command (working directory) | Result and claim |
|---|---|---|
| Backend baseline | `/tmp/opencode/qa-venv/bin/python -m pytest -q` (`backend/`) | 1375 passed, 11 failed, 22 skipped. The 11 failures returned HTTP 507 due to <500 MB on upload fixture's `/tmp` tmpfs. Preserved in `QA_CONTRACT_RECORDS.md`. |
| Backend integrated candidate | `TMPDIR=/var/tmp/cortex-qa-tmp /tmp/opencode/qa-venv/bin/python -m pytest -q` (`backend/`) | **1402 passed, 0 failed, 22 skipped**. Root-filesystem scratch with adequate space; disk guard unchanged. +16 new tests; 11 environmental failures closed. |
| Backend lint | `/tmp/opencode/qa-venv/bin/python -m ruff check --select E9,F63,F7,F82 app/ tests/` (`backend/`) | passed; all live E2E remain unexecuted/skipped |
| Chat | `npm run typecheck`; `npm test` (`cortex-chat/`) | **60 passed, 0 failed/skipped**, typecheck exit 0; real client, crypto and throwaway SQLite migrator |
| Skills SDK | `npm test` (`cortex-skills/sdk/`) | **25 passed, 0 failed/skipped**; wire/error/memory and incremental callback contracts |
| Standalone MCP | `npm test` (`cortex-skills/mcp-server/`) | **6 passed, 0 failed/skipped**; real stdio process and local HTTP fixture, no real backend/provider |
| Skills site | `npm run lint`; `npm run manifest`; `npm run build` (`cortex-skills/`) | passed; two pre-existing lint warnings; consecutive generated manifest bytes identical |
| Docs static + gate controls | `npm run validate`; `npm test` (`documentation/`) | 31 pages, 155 method+path routes, 329+71 env names; **10 passed, 0 failed/skipped** including all-local healthy/broken mirror fixtures |
| Docs build | `npm ci`; `npm run build` (`documentation/`) | passed; generated mirror includes `/guides/versions`; API prerender `<html>` warning observed; not a behavior/schema proof |
| Published docs | `node scripts/validate-docs.mjs --live` (`documentation/`) | **not current**: deployed mirror has 30 documents and lacks `/guides/versions`; all four live targets ran; publication deferred to normal release process |

Final root-script gates, docs rerun after editorial review, link checks and app
fresh-session probe are recorded in the final verification section below.
Test counts identify this run, not fixed future acceptance thresholds. Process
exit, selected cases, setup/teardown errors and skips matter as well as assertions.

### Evaluator sensitivity and review harvest

- App auth tests exercise actual prefix/hash authentication with two principals;
  owner-blind fake is rejected for exposing the other key's memory. Actual Cypher
  ownership is outside this offline gate. Library real ZIP export/import compares
  records, domain IDs and restored bytes against synthetic source state. Healthy
  control plus incomplete/altered state probes retained; fake method calls bind to
  actual store signatures after reviewer flagged possible interface drift.
- Chat's shared late-memory oracle accepts real `askQuestionStream` and rejects
  a shape-valid return-at-done mutant for missing `memory_update`; import errors
  cannot count as rejection. Real migrator fixtures expose legacy FK detach needs.
  Test runner uses explicit files rather than a shell-expanded glob.
- Skills controls originally mutated shared source then restored it; this was an
  invalid isolation method. Evidence was corrected and rerun in temporary copies
  with runtime sources clean. See skills `records/negative-control-recipes.md`.
- Source-assisted SDK batch collector passed the initial 24-test gate but delayed
  callbacks until EOF. This exposed a missing published streaming promise; the
  gate was expanded independently and the unchanged reference passed. **No
  replacement or reconstruction claim** follows from the earlier green trial.
- Docs gate review fixed method-blind endpoint checks, empty-200 mirror bypass,
  silent unavailable-network checks and unbounded fetches. Self-tests now serve
  every live target locally; a network-isolated run also passed. Static checks
  remain name/shape checks, not defaults, authorization or prose semantics.
- Upgrade prose review rejected overly broad "byte-exact whole graph", "tested
  pinned combination", count-only restore acceptance and unconditional rollback
  advice. The owning guide now specifies all-resource completeness and honest
  historical-vs-current evidence. No restore was executed.

## Instruction loading and adoption evidence

| Entry / harness | Delivery vs read | Evidence / scope |
|---|---|---|
| App root, current OpenCode lead | Initial harness supplied an `AGENTS.md` read rule; root `CLAUDE.md` and relevant guides explicitly read. New on-disk AGENTS adapter requires canonical root + scoped map. | Initial-context observation plus content check; not proof of every later session. |
| Chat root, fresh OpenCode | Ordinary title-maintenance question (no instruction file names); explicit root/guides/index reads, preserved title/auth/memory constraints and named gates | Sampled behavior; log does not establish which files arrived automatically. Earlier instruction-naming probe is only navigation evidence. |
| Skills root, fresh OpenCode | Harness delivered `AGENTS.md`; first explicit read `CLAUDE.md`, then local packet | Loader observation + unprompted checks-discovery task. One sample; no inference about Claude-first or subtree starts. |
| Claude Code starts / other entry paths | CLAUDE convention expected; ordinary links are inert unless read | Not observed in this campaign; working explicit-read fallback retained. |

Rubric for app follow-up probe: ordinary maintenance question without instruction
names; find canonical owner/state, preserve promises vs proposed delta, choose a
meaningful gate, protect user work/production, distinguish limited evidence and
place new knowledge with its owner. Root and relevant subtree must be tested
separately before claiming both entry paths work. No automatic-loading claim may
be inferred merely from an agent being explicitly told which files to read.

## Completion and remaining work

Verified change types: **instruction/navigation, documentation, evaluation and CI
improvements**. Runtime interfaces/state formats retained. Knowledge readiness:
selected scoped obligations evaluable; broader capabilities have explicit gaps in
the inventory. Highest local pipeline stage: 5 (evaluate); no rollout, operational
observation, reconstruction or production path retirement. Root instruction detail
was compacted into scoped guides without removing incident knowledge.

No measured maintenance-time saving claimed. Fresh navigation and new behavioral
gates exercised the expected benefit; the next real upgrade/feature change must
test end-to-end cost. Follow-ups are ordered in `.claude/regeneration.md`: current
whole-stack restore, Chat route/reload journeys, SDK CRLF/MCP state, calibrated
quality, then publication/schema drift. Existing bench heuristics/incident evidence
are retained but not a calibrated quality acceptance baseline for this candidate.

## Final verification

- Root `node --test scripts/*.test.mjs`: **34 passed**, no failures/skips;
  `node scripts/check-version-sync.mjs`: **Versions in sync**.
- Docs after final compatibility prose review: `npm run validate` and `npm test`
  passed (10/10); `npm ci && npm run build` passed on the integrated docs. Pagefind
  indexed 49 pages; generated API/root pages emit the existing missing-HTML warning.
  No dependency update performed in response to install audit output.
- Independent content/pointer review: all inspected relative links resolve and
  original root routing rows survive the move; no runtime compatibility change.
  Root instruction sizes are app 61, chat 40, skills 80 lines. No unconditional
  imports hide expanded content: app's explicit start additionally reads the
  architecture/map and affected guide; chat/skills similarly read on demand.
- **Fresh app-root delegated task** (`ses_f08532bc4ffeltgv3u0qVBg1RO`): asked if a
  chat client could stop at `done`, without naming instructions. It read canonical
  root/map/architecture, relevant frontend/backend sources and guides, found that
  visible rendering already finalizes there, rejected losing post-done memory,
  identified gates and owning knowledge. Read-only; no tests or edits claimed.
- **Fresh backend-directory OpenCode 1.18.34 task** (`ses_f08510d11ffeCXD9ncjm845uq2`,
  `litellm/local-agent`): asked whether a library ZIP suffices to roll back a future
  memory-format change in App and Chat. First explicit reads were root `CLAUDE.md`
  and `navigation.md`, followed by architecture/recovery/RAG/QA/index and actual
  export/session sources plus chat guides. Correctly identified ApiSession and
  SQLite/keys as outside the ZIP, distinguished rollback from restore, selected
  real commands and named live/quality gaps. No file edits or restore execution.
  This demonstrates sampled subtree **read/navigation behavior**, not loader
  injection or comprehensive instruction adherence. The task exposed overly broad
  "both under test/all gated" wording in chat guidance; narrowed to parser coverage
  vs the still-missing persistence journey. Also removed checkpoint-only backup
  advice from chat's owning state guide.
- Probe raw transcript is temporary harness output (session ID above); the tasks,
  observed decisions, explicit reads and limitations are retained here. Some probe
  recommendations exceeded the smallest necessary slice; only the owning contract
  and relevant gates govern a future change, not an automatic whole-stack rewrite.
- SDK callback control finalized: reference **25/25, exit 0**; batch candidate
  **24 pass / 1 assertion failure / 0 cancelled, exit 1**, message `onContent must
  fire before the source stream closes (EOF)`. Earlier runner cancellation was
  insufficient sensitivity evidence and is superseded (skills record preserves it).
- Final `git diff --check` passed in all three repos. Explicit diffs of app/backend
  and frontend runtime, schemas/deployment/backup files, chat runtime/migrations,
  SDK/MCP/site implementations were empty. Generated docs mirror was inspected
  after the final build: `/guides/versions` and revised recovery/artifact-identity
  text are present. The manifest below identifies the final source/test/doc patch;
  generated/ignored build outputs and temporary probe logs are excluded.

Required local slice checks are green. Live production journeys, current
whole-stack restore, mixed-version compatibility and calibrated quality remain
unestablished. Published docs still require the normal publication step.

**Subsequent checkpoint:** the quiesced storage portion of whole-stack restoration
was exercised in the next slice; see `qa/restore/RESULTS.md`. The manifest linked
above is now a historical adoption identity, not the current recovery candidate.
Online snapshots, boot/login and mixed-version recovery remain unestablished.

**Historical continuation checkpoint (2026-10-02):**
`chat-project-lifecycle-20261002-c`, after b's turn-bound UI slice. Project HTTP8/8,
actual Chromium23/23; combined Chat candidate HTTP59 + six positive callback gates,
Chromium33/33, suite132/132 and repo + three evaluation typechecks pass. New Chat
view/relay ownership fixes preserve server LWW, opaque memory and edit/regenerate;
schemas/migrations/dependency resolution unchanged. Fresh integrated gate IDs are
justified by changed runtime inputs; historical b/D/K receipts stay intact. Owning
record (QA-relative): `../../cortex-chat/docs/regeneration/records/2026-10-02-project-lifecycle.md`.
The original adoption and
manifest above remain historical identities, not the current runtime candidate.

**Current continuation checkpoint:** `chat-project-selection-20261002-a` closes
genuine held GET/relay replacement/token and terminal same-exchange navigation
regenerate coverage. Selection HTTP6/6/Chromium29/29; fresh integrated continuity
7/25, follow-up6/29, project8/23, ask HTTP59+six callbacks/browser33; suite132/132,
repo/six evaluation typechecks and docs validator/10 controls PASS. Two frozen
baseline rejections justify the page-only immutable-snapshot load repair. Failed
target-crash/dev-compile timeout attempts and all previous baselines retained.
Owning record: `../../cortex-chat/docs/regeneration/records/2026-10-02-project-selection.md`.
Next: terminal edit-last/different-exchange fallback under held compaction.
