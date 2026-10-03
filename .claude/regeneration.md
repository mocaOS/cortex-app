# Cortex maintenance / regeneration index

Mode: **translate**, playbook [`REGENERATIVE-SOFTWARE.md` v2.14.0](../REGENERATIVE-SOFTWARE.md).
Product scope: cortex-app + cortex-chat + cortex-skills, including the public docs
at `https://docs.cortex.eco/llms-full.txt`. Canonical session policy is each repo's
root `CLAUDE.md`; `AGENTS.md` is an explicit-read adapter. This index is knowledge
and checkpoint, not another policy owner. Companion paths below are relative to
the directory containing the three checkouts; each repo also works independently.

## Checkpoint and evidence

2026-10-01: compatibility-preserving instruction, documentation and evaluation
improvements across all three repos. No product runtime, migration, deployment
configuration, default, API schema or dependency resolution changes. The skills
lockfile's workspace version metadata now agrees with its existing package version.
No deployment/publication/commit performed. This is locally verified enabling
work, **not** a claim that the product has been reconstructed or is release-ready.

Follow-up: **quiesced storage restore + restored-state HTTP consumers demonstrated
locally** on 2026-10-01. Run `restored-consumers-20261001-k`: 20/20 stages, exit 0;
15 backend probes and 27 Chat checks passed on copies, original graph/all five
file roots unchanged, startup deltas strictly accepted, zero provider hits/egress
attempts, negative controls and cleanup passed. Input hashes still match at
close-out. [Restore receipt](../qa/restore/RESULTS.md) owns exact replay, attempts,
gate v2 correction and environment/claim limits. Browser send/reload, production
runtime/DNS parity, live-WAL and mixed-version recovery remain open.

2026-10-02 harvest: portable playbook v2.4.0 folds in consumer-on-copy recovery,
independently derived boot deltas, supported-boundary gate corrections, stable
resource identity, fail-closed multi-stage evidence and fresh-session handoffs.
Local operating rules are in `qa.md` and `upgrade-recovery.md`; execution history
stays behind this index.

2026-10-02 ask/memory checkpoint **`chat-ask-memory-20261002-d`**: real Next dev
HTTP evaluation passed (59 HTTP checks, one healthy callback control, two defect
confirmations), input drift/cleanup passed, contract suite 122/122 and typechecks
clean. Production runtime/schema/migrations/dependency resolution unchanged.
**Two page late-callback obligations fail** in source-extracted callback + HTTP
reproductions; browser remains not-run. Owning receipt/follow-up:
`cortex-chat/docs/regeneration/records/2026-10-02-ask-memory-journey.md`.
K was not repeated. Evaluation success is not acceptance of the page defects.

Playbook harvest v2.5.0: portable lessons now cover execution-versus-obligation
verdicts, callback/driver/UI evidence levels, asynchronous result ownership,
known-defect probe conversion, intentional teardown and actual receipt retention.
This documentation harvest does not change or rerun D's executed inputs.

Earlier **`chat-ui-turn-bound-20261002-b`**: explicitly authorized local Chat
`page.tsx` callback/persistence fix, after actual Chromium broken-baseline races.
**Browser 33/33; HTTP 59 + six positive extracted-callback gates; contract suite
129/129; repo + both evaluation typechecks PASS.** Rapid next-send, regenerate,
edit-last and chat-switch isolation now pass with healthy settled/legacy controls.
Schemas/migrations/dependency resolution unchanged; server LWW retained. Both b
runs cleanly retained evidence and removed owned scratch. K/D not replayed;
their history and D's broken baseline remain. Owning historical receipt:
`cortex-chat/docs/regeneration/records/2026-10-02-turn-bound-ui.md`.

Portable harvest **v2.6.0**: continuation/edit/navigation ownership, committed-state
observation, incidental constraint protection, all-context error gates, external
browser tooling and final receipt identities. Documentation-only harvest; b/D/K
runtime/evaluation inputs and historical verdicts were not changed or replayed.

