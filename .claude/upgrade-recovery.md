# Upgrade & Recovery

Owns: persistent-state ownership, backup/export scope boundaries, upgrade
compatibility, restore/verification protocol, and the gap list. Related:
[`development.md`](development.md) (deploy paths, self-host update/backup
mechanics, volume ownership), [`environment.md`](environment.md) (backup env
vars), [`domain/admin-features.md`](domain/admin-features.md) (library
export/import, system reset), [`domain/skills.md`](domain/skills.md),
[`domain/apps.md`](domain/apps.md), [`domain/git-integration.md`](domain/git-integration.md),
[`domain/document-pipeline.md`](domain/document-pipeline.md). Chat state
ownership lives in the cortex-chat repo's
`.claude/guides/operations-and-state.md` (SQLite/WAL/encryption/migrations).
Restore runbooks of record: `ops/backup/backup.sh` + `ops/backup/restore.sh`
headers, `selfhost/README.md` § Backups.

## Core distinction

**Library export ≠ disaster backup.** Two independent mechanisms with
different payloads; neither substitutes for the other.

| | Library export/import (`library_transfer_service.py`) | Backup sidecar (`ops/backup/`) |
|---|---|---|
| Mechanism | `POST /api/admin/export` → ZIP of 12 NDJSON sections + `files/` + manifest (v1.0) | Nightly APOC `apoc.export.cypher.all` (`graph.cypher.gz`) + `files.tar.gz` of mounted volumes |
| Graph fidelity | Curated app sections only | APOC logical export of graph records/properties (including API keys, `GitConnection`, x402 config, tasks, usage and system metadata); not a byte-for-byte physical database snapshot |
| Files | Original document files (`files/{doc_id}.ext`) | Whole volumes: uploads, custom_inputs, chat, skills, apps (existence-guarded) |
| Skills | Nodes + files, **secret-typed `config.json` fields stripped** (re-enter after import) | Full volume incl. encrypted secrets |
| Embedding carry | Chunks + entities carry vectors; import warns on model/dimension mismatch | Raw export; vector **indexes** not carried — backend rebuilds at startup (so no re-embed) |
| Secrets/keys | Never (no API keys, no git PATs — by design) | Carries everything on the volumes/graph |
| Purpose | Portability/migration between instances, sharing | Point-in-time disaster recovery on the same stack shape |

## Persistent state ownership

| State | Owner / location | Backed up by | Lost if |
|---|---|---|---|
| Knowledge graph + SystemMeta + API keys + GitConnection + x402 config + TaskRecord + usage days | Neo4j, volume `neo4j_data` (`docker-compose.prod.yml:26`) | APOC export; library export covers the app sections only | volume loss without backup |
| Server-side conversation sessions (`ApiSession`) | Backend `session_service.py`, Neo4j, scoped by creating key | APOC graph export; **excluded from library ZIP** | library-only restore cannot recover these sessions |
| Schema (constraints/indexes incl. vector) | APOC carries ordinary schema; restore drops constraints/non-vector non-lookup indexes before replay. Backend startup ensures its schema; vectors require separate startup handling (`restore.sh:64-89`) | export + versioned startup code | index recreation can fail on incompatible data/config; verify readiness and query behavior |
| Uploaded documents | `uploads_data` → `upload_dir` `./uploads` (`config.py:140`) | both (`files/` in export; tar in backup) | volume loss |
| Custom inputs | `custom_inputs_data` → `./custom_inputs` (`config.py:141`) | both | volume loss |
| Skill files + `config.json` (secrets encrypted with backend `ENCRYPTION_KEY`) | `skills_data` → `skills_dir` `.agents/skills` (`config.py:806`; `skill_service.py` encrypts secret-typed fields) | backup tar only; export carries sanitized config | volume loss; export loses secrets; `ENCRYPTION_KEY` loss → reconfigure (skill_service logs "encryption key changed or removed") |
| Apps: bundles + per-app `storage.sqlite` | `apps_data` → `apps_dir` `.agents/apps` (`config.py:1164`; `app_storage_service.py:58`) | backup tar only; **not in library export** | volume loss |
| Git connection clones | `git_work_dir` `./git_repos` (`config.py:855`) — **cache, not source of truth**; connections are `GitConnection` nodes, PAT never persisted (`git_connector_service.py:15`) | APOC export (config only); re-clone on demand | re-clone cost only |
| Chat state (SQLite users/groups/minted keys/history, WAL, avatars `.{png,jpg,webp,gif}`, branding) | `/app/data`; app stack uses `chat_data`, standalone chat compose uses `cortex-chat-data` — resolve actual project volume | backup tar only where mounted; Markdown conversation export is not a full-state backup | volume loss; **`APP_ENCRYPTION_KEY` loss makes every minted key undecryptable** → re-provision group and user content keys |
| Env/secrets (`.env`: `ADMIN_API_KEY`, `ADMIN_PASSWORD`, `NEO4J_PASSWORD`, `SESSION_SECRET`, `APP_ENCRYPTION_KEY`, `CHAT_APP_ENCRYPTION_KEY`, LLM keys) | host `.env` | **nothing backs this up** | operator's responsibility |
| Regenerable | `hf_cache` volume, chat `logo.email.png`, git clones | — | re-download/rebuild |

