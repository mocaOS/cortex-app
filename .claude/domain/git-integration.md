# Git Integration (GitHub / GitLab / Gitea connector)

Native, bidirectional repo integration: ingest a repo's file-tree + wiki into the knowledge graph (read), and let the researcher agent act on it via pull requests (read/write). Single-tenant, PAT-per-connection. Gated by `ENABLE_GIT_INTEGRATION` (see [`environment.md`](../environment.md)).

## Architecture

```
GitProvider (Protocol, services/git_providers/)
 ├ GitHubProvider   api.github.com | x-access-token: clone | wiki = clone repo.wiki.git
 ├ GitLabProvider   /api/v4 | projects/MRs | oauth2: clone | wiki = Wikis API
 └ GiteaProvider    /api/v1 | token clone | wiki = Wikis API
        │
        ├ git_connector_service.py   READ → clone + diff → document_processor
        └ researcher_agent git_repo  READ/WRITE → PR via provider REST
```

The PAT lives in the provider instance / connection node and is injected server-side into every git + REST call — the LLM never sees it. It is scrubbed from logs/errors and never persisted on disk: the clone remote (repo **and** wiki) is rewritten to a tokenless URL after clone, and `.git/FETCH_HEAD` (where git records the fetch URL) is scrubbed after every fetch. Every git subprocess runs under a hard timeout (`_GIT_CMD_TIMEOUT`), and syncs are serialized per connection via an `asyncio.Lock`.

## Provider abstraction (`services/git_providers/`)

`base.py` defines the `GitProvider` ABC + dataclasses (`GitRepoRef`, `GitWriteResult`, `VerifyResult`, `WikiPage`). `get_provider(vendor, token, base_url)` (in `__init__.py`) wires timeout + TLS policy from settings. Methods: `verify`, `list_repos`, `default_branch`, `get_file_content`, `authenticated_clone_url`, `wiki_clone_url` (GitHub only; GL/Gitea return None), `list_wiki_pages` (GL/Gitea API), and writes `create_branch` / `commit_files` / `open_pull_request` / `comment`.

**GitLab specifics**: repos→projects (URL-encoded `namespace/path` id), PRs→merge_requests (`iid`), clone user literal `oauth2`, multi-file commit = single atomic `actions[]` payload.

## Data model

`(:GitConnection)` Neo4j node (CRUD in `neo4j_service.py`): `id, vendor, base_url, repo_owner, repo_name, pat, pat_last4, access_level (read|read_write), branch, default_branch, include_globs, exclude_globs, wiki_enabled, collection_id, sync_interval_minutes, last_synced_sha, last_synced_wiki_sha, last_synced_at, next_sync_due, sync_status`. Constraint on `id` + composite index `(d.git_connection_id, d.git_path)` added in `initialize_schema`.

**Document git-provenance** (optional props on `Document`, set via `store_document`): `git_connection_id, git_path, git_blob_sha, git_commit_sha, git_sync_status`. These — not filename+filesize — are the sync key. `find_git_document(connection_id, git_path)` is the keyed lookup.

PAT is stored plaintext (matches skill-secret handling) and masked in API responses as `••••{last4}`.

## Incremental sync engine (`git_connector_service.py`)

`sync_connection(connection_id, task_id, progress)` — runs as a `git_repo_sync` background task:

1. **Clone or fetch** into `{GIT_WORK_DIR}/{id}/repo` (shallow, PAT-injected URL; remote rewritten tokenless). Size guard vs `GIT_MAX_REPO_SIZE_MB`.
2. **Change set**: if `last_synced_sha` exists, is present, and is an ancestor of HEAD → `git diff --name-status -M -z` (`_diff_ops`). Otherwise (force-push / history rewrite / glob change) → **full-tree reconcile** (`_apply_fulltree`) comparing every blob sha to stored provenance. Self-healing.
3. **Filter**: `_supported` (extension ∈ `document_processor.RAW_TEXT_EXTENSIONS`) + include/exclude globs (`pathspec`) + per-file size guard.
4. **Map A/M/D/R** (`_apply_diff_ops`), reusing existing functions:
   - **A** → write content file → `process_file(..., git_provenance=...)` (pending). Idempotent: falls back to M if the doc already exists.
   - **M** → `find_git_document` → `delete_document_chunks` (clears chunks + rels) → reprocess in place; update blob/commit sha.
   - **D** → `mark_git_document_orphaned` (**flag for review — never auto-delete**).
   - **R** → `remap_git_document` (+ reprocess if `R<100`).
