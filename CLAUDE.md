# Cortex — repository instructions (canonical)

Cortex ingests documents, builds a Neo4j knowledge graph, and serves RAG through
FastAPI and a Next.js frontend. This repository also owns the public documentation.
Companions: `cortex-chat` owns chat identity/history/UI; `cortex-skills` owns the
published skills, TypeScript SDK and standalone MCP server. Each has local rules.

## Every change: preserve the knowledge as well as the product

Apply this loop proportionately; an ordinary patch needs no seven-document report.

- **Start:** inspect revision and `git status`; preserve existing work. Read
  [architecture](.claude/architecture.md), then the affected guides in the
  [scoped map](.claude/navigation.md). Check the [campaign checkpoint](.claude/regeneration.md)
  when continuing maintenance or changing a cross-repo boundary.
- **Intent:** name the outcome, preserved promises, and any accepted behavior delta.
  Separate required behavior from observations, defects and proposals.
- **Compilation:** identify the unit, consumers, public boundary, state owner and
  permitted effects. Keep a local fix local; redesign a boundary explicitly.
- **Evaluations:** select meaningful checks/oracles before implementation; capture
  the baseline and freeze the gate before a replacement. Use [QA](.claude/qa.md).
  Never relax the gate to fit a candidate; isolate fault probes from shared files/state.
- **Provenance:** retain reasons, input/candidate identities, commands, results and
  limitations in existing tests/docs or a concise change record. Diagnose failures
  from the operation, version, path and event order, not a health signal alone.
- **Pace:** match checks and introduction/recovery to coupling. Persisted formats,
  auth, embeddings, public REST/SSE and independent client releases are slow layers;
  consult [upgrade/recovery](.claude/upgrade-recovery.md) before changing them.
- **Deletion:** preserve knowledge and state first. Establish consumers, supported
  version combinations and removal/recovery conditions before retiring a path.
- **Compaction:** remove justified duplication and temporary machinery when its
  exit conditions hold; keep one authoritative home for each rule and incident lesson.
- **Finish:** verify the integrated change, update canonical and affected published
  knowledge per [maintenance](.claude/maintenance.md), harvest into the owning guide,
  report failures/skips honestly, and leave the next action. A unit-test pass is not
  model-quality, replacement, restore, or production evidence.

## Production and collaboration constraints

- Preserve supported consumers and persisted state unless a behavior/migration
  change is explicitly in scope. Do not deploy, publish, commit, or mutate live
  stores merely to run a test. Use existing release authority and disposable fixtures.
- Never reset/stash another writer's work. Delegate with disjoint write paths,
  accepted inputs and exact checks; review and verify integrated results.
- Anonymize customer-facing references, comments, tests, fixtures and commits:
  no tenant/customer names, instance hostnames, real keys or IDs. Preserve the
  generic failure mechanism and record secret references rather than values.
- `bench/` is internal, not publicly documented; read its scoped guide before use.

## Navigation and instruction ownership

- [Scoped map](.claude/navigation.md): all backend/frontend/domain path-routing rules.
- [QA](.claude/qa.md): exact test commands, environment setup and gate limitations.
- [Development](.claude/development.md): build/release/self-host commands.
- [Regeneration index](.claude/regeneration.md): three-repo contracts, evidence, backlog.
- `AGENTS.md` is a minimal explicit-read adapter to this canonical file. Markdown
  links do not automatically load their targets; subtree/delegated sessions must
  read this root and the affected guide if not already delivered.
- Keep this root under 80 lines; update `.claude/navigation.md` when a guide moves
  or is added. Keep scoped guides focused (normally 50–300 lines); history belongs
  behind the index. The portable `REGENERATIVE-SOFTWARE.md` remains product-neutral.