Earlier **`chat-project-lifecycle-20261002-c`**: shared project-chat rebase/adoption,
overlapping streams and switch-away/back exercised on isolated SQLite/synthetic
users/held loopback SSE. **Project HTTP8/8; actual Chromium23/23 (gate v1.1).**
Frozen baseline gates exposed same-ID adoption, delayed navigation/recall, returning
own live-view/loading and relay registry ownership defects; smallest Chat-local
fixes accepted. Server atomic full-snapshot LWW, opaque memory, done-visible
completion and regenerate/edit retained; schemas/migrations/dependencies unchanged.
**Combined candidate HTTP59 + six callback gates, Chromium33/33, suite132/132 and
repo + three evaluation typechecks PASS.** Established gates executed under fresh
integrated IDs because runtime inputs changed; historical b/D/K receipts preserved.
Retrievable receipts, failed attempts, hashes and read-only integrated closeout:
`cortex-chat/docs/regeneration/records/2026-10-02-project-lifecycle.md`.

Portable harvest **v2.7.0**: stable identity versus fresh content/state, read-only
adoption/view/request ownership, ephemeral relay identity versus durable LWW,
actual feed/read-barrier prerequisites, repair-masking consumer paths, browser
process budgeting and precise evaluator/strengthened-gate verdicts. Documentation
only; c's executed runtime/evaluation inputs, receipts and verdicts remain intact.

Earlier **`chat-project-followup-20261002-b`**: remote-adopted regenerate/edit
local forks, immutable at-send memory, overlapping genuine adoption reads and
idle/live-completion disconnect/reconnect evaluated. Frozen baseline e rejects
missed settled adoption and ghost-stream completion; page-only `open` adoption /
`error` ephemeral-pair cleanup fixes accepted. **Follow-up HTTP6/6, Chromium29/29
(v1.2); integrated project HTTP8/8, Chromium23/23; ask HTTP59 + six callback
gates, Chromium33/33; suite132/132, repo + four evaluation typechecks, docs validator
and 10/10 controls PASS.** Full closeout and retrievable input
identities are owned by Chat `records/2026-10-02-project-followup.md`. c/b/D/K
historical evidence preserved; changed page justified fresh integrated IDs.

Earlier **`chat-project-continuity-20261002-a`**: still-live repeated reconnect
and remote append/same-exchange adoption during own held late-memory. Frozen
Chromium baseline25/22 rejected missing settled prefix and two incoherent feedback
pairs; page-only live-overlay/coherent late-selection repair passes **continuity
HTTP7/7 and Chromium25/25**. Fresh integrated gates after changed page: follow-up
HTTP6/6/browser29/29, project HTTP8/8/browser23/23, ask HTTP59+six callbacks and
Chromium33/33, suite132/132, repo + five evaluation typechecks, docs validation
and 10/10 controls PASS. Final hashes and retrievable evidence:
Chat `docs/regeneration/records/2026-10-02-project-continuity.md`.

Portable harvest **v2.8.0**: genuine transport failure versus offline emulation,
reconnect versus catch-up, live overlays with fresh settled state, coherent
history/recall/immutable-snapshot selection under LWW, auxiliary-write commit
observations, adapter field fidelity and executed gate-byte retention. Documentation
only; continuity/follow-up/c/b/D/K runtime/evaluator inputs, receipts and verdicts
remain intact. At that harvest, continuation pointers used v2.8.0; historical execution
records retain their actual v2.7.0 basis.

Earlier **`chat-project-selection-20261002-a`**: genuine held adoption GETs across
relay replacement/token arrival and terminal sidebar-away/browser-back with held
compaction pass **selection HTTP6/6, Chromium29/29 v1**. Frozen baseline29/27
rejects two lost terminal at-send snapshots with all healthy controls passing.
Page-only same-exchange load repair preserves local immutable snapshots; loaded
history/recall and server LWW remain intact. Fresh integrated continuity7/25,
follow-up6/29, project8/23, ask HTTP59+six callbacks and Chromium33/33 PASS;
suite132/132, repo + six evaluation typechecks and docs validation/10 controls PASS.
One target crash and one measured30.8s Next dev event-route compile/reopen timeout
remain failed attempts; separate fresh gates pass without widening bounds.
Final input/receipt verification checks10 accepted runs and225 evidence-file digests,
plus prior continuity144/follow-up109/c87 files; diagnostics preserved. Exact
commands, launcher adaptations, hashes and next action:
Chat `docs/regeneration/records/2026-10-02-project-selection.md`.

