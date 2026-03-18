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
  // Image analysis progress
  image_progress_current?: number;
  image_progress_total?: number;
  image_progress_message?: string;
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
  pending_count?: number;
  completed_count?: number;
  failed_count?: number;
  processing_count?: number;
  avg_chunks_per_doc?: number;
  entity_type_counts?: Record<string, number>;
  avg_entity_mentions?: number;
  last_relationship_analysis_at?: string | null;
  last_community_detection_at?: string | null;
  last_entity_merge_at?: string | null;
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

// =============================================================================
// Admin / API Key Types
// =============================================================================

export type APIKeyPermission = "read" | "manage";

export interface APIKeyListItem {
  id: string;
  name: string;
  key_prefix: string;
  permissions: APIKeyPermission[];
  is_active: boolean;
  created_at: string;
  last_used_at?: string | null;
  created_by: string;
}

export interface CreateAPIKeyRequest {
  name: string;
  permissions: APIKeyPermission[];
}

export interface CreateAPIKeyResponse {
  id: string;
  name: string;
  key: string;  // The actual key - only shown once!
  key_prefix: string;
  permissions: APIKeyPermission[];
  created_at: string;
}

export interface UpdateAPIKeyRequest {
  name?: string;
  permissions?: APIKeyPermission[];
  is_active?: boolean;
}

// =============================================================================
// API Key Usage Statistics Types
// =============================================================================

export interface APIKeyStats {
  total_requests: number;
  requests_today: number;
  requests_this_week: number;
  requests_this_month: number;
  error_count: number;
  last_error_at?: string | null;
  last_error_message?: string | null;
  endpoint_breakdown: Record<string, number>;
}

export interface APIKeyUsageDataPoint {
  date: string;
  requests: number;
  errors: number;
}

export interface APIKeyWithStats extends APIKeyListItem {
  stats?: APIKeyStats;
}

export interface APIKeyUsageHistoryResponse {
  key_id: string;
  key_name: string;
  history: APIKeyUsageDataPoint[];
  period_days: number;
}

export interface AdminStatsOverview {
  total_keys: number;
  active_keys: number;
  total_requests_all_time: number;
  total_requests_today: number;
  total_requests_this_week: number;
  total_requests_this_month: number;
  total_errors: number;
  most_active_key?: string | null;
  endpoint_breakdown: Record<string, number>;
}

// =============================================================================
// Entity Deduplication Types
// =============================================================================

export interface DuplicateEntityInfo {
  name: string;
  type: string;
  description: string;
  mention_count: number;
  relationship_count: number;
}

export interface DuplicateGroup {
  suggested_canonical: string;
  entities: DuplicateEntityInfo[];
  similarity: number;
  method: string;
}

export interface DuplicateSuggestionsResponse {
  groups: DuplicateGroup[];
  total_groups: number;
}

export interface MergeEntitiesRequest {
  canonical: string;
  merge: string[];
}

export interface MergeEntitiesResponse {
  canonical: string;
  merged: string[];
  relationships_retargeted: number;
  aliases_added: number;
  chunks_relinked: number;
}

export interface MergeHistoryEntitySnapshot {
  name: string;
  type: string;
  description: string;
  mention_count: number;
  relationship_count: number;
  is_canonical: boolean;
}

export interface MergeHistoryEntry {
  id: string;
  canonical_name: string;
  merged_names: string[];
  merged_count: number;
  relationships_retargeted: number;
  chunks_relinked: number;
  merged_description: string;
  entities_snapshot: MergeHistoryEntitySnapshot[];
  merged_at: string;
}

export interface MergeHistoryResponse {
  history: MergeHistoryEntry[];
  total: number;
}

// =============================================================================
// System Reset Types
// =============================================================================

export interface SystemResetRequest {
  delete_documents?: boolean;
  delete_uploaded_files?: boolean;
  delete_custom_inputs?: boolean;
  delete_collections?: boolean;
  delete_api_keys?: boolean;
}

export interface SystemResetResponse {
  message: string;
  documents_deleted: number;
  entities_removed: number;
  communities_removed: number;
  collections_deleted: number;
  api_keys_deleted: number;
  uploaded_files_deleted: number;
  custom_inputs_deleted: number;
  processing_cancelled: number;
}

// =============================================================================
// System Configuration Types
// =============================================================================

export interface SystemConfig {
  // LLM Configuration
  openai_model: string;
  fast_mode_model: string;
  
  // Embedding Configuration
  embedding_model: string;
  embedding_dimension: number;
  use_openai_embeddings: boolean;
  
  // Upload Configuration
  max_file_size_mb: number;
  allowed_extensions: string[];
  
  // Chunking Configuration
  chunk_size: number;
  chunk_overlap: number;
  chunk_by: string;
  sentences_per_chunk: number;
  
  // GraphRAG Configuration
  enable_graph_extraction: boolean;
  max_graph_hops: number;
  concurrent_extractions: number;
  
  // Batch Processing
  batch_processing_concurrency: number;
  processing_thread_workers: number;
  
  // Enhanced RAG Configuration
  enable_reranking: boolean;
  reranking_model: string;
  enable_hybrid_search: boolean;
  vector_weight: number;
  keyword_weight: number;
  graph_weight: number;
  max_conversation_history: number;
  enable_agentic_rag: boolean;
  max_agentic_steps: number;
  
  // Community Detection
  enable_community_detection: boolean;
  min_community_size: number;
  max_communities: number;
  enable_graph_summarization: boolean;
  
  // Entity Resolution
  enable_semantic_entity_resolution: boolean;
  entity_similarity_threshold: number;
  
  // Collections
  enable_collections: boolean;
  default_collection: string;
  
  // Visibility/UX
  stream_reasoning_steps: boolean;
  show_retrieval_stats: boolean;
  
  // Security
  prompt_security: boolean;
  
  // Turbo Mode (Compute3)
  turbo_mode_available: boolean;
  compute3_gpu_type: string;
  compute3_gpu_count: number;
  compute3_model: string;
  compute3_default_runtime: number;
  
  // Vision Model
  vision_model_available: boolean;
  vision_model: string;
}
