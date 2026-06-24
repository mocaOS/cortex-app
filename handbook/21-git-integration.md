# Chapter 21: Git Integration

Git Integration connects Cortex directly to your code and documentation repositories on **GitHub, GitLab, and Gitea** (including self-hosted instances). A connected repository becomes a living, bidirectional interface: Cortex ingests the repo's files and wiki into the knowledge graph so they're searchable and queryable, and — when you allow it — the research agent can act on the repository by opening pull requests.

This makes a repo a first-class knowledge source, sitting alongside uploaded documents and custom inputs, and opens up an array of use cases: documentation Q&A, keeping the graph current with an evolving codebase, and letting the agent propose changes back to the repo for human review.

It is disabled by default. An administrator enables it with `ENABLE_GIT_INTEGRATION=true` (see [Chapter 4: Configuration](04-configuration.md)). Once enabled, a **Git Integration** card appears on the Settings page.

## How it works

Each connection points at one repository and carries an **access level** that determines what Cortex may do with it:

- **Read-only** — Cortex clones the repository and feeds matching files (and optionally the wiki) through the normal ingestion pipeline: conversion, chunking, embedding, and entity/relationship extraction. The repo's content becomes part of your knowledge graph.
- **Read/write** — everything read-only does, plus the research agent gains a `git_repo` tool that can read live file contents and **open pull requests** with proposed changes.

Once a repository is synced, its content behaves like any other document in Cortex — it appears in Search, Ask AI, the knowledge graph, communities, and deduplication.

```
Connect repo (read-only)
  → Cortex clones it with your token
  → Matching files (.pdf/.md by default) are ingested
  → Chunked, embedded, entities + relationships extracted
  → Repo knowledge is now searchable and queryable

Connect repo (read/write)
  → All of the above, plus:
  → Agent can read files and open pull requests
  → Every change becomes a PR on a new branch for your review
```

## Connecting a repository

From **Settings → Git Integration**, click **Connect repository**:

