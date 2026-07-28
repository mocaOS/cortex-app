# Chapter 26: Self-Hosting

The [Getting Started](03-getting-started.md) chapter builds Cortex from source — the right path for development, or for tracking `main` directly. **This chapter covers the other path**: running Cortex and Cortex Chat together from prebuilt, versioned images, for anyone who wants a production instance without cloning a repository or building anything.

Two ways to get there:

- **`npx @mocaos/cortex`** — an interactive installer that does everything below for you. This chapter documents what it does and why, so its choices aren't a black box.
- **The manual path** — copying release artifacts and editing `.env` by hand, documented in [`selfhost/README.md`](https://github.com/mocaOS/cortex-app/blob/main/selfhost/README.md) in the main repository. The installer automates exactly that procedure; the two are kept in sync, so if you ever need to step outside the installer, the manual document describes the same files it created.

Both produce the identical stack: Cortex, Cortex Chat, Neo4j, a nightly backup sidecar, and — in public-domain mode — Caddy for automatic HTTPS.

## What you need

- **Docker with the Compose v2 plugin** (`docker compose version`). On Linux, `apt install docker.io` does **not** include Compose v2 — install Docker's official packages or Docker Desktop instead. This is the only thing you install yourself; `npx` brings its own Node.
- If you already have a Node on your `PATH`, it needs to be **20.12 or newer**. This floor comes from the installer's prompt library, not from Cortex itself — an older Node, including 18, is rejected by the installer's first check, before anything is written or pulled.
- **~20 GB free disk, ~8 GB RAM**, `linux/amd64` or `linux/arm64`.
- **An OpenAI-compatible API key** — OpenAI, OpenRouter, Venice, Groq, a local Ollama, or any other endpoint that implements `/v1/chat/completions` and `/v1/embeddings`.

Images pull about **1.7 GB total**: the backend is the largest at ~1.2 GB, Neo4j is ~340 MB, and the frontend and chat images are ~70–75 MB each. Public-domain mode also pulls Caddy (~23 MB). None of this is downloaded until your LLM credentials have already been verified — see below.

## Installing

`npx @mocaos/cortex` walks through the same sequence every time:

1. **Reads the release manifest.** It fetches `stack.json` for the latest release (or a pinned one, with `--stack <version>`) and refuses to run if that release needs a newer installer than the one you're running (`npx @mocaos/cortex@latest` picks up the newer installer).
2. **Checks your environment.** Node version, Docker daemon reachability, Compose v2 version, CPU architecture, free disk, and RAM. Node, Docker, Compose, and architecture are fatal — the install stops with a clear reason if any fail.
3. **Asks how you'll reach Cortex** — localhost or a public domain — and, in domain mode, resolves both hostnames and warns if either doesn't point at this host yet (Let's Encrypt validates over HTTP, so a domain that can't resolve can't get a certificate).
4. **Picks a project name**, defaulting to `cortex`. If Docker already has volumes from an earlier project of that name on this machine, the wizard stops and offers **rename** (recommended — leaves the existing data alone), **reuse** (only if you know that data is yours), or **abort**. This matters because Neo4j only applies `NEO4J_AUTH` the first time its data volume is created — silently reusing a project name that already owns a Neo4j volume would write a freshly generated password that Neo4j never adopts, and the backend would then retry the wrong credentials until Neo4j rate-limits it.
5. **Asks your setup depth** — Quick (one provider, sensible defaults) or Advanced (per-task model overrides, resource limits, SMTP).
6. **Picks a provider and models**, offering the real model list from the endpoint's `/v1/models` when it has one, and free-text entry otherwise.
7. **Verifies your LLM credentials with two live calls** — a chat completion and an embedding call — before anything else happens. This is true on every path, interactive or `--yes`: nothing is written to disk and no image is pulled until both probes succeed. A bad key or an unreachable endpoint fails here, not after a multi-minute image pull.
8. **Records the embedding dimension** the probe reports, and warns you up front: this dimension is baked into the Neo4j vector index on first use, and changing it later requires re-embedding the corpus.
9. **Collects your admin identity and secrets** — Cortex and Cortex Chat share one identity (see [Logging in](#logging-in)) — generating all five secrets automatically unless you choose to set them yourself.
10. **Asks one privacy question**: "Send anonymous crash reports to the Cortex maintainers?" (see [Privacy and error reporting](#privacy-and-error-reporting) — it's narrower than it sounds).
11. **Writes `.env`**, fetches the matching release's Compose files, pulls images, starts the stack, and waits for every service to report healthy before printing your login.

### Non-interactive installs

`--yes` runs the identical sequence, sourced from environment variables instead of prompts:

```bash
CORTEX_ADMIN_EMAIL=you@example.com \
CORTEX_OPENAI_API_KEY=sk-... \
CORTEX_OPENAI_MODEL=gpt-5.2 \
CORTEX_EMBEDDING_MODEL=text-embedding-3-small \
CORTEX_EMBEDDING_DIMENSION=1536 \
npx @mocaos/cortex --yes
```

Add `CORTEX_MODE=domain` with `CORTEX_APP_DOMAIN`, `CORTEX_CHAT_DOMAIN`, and `CORTEX_ACME_EMAIL` for a public deployment. Secrets are generated unless you supply `CORTEX_ADMIN_PASSWORD`, `CORTEX_NEO4J_PASSWORD`, `CORTEX_ADMIN_API_KEY`, `CORTEX_SESSION_SECRET`, or `CORTEX_CHAT_ENCRYPTION_KEY`. The same LLM probes and the same project-name collision check both run here too — a collision under a project name you didn't set explicitly is a hard failure (since `--yes` can't prompt for rename/reuse/abort); setting `CORTEX_PROJECT_NAME` yourself is treated as deliberate reuse.

## The two modes

| | Localhost | Public domain |
|---|---|---|
| Cortex | `http://localhost:3000` | `https://<your app domain>` |
| Cortex Chat | `http://localhost:3001` | `https://<your chat domain>` |
| Backend API | `http://localhost:8000` | proxied through the frontend at `/api/...` |
| Neo4j Browser | `http://localhost:7474` | not published |
| TLS | none — ports are bound to `127.0.0.1` only | automatic, via Caddy |

Public-domain mode needs **both** A records pointing at the host *before* the stack starts, or certificate issuance fails. Only Caddy publishes ports in this mode; the API and Neo4j stay private on the Compose network.

## Logging in

Cortex and Cortex Chat share one identity: the admin email and password you set during install. Chat mints its own scoped backend key from the admin API key behind the scenes, so the same credentials that open `http://localhost:3000` also open `http://localhost:3001` — there's nothing separate to configure.

## Day-to-day commands

| Command | Does |
|---|---|
| `cortex status` | Service state, health, and URLs |
| `cortex logs [service]` | Follows logs, optionally scoped to one service |
| `cortex start` / `stop` / `restart` | Lifecycle (`restart` is `stop` then `start`, each waiting for health) |
| `cortex config` | Reminds you where `.env` and `.env.example` live; edit and `restart` to apply |
| `cortex doctor` | One pasteable diagnostic block: environment checks, container health, an LLM reachability probe (secrets redacted), backup freshness, and whether a newer release is available |

All of these accept `--dir <path>` if your install isn't `./cortex`; otherwise they walk up from the current directory looking for `cortex.json`.

## Updating

```bash
cortex update
```

Fetches the latest release manifest, shows you a diff of every component that would move (backend, frontend, chat, Neo4j, Caddy), and — unless already current — offers to back up first. It then re-fetches that release's Compose files and `ops/` directory (so any changes to the stack itself come along), and rewrites **only the image and version pin lines in `.env`** — comments, custom additions, and every secret are preserved byte for byte. It pulls the new images, recreates the containers, and waits for health.

If the health check times out, `update` prints the exact five pin lines to restore in `.env` to roll back (also recorded as `previous` in `cortex.json`), followed by `cortex start`.

## Backups and restore

A backup sidecar runs nightly: a verified APOC graph export plus a tar of uploads, custom inputs, chat data, skills, and apps. `cortex backup` runs one immediately; `cortex doctor` reports how old the last verified backup is.

**Restoring is six steps, not one command** — the graph restore alone does not bring back your uploads, skills, apps, or chat data, because the backup sidecar mounts those volumes read-only and cannot write them back itself:

1. List available backups and pick a timestamp — `cortex restore` prompts you with the last 20 if you don't pass one.
2. Stop the backend, since it's about to have its graph wiped and replayed underneath it.
3. Restore the graph (`RESTORE_WIPE=yes`, which `DETACH DELETE`s the whole graph before replaying the chosen export).
4. Restore the file volumes (uploads, custom inputs, chat, skills, apps) — this runs in a throwaway container instead of the sidecar, for the read-only reason above.
5. Start the backend, so it recreates every constraint and vector index the logical export doesn't carry.
6. Verify document and entity counts on `GET /api/stats`.

`cortex restore` handles the first two steps interactively (picking the timestamp, and a typed `restore` confirmation), then prints the exact remaining commands rather than running them itself — steps 3–5 need host-level `docker` access to volumes the backup sidecar can only read, so wrapping some of them and not others would leave you with a half-restored instance and no signal about it.

**Ship backups off-host.** A volume on the same disk as the instance it backs up is not disaster recovery.

## Uninstalling

```bash
cortex uninstall
```

Removes the containers; your data volumes are untouched by default. To also delete the knowledge graph, uploads, skills, apps, chat history, and every backup, you have to explicitly opt in and then type a confirmation phrase — there's no single flag that skips both checks.

## Privacy and error reporting

Error reporting is off by default, everywhere, for every self-hosted instance — not just until you configure it, but structurally: the backend and frontend have no error-reporting endpoint wired in at all unless you add one yourself, and the browser-side bundle has reporting compiled out at release build time in every published image, full stop.

The one privacy question the wizard asks — *"Send anonymous crash reports to the Cortex maintainers?"* — is narrower than it reads: it only toggles Cortex Chat's **server-side** reporting to the mocaOS team's own instance. It does not affect the backend or frontend, and it does not affect Cortex Chat's browser-side reporting either (that's the part compiled out at build time regardless). If you want the backend or frontend to report anywhere, set `SENTRY_DSN_BACKEND` / `SENTRY_DSN_FRONTEND` in `.env` yourself and point them at your own GlitchTip or Sentry — the wizard doesn't ask for this, but nothing stops you from adding it.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Install stops at "Compose v2" | `apt install docker.io` doesn't include the plugin | Install Docker's official packages or Docker Desktop |
| Install stops at "Node" | Node on `PATH` is older than 20.12 | Use a newer Node, or let a fresh shell's `npx` fetch one |
| Wizard warns about an existing project's data volumes | An earlier install (or the app's own dev Compose file) already created volumes under this project name | Choose a different project name (recommended), or only pick "reuse" if you're certain the data is yours |
| Chat probe or embedding probe fails | Wrong API key, wrong base URL, or a model name the endpoint doesn't serve | Fix the value and re-run — nothing was written, so there's nothing to undo |
| Admin login silently fails on localhost | Browser dropped a `Secure` cookie over plain HTTP | The installer's localhost mode already sets this correctly; if you hand-edited `COMPOSE_FILE` in `.env`, make sure `docker-compose.ports.yml` is still included |
| Search returns nothing after changing the embedding model | `EMBEDDING_DIMENSION` is baked into the Neo4j vector index on first use | Changing it requires re-embedding the corpus — there's no shortcut |
| A port is already in use | Another service on this host holds 3000/3001/8000/7474/7687 | The wizard detects this and offers alternate ports during install; for a running instance, edit the port lines in `.env` and `cortex restart` |

For anything else, `cortex doctor` produces one block you can paste when asking for help — it redacts your API key even if the provider's own error response would have echoed it back.

## Where to go next

- [Chapter 3: Getting Started](03-getting-started.md) — building and running Cortex from source instead of prebuilt images
- [`selfhost/README.md`](https://github.com/mocaOS/cortex-app/blob/main/selfhost/README.md) — the manual procedure this installer automates
- [Chapter 17: Administration](17-administration.md) — API keys, usage stats, and library import/export once your instance is running
- [Chapter 25: Cortex Chat](25-cortex-chat.md) — what the chat app gives your users beyond the shared login
- [Chapter 19: Troubleshooting](19-troubleshooting.md) — issues that aren't specific to self-hosting
