# Chapter 25: Cortex Chat

Cortex's own web interface is a workbench: it is where documents are managed, graphs are built, entities are deduplicated, and the instance is administered. Most of the people who *benefit* from a knowledge base never need any of that — they just want to ask questions and get grounded answers. **Cortex Chat** is the application built for them.

Cortex Chat is a standalone, multi-tenant chat frontend for any Cortex instance. It is a separate application with its own deployment, its own user accounts, and its own database — it connects to your Cortex backend purely through the [REST API](15-api-reference.md), using scoped API keys. Where the [Apps](24-apps.md) chapter describes small web apps that run *inside* your instance, Cortex Chat is the flagship example of the other kind: a full **standalone app** that lives outside the instance and treats it as a service.

The source lives at [github.com/mocaOS/cortex-chat](https://github.com/mocaOS/cortex-chat), and it is listed alongside the rest of the ecosystem on the [Cortex apps page](https://cortex.eco/apps).

## What it gives you

In one sentence: you provision an account for each team member, assign them to a group scoped to the collections they should see, and every one of them gets a clean, branded "Ask AI" experience — streaming answers, Deep Research, inline citations, and a personal chat history that follows them across devices — without ever touching Cortex itself or seeing an API key.

A few things make this more than a thin wrapper around the Ask AI endpoint:

- **It is multi-tenant.** Users, groups, roles, and per-group collection access are managed inside Cortex Chat, not in Cortex. One Cortex instance can serve many audiences, each seeing only its slice of the knowledge base.
- **It is self-contained.** Accounts are email/password with server-side sessions; everything is stored in the app's own SQLite database. Your users never need Cortex credentials.
- **It is brandable at runtime.** Accent color, logo, page title, description, support link, default chat mode, and default language (English or German) are edited by an administrator in the app's settings screen and stored in the database — changing them never requires a rebuild or restart.
- **It doubles as a lightweight library console.** Administrators get document, processing, and collection management tabs inside the app, so routine knowledge-base upkeep — including running the knowledge-graph pipeline — doesn't require opening the Cortex workbench at all.
- **It is safe by construction.** The browser never talks to the Cortex backend directly and the client bundle contains zero secrets. Every request flows through the app's server, which attaches the right scoped key on the user's behalf.

## The chat experience

The heart of the app is a single-purpose chat page backed by your instance's [Ask AI](10-ask-ai.md) pipeline:

- **Streaming answers.** Responses render token by token over Server-Sent Events, so the answer starts appearing immediately. Users can turn streaming off in the settings panel if they prefer complete answers.
- **Chat and Deep Research modes.** A toggle switches between standard chat and the agentic Deep Research mode. In Deep Research, users watch live **thinking steps**, sub-question decomposition, and retrieval progress in an auto-expanding panel while the agent works. An administrator chooses which mode every new conversation *starts* in (the **default chat mode** setting); users can still switch per conversation.
- **Inline citations.** `[src_N]` annotations in answers render as clickable numbered badges. Clicking one — or any source chip — opens a **source explorer** modal showing the full document chunk with relevance scores, so every claim can be checked against the underlying document.
- **Collection scoping.** By default a chat searches *all* collections the user's group has access to. The settings panel (gear icon) lets the user narrow to a single collection; the scope indicator in the input bar always shows what is being searched.
- **Conversation memory.** The app round-trips the backend's conversation-memory blob on every turn, so multi-turn conversations keep recall and citation continuity even beyond the backend's history window.
- **Server-side chat history.** Sessions, messages, and auto-generated titles are stored per user in the app's database. A user can start a conversation on a laptop and continue it on a phone; history appears in a slide-in sidebar.
- **Support link.** If configured, a support button appears in the chat header pointing wherever you like — a helpdesk, a mailto link, an internal wiki.
- **Multilingual UI.** The interface ships in English and German, with the default language chosen per deployment.

Every user also gets a **profile page** to change their username, avatar, and password.

## Users, groups, and roles

Cortex Chat manages its own accounts. Three roles exist:

| Role | Who | What they can do |
|---|---|---|
| **Superadmin** | Exactly one, bootstrapped from environment variables on every boot | Everything an admin can do, plus promote users to admin or demote them |
| **Admin** | Created by the superadmin or another admin | Full admin area: create users and groups, approve registrations, grant content roles, edit branding and settings, view analytics — plus the library console described below |
| **User** | The default for created accounts | Chat, browse their own history, edit their own profile. If granted a *content role*, they can also upload documents |

Every user belongs to exactly **one group**, and the group determines what they can see: each group carries a set of allowed collections (or "all collections"). When an admin creates a group, the app mints a **read-only Cortex API key scoped to those collections** and stores it encrypted; every chat request from that group's members is signed with that key. Because the Cortex backend filters results by key scope, users can only ever search and cite what their group is allowed to see — the collection picker in the chat UI never even shows anything else.

On the very first boot with a fresh database, the app automatically creates a **"Full Access" group** (a read key over all collections) and assigns the superadmin to it, so chat works out of the box without a manual trip through group setup. This happens at most once per database — groups you create or delete later are respected. (The Cortex backend may still be starting when Cortex Chat boots; the app quietly retries in the background until the group can be minted.)

### How accounts are created

There are three paths:

1. **Provisioned by an admin.** An admin creates the user at **Admin → Users** with an email and initial password, and assigns them to a group.
2. **Self-registration with approval.** A public `/register` page lets people sign up with email and password. Registrations land in a pending **Registrations** tab on the Users page; an admin approves each one by picking the group it should join. If email is configured, the applicant gets an approval notice, and admins can list addresses to be **notified whenever someone registers** (the *registration notifications* setting). Self-registration is on by default and can be disabled with `ENABLE_REGISTRATION=false`, which removes the sign-up link and the registration endpoint entirely. Someone who tries to log in while still pending gets a clear "awaiting approval" message — but only if their password is correct, so outsiders can't probe which emails have registered.
3. **Bulk import.** The repository ships a command-line script (`scripts/import-users.ts`) that bulk-creates users from an `.xlsx` spreadsheet (columns `email` and `benutzername`) against a running deployment: it logs in as the superadmin, assigns everyone to a chosen group with a shared initial password, defaults to a **dry run**, and never modifies existing accounts — re-running it is safe. See `scripts/README.md` in the repository.

Sessions are cookie-based with a 30-day sliding lifetime, and passwords are hashed with argon2id.

### Password reset and email

If SMTP is configured (see the configuration section), two email flows light up:

- **Self-service reset.** A "Forgot password?" link on the login page sends a single-use reset link valid for 60 minutes. The endpoint always reports success, so it can't be used to discover which emails have accounts.
- **Admin-triggered reset.** Admins can send a reset email to any user from the Users page.

Reset and approval emails automatically reuse your deployment's branding — accent color, logo, and app title. The superadmin's own password is deliberately excluded from email reset: it is environment-managed, and rotating it means editing the env value and restarting. Without SMTP, all email affordances are simply hidden.

## The key model — one secret, many scoped keys

This is the most important design idea in the app, and worth understanding as an administrator:

There is exactly **one** privileged credential — an admin-tier Cortex API key you place in the app's environment as `BACKEND_ADMIN_API_KEY`. It never leaves the server and is never written to the database. The app uses it as a *factory* to mint narrower keys against your Cortex backend (see [Chapter 17](17-administration.md) for Cortex's API key tiers):

