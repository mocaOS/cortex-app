# Restore and consumer rehearsal receipts — 2026-10-01

## Historical storage-only result

**Passed: local, quiesced multi-resource storage restoration.** The actual
`ops/backup/backup.sh` and `restore.sh` executed against two real disposable
Neo4j/APOC stores and fresh file volumes. The completeness gate accepted the
healthy copy and rejected a valid-checksum archive missing a Chat avatar.
No production data, deployment, datastore, API/provider calls or commits involved.

Run of record: **`restore-final-20261001`**, 14:37:49–14:40:19 UTC, 18/18 stages
successful. Working inputs were the original app `bd6c1e0` and Chat `40fe797`
revisions plus the uncommitted adoption/fixture patches. Existing work retained.
Skills source was not modified by this slice.

Why: a green backup job and a library ZIP cannot certify recovery of separately
owned graph, document bytes, Chat keys/history/assets and app SQLite. This gate
makes a likely next backup/format change independently checkable rather than
requiring rediscovery of every volume owner. Reduced future-change cost is still
a hypothesis; a subsequent real upgrade must exercise it.

## Verification and oracles

| Obligation (v1) | Observation / accepted result |
|---|---|
| RESTORE-001: preserve frozen graph/domain identities, properties and relationships | 19 representative nodes, 12 relationships; zero exact graph diffs; duplicate IDs and relationship multiplicity checked, no internal Neo4j IDs used |
| RESTORE-002: preserve all owned fixture file resources and SQLite rows | Exact per-file SHA-256 across uploads/custom_inputs/chat/skills/apps; app KV and actual Chat migration-schema rows equal; Chat encrypted keys decrypt, password digests verify, opaque memory/messages and avatar/branding bytes retained |
| RESTORE-003: restore schema on an existing target | Source and target constraint/fulltext/vector definitions agree. Actual replay drops/recreates ordinary schema; pre-created vector index deliberately retained. Source fulltext result 2 = restored result 2 |
| RESTORE-004: retain the original snapshot point | Source-only post-T Document/blob, usage bump and Chat message/memory/usage/avatar canaries absent from healthy target. Mutated Chat source demonstrably rejected by its fixed oracle |
| RESTORE-005: detect complete-looking but incomplete recovery | Recomputed-valid checksum archive restored with exit 0; oracle rejected **exactly** missing `chat/avatars/fx-user-0001.png`, whose expected SHA-256 is `c414cd0e204de974f73753c7e28d7638e7b3691bb8b1a2bab6b25bb7fed7ce77` |
| RESTORE-006: invalid archive refusal precedes destruction | No-`.complete` and bad-checksum runs exited 1 with their specific diagnostics. Exact graph captures unchanged before/after; counts only advisory |
| RESTORE-007: captures must not ignore SQLite journals | Real active-WAL fixture rejected before immutable capture; closed/checkpointed fixture accepted with committed row intact. Applies to both app and Chat stores |

Exact final commands:

```bash
# From cortex-app (requires cached Neo4j/alpine images + Chat dependencies):
python3 qa/restore/oracle.py selftest
bash -n qa/restore/rehearsal.sh
bash -n ops/backup/restore.sh
qa/restore/rehearsal.sh --run-id restore-final-20261001
# exit 0; 18 stages including verified cleanup

# From cortex-chat (specialist verification):
npm test             # 71 passed, 0 failed/skipped; 11 new fixture controls
npm run typecheck    # exit 0

# From cortex-app/documentation after runbook corrections:
npm run validate     # 31 pages, 155 method+path routes, 329+71 env names
npm test             # 10 passed, 0 failed/skipped
npm run build        # success; generated LLM mirrors, Pagefind 49 pages
```

Environment: rootless Podman 5.4.2 (`docker` emulation), Node 22.22.3,
Python 3.13.5, Neo4j/APOC **5.26.31** from `neo4j:5.26-community`, image digest
`sha256:5eb12ad77fa46ab73e23df9ea1f43f5c0f2a79523435577648e046be042b9b93`.
Neo4j image ID `2d8d9803fbe0cb13143971c97618d30a77dc597b96a2dea20a776a64e5fc9c8c`;
sidecar built from actual Dockerfile, final image short ID `4081e2b90d97`.

