# Chapter 24: Apps

Apps let you install small web applications directly into your Cortex instance. Where [Web Import](23-web-import.md) brings the outside web *into* your knowledge base, Apps do the reverse: they put purpose-built interfaces and integrations *on top of* it. An app might be a custom search dashboard, a domain-specific question-and-answer front end, a form that files data into a collection, or a bridge to another piece of software you use. Each app is a self-contained bundle that an administrator installs from a zip file, and Cortex serves it, sandboxes it, and gives it carefully limited access to your Cortex API.

Apps are a way to extend Cortex without changing Cortex itself. Because the platform hosts them, users don't have to install anything or visit a separate site — the app appears right inside your instance, alongside your documents and graph.

There is also a second, complementary kind of app: **standalone apps**, full applications that run *outside* your instance — with their own deployment and their own users — and connect to it through the REST API with scoped keys. The flagship example is Cortex Chat, the multi-tenant chat frontend, covered in [Chapter 25](25-cortex-chat.md). This chapter is about the in-instance kind.

Apps are **disabled by default**. An administrator turns the whole subsystem on by setting `ENABLE_APPS=true` (see [Chapter 4: Configuration](04-configuration.md)). While it is off, there is no trace of the feature at all: every app-related web address returns "not found", and the Apps section never appears in Settings. Nothing changes for anyone until an administrator deliberately enables it.

## Where apps come from

You don't have to build apps by hand. Apps are created from the open [**cortex-app-template**](https://github.com/mocaOS/cortex-app-template), which provides everything a developer needs: the manifest format, a validator, and a working example to start from. Developers (or AI coding assistants working on their behalf) can follow the **builder skills** published at [cortexskills.org/builder](https://cortexskills.org/builder/SKILL.md), which explain step by step how to scaffold, configure, and package a Cortex app. The end result is always the same: a single `.zip` package that an administrator installs.

This chapter is written for the people who **run** a Cortex instance and the people who **use** the apps installed in it — not for app developers, who should follow the template and builder skills above.

## How apps work

When an app is installed and enabled, this is what happens behind the scenes:

- Cortex serves the app's files at a private web address inside your instance.
- The app runs inside a **sandbox** — a locked-down frame that cannot see your Cortex login, your cookies, or the rest of the interface.
- Each app is given its own **dedicated API key**, created just for it at install time. This key is never handed to the browser; it stays on the server.
- When the app needs data from Cortex, its request passes through a **proxy** that checks the request against a list of endpoints the app is allowed to use, and only then attaches the key. Anything outside that list is refused.

The practical upshot: an app can do exactly what it was declared to do, and nothing more. It cannot quietly reach parts of Cortex it wasn't granted, and it never holds a credential that would work anywhere except through its own proxy.

## Installing an app (for administrators)

Installation happens on the **Settings → Apps** page, which only appears when the feature is enabled.

1. **Upload the package.** Choose the app's `.zip` file. Cortex checks its size and inspects it for unsafe contents before writing anything to disk.
2. **Validation.** Cortex reads the app's manifest and validates it. If something is wrong, the install is refused and every problem is listed at once — the app is never left half-installed.
3. **Choose what the app can access.** During install you set the app's access scope. The manifest declares whether the app needs read-only or read-and-write access; you decide which **collections** it may touch, using a collection picker. You can grant all collections or restrict the app to a chosen few. Those two choices define the minted key's power precisely.
4. **Configure it, if needed.** Some apps require settings — an API token for another service, a base URL, and so on. These are filled in through a setup wizard, the same one used for [Agent Skills](18-skills.md). Secret values such as tokens are masked in the interface and stored encrypted.
5. **Enable it.** Apps arrive switched off. Turn the app on once you're happy with its configuration, and it becomes available to users.

Upgrading an app (installing a newer package for one you already have) keeps its key, its saved configuration, and any share links intact, so an upgrade won't break links or force you to reconfigure. Deleting an app revokes its dedicated key.

## Installing from the registry

Instead of handling zip files, you can install apps straight from the public
Cortex Registry. In **Settings → Apps**, open **Browse Registry**: every
listed app shows what it will be allowed to do — its key scope, the exact
API endpoints it may call, and any server-side capabilities — before you
install it.

Registry installs are integrity-checked twice over: the registry pins every
app package by checksum and its automation re-verifies packages continuously,
and your own instance verifies the downloaded package against that pinned
checksum again before unpacking it. If the file on the internet has changed
in any way since it was reviewed, the install refuses. When a newer version
of an installed app is listed, the same panel offers a one-click update that
keeps your configuration, share links, and stored data.