Portable harvest **v2.9.0** generalizes selection's terminal-navigation snapshot
retention, held-read/latest-operation progress gates, phase-specific timeout
diagnosis, budget/diagnostic adaptations, selectively resumed integrated stages
and process-background network limits. At that harvest, continuation pointers used v2.9.0;
selection retains its executed v2.8.0 basis. Documentation-only harvest; all
executed source/evaluator/receipt bytes and verdicts remain intact.

Earlier **`chat-project-terminal-20261002-d` — blocked / not accepted**: terminal
edit-last after return and different/fresh-page edit/regenerate fallback exercised
with compaction held. **HTTP6/6 PASS; Chromium a/b/c/d43/47,20/22,45/47,45/47,
all exit1.** c/d direct consumer/value/healthy controls pass; remaining fresh feedback
targets race genuine post-completion adoption. Gate corrections preserve attempts;
final **v1.3 frozen/typechecked, browser not-run** due unchanged2GiB capacity floor.
No intended product-value assertion rejected; Chat/App/SDK/MCP runtime unchanged.
At d, Chat suite122/132 (10 capacity setup/dependent failures), repo + terminal
types, docs validator/10 controls and whitespace pass. This is partial evaluation/
knowledge work, not a verified product slice. Root~1.9GiB free, `/tmp`/swap full;
new owned graph duplicates reclaimed only after receipt/identity checks, generated
cache diagnostics losslessly re-retained. Earlier225 files/10 selected runtime
manifests match read-only; historical gates not replayed. Exact evidence/retrieval/
next command: Chat `docs/regeneration/records/2026-10-02-project-terminal.md`.

Earlier **`chat-project-terminal-20261002-f`**: user-expanded capacity verified;
terminal **HTTP6/6, Chromium47/47 v1.4, suite132/132, repo/terminal types and docs
validator/10 controls PASS**. v1.3 e executes47/42: PATCH fallthrough consumes its
Playwright GET-barrier quota. Terminal-local method/document-filtered adapter
corrected and frozen separately; latest/older real reads, exact feedback and all
direct edit/different/fresh fallback controls pass. **No product runtime fix**;
selection's source/schema/dependency inputs remain unchanged. d/a..e failure
receipts, diagnostics, archives and all historical evidence preserved. Expanded
root~90GiB free/RAM~13GiB available/swap free; successful scratch removed after
receipts, shared/unknown-owner resources untouched. New integrated evidence:
`output/chat-project-terminal-resume-20261002/`; owning record above. Verified
evaluation/knowledge slice, not reconstruction, model quality or production parity.

Portable harvest **v2.10.0** generalizes terminal's operation-filtered interception
quotas, stabilized consumer selection, adapter-specific read schedules, independent
instance supersession, lossless diagnostic re-retention and bounded capacity recovery.
Current pointers use v2.10.0; f retains its executed v2.9.0 basis. Documentation-only:
runtime/evaluator/receipt bytes and verdicts remain intact. Harvest validation:
`output/chat-project-terminal-resume-20261002/playbook-harvest-verification.json`.

Earlier **`chat-ask-shutdown-20261002-a`**, translate/v2.10.0: frozen genuine partial
shutdown/reset/two-resubmits/exhaustion, stable IDs, exact history/opaque memory,
direct consumers/immutable snapshots and settled/legacy controls pass **HTTP7/Chromium31**.
Suite136/types/docs10 controls pass; no runtime fix. Historical inputs/diagnostics
verified read-only; successful scratch removed after retention. Exact evidence:
`output/chat-ask-shutdown-20261002/`, Chat `records/2026-10-02-ask-shutdown.md`.

Earlier **`chat-project-sharing-20261002-b`**: owner grant/revoke/regain passes HTTP11/
Chromium25, suite138/types/docs10 controls. No runtime fix; connect-time feed admission
limitation documented. Failed adapter/no-op feedback evidence and exact input reuse
preserved: `output/chat-project-sharing-20261002/`, Chat `records/2026-10-02-project-sharing.md`.

