# QA Contract & Evaluation Records — evaluation slice 2026-10-01

Compact, stable-ID records for the deterministic backend contract gates added
in this slice (baseline `bd6c1e0`, clean tree). Scope: evaluation improvement
only — no runtime, dependency, migration, or default changes. These records
separate **hard invariants** (deterministic, enforced by the offline suite)
from **model quality** (agentic answer/retrieval quality), which is NOT
measured here and has no calibrated quantitative acceptance yet (see
`UNRESOLVED-QUALITY-001`).

## Environment & baseline

- Runner: torch-free venv built from `backend/requirements-base.txt`
  (no committed `.qa-venv` existed; built at `/tmp/opencode/qa-venv` because
  this session may only write `backend/tests/**` and `qa/**`).
- Baseline full suite (before additions, first run):
  `cd backend && <venv>/bin/python -m pytest -q`
  → **1375 passed, 11 failed, 22 skipped**.
- The 11 failures were one environmental class, NOT defects in the slices
  touched here: the upload disk guard (`MIN_FREE_DISK_MB=500`) rejected
  uploads because the runner's uploads filesystem (`/tmp` tmpfs, where pytest
  `tmp_path` lives) had ~433 MB free — affecting `test_max_files.py` (2),
  `test_max_entities.py` (2), `test_import_chunked_upload.py` (5),
  `test_api_ergonomics.py` (2). Classified `blocked (environment)`.
- **Closed by re-run (same day, no limit relaxed):** after space was freed on
  the root filesystem, the FULL offline suite was rerun exactly once with
  pytest `TMPDIR` pointed at the root fs (`TMPDIR=/var/tmp/cortex-qa-tmp`,
  `df -h /` → 4.4 GB free ≥ the guard's 500 MB; `/tmp` tmpfs itself still
  had only ~373 MB and was NOT touched):
  `cd backend && TMPDIR=/var/tmp/cortex-qa-tmp <venv>/bin/python -m pytest -q`
  → **1402 passed, 0 failed, 22 skipped in 58.33s** — the 11 disk-blocked
  uploads tests pass with the guard fully enforced; the original baseline
  record above is preserved as history.