The registry location is configurable (`APP_REGISTRY_URL`) — organizations
can point their instances at a private fork to curate exactly which apps
their admins may install.

## The app catalog

Eight first-party apps are published in the registry today, and the set grows over time — browse the live catalog at [registry.cortex.eco](https://registry.cortex.eco). All of them are **platform** apps: once configured, they do their work on the server, on demand or on a schedule, with no browser open anywhere. Each one writes into the collection you choose at install time, remembers what it has already transferred (so re-runs are cheap and nothing is ingested twice), and keeps its credentials encrypted on the server.

### Bringing your documents in

Seven of the apps are sync bridges: they watch a place where your documents already live and keep your knowledge base up to date with it.

- **Paperless Sync** — mirrors a [paperless-ngx](https://docs.paperless-ngx.com/) document archive into Cortex, transferring the full original documents. This is the natural companion for anyone who scans their paperwork: the archive keeps being the archive, and Cortex makes all of it answerable. Needs your paperless server's address and an API token.
- **Dropbox Sync** — syncs selected Dropbox folders. You sign in to Dropbox through its standard consent screen; the app never needs a Dropbox app secret.
- **Google Drive Sync** — syncs Google Drive folders with no consent screens at all: you create a Google service account once, then simply *share* the folders you want synced with the service account's email address, like sharing with a colleague.
- **OneDrive Sync** — syncs OneDrive folders. Sign-in uses Microsoft's device-code flow against a free Entra app registration, and the app tracks changes through Microsoft's delta mechanism, so after the first run only new and changed files transfer.
- **SharePoint Sync** — syncs SharePoint document libraries for organizations, using app-only access of the most restrictive kind Microsoft offers (`Sites.Selected`): the app can reach exactly the sites an admin has granted, and no user ever signs in.
- **Nextcloud Sync** — syncs folders from a Nextcloud server, authenticated with an app password rather than your account password.
- **WebDAV Sync** — the universal fallback: syncs folders from *any* WebDAV server — Synology and QNAP NAS boxes, Koofr, pCloud, GMX/WEB.DE, MagentaCLOUD, HiDrive, Seafile, Hetzner Storage Boxes, and more.

### Turning media into knowledge

- **YT Transcriber** — turns YouTube videos, or whole channels at once, into clean transcripts inside your knowledge graph. Transcription runs through a Venice AI key you provide; the raw transcript is then refined by your instance's own language model before being saved, so what lands in your collections reads like a document, not like subtitles.

Every app in the catalog declares — visibly, in the registry panel, before you install — exactly which Cortex endpoints it may call, which external services it talks to, and what configuration it needs. The sync apps ask for read-and-write access because they upload documents; all of them can be confined to a single collection, which is the recommended setup — one collection per source (e.g. `Paperless`), so each app's documents stay cleanly separated (see [Chapter 11: Collections](11-collections.md)).

## Using an app (for everyone)

Enabled apps show up in a **launcher grid** — a page inside your instance that lists every app available to you, each with its icon. Click one and it opens in its own sandboxed view. From your point of view it simply works: you interact with the app, and it talks to your Cortex knowledge base in the background. You never have to copy or paste an API key, and nothing you do in the app can reach outside the bounds the administrator set.

## Sharing an app with people outside Cortex

Some apps are meant for an audience that doesn't have a Cortex login at all — a client, a colleague on another team, the general public. For apps that permit it, an administrator can create a **share link**.

A share link opens the app in a stripped-down, login-free view. The person who follows it can use the app, but that is *all* they can do:

- They never see the Cortex administration interface, the main API, or any other app.
- Their access is limited to exactly what the app itself exposes.
- The administrator can **revoke** a share link at any time, which immediately cuts off anyone using it — even sessions already in progress.
- Links can be marked view-only or allowed to make changes, and can be set to expire.

This makes an app a controlled window onto a single, deliberately chosen slice of your knowledge base — safe to hand to someone who should see that slice and nothing else.

## App classes and platform capabilities

Most apps are **static**: they run in the browser and talk only to Cortex. A more capable kind, called a **platform** app, can also do work on the server side. The most useful of these is the ability to call **external** services.

When a platform app makes an external call, Cortex makes it *for* the app, from the server, and attaches any needed credentials from the app's saved configuration. This has two big advantages over a browser making the call itself:

- **The other service needs no special setup.** Browser-to-service calls are often blocked by a web security mechanism called CORS; because Cortex makes the call from its server instead, that problem simply doesn't arise.
- **Credentials stay hidden.** The token or key for the external service lives in the app's encrypted configuration and is only ever used on the server. It never reaches the browser or the app's visible code.

This is the recommended way to integrate other software with Cortex. A platform app can also read back its own non-secret settings at runtime; secret settings are never exposed to it.

Platform apps have three further abilities:

- **Storing data.** An app can keep its own data — sync progress, results, user preferences — in a private, size-limited store inside your instance. Each app's store is completely separate from every other app's, and it is deleted when the app is uninstalled. This is what lets an app remember things across sessions and devices, instead of only inside one browser.
- **Running background work.** An app can hand Cortex a list of jobs to run **on the server** — fetching from an external service, calling the language model, saving results. Once submitted, the work keeps running even if you close the tab, and you can pause, resume, cancel, or retry it from the app. If the Cortex server restarts mid-run, unfinished work resumes automatically.
- **Running on a schedule.** A background job can repeat at a fixed interval (for example, every hour). This turns an integration app into a true sync: a paperless-ngx app, say, can pull newly added documents into Cortex around the clock, with no browser open anywhere. The app's language-model use is counted against the instance's normal usage quota, and sensible caps keep any single app from monopolizing a small server.

A third class of app, called a **service** app, ships its own container and runs separately; Cortex does not host those, and they are outside the scope of this chapter.

## Why apps are safe to run

Everything about the Apps subsystem is built so that installing an app — and even sharing one with strangers — is a bounded, reversible decision:

- **No real key ever reaches the browser.** The app's dedicated key stays on the server. The browser only ever holds a short-lived token that stops working after a few minutes and is automatically renewed while the app is open.
- **The sandbox is strict.** An app runs isolated from the rest of Cortex and cannot read your session or cookies.
- **Access is on a leash.** Every call the app makes to Cortex is checked against the endpoints its manifest declared, and scoped to the collections you chose at install time. Background jobs pass exactly the same checks as live calls — there is no privileged "server mode".
- **Visitors can look, not touch.** Someone using an app through a share link can read what the app shows them, but cannot change the app's stored data or start background work unless the link was created with editor rights.
- **You can pull the plug.** Disabling or deleting an app, or revoking a share link, takes effect immediately — deletion also stops any background work the app had running.

## Configuration reference

These settings, placed in the backend environment, control the Apps subsystem. Only the first is required to turn it on.

| Setting | Default | Purpose |
|---|---|---|
| `ENABLE_APPS` | `false` | Master switch. Off = all app routes return "not found" and the admin section is hidden. |
| `APPS_DIR` | `.agents/apps` | Where installed app bundles are stored. Persist via a Docker volume in production. |
| `APP_MAX_PACKAGE_MB` | `50` | Largest app package (zip) that can be uploaded; uncompressed size is capped at 4× this. |
| `APP_TOKEN_TTL_SECONDS` | `900` | How long the short-lived app tokens last (15 minutes) before renewal. |
| `APP_PROXY_UPSTREAM` | `http://127.0.0.1:8000` | Where the app proxy forwards allowed Cortex API calls (the instance itself). |
| `APP_HTTP_TIMEOUT` | `30` | Timeout, in seconds, for a platform app's server-side external calls. |
| `APP_STORAGE_MAX_MB` | `50` | How much data one app may keep in its private store. |
| `APP_STORAGE_MAX_VALUE_KB` | `1024` | Largest single item an app may store. |
| `APP_TASK_MAX_ITEMS` | `2000` | Most items one background job may contain. |
| `APP_TASK_MAX_CONCURRENCY` | `4` | How many of a job's items may run at once. |
| `APP_TASKS_GLOBAL_CONCURRENCY` | `8` | Server-wide ceiling on simultaneously running job items, across all apps. |
| `APP_TASK_MIN_SCHEDULE_MINUTES` | `15` | Shortest allowed repeat interval for scheduled jobs. |
| `APP_TASK_LLM_CALLS_PER_RUN` | `500` | Most language-model calls one job run may make. |
| `APP_TASK_STEP_OUTPUT_MAX_KB` | `2048` | Largest intermediate result a job step may produce. |
| `APP_TASK_MAX_PER_APP` | `50` | Most stored jobs per app; old finished ones are cleaned up first. |
| `APP_REGISTRY_URL` | official registry | Where the Browse Registry panel gets its catalog; empty hides it. |

In Docker deployments, mount `APPS_DIR` as a named volume (for example `apps_data:/app/.agents/apps`) so that installed apps survive container restarts.