## Evidence identity and retention

Local artifacts: `output/restore-final-20261001/` (gitignored), containing receipts,
sanitized logs, captures/verdicts and the **original retained** snapshot at
`evidence/archive-frozen/20261001-143850/`. Cleanup removed only this run's
containers, volumes, network, sidecar image and scratch workspace; retained inputs
and evidence remain. Independent read-only review accepted the earlier full run;
lead reran after the final SQLite guard and runbook-comment change.

Accepted source SHA-256s from `evidence/provenance.json`:

| Input | SHA-256 |
|---|---|
| `ops/backup/backup.sh` | `bb41da4b2f1d4ab2efc12846b94bb88b374a8c37bd141bcf1c95d6baac7bd5f0` |
| `ops/backup/restore.sh` | `ab9b69316b5df8764040fbcb3d5d5323a82bfaf53905ab9498a60796b261a5a9` |
| `ops/backup/Dockerfile` | `643b0af793c22be42f612e2a8f7e2b4330eef14981a618775149bcc684a2bb6a` |
| `qa/restore/rehearsal.sh` | `8940a331d64305994735597cece7f0d53e0c7823d44f17f78faef66ca6af9e59` |
| `qa/restore/oracle.py` | `6f30148db4d27955dad69c77c6647c66e95c725b65c80d259179f150e71334c2` |
| `cortex-chat/scripts/restore-fixture.mjs` | `36d95f027712d02a666f322218e01707e015ac6e55bbc3a1c2a8167285f10932` |

Frozen snapshot hashes: `graph.cypher.gz`
`9679fefd5b3bf33e695782d13f6b765f057fba6099dba80616071b02e38b907d`,
`files.tar.gz` `05623b17a22b662f39c08d0b18154d63fc13bf502b2c3412f6638b549d182404`,
`SHA256SUMS` `c159e3ec901783ade9cdac58f1c1b08afc174c11a145e928991b8dd2dd2c2c3e`.
The compact reviewed result survives in this file; local binaries/logs are not
off-host backups or guaranteed durable artifact storage. Replaying synthesizes a
new fixture and snapshot; randomized password/encryption envelopes need not match
these bytes. Within one run the frozen source and restored bytes must match exactly.

## Diagnoses, claim limits and next step

- Initial smoke `20261001-140923-430d6775` omitted Chat because its helper wasn't
  integrated: limited graph/files evidence only. Full mode now requires Chat;
  explicit `--no-chat` is labeled reduced smoke, never full acceptance.
- Oracle review repaired string-label absence matching, duplicate-edge loss,
  silent truncated argument lists, optional Chat omission, source-canary copy
  handling, target-sentinel-derived asset expectations and SQLite sidecar writes.
  These were harness defects, not production regressions; healthy controls and
  intended broken fixtures rerun after gate corrections.
- Production restore **execution is unchanged**; header/runbook documentation
  now says stop all writers, use fresh target files, preserve keys/current state,
  compare completeness. The raw summary's static "unchanged" script label refers
  to execution; the provenance hash above includes the updated header comments.
- Representative synthetic records are not every product data shape or a real
  customer workload. Backend skill-secret config is preserved as fixture bytes;
  only Chat's real crypto/password modules are exercised for decryptability.
- This is **quiesced storage** evidence, not atomic online/WAL-inflight backup,
  mixed-version compatibility, application boot, API auth, browser reload/login,
  model quality, or off-host disaster recovery. Vector *retention* is demonstrated;
  rebuilding indexes on a fresh backend startup is not.

The consumer follow-up below completes boot/login/read evidence for the current
synthetic fixture. Search/model, browser, live-WAL, supported version pairs and
deployment-specific missing backup mounts remain separate obligations.

## Consumer run of record: `restored-consumers-20261001-k`

**PASS, 20/20 stages, exit 0**, 20:50:54–20:55:46 UTC. This fresh integrated run
includes healthy storage acceptance, real backend/Chat HTTP consumers on restored
state copies, original-state isolation, incomplete-restore rejection, pre-wipe
refusals, finalization and cleanup. Independent read-only review found no blocking
defects. Subsequent close-out confirmed no run-K containers, volumes or network
remain and all eight executed input hashes still match provenance.