Earlier **`chat-project-move-20261002-d`**, translate/v2.10.0: move HTTP8/Chromium23
v1.3 and18 combined stages/suite142/types/docs10 controls pass after page/client
success/live-ref repair. Raw state/immutable snapshots/LWW preserved; failed gates
and diagnostics retained. Owning Chat `records/2026-10-02-project-move.md`, App
`output/chat-project-move-20261002/`. Portable v2.11.0 harvest changes guidance only;
d's execution remains intact, with separate `playbook-harvest-verification.json`.

Earlier **`chat-project-delete-20261003-c`**, translate/v2.11.0: delete HTTP9/Chromium21
v1.1 and20 combined stages/suite144 pass after page-only current-context acknowledgment
repair. Exact author-state/late-memory/direct consumers and all failures retained:
App `output/chat-project-delete-20261003/`, Chat `records/2026-10-03-project-delete.md`.

Earlier **`chat-project-delete-rejection-20261003-b`**, translate/v2.11.0: frozen
**Chromium23 PASS** after a23/21 rejects direct edit/regenerate context following
genuine pre-dispatch DELETE failure. Page-only rejected-delete early return;21 fresh
combined stages/suite147/types/docs10 controls and Skills lint/unchanged manifest pass.
Prior gates/state/LWW/snapshots/evidence preserved; root~81GiB, unknown owners untouched.
App `output/chat-project-delete-rejection-20261003/`, owning Chat
`records/2026-10-03-project-delete-rejection.md`; no CAS/schema/migration/dependency,
commit/deploy/publication/live/paid changes; public source labels patch unreleased. b retains its executed v2.11.0 basis; [v2.12.0 documentation-harvest audit](../output/chat-project-delete-rejection-20261003/playbook-harvest-verification.json).

Earlier **`chat-project-remote-delete-20261003-b`**, translate/v2.12.0: frozen
**remote HTTP10/Chromium31 v1.1 PASS** after browser a31/29 rejects stale selected
project context in member-author direct regenerate/edit. Existing flat-list positive
author/association evidence clears context only; exact detach/late compaction/local
snapshots/LWW retained. Non-author fresh read/save/feed404 is separate from admitted
ask/relay completion and open-feed post-detach delivery; actual late browser save404
leaves storage unchanged. HTTP a's optional-event-kind adapter error and both failed
scratchs retained. **23 fresh combined stages, suite147/types/docs10 controls and
Skills lint/unchanged manifest PASS**; root~79GiB, unknown owners untouched. Exact
receipts/inputs: `output/chat-project-remote-delete-20261003/`, owning Chat
`records/2026-10-03-project-remote-delete.md`. No CAS/schema/migration/dependency,
commit/deploy/publication/live/paid changes. Current public source labels patch unreleased.

Earlier **`chat-project-association-list-20261003-c`**, translate/v2.12.0: frozen
**Chromium27 v1.1 PASS** after b27/25 rejects old personal-list context clearing
across acknowledged move/newer refresh and navigation with no newer list. Page-only
request-generation plus dispatch-view/selection guards; exact state/late compaction/
immutable consumers/LWW preserved. Unsupported memory-only notification trigger a
and both failed scratches retained. **24 fresh combined stages, suite147/types/
docs10 controls, Skills lint/unchanged manifest PASS**; root~77GiB. Owning Chat
`records/2026-10-03-project-association-list.md`, App
`output/chat-project-association-list-20261003/`; no CAS/schema/migration/dependency,
commit/deploy/publication/live/paid changes. Old gates/evidence unchanged.

Earlier **`chat-project-delete-response-loss-20261003-a`**, translate/v2.12.0:
frozen **Chromium26 v1 PASS on unchanged runtime**. Genuine DELETE200/committed
detach precedes lost delivery/requestfailed/no browser ack; supported lists reconcile
personal context, valid late memory/direct immutable edit/regenerate/no automatic
DELETE replay pass. Actual pre-dispatch/cancel/acknowledged-success controls distinguish
phases. One fresh journey integrates association's24 accepted journeys/suite147 only
after whole-input reconciliation; none relabeled as fresh. Types/docs10 controls/
Skills lint/unchanged manifest PASS, root~77GiB. Owning Chat
`records/2026-10-03-project-delete-response-loss.md`, App
`output/chat-project-delete-response-loss-20261003/`. No runtime fix/CAS/schema/
migration/dependency/commit/deploy/publication/live/paid changes; old evidence retained.

