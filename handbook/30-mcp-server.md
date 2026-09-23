# Chapter 30: MCP Server

Cortex can be a tool provider for your AI assistant. Through the [Model Context Protocol](https://modelcontextprotocol.io) (MCP), Claude Code, Cursor, Claude Desktop and any other MCP-capable client get native tools to search the knowledge base, ask it questions (quick chat or full deep research), read documents, walk the knowledge graph and — with a write key — save notes into it. No copy-pasting search results, no hand-written API calls.

There are two servers, with the same tools:

- **Remote, instance-hosted** — the Cortex backend itself serves MCP at `/mcp`. Nothing to install on the client machine; connect with the URL and an API key. Enabled by an administrator with `ENABLE_REMOTE_MCP=true`. This chapter is about this one.
- **Local, npm** — `npx @mocaos/cortex-mcp`, a small stdio process that talks to the REST API. For clients that only speak stdio, and for conversation threads (client-side state the stateless remote server does not keep). Documented in the [MCP skill](https://cortexskills.org/mcp/SKILL.md).

## Enabling the remote server

Set `ENABLE_REMOTE_MCP=true` on the **backend** service and redeploy (see [Chapter 4: Configuration](04-configuration.md#mcp-server-configuration)). The stock Coolify, Dokploy and self-host compose files already pass the variable through. It is off by default, and off means the endpoint does not exist: `/mcp` answers 404 and leaves no trace, the same philosophy as Apps.

Nothing new appears in the web interface. The feature is an endpoint at `https://your-instance/mcp`.

**Two hostnames?** The Dokploy and Coolify stacks put the UI and the API on separate domains (`cortex.example.com` and `api-cortex.example.com`). Both serve `/mcp`: the frontend forwards the path to the backend just as it forwards `/api/*`. If the UI domain answers with an HTML 404 while the API domain works, the frontend image predates that forward (added September 23, 2026) — rebuild it, or use the API hostname in the meantime.

## Keys, permissions and scope

MCP calls are API calls. Each tool is executed through the instance's own REST API with the key the client connected with, so everything you know from [Chapter 5: Security](05-security.md) carries over:

- A key with `read` permission (`cortex_ro_…`) unlocks every tool except `upload_document`, which needs `manage` (`cortex_rw_…`). Create keys under **Admin → API Keys**.
- Send the key as the `X-API-Key` header, or as `Authorization: Bearer <key>`.
- A **collection-scoped** key limits the assistant to those collections — the same isolation as for the API.
- Calls count toward the key's monthly quota and appear in the admin usage analytics as ordinary API traffic.

The web UI's login and session cookie are not involved.

## The tools

| Tool | What it does | Key needed |
|------|--------------|------------|
| `search_knowledge` | Hybrid search (vector + keyword + graph, reciprocal rank fusion). Returns the top chunks with filenames and scores. `top_k` 1–50, optional `collection_id`. | read |
| `ask_question` | A cited answer from the RAG engine. `mode: "chat"` answers in seconds; `mode: "deep_research"` runs the agentic researcher and can take minutes — it is the first choice when the task is "find out what the knowledge base says". | read |
| `get_context` | A token-budgeted context bundle (reranked chunks + graph context + community summaries) for your **own** prompt — retrieval without Cortex writing the answer. `max_tokens` 200–32000, default 4000. Backed by `POST /api/context`. | read |
| `list_documents` | Documents, newest first, with optional `collection_id` / `status` filter and `limit` (max 500). The ground truth for "what is in here". | read |
| `get_document` | Metadata and processing status of one document. | read |
| `get_document_content` | The full text of one document (all chunks concatenated). | read |
| `list_entities` | Knowledge-graph entities, optionally filtered by `entity_type` or a `search` string. | read |
| `search_entities` | Fuzzy entity search by name — resolve the exact name before calling `get_entity`. | read |
| `get_entity` | One entity by exact name with its relationship neighbourhood (`max_hops` 1–3). | read |
| `list_collections` | The collections (document namespaces) the key can see. | read |
| `list_communities` | Auto-detected entity communities with their summaries. | read |
| `get_stats` | Counts of documents, chunks, entities, relationships and communities. | read |
| `upload_document` | Saves a text/markdown document (`filename`, `content`, optional `collection_id`) and starts processing immediately. | **manage** (`cortex_rw_…`) |

When a client connects, the server hands it usage guidance: for retrieval questions prefer `ask_question` with `mode: "deep_research"` (thorough, minutes) or `get_context` to pull raw knowledge into its own reasoning; `search_knowledge` returns verbatim chunks; `list_documents` is the inventory of record.

## Connecting a client

### Claude Code

```bash
claude mcp add --transport http cortex https://your-instance/mcp \
  --header "X-API-Key: cortex_ro_your_key_here"
```

`claude mcp list` should then show `cortex` as connected, and `/mcp` inside a session lists the thirteen tools.

### Cursor

`.cursor/mcp.json` in the project (or the global Cursor MCP settings):

```json
{
  "mcpServers": {
    "cortex": {
      "url": "https://your-instance/mcp",
      "headers": { "X-API-Key": "cortex_ro_your_key_here" }
    }
  }
}
```

### Claude Desktop and other clients without header support

Claude Desktop's **Custom connectors** take a URL but cannot attach a custom header, so the API key has no place to go. Bridge through the community `mcp-remote` shim, which forwards the header for you, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cortex": {
      "command": "npx",
      "args": [
        "mcp-remote", "https://your-instance/mcp",
        "--header", "X-API-Key: cortex_ro_your_key_here"
      ]
    }
  }
}
```

Any client that speaks MCP over streamable HTTP and can send a header works the same way: URL `https://your-instance/mcp`, header `X-API-Key: <key>` (or `Authorization: Bearer <key>`).

### Check it from the shell

```bash
# initialize — answers as JSON with the server info and protocol version
curl -s https://your-instance/mcp \
  -H "Content-Type: application/json" -H "X-API-Key: cortex_ro_..." \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# tools/list — the thirteen tool definitions
curl -s https://your-instance/mcp \
  -H "Content-Type: application/json" -H "X-API-Key: cortex_ro_..." \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

## Under the hood

The server implements MCP's **streamable HTTP** transport (protocol versions 2024-11-05 to 2025-06-18) as a **stateless** server: no sessions, so `GET` and `DELETE /mcp` answer 405 and clients only ever POST single JSON-RPC messages (batches are rejected with 400, as the current spec requires). `initialize`, `ping` and `tools/list` answer as JSON. `tools/call` answers as JSON as well, unless the client accepts `text/event-stream`, in which case the result is delivered as one SSE event with keep-alive comments beforehand — this is what lets a multi-minute deep-research call survive proxies with short idle timeouts. Each tool call has a 300-second ceiling.

## Troubleshooting

| Symptom | Meaning |
|---------|---------|
| `POST /mcp` answers **404** (an HTML page) | Either the flag is off — with `ENABLE_REMOTE_MCP` unset the endpoint does not exist, by design — or the frontend serving that domain predates September 23, 2026 and does not forward `/mcp` yet. On a deployment with separate UI and API hostnames, try the API hostname (`https://api.your-instance/mcp`) directly; if that works, the backend is fine and the frontend needs a rebuild. |
| `POST /mcp` answers **401** `API key required` / **403** | The flag is on and the endpoint is live; the key is missing, wrong, or lacks `read` permission. |
| `GET /mcp` answers **405** | Normal. The server is stateless and offers no server-initiated stream; the MCP spec allows 405 here. Clients only POST. |
| **400** `Batching is not supported` | The client sent a JSON-RPC array. Batching was removed from the MCP spec; send single messages. |
| `upload_document` returns an error result | The key is read-only. Uploads need a `manage` key (`cortex_rw_…`). |
| A deep-research call drops after ~60 s | A proxy in front of the instance is cutting idle streams. The server sends SSE keep-alives when the client accepts `text/event-stream`; make sure the client does, and raise the proxy's read timeout (Traefik `readTimeout`, nginx `proxy_read_timeout`) to several minutes. |

## Related chapters

- [Chapter 4: Configuration](04-configuration.md#mcp-server-configuration) — the flag
- [Chapter 5: Security](05-security.md) — API keys, permissions, collection scoping
- [Chapter 10: Ask AI](10-ask-ai.md) — what chat and deep research do
- [Chapter 16: Integrations](16-integrations.md) — SDK, skills and the npm MCP server
- [Chapter 18: Agent Skills](18-skills.md) — not the same thing: skills extend Cortex's own researcher, MCP lets your assistant call Cortex