5. **Ingest**: staged docs are processed via `process_pending_documents` (existing concurrency control), through the raw-text fast path (see [`document-pipeline.md`](document-pipeline.md)).
6. **Staleness bump**: if anything changed, reset `last_relationship_analysis_at` / `last_community_detection_at` to the epoch sentinel `"2000-01-01T00:00:00+00:00"` so the UI flags re-extraction. (Sync ingests + embeds only; the user triggers Steps 2/3 manually.)
7. **SHA advance**: `last_synced_sha = HEAD` only on zero hard failures (else keep old SHA so the next run re-diffs and retries — A→M idempotency makes this safe).

**Wiki**: GitHub → clone `repo.wiki.git`, paths prefixed `wiki/`. GitLab/Gitea → Wikis API. Both paths use a content hash (`_wiki_sha`) as the pseudo blob sha; `_ingest_raw` skips unchanged pages, and pages removed from the wiki are flagged orphaned (the main fulltree sweep deliberately leaves `wiki/` paths to this sweep).

### Global stale-relationship fix
Relationships carry `source_document_id` but previously survived reprocess/delete unless an endpoint entity fully orphaned. `delete_relationships_by_source_document` + an inline cleanup step now run in `delete_document_chunks` and `delete_document` — fixing stale `RELATES_TO` edges for **all** documents (uploads/custom-inputs/git), not just git.

## Write tool (`git_repo`, researcher agent)

Defined in `research_prompts.py` (`GIT_REPO_TOOL`), appended by `get_tools_with_skill_activation(..., has_git=True)` only when a connection exists. Executed in `researcher_agent.py` (mirrors the `http_request` block). Actions:
- `read_file` (any access level) — live file contents via `get_file_content`.
- `propose_change` (read_write only) — **always** creates a fresh `cortex/agent-{id}` branch off the default branch, commits the files, and opens a PR/MR. Never pushes to the default branch.
- `comment` (read_write only) — comment on an existing PR/MR.

Read-only enforcement is server-side: write actions on a `read` connection return an error tool message. The agent loads the primary connection (prefers a `read_write` one) at loop start.

## Scheduled polling

A lifespan asyncio loop (`_git_sync_scheduler` in `main.py`, every `GIT_SYNC_POLL_INTERVAL` min, guarded by the flag) triggers `sync_connection` for connections whose `sync_interval_minutes > 0` and `next_sync_due <= now`, skipping any with an in-flight sync. No webhooks.

## API endpoints (`/api/integrations/git/*`, admin-gated)

`POST /verify` · `GET /browse` · `POST|GET /connections` · `GET|PATCH|DELETE /connections/{id}` (`?purge_documents=`) · `POST /connections/{id}/sync` (→ task_id, poll `GET /api/tasks/{id}`) · `GET /connections/{id}/orphaned`. Each 404s when `ENABLE_GIT_INTEGRATION` is off.

## Frontend

`components/admin/GitIntegrations.tsx` on the Settings page (gated by `config.enable_git_integration`): connect flow (provider + base_url + PAT → Test/verify → owner/repo + access-level + globs + wiki + interval), connection list with sync status + "Sync now" (polls the task), expandable details with the orphaned-documents review panel and delete (keep / purge documents).

## Deps & ops

Requires `git` in the backend image (Dockerfile) + `pathspec` (requirements). `GIT_WORK_DIR` must be writable. See [`environment.md`](../environment.md) for all env vars and [`development.md`](../development.md) for Docker.