## Backup coverage by deployment path

`backup.sh` tars any of `/data/{uploads,custom_inputs,chat,skills,apps}` that
exist — but the sidecar only sees what the compose mounts:

| Stack | Sidecar mounts into backup | Gap |
|---|---|---|
| selfhost (`selfhost/docker-compose.yml:209-214`) | all five | — |
| Dokploy (`dokploy/docker-compose.dokploy.yml`) | all five | — |
| Coolify (`coolify/docker-compose.coolify.yml:254-257`) | uploads + custom_inputs only | skills/apps volumes exist but are **not archived**; no chat service |
| app-repo overlay (`docker-compose.backup.yml:46-49`) | uploads + custom_inputs only | chat/skills/apps not archived |

Both tiers need the neo4j wiring (`NEO4J_apoc_export_file_enabled=true` +
backups volume at `/var/lib/neo4j/import`) or the export fails loudly
(`backup.sh:79`). Tier 2 physical (`NEO4J_ENTERPRISE_BACKUP=true`) is
Enterprise-only and needs `neo4j-admin database restore` on the other side
(`restore.sh:48`). `.complete`, `SHA256SUMS` and the row-count check record that
the backup job's checks passed (`backup.sh:82-132`); they do **not** demonstrate
restorability or cross-resource consistency. Health proves recent local job
completion, not record/identity/byte fidelity or an off-host copy. The disposable
protocol below is the stronger recovery gate.

## Upgrade compatibility

- **Backend/schema**: startup ensures the schema expected by that code version.
  APOC export is a cypher-shell replay, still dependent on compatible Neo4j/APOC
  syntax and data meaning. `stack.json` pins chat/neo4j/caddy; chat must be
  released before cortex-app (`scripts/check-version-sync.mjs`); self-host
  update = backup → re-fetch `selfhost/` + `ops/` → `pull && up -d --build`
  (`--build` is how backup-script fixes reach an existing sidecar image;
  `selfhost/README.md` § Updating). `minInstaller` floor: 1.2.2.
- **Backend rollback**: rolling an image tag back is *not* a restore — data
  written by newer code has no tested downgrade path. Prefer forward-fix; if
  rollback is needed, first test whether the old image reads newer state. A
  pre-upgrade snapshot is a separate disaster-restore option: it discards later
  valid writes unless they are preserved/reconciled. Recovery choice depends on
  the affected state and external effects, not just image availability.
- **Chat**: Drizzle applies migrations by timestamp watermark only — editing
  an applied migration retroactively does nothing; fix-forward with a new
  migration (chat `operations-and-state.md`). Same image version floor rules
  as the app release.
