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
  // Source tracking
  source?: string;
  // Degraded-document signals (-1 entity_count = unknown / extraction disabled)
  entity_count?: number;
  unembedded_chunk_count?: number;
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

// =============================================================================
// Ask / Chat Types (shared between AskPanel and ChatMessage)
// =============================================================================

export interface AskSource {
  document_id: string;
  chunk_id: string;
  content: string;
  score: number;
  /** Conversation-stable source id from the backend (content hash — keeps
   *  citation identity across turns; unrelated to per-turn [src_N] numbers). */
  sid?: string;
  metadata: {
    filename: string;
    chunk_index?: number;
    rerank_score?: number;
  };
}

export interface AskMessage {
  /** Stable client-side identity — React key + streaming update target. */
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: AskSource[];
  graphContext?: GraphContext;
  reasoningSteps?: string[];
  thinkingSteps?: string[];
  subQuestions?: string[];
  isStreaming?: boolean;
  reranked?: boolean;
  /** Latest backend pipeline stage label (from the `status` SSE event). */
  statusMessage?: string;
}

export interface StreamEvent {
  content?: string;
  sources?: AskSource[];
  graph_context?: GraphContext;
  done?: boolean;
  /** Set on `done` when a `memory_update` event will still follow. */
  pending_memory?: boolean;
  /** Opaque conversation-memory blob; echo back as `conversation_memory`. */
  memory_update?: unknown;
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
  // Agent Skills events
  skill_tool?: string;
  skill_name?: string;
  is_error?: boolean;
  // Pipeline stage status (drives the live thinking indicator)
  status?: { stage?: string; message?: string };
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  status: ProcessingStatus;
  message: string;
  source?: string;
}

export interface Stats {
  document_count: number;
  chunk_count: number;
  total_size: number;
  entity_count?: number;
  relationship_count?: number;
  per_chunk_relationship_count?: number;
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
  entity_relationship_ratio?: number;
  relationship_target_ratio?: number;
  // Monthly unit quota (MAX_QUERIES_PER_MONTH, denominated in LLM completions)
  monthly_usage_used?: number;
  monthly_usage_limit?: number;
  monthly_usage_query?: number;
  monthly_usage_processing?: number;
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
export type CollectionScope = "all" | "restricted";

export interface APIKeyListItem {
  id: string;
  name: string;
  key_prefix: string;
  permissions: APIKeyPermission[];
  is_active: boolean;
  created_at: string;
  last_used_at?: string | null;
  created_by: string;
  collection_scope: CollectionScope;
  allowed_collections: string[];
  allowed_collection_names?: string[] | null;
}

export interface CreateAPIKeyRequest {
  name: string;
  permissions: APIKeyPermission[];
  collection_scope?: CollectionScope;
  allowed_collections?: string[];
}

export interface CreateAPIKeyResponse {
  id: string;
  name: string;
  key: string;  // The actual key - only shown once!
  key_prefix: string;
  permissions: APIKeyPermission[];
  created_at: string;
  collection_scope: CollectionScope;
  allowed_collections: string[];
}

export interface UpdateAPIKeyRequest {
  name?: string;
  permissions?: APIKeyPermission[];
  is_active?: boolean;
  collection_scope?: CollectionScope;
  allowed_collections?: string[];
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
// Agent Skills Types (agentskills.io)
// =============================================================================

export interface SkillInfo {
  skill_id: string;
  name: string;
  description: string;
  version?: string | null;
  author?: string | null;
  license?: string | null;
  source: "local" | "registry" | "url";
  source_url?: string | null;
  skill_type: "instruction" | "tool";
  enabled: boolean;
  installed_at: string;
  tool_count: number;
  tool_names: string[];
  config_status?: "configured" | "needs_setup" | null;
}

export interface SkillDetail extends SkillInfo {
  body: string;
  tools_config?: Record<string, unknown>[] | null;
}

export interface SkillRegistryItem {
  namespace: string;
  name: string;
  description: string;
  install_count?: number | null;
  download_url: string;
}

export interface SkillConfigVariable {
  name: string;
  description: string;
  required: boolean;
  type: "secret" | "text";
  auth_header?: string;
}

export interface SkillConfigSchema {
  skill_id: string;
  variables: SkillConfigVariable[];
}

export interface SkillConfigResponse {
  skill_id: string;
  schema: SkillConfigVariable[] | null;
  values: Record<string, string>;
  base_url?: string | null;
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
  /** "complete" carries groups; "running" (HTTP 202) bodies omit them —
   *  api.suggestDuplicates polls internally and only resolves when complete. */
  status?: "complete" | "running";
  /** Scan progress 0..1, present while status is "running". */
  progress?: number;
  /** True when served from the server-side scan cache. */
  cached?: boolean;
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
  merge_history_deleted: number;
  system_meta_deleted: number;
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
  openai_api_base: string;
  openai_max_context: number;
  openai_max_output_tokens: number;
  extraction_model: string;
  extraction_api_base: string;
  extraction_max_context: number;
  extraction_max_output_tokens: number;
  relationship_max_context: number;
  relationship_max_output_tokens: number;
  relationship_batch_max_output_tokens: number;
  parallel_relationship_batches: number;