Earlier **`chat-project-move-overlap-20261003-c`**, translate/v2.12.0: frozen
**Chromium21 v1.1 PASS** after b21/19 rejects two stale A-context consumers despite
committed B. Page-only positive current project-list association under existing
request/view/session guards replaces ack-target rebinding; LWW/accepted effects/
snapshots preserved. a's overlay failure and both scratchs/gates retained. Delegated
evaluator + independent read-only reviewer accept combined evidence. **26 fresh
journeys, suite147/types/docs10 controls/Skills lint+unchanged manifest PASS**;
root~75GiB. Owning Chat `records/2026-10-03-project-move-overlap.md`, App
`output/chat-project-move-overlap-20261003/`; no new GET/ref/CAS/schema/migration/
dependency/commit/deploy/publication/live/paid change. Reverse commit order unrun.

Portable harvest **v2.13.0** generalizes this session's positive association/read-
versus-view ownership, commit/ack order discrimination, admitted-operation versus
fresh-save authority, actual post-commit delivery loss/reconciliation, branch-specific
notifications, barrier modes/UI overlays and corrected independent-review claims.
Current continuation pointers usev2.13.0; c and the preceding slices retain their
executedv2.12.0 basis, all runtime/gate bytes, original26 results and failures.
Separate documentation-only audit (no journey replay):
`output/chat-project-move-overlap-20261003/playbook-harvest-verification.json`.

Current **`chat-project-move-reverse-20261003-b`**, translate/v2.13.0: frozen
**Chromium21 v1.1 PASS on unchanged product**. Native A is held before forwarding;
exact unchanged state/no response/no move acknowledgment precedes B's real commit200
and browser200, then A forwards/commits/acks last. Observed commits B→A, final
storage/direct edit/regenerate context A, distinct instructions, valid held compaction
and immutable memory/prefix/personality association pass with healthy controls.
Both moves remain accepted under unchanged LWW. v1 a12/8 is an evaluator no-ack
predicate counting the earlier origin-history save; original gate/receipt/failed
scratch `chat-journey-990zdZ` retained, including dependent teardown failures and
post-abort A dispatch. No product-value rejection, no runtime fix. Independent
read-only review accepts b; **one fresh journey +26 explicitly inherited c runs**
reconciled by full source/evaluator/installed-lock/tool/evidence identities. Fresh
suite147/types/docs10 controls/Skills lint+unchanged manifest PASS at closeout.
Owning Chat `records/2026-10-03-project-move-reverse.md`, App
`output/chat-project-move-reverse-20261003/`; c's executed v2.12.0/original receipts
and all earlier failures remain immutable. No schema/migration/dependency/CAS,
commit/deploy/publication/live-store/paid-call change.

2026-10-03 **`release-readiness-20261003` — verdict READY, conditional**: the
accumulated three-repo work is evidence-backed for commit and a **source-built**
production push (user-selected path; push = Dokploy/Coolify rebuild).
Production-actual Chat image smoke gate v1.2 run c **63/63 exit0** (17 prod +
21 move + 19 delete + runtime/selection/post), image `be922861` Node20-Alpine,
219/219 source-binding; Chat suite147+types in Node20-Alpine git container;
backend slim image build + ruff + pytest1402/22skip with frozen public corpus;
frontend tsc/lint/build + prod image; docs/restore/version-sync/Skills lint+build/
SDK25/MCP6 pass on hash-verified copies. Run a loader failure and run b
Secure-cookie observer failure (evaluator artifact, not product defect) retained
and diagnosed; independent reviews accepted the delta and run c. Remaining
pre-push gates: source-default telemetry-variant boot smoke (owned Sentry sink)
and Chat CI `fetch-depth: 0`. No commit/push/deploy performed — authorization
pending. Verdict/runbook: `output/release-readiness-20261003/VERDICT.md` and
`HANDOFF-source-built.md`; fresh-session packet: `qa/NEXT_SESSION.md`.

- [Campaign receipt](../qa/REGENERATIVE_ADOPTION.md): baselines, input identities,
  integrated verification, instruction probes, decisions and limitations.