- **Version matrices**: `development.md` records a historical 2026-07-07 restore
   check, including escaping. This campaign now has identified current-version
   quiesced storage + HTTP consumer replay, but no mixed-version matrix; neither
   historical nor current same-version results establish older-version compatibility.
   Imports of exports from *older* app
  versions only warn on version/embedding mismatch
  (`library_transfer_service.py:422-465`) — no multi-version jump matrix has
  been run; treat any N-version export→new-instance import or tag rollback
  as **untested** until exercised on disposable infra (protocol below).

## Recovery decision matrix

| Scenario | Mechanism | Commands (existing only — never invent destructive ones) |
|---|---|---|
| Same-host disaster (volume loss, bad deploy) | Backup sidecar Tier 1 | Use the runbook in `ops/backup/restore.sh` / `selfhost/README.md` § Backups with resolved target project/volume names; additionally stop **all writers** (including chat), restore files into fresh targets, then run record/identity/byte comparisons below. The runbook's final `/api/stats` check alone is insufficient. |
| Replay failure mid-restore | Failed recovery; source snapshot retained | Wipe has happened; replay may have committed some batches, so target may be empty **or partial**, despite the script's "EMPTY" message. Keep consumers stopped, diagnose operation/version/event order and rerun from the retained snapshot after correcting the cause. |
| Migrate to a new instance | Library export/import | Export ZIP on old instance → import (`clean` on empty target, `replace` wipes) on new; re-enter skill secrets, re-provision env/secrets/keys, recreate chat groups (minted keys are backend-bound) |
| Partial loss (documents/skills) | Library export/import or volume restore | export covers documents+skills (sans secrets); skills/app *volumes* only via the tar |
| Chat restore | Same tar, chat volume | Stop chat first (gap: runbook doesn't say so — see below) |
| Very large graph | `CORTEX_NEO4J_TX_TIMEOUT` | Raise it if export/restore approaches 300s (`backup.sh:76`, `restore.sh:59`) |

## Disposable restore-verification protocol

Run on **disposable** infra (separate compose project name → separate volume
names; no live volumes, no LLM keys configured, no paid calls, no runtime
edits to the production stack). Artifacts of record per snapshot
`/backups/<ts>/`: `graph.cypher.gz`, `files.tar.gz`, `SHA256SUMS`, `meta.json`
(nodes/relationships/db_nodes_at_export/raw_bytes), `.complete` marker.

The runnable storage and opt-in HTTP consumer phases live in `qa/restore/rehearsal.sh`; see
[`../qa/restore/RESULTS.md`](../qa/restore/RESULTS.md) for the 2026-10-01 local
**quiesced** graph/files/Chat/Apps + consumer boot/login/read result and controls.
Browser, production runtime parity, online/WAL-inflight and cross-version
combinations remain future work. The
library-transfer test remains a different subset, not whole-stack recovery.

1. **Freeze the gate and source fixture.** Record app/chat image digests,
   Neo4j/APOC versions, actual volume/config paths and key *references*. Seed
   synthetic linked records across every owned resource above (including an
   encrypted chat key, SQLite messages/memory, avatars, skills and app storage).
   With writers quiesced, capture complete logical records and stable domain
   identities/relationships plus per-file SHA-256 hashes. Declare allowed startup
   changes individually (e.g. bootstrapped superadmin hash, interrupted task state);
   no broad "timestamps/metadata ignored" escape. Record expected schemas separately.
2. **Snapshot** the quiesced fixture using the actual backup command. Retain
   original archives and verify their checksum manifest and `.complete`. The
   manifest checks archive integrity, not individual restored files. Record T.
3. **Exercise post-T writes** only on the disposable source after snapshot
   completion (e.g. a new record plus new blob). Retain these values as exclusion
   assertions. Freeze the original snapshot; do not replace it with a live copy.
4. **Restore into new empty stores/volumes** in a separate compose project,
   stopping all target writers. Follow existing restore tools with actual target
   mount/config precedence. Never untar over live SQLite; a checkpoint alone
   does not freeze future WAL writes. Start only the intended version afterward.
5. **Compare completeness at the target:** exact sets of domain IDs and linked
   records, field values/opaque memory and decrypted synthetic-key value; exact
   referenced file paths and bytes against the frozen per-file manifest. Verify
   all post-T records/blobs are absent. Counts and health are diagnostics, not
   acceptance; the backup job's 90% count tolerance is **not** this oracle.
6. **Verify real consumers on copies before destructive controls:** capture the
   original target graph/files before and after to require unchanged state. Boot
   the actual backend/Chat with sanitized configuration, fixture sinks/tripwires;
   verify login/history, accepted decrypted keys, scoped reads/denials and cited
   content through supported routes. Storage equality is not usability evidence.
   Enforce an exact startup-delta allowlist (schema definitions/defaults/backfills/
   usage) with expected values derived independently from pre-boot state. New
   differences require diagnosis and recorded acceptance, not blanket exclusions.
7. **Challenge this oracle:** restore an isolated copy containing valid graph/DB
   records but omitting an avatar or uploaded blob (recompute archive checksums so
   transport integrity still passes). The *target completeness comparison* must
   reject the missing bytes/identity. Also test checksum/no-`.complete` refusal,
   but those controls alone only establish corruption detection. Healthy full
   restore must pass the same oracle.
8. **Retain evidence and clean up:** keep original snapshots, structured consumer
   observations, comparisons, process failures and candidate/gate identities before
   cleanup. Finalize/cleanup failures invalidate overall success. Re-resolve ephemeral
   endpoints against stable run-owned identities at every claimed operation after
   restart; hold writes to accepted inputs during execution. Delete only this run's
   scratch resources. A quiesced pass establishes neither online atomic backup nor
   mixed-version compatibility; repeat only for actual supported combinations.

## Known recovery gaps

Recorded, not fixed (do not silently work around them in docs):

- **No off-host transport**: backups live in the local `backups` volume;
  shipping off-host is the operator's job (`selfhost/README.md` says so).
- **Live-volume tar consistency**: `files.tar.gz` is taken from running
  volumes (tar exit 1 tolerated, `backup.sh:122-124`) — a doc ingested
  between graph export and tar can exist as node without bytes, and a
   **running chat's SQLite/WAL can be copied mid-write**. Quiesce every writer or
   use an independently verified consistent-snapshot procedure; do not equate a
   one-time checkpoint with a stable live file set.
- **All-writer quiescence is now explicit** in the restore header, self-host and
  deployment docs (including optional Chat). Existing backup automation still tars
  live volumes; corrected instructions do not establish online snapshot consistency.
- **`file_path` remap on import**: library import remaps file paths and
  doc-scopes basenames; exports whose documents were ingested by very old
  versions predate the doc-scoped naming — import degrades to warnings, not
  silence.
- **Chat has no state export tool**: users/keys/history exist only in the
  chat SQLite volume + `APP_ENCRYPTION_KEY`; nothing else copies them.
- **Env/secrets are never in any backup**; losing `.env` loses
  `ADMIN_API_KEY`, chat `APP_ENCRYPTION_KEY` (minted keys unrecoverable —
  recreate groups), backend `ENCRYPTION_KEY` (skill secrets reconfigure).
- **Untested matrices**: tag rollback, multi-version export imports, and
  Tier-2 physical restore are documented but not round-trip validated.

## Baselines

Review baseline (this slice): cortex-app `bd6c1e0`, cortex-chat `40fe797`
(v1.3.0), cortex-skills `1156ade`. Self-host release stack currently pins an
older chat (see `selfhost/stack.template.json`) — docs must keep the two
distinguishable.
