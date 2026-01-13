import type {
  Document,
  SearchResponse,
  RAGResponse,
  RAGRequest,
  UploadResponse,
  Stats,
  HealthResponse,
  ConversationMessage,
  StreamEvent,
} from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

class ApiClient {
  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_BASE}${endpoint}`;
    const res = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: "Request failed" }));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }

    return res.json();
  }

  async health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health");
  }

  async getStats(): Promise<Stats> {
    return this.request<Stats>("/api/stats");
  }

  async uploadFile(file: File): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch(`${API_BASE}/api/upload`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: "Upload failed" }));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }

    return res.json();
  }

  async getDocuments(): Promise<{ documents: Document[]; total: number }> {
    return this.request<{ documents: Document[]; total: number }>("/api/documents");
  }

  async getDocument(id: string): Promise<Document> {
    return this.request<Document>(`/api/documents/${id}`);
  }

  async deleteDocument(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/api/documents/${id}`, {
      method: "DELETE",
    });
  }

  async reprocessDocuments(documentIds: string[]): Promise<{
    results: Array<{
      document_id: string;
      status: string;
      message: string;
    }>;
    total_queued: number;
  }> {
    return this.request<{
      results: Array<{
        document_id: string;
        status: string;
        message: string;
      }>;
      total_queued: number;
    }>("/api/documents/reprocess", {
      method: "POST",
      body: JSON.stringify({ document_ids: documentIds }),
    });
  }

  async reprocessDocumentWithFile(
    documentId: string,
    file: File
  ): Promise<{
    document_id: string;
    filename: string;
    status: string;
    message: string;
  }> {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch(`${API_BASE}/api/documents/${documentId}/reprocess`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: "Reprocess failed" }));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }

    return res.json();
  }

  async search(query: string, topK = 10): Promise<SearchResponse> {
    return this.request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({ query, top_k: topK }),
    });
  }

  async ask(
    question: string, 
    options: {
      topK?: number;
      conversationHistory?: ConversationMessage[];
      useReranking?: boolean;
      useAgentic?: boolean;
      useGraph?: boolean;
    } = {}
  ): Promise<RAGResponse> {
    const {
      topK = 5,
      conversationHistory,
      useReranking = true,
      useAgentic = false,
      useGraph = true,
    } = options;

    return this.request<RAGResponse>("/api/ask", {
      method: "POST",
      body: JSON.stringify({ 
        question, 
        top_k: topK,
        conversation_history: conversationHistory,
        use_reranking: useReranking,
        use_agentic: useAgentic,
        use_graph: useGraph,
      }),
    });
  }

  /**
   * Stream the RAG response for better UX.
   * Returns an async generator that yields stream events.
   */
  async *askStream(
    question: string,
    options: {
      topK?: number;
      conversationHistory?: ConversationMessage[];
      useReranking?: boolean;
      useGraph?: boolean;
    } = {}
  ): AsyncGenerator<StreamEvent, void, unknown> {
    const {
      topK = 5,
      conversationHistory,
      useReranking = true,
      useGraph = true,
    } = options;

    const res = await fetch(`${API_BASE}/api/ask/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        question,
        top_k: topK,
        conversation_history: conversationHistory,
        use_reranking: useReranking,
        use_graph: useGraph,
      }),
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: "Stream failed" }));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      throw new Error("No response body");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      
      // Process complete SSE messages
      const lines = buffer.split("\n");
      buffer = lines.pop() || ""; // Keep incomplete line in buffer

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            yield data as StreamEvent;
          } catch (e) {
            console.warn("Failed to parse SSE data:", line);
          }
        }
      }
    }
  }
}

export const api = new ApiClient();
