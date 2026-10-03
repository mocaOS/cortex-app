# Disposable whole-stack restore rehearsal (qa/restore)

Exercises the ACTUAL `ops/backup/backup.sh` + `ops/backup/restore.sh` (unchanged)
against two disposable real Neo4j 5.26-community stores plus all five file roots
(uploads, custom_inputs, chat, skills, apps) and an independent frozen oracle.
No local production data, no live volumes, no LLM/API services.

## Transport adapter (opt-in `--podman-no-dns` / `CORTEX_RESTORE_PODMAN_NO_DNS=1`)

Fixture-mode environmental adaptation only — the actual backup/restore scripts,
Neo4j, APOC and consumer HTTP journeys are unchanged:

- On engines where user-level podman (5.4.2 private root) cannot start
  aardvark DNS on user-defined networks, `--podman-no-dns` creates the
  run-owned network with `--disable-dns` (podman-supported), decodes the
  network binding from the actual `docker network inspect` payload
  (`oracle.py network-binding`: Docker `IPAM.Config` and Podman `subnets`
  schemas both supported — podman 5.4.2 returns `subnets[]`, where the
  Docker-only `{{.IPAM.Config}}` template failed; orphan/duplicate/unknown-
  shape/invalid-or-non-private-CIDR/malformed payloads fail closed), resolves
  each store's INTERNAL IP on that network via actual `docker inspect` after
  start, validates the binding (container network id == run-owned network id,
  IP inside the decoded subnet; `oracle.py transport-endpoint`), and drives
  `backup.sh`/`restore.sh` with `NEO4J_ADDRESS=bolt://<inspected-ip>:7687`
  (`SOURCE_ADDRESS`/`TARGET_ADDRESS`, built fresh per stage and logged in every
  receipt). Consumer bolt stays on the published 127.0.0.1 loopback port; the
  consumer graph copy joins the same private network; no external providers.
- Default (no flag) remains name-based DNS for normal Docker/podman engines.
- **Claim limits**: in direct-ip mode no DNS-based service discovery is
  exercised or evidenced; this is a transport adapter, not a replacement for
  the actual Neo4j/APOC or consumer HTTP surface, and no checker assertion is
  lowered. If the engine rejects `--disable-dns`, the run dies
  `TRANSPORT-BLOCKED` (no silent fallback to a DNS network).
- **Endpoint refresh at every claimed operation**: the container name/id is
  the stable identity; the internal IP is ephemeral (rootless podman may
  reassign it across stop/start, and a stale target IP could address the
  consumer-copy store). The harness therefore re-inspects and re-validates the
  source endpoint before the snapshot and the target endpoint immediately
  after the consumer-phase restart and before each destructive restore
  (healthy, incomplete, refusals), using the same oracle transport
  validation — no new gate semantics, the allowed-transport constraint
  enforced where the operation is claimed. Each resolved endpoint + container
  id is recorded in the stage receipts and `logs/transport-refresh.log`.
  Chosen mode is captured in `provenance.json` (`harness_meta`) and the
  chosen endpoints in every stage receipt + `summary.json` (`transport`
  block) + `evidence/transport.json` (includes both container ids as the
  stable identity, not the IP alone).

## Consumer phase (opt-in `--consumers`)

After the healthy-restore oracle passes and BEFORE the destructive incomplete
control, `--consumers` runs isolated real consumers against a disposable COPY
of the restored state only (never the source store, never the original restored
volumes, never providers):

- The consumer phase runs against a **graph COPY**: the target Neo4j data
  volume is copied offline (target stopped, copied, restarted) into a dedicated
  consumer container with bolt on a second random loopback port; the five
  restored file roots are copied into
  `<workspace>/consumer-files/{uploads,custom_inputs,chat,skills,apps}`.
- **Frozen-state hashes are collected before any consumer write**: a graph
  capture and per-root file manifests of the COPY, plus captures of the
  ORIGINAL target graph and restored volumes. After the consumer boot the
  harness re-captures the originals and requires byte-identical equality
  (isolation proof; any breach fails the stage).
- `qa/restore/backend-consumer.py` (owned by backendagent) launches the actual
  FastAPI app against the consumer-copy bolt with sanitized env
  (`EMBEDDING_DIMENSION=8` — matches the restored vector index, so startup
  schema-ensure is a no-op and no re-embed tasks run), asserts fixture data
  through real endpoints with the shared synthetic keys (scope-positive and
  scope-negative), then — while the server is live — invokes
  `cortex-chat/scripts/restore-consumer.mjs` (owned by chatagent, flags
  `--state-dir/--backend-url/--backend-admin-key/--document-file/--app-node`)
  and only then stops the
  server.
