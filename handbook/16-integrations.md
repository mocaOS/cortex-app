# Chapter 16: Integration Patterns

This chapter provides complete code examples for integrating the Library into your applications and workflows.

## Python Client

A full-featured Python client class:

```python
import requests
import json
from typing import Optional, Iterator

class CortexClient:
    """Complete Python client for the Cortex Library API."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        })

    # ── Health & Stats ─────────────────────────────────────────

    def health(self) -> dict:
        return requests.get(f"{self.base_url}/health").json()

    def stats(self) -> dict:
        return self.session.get(f"{self.base_url}/api/stats").json()

    # ── Documents ──────────────────────────────────────────────

    def upload(self, filepath: str, collection_id: str = None,
              start_processing: bool = True) -> dict:
        params = {"start_processing": str(start_processing).lower()}
        if collection_id:
            params["collection_id"] = collection_id
        with open(filepath, "rb") as f:
            return requests.post(
                f"{self.base_url}/api/upload",
                headers={"X-API-Key": self.session.headers["X-API-Key"]},
                files={"file": f},
                params=params
            ).json()

    def documents(self) -> dict:
        return self.session.get(f"{self.base_url}/api/documents").json()

    def document(self, doc_id: str) -> dict:
        return self.session.get(f"{self.base_url}/api/documents/{doc_id}").json()

    def document_content(self, doc_id: str) -> dict:
        return self.session.get(f"{self.base_url}/api/documents/{doc_id}/content").json()

    def delete_document(self, doc_id: str) -> dict:
        return self.session.delete(f"{self.base_url}/api/documents/{doc_id}").json()

    def delete_documents(self, doc_ids: list) -> dict:
        return self.session.post(
            f"{self.base_url}/api/documents/delete",
            json={"document_ids": doc_ids}
        ).json()

    def reprocess(self, doc_ids: list, concurrency: int = 5) -> dict:
        return self.session.post(
            f"{self.base_url}/api/documents/reprocess",
            params={"concurrency": concurrency},
            json={"document_ids": doc_ids}
        ).json()

    def process_pending(self, concurrency: int = 5) -> dict:
        return self.session.post(
            f"{self.base_url}/api/documents/process-pending",
            params={"concurrency": concurrency}
        ).json()

    # ── Search ─────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5,
               collection_id: str = None) -> dict:
        payload = {"query": query, "top_k": top_k}
        if collection_id:
            payload["collection_id"] = collection_id
        return self.session.post(
            f"{self.base_url}/api/search", json=payload
        ).json()

    # ── Ask AI ─────────────────────────────────────────────────

    def ask(self, question: str, **kwargs) -> dict:
        payload = {"question": question, **kwargs}
        return self.session.post(
            f"{self.base_url}/api/ask", json=payload
        ).json()

    def ask_stream(self, question: str, **kwargs) -> Iterator[dict]:
        """Stream Ask AI responses. Yields parsed SSE events."""
        payload = {"question": question, **kwargs}
        response = self.session.post(
            f"{self.base_url}/api/ask/stream",
            json=payload, stream=True
        )
        for line in response.iter_lines():
            if line:
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    try:
                        yield json.loads(text[6:])
                    except json.JSONDecodeError:
                        continue

    # ── Collections ────────────────────────────────────────────

    def collections(self) -> dict:
        return self.session.get(f"{self.base_url}/api/collections").json()

    def create_collection(self, name: str, description: str = "") -> dict:
        return self.session.post(
            f"{self.base_url}/api/collections",
            json={"name": name, "description": description}
        ).json()

    def delete_collection(self, collection_id: str) -> dict:
        return self.session.delete(
            f"{self.base_url}/api/collections/{collection_id}"
        ).json()

    # ── Knowledge Graph ────────────────────────────────────────

    def graph_visualization(self, limit: int = 100) -> dict:
        return self.session.get(
            f"{self.base_url}/api/graph/visualization",
            params={"limit": limit}
        ).json()

    def entity(self, name: str) -> dict:
        return self.session.get(
            f"{self.base_url}/api/graph/entity/{name}"
        ).json()

    def search_entities(self, query: str) -> dict:
        return self.session.get(
            f"{self.base_url}/api/graph/search",
            params={"query": query}
        ).json()

    def entities(self, skip: int = 0, limit: int = 50,
                 search: str = None, entity_type: str = None) -> dict:
        params = {"skip": skip, "limit": limit}
        if search: params["search"] = search
        if entity_type: params["entity_type"] = entity_type
        return self.session.get(
            f"{self.base_url}/api/graph/entities", params=params
        ).json()

    # ── Deduplication ──────────────────────────────────────────

    def find_duplicates(self, threshold: float = 0.75) -> dict:
        return self.session.get(
            f"{self.base_url}/api/entities/duplicates",
            params={"threshold": threshold}
        ).json()

    def merge_entities(self, canonical: str, merge: list) -> dict:
        return self.session.post(
            f"{self.base_url}/api/entities/merge",
            json={"canonical": canonical, "merge": merge}
        ).json()

    # ── Tasks ──────────────────────────────────────────────────

    def task_status(self, task_id: str) -> dict:
        return self.session.get(
            f"{self.base_url}/api/tasks/{task_id}"
        ).json()

    def wait_for_task(self, task_id: str, poll_interval: float = 2.0) -> dict:
        """Poll a task until completion. Returns the final status."""
        import time
        while True:
            status = self.task_status(task_id)
            if status.get("status") in ("completed", "failed"):
                return status
            time.sleep(poll_interval)
```