| Key | Scope | Minted when | Used for |
|---|---|---|---|
| Group chat key | `read`, limited to the group's collections | An admin creates a group | Every chat and search request by that group's members |
| User content key | `manage`, limited to chosen collections | An admin grants a user a content role | That user's document uploads and web imports |

Minted keys are stored **encrypted at rest** (AES-256-GCM, using the app's `APP_ENCRYPTION_KEY`) and injected server-side as the `X-API-Key` header when the relevant user makes a request. End users never see any key — they only ever hold a session cookie. Admin operations (the library console, collection listing, key minting) use the environment admin key directly, also server-side only.

One consequence to know about: minted keys belong to *one specific backend*. If you later re-point the app at a different Cortex instance, every stored key is unknown to the new backend and chat returns authentication errors. The fix is to recreate the groups (which mints fresh keys) and reassign users — chat history survives, because it is keyed by user, not group.

## Adding and managing content

The **Upload** page (reachable from the chat header) is more than an upload form — it is a tabbed workspace whose tabs appear based on who you are.

### Upload (content-role users and admins)

An admin can grant any individual user a **content role**, which mints them a `manage`-scoped key and unlocks the Upload tab. There they can upload documents — multi-file, up to 200 MB per file, with a per-file status list and a batch summary — into the collections their role covers. Uploads are confirmed as soon as the files land; extraction runs in the background on the Cortex side and is deliberately not surfaced in this UI, so contributors are never left staring at a pipeline they don't control. Admins and the superadmin can always upload; they use the app's environment admin key rather than a minted content key.

**Web Import** appears as a second mode on the same tab if your Cortex backend has [Web Import](23-web-import.md) enabled (`ENABLE_WEB_CRAWL=true` plus a reachable crawl4ai service): paste URLs or discover same-site links, pick a content filter (Readable / Full page / Relevance-ranked), and harvest pages into a collection as markdown, with a live progress bar for the crawl. The app detects this automatically by asking the backend which features are on — there is nothing to configure in Cortex Chat itself, and the toggle is invisible when the backend doesn't support it.

### Documents (admins only)

A management view of the backend's document library: browse and filter documents by collection, see each document's processing status, **reprocess** individual documents, **process pending** uploads in one click, and **delete** documents. Documents that completed processing but produced no entities or have unembedded chunks are flagged with a *degraded* badge, and documents held back by Cortex's prompt-injection screening show an *injection-flagged* badge with the reason — so an admin can spot trouble without leaving the chat app.

### Processing (admins only)

A compact mirror of the Cortex [Knowledge Graph pipeline page](08-knowledge-graph.md): KPI tiles for documents, chunks, entities, relationships, communities, pending tasks, free disk space, and the instance's **monthly usage meter** (amber at 80%, red when exhausted) — followed by the three pipeline steps, each with its own controls:

1. **Extraction** — process pending documents.
2. **Relationships** — run batch relationship analysis, or a full rebuild.
3. **Communities** — detect communities, then summarize them.

Plus a cleanup action for orphaned entities. The tab is aware of running tasks: while the pipeline is busy, conflicting actions are blocked with an explanation of *what* is blocking them, and the buttons come back when the task finishes.

### Collections (admins only)

Create, rename, and delete collections on the backend, with document counts — the same collections that group scopes and upload targets refer to everywhere else in the app.

## The admin area

Admins and the superadmin get a separate admin area with five pages:

- **Overview** — the analytics dashboard: KPI tiles (active users, logins, messages, uploads) over a selectable time window, a time-series activity chart, and a paginated login-history table.
- **Users** — create, edit, and delete accounts; assign groups; set roles (the superadmin can promote admins); send password-reset emails; and the **Registrations** tab for approving or declining self-service signups.
- **Groups** — create and edit groups, choosing per-group collection access ("all collections" or a specific set). Creating a group mints its scoped read key behind the scenes.
- **Content Roles** — grant or revoke upload rights per user, choosing which collections each contributor may write into.
- **Settings** — everything brandable and behavioral, applied instantly with no rebuild:
  - App **title** and **description** (shown on the chat empty state)
  - **Accent color** and **logo** (upload or remove; the logo is also reused in system emails)
  - Default **language** (English/German) and default **chat mode** (Chat or Deep Research)
  - **Support link** URL and label for the chat header
  - **Registration notification** email addresses
  - The **chat analytics template** (below)

### Chat analytics injection

Beyond the dashboard, admins can define a `<cortexchatanalytics>` template in Settings. On every request, the server renders it — substituting `$userEmail` and `$userName` for the authenticated user — and injects it invisibly into the conversation sent to the backend. Users never see it, and it is never stored in chat history — but [Agent Skills](18-skills.md) running on the Cortex side *can* read it, which enables patterns like routing per-user chat summaries to a CRM or BI tool. Leave the template empty to disable injection entirely.

## Built for imperfect networks

Worth knowing as an operator, even though none of it needs configuration: Cortex Chat is written to degrade gracefully when the backend misbehaves. If the Cortex instance restarts mid-answer, the stream reconnects and regenerates transparently. Rate-limit responses (both burst limits and monthly quotas) surface as clear, localized messages — including "your monthly quota resets on <date>" rather than a nonsensical retry timer. Storage-full and oversized-upload conditions get friendly errors instead of stack traces. Every user action carries a request ID that the backend echoes, so a support question can be correlated across both apps' logs. Errors are reported to a GlitchTip (Sentry-compatible) project in production builds; set `SENTRY_DISABLED=1` to opt out entirely, or `SENTRY_DSN` to point at your own instance.

## Setting it up

### Prerequisites

- A running Cortex instance (any deployment from [Chapter 3](03-getting-started.md))
- An **admin-tier API key** (`moca_admin_...`) generated in that instance — see [Chapter 17: Administration](17-administration.md)
- Node.js 18+ if running from source, or Docker

### Configuration

All configuration is server-side and read at runtime — the same built image can serve any tenant. The required variables:

| Variable | Purpose |
|---|---|
| `CORTEX_API_URL` | URL of your Cortex backend. All traffic goes through the app's server-side proxy; the browser never calls this directly. |
| `BACKEND_ADMIN_API_KEY` | The admin-tier Cortex key used to mint per-group and per-user keys. |
| `SUPERADMIN_EMAIL` | Bootstraps the superadmin account on every server start. |
| `SUPERADMIN_PASSWORD` | Re-hashed on every boot — rotate it by editing the value and restarting. |
| `APP_ENCRYPTION_KEY` | 32 random bytes, base64-encoded (`openssl rand -base64 32`). Encrypts minted keys at rest. |

Optional:

| Variable | Purpose |
|---|---|
| `DATABASE_PATH` | SQLite file path (default `./data/cortex-chat.db`). Avatars and branding assets live alongside it. |
| `PORT` | Published port in the Docker Compose file (default `3000`). |
| `ENABLE_REGISTRATION` | Self-registration with admin approval. On by default; set `false` to disable. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_SECURE`, `SMTP_FROM` | Outbound email for password resets, approval notices, and registration notifications. Unset `SMTP_HOST` = no email features, links hidden. `SMTP_SECURE=true` means implicit TLS (port 465); `false` means STARTTLS (587). For local testing, Mailpit on port 1025 works with no auth. |
| `APP_BASE_URL` | The app's public URL, used to build links in emails. Required when SMTP is configured; reset links never trust the request's Host header. |
| `SENTRY_ENVIRONMENT`, `SENTRY_DSN`, `SENTRY_DISABLED` | Error-tracking knobs: tag events per deployment, override the built-in GlitchTip DSN, or switch reporting off. `SENTRY_AUTH_TOKEN` is a *build-time* variable that enables source-map upload so stack traces show real file names. |

The app validates its configuration at boot and refuses to start with a single error listing every problem — a missing key, a malformed encryption key, SMTP configured without `SMTP_FROM` or `APP_BASE_URL` — so a misconfigured deployment fails loudly instead of half-working. Never prefix any of these with `NEXT_PUBLIC_` — that would bake them into the client bundle.

Two legacy notes for older deployments: the deprecated names `LIBRARY_API_URL` and `NEXT_PUBLIC_API_URL` are still accepted as aliases for `CORTEX_API_URL` (with a warning), and a legacy `ACCENT_COLOR` / `NEXT_PUBLIC_ACCENT_COLOR` env var is migrated into the settings database once on first boot, after which it can be removed. Branding is deliberately **not** environment configuration: it lives in the database and is edited at **Admin → Settings** after first login.

### Deployment

The repository ships a multi-stage Dockerfile and a `docker-compose.yml` aimed at managed platforms (Coolify, Dokploy) — the same pattern used to deploy Cortex itself. The short version:

```bash
docker run -p 3000:3000 \
  -e CORTEX_API_URL=https://your-cortex-instance.com \
  -e BACKEND_ADMIN_API_KEY=moca_admin_your-admin-key \
  -e SUPERADMIN_EMAIL=admin@example.com \
  -e SUPERADMIN_PASSWORD=change-me \
  -e APP_ENCRYPTION_KEY="$(openssl rand -base64 32)" \
  -v cortex-chat-data:/app/data \
  cortex-chat
```

The container listens on port 3000 and persists all state under `/app/data` — users, groups, minted keys, chat history, avatars, and the uploaded logo — so mount a volume there. On Coolify or Dokploy, create a Docker Compose resource pointing at the repository, set the five required variables (plus any optional ones) in the platform's environment settings, and deploy; the shipped Compose file already wires everything, including the optional `SENTRY_AUTH_TOKEN` build argument for source maps. Any platform with Dockerfile builds (Railway, Render, Fly.io) works the same way.

Running from source is `npm install`, `.env.local` from the annotated `.env.example`, and `npm run dev`. Database migrations are applied automatically on every server start.

One gotcha worth calling out: `CORTEX_API_URL` must be reachable *from inside the container*. `http://localhost:8000` points at the container itself — if your Cortex backend also runs in Docker, attach the app to the backend's network and address it by service name. The [repository README](https://github.com/mocaOS/cortex-chat) covers this and other deployment variants (standalone Docker, local development against a bind mount, other platforms) in detail.

### First run, step by step

1. **Log in as the superadmin** with the email and password from the environment.
2. **Brand it** at **Admin → Settings**: accent color, logo, page title, description, default language and chat mode, support link. Changes apply immediately.
3. **Check the default group.** A "Full Access" group over all collections was created automatically. Keep it for yourself, or create purpose-scoped groups.
4. **Create groups** for each audience, choosing exactly which collections each may search. Creating a group mints its scoped read key behind the scenes.
5. **Add people** — create accounts directly, bulk-import them from a spreadsheet, or let them self-register and approve them into the right group.
6. **Optionally grant content roles** to users who should be able to upload documents or run Web Import.
7. **Optionally configure SMTP** so password resets, approval notices, and registration notifications work.
8. **Optionally set a chat analytics template** if backend skills should receive user identity with each conversation.

From that point on, day-to-day operation is entirely inside the app: users chat, contributors upload, admins manage people, tend the document library and pipeline from the library console, and watch the analytics dashboard — and Cortex itself only ever sees properly scoped API requests.

## Where to go next

- [Chapter 10: Ask AI](10-ask-ai.md) — the retrieval and Deep Research pipeline behind every answer
- [Chapter 8: The Knowledge Graph](08-knowledge-graph.md) — the pipeline the Processing tab drives
- [Chapter 17: Administration](17-administration.md) — generating the admin-tier API key Cortex Chat needs
- [Chapter 24: Apps](24-apps.md) — the other kind of app: small web apps hosted *inside* your instance
- [Chapter 16: Integration Patterns](16-integrations.md) — building your own frontend against the same API