- **Strict gate** (`oracle.py validate_consumer_receipt`, hermetically
  selftested against the ACTUAL delivered shapes — schema/interface alignment
  recorded in `consumer-coordination.json`): exit 0, zero errors, receipt
  `chat` = the RAW chat receipt with `ok: true`, empty `failures`,
  `upstream.mode='isolated-backend'`, `upstream.url` equal to the backend's
  reported `startup.baseUrl`, `checks[{id, ok, reportOnly?, status?}]` where
  every non-reportOnly check passes and the two exact required ids
  (`proxy-isolated-backend-collections-200-scoped`,
  `proxy-isolated-backend-citation-content-exact`) are present as real
  (non-reportOnly) passing observations — gate version 2: the citation
  content-exact check compares the text retrieved via the supported chat entry
  `GET /documents/{id}/content` (chunk-sorted, joined `\n\n`) against the
  fixture UTF-8 snapshot; the chat proxy `/file` route is intentionally 404
  (`PROXY_ALLOWLIST` route.ts:16-22) and the legacy
  `proxy-isolated-backend-citation-file-exact` id is **rejected** wherever it
  appears (lead-accepted correction of a wrong invented entry — no product
  permission change, no relaxed value oracle; the backend original raw
  file-bytes gate and the plaintext group-key acceptance remain hard and
  unchanged). `networkTripwire.clean == true` with
  `blockedAttempts` an empty ARRAY (any entry = attempted egress; integer 0 or
  other types are schema errors), `cleanup.stateDirUnchanged == true`, and
  `boot_deltas` present with `boot_deltas_accepted: true` plus a consistent
  non-empty `chat_execution` wrapper record. Missing/false JSON is a failure,
  never green; the gate is not weakened to fit a candidate after a failure.
  The harness passes an optional chat-app node binary via `--chat-app-node`
  (env `CORTEX_CHAT_APP_NODE`) for the chat app process (`--app-node` on the
  chat runner); when provided it is resolved, hashed and version-recorded,
  when unset that is recorded plainly — the top node (v22, fixture ABI,
   lockfile-locked install) is the working default and no runtime cause is attached
  to either state.
- Interpreter preference for the runner: `CORTEX_CONSUMER_PYTHON` env, else
  `/tmp/opencode/qa-venv/bin/python`, else system `python3`; interpreter
  version + per-module dependency identity is recorded. Missing backend deps
  are an environment block (the runner exits 2 by design), not a product
  failure and never mocked.
- Contract and ownership boundaries (frozen, replaces earlier positional
  drafts): `qa/restore/consumer-coordination.json`; fixture v2 data contract
  emitted as `fixture-contract.json` by `oracle.py gen-fixture`.