### Usage Examples

```python
client = CortexClient("http://localhost:8000", "your-api-key")

# Upload and process a document
result = client.upload("report.pdf", collection_id="quarterly-reports")
print(f"Uploaded: {result['document_id']}")

# Search
results = client.search("quarterly revenue", collection_id="quarterly-reports")
for r in results.get("results", []):
    print(f"  [{r['score']:.2f}] {r['content'][:100]}...")

# Stream a question
for event in client.ask_stream(
    "What were the key financial highlights?",
    collection_id="quarterly-reports",
    use_graph=True
):
    if "content" in event:
        print(event["content"], end="", flush=True)
    elif "done" in event:
        print("\n--- Done ---")
```

## JavaScript/TypeScript Client

```typescript
class CortexClient {
  constructor(private baseUrl: string, private apiKey: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private async request(path: string, options: RequestInit = {}) {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'X-API-Key': this.apiKey,
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  stats = () => this.request('/api/stats');
  documents = () => this.request('/api/documents');
  collections = () => this.request('/api/collections');

  search = (query: string, topK = 5, collectionId?: string) =>
    this.request('/api/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK, collection_id: collectionId }),
    });

  async *askStream(question: string, options: Record<string, any> = {}) {
    const response = await fetch(`${this.baseUrl}/api/ask/stream`, {
      method: 'POST',
      headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, ...options }),
    });
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try { yield JSON.parse(line.slice(6)); } catch {}
        }
      }
    }
  }
}
```

## LangChain Retriever

```python
from langchain.retrievers import BaseRetriever
from langchain.schema import Document
import requests

class CortexRetriever(BaseRetriever):
    base_url: str
    api_key: str
    collection_id: str = None
    top_k: int = 5

    def _get_relevant_documents(self, query: str):
        payload = {"query": query, "top_k": self.top_k}
        if self.collection_id:
            payload["collection_id"] = self.collection_id
        response = requests.post(
            f"{self.base_url}/api/search",
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            json=payload
        )
        return [
            Document(
                page_content=r["content"],
                metadata={"source": r.get("document_id"), "score": r.get("score")}
            )
            for r in response.json().get("results", [])
        ]

# Usage with LangChain
retriever = CortexRetriever(
    base_url="http://localhost:8000",
    api_key="your-key",
    collection_id="research-papers"
)
docs = retriever.get_relevant_documents("machine learning applications")
```

## Agent Memory Pattern

Use Cortex as persistent long-term memory for AI agents.

