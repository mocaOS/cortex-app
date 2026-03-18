import type {
  Document,
  DocumentContent,
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
  GraphData,
  EntityDetails,
  EntityRelationshipsResponse,
  TurboStatus,
  TurboJob,
  TurboBalance,
  CustomInputCreate,
  CustomInputResponse,
  CustomInputItem,
  APIKeyListItem,
  CreateAPIKeyRequest,
  CreateAPIKeyResponse,
  UpdateAPIKeyRequest,
  APIKeyStats,
  APIKeyWithStats,
  APIKeyUsageHistoryResponse,
  AdminStatsOverview,
  SystemResetRequest,
  SystemResetResponse,
  SystemConfig,
  DuplicateSuggestionsResponse,
  MergeEntitiesRequest,
  MergeEntitiesResponse,
  MergeHistoryResponse,
} from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * Get the admin API key from localStorage (set after login).
 * This is used for authenticated API calls from the frontend.
 */
function getAdminApiKey(): string {
  if (typeof window !== "undefined") {
    return localStorage.getItem("admin_api_key") || "";
  }
  return "";
}

/**
 * Set the admin API key in localStorage.
 * Called after successful admin login.
 */
export function setAdminApiKey(key: string): void {
  if (typeof window !== "undefined") {
    localStorage.setItem("admin_api_key", key);
  }
}

/**
 * Clear the admin API key from localStorage.
 * Called on logout.
 */
export function clearAdminApiKey(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem("admin_api_key");
  }
}

