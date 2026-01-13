export interface Document {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  upload_date: string;
  chunk_count: number;
  processing_status: ProcessingStatus;
  error_message?: string;
  // Progress tracking fields
  progress_current?: number;
  progress_total?: number;
  progress_message?: string;
}

export type ProcessingStatus = "pending" | "processing" | "extracting" | "completed" | "failed";

export interface DocumentChunk {
  id: string;
  document_id: string;
  content: string;
  embedding?: number[];
  chunk_index: number;
  metadata: Record<string, unknown>;
}

export interface SearchResult {
  document_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata: {
    filename: string;
    chunk_index: number;
  };
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total_results: number;
}

export interface GraphContext {
  entities: Array<{
    name: string;
    type: string;
    description: string;
  }>;
  relationships: Array<{
    source: string;
    target: string;
    type: string;
    description?: string;
  }>;
  chunks: Array<{
    chunk_id: string;
    content: string;
    document_id: string;
    filename: string;
  }>;
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
}

export interface RAGRequest {
  question: string;
  top_k?: number;
  use_graph?: boolean;
  max_hops?: number;
  conversation_history?: ConversationMessage[];
  use_reranking?: boolean;
  use_agentic?: boolean;
}

export interface RAGResponse {
  question: string;
  answer: string;
  sources: SearchResult[];
  graph_context?: GraphContext;
  reranked?: boolean;
  reasoning_steps?: string[];
}

export interface StreamEvent {
  content?: string;
  sources?: SearchResult[];
  graph_context?: GraphContext;
  done?: boolean;
  error?: string;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  status: ProcessingStatus;
  message: string;
}

export interface Stats {
  document_count: number;
  chunk_count: number;
  total_size: number;
  entity_count?: number;
  relationship_count?: number;
}

export interface HealthResponse {
  status: string;
  neo4j_connected: boolean;
  version: string;
}