| Consumer obligation | Observation |
|---|---|
| Real backend startup + restored keys | Real FastAPI import and uvicorn lifespan against real Neo4j; 15 HTTP probes passed, including 401 without/with invalid keys, scoped document/collection/stats reads, off-scope 403s, manage-only read 403s, and exact document content/raw-file bytes |
| Real Chat startup + restored identity/history/assets | Isolated locked install/build/server; 27 passing checks, no failures: login success/denials, restored users/projects/messages/opaque memory/citation shape, avatar/branding bytes and private-chat isolation |
| Actual backend key accepted through Chat | Chat's upstream URL equals the launched backend `startup.baseUrl`; restored encrypted read key succeeds on scoped collections and citation content through the supported proxy route |
| No provider/model effects | Provider sink hits = 0; Chat network tripwire clean, `blockedAttempts: []`; no search/ask/model journey exercised |
| Consumer isolation | Original restored graph unchanged and per-file manifests across uploads/custom_inputs/chat/skills/apps identical before/after consumers; Chat input state unchanged; consumer writes confined to copies |
| Startup writes accounted for | `boot_deltas_accepted: true`, `delta_enforcement.violations: []`; no node/relationship removal or schema-definition drift |
| Negative controls still discriminate | Valid-checksum incomplete restore rejected for exactly the missing avatar; invalid checksum/no-complete copies refused before wipe; Chat's separate missing-avatar copy returns 404 while login/branding still work |
| Evidence and teardown | All receipts `status: ok`, `exit: 0`; structured consumer results retained; backend stopped, scratch removed, finalization and run-resource cleanup passed |

**Gate v2 acceptance correction:** Chat's proxy permits document `/content`, not
`/file`. The required IDs are `proxy-isolated-backend-collections-200-scoped` and
`proxy-isolated-backend-citation-content-exact`; the legacy citation-file-exact ID
is rejected. Content is chunk-sorted and joined with `\n\n`, exactly matching the
independent UTF-8 fixture snapshot. `/file` 404 is asserted as the permission
boundary; the backend's raw-file byte check remains required. This corrected a
mistaken oracle, without changing product permissions or relaxing value checks.
See `consumer-coordination.json`, `README.md` and the Chat correction record.

**Accepted boot deltas:** exact schema ensures, one default Collection, APIKey
usage bookkeeping plus APIKeyUsageLog/HAS_USAGE, stats-seeded SystemMeta keys
`last_relationship_analysis_at` and `last_community_detection_at`, seven RANGE
indexes paired precisely with uniqueness constraints, and Document.entity_count
backfill independently computed from pre-boot topology. The three expected counts
in K are **1, 1, 1** (`doc-crr-1`, `doc-crr-2`, `fx-source-0001`). Broad name or
metadata/timestamp exemptions are not accepted.

### Replay and environment

Run from `cortex-app` with a **new** run ID. This is the full command selecting
both opt-in phases used in K; `summary.json.replay_command` is a storage-only
template and omits them, so use this command for consumer replay:

```bash
PATH="/home/clippy/.local/share/cortex-isolated-engine/iso-20261001-pid77180/bin:$PATH" \
NEO4J_IMAGE="docker.io/library/neo4j@sha256:5eb12ad77fa46ab73e23df9ea1f43f5c0f2a79523435577648e046be042b9b93" \
ALPINE_IMAGE="docker.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc" \
CORTEX_CONSUMER_PYTHON=/tmp/opencode/qa-venv/bin/python \
qa/restore/rehearsal.sh --run-id <new-id> --consumers --podman-no-dns
```

These host-specific paths are prerequisites to recheck, not portable tooling.
K used a **private** rootless Podman 5.4.2 engine (vfs storage, private runtime
root, cgroupfs). The shared engine was broken with a pause-process diagnostic;
**do not migrate/reset/prune it**, or touch unknown-owner
`crr-crr3-final-201857-*` resources. Private network DNS is unavailable without
the systemd user bus; opt-in direct-IP endpoints are inspected and validated
against run-owned identities at each operation. No DNS-discovery claim follows.
Healthy engines can use the default name-based transport without this adapter.

