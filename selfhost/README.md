# Self-hosting Cortex

Runs Cortex and Cortex Chat from prebuilt images. Everything is configured
through `.env`; the Compose files are static release artifacts you never need
to edit.

> An interactive installer that does all of this for you is coming as
> `npx @mocaos/cortex`. These are the manual instructions it automates.

## Requirements

- Docker Engine 24+ with the **Compose v2 plugin** (`docker compose version`).
  `apt install docker.io` does *not* include it — use the official Docker
  packages or Docker Desktop.
- `git` and `jq` — the install and update commands below use both, and
  neither is guaranteed present on a minimal server image.
- `openssl` — `.env.example`'s secret-generation commands use it, for the
  same reason `git`/`jq` are listed here.
- ~20 GB free disk, ~8 GB RAM.
- `linux/amd64` or `linux/arm64`.
- An OpenAI-compatible API key.

## Install

```bash
curl -fsSL https://github.com/mocaOS/cortex-app/releases/latest/download/stack.json -o stack.json

git clone --depth 1 --branch "v$(jq -r .stack stack.json)" \
  https://github.com/mocaOS/cortex-app.git /tmp/cortex-src

mkdir -p cortex && cd cortex
cp -r /tmp/cortex-src/selfhost/. .
cp -r /tmp/cortex-src/ops ./ops
cp Caddyfile.template Caddyfile
cp .env.example .env
chmod 600 .env
```

Edit `.env` — at minimum the secrets block and the LLM block. Generate secrets
with the commands in the comments there. Then:

```bash
docker compose up -d
docker compose ps
```

## Modes

`COMPOSE_FILE` in `.env` selects the mode.

**Localhost** (default) — ports on `127.0.0.1` only:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml
```

The session cookie's `Secure` flag is handled for you: the ports overlay sets
`SESSION_COOKIE_SECURE=false` (browsers drop `Secure` cookies on plain HTTP),
and domain mode leaves it unset so the app defaults to secure. Nothing to set.

| | |
|---|---|
| Cortex | http://localhost:3000 |
| Chat | http://localhost:3001 |
| API | http://localhost:8000 |
| Neo4j | http://localhost:7474 |

**Public domain** — Caddy with automatic HTTPS. Point both A records at this
host *first*, or Let's Encrypt issuance fails:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
APP_DOMAIN=cortex.example.com
CHAT_DOMAIN=chat.example.com
ACME_EMAIL=you@example.com
CHAT_BASE_URL=https://chat.example.com
CORS_ALLOWED_ORIGINS=https://cortex.example.com,https://chat.example.com
```

Only Caddy publishes ports in this mode — the API and Neo4j browser stay
private on the Compose network. The API is still reachable at
`https://cortex.example.com/api/...` because the frontend proxies it.

## Logging in

Cortex and Chat share one identity: `ADMIN_EMAIL` + `ADMIN_PASSWORD`. Chat
mints its scoped backend keys with `ADMIN_API_KEY`.

## Customizing

Put local changes in `docker-compose.override.yml` — Compose merges it
automatically and updates never touch it. Do not edit `docker-compose.yml`.

**Do not rename the `backend` service.** The published frontend image bakes
`API_URL=http://backend:8000` into its Next.js rewrite manifest at build time.

## Updating

```bash
curl -fsSL https://github.com/mocaOS/cortex-app/releases/latest/download/stack.json -o stack.json
```

Update the three `CORTEX_*_IMAGE` lines in `.env` to the versions in
`stack.json`, then:

```bash
docker compose exec backup /backup.sh   # back up first
docker compose pull
docker compose up -d
```

## Backups

The `backup` sidecar runs nightly: a verified APOC graph export plus a tar of
uploads, custom inputs, chat data, skills and apps. It goes unhealthy if the
newest verified backup is older than two intervals.

```bash
docker compose exec backup /backup.sh                        # run now
docker compose exec backup ls /backups                       # list
```

Backups live in the `backups` named volume. **Ship them off-host** — a volume
on the same disk is not disaster recovery.

**Restoring is six steps, not one command** — the graph restore alone leaves
your uploads, skills, apps and chat data untouched. The authoritative runbook
is the header comment in `ops/backup/restore.sh`; this is the same procedure:

```bash
# 1. List available backups and pick a <timestamp>.
docker compose exec backup ls /backups

# 2. Stop the backend — it's about to have its graph wiped and replayed
#    underneath it.
docker compose stop backend

# 3. Restore the graph. RESTORE_WIPE=yes is required: this DETACH DELETEs the
#    whole graph — and drops its constraints and indexes, which the replay
#    recreates — before replaying the chosen backup's export. Vector indexes
#    are left in place; the export doesn't carry them and the backend rebuilds
#    them at startup, so dropping them would force a pointless re-embed.
docker compose exec -e RESTORE_WIPE=yes backup /restore.sh <timestamp>

# 4. Restore the file volumes (uploads, custom_inputs, chat, skills, apps).
#    The backup sidecar mounts these read-only, so it cannot write them back
#    itself — this runs in a throwaway container instead. Volume names are
#    ${COMPOSE_PROJECT_NAME}_<name>; .env.example sets COMPOSE_PROJECT_NAME=cortex,
#    so a default install uses the cortex_* names below (use your own prefix
#    if you changed COMPOSE_PROJECT_NAME).
docker run --rm \
  -v cortex_uploads_data:/data/uploads \
  -v cortex_custom_inputs_data:/data/custom_inputs \
  -v cortex_chat_data:/data/chat \
  -v cortex_skills_data:/data/skills \
  -v cortex_apps_data:/data/apps \
  -v cortex_backups:/backups:ro \
  alpine tar -xzf /backups/<timestamp>/files.tar.gz -C /

# 5. Start the backend. Startup recreates every constraint/index, including
#    the vector indexes the logical export does not carry.
docker compose start backend

# 6. Verify document/entity counts on GET /api/stats.
```

## Troubleshooting

**Admin login silently fails on localhost.** Browsers drop `Secure` cookies on
non-TLS origins. The ports overlay sets `SESSION_COOKIE_SECURE=false` for you,
so check that `COMPOSE_FILE` actually includes `docker-compose.ports.yml` — a
`.env` listing only the base file hits exactly this symptom.

**Chat login silently fails after changing `BIND_ADDR` to a LAN address.**
Unlike Cortex's frontend, Cortex Chat has no `SESSION_COOKIE_SECURE`
equivalent — its session cookie is always `Secure` in the published image.
That works fine at `http://localhost:3001` (browsers treat `localhost` as a
trustworthy origin and send `Secure` cookies there even over plain HTTP), but
if you point `BIND_ADDR` at a LAN IP and reach chat over plain HTTP instead of
`localhost`, the browser silently drops the cookie and login appears to do
nothing. Put chat behind TLS (domain mode) if you need LAN/remote access.

**Backend exits at boot with "Insecure configuration for ENVIRONMENT=production".**
A secret is still a placeholder. Regenerate it with the commands in
`.env.example`.

**Documents fail to process.** Check `docker compose logs backend`. The most
common cause is an unreachable LLM endpoint or a wrong `OPENAI_MODEL`.

**Search returns nothing after changing the embedding model.**
`EMBEDDING_DIMENSION` is baked into the Neo4j vector index on first use.
Changing it requires re-embedding the corpus.