- The 22 skips are the live E2E modules (auto-skip: "set CORTEX_E2E_API_KEY
  and run a live backend…"). Verified no `CORTEX_E2E_*` variables were set in
  the run environment — no live connection is attempted beyond the known skip.

## Gate E1 — `backend/tests/test_scoped_principal_journeys.py`

**Boundary:** authenticated-principal journeys — server-side sessions
(`ENABLE_SESSIONS`) and the remote-MCP tool dispatch (`/mcp`) — exercised at
the real HTTP surface with the REAL auth dependency chain (no `client`
auth-bypass override; two fabricated API keys validated through prefix lookup
+ SHA-256 hash against a scripted key store).

Motivation: the shared `client` fixture overrides `require_*` with a fake
admin (`key_id="test-admin"`), so every pre-existing session/MCP test ran as
the SAME superuser. A regression that drops `auth.key_id` from the
owner-scoped store lookups, or drops the caller's key from the MCP→REST
forwarding, was invisible. This gate makes the principal observable.

| ID | Hard invariant (deterministic) | Check |
|---|---|---|
| INV-SESS-001 | A session belongs to the creating key; another key's GET/list/DELETE/ask on it → 404, no turn persisted (session_service doctrine) | `TestSessionOwnership` |
| INV-SESS-002 | `session_id` + client-carried `conversation_memory` are mutually exclusive on the STREAMING ask path (previously only non-streaming 400 + stream fast-search 400 were covered) | `test_stream_conflict_session_vs_client_memory` |
| INV-SESS-003 | The owner's opaque memory blob round-trips verbatim to its owner only; a streaming turn persists history+memory under the owner's principal, foreign key still 404 | `test_owner_sees_own_session_with_opaque_memory_intact`, `test_owner_stream_turn_persists_under_its_own_principal` |
| INV-MCP-001 | `/mcp` requires a valid key (401 otherwise); `tools/call` forwards the CALLER's key into the internal ASGI dispatch — a collection-restricted key sees only its allowed collections through `search_knowledge` | `TestMcpPrincipalForwarding` |
| INV-MCP-002 | `ask_question` `mode=deep_research` aggregates the ACTUAL internal `/api/ask/stream` SSE leg (quality-mode agent stream — reachable only via the streaming endpoint) and maps a stream `error` frame to an `isError` tool result, never a protocol crash | `TestMcpDeepResearchSse` |

**Negative control (INV-SESS-001 sensitivity), isolated:**
`_OwnerBlindSessionStore` — a store substitute with ownership matching
disabled (the plausible violation "the chain stops matching `key_id"`).
The exact INV-SESS-001 read-isolation assertion fails under the mutant
(`pytest.raises(AssertionError)`), and the cause is verified as the intended
leak: the foreign key reads the owner's session INCLUDING its memory blob
(200 + fact visible). In-test fixture only; no shared runtime mutated.

**Exact commands / results:**
```text
cd backend && /tmp/opencode/qa-venv/bin/python -m pytest -q \
  tests/test_scoped_principal_journeys.py
→ 11 passed in ~4s
```

**Limitations:**
- The ownership predicate itself lives in Neo4j Cypher
  (`neo4j_service.get_api_session` etc.). Offline, the scripted store mirrors
  the documented store contract; the gate proves the HTTP layer passes the
  REAL authenticated principal into owner-scoped lookups and maps a miss to
  404 — it does not execute the Cypher predicate. Live per-key isolation
  remains the E2E harness's job (currently not covered there either —
  `test_live_e2e_authed.py` has no session/MCP journey).
- LLM/Neo4j mocked per `conftest.py`; no answer quality measured.

## Gate E2 — `backend/tests/test_library_transfer_roundtrip.py`

**Boundary:** library transfer state continuity — the REAL production export
writer → real ZIP → REAL production importer into a fresh target
(`ScriptedGraphStore` mirrors the documented `neo4j_service`
export_*/import_* contract with in-memory tables). Pre-existing tests covered
the serialization primitives and crafted-archive hardening with a *synthetic*
archive; a full export→import round trip had never been executed.

| ID | Hard invariant (deterministic) | Check |
|---|---|---|
| INV-LIB-001 | Every exported record class survives import with its identity: documents (identity fields; `file_path` remap + `processing_status=completed` are the declared restore deltas), chunks, entities (exact equality), relationships (endpoints + promised props: description/weight/confidence/extraction_method/source_document_id/extracted_at), communities, community/collection memberships, chunk mentions, merge history, system meta; result counts equal source counts; `embedding_compatible=true` | `test_records_identities_survive_import` |
| INV-LIB-002 | Restored files are byte-identical to their sources and confined to the instance upload / custom-input dirs (custom inputs in their dir); doc-scoped restore names | `_assert_full_continuity` (byte section) |
| INV-LIB-003 | Clean mode refuses a non-empty target BEFORE any destructive action; target tables unchanged, nothing imported | `test_clean_mode_refuses_nonempty_target_without_destroying_it` |
| INV-LIB-004 | An archive missing a record class (`chunks.ndjson`) and a document's blob must not import "silently complete": warnings name the gap, restored counts reflect what was actually present | `test_incomplete_archive_reports_gap_and_restores_only_what_is_present` |
| INV-LIB-005 | The `ScriptedGraphStore` substitute cannot drift from the real store contract: every call the PRODUCTION transfer code actually makes (recorded during a live round trip) must still bind to the real `Neo4jService` method's signature — actual observed calls, not a hardcoded method inventory | `test_store_substitute_calls_still_bind_to_real_neo4j_service_contract` |

**Gate-sensitivity probes (harness self-checks, NOT acceptance runs;
disposable script in /tmp, per REGENERATIVE-SOFTWARE §9.5/§14.1):**
the extracted gate function `_assert_full_continuity` was run against six
mutant store substitutes + one healthy control:

```text
drop-doc-title            → rejected (identity)      OK
corrupt-restored-bytes    → rejected (byte compare)  OK
drop-rel-confidence       → rejected (promised props) OK
silently-drop-chunks      → rejected (counts/records) OK
file-outside-upload-dir   → rejected (containment)   OK
inflated-import-count     → rejected (count honesty) OK
healthy-baseline          → accepted (no false positive) OK
```
An earlier probe round exposed that byte/prop checks lived in a separate test
from identity checks; the assertions were consolidated into
`_assert_full_continuity` so probes (and future evaluators) run the EXACT gate.

**INV-LIB-005 drift-gate sensitivity probes (harness self-checks, /tmp):**
the binding check rejects a store contract that dropped
`import_relationship` entirely (getattr → None → "no longer offers") and a
`export_all_chunks_batched` whose `batch_size`/`skip` kwargs were renamed
(`TypeError` on bind) — the kwarg-bearing calls are exactly the ones
positional call compatibility would hide.

**Exact commands / results:**
```text
cd backend && /tmp/opencode/qa-venv/bin/python -m pytest -q \
  tests/test_library_transfer_roundtrip.py
→ 5 passed in ~2s
```

**Fixtures:** deterministic sanitized synthetic data only (synthetic doc ids,
filenames, byte payloads); no tenant/customer content. `source_env` pins
`upload_dir`/`custom_inputs_dir` via the autouse `_isolate_env`.

**Limitations / observations:**
- OBS-LIB-001 (observation, not gated as contract): relationship properties
  outside the importer's explicit props dict (e.g. an `evidence` key) are
  dropped on restore. The export carries them; the import contract restores
  the six promised props only. If full relationship-property fidelity is
  intended, this is a gap to decide on; the gate intentionally does not freeze
  the current loss as contract.
- Skills (nodes + files) are out of this gate's scope (no skill fixtures);
  skill export sanitization and skill-restore path traversal are covered by
  existing suites.
- The store substitute mirrors the documented store contract, not live Cypher;
  the real end-to-end export→import against Neo4j remains manual/live-E2E.

## Post-change suite state

First post-change run (uploads filesystem still space-constrained — see
baseline section for the environmental classification):
```text
cd backend && /tmp/opencode/qa-venv/bin/python -m pytest -q
→ 1390 passed, 11 failed (pre-existing, disk-guard environment), 22 skipped
```
**Closed by the single re-run with sufficient uploads-filesystem space
(`TMPDIR=/var/tmp/cortex-qa-tmp`, root fs 4.4 GB free, disk guard fully
enforced, no limit relaxed):**
```text
cd backend && TMPDIR=/var/tmp/cortex-qa-tmp /tmp/opencode/qa-venv/bin/python -m pytest -q
→ 1402 passed, 0 failed, 22 skipped in 58.33s
```
Net vs the 1375-pass baseline: +27 (16 new gate tests from this slice + the
11 formerly disk-blocked uploads tests now passing). The 22 skips are the
known live-E2E auto-skips (no `CORTEX_E2E_*` env vars set; no connection
attempted).

CI-parity lint:
```text
<venv>/bin/python -m ruff check --select E9,F63,F7,F82 app/ tests/
→ All checks passed
```
No flakiness observed across repeated runs of the new gates.

## UNRESOLVED-QUALITY-001 — agentic/model quality acceptance

**Status: explicitly unresolved for this candidate.** No version-identified,
calibrated acceptance baseline was established in this campaign for answer
quality, retrieval relevance, extraction precision, or deep-research completion.
Existing internal `bench/` heuristics and historical A/B notes are separate
evidence to inspect via `.claude/bench.md`; do not treat them as nonexistent or
as current release acceptance. No quantitative quality criterion is defined here and none
should be inferred from the green gates above. The offline gates prove hard
invariants only (isolation, scoping, state continuity, error mapping).
What a calibrated quality evaluation would need (owner: lead/evaluation
stakeholder):
- A versioned golden question set (ordinary, ambiguous, unanswerable,
  adversarial; representative of the deployment's corpus), with held-out
  acceptance cases separated from tuning cases.
- Frozen experimental conditions per REGENERATIVE-SOFTWARE §10.2 (model
  identifiers/revision, prompts, retrieval config, corpus).
- Pre-declared statistical acceptance (sample size, margins, decision rule)
  BEFORE any candidate comparison; model judges only with a versioned rubric
  calibrated against reviewed examples.
- A live/sandboxed deployment (real Neo4j + real model) — cannot be produced
  by offline unit tests, and this slice ran none.

## Integrated guide harvest

The lead incorporated these lessons into `.claude/qa.md`; this list preserves the
handoff provenance, not outstanding work:

1. Coverage map: add `test_scoped_principal_journeys.py` (real-principal
   session scoping + MCP key forwarding + deep-research SSE aggregation) and
   `test_library_transfer_roundtrip.py` (full export→import state continuity,
   clean-mode refusal, incomplete-archive control) to the unit/contract list,
   and note the `client`-fixture auth-bypass caveat (real-scope gates must use
   a no-bypass client like `test_auth_enforcement_http.py`).
2. Coverage map: note that `features.json` gained `F-SESS-001` (server-side
   sessions) and `F-MCP-001` (remote MCP) — both were absent from the
   inventory.
3. Gotchas: pytest `tmp_path` lives on `TMPDIR` — on this machine `/tmp` is a
   small tmpfs, so a space-constrained `/tmp` blocks 11 upload tests via the
   disk guard (`MIN_FREE_DISK_MB`); point `TMPDIR` at a filesystem with
   ≥500 MB free instead of relaxing the guard. The torch-free QA venv can be
   built with `uv` when `python3-venv`/pip are unavailable.
4. Provenance: the 15 gate tests from the first increment were independently
   re-run and passed by a read-only reviewer agent before the follow-up
   increment (INV-LIB-005 drift gate + full-suite closure re-run) described
   in the updates above.
