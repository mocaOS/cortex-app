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
  Collection,
  CollectionCreate,
  CollectionEntity,
  Community,
  ThinkingStreamEvent,
  TaskProgress,
  TaskStartResponse,
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

  async uploadFile(file: File, collectionId?: string): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const url = collectionId 
      ? `${API_BASE}/api/upload?collection_id=${encodeURIComponent(collectionId)}`
      : `${API_BASE}/api/upload`;

    const res = await fetch(url, {
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

  // ===========================================================================
  // Collection API (R2R-style)
  // ===========================================================================

  async getCollections(): Promise<{ collections: Collection[]; total: number }> {
    return this.request<{ collections: Collection[]; total: number }>("/api/collections");
  }

  async getCollection(id: string): Promise<Collection> {
    return this.request<Collection>(`/api/collections/${id}`);
  }

  async createCollection(data: CollectionCreate): Promise<Collection> {
    return this.request<Collection>("/api/collections", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async deleteCollection(id: string, deleteDocuments = false): Promise<{ message: string }> {
    return this.request<{ message: string }>(
      `/api/collections/${id}?delete_documents=${deleteDocuments}`,
      { method: "DELETE" }
    );
  }

  async addDocumentToCollection(collectionId: string, documentId: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(
      `/api/collections/${collectionId}/documents/${documentId}`,
      { method: "POST" }
    );
  }

  async moveDocumentsToCollection(documentIds: string[], targetCollectionId: string): Promise<{ message: string; moved_count: number }> {
    return this.request<{ message: string; moved_count: number }>(
      "/api/documents/move",
      {
        method: "POST",
        body: JSON.stringify({ 
          document_ids: documentIds, 
          target_collection_id: targetCollectionId 
        }),
      }
    );
  }

  async getCollectionEntities(collectionId: string, limit = 100): Promise<{ entities: CollectionEntity[]; total: number }> {
    return this.request<{ entities: CollectionEntity[]; total: number }>(
      `/api/collections/${collectionId}/entities?limit=${limit}`
    );
  }

  // ===========================================================================
  // Community API (R2R-style)
  // ===========================================================================

  async getCommunities(limit = 50): Promise<{ communities: Community[]; total: number }> {
    return this.request<{ communities: Community[]; total: number }>(
      `/api/graph/communities?limit=${limit}`
    );
  }

  async detectCommunities(minSize = 3, collectionId?: string): Promise<TaskStartResponse> {
    const params = new URLSearchParams({ min_size: String(minSize) });
    if (collectionId) params.set("collection_id", collectionId);
    
    return this.request<TaskStartResponse>(
      `/api/graph/communities/detect?${params}`,
      { method: "POST" }
    );
  }

  // ===========================================================================
  // Task API (Background Task Tracking)
  // ===========================================================================

  async getTaskStatus(taskId: string): Promise<TaskProgress> {
    return this.request<TaskProgress>(`/api/tasks/${taskId}`);
  }

  async getTaskResult<T = Record<string, unknown>>(taskId: string): Promise<T | null> {
    const url = `${API_BASE}/api/tasks/${taskId}/result`;
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
    });
    
    if (res.status === 202) {
      // Task still running
      return null;
    }
    
    if (!res.ok) {
      const error = await res.text();
      throw new Error(error || `Request failed with status ${res.status}`);
    }
    
    return res.json();
  }

  async pollTask<T = Record<string, unknown>>(
    taskId: string,
    onProgress?: (progress: TaskProgress) => void,
    intervalMs = 1000,
    maxAttempts = 600 // 10 minutes max
  ): Promise<T> {
    let attempts = 0;
    
    while (attempts < maxAttempts) {
      const status = await this.getTaskStatus(taskId);
      
      if (onProgress) {
        onProgress(status);
      }
      
      if (status.status === "completed") {
        // Fetch the result
        const result = await this.getTaskResult<T>(taskId);
        if (result) return result;
      }
      
      if (status.status === "failed") {
        throw new Error(status.error || "Task failed");
      }
      
      // Wait before next poll
      await new Promise(resolve => setTimeout(resolve, intervalMs));
      attempts++;
    }
    
    throw new Error("Task polling timeout");
  }

  async getCommunity(id: number): Promise<Community> {
    return this.request<Community>(`/api/graph/communities/${id}`);
  }

  async summarizeCommunities(communityIds?: number[], forceRegenerate = false): Promise<{ results: Array<{ id: number; status: string; name?: string; summary?: string }>; total_processed: number }> {
    return this.request<{ results: Array<{ id: number; status: string; name?: string; summary?: string }>; total_processed: number }>(
      "/api/graph/communities/summarize",
      {
        method: "POST",
        body: JSON.stringify({
          community_ids: communityIds,
          force_regenerate: forceRegenerate,
        }),
      }
    );
  }

  // ===========================================================================
  // Extended Thinking Stream (R2R-style)
  // ===========================================================================

  async *askStreamWithThinking(
    question: string,
    options: {
      topK?: number;
      conversationHistory?: ConversationMessage[];
      useGraph?: boolean;
      maxHops?: number;
    } = {}
  ): AsyncGenerator<ThinkingStreamEvent, void, unknown> {
    const {
      topK = 5,
      conversationHistory,
      useGraph = true,
      maxHops = 2,
    } = options;

    const res = await fetch(`${API_BASE}/api/ask/stream/thinking`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        question,
        top_k: topK,
        conversation_history: conversationHistory,
        use_graph: useGraph,
        max_hops: maxHops,
        use_agentic: true,
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
      
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            yield data as ThinkingStreamEvent;
          } catch (e) {
            console.warn("Failed to parse SSE data:", line);
          }
        }
      }
    }
  }
}

export const api = new ApiClient();
