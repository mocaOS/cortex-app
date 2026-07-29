# Optional Cortex Chat — design

**Date:** 2026-07-29
**Status:** approved
**Repos touched:** `mocaOS/cortex-app` (`selfhost/`, docs), `mocaOS/cortex-installer`

## Goal

Let someone installing Cortex choose whether Cortex Chat is installed alongside it, or run Cortex alone with no external chat. **Default off.**

Today the released stack always runs six services and the installer always waits for chat to become healthy. Chat is a genuinely optional companion — nothing in the backend or frontend references it (verified: no consumer of `CHAT_BASE_URL` in `backend/app/config.py`, `backend/app/main.py`, or anywhere under `frontend/src`) — so "off" needs to mean *not present*, not *present but idle*.

## Decisions

| Question | Decision | Why |
|---|---|---|
| How to exclude the service | Compose **profile** (`profiles: ["chat"]`) | One `.env` line toggles it; no new compose files; the `docker-compose.ports.yml` chat entry is excluded with it automatically |
| Domain-mode Caddyfile | **Two templates** | An optional glob `import` also works but logs `No files matching import glob pattern` on every start, and chat-off is the default — a permanent warning for the majority case |
| Switching after install | **Documented `.env` edit + `cortex restart`** | One line in localhost mode; no new verb to build and test |
| Chat-only config when off | **Write the key and port, skip the prompts** | Makes that documented switch genuinely one line |

A `cortex chat on|off` verb was considered and deferred. It is the nicer UX and remains an obvious follow-up, but it needs domain prompting, key generation, Caddyfile swapping and its own tests — none of which the `.env` route requires.

## Verified mechanism facts

These were established experimentally against real Compose and Caddy. They are load-bearing: each one, if assumed the other way, produces a broken stack.

1. **A profiled service's overlay entries are excluded with it.** `docker-compose.ports.yml` declares `chat:` with a published port. With the profile off, `docker compose config` emits one published port; with it on, two. So the ports overlay needs no change.
2. **`depends_on` on a profile-disabled service is a hard error** — `service "caddy" depends on undefined service "chat": invalid compose project`. The fix is the long form with `required: false`, which Compose accepts and which resolves cleanly with the profile both on and off.
3. **Interpolation runs before profile filtering.** `${CORTEX_CHAT_IMAGE:?...}` on the profiled `chat` service *still* fails when the profile is off. Consequence: every `${VAR:?}` that chat's own definition references must remain satisfied even when chat is not installed. This is why `.env` keeps writing `CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` — it is not merely a convenience.
4. **Caddy's own service requires `CHAT_DOMAIN`**, independently of chat: `CHAT_DOMAIN=${CHAT_DOMAIN:?CHAT_DOMAIN is required in domain mode}` sits in the `caddy` environment block. It must relax to `${CHAT_DOMAIN:-}`.
5. **A Caddyfile with an unset site address does not merely warn — it fails to adapt**: `server block without any key is global configuration, and if used, it must be first`. An app-only domain install therefore cannot keep the `{$CHAT_DOMAIN}` block at all; Caddy would not start.

## Changes: `cortex-app/selfhost/`

**`docker-compose.yml`** — the `chat` service gains:

```yaml
    profiles: ["chat"]
```

**`docker-compose.caddy.yml`** — two edits to the `caddy` service:

```yaml
      environment:
        - CHAT_DOMAIN=${CHAT_DOMAIN:-}        # was ${CHAT_DOMAIN:?...}
      depends_on:
        frontend:
          condition: service_started
        chat:
          condition: service_started
          required: false
```

Relaxing `CHAT_DOMAIN` loses a clear Compose-level error for a domain-mode-with-chat user who forgets it. Accepted: the installer always writes it when chat is on, `.env.example` documents it, and the failure that remains (Caddy refusing to adapt the chat template with an empty address) is loud, if less friendly. Compose cannot express "required only when this profile is active".

**`Caddyfile.template`** — the `{$CHAT_DOMAIN}` block is removed. This file becomes the app-only config.

**`Caddyfile.chat.template`** — new; the current content, i.e. the global block plus both site blocks.

**`.env.example`** — documents the opt-in and reflects the new default:

```dotenv
# Cortex Chat is optional and OFF by default. To run it, uncomment this and
# `docker compose up -d`. In domain mode also set CHAT_DOMAIN + CHAT_BASE_URL,
# add the chat origin to CORS_ALLOWED_ORIGINS, and use Caddyfile.chat.template.
# COMPOSE_PROFILES=chat
```

`CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` stay uncommented with a note that Compose reads them whether or not chat runs (fact 3).

## Changes: `cortex-installer`

### Wizard (`src/wizard.ts`)

A `p.confirm` with `initialValue: false`, placed **immediately after the mode question and before the domain prompts**, because `CHAT_DOMAIN` must only be asked when chat is on:

> `Also install Cortex Chat? (a separate chat front end)` — default **No**

