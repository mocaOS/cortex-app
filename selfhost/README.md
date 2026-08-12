# Self-hosting Cortex

Runs Cortex from prebuilt images, with Cortex Chat as an opt-in extra.
Everything is configured through `.env`; the Compose files are static release
artifacts you never need to edit.

> An interactive installer does all of this for you: `npx @mocaos/cortex`
> (no `install` subcommand — `npx` already fetches and runs). These are the
> manual instructions it automates, kept in sync with it.
>
> If npm answers `ENOVERSIONS`, an `.npmrc` with `min-release-age` set is
> filtering out the fresh release; run
> `npx --min-release-age=0 @mocaos/cortex` for that one command rather than
> disabling the policy.

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
cp Caddyfile.template Caddyfile        # or Caddyfile.chat.template, with chat
cp .env.example .env
chmod 600 .env
```

Edit `.env` — at minimum the secrets block and the LLM block. Generate secrets
with the commands in the comments there. Then:

```bash
docker compose up -d
docker compose ps
```

## Cortex Chat (optional)

Chat is **off by default**. To run it, set this in `.env` and `docker compose up -d`:

```dotenv
COMPOSE_PROFILES=chat
```

In domain mode, `CHAT_DOMAIN` needs its own A record pointing at this host
before you start Caddy — same as `APP_DOMAIN`, or Let's Encrypt issuance fails.
Then set `CHAT_DOMAIN` and `CHAT_BASE_URL`, add the chat origin to
`CORS_ALLOWED_ORIGINS`, and use the other Caddy template:

```bash
cp Caddyfile.chat.template Caddyfile
```

To turn chat off again, remove the `COMPOSE_PROFILES` line, then stop and
remove the container explicitly:

```bash
docker compose stop chat && docker compose rm -f chat
```

`--remove-orphans` does **not** do this, and neither does `docker compose down`:
Compose filters by profile on the way down as well as up, so with chat already
switched off in `.env` a running chat container is simply not in the set either
command acts on. (`npx @mocaos/cortex restart` does remove it, from installer
1.2.2 on — it names the profile when tearing down for exactly this reason.)

**Single Sign-On (optional).** Chat can authenticate against any OpenID
Connect IdP — Entra ID, Okta, Keycloak, Authentik, … Register a client at the
IdP with redirect URI `{CHAT_BASE_URL}/api/auth/oidc/callback`, then:

```dotenv
OIDC_ISSUER_URL=https://id.example.com/realms/yourrealm
OIDC_CLIENT_ID=cortex-chat
OIDC_CLIENT_SECRET=...
# optional: OIDC_BUTTON_LABEL, OIDC_DEFAULT_GROUP (group for first-login
# auto-provisioning), OIDC_ONLY=true (disable password login), OIDC_SCOPES
```

`CHAT_BASE_URL` must be set for SSO (it already is in domain mode). Unset
`OIDC_ISSUER_URL` = the feature is invisible. The chat superadmin
(`ADMIN_EMAIL`) always keeps its password login as break-glass access.

**Public demo mode (optional).** `DEMO_MODE=true` flips chat into a "try the
product" instance: a shared demo user is bootstrapped at boot (default
`test@test.com`/`test`, override with `DEMO_EMAIL`/`DEMO_PASSWORD`; its
password is re-hashed from env every start), the login form comes prefilled,
the demo user's chats are stored in the visitor's **browser** (localStorage)
instead of the server, its per-user mutations (password/profile/personal
personalities/projects/…) answer 403, and its chat turns are throttled per
visitor IP. Other accounts on the instance are unaffected. `DEMO_GROUP`
(group name) pins which collections the demo can search; on a public demo,
set `ENABLE_REGISTRATION=false` too. Turning `DEMO_MODE` off disarms the
demo login on the next boot. Incompatible with `OIDC_ONLY`.

Chat's data stays in the `chat_data` volume either way.

`CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` stay set even with chat off:
Compose interpolates variables before it filters profiles, so an unset value
aborts the whole project.

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
| Chat | http://localhost:3001 (only with `COMPOSE_PROFILES=chat`) |
| API | http://localhost:8000 |
| Neo4j | http://localhost:7474 |

**Public domain** — Caddy with automatic HTTPS. Point `APP_DOMAIN`'s A record
at this host *first*, or Let's Encrypt issuance fails:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
APP_DOMAIN=cortex.example.com
ACME_EMAIL=you@example.com
```

With chat on, also add — see Cortex Chat (optional) above for the second A
record it needs:

```dotenv
CHAT_DOMAIN=chat.example.com
CHAT_BASE_URL=https://chat.example.com
CORS_ALLOWED_ORIGINS=https://cortex.example.com,https://chat.example.com
```

Only Caddy publishes ports in this mode — the API and Neo4j browser stay
private on the Compose network. The API is still reachable at
`https://cortex.example.com/api/...` because the frontend proxies it.

## Logging in

Cortex and Chat share one identity (when chat is installed): `ADMIN_EMAIL` +
`ADMIN_PASSWORD`. Chat mints its scoped backend keys with `ADMIN_API_KEY`.

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
`stack.json`, then re-fetch that release's stack files as well. **New image
tags are not enough**: the Compose files and `ops/` scripts change between
releases too, and a fix that lives in a shell script arrives only this way.

```bash
docker compose exec backup /backup.sh   # back up first

rm -rf /tmp/cortex-src
git clone --depth 1 --branch "v$(jq -r .stack stack.json)" \
  https://github.com/mocaOS/cortex-app.git /tmp/cortex-src
cp -r /tmp/cortex-src/selfhost/. .
cp -r /tmp/cortex-src/ops ./ops

docker compose pull
docker compose up -d --build
```

Your `.env`, your `Caddyfile`, and any `docker-compose.override.yml` survive
that copy — the release ships `.env.example` and `Caddyfile.template`, and
carries no override file. In domain mode, if you never hand-edited
`Caddyfile`, re-copy it (`cp Caddyfile.template Caddyfile`) so changes to the
template land as well.

**`--build` is not optional.** The `backup` sidecar is built here from
`ops/backup` rather than pulled (it has no `image:`, so `pull` skips it
entirely), and Compose does not rebuild an existing image just because its
build context changed. Without `--build`, a release that ships a corrected
`backup.sh` or `restore.sh` writes it to disk and keeps running the old one —
which is exactly how the v1.0.1 restore fix would fail to reach an install
that already had a sidecar image. `npx @mocaos/cortex update` passes it for
you, and `stack.json`'s `minInstaller` is raised to require an installer that
does.

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
