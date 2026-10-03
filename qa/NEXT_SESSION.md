# Fresh-session kickoff — release closeout (source-built path)

Continuation packet, not policy. Current checkpoint
**`release-readiness-20261003` — verdict READY, conditional on one pre-push gate
plus explicit authorization**. Owning verdict/evidence:
`output/release-readiness-20261003/VERDICT.md`; deploy runbook:
`output/release-readiness-20261003/HANDOFF-source-built.md`; adapter review:
`output/release-readiness-20261003/production-gate-review.md`; setup/failure
diagnoses: `output/release-readiness-20261003/setup-diagnoses.md`.

Selected deployment path: **source-built** (existing Dokploy/Coolify rebuild from
repository source). On this path a push to the tracked deploy branch is a
production rebuild; App docs changes publish when docs.cortex.eco rebuilds.

```text
Close out the three-repo release (cortex-app base bd6c1e0, cortex-chat base
40fe797 = tag v1.3.0, cortex-skills base 1156ade). All work is uncommitted in
dirty trees — preserve every byte; no git mutations without explicit user
authorization. Read VERDICT.md, HANDOFF-source-built.md, production-gate-review.md
and setup-diagnoses.md in output/release-readiness-20261003/ first.

Accepted evidence: production-actual Chat image smoke gate v1.2 run c 63/63 exit0
(17 prod + 21 move + 19 delete + runtime.no-unhandled-rejections + selection +
4 post), image be922861 linux/amd64 Node20.20.2 Alpine musl euid1001, 219/219
source-binding match, dual-mode Secure-cookie attribution observed, zero browser
injection, tripwire clean, container removed only after receipts. Chat suite
147/147 + typecheck in Node20-Alpine git container; backend slim production image
build + ruff + pytest 1402/22skip against frozen public15-case corpus; frontend
tsc/lint/build + Dockerfile.prod image; app script tests, version-sync, restore
oracle/shell, docs validate/controls/build, Skills lint/build, SDK25, MCP6 all
pass on hash-verified owned copies. Independent read-only reviews accepted the
Chat runtime delta, the App/Skills no-runtime-delta verdict and run c.

Remaining pre-push gate (Chat only): build the same Dockerfile WITHOUT
NEXT_PUBLIC_SENTRY_DISABLED (source-default: GlitchTip reporting enabled,
baked-in DSN, pre-existing released behavior), boot under the owned private
engine with the journey fixture and SENTRY_DSN pointed at an owned local sink,
verify boot/readiness/login + clean tripwire, retain receipts, fresh run ID. Do
NOT edit the frozen v1.2 closure (11 inputs incl. campaign.mjs) — separate
bounded step. Fix Chat CI fetch-depth:0 on the npm-test checkout before push
(provenance tests need historical blobs). Then commit per HANDOFF composition
(Chat runtime+runner+harness+docs, App CI unit + docs unit + QA ledgers + guides
+ output/.gitignore, EXCLUDE qa/release/, Skills test+site+manifest units),
order Chat → App → Skills, back up Chat data volume + App per ops/backup, push
with authorization, post-deploy HTTPS smoke (login/ask/reload/memory/move/
delete//api/auth/config), rollback = redeploy bd6c1e0/40fe797/1156ade (no
migrations in delta).

Known non-claims: GHCR-parity telemetry-disabled build only; no real backend/LLM,
full-ML image, arm64, App backend+frontend together, real HTTPS deploy env, live
stores, multi-replica, GHCR publication. Pre-existing compose gap: cortex-chat
docker-compose.yml documents SENTRY_DISABLED opt-out but never declares it.
Secure session cookie in production requires HTTPS (loopback excepted) —
evaluator artifact in run b, not a product defect.

Retention: failed run-b container cortex-chat-prod-chat-production-20261003-b
(stopped), suite containers cortex-chat-suite-b-20261003 /
cortex-backend-suite-b-20261003 (Exited 0), scratch chat-journey-qqRitL, all
images under /var/tmp/cortex-qa-tmp/release-readiness-20261003/engine-root (vfs),
failed python-venv, every command/log/receipt pair. Capacity 43G free, 2GiB
floor. Shared/unknown-owner resources untouched
(cortex-isolated-engine/iso-20261001-pid77180, other /var/tmp/cortex-qa-tmp/*).

After release closeout, campaign backlog resumes unchanged: delayed positive
project-list ordering gate (hold genuine B list response, release A, deliver
newer A then older B last, fresh IDs/version), plus .claude/regeneration.md
ordered slices 2–5. No product fix without frozen positive-value rejection; no
schema/migration/dependency change; preserve failed receipts/diagnostics; fresh
gate version/run IDs per rerun; independent read-only review; digests not mtimes.
```

## Continuation addendum — 2026-10-03, pre-push gates closed

Read `output/release-readiness-20261003/CLOSEOUT-source-built-20261003.md`
alongside the original verdict/handoff. Source-default Chat image built without
`NEXT_PUBLIC_SENTRY_DISABLED`; fresh evaluator v1.1 run
`chat-source-default-20261003-b` **13/13, exit 0**, owned server/browser telemetry
sink, clean tripwire, independent ACCEPT. Run a's invalid synthetic DSN remains
failed/retained. Chat CI npm-test checkout now has `fetch-depth: 0`.
Frozen v1.2 closure unchanged (11/11); old receipts/resources/dirty work preserved.

**Next: ask for explicit commit/backup/push/deploy authorization** and confirm
branches plus target/access/backup/smoke-account references. Proposed composition
includes the complete existing `qa/restore/` package; exclude `qa/release/`.
**Endpoint correction:** HTTPS smoke uses Chat `/api/config` and `/api/auth/me`;
the earlier `/api/auth/config` spelling has no supported route. No commit,
backup, push, production deploy or live-store access has occurred. Capacity
approximately 38.7 GiB free, floor still 2 GiB. Rollback/order/backlog unchanged.

## Authorized Git publication — 2026-10-03

User authorized completing the technical App docs changelog, harvesting portable
playbook v2.14.0, then committing/pushing **all three repos to `main`**. Include
the complete referenced `qa/restore/` package; exclude `qa/release/` and ignored
output except `output/.gitignore`. Backups are covered by separate user routines;
**no instances auto-deploy on push**, superseding the earlier target assumption.
Publication is not a production deployment or HTTPS-smoke claim. New checks and
commit/push receipts:
`output/release-readiness-20261003/authorized-publication-20261003/` when present.
Preserve original v1.2 inputs, failed receipts, retained containers and 2GiB floor.
After publication, resume the delayed-positive-list/campaign backlog; verify
HTTPS only against an identified deployment actually updated to these revisions.