When off it also suppresses the chat-only prompts: `CHAT_DOMAIN` in domain mode, and — in Advanced — `Configure SMTP for chat password reset?`. Those configure chat and nothing else. (There is no registration prompt to suppress: `ENABLE_REGISTRATION` has zero references in the installer and is never written, so the chat image's own default applies.)

`buildConfigNonInteractive` reads `CORTEX_ENABLE_CHAT`; anything other than `"true"` means off, matching how `CORTEX_ERROR_REPORTING` already behaves.

`InstallConfig` gains `chat: boolean`.

### Env rendering (`src/env.ts`)

| Variable | chat on | chat off |
|---|---|---|
| `COMPOSE_PROFILES=chat` | written | omitted |
| `CORTEX_CHAT_IMAGE` | written | **written** (fact 3) |
| `CHAT_APP_ENCRYPTION_KEY` | written | **written** (fact 3 + one-line switch) |
| `CHAT_PORT` (localhost) | written | written (one-line switch) |
| `CHAT_DOMAIN`, `CHAT_BASE_URL` (domain) | written | omitted |
| chat origin in `CORS_ALLOWED_ORIGINS` | included | excluded |
| `SMTP_*`, `ENABLE_REGISTRATION` | as collected | omitted |

Chat-off installs get a comment above the retained variables explaining that they are pre-seeded so enabling chat is a single line, and that Compose reads the image and key regardless.

`REQUIRED_VARS` drops `CHAT_DOMAIN`, mirroring the compose change. It keeps `CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY`.

### Health gating (`src/docker.ts`)

`healthServices` currently hardcodes `["neo4j", "backend", "frontend", "chat"]`. It must take chat into account — waiting for a service that will never be created is a 300-second hang ending in a false failure. Signature becomes `healthServices(mode, chat: boolean)`.

### Artifacts (`src/artifacts.ts`)

`ARTIFACT_FILES` gains `Caddyfile.chat.template`, so a release missing it fails loudly rather than at Caddy start. The existing `Caddyfile.template → Caddyfile` copy becomes conditional on the chat choice, keeping its current rationale intact (a missing `Caddyfile` makes Docker create a root-owned *directory* and Caddy crash-loops on "is a directory").

### State (`src/state.ts`)

`InstallState` gains `chat: boolean`, so later commands know what was installed without re-parsing `.env`.

### Ports preflight (`src/commands/install.ts`)

`portsToCheck` omits the chat port when chat is off — otherwise a port that nothing will bind can abort the install under `--yes`.

### Final summary box (`src/commands/install.ts`)

The localhost URL list drops the `Chat` line when off.

### Deliberately unchanged

`status`, `doctor` and `formatStatusTable` are driven by `docker compose ps` output, so an absent chat service simply produces no row. `uninstall` enumerates volumes by project prefix, so `chat_data` is handled either way. `update.ts`'s `IMAGE_LINES` keeps repinning `CORTEX_CHAT_IMAGE` — required by fact 3, and it keeps a later opt-in on a tested version.

## Migration: existing installs must not silently lose chat

Every install to date runs chat. Applying the new stack without care means the profile is absent and **chat disappears on the next `update`** — data intact in `chat_data`, but the service gone with no explanation.

Two guards, both required:

1. **`update` back-fills the profile.** An install whose `cortex.json` has no `chat` field predates this feature and was therefore running chat. `update` treats that as chat-enabled: it writes `chat: true` into the state and `COMPOSE_PROFILES=chat` into `.env`, preserving what is running. A `cortex.json` that *does* carry the field is honoured as-is.
2. **`minInstaller` rises to the installer version that ships this** — a minor bump, `1.2.0` at time of writing, since this adds a feature. An older installer applying the new stack would not know to add that line, so it must be refused rather than allowed to drop a service. Set in `selfhost/stack.template.json`; the exact value is whatever the installer release actually carries.

## Documentation

Each of these currently states that the stack includes chat, and becomes wrong:

- `selfhost/README.md` — chat as opt-in; both Caddyfile templates; the enable/disable steps
- `handbook/26-self-hosting.md` — the wizard sequence and the "identical stack" claim
- `documentation/pages/guides/deployment.mdx` — the installer section's service list and the manual path's "runs Cortex **and Cortex Chat**"
- `README.md` — the Quick Start and Self-hosting blurbs
- `documentation/pages/changelog.mdx` — a July 29 entry (the day's entry already exists; this appends a section rather than a second `##`)
- `.claude/development.md` — the profile mechanism and the `minInstaller` coupling, for maintainers

## Testing

**Unit (installer):** env rendering asserts the exact variable sets in both states, including that `CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` survive chat-off and that the chat origin leaves `CORS_ALLOWED_ORIGINS`; `healthServices` for all four mode × chat combinations; `CORTEX_ENABLE_CHAT` parsing; the update back-fill for a `cortex.json` with and without the `chat` field.

**Compose contract:** `docker compose config` against the real files asserting chat is absent by default and present with `COMPOSE_PROFILES=chat`, in both modes — this is the regression net for facts 1–4, each of which is invisible to unit tests.

**Caddy:** `caddy validate` both templates against `caddy:2-alpine`, app-only with `CHAT_DOMAIN` unset (fact 5) and the chat template with it set.

**Live:** four installs — localhost/domain × chat on/off, verifying service count, health, and reachable URLs; plus an update from a chat-enabled pre-migration install confirming chat survives.

## Out of scope

- A `cortex chat on|off` verb (deferred; see Decisions)
- Any change to cortex-chat itself, or to its release cadence
- Removing chat from the `stack.json` manifest — it stays pinned so an opt-in gets a tested version
- Migrating an existing install *away* from chat (the `.env` route covers it; no automation)