- [Backend obligation ledger](../qa/QA_CONTRACT_RECORDS.md): per-key session/MCP
  journeys and library-transfer record/identity/byte checks; negative controls.
- Chat: `cortex-chat/docs/regeneration/index.md`, with detailed adoption record.
- Skills: `cortex-skills/docs/regeneration/index.md`, with records and isolated
  negative-control recipes. A trial collector exposed a streaming-callback gate
  gap; it is **inconclusive replacement evidence**, not an adopted replacement.
- [Upgrade/recovery](upgrade-recovery.md): state inventory, actual backup coverage,
  compatibility gaps and the required disposable whole-stack restore procedure.

## Product journeys and contract owners

| Journey / invariant | Owner and consumers | Surviving knowledge / gate |
|---|---|---|
| Ingest → process → index → search → source bytes | App owns Neo4j + uploads/custom inputs; helper, LLM and crawl services are external dependencies | `domain/document-pipeline.md`, `domain/entities.md`, `domain/relationships.md`, backend document/search suites; live E2E skips are not pipeline evidence |
| Ask → streamed answer → late memory → next turn | App owns meaning of opaque memory/SSE; chat owns history/persistence; SDK/MCP carry state | `domain/rag-pipeline.md`, `docs/cortex-chat-integration.md`; chat `tests/streaming-contract.test.ts`; SDK `sdk/test/contract.test.ts` |
| Session privacy / MCP → REST principal | App owns key auth, collection scope, ApiSession; instance MCP forwards caller authority | `qa/QA_CONTRACT_RECORDS.md` INV-SESS-001..003, INV-MCP-001..002; actual Cypher ownership still requires live coverage |
| Browser login → group key → scoped chat | Chat owns SQLite users/groups/sessions, encrypted backend keys, route authorization | chat scoped auth/integration guides; restored HTTP login/history/assets, actual selected ask/project Chromium journeys; production/SSO/demo and broader auth lifecycle remain open |
| Agent tool → SDK/stdio MCP → API | Skills owns SDK/tool surface and **file-backed** MCP thread state; app supplies REST/SSE | skills local contract map, SDK + real-stdio/loopback MCP tests; instance `/mcp` is a different implementation |
| Library move / whole-stack recovery | App curated ZIP differs from APOC + file backups; Chat has separate state and keys | `upgrade-recovery.md`, `domain/admin-features.md`; library gate INV-LIB-001..005 is not disaster-restore evidence |
| Install/update → independently released consumers | App stack pins producer + chat/Neo4j/Caddy; installer is external | `development.md`, `selfhost/README.md`, `scripts/check-version-sync.mjs`; version checks do not establish rolling/downgrade support |

Preserve both SSE orderings (`done`→`memory_update` and legacy reverse), opaque
state and stable source `sid`. Deep research uses streaming; legacy depth flags
remain supported. Chat's relay `session_id` is not an app ApiSession: both chat
ask proxies strip their local IDs before forwarding. See the owners above rather
than copying schema/semantics here. Library and state recovery must retain known
producer/model identity; equal vector dimensions do not establish compatibility.

## Gates and environments

| Working directory | Command / source | Evidence scope |
|---|---|---|
| `cortex-app/backend` | `.qa-venv/bin/python -m pytest -q`; setup + lint in `qa.md` | Offline behavior, mocks explicitly scoped; live skips reported separately |
| `cortex-app` | `node --test scripts/*.test.mjs`; `node scripts/check-version-sync.mjs` | Stack/version tooling; no deploy |
| `cortex-app/documentation` | `npm run validate`; `npm test` | Dependency-free name/shape docs gate + hermetic controls |
| `cortex-app/documentation` | `npm ci`; `npm run build`; after publication `npm run validate:live` | Build/LLM-mirror structure; publication is separate authority |
| `cortex-chat` | `npm ci`; `npm run typecheck`; `npm test` | Real client/crypto/temp SQLite contracts; no browser or upstream integration |
| `cortex-skills` | `npm ci`; `npm run lint`; `npm run manifest`; `npm run build` | Site/manifest; compare consecutive output bytes if working tree intentionally dirty |
| `cortex-skills/sdk`, `cortex-skills/mcp-server` | `npm test` in each | SDK fake-fetch/loopback; real MCP subprocess against fixture backend |

