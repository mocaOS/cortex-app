"""Instance-hosted remote MCP — the backend speaks Model Context Protocol.

Point Claude Desktop / Claude Code / Cursor at `https://<instance>/mcp` with a
Cortex API key and every deployment is MCP-native with zero client install —
no npm, no stdio process. Env-gated `ENABLE_REMOTE_MCP` (default off).

Implementation notes:
- **Minimal streamable-HTTP transport, hand-rolled.** The protocol surface a
  stateless tool server needs is tiny (initialize, tools/list, tools/call,
  ping, notification acks), and owning it means zero new dependencies and no
  version drift against a fast-moving SDK. Stateless: no Mcp-Session-Id is
  issued; every request is self-contained (the spec permits this).
- **Tools dispatch through the instance's OWN REST API** via an in-process
  ASGI client, forwarding the caller's API key. Auth, collection scoping,
  quotas, and usage analytics apply to MCP traffic exactly as to REST traffic
  — the MCP layer is a protocol adapter, never a second security surface.
- **Long tool calls stream.** deep_research runs minutes; when the client
  accepts `text/event-stream`, tools/call responds as SSE with keep-alive
  comments during silence so proxies don't idle-timeout (same trick as
  /api/ask/stream). Clients that only accept JSON get a buffered response.

Tool surface mirrors @mocaos/cortex-mcp minus client-filesystem semantics:
upload_document takes (filename, content) instead of a local path, and
conversation threads are not offered (they are client-side state; server-side
sessions are a future feature).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Protocol versions this transport is known-good for. We echo the client's
# requested version when we support it, else answer with our latest.
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO = {"name": "cortex", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema, MCP shape)
# ---------------------------------------------------------------------------

def _schema(properties: dict, required: list) -> dict:
    return {"type": "object", "properties": properties, "required": required}


TOOLS: list[dict] = [
    {
        "name": "search_knowledge",
        "description": (
            "Hybrid search (vector + keyword + graph with reciprocal rank fusion) "
            "over the Cortex knowledge base. Returns the most relevant document "
            "chunks with filenames and scores."
        ),
        "inputSchema": _schema({
            "query": {"type": "string", "description": "The search query"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "collection_id": {"type": "string", "description": "Scope to a collection"},
        }, ["query"]),
    },
    {
        "name": "ask_question",
        "description": (
            "Ask the Cortex RAG engine a question and get a cited answer. "
            "mode deep_research is the first choice for retrieving knowledge "
            "(agentic multi-step research, can take minutes); chat answers in "
            "seconds for quick lookups."
        ),
        "inputSchema": _schema({
            "question": {"type": "string"},
            "mode": {"type": "string", "enum": ["chat", "deep_research"], "default": "chat"},
            "collection_id": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        }, ["question"]),
    },
    {
        "name": "get_context",
        "description": (
            "Assemble a token-budgeted context bundle (reranked chunks + graph "
            "context + community summaries) for injection into YOUR OWN prompt — "
            "retrieval without Cortex writing the answer."
        ),
        "inputSchema": _schema({
            "query": {"type": "string"},
            "max_tokens": {"type": "integer", "minimum": 200, "maximum": 32000, "default": 4000},
            "collection_id": {"type": "string"},
        }, ["query"]),
    },
    {
        "name": "list_documents",
        "description": "List documents, newest first. Server-side filtering and pagination.",
        "inputSchema": _schema({
            "collection_id": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "processing", "extracting", "completed", "failed"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        }, []),
    },
    {
        "name": "get_document",
        "description": "Get a document's metadata and processing status.",
        "inputSchema": _schema({"document_id": {"type": "string"}}, ["document_id"]),
    },
    {
        "name": "get_document_content",
        "description": "Get a document's full text content (all chunks concatenated).",
        "inputSchema": _schema({"document_id": {"type": "string"}}, ["document_id"]),
    },
    {
        "name": "list_entities",
        "description": "List knowledge-graph entities with optional type filter and search.",
        "inputSchema": _schema({
            "entity_type": {"type": "string"},
            "search": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
        }, []),
    },
    {
        "name": "get_entity",
        "description": "Get an entity by exact name with its relationship neighborhood.",
        "inputSchema": _schema({
            "name": {"type": "string"},
            "max_hops": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
        }, ["name"]),
    },
    {
        "name": "search_entities",
        "description": "Fuzzy-search entities by name (resolve exact names before get_entity).",
        "inputSchema": _schema({"query": {"type": "string"}}, ["query"]),
    },
    {
        "name": "list_collections",
        "description": "List collections (document namespaces).",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "list_communities",
        "description": "List auto-detected entity communities with summaries.",
        "inputSchema": _schema({
            "search": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
        }, []),
    },
    {
        "name": "upload_document",
        "description": (
            "Save a text/markdown document into the knowledge base (requires a "
            "manage-permission key). Processing starts immediately."
        ),
        "inputSchema": _schema({
            "filename": {"type": "string", "description": "e.g. note.md"},
            "content": {"type": "string"},
            "collection_id": {"type": "string"},
        }, ["filename", "content"]),
    },
    {
        "name": "get_stats",
        "description": "Knowledge-base statistics: documents, chunks, entities, relationships, communities.",
        "inputSchema": _schema({}, []),
    },
]


# ---------------------------------------------------------------------------
# Tool execution — in-process calls against the instance's own REST API
# ---------------------------------------------------------------------------

def _client(api_key: str) -> httpx.AsyncClient:
    from app.main import app  # late import — main imports this module

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp-internal",
        headers={"X-API-Key": api_key},
        timeout=httpx.Timeout(300.0, connect=5.0),
    )


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


async def _rest(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> Any:
    res = await client.request(method, path, **kwargs)
    if res.status_code >= 400:
        detail = res.text[:500]
        raise _ToolHttpError(f"Cortex API {res.status_code} on {path}: {detail}")
    return res.json()


class _ToolHttpError(Exception):
    pass


def _fmt_sources(sources: list) -> str:
    lines = []
    for i, s in enumerate(sources or []):
        name = (s.get("metadata") or {}).get("filename") or s.get("document_title") or s.get("document_id")
        lines.append(f"{i + 1}. {name} (doc:{s.get('document_id')}, score: {s.get('score')})")
    return "\n".join(lines)


async def _tool_search(c, args):
    body: Dict[str, Any] = {"query": args["query"], "top_k": args.get("top_k", 10)}
    if args.get("collection_id"):
        body["collection_id"] = args["collection_id"]
    data = await _rest(c, "POST", "/api/search", json=body)
    results = data.get("results", [])
    if not results:
        return _ok("No results found.")
    parts = [
        f"[{i + 1}] {(r.get('metadata') or {}).get('filename') or r.get('document_id')} "
        f"(doc:{r.get('document_id')}, score: {r.get('score')})\n{r.get('content')}"
        for i, r in enumerate(results)
    ]
    return _ok("\n\n---\n\n".join(parts))


async def _tool_ask(c, args):
    deep = args.get("mode") == "deep_research"
    body: Dict[str, Any] = {
        "question": args["question"],
        "depth": "deep" if deep else "standard",
        "use_agentic": deep,
    }
    if args.get("collection_id"):
        body["collection_id"] = args["collection_id"]
    if args.get("top_k"):
        body["top_k"] = args["top_k"]

    if deep:
        # Aggregate the SSE stream in-process (only place agentic runs)
        answer_parts: list[str] = []
        sources: list = []
        async with c.stream(
            "POST", "/api/ask/stream", json=body,
            headers={"Accept": "text/event-stream"},
        ) as res:
            if res.status_code >= 400:
                detail = (await res.aread()).decode(errors="replace")[:500]
                raise _ToolHttpError(f"Cortex API {res.status_code} on /api/ask/stream: {detail}")
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev.get("error"):
                    raise _ToolHttpError(f"Cortex stream error: {ev['error']}")
                if isinstance(ev.get("content"), str):
                    answer_parts.append(ev["content"])
                if ev.get("sources"):
                    sources = ev["sources"]
        text = "".join(answer_parts) or "No answer generated."
    else:
        data = await _rest(c, "POST", "/api/ask", json=body)
        text = data.get("answer") or "No answer generated."
        sources = data.get("sources", [])

    if sources:
        text += f"\n\n**Sources:**\n{_fmt_sources(sources)}"
    return _ok(text)


async def _tool_get_context(c, args):
    body: Dict[str, Any] = {
        "query": args["query"],
        "max_tokens": args.get("max_tokens", 4000),
    }
    if args.get("collection_id"):
        body["collection_id"] = args["collection_id"]
    data = await _rest(c, "POST", "/api/context", json=body)
    return _ok(data.get("text") or "No context available.")


async def _tool_list_documents(c, args):
    params = {"limit": args.get("limit", 50)}
    if args.get("collection_id"):
        params["collection_id"] = args["collection_id"]
    if args.get("status"):
        params["status"] = args["status"]
    data = await _rest(c, "GET", "/api/documents", params=params)
    docs = data.get("documents", [])
    if not docs:
        return _ok("No documents found.")
    lines = [
        f"- **{d.get('filename')}** ({d.get('id')}) — status: {d.get('processing_status')}, "
        f"chunks: {d.get('chunk_count', 0)}"
        + (f", collection: {d.get('collection_name')}" if d.get("collection_name") else "")
        for d in docs
    ]
    total = data.get("total", len(docs))
    suffix = f"\n\n({len(docs)} of {total} shown)" if total > len(docs) else ""
    return _ok("\n".join(lines) + suffix)


async def _tool_get_document(c, args):
    data = await _rest(c, "GET", f"/api/documents/{args['document_id']}")
    return _ok(json.dumps(data, indent=2))


async def _tool_get_document_content(c, args):
    data = await _rest(c, "GET", f"/api/documents/{args['document_id']}/content")
    header = (
        f"# {data.get('filename')} ({data.get('id')})\n"
        f"Status: {data.get('processing_status')}, chunks: {len(data.get('chunks') or [])}\n\n"
    )
    return _ok(header + (data.get("full_content") or "(no content extracted)"))


async def _tool_list_entities(c, args):
    params: Dict[str, Any] = {"limit": args.get("limit", 50)}
    if args.get("entity_type"):
        params["entity_type"] = args["entity_type"]
    if args.get("search"):
        params["search"] = args["search"]
    data = await _rest(c, "GET", "/api/graph/entities", params=params)
    entities = data.get("entities", [])
    if not entities:
        return _ok("No entities found.")
    lines = [
        f"- **{e.get('name')}** ({e.get('type')}, {e.get('mention_count', 0)} mentions)"
        + (f": {e.get('description')}" if e.get("description") else "")
        for e in entities
    ]
    return _ok("\n".join(lines))


async def _tool_get_entity(c, args):
    data = await _rest(
        c, "GET",
        f"/api/graph/entity/{args['name']}",
        params={"max_hops": args.get("max_hops", 1)},
    )
    return _ok(json.dumps(data, indent=2))


async def _tool_search_entities(c, args):
    data = await _rest(c, "GET", "/api/graph/search", params={"query": args["query"]})
    results = data.get("results", [])
    return _ok(json.dumps(results, indent=2) if results else "No matching entities found.")


async def _tool_list_collections(c, args):
    data = await _rest(c, "GET", "/api/collections")
    cols = data.get("collections", []) if isinstance(data, dict) else data
    if not cols:
        return _ok("No collections found.")
    lines = [
        f"- **{col.get('name')}** ({col.get('id')})"
        + (f": {col.get('description')}" if col.get("description") else "")
        for col in cols
    ]
    return _ok("\n".join(lines))


async def _tool_list_communities(c, args):
    params: Dict[str, Any] = {"limit": args.get("limit", 50)}
    if args.get("search"):
        params["search"] = args["search"]
    data = await _rest(c, "GET", "/api/graph/communities", params=params)
    communities = data.get("communities", [])
    if not communities:
        return _ok("No communities found.")
    lines = []
    for com in communities:
        name = com.get("name") or f"Community {com.get('id')}"
        entry = f"- **{name}** ({com.get('entity_count', '?')} entities)"
        if com.get("summary"):
            entry += f"\n  {com.get('summary')}"
        lines.append(entry)
    return _ok("\n".join(lines))


async def _tool_upload_document(c, args):
    files = {"file": (args["filename"], args["content"].encode(), "text/markdown")}
    params = {"start_processing": "true"}
    if args.get("collection_id"):
        params["collection_id"] = args["collection_id"]
    data = await _rest(c, "POST", "/api/upload", params=params, files=files)
    return _ok(
        f"Uploaded **{data.get('filename')}** ({data.get('document_id')}) — "
        f"status: {data.get('status')}\n{data.get('message', '')}"
    )


async def _tool_get_stats(c, args):
    s = await _rest(c, "GET", "/api/stats")
    lines = [
        f"Documents: {s.get('document_count')} (completed: {s.get('completed_count')}, "
        f"pending: {s.get('pending_count')}, failed: {s.get('failed_count')})",
        f"Chunks: {s.get('chunk_count')}",
        f"Entities: {s.get('entity_count')}",
        f"Relationships: {s.get('relationship_count')}",
        f"Communities: {s.get('community_count')}",
        f"Collections: {s.get('collection_count')}",
    ]
    return _ok("\n".join(lines))


_TOOL_HANDLERS: Dict[str, Callable] = {
    "search_knowledge": _tool_search,
    "ask_question": _tool_ask,
    "get_context": _tool_get_context,
    "list_documents": _tool_list_documents,
    "get_document": _tool_get_document,
    "get_document_content": _tool_get_document_content,
    "list_entities": _tool_list_entities,
    "get_entity": _tool_get_entity,
    "search_entities": _tool_search_entities,
    "list_collections": _tool_list_collections,
    "list_communities": _tool_list_communities,
    "upload_document": _tool_upload_document,
    "get_stats": _tool_get_stats,
}


async def execute_tool(name: str, args: dict, api_key: str) -> dict:
    """Run one tool call. Returns an MCP CallToolResult dict; API failures come
    back as isError results (the MCP convention), never protocol errors."""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _err(f"Unknown tool: {name}")
    try:
        async with _client(api_key) as c:
            return await handler(c, args or {})
    except _ToolHttpError as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001 — a tool crash must not kill the transport
        logger.error(f"Remote MCP tool {name} failed: {e}", exc_info=True)
        return _err(f"Tool execution failed: {e}")


# ---------------------------------------------------------------------------
# JSON-RPC handling
# ---------------------------------------------------------------------------

def rpc_result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def rpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_initialize(msg: dict) -> dict:
    requested = ((msg.get("params") or {}).get("protocolVersion")) or LATEST_PROTOCOL_VERSION
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
    return rpc_result(msg.get("id"), {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            "Cortex knowledge base. For retrieval questions prefer ask_question "
            "with mode deep_research (thorough, minutes) or get_context to pull "
            "raw knowledge into your own reasoning; search_knowledge returns "
            "verbatim chunks; list_documents is the ground truth for inventory."
        ),
    })


def handle_tools_list(msg: dict) -> dict:
    return rpc_result(msg.get("id"), {"tools": TOOLS})