class ApiClient {
  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_BASE}${endpoint}`;
    const apiKey = getAdminApiKey();
    
    const res = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
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

  /**
   * Upload a file to the knowledge base.
   * 
   * @param file - The file to upload
   * @param collectionId - Optional collection to add document to
   * @param startProcessing - If false, file is stored but not processed (use for bulk uploads)
   */
  async uploadFile(file: File, collectionId?: string, startProcessing = false): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const params = new URLSearchParams();
    if (collectionId) params.set("collection_id", collectionId);
    params.set("start_processing", String(startProcessing));

    const url = `${API_BASE}/api/upload?${params}`;
    const apiKey = getAdminApiKey();

    const res = await fetch(url, {
      method: "POST",
      body: formData,
      headers: apiKey ? { "X-API-Key": apiKey } : {},
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: "Upload failed" }));
      throw new Error(error.detail || `HTTP ${res.status}`);
    }

    return res.json();
  }

  /**
   * Create a custom knowledge input (Q&A, text, or markdown).
   * 
   * This allows users to manually add knowledge to the knowledge base without uploading files.
   * The content is saved as a markdown file and processed like any uploaded document.
   * The filename is automatically generated using an LLM.
   * 
   * @param input - The custom input data
   */
  async createCustomInput(input: CustomInputCreate): Promise<CustomInputResponse> {
    return this.request<CustomInputResponse>("/api/custom-input", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  /**
   * Generate a topic hint for custom content using AI.
   * Also returns similar existing topics from the knowledge base.
   */
  async generateTopicHint(
    content: string,
    inputType: string,
    answer?: string
  ): Promise<{ topic_hint: string; existing_similar: string[] }> {
    return this.request<{ topic_hint: string; existing_similar: string[] }>(
      "/api/custom-input/generate-topic",
      {
        method: "POST",
        body: JSON.stringify({
          content,
          input_type: inputType,
          answer,
        }),
      }
    );
  }

  /**
   * List all custom inputs with optional search.
   */
  async getCustomInputs(search?: string, limit = 50): Promise<{ custom_inputs: CustomInputItem[]; total: number }> {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    params.set("limit", String(limit));
    return this.request<{ custom_inputs: CustomInputItem[]; total: number }>(
      `/api/custom-inputs?${params}`
    );
  }

  /**
   * Get a single custom input for editing.
   */
  async getCustomInput(documentId: string): Promise<CustomInputItem> {
    return this.request<CustomInputItem>(`/api/custom-inputs/${documentId}`);
  }

  /**
   * Get all pending documents waiting to be processed.
   */
  async getPendingDocuments(): Promise<{ pending_count: number; documents: Document[] }> {
    return this.request<{ pending_count: number; documents: Document[] }>("/api/documents/pending");
  }

  /**
   * Start processing all pending documents as a background task.
   * Use after bulk uploads.
   */
  /**
   * Start processing all pending documents.
   * If concurrency is not provided, uses BATCH_PROCESSING_CONCURRENCY from backend config.
   */
  async processPendingDocuments(concurrency?: number): Promise<TaskStartResponse & { pending_count: number }> {
    const url = concurrency !== undefined
      ? `/api/documents/process-pending?concurrency=${concurrency}`
      : `/api/documents/process-pending`;
    return this.request<TaskStartResponse & { pending_count: number }>(url, { method: "POST" });
  }

  async getDocuments(): Promise<{ documents: Document[]; total: number }> {
    return this.request<{ documents: Document[]; total: number }>("/api/documents");
  }

  async getDocument(id: string): Promise<Document> {
    return this.request<Document>(`/api/documents/${id}`);
  }

  /**
   * Get document content including all chunks.
   * Returns the full document text concatenated from all chunks.
   */
  async getDocumentContent(id: string): Promise<DocumentContent> {
    return this.request<DocumentContent>(`/api/documents/${id}/content`);
  }

  async deleteDocument(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/api/documents/${id}`, {
      method: "DELETE",
    });
  }

  /**
   * Bulk delete multiple documents.
   */
  async deleteDocuments(documentIds: string[]): Promise<{ message: string; deleted_count: number }> {
    return this.request<{ message: string; deleted_count: number }>("/api/documents/delete", {
      method: "POST",
      body: JSON.stringify({ document_ids: documentIds }),
    });
  }

  /**
   * Reprocess multiple documents using their stored original files.
   * No file re-upload needed - files are stored permanently.
   * Documents are queued and processed with controlled concurrency.
   * 
   * @param documentIds - Array of document IDs to reprocess
   * @param concurrency - Optional concurrency limit (defaults to server config)
   */
  async reprocessDocuments(documentIds: string[], concurrency?: number): Promise<{
    results: Array<{
      document_id: string;
      status: string;
      message: string;
    }>;
    total_queued: number;
    task_id?: string;
    concurrency?: number;
    message: string;
  }> {
    const params = new URLSearchParams();
    if (concurrency) params.set("concurrency", String(concurrency));
    
    const queryString = params.toString();
    const url = `/api/documents/reprocess${queryString ? `?${queryString}` : ""}`;
    
    return this.request<{
      results: Array<{
        document_id: string;
        status: string;
        message: string;
      }>;
      total_queued: number;
      task_id?: string;
      concurrency?: number;
      message: string;
    }>(url, {
      method: "POST",
      body: JSON.stringify({ document_ids: documentIds }),
    });
  }

  /**
   * Reprocess a single document.
   * If no file is provided, uses the stored original file.
   * If a file is provided, updates the stored file and reprocesses.
   */
  async reprocessDocument(
    documentId: string,
    file?: File
  ): Promise<{
    document_id: string;
    filename: string;
    status: string;
    message: string;
  }> {
    const apiKey = getAdminApiKey();
    
    if (file) {
      // With new file
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`${API_BASE}/api/documents/${documentId}/reprocess`, {
        method: "POST",
        body: formData,
        headers: apiKey ? { "X-API-Key": apiKey } : {},
      });

      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: "Reprocess failed" }));
        throw new Error(error.detail || `HTTP ${res.status}`);
      }

      return res.json();
    } else {
      // Without file - use stored file
      const res = await fetch(`${API_BASE}/api/documents/${documentId}/reprocess`, {
        method: "POST",
        headers: apiKey ? { "X-API-Key": apiKey } : {},
      });

      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: "Reprocess failed" }));
        throw new Error(error.detail || `HTTP ${res.status}`);
      }

      return res.json();
    }
  }

  /**
   * @deprecated Use reprocessDocument instead
   */
  async reprocessDocumentWithFile(
    documentId: string,
    file: File
  ): Promise<{
    document_id: string;
    filename: string;
    status: string;
    message: string;
  }> {
    return this.reprocessDocument(documentId, file);
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
      useFastSearch?: boolean;
      collectionId?: string;
    } = {}
  ): Promise<RAGResponse> {
    const {
      topK = 5,
      conversationHistory,
      useReranking = true,
      useAgentic = false,
      useGraph = true,
      useFastSearch = false,
      collectionId,
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
        use_fast_search: useFastSearch,
        ...(collectionId ? { collection_id: collectionId } : {}),
      }),
    });
  }

  /**
   * Stream the RAG response for better UX.
   * Returns an async generator that yields stream events.
   * 
   * When useAgentic is true, additional events are yielded:
   * - thinking: Reasoning step updates
   * - sub_questions: Decomposed research questions
   * - retrieval: Source retrieval progress
   * - retrieval_stats: Final retrieval statistics
   * 
   * When useFastSearch is true:
   * - Uses simple vector search only (no hybrid/reranking)
   * - Fastest response time for quick queries
   */
  async *askStream(
    question: string,
    options: {
      topK?: number;
      conversationHistory?: ConversationMessage[];
      useReranking?: boolean;
      useGraph?: boolean;
      useAgentic?: boolean;
      useFastSearch?: boolean;
      collectionId?: string;
    } = {}
  ): AsyncGenerator<StreamEvent, void, unknown> {
    const {
      topK = 5,
      conversationHistory,
      useReranking = true,
      useGraph = true,
      useAgentic = false,
      useFastSearch = false,
      collectionId,
    } = options;

    const apiKey = getAdminApiKey();
    const res = await fetch(`${API_BASE}/api/ask/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
      },
      body: JSON.stringify({
        question,
        top_k: topK,
        conversation_history: conversationHistory,
        use_reranking: useReranking,
        use_graph: useGraph,
        use_agentic: useAgentic,
        use_fast_search: useFastSearch,
        ...(collectionId ? { collection_id: collectionId } : {}),
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
  // Collection API
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

  async deleteCollection(id: string): Promise<{ message: string; documents_moved: number }> {
    return this.request<{ message: string; documents_moved: number }>(
      `/api/collections/${id}`,
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
  // Community API
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

  async analyzeRelationships(collectionId?: string, scope = "full", rebuild = false): Promise<TaskStartResponse> {
    const params = new URLSearchParams({ scope });
    if (collectionId) params.set("collection_id", collectionId);
    if (rebuild) params.set("rebuild", "true");

    return this.request<TaskStartResponse>(
      `/api/graph/relationships/analyze?${params}`,
      { method: "POST" }
    );
  }

  async deleteAllRelationships(): Promise<{ relationships_deleted: number }> {
    return this.request("/api/graph/relationships", { method: "DELETE" });
  }

  // ===========================================================================
  // Task API (Background Task Tracking)
  // ===========================================================================

  async getTaskStatus(taskId: string): Promise<TaskProgress> {
    return this.request<TaskProgress>(`/api/tasks/${taskId}`);
  }

  async listTasks(status?: string, taskType?: string): Promise<{ tasks: TaskProgress[]; total: number }> {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (taskType) params.set("task_type", taskType);
    const query = params.toString();
    return this.request(`/api/tasks${query ? `?${query}` : ""}`);
  }

  async getTaskResult<T = Record<string, unknown>>(taskId: string): Promise<T | null> {
    const url = `${API_BASE}/api/tasks/${taskId}/result`;
    const apiKey = getAdminApiKey();
    const res = await fetch(url, {
      headers: { 
        "Content-Type": "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
      },
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

  async deleteCommunity(id: number): Promise<{ deleted: boolean; community_id: number; entities_unlinked: number }> {
    return this.request(`/api/graph/communities/${id}`, { method: "DELETE" });
  }

  async deleteAllCommunities(): Promise<{ communities_deleted: number; entities_unlinked: number }> {
    return this.request("/api/graph/communities", { method: "DELETE" });
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
  // Cleanup API
  // ===========================================================================

  async cleanupOrphanedEntities(): Promise<{ message: string; orphaned_entities_removed: number; orphaned_communities_removed: number }> {
    return this.request("/api/cleanup/orphaned-entities", { method: "POST" });
  }

  // ===========================================================================
  // Extended Thinking Stream
  // ===========================================================================

  // ===========================================================================
  // Knowledge Graph Visualization API
  // ===========================================================================

  /**
   * Get graph data for visualization.
   * Returns nodes (entities), edges (relationships), and stats for the knowledge graph.
   * 
   * @param limit - Maximum number of core entities to fetch (0 = all entities)
   * @param includeNeighbors - If true, expands entity set to include 1-hop neighbors for more relationships
   */
  async getGraphVisualization(limit = 100, includeNeighbors = true): Promise<GraphData> {
    const params = new URLSearchParams();
    // Always send limit - 0 means "fetch all"
    params.set("limit", String(limit));
    params.set("include_neighbors", String(includeNeighbors));
    
    const url = `/api/graph/visualization?${params}`;
    return this.request<GraphData>(url);
  }

  /**
   * Get details about a specific entity and its relationships.
   */
  async getEntityDetails(entityName: string, maxHops = 2): Promise<EntityDetails> {
    return this.request<EntityDetails>(
      `/api/graph/entity/${encodeURIComponent(entityName)}?max_hops=${maxHops}`
    );
  }

  /**
   * Get an entity and all its relationships up to maxDepth hops.
   * Enables focused graph exploration from a specific entity.
   */
  async getEntityRelationships(
    entityName: string,
    maxDepth = 2,
    limit = 50
  ): Promise<EntityRelationshipsResponse> {
    const params = new URLSearchParams({
      max_depth: String(maxDepth),
      limit: String(limit),
    });
    return this.request<EntityRelationshipsResponse>(
      `/api/graph/entity/${encodeURIComponent(entityName)}/relationships?${params}`
    );
  }

  /**
   * Get a subgraph containing specified entities and their interconnections.
   * Method for focused graph visualization of specific entities.
   */
  async getGraphSubgraph(
    entityNames: string[],
    includeConnections = true
  ): Promise<GraphData> {
    return this.request<GraphData>(
      `/api/graph/subgraph?include_connections=${includeConnections}`,
      {
        method: "POST",
        body: JSON.stringify(entityNames),
      }
    );
  }

  /**
   * Search for entities by name.
   */
  async searchEntities(query: string): Promise<{ query: string; results: Array<{ name: string; type: string; description: string; score: number }> }> {
    return this.request<{ query: string; results: Array<{ name: string; type: string; description: string; score: number }> }>(
      `/api/graph/search?query=${encodeURIComponent(query)}`
    );
  }

  /**
   * List entities with optional type filter.
   */
  async getEntities(entityType?: string, limit = 50): Promise<{ entities: Array<{ name: string; type: string; description: string; mention_count: number }>; total: number }> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (entityType) params.set("entity_type", entityType);
    return this.request<{ entities: Array<{ name: string; type: string; description: string; mention_count: number }>; total: number }>(
      `/api/graph/entities?${params}`
    );
  }

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

    const apiKey = getAdminApiKey();
    const res = await fetch(`${API_BASE}/api/ask/stream/thinking`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
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
  // ===========================================================================
  // Turbo Mode API (Compute3 GPU Acceleration)
  // ===========================================================================

  /**
   * Get Turbo Mode status.
   * Returns whether Turbo Mode is available (API key configured) and active (GPU job running).
   */
  async getTurboStatus(): Promise<TurboStatus> {
    return this.request<TurboStatus>("/api/turbo/status");
  }

  /**
   * Get Compute3 account balance.
   */
  async getTurboBalance(): Promise<TurboBalance> {
    return this.request<TurboBalance>("/api/turbo/balance");
  }

  /**
   * Start Turbo Mode by launching a GPU job on Compute3.
   * 
   * @param options - Optional configuration for the GPU job
   */
  async startTurboMode(options?: {
    runtime?: number;
    gpuType?: string;
    gpuCount?: number;
  }): Promise<{ message: string; job: TurboJob }> {
    const params = new URLSearchParams();
    if (options?.runtime) params.set("runtime", String(options.runtime));
    if (options?.gpuType) params.set("gpu_type", options.gpuType);
    if (options?.gpuCount) params.set("gpu_count", String(options.gpuCount));
    
    const queryString = params.toString();
    const url = `/api/turbo/start${queryString ? `?${queryString}` : ""}`;
    
    return this.request<{ message: string; job: TurboJob }>(url, { method: "POST" });
  }

  /**
   * Stop Turbo Mode by cancelling the active GPU job.
   */
  async stopTurboMode(jobId?: string): Promise<{ message: string; job_id?: string }> {
    const params = new URLSearchParams();
    if (jobId) params.set("job_id", jobId);
    
    const queryString = params.toString();
    const url = `/api/turbo/stop${queryString ? `?${queryString}` : ""}`;
    
    return this.request<{ message: string; job_id?: string }>(url, { method: "POST" });
  }

  /**
   * Extend Turbo Mode runtime.
   * 
   * @param additionalSeconds - Additional runtime in seconds
   * @param jobId - Optional specific job ID to extend
   */
  async extendTurboMode(additionalSeconds: number, jobId?: string): Promise<{ message: string; job: TurboJob }> {
    const params = new URLSearchParams();
    params.set("additional_seconds", String(additionalSeconds));
    if (jobId) params.set("job_id", jobId);
    
    return this.request<{ message: string; job: TurboJob }>(
      `/api/turbo/extend?${params}`,
      { method: "POST" }
    );
  }

  /**
   * List all Turbo Mode jobs (current and historical).
   */
  async listTurboJobs(state?: string): Promise<{ jobs: TurboJob[]; total: number }> {
    const params = new URLSearchParams();
    if (state) params.set("state", state);
    
    const queryString = params.toString();
    const url = `/api/turbo/jobs${queryString ? `?${queryString}` : ""}`;
    
    return this.request<{ jobs: TurboJob[]; total: number }>(url);
  }

  /**
   * Get details of a specific Turbo Mode job.
   */
  async getTurboJob(jobId: string): Promise<TurboJob> {
    return this.request<TurboJob>(`/api/turbo/jobs/${jobId}`);
  }

  /**
   * Get logs from a Turbo Mode job.
   */
  async getTurboJobLogs(jobId: string): Promise<{ job_id: string; logs: string }> {
    return this.request<{ job_id: string; logs: string }>(`/api/turbo/jobs/${jobId}/logs`);
  }

  // ===========================================================================
  // Admin API Key Management
  // ===========================================================================

  /**
   * List all API keys (admin only).
   */
  async listApiKeys(): Promise<APIKeyListItem[]> {
    return this.request<APIKeyListItem[]>("/api/admin/api-keys");
  }

  /**
   * Create a new API key (admin only).
   * The actual key is returned only once in the response.
   */
  async createApiKey(request: CreateAPIKeyRequest): Promise<CreateAPIKeyResponse> {
    return this.request<CreateAPIKeyResponse>("/api/admin/api-keys", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  /**
   * Get a specific API key by ID (admin only).
   */
  async getApiKey(keyId: string): Promise<APIKeyListItem> {
    return this.request<APIKeyListItem>(`/api/admin/api-keys/${keyId}`);
  }

  /**
   * Update an API key (admin only).
   */
  async updateApiKey(keyId: string, request: UpdateAPIKeyRequest): Promise<APIKeyListItem> {
    return this.request<APIKeyListItem>(`/api/admin/api-keys/${keyId}`, {
      method: "PATCH",
      body: JSON.stringify(request),
    });
  }

  /**
   * Delete an API key (admin only).
   */
  async deleteApiKey(keyId: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/api/admin/api-keys/${keyId}`, {
      method: "DELETE",
    });
  }

  /**
   * Revoke an API key (admin only).
   */
  async revokeApiKey(keyId: string): Promise<APIKeyListItem> {
    return this.request<APIKeyListItem>(`/api/admin/api-keys/${keyId}/revoke`, {
      method: "POST",
    });
  }

  /**
   * Activate a revoked API key (admin only).
   */
  async activateApiKey(keyId: string): Promise<APIKeyListItem> {
    return this.request<APIKeyListItem>(`/api/admin/api-keys/${keyId}/activate`, {
      method: "POST",
    });
  }

  // ===========================================================================
  // API Key Statistics
  // ===========================================================================

  /**
   * List all API keys with their usage statistics (admin only).
   */
  async listApiKeysWithStats(): Promise<APIKeyWithStats[]> {
    return this.request<APIKeyWithStats[]>("/api/admin/api-keys/with-stats");
  }

  /**
   * Get detailed usage statistics for a specific API key (admin only).
   */
  async getApiKeyStats(keyId: string): Promise<APIKeyStats> {
    return this.request<APIKeyStats>(`/api/admin/api-keys/${keyId}/stats`);
  }

  /**
   * Get daily usage history for an API key (admin only).
   * 
   * @param keyId - The API key ID
   * @param days - Number of days of history (default 30, max 365)
   */
  async getApiKeyUsageHistory(keyId: string, days = 30): Promise<APIKeyUsageHistoryResponse> {
    return this.request<APIKeyUsageHistoryResponse>(
      `/api/admin/api-keys/${keyId}/usage-history?days=${days}`
    );
  }

  /**
   * Get aggregated statistics across all API keys (admin only).
   */
  async getAdminStatsOverview(): Promise<AdminStatsOverview> {
    return this.request<AdminStatsOverview>("/api/admin/stats/overview");
  }

  // ===========================================================================
  // System Configuration
  // ===========================================================================

  /**
   * Get system configuration (admin only).
   * 
   * Returns current system configuration excluding sensitive data
   * like API keys, passwords, and secrets.
   */
  async getSystemConfig(): Promise<SystemConfig> {
    return this.request<SystemConfig>("/api/admin/config");
  }

  // ===========================================================================
  // Entity Deduplication
  // ===========================================================================

  /**
   * Suggest duplicate entity groups for review.
   */
  async suggestDuplicates(threshold = 0.75, limit = 100): Promise<DuplicateSuggestionsResponse> {
    const params = new URLSearchParams({
      threshold: String(threshold),
      limit: String(limit),
    });
    return this.request<DuplicateSuggestionsResponse>(`/api/entities/duplicates?${params}`);
  }

  /**
   * Get entity merge history.
   */
  async getMergeHistory(limit = 50): Promise<MergeHistoryResponse> {
    return this.request<MergeHistoryResponse>(`/api/entities/merge-history?limit=${limit}`);
  }

  /**
   * Merge duplicate entities into a canonical entity.
   */
  async mergeEntities(request: MergeEntitiesRequest): Promise<MergeEntitiesResponse> {
    return this.request<MergeEntitiesResponse>("/api/entities/merge", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  // ===========================================================================
  // System Reset
  // ===========================================================================

  /**
   * Reset the system by deleting selected data (admin only).
   * 
   * WARNING: This is a destructive operation that cannot be undone.
   * 
   * @param options - What to delete
   */
  async resetSystem(options: SystemResetRequest): Promise<SystemResetResponse> {
    return this.request<SystemResetResponse>("/api/admin/reset", {
      method: "POST",
      body: JSON.stringify(options),
    });
  }
}

export const api = new ApiClient();