Use the QA interpreter available locally; `.qa-venv` is a recipe, not a committed
environment. Check pytest's **temporary-upload filesystem** space, not just `/`.
All contract additions fit existing runners; no new evaluation platform. Model
quality/provider runs remain separate from hard isolation/state/tool invariants.
Internal `bench/` has existing heuristics/A-B procedures; neither those historical
notes nor current offline tests establish calibrated quality for this revision.

## Capability inventory (disposition is not rewrite readiness)

| Capability | Disposition | Evidence and consequential remaining gap |
|---|---|---|
| Agent instruction delivery/navigation, all repos | improved | concise operating contracts, scoped owners and sampled fresh-session evidence; other harness/subtree paths unobserved |
| Public docs / handbook / LLM mirror | improved | source-backed corrections, build + validator; published site still lacks candidate updates |
| App auth / sessions / instance MCP | improved | real principal through HTTP + SSE tests; fake graph means Cypher isolation unverified live |
| Chat streaming / opaque memory / retries | improved (selected actual UI + HTTP) | accepted turn/project/selection/terminal gates retained; ask partial shutdown/reset/resubmit/exhaustion HTTP7/Chromium31 and suite136 pass; post-done shutdown and production parity remain open |
| SDK + standalone MCP | improved | public consumer and stdio gates; LF coverage, CRLF parser gap recorded; MCP persisted-thread restart not exercised |
| Library transfer / state recovery | improved | ZIP gate + ownership/backup matrix + real quiesced restore/consumer controls; HTTP boot/login/read and original-state isolation passed; browser/online/old-version recovery and extra relationship-property fidelity remain open |
| Chat crypto / SQLite migrations | improved | actual crypto/migrator, historical FK behavior and storage restore; old rows through intermediate migrations and live-WAL recovery not covered |
| Ingestion / extraction / graph / dedup / communities | adequate-for-current-needs | existing offline suites and domain guides retained, full suite green; real pipeline/model quality not revalidated |
| Search / researcher / writer / prompt guards | adequate-for-current-needs | existing deterministic scope/citation/tool checks retained; calibrated retrieval/answer quality deferred pending corpus/provider baseline |
| Backend skills execution / HTTP egress / git / crawl / webhooks | adequate-for-current-needs | existing suites/guides retained; external side effects and failure/replay integration not observed |
| Hosted apps / x402 / quotas | adequate-for-current-needs | existing scoped guides and deterministic suites retained; real payment/facilitator/app-state recovery not exercised |
| Admin frontend / graph browsers | adequate-for-current-needs | instructions retained, no UI change; browser journeys not rerun |
| Chat auth/SSO/reset/demo/proxy authorization | improved (ask + selected reads/relay/UI) | real Next ask auth/scope/header/allowlist/strip checks, selected relay feeds and restored reads; actual synthetic-user Chromium logins; demo/reset/SSO and broader browser lifecycle remain open |
| Chat projects/realtime/personalities/voice/upload UI | improved (selected project-chat/share/move/delete); others deferred-with-reason | overlap/reverse Chromium21 each +25 established stages retained with unchanged-input inheritance explicit; delayed positive project-list orderings, other uncertain outcomes/unavailable reconciliation, instantaneous open-feed revocation, personalities/voice/upload lifecycle remain open |
| Skill content / manifest/site | improved | navigation repaired, built and manifest regenerated; deeper field/permission audit beyond path checks still needed |
| Release / backup / observability / provider upgrades | improved | version tool gate + recovery matrix + quiesced storage rehearsal; no mixed-version/online restore or production observation; external helper/installer outside write scope |

## Ordered next slices

0. **Release closeout (supersedes until done):** run the source-default telemetry
   boot smoke, fix Chat CI fetch-depth, then commit/push per
   `output/release-readiness-20261003/HANDOFF-source-built.md` with explicit
   authorization. Packet: `qa/NEXT_SESSION.md`.
1. **Delayed positive project-list ordering:** after B commits/acks while A remains
   pre-forward-held, capture an actual project-list response containing B. Release
   A to commit, capture/deliver newer positive A lists, then deliver older B last.
   Freeze direct edit/regenerate context A/full retained state/late-memory/immutable
   no-chat-GET consumers and healthy controls under fresh IDs/version. Require actual
   request/view ownership; preserve both accepted move outcomes/LWW and both executed
   overlap/reverse schedules. No latest-gesture/CAS rule or product fix before rejection.