- **Writer hold**: qa/restore/** must not be edited while any rehearsal run is
  active — provenance hashes the harness and runner inputs at stage start and
  re-verifies at finalize (a mid-run edit fails provenance; this is how run
  crr3-final-201857 was invalidated, and that failure evidence is preserved).

Fixture v2 aligns the store-only fixture with the actual runtime auth contract
(label `APIKey`, `key_prefix` = first 12 chars of the plaintext key,
`key_hash` = sha256 hexdigest) and the shared chat fixture IDs
(`fx-backend-key-0001/0002`, `fx-collection-0001`, citation source
`fx-source-0001`). Prior v1 fixture records stay untouched as history.

Candidate identity: provenance hashes the executed `ops/backup/*.sh`,
`Dockerfile`, both consumer runners, the chat fixture script, and a coarse
manifest digest of the consumed production source (`backend/app`,
requirements/lock files) at stage start and re-verifies at finalize — script
identities are whatever is on disk at run time, never assumed unchanged.

## Files

- `rehearsal.sh` — stage orchestrator; per-stage JSON receipts written before cleanup;
  on failure leaves disposable resources in place for diagnosis.
- `oracle.py` — fixture generator, graph/file/SQLite capture + comparison oracle,
  hermetic selftest (`oracle.py selftest`), receipt/hash helpers.
- `output/<run-id>/` — gitignored local artifacts of record (receipts, evidence,
  logs, summary). Nothing here is picked up by the production backup sidecar,
  which only tars the five data volumes.

## Replay

```bash
qa/restore/rehearsal.sh --run-id <unique-id>
# full gate: requires the companion chat fixture (default discovery at
# ../cortex-chat/scripts/restore-fixture.mjs, or pass --chat-fixture <path>)
# reduced smoke without chat (NOT whole-stack restore evidence):
qa/restore/rehearsal.sh --run-id <unique-id> --no-chat
```

Requirements: podman/docker CLI (rootless ok), node, python3, ~1GB disk for the
pulled `neo4j:5.26-community` image, ~2.5 minutes. **Engine preflight**: before
any image inspection the harness runs `docker info` (actual stdout+stderr kept
in `logs/engine-info.txt`, no credentials) and dies `ENGINE-BLOCKED` on engine
failure — an engine outage is never misreported as a missing image
(`IMAGE-BLOCKED` is the separate classification). Uses one neo4j image for both
stores + sidecar build from the actual `ops/backup/Dockerfile` (image passed via
`--build-arg NEO4J_IMAGE`). Scratch is claimed atomically under
`~/.local/share/cortex-restore-rehearsal/<run-id>` and removed on success;
failure leaves the `crr-<run-id>` containers/volumes/network for diagnosis
(remove with `docker rm -f`, `docker volume rm`, `docker network rm`). Existing
scratch paths and any pre-existing `crr-<run-id>-*` resources are refused, not
reused. Receipts are fail-closed (a failed receipt write aborts the run).

Before replay, ensure the images exist (`docker pull
docker.io/library/neo4j:5.26-community` and `docker pull
docker.io/library/alpine:3.20` if absent), and run `npm ci` in the companion Chat
checkout if its dependencies are missing. The harness fails on missing prerequisites;
it does not install packages or silently skip required Chat coverage. Use a **new**
run ID; retained evidence/workspaces and resource names are never reused.

[Results of record](RESULTS.md) identify the current accepted run and limitations.

## What each run proves

1. **Healthy full restore** — actual `backup.sh` snapshot of a quiesced seeded
   store; actual `restore.sh` runbook (graph replay + runbook step-4 untar) into
   clean volumes; oracle must PASS: exact graph records by domain IDs/properties
   (never internal Neo4j IDs, APOC JSON export capture), persisted schema
   definitions (unique constraint, fulltext, and a pre-created vector index the
   restore deliberately retains), per-file SHA-256 across all five roots,
   per-app + chat SQLite rows, post-snapshot writes (new Document + blob +
   usage bump + chat canaries) absent, and a usable restored fulltext index
   (query count equal to the source's healthy count).
2. **Valid-checksum-incomplete control** — archive copy with the chat avatar
   removed and checksums recomputed (transport integrity passes): `restore.sh`
   succeeds but the oracle must REJECT and name the missing bytes.
3. **Refusal controls** — no-`.complete` and checksum-corrupt copies: `restore.sh`
   refuses with its exact diagnostics before the wipe; the exact target graph
   capture is byte-identical before/after (node count advisory only).
4. **Chat gate** (full mode) — companion `verify` passes on the frozen fixture
   and on the restored volume (chat DB sha256 proven stable across verify);
    `mutate` canaries are written and rejected by `verify` at the source **after**
    the snapshot is frozen; they must be absent at the restored target. Every
    copy/mutate step is fail-closed.

## Known limitations (recorded honestly)

- Quiesced stores: no online/WAL-inflight backup or restore is demonstrated
  (the chat fixture DB is closed and checkpointed; the harness rejects any
  `-wal`/`-shm` presence via the companion's quiesce check).
- No consumer journeys (boot/login/search) are run unless `--consumers` is
  passed; even then the consumer phase is read-journey + isolated-boot
  evidence on copies, not a boot rehearsal of a production deployment.
- Schema replay coverage is limited to what the fixture defines (one unique
  constraint + one fulltext + one vector index); backend-startup schema
  recreation is exercised only in the consumer phase's declared boot deltas.
- **Writer hold**: do not edit `qa/restore/**` (or the runner scripts) while
  any rehearsal run is active anywhere — provenance hashes inputs at stage
  start and re-verifies at finalize; mid-run edits invalidate the run
  (preserved failure evidence: run `crr3-final-201857`).
