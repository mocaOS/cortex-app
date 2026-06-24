# MDHarvest powered by Crawl4ai (web → markdown)

The "Web Import" feature: harvest URLs into clean markdown and ingest them as normal documents (chunk → embed → entity/relationship extraction → KG). It supersedes the standalone, now-deprecated `mdharvest` tool. Gated by `ENABLE_WEB_CRAWL` + `CRAWL_SERVICE_URL` (see [`environment.md`](../environment.md)).

**Core principle:** cortex-app never embeds a browser/crawler stack. It speaks [crawl4ai](https://github.com/unclecode/crawl4ai)'s native REST API over HTTP. One code path, two deployments — only env differs:

- **self-host** — `CRAWL_SERVICE_URL` → the user's own crawl4ai (`:11235`). They own their data.
- **cloud (AaaS)** — `CRAWL_SERVICE_URL` → the shared per-host crawl4ai (one container per server, many tenant stacks), set by the operator in env the customer never sees. Hosted in the `cortex-helper` repo's compose.

Empty URL ⇒ feature off. There is **no** in-process fallback — that ~4 GB browser stack is exactly the per-tenant footprint the shared-service model exists to avoid.

## Architecture

```
cortex-app                                  crawl4ai service (own / shared)
 ┌────────────────────────────┐             ┌──────────────────────────────┐
 │ crawl_client.py            │  POST /md   │ /md    → clean markdown        │
 │  crawl_markdown(url)  ──────┼────────────▶│         (f=fit readability)    │
 │  discover_links(url)  ──────┼── POST /crawl ▶ /crawl → result incl. links  │
 │   retries · breaker · auth │             │ browser pool · ephemeral · no  │
 │   cache-bypass (c="0")     │             │ persistent volume · no Redis   │
 └──────────────┬─────────────┘             └──────────────────────────────┘
                │ markdown
                ▼
 main.py  POST /api/web-import          → background _run_web_import_task
          POST /api/web-import/discover → synchronous link discovery
                │
                ▼  store_file_only(source="crawl:<host>")  → process_pending_documents()
          document pipeline (raw-text fast path; see document-pipeline.md)
```

## Client (`services/crawl_client.py`)

Mirrors `helper_client.py`'s transport discipline: one shared `httpx.AsyncClient`, 3 retries with backoff+jitter on transient failures, and its own `CircuitBreaker("crawl")` (reused from `helper_client`; surfaced as op `crawl` in `/metrics`). Auth: `Authorization: Bearer <CRAWL_SERVICE_TOKEN>` when set (crawl4ai's scheme — NOT the helper's `X-Helper-Token`); also sends `X-Tenant-ID` + `X-Request-ID`. The token is effectively mandatory for crawl4ai ≥ 0.9.0 — tokenless, crawl4ai binds its API to `127.0.0.1` only and is unreachable cross-container. `main.py`'s lifespan logs a WARN when `ENABLE_WEB_CRAWL` + `CRAWL_SERVICE_URL` are set but `CRAWL_SERVICE_TOKEN` is empty.

- **`crawl_markdown(url, content_filter=None, query=None)`** → POST `/md` with `{url, f, c:"0"}` (+ `q` for bm25). `f` defaults to `CRAWL_CONTENT_FILTER` (`fit`). Returns `{url, title, markdown}`. Title is best-effort: first `# ` H1 in the markdown, else derived from the URL path. Raises `CrawlUnavailableError` on circuit-open / network / 4xx / empty markdown. `c="0"` = cache-bypass (defense-in-depth; see Privacy).
- **`discover_links(url)`** → POST `/crawl` with `{urls:[url]}`, reads `results[0].links.internal`. Filters to **same-host** http(s) links, drops `_SKIP_PATTERNS` (login/cart/legal/…) and asset extensions, dedups, caps at `CRAWL_DISCOVER_MAX_LINKS`. Returns `{source_url, domain, links:[{url,title}]}`.

4xx is the caller's problem (bad URL/auth) and does NOT trip the breaker; transient 5xx/timeouts do.

## Endpoints (`main.py`)

`_require_web_crawl_enabled()` 404s unless `ENABLE_WEB_CRAWL` and `CRAWL_SERVICE_URL` are both set. Both endpoints require `manage` permission.

- **`POST /api/web-import`** `{urls[], collection_id?, content_filter?, query?}` → validates http(s) + dedups, rejects > `CRAWL_MAX_URLS_PER_JOB` (400) and the graph file-limit (403), then spawns `_run_web_import_task` and returns `WebImportResponse{task_id, accepted_urls, message}` immediately. Poll `GET /api/tasks/{task_id}`.
- **`POST /api/web-import/discover`** `{url}` → synchronous `discover_links`; 502 on crawl failure. Returns `WebDiscoverResponse{source_url, domain, links[]}`.

**`_run_web_import_task`** (two-phase, mirrors the git connector's shape):
1. Crawl all URLs concurrently (`asyncio.Semaphore(CRAWL_CONCURRENCY)`), wrap each in the provenance header (below), write to `{custom_inputs_dir}/{uuid}.md`, `store_file_only(source=f"crawl:{netloc}")` (staged PENDING). Per-URL failures are collected, not fatal.
2. If anything succeeded, `processor.process_pending_documents(progress_callback=...)` runs the shared extract/embed pass. `complete_task` with `{imported, failed, total, succeeded[], failures[], processing}`. All-fail ⇒ `fail_task`.

**Markdown provenance header** (`_format_crawl_markdown`):

```markdown
# {title}

> Source: {url}
> Extracted: {YYYY-MM-DD}

---

{crawl4ai markdown}
```

The `.md` extension routes through `document_processor`'s `RAW_TEXT_EXTENSIONS` fast path — no Docling, markdown passes through verbatim (see [`document-pipeline.md`](document-pipeline.md)).

## Privacy (multi-tenant cloud) — no cross-customer leakage

A hard requirement, satisfied by construction. The controls live where the operator owns them (the shared crawl4ai config + this client), not in tenant trust:

1. **No persistent volume** on the crawl4ai container → cache/results are ephemeral, gone on restart, never browsable.
2. **Sync endpoints only** (`/md`, `/crawl`) — never the async `/crawl/job/{id}` API whose results live (TTL'd) in Redis and are addressable by id. The shared deployment runs no Redis.
3. **Cache-bypass per request** (`c="0"`) so one tenant is never served another's cached page.
4. **Network isolation + token** — crawl4ai binds a private interface, firewalled off the public net; `CRAWL_SERVICE_TOKEN` ↔ crawl4ai `CRAWL4AI_API_TOKEN` (`security.api_token`). For crawl4ai ≥ 0.9.0 the token is required (tokenless it binds `127.0.0.1` only).

There is no crawl-history surface to leak; 1–3 make cross-tenant visibility impossible, 4 controls who can call it. **SSRF**: crawl4ai fetches any URL given — on shared hosts, add egress rules blocking RFC1918 + `169.254.169.254` at the network layer (see `cortex-helper/README.md`).

## Frontend (Documents-page modal)

The entry point is a split **Upload** button on the Documents page (`components/DocumentList.tsx`): a `▾` dropdown beside Upload offers a single **Web Import** item that opens `components/documents/WebImportModal.tsx` (collection → URLs → Discover links → content filter → "Import from Web" → live progress → completion screen; refreshes the doc list via `onImported`).

Gating: `DocumentList` calls `/api/features` (read-permission, non-admin) which returns `enable_web_crawl` (already AND-ed with "crawl service configured") and renders the `▾`/modal only when true; the full `/api/admin/config` (`SystemConfigResponse.enable_web_crawl`) is admin-only. The modal reuses `api.webImport` / `api.webDiscover` / `api.pollTask`. (There is no `/add`-page entry point — that approach was dropped.)

## Design notes (why cortex-app stays thin)

- **crawl4ai does all the heavy lifting** — JS rendering via a headless browser pool, anti-scrape handling, and HTML→markdown content filtering (`fit` = readability extraction). cortex-app carries no browser, HTML parser, or scraping dependency of its own — just an HTTP client (`crawl_client.py`).
- **No crawl-specific storage** — jobs reuse the existing `TaskProgress` tracker and land as ordinary `Document` nodes; there is no separate crawl database or history table.
- The only crawl-specific logic in cortex-app is the same-host link/skip-pattern filtering (`crawl_client.py`) and the provenance header (`_format_crawl_markdown`).

## Future (not built — MVP is batch import + discovery)

A recurring **crawl connector** (saved sources, scheduled re-crawl, content-hash dedup, `crawl_*` provenance on `Document`) would mirror the git connector; a researcher-agent `web_crawl` tool would mirror `git_repo`. Both are deferred.