2. **Recovery fidelity follow-up:** storage + HTTP consumer gate completed
   (`qa/restore/RESULTS.md`). Small replay/receipt presentation fixes are recorded
   there; do not rerun K without changed relevant inputs or a new claim. Separately
   identify CI/production runtime pairs, a healthy DNS-capable engine, live-WAL
   snapshot controls, missing deployment backup mounts and supported upgrade pairs.
   Shared Podman is broken: never migrate/reset/prune it; use only run-owned
   private resources. Check actual temp/storage capacity before installs.
3. **SDK/MCP protocol evolution:** preserve callback timing, add CRLF semantics with
   explicit acceptance delta and baseline defect case; reconcile MCP advertised
   package version and add file-thread restart/interop coverage. No runtime fix was
   silently folded into this compatibility-preserving campaign.
4. **Calibrated agentic quality:** select sanitized corpus and held-out tasks,
   retrieve historical bench inputs where available, pin prompts/models/tools,
   agree margins and judge calibration *before* comparison; use an authorized
   disposable provider environment. Success includes isolation, grounded citations,
   task completion, cost/latency and failure/abstention strata, not one average.
5. **Publication and drift:** release docs through the normal path, run live mirror
   checks; resolve static OpenAPI `1.0.0` vs runtime contract `2.0.0` with schema
   comparison (metadata agreement alone cannot prove the schemas match). Audit
   high-traffic skills' fields/permissions against the selected backend revision.

Expected benefit: follow-ups use named owners/reusable gates; navigation, sensitivity and fixture reuse were exercised. Total change-cost savings remain a hypothesis.

Reconcile the [fresh-session work package](../qa/NEXT_SESSION.md), this checkpoint and current work before execution.

## Source-built release closeout addendum — 2026-10-03

Pre-push configuration gate closed: fresh Chat source-default evaluator v1.1 run
`chat-source-default-20261003-b` **13/13 exit0**, reporting enabled with owned
server/browser telemetry sink, source-binding219, clean tripwire, independent
ACCEPT. Chat npm-test CI checkout now sets `fetch-depth: 0`. Failed run a's
invalid synthetic DSN and all previous receipts/resources remain retained;
frozen production-v1.2 closure11 unchanged. Entire previous dirty-document bytes
are preserved beneath append-only continuation pointers. Capacity~38.7GiB,
2GiB floor. Full evidence, setup diagnosis and proposed commit composition:
`output/release-readiness-20261003/CLOSEOUT-source-built-20261003.md`.

Next: obtain explicit commit/backup/push/deploy authority and target/branch/access
references, then execute original Chat→App→Skills runbook. Include complete
referenced `qa/restore/` package; exclude `qa/release/`. Supported Chat config
smoke surface is `/api/config` (+ `/api/auth/me` identity), correcting the old
`/api/auth/config` runbook typo. No commit/backup/push/production deploy/live-store
access performed; rollback refs and subsequent campaign backlog unchanged.

## Authorized publication and portable harvest — 2026-10-03

User authorized the technical changelog, playbook harvest and commit/push of all
three repos to `main`, including complete referenced `qa/restore/` and excluding
`qa/release/`. User clarified that backups are covered by separate routines and
**no instances auto-deploy on push**; the earlier push-as-production assumption
does not apply to these targets. Git publication is not deployment evidence.

Portable **v2.14.0** harvests exact build-variant/runtime prerequisites, supported
endpoint and filtered-observer checks, valid synthetic reporting configuration,
owned telemetry positive controls, inherited fixture labels, frozen failed-run
retention and complete atomic source composition. Historical v2.13.0 and earlier
execution bases/receipts remain unchanged. The Oct3 App docs changelog describes
technical changes across the campaign without attribution or deployment claims.
Fresh documentation gates and publication receipts live under
`output/release-readiness-20261003/authorized-publication-20261003/` when present.
After Git publication, the delayed-positive-list slice resumes; production
HTTPS smoke requires an actual identified deployment of these revisions.
