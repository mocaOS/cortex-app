# Chapter 23: Web Import (MDHarvest powered by Crawl4ai)

Web Import turns web pages into knowledge. You give Cortex a list of URLs — or let it discover the links on a page — and it crawls each one, extracts the readable content as clean markdown, and ingests it exactly like an uploaded document: converted, chunked, embedded, and run through entity/relationship extraction. Once imported, the pages are searchable and queryable in Search, Ask AI, and the knowledge graph, alongside everything else.

This feature supersedes the standalone `mdharvest` tool. The crawling is performed by [**crawl4ai**](https://github.com/unclecode/crawl4ai), an open-source web crawler built for LLM pipelines. Cortex itself never runs a browser — it calls a crawl4ai service over HTTP. You run crawl4ai once and point Cortex at it; a single crawl4ai instance can serve many Cortex deployments.

It is disabled by default. An administrator enables it by setting `ENABLE_WEB_CRAWL=true` and a `CRAWL_SERVICE_URL` (see [Chapter 4: Configuration](04-configuration.md)). When both are set, a **Web Import** option appears in the dropdown next to the **Upload** button on the **Documents** page.

## How it works

1. On the **Documents** page, click the small **▾** arrow beside the **Upload** button and choose **Web Import**. A modal opens.
2. Pick a **collection** to import into.
3. Provide URLs in one of two ways:
   - **Paste** them directly into the URL box, one per line.
   - **Discover links** — enter a single page URL and Cortex lists the same-site links it finds, each with a checkbox, so you can add only the pages you want to the list.
4. Choose a **content filter**:
   - **Readable** (default) — the main article content with navigation, ads, and boilerplate removed.
   - **Full page** — the whole page converted to markdown.
   - **Relevance-ranked** — keeps only the passages most relevant to a query you supply.
5. Click **Import from Web**. Cortex crawls the pages (several at a time) and processes them into the graph, showing a live progress bar.
6. When it finishes, the modal reports how many pages were imported (and any failures). Close it — the new documents are already listed on the Documents page.

Every imported page carries a provenance header recording its source URL and the date it was extracted, so its origin is always traceable.

## Self-hosting the crawler

Run a crawl4ai container and point Cortex at it. **crawl4ai 0.9.0 and newer require an API token** — start it without one and crawl4ai binds its API to `127.0.0.1` only, so the Cortex container can't reach it. Pick a token and pass it to crawl4ai as `CRAWL4AI_API_TOKEN`:

```bash
docker run -d --name crawl4ai -p 11235:11235 --shm-size=1g \
  -e CRAWL4AI_API_TOKEN=your-strong-token \
  unclecode/crawl4ai:0.9.0
```

Then in the Cortex backend configuration:

```
ENABLE_WEB_CRAWL=true
CRAWL_SERVICE_URL=http://crawl4ai:11235
CRAWL_SERVICE_TOKEN=your-strong-token   # must match crawl4ai's CRAWL4AI_API_TOKEN
```

`CRAWL_SERVICE_TOKEN` must match the `CRAWL4AI_API_TOKEN` you gave the crawler; Cortex sends it as `Authorization: Bearer <token>`. The crawl service must be reachable from the Cortex backend (use a shared Docker network or the host IP). crawl4ai uses a headless browser pool — give it roughly 4 GB of memory and `--shm-size=1g`, and keep port 11235 on a private network, never exposed to the public internet.

## Configuration knobs

| Setting | Default | Purpose |
|---|---|---|
| `ENABLE_WEB_CRAWL` | `false` | Master switch for Web Import. |
| `CRAWL_SERVICE_URL` | _(empty)_ | Base URL of the crawl4ai service. Empty = feature off. |
| `CRAWL_SERVICE_TOKEN` | _(empty)_ | Bearer token; must match crawl4ai's `CRAWL4AI_API_TOKEN`. Required for crawl4ai ≥ 0.9.0 (tokenless binds 127.0.0.1 only). |
| `CRAWL_CONTENT_FILTER` | `fit` | Default content filter (`fit` / `raw` / `bm25`). |
| `CRAWL_CONCURRENCY` | `5` | URLs crawled at once per job. |
| `CRAWL_MAX_URLS_PER_JOB` | `100` | Maximum URLs per import. |
| `CRAWL_HTTP_TIMEOUT` | `60` | Per-page crawl timeout (seconds). |
| `CRAWL_DISCOVER_MAX_LINKS` | `200` | Cap on links returned by Discover. |

## A note on shared, multi-customer hosting

In Cortex's hosted (Agent-as-a-Service) model, many independent customer deployments may share a single crawl4ai instance per server to save resources. This is done so that **no crawl history is kept and no customer can see what another has crawled**: the crawler's storage is ephemeral, Cortex only ever uses request-scoped endpoints (never the asynchronous job API), and every request bypasses the cache. Operators additionally keep the crawler on a private network behind a token and block crawling of internal addresses. Self-hosted single-tenant deployments don't face this concern at all — the crawler is yours alone.