1. **Provider** — GitHub, GitLab, or Gitea. For self-hosted GitLab/Gitea, enter the API base URL (e.g. `https://git.example.com`); leave it blank for github.com/gitlab.com.
2. **Personal access token** — paste a token (see [Choosing a token](#choosing-a-token) below). The form shows step-by-step, provider-specific instructions and a direct link to the right settings page. Click **Test** to confirm the token works ("Authenticated as …").
3. **Owner/org** and **Repository** — e.g. `mocaOS` and `cortex-skills`.
4. **Access level** — read-only (ingest) or read/write (agent can open PRs).
5. **File filter** — leave **"Only ingest .pdf and .md files"** checked (recommended), or uncheck it to define custom globs.
6. Click **Connect**, then **Sync** to ingest.

### Choosing a token

Cortex always recommends the **least-privilege** token that does the job:

| Provider | Read-only (ingest) | Read/write (open PRs) |
|---|---|---|
| **GitHub** | Fine-grained token, scoped to the repo, **Contents: Read** | Add **Contents: Read and write** + **Pull requests: Read and write** |
| **GitLab** | Project Access Token, role **Reporter**, scope `read_repository` | Role **Developer**, scopes `api` + `write_repository` |
| **Gitea** | Scoped token, **Repository: Read** | **Repository: Read and Write** + **Issue: Read and Write** |

> **GitHub wiki note:** GitHub wikis are cloned via a separate git endpoint that fine-grained tokens don't cover. To ingest a GitHub wiki, use a **classic** token with the `repo` scope.

Your token is stored securely on the server, shown only masked (`••••abcd`), injected automatically into every git and API call, and **never exposed to the agent or written to logs**.

## Controlling what gets ingested

New connections default to ingesting **`.pdf` and `.md` files only** — a safe default for documentation. To customize, uncheck **"Only ingest .pdf and .md files"**; the include/exclude glob fields appear.

Globs are gitignore-style patterns, comma-separated:

- **Include** — `src/**, docs/**, **/*.md`
- **Exclude** — `**/node_modules/**, *.lock`

Supported types are text/code files (`.py`, `.ts`, `.go`, `.md`, `.rst`, …) and documents (`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`). Code and markdown ingest through a fast path; PDFs and Office documents are converted by Docling. Images and audio are **not** ingested from repositories.

If you turn the `.pdf`/`.md` default off and have other filters defined, Cortex asks you to confirm before changing the setting so you don't accidentally discard your custom filters.

## Keeping a repository up to date

Cortex syncs **incrementally** — it never re-ingests the whole repo on every sync. It remembers the last commit it synced and uses git history to handle only what changed:

| Change in the repo | What happens in Cortex |
|---|---|
| **New file added** | A new document is created and ingested |
| **File modified** | That document is re-extracted in place (its old chunks and relationships are replaced) |
| **File deleted** | The document is **flagged for review** — never silently deleted |
| **File renamed** | The document's path is updated, no re-ingestion needed |

If a branch is force-pushed (history rewritten) or you change the file filters, Cortex automatically falls back to a full comparison so nothing is missed or stale.

After any sync that changed content, the knowledge graph is marked **stale**, prompting you to re-run relationship analysis and community detection from the [Knowledge Graph](08-knowledge-graph.md) page (Cortex doesn't auto-run these expensive steps on every sync).

### Two ways to sync

- **Manual** — click **Sync** on a connection whenever you want. A progress indicator shows clone → ingest stages.
- **Scheduled** — set an **Auto-sync** interval (in minutes) under Advanced. A background process re-syncs that connection automatically. No webhooks or public URL needed.

### Reviewing removed files

Because deletions are flagged rather than auto-applied, expand a connection to see any documents whose source file was removed from the repo. Review them and delete from the [Documents](07-documents.md) page if they're no longer wanted.

## Letting the agent act on a repository

When a connection is set to **read/write**, the research agent (in Chat and Deep Research — see [Chapter 10: Ask AI](10-ask-ai.md)) gains a `git_repo` tool that can:

- **Read** a file's current contents.
- **Propose a change** by opening a pull request.
- **Comment** on an existing pull request.

The safety model is strict and enforced in code:

- Writes **always** go onto a new `cortex/agent-…` branch and open a **pull request** — the agent never pushes to your default branch.
- Every change is a PR you review and merge yourself.
- On a **read-only** connection, the agent's write actions are refused outright.

So even with read/write enabled, nothing lands in your repository without your explicit approval via the normal PR flow.

## Editing and removing connections

Expand any connection and click **Edit** to change its access level, branch, auto-sync interval, file filters, wiki ingestion, or to **rotate the token** (leave the token field blank to keep the current one). Provider, owner, and repository can't be changed — create a new connection to point elsewhere.

**Delete** offers two choices:

- **Keep documents** — removes the connection but leaves everything it ingested.
- **Delete + purge documents** — removes the connection and all documents it created.

## Security notes

- **Single-tenant, token-per-connection.** Each connection holds its own personal access token. There's no OAuth flow; you supply a token you control.
- **Least privilege.** Use the narrowest token scope for your needs — read-only for ingestion, and only grant write/PR scopes if you want the agent to propose changes.
- **Self-hosted TLS.** For self-hosted GitLab/Gitea with self-signed certificates, an administrator can allowlist specific hosts via `GIT_HTTP_INSECURE_HOSTS`. This is opt-in and per-host; all other hosts are verified.

## Related chapters

- [Chapter 4: Configuration](04-configuration.md) — environment variables, including the git settings
- [Chapter 7: Document Management](07-documents.md) — how ingested content is managed
- [Chapter 8: The Knowledge Graph](08-knowledge-graph.md) — re-running extraction after a sync
- [Chapter 10: Ask AI](10-ask-ai.md) — querying ingested repo content and the agent
- [Chapter 17: Administration](17-administration.md) — admin settings overview
- [Chapter 18: Agent Skills](18-skills.md) — the other way to extend the agent
