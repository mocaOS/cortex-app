# Chapter 24: Apps

Apps let you install small web applications directly into your Cortex instance. Where [Web Import](23-web-import.md) brings the outside web *into* your knowledge base, Apps do the reverse: they put purpose-built interfaces and integrations *on top of* it. An app might be a custom search dashboard, a domain-specific question-and-answer front end, a form that files data into a collection, or a bridge to another piece of software you use. Each app is a self-contained bundle that an administrator installs from a zip file, and Cortex serves it, sandboxes it, and gives it carefully limited access to your Cortex API.

Apps are a way to extend Cortex without changing Cortex itself. Because the platform hosts them, users don't have to install anything or visit a separate site — the app appears right inside your instance, alongside your documents and graph.

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

In Docker deployments, mount `APPS_DIR` as a named volume (for example `apps_data:/app/.agents/apps`) so that installed apps survive container restarts.