Scratch/install work uses the root filesystem; `/tmp` is a small tmpfs that hit
ENOSPC. Check actual free space and use an existing adequately sized `TMPDIR` for
local tests. Retained failed-run scratch is diagnostic data, not automatically
safe to delete by prefix; verify ownership and preserve needed evidence first.

Backend: host Python **3.13.5**, FastAPI 0.142.2, uvicorn 0.54.0, neo4j 6.3.1,
httpx 0.28.1, `EMBEDDING_DIMENSION=8`. Chat: Node **22.22.3**, isolated `npm ci`,
Next 16.1.7, React/React DOM 19.2.4, Sentry 10.62.0, better-sqlite3 12.9.0;
native binding smoke passed. This is **lockfile-locked**, not CI-identical:
CI Node 20, production Node 20 Alpine/musl and the backend production image
were not exercised.

### Evidence identity and attempt history

Local artifacts: `output/restored-consumers-20261001-k/` (gitignored). Keep
`summary.json`, all 20 receipts, `evidence/consumer-results.json` (includes **all
27 Chat check observations**), graph/file captures, transport/provenance and both
verdicts. Scratch referenced in raw receipt paths has been removed; the retained
JSON is the authoritative consumer evidence. Local artifacts are not off-host
or guaranteed long-term storage.

App base `bd6c1e035b3e6ece7741a0b733bfd8735145f846`, Chat base
`40fe79799c4729c4b695890566ef3a51746b3b5e`; uncommitted work recorded honestly
(28/10 dirty entries at execution). Consumed production source/locks manifest:
313 files, digest `90617b51e993d38190c8533e40c5733ed4092c903e0896dcf0726acf441c7b53`.
Backup scripts/Dockerfile retain the three hashes in the historical table above;
new executed input identities from K's `evidence/provenance.json`:

| Input | SHA-256 |
|---|---|
| `qa/restore/rehearsal.sh` | `72a5a7ab6252295ce0a9b8bec581a07c9bb2c3e72d54af737b51a7d9fd4884d4` |
| `qa/restore/oracle.py` | `4608239598c0c63093e790bbca128a40950d6f558ec262ceafe97651435d40a0` |
| `qa/restore/backend-consumer.py` | `43de36d4bf5c6322350428b3ef26834794d5a1794ea63c8e49b2071aef5f82f9` |
| `cortex-chat/scripts/restore-fixture.mjs` | `03480938859b1b641b3bd07836992f15167bf0d3226cbe8ddad1a4e91bc789a2` |
| `cortex-chat/scripts/restore-consumer.mjs` | `695881eb47815efa29d9fff8f66588ee50e4980cb56184bb53457fb43405b17c` |

| Attempt (`restored-consumers-20261001-…`) | Diagnosis / disposition |
|---|---|
| a | Broken shared engine; environment blocked |
| b–c | Private runroot/netavark/IPAM visibility; engine isolation repaired |
| d–e | Transport payload decoder and CLI binding wiring; harness repaired |
| f | Non-string subprocess environment value; runner repaired; invented Chat `/file` expectation diagnosed separately |
| g | Coherence oracle used flat `filename` rather than public `metadata.filename`; corrected |
| h | npm ENOSPC on tmpfs; moved install scratch; independently justified startup deltas added |
| i | Consumer gate accepted, then isolation locator mismatch; read-only re-comparison passed after harness correction |
| j | Storage/consumers/isolation/refusals passed, finalizer positional arity failed; named summary arguments repaired and read-only selfcheck passed |
| k | Fresh combined execution: 20/20, exit 0, reviewed and cleaned |

These diagnoses concern environment/evaluation machinery and observed startup
behavior; they are not eleven product regressions. Read-only re-judgment of i/j
helped confirm repairs but did not replace K's fresh integrated execution.

### Claim limits and next action