  // Relationship Extraction Model
  relationship_model: string;
  relationship_api_base: string;
  concurrent_relations: number;

  // Vision Model
  vision_model_available: boolean;
  vision_model: string;
  vision_api_base: string;
  vision_max_concurrent: number;
  vision_max_output_tokens: number;
  vision_min_image_side: number;
  vision_max_image_side: number;
  vision_jpeg_quality: number;

  // Embedding Configuration
  embedding_model: string;
  embedding_dimension: number;
  embedding_api_base: string;
  embedding_send_dimensions: boolean;
  embedding_max_input_tokens: number;
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
  display_full_system_config: boolean;

  // Security
  prompt_security: boolean;
  // Experimental master flag (ENABLE_INGESTION_INJECTION_SCAN); false = the
  // ingestion scan feature is absent and its toggle is hidden.
  enable_ingestion_injection_scan: boolean;
  ingestion_injection_scan: boolean;
  prompt_guard: boolean;

  // Privacy (LLM observability content handling)
  langfuse_tracing_active: boolean;
  langfuse_log_extended: boolean;

  // Agent Skills
  enable_skills: boolean;
  enable_skill_scripts: boolean;
  max_skill_tools: number;

  // Git Integration
  enable_git_integration: boolean;

  // Web Crawl (MDHarvest powered by Crawl4ai)
  enable_web_crawl: boolean;
}

// =============================================================================
// Feature Flags (GET /api/features)
// =============================================================================

export interface FeatureFlags {
  enable_collections: boolean;
  enable_skills: boolean;
  enable_git_integration: boolean;
  enable_web_crawl: boolean;
}

// =============================================================================
// Web Import (MDHarvest powered by Crawl4ai)
// =============================================================================

export type WebContentFilter = "fit" | "raw" | "bm25";

export interface WebImportRequest {
  urls: string[];
  collection_id?: string;
  content_filter?: WebContentFilter;
  query?: string;
}

export interface WebImportResponse {
  task_id: string;
  accepted_urls: number;
  message: string;
}

export interface WebDiscoverLink {
  url: string;
  title: string;
}

export interface WebDiscoverResponse {
  source_url: string;
  domain: string;
  links: WebDiscoverLink[];
}

// =============================================================================
// Git Integration
// =============================================================================

export type GitVendor = "github" | "gitlab" | "gitea";
export type GitAccessLevel = "read" | "read_write";

export interface GitConnection {
  id: string;
  vendor: GitVendor;
  base_url?: string | null;
  repo_owner: string;
  repo_name: string;
  pat_masked: string;
  access_level: GitAccessLevel;
  branch?: string | null;
  default_branch?: string | null;
  include_globs: string[];
  exclude_globs: string[];
  wiki_enabled: boolean;
  collection_id?: string | null;
  sync_interval_minutes: number;
  last_synced_sha?: string | null;
  last_synced_at?: string | null;
  next_sync_due?: string | null;
  sync_status?: string | null;
  created_at?: string | null;
}

export interface GitConnectionCreate {
  vendor: GitVendor;
  base_url?: string | null;
  repo_owner: string;
  repo_name: string;
  pat: string;
  access_level: GitAccessLevel;
  branch?: string | null;
  include_globs: string[];
  exclude_globs: string[];
  wiki_enabled: boolean;
  collection_id?: string | null;
  sync_interval_minutes: number;
}

export interface GitConnectionUpdate {
  pat?: string;
  access_level?: GitAccessLevel;
  branch?: string | null;
  include_globs?: string[];
  exclude_globs?: string[];
  wiki_enabled?: boolean;
  collection_id?: string | null;
  sync_interval_minutes?: number;
}

export interface GitVerifyResponse {
  valid: boolean;
  login?: string | null;
  message?: string | null;
}

export interface GitRepoBrowseItem {
  owner: string;
  name: string;
  full_name: string;
  default_branch?: string | null;
  private: boolean;
  web_url?: string | null;
}

export interface GitSyncTriggerResponse {
  task_id: string;
  connection_id: string;
  message: string;
}

export interface GitOrphanedDocument {
  id: string;
  filename: string;
  git_path: string;
}