**Off the shelf:** for SKILL.md-capable agent runtimes there's a ready-made memory skill — no code required. [Hermes](https://nousresearch.com) is the recommended integration ([cortexskills.org/hermes/SKILL.md](https://cortexskills.org/hermes/SKILL.md)): "dump your session into your cortex" to save, "check your cortex for X" to recall, plus a native memory-provider plugin for ambient recall. [OpenClaw](https://docs.openclaw.ai) runs the same canonical skill through the open SKILL.md standard — [cortexskills.org/openclaw/SKILL.md](https://cortexskills.org/openclaw/SKILL.md) covers the adapter (install path, env via `openclaw.json`, cron heartbeat). Both follow the multi-memory scoping model: the agent writes to its own collection but recalls across every collection in the instance.

**Roll your own** for any other framework:

```python
class AgentMemory:
    def __init__(self, cortex: CortexClient, agent_id: str):
        self.cortex = cortex
        self.collection_id = f"agent-{agent_id}"
        try:
            self.cortex.create_collection(
                f"Agent {agent_id} Memory",
                f"Long-term memory for agent {agent_id}"
            )
        except Exception:
            pass  # Collection may already exist

    def remember(self, content: str):
        """Store a memory."""
        requests.post(
            f"{self.cortex.base_url}/api/custom-input",
            headers=dict(self.cortex.session.headers),
            json={"input_type": "text", "content": content,
                  "collection_id": self.collection_id}
        )

    def recall(self, query: str, top_k: int = 5) -> list:
        """Retrieve relevant memories."""
        return self.cortex.search(query, top_k, self.collection_id)

    def reflect(self, question: str) -> str:
        """Ask a question about stored memories."""
        result = self.cortex.ask(question, collection_id=self.collection_id)
        return result.get("answer", "")

# Usage
memory = AgentMemory(client, "curator-bot")
memory.remember("User prefers detailed explanations with examples")
context = memory.recall("How should I format responses?")
```

## Webhook Integration (Flask)

```python
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
CORTEX_URL = "http://localhost:8000"
CORTEX_KEY = "your-api-key"

@app.route("/webhook/ask", methods=["POST"])
def handle_ask():
    data = request.json
    response = requests.post(
        f"{CORTEX_URL}/api/ask",
        headers={"X-API-Key": CORTEX_KEY, "Content-Type": "application/json"},
        json={
            "question": data.get("question", ""),
            "use_graph": True,
            "collection_id": data.get("collection_id")
        }
    )
    return jsonify(response.json())

@app.route("/webhook/search", methods=["POST"])
def handle_search():
    data = request.json
    response = requests.post(
        f"{CORTEX_URL}/api/search",
        headers={"X-API-Key": CORTEX_KEY, "Content-Type": "application/json"},
        json={"query": data.get("query", ""), "top_k": 5}
    )
    return jsonify(response.json())
```

## Slack Bot Integration

```python
from slack_bolt import App

app = App(token="xoxb-your-slack-token", signing_secret="your-signing-secret")
cortex = CortexClient("http://localhost:8000", "your-api-key")

@app.command("/ask")
def handle_ask(ack, respond, command):
    ack()
    question = command["text"]
    result = cortex.ask(question, use_graph=True, use_reranking=True)
    answer = result.get("answer", "No answer found.")
    sources = result.get("sources", [])
    source_text = "\n".join(
        f"• {s.get('metadata', {}).get('document_name', 'Unknown')}"
        for s in sources[:3]
    )
    respond(f"*Answer:*\n{answer}\n\n*Sources:*\n{source_text}")

@app.command("/search")
def handle_search(ack, respond, command):
    ack()
    results = cortex.search(command["text"], top_k=5)
    blocks = []
    for r in results.get("results", [])[:5]:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*[{r['score']:.2f}]* {r['content'][:200]}..."}
        })
    respond(blocks=blocks if blocks else [{"type": "section",
            "text": {"type": "mrkdwn", "text": "No results found."}}])
```

## Next.js API Route

```typescript
// app/api/ask/route.ts
import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
  const { question, collection_id } = await request.json();

  const response = await fetch(`${process.env.CORTEX_URL}/api/ask`, {
    method: 'POST',
    headers: {
      'X-API-Key': process.env.CORTEX_API_KEY!,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ question, collection_id, use_graph: true }),
  });

  return NextResponse.json(await response.json());
}
```