Demonstrated: synthetic **quiesced storage restoration plus real HTTP boot,
login and selected authorized/denied reads on isolated copies**. No production
data, live stores, paid model calls, commits, publication or deployment involved;
runtime source/schema/migration/dependency resolution remained unchanged.
Not demonstrated: browser rendering/send→late-memory→reload, search/quality,
CI/production runtime parity, DNS discovery, live-WAL/online atomic snapshots,
mixed-version recovery, absent deployment mounts or off-host disaster recovery.

Next locally actionable slice: behavioral Chat ask-route and persistence gates
(auth/scope, identifier stripping, header wiring, late memory and reload).
Recovery follow-ups require separately identified environments/version pairs.
Minor harness follow-up: emit phase-complete replay commands and align receipt
prose (`citation-file` wording / `document_id` versus existing
`rehearsal_doc_id`) with the strict realized checks. Required values and observed
checks already passed; these presentation fixes need focused verification and
selective revalidation, not a rewrite of K's historical evidence.

### Harvest verification — 2026-10-02

Docs/checkpoints updated and portable playbook advanced to v2.4.0. Executed with
`TMPDIR=/var/tmp/cortex-qa-tmp` (existing directory on the root filesystem):

| Working directory | Command | Result |
|---|---|---|
| `cortex-app` | `/tmp/opencode/qa-venv/bin/python qa/restore/oracle.py selftest` | PASS |
| `cortex-app` | `/tmp/opencode/qa-venv/bin/python qa/restore/backend-consumer.py selftest` | PASS |
| `cortex-app` | `/tmp/opencode/qa-venv/bin/python qa/restore/test_backend_consumer_units.py` | 62 passed |
| `cortex-app` | `bash -n qa/restore/rehearsal.sh`; `bash -n ops/backup/restore.sh` | exit 0 |
| `cortex-chat` | `npm test`; `npm run typecheck` | 90 passed, 0 failed/skipped; typecheck exit 0 |
| `cortex-app/documentation` | `npm run validate`; `npm test` | offline validator PASS; 10 passed |

`git diff --check` passed in all three repos. Runtime tracked diffs and untracked
entries are empty in app `backend/app`/`frontend/src`, Chat `src`, and skills
`src`/`sdk/src`/`mcp-server/src`. These focused checks close out the harvest;
the earlier adoption full-suite totals remain historical evidence. K's scripts
were not changed during harvest, so its recorded execution remains current for
those inputs; the new documentation does not claim a new full rehearsal.

### Next-slice completion — Chat ask/memory, 2026-10-02

K was **not rerun** and its executed scripts were preserved. The next local slice
completed as `chat-ask-memory-20261002-d`: 59 real Next HTTP ask/persistence checks,
one healthy source-extracted callback control and two successful defect
confirmations; execution exit 0, input drift and cleanup passed. Contract suite
122/122; both Chat typechecks passed. **Two page orchestration obligations fail**
(later partial-answer persistence and regenerate-snapshot overwrite); browser
not-run. No production runtime/schema/migration/dependency resolution changes.

Owning receipt and separately scoped next step:
`cortex-chat/docs/regeneration/records/2026-10-02-ask-memory-journey.md`.
This adds dev request-context/HTTP evidence, not a new restore, production runtime
or browser claim. `/tmp` remained full; root-backed scratch used. No shared engine
operation performed. The historical K `output/` path was absent at this session's
start; this does not re-establish retrieval of K's raw artifacts.

### Authorized Chat UI follow-up — 2026-10-02

K/D were not replayed. Current `chat-ui-turn-bound-20261002-b` fixes only Chat
`src/app/page.tsx` runtime ownership/persistence: actual Chromium 33/33, HTTP
59 + six positive source-extracted callback gates, contract suite129/129 and
typechecks clean. Retained broken-baseline UI races and positive rejection controls
precede candidate acceptance. Owning receipt/tooling/resources:
`cortex-chat/docs/regeneration/records/2026-10-02-turn-bound-ui.md`.
This adds selected **actual Chat UI** evidence, not a new restore/production/runtime
parity claim. Shared Podman/unknown-owner resources remain untouched; root scratch
used while `/tmp` stayed full. K's missing raw-artifact limitation remains historical.
