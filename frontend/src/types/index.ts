export interface Document {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  file_path?: string | null;  // Path to stored original file
  upload_date: string;
  chunk_count: number;
  processing_status: ProcessingStatus;
  error_message?: string;
  // Progress tracking fields
  progress_current?: number;
  progress_total?: number;
  progress_message?: string;
  // Collection info
  collection_id?: string | null;
  collection_name?: string | null;
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

export interface DocumentContent {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  upload_date: string;
  chunk_count: number;
  chunks: Array<{
    id: string;
    content: string;
    chunk_index: number;
  }>;
  full_content: string;
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
  use_fast_search?: boolean;
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
  // Fast mode indicator
  fast_mode?: boolean;
  // Agentic mode events
  thinking?: string;
  sub_questions?: string[];
  retrieval?: string;
  retrieval_stats?: {
    total_sources: number;
    unique_sources: number;
    communities_used: number;
  };
  communities_used?: number[];
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
  community_count?: number;
  collection_count?: number;
}

export interface HealthResponse {
  status: string;
  neo4j_connected: boolean;
  version: string;
}

// =============================================================================
// Collection Types
// =============================================================================

export interface Collection {
  id: string;
  name: string;
  description?: string;
  created_at: string;
  document_count: number;
  entity_count: number;
}

export interface CollectionCreate {
  name: string;
  description?: string;
}

export interface CollectionEntity {
  name: string;
  type: string;
  description: string;
  community_id?: number;
  mention_count: number;
}

// =============================================================================
// Community Types
// =============================================================================

export interface Community {
  id: number;
  name?: string;
  summary?: string;
  entity_count: number;
  entities?: Array<{
    name: string;
    type: string;
    description: string;
  }>;
  sample_entities?: string[];
}

// =============================================================================
// Extended Thinking Types
// =============================================================================

export interface ThinkingStreamEvent extends StreamEvent {
  thinking?: string;
  search?: string;
  retrieval?: string;
  sub_questions?: string[];
  retrieval_stats?: {
    total_sources: number;
    unique_sources: number;
    communities_used: number;
  };
  communities_used?: number[];
}

// =============================================================================
// Background Task Types
// =============================================================================

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface TaskProgress {
  task_id: string;
  task_type: string;
  status: TaskStatus;
  progress_current: number;
  progress_total: number;
  progress_percent: number;
  message: string;
  started_at?: string;
  completed_at?: string;
  error?: string;
  result?: Record<string, unknown>;
}

export interface TaskStartResponse {
  task_id: string;
  status: TaskStatus;
  message: string;
}

// =============================================================================
// Knowledge Graph Visualization Types
// =============================================================================

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  description?: string;
  community_id?: number;
  mention_count: number;
  // Force graph properties
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  description?: string;
  weight?: number;  // Relationship weight (0-10)
}

export interface GraphStats {
  displayed_entities: number;
  displayed_relationships: number;
  total_entities: number;
  total_relationships: number;
  neighbor_entities_included?: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats?: GraphStats;  // Metadata about graph data
}

export interface EntityRelationshipsResponse {
  entity: {
    name: string;
    type: string;
    description: string;
    community_id?: number;
    mention_count: number;
  } | null;
  related_entities: Array<{
    name: string;
    type: string;
    description?: string;
    community_id?: number;
  }>;
  relationships: Array<{
    source: string;
    target: string;
    type: string;
    description?: string;
    weight?: number;
  }>;
}

export interface EntityDetails {
  name: string;
  type: string;
  description: string;
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

// =============================================================================
// Turbo Mode Types (Compute3 GPU Acceleration)
// =============================================================================

export interface TurboJob {
  job_id: string;
  state: string;
  gpu_type: string;
  gpu_count: number;
  region: string;
  price_per_hour: number;
  runtime: number;
  hostname?: string;
  base_url?: string;
  is_running: boolean;
  is_ready: boolean;  // vLLM server is ready for requests
  created_at?: number;
  started_at?: number;
  completed_at?: number;
  completed?: boolean;
}

export interface TurboStatus {
  available: boolean;
  active: boolean;   // GPU job is running
  ready: boolean;    // vLLM server is ready for requests
  job?: TurboJob;
  config?: {
    gpu_type: string;
    gpu_count: number;
    model: string;
    default_runtime: number;
  };
}

export interface TurboBalance {
  total?: number;
  available?: number;
  reserved?: number;
  error?: string;
}

// =============================================================================
// Custom Input Types (Manual Q&A, Text, Markdown)
// =============================================================================

export type CustomInputType = "qa" | "text" | "markdown";

export interface CustomInputCreate {
  input_type: CustomInputType;
  content: string;
  answer?: string;  // Only for Q&A type
  title?: string;   // Optional hint for filename generation
  collection_id?: string;
  start_processing?: boolean;
}

export interface CustomInputResponse {
  document_id: string;
  filename: string;
  status: ProcessingStatus;
  message: string;
  input_type: CustomInputType;
}

export interface CustomInputItem {
  id: string;
  filename: string;
  input_type: CustomInputType;
  content: string;
  answer?: string | null;
  topic_hint?: string | null;
  created_at: string;
  status: ProcessingStatus;
  collection_id?: string | null;
  collection_name?: string | null;
}
