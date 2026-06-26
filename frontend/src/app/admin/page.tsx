"use client";

import { useState, useEffect, useCallback, createContext, useContext } from "react";
import { PageTransition } from "@/components/layout";
import { SystemResetModal, ApiKeyManager, LibraryTransferSection, SkillsManager, GitIntegrations } from "@/components/admin";
import { motion, AnimatePresence } from "framer-motion";
import {
  LogOut,
  ChevronDown,
  Loader2,
  AlertTriangle,
  Trash2,
  Settings2,
  Brain,
  Database,
  FileText,
  Search,
  Network,
  Shield,
  Lock,
  Eye,
  Check,
  X,
  BarChart3,
  FolderOpen,
  BookOpen,
  Link2,
  Users,
  Clock,
  CheckCircle2,
  XCircle,
  Layers,
  Share2,
  Info,
} from "lucide-react";
import { logout } from "@/lib/auth";
import { api, clearAdminApiKey } from "@/lib/api";
import { formatBytes, formatModelName, formatApiBase } from "@/lib/utils";
import type { SystemConfig, Stats } from "@/types";

// Helper component for displaying config items with optional tooltip
// When false, advanced tuning knobs are hidden from the System Config panel.
// Driven by the DISPLAY_FULL_SYSTEM_CONFIG backend flag. Defaults to true so a
// ConfigItem rendered outside a provider always shows.
const DisplayFullConfigContext = createContext(true);

function ConfigItem({ label, value, type = "text", tooltip, format, advanced }: { label: string; value: string | number | boolean; type?: "text" | "boolean" | "list"; tooltip?: string; format?: "model" | "apiBase"; advanced?: boolean }) {
  const displayFull = useContext(DisplayFullConfigContext);
  if (advanced && !displayFull) return null;

  if (type === "boolean") {
    return (
      <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
        <span className="text-muted-foreground text-sm flex items-center gap-1.5">
          {label}
          {tooltip && (
            <span className="relative group">
              <Info className="w-3 h-3 text-muted-foreground/50 cursor-help" />
              <span className="absolute top-full left-0 mt-1.5 px-2.5 py-1.5 bg-popover border border-border text-popover-foreground text-xs rounded-md shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-normal w-56 z-50 leading-relaxed">
                {tooltip}
              </span>
            </span>
          )}
        </span>
        {value ? (
          <span className="flex items-center gap-1 text-green-500 text-sm">
            <Check className="w-3.5 h-3.5" /> Enabled
          </span>
        ) : (
          <span className="flex items-center gap-1 text-muted-foreground text-sm">
            <X className="w-3.5 h-3.5" /> Disabled
          </span>
        )}
      </div>
    );
  }

  const rawValue = String(value);
  const displayValue =
    format === "model" ? formatModelName(rawValue) : format === "apiBase" ? formatApiBase(rawValue) : rawValue;

  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-muted-foreground text-sm flex items-center gap-1.5">
        {label}
        {tooltip && (
          <span className="relative group">
            <Info className="w-3 h-3 text-muted-foreground/50 cursor-help" />
            <span className="absolute top-full left-0 mt-1.5 px-2.5 py-1.5 bg-popover border border-border text-popover-foreground text-xs rounded-md shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-normal w-56 z-50 leading-relaxed">
              {tooltip}
            </span>
          </span>
        )}
      </span>
      <span className="text-foreground text-sm font-mono" title={displayValue !== rawValue ? rawValue : undefined}>
        {displayValue}
      </span>
    </div>
  );
}

// Collapsible config section
function ConfigSection({
  title,
  description,
  icon: Icon,
  children,
  isOpen,
  onToggle,
}: {
  title: string;
  description?: string;
  icon: React.ElementType;
  children: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border border-border/50 rounded-lg">
      <button
        onClick={onToggle}
        className={`w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 transition-colors rounded-t-lg ${isOpen ? "" : "rounded-b-lg"}`}
      >
        <div className="flex items-center gap-3">
          <Icon className="w-4 h-4 text-accent" />
          <span className="font-medium text-foreground">{title}</span>
        </div>
        <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          // overflow is hidden during the height animation (so the collapse clips
          // cleanly) but set to visible once open, so hover tooltips on the bottom
          // rows can escape the card instead of being cut off at its border.
          <motion.div
            initial={{ height: 0, opacity: 0, overflow: "hidden" }}
            animate={{ height: "auto", opacity: 1, transitionEnd: { overflow: "visible" } }}
            exit={{ height: 0, opacity: 0, overflow: "hidden" }}
            transition={{ duration: 0.2 }}
          >
            <div className="px-4 py-3 bg-card rounded-b-lg">
              {description && (
                <p className="text-muted-foreground text-xs mb-3">{description}</p>
              )}
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// Config section IDs for expand/collapse tracking
type ConfigSectionId = "llm" | "documents" | "search" | "graph" | "features" | "privacy";

export default function AdminPage() {
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [showResetModal, setShowResetModal] = useState(false);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [configError, setConfigError] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [openSections, setOpenSections] = useState<Set<ConfigSectionId>>(new Set(["llm"]));
  const [graphStatsOpen, setGraphStatsOpen] = useState(false);

  const toggleSection = (id: ConfigSectionId) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const allSectionIds: ConfigSectionId[] = ["llm", "documents", "search", "graph", "features", "privacy"];
  const visibleSectionIds = allSectionIds;
  const allExpanded = visibleSectionIds.every(id => openSections.has(id));

  const toggleAllSections = () => {
    if (allExpanded) {
      setOpenSections(new Set());
    } else {
      setOpenSections(new Set(visibleSectionIds));
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const refreshStats = useCallback(async () => {
    try {
      const data = await api.getStats();
      setStats(data);
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  useEffect(() => {
    async function fetchConfig() {
      try {
        const data = await api.getSystemConfig();
        setConfig(data);
        setConfigError(null);
      } catch (err) {
        setConfigError(err instanceof Error ? err.message : "Failed to load configuration");
      } finally {
        setConfigLoading(false);
      }
    }
    fetchConfig();
    refreshStats();
  }, [refreshStats]);

  const handleLogout = async () => {
    setIsLoggingOut(true);
    clearAdminApiKey();
    await logout();
  };

  return (
    <PageTransition>
      <div className="space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-foreground mb-2">Settings</h1>
            <p className="text-muted-foreground">
              Manage your Cortex settings and API access
            </p>
          </div>
          <button
            onClick={handleLogout}
            disabled={isLoggingOut}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            {isLoggingOut ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <LogOut className="w-4 h-4" />
            )}
            <span>{isLoggingOut ? "Logging out..." : "Logout"}</span>
          </button>
        </div>

        {/* API Key Management */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <ApiKeyManager />
        </motion.div>

        {/* Agent Skills */}
        {config?.enable_skills && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.11 }}
          >
            <SkillsManager />
          </motion.div>
        )}

        {/* Git Integration */}
        {config?.enable_git_integration && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.115 }}
          >
            <GitIntegrations />
          </motion.div>
        )}

        {/* Statistics */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.12 }}
        >
          <div className="glass rounded-xl overflow-hidden">
            <div className="px-6 py-4 border-b border-border/50">
              <div className="flex items-center gap-3">
                <BarChart3 className="w-5 h-5 text-accent" />
                <h2 className="text-lg font-semibold text-foreground">Statistics</h2>
              </div>
              <p className="text-muted-foreground text-sm mt-1">
                Knowledge base overview
              </p>
            </div>

            <div className="p-6">
              {statsLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-accent" />
                </div>
              ) : stats ? (
                <div className="space-y-6">
                  {/* Primary KPIs */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                    <div className="p-3 bg-muted/30 rounded-lg text-center">
                      <FolderOpen className="w-4 h-4 mx-auto mb-1.5 text-muted-foreground" />
                      <p className="text-xl font-semibold">{stats.collection_count ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Collections</p>
                    </div>
                    <div className="p-3 bg-muted/30 rounded-lg text-center">
                      <FileText className="w-4 h-4 mx-auto mb-1.5 text-muted-foreground" />
                      <p className="text-xl font-semibold">{stats.document_count}</p>
                      <p className="text-xs text-muted-foreground">Documents</p>
                    </div>
                    <div className="p-3 bg-muted/30 rounded-lg text-center">
                      <BookOpen className="w-4 h-4 mx-auto mb-1.5 text-muted-foreground" />
                      <p className="text-xl font-semibold">{stats.chunk_count}</p>
                      <p className="text-xs text-muted-foreground">Chunks</p>
                    </div>
                    <div className="p-3 bg-muted/30 rounded-lg text-center">
                      <Layers className="w-4 h-4 mx-auto mb-1.5 text-muted-foreground" />
                      <p className="text-xl font-semibold">{stats.entity_count ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Entities</p>
                    </div>
                    <div className="p-3 bg-muted/30 rounded-lg text-center">
                      <Share2 className="w-4 h-4 mx-auto mb-1.5 text-muted-foreground" />
                      <p className="text-xl font-semibold">{stats.relationship_count ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Relationships</p>
                    </div>
                    <div className="p-3 bg-muted/30 rounded-lg text-center">
                      <Users className="w-4 h-4 mx-auto mb-1.5 text-muted-foreground" />
                      <p className="text-xl font-semibold">{stats.community_count ?? 0}</p>
                      <p className="text-xs text-muted-foreground">Communities</p>
                    </div>
                  </div>

                  {/* Document Processing */}
                  <div>
                    <h3 className="text-sm font-medium text-foreground mb-3">Document Processing</h3>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between py-2 border-b border-border/50">
                        <span className="flex items-center gap-2 text-sm text-muted-foreground">
                          <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                          Completed
                        </span>
                        <span className="text-sm font-mono text-foreground">{stats.completed_count ?? 0}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-border/50">
                        <span className="flex items-center gap-2 text-sm text-muted-foreground">
                          <Loader2 className="w-3.5 h-3.5 text-accent" />
                          Processing
                        </span>
                        <span className="text-sm font-mono text-foreground">{stats.processing_count ?? 0}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-border/50">
                        <span className="flex items-center gap-2 text-sm text-muted-foreground">
                          <Clock className="w-3.5 h-3.5" />
                          Pending
                        </span>
                        <span className="text-sm font-mono text-foreground">{stats.pending_count ?? 0}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-border/50">
                        <span className="flex items-center gap-2 text-sm text-muted-foreground">
                          <XCircle className="w-3.5 h-3.5 text-destructive" />
                          Failed
                        </span>
                        <span className="text-sm font-mono text-foreground">{stats.failed_count ?? 0}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-border/50">
                        <span className="text-sm text-muted-foreground">Total Storage</span>
                        <span className="text-sm font-mono text-foreground">{formatBytes(stats.total_size)}</span>
                      </div>
                      <div className="flex items-center justify-between py-2">
                        <span className="text-sm text-muted-foreground">Avg. Chunks per Document</span>
                        <span className="text-sm font-mono text-foreground">{stats.avg_chunks_per_doc ?? 0}</span>
                      </div>
                    </div>
                  </div>

                  {/* Knowledge Graph (collapsible) */}
                  <div className="border border-border/50 rounded-lg overflow-hidden">
                    <button
                      onClick={() => setGraphStatsOpen(!graphStatsOpen)}
                      className="w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <Network className="w-4 h-4 text-accent" />
                        <span className="text-sm font-medium text-foreground">Knowledge Graph</span>
                      </div>
                      <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${graphStatsOpen ? "rotate-180" : ""}`} />
                    </button>
                    <AnimatePresence initial={false}>
                      {graphStatsOpen && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                        >
                          <div className="px-4 py-3 space-y-1">
                            <div className="flex items-center justify-between py-2 border-b border-border/50">
                              <span className="text-sm text-muted-foreground">Avg. Mentions per Entity</span>
                              <span className="text-sm font-mono text-foreground">{stats.avg_entity_mentions ?? 0}</span>
                            </div>
                            {stats.entity_type_counts && Object.keys(stats.entity_type_counts).length > 0 && (
                              <>
                                <div className="pt-2 pb-1">
                                  <span className="text-xs text-muted-foreground uppercase tracking-wider">Entity Types</span>
                                </div>
                                {Object.entries(stats.entity_type_counts)
                                  .sort(([, a], [, b]) => b - a)
                                  .map(([type, count]) => (
                                    <div key={type} className="flex items-center justify-between py-1.5 border-b border-border/30 last:border-0">
                                      <span className="text-sm text-muted-foreground">{type}</span>
                                      <span className="text-sm font-mono text-foreground">{count}</span>
                                    </div>
                                  ))}
                              </>
                            )}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </motion.div>

        {/* System Configuration */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
        >
          <div className="glass rounded-xl overflow-hidden">
            {/* Header */}
            <div className="px-6 py-4 border-b border-border/50">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Settings2 className="w-5 h-5 text-accent" />
                  <h2 className="text-lg font-semibold text-foreground">System Configuration</h2>
                </div>
                {config && !configLoading && !configError && (
                  <button
                    onClick={toggleAllSections}
                    className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {allExpanded ? "Collapse All" : "Expand All"}
                  </button>
                )}
              </div>
              <p className="text-muted-foreground text-sm mt-1">
                Current system settings (read-only)
              </p>
            </div>

            {/* Content */}
            <div className="p-6">
              {configLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-accent" />
                </div>
              ) : configError ? (
                <div className="flex items-center gap-2 text-destructive py-4">
                  <AlertTriangle className="w-4 h-4" />
                  <span>{configError}</span>
                </div>
              ) : config ? (
                <DisplayFullConfigContext.Provider value={config.display_full_system_config ?? false}>
                <div className="space-y-3">
                  {/* LLM Configuration */}
                  <ConfigSection title="LLM Configuration" icon={Brain} isOpen={openSections.has("llm")} onToggle={() => toggleSection("llm")}>
                    {/* Primary Model */}
                    <div className="mb-4">
                      <h4 className="text-sm font-medium text-foreground mb-0.5">Primary Model</h4>
                      <p className="text-muted-foreground text-xs mb-2">Handles agentic inference, Q&A, deep research, and chat. Powerful reasoning models like Minimax M3, GLM5, or Kimi K2.5 recommended for maximum performance in deep research mode.</p>
                      <ConfigItem label="Model" value={config.openai_model} format="model" tooltip="The main LLM used for agentic inference, Q&A, research, and chat (OPENAI_MODEL)" />
                      <ConfigItem label="API Base" value={config.openai_api_base} format="apiBase" tooltip="OpenAI-compatible API endpoint for the primary model (OPENAI_API_BASE)" />
                      <ConfigItem label="Context Window" value={config.openai_max_context.toLocaleString()} tooltip="Input context budget for the primary model. Floor of the context-budget fallback chain — sub-tier *_MAX_CONTEXT knobs inherit from this when set to 0 (OPENAI_MAX_CONTEXT)" />
                      <ConfigItem advanced label="Output Tokens" value={config.openai_max_output_tokens.toLocaleString()} tooltip="Output-token budget for the primary model. Floor of the output-budget fallback chain — sub-tier *_MAX_OUTPUT_TOKENS knobs inherit from this when set to 0 (OPENAI_MAX_OUTPUT_TOKENS)" />
                    </div>

                    {/* Extraction Model */}
                    <div className="mb-4 pt-3">
                      <h4 className="text-sm font-medium text-foreground mb-0.5">Extraction Model</h4>
                      <p className="text-muted-foreground text-xs mb-2">Discovers entities and their types from document chunks during ingestion, and generates community summaries. Instruction-following models recommended (e.g. Mistral Small 24B, Ministral 14B). Defaults to the primary model if not set separately.</p>
                      <ConfigItem label="Model" value={config.extraction_model} format="model" tooltip="LLM used for entity extraction during document ingestion. Defaults to the primary model if not set (GRAPH_EXTRACTION_MODEL)" />
                      <ConfigItem label="API Base" value={config.extraction_api_base} format="apiBase" tooltip="API endpoint for the extraction model. Defaults to primary API base if not set (GRAPH_EXTRACTION_API_BASE)" />
                      <ConfigItem label="Context Window" value={config.extraction_max_context.toLocaleString()} tooltip="Max context window tokens for entity extraction. Inherits OPENAI_MAX_CONTEXT when GRAPH_EXTRACTION_MAX_CONTEXT is 0 (GRAPH_EXTRACTION_MAX_CONTEXT)" />
                      <ConfigItem advanced label="Output Tokens" value={config.extraction_max_output_tokens.toLocaleString()} tooltip="Output budget for entity-extraction LLM calls. Inherits OPENAI_MAX_OUTPUT_TOKENS when EXTRACTION_MAX_OUTPUT_TOKENS is 0 (EXTRACTION_MAX_OUTPUT_TOKENS)" />
                      <ConfigItem advanced label="Concurrent Extractions" value={config.concurrent_extractions} tooltip="How many chunks are extracted in parallel per document. Thread pool size for entity-extraction LLM calls (CONCURRENT_EXTRACTIONS)" />
                    </div>

                    {/* Relationship Model */}
                    <div className="mb-4 pt-3">
                      <h4 className="text-sm font-medium text-foreground mb-0.5">Relationship Model</h4>
                      <p className="text-muted-foreground text-xs mb-2">Used for all relationship discovery (Step 1 per-chunk and Step 2 batch analysis). Separate rate limit from entity extraction. Instruction-following models recommended (e.g. OpenAI GPT OSS 120B). Defaults to extraction model.</p>
                      <ConfigItem label="Model" value={config.relationship_model} format="model" tooltip="LLM used for per-chunk relationship extraction. Defaults to the extraction model if not set (RELATIONSHIP_EXTRACTION_MODEL)" />
                      <ConfigItem label="API Base" value={config.relationship_api_base} format="apiBase" tooltip="API endpoint for the relationship model. Defaults to extraction API base if not set (RELATIONSHIP_EXTRACTION_API_BASE)" />
                      <ConfigItem label="Context Window" value={config.relationship_max_context.toLocaleString()} tooltip="Max input context tokens for Phase 2 batch analysis. Inherits extraction → primary when RELATIONSHIP_MAX_CONTEXT is 0 (RELATIONSHIP_MAX_CONTEXT)" />
                      <ConfigItem advanced label="Output Tokens (per-chunk)" value={config.relationship_max_output_tokens.toLocaleString()} tooltip="Output budget for per-chunk + candidate-scan calls. Inherits extraction → primary when RELATIONSHIP_MAX_OUTPUT_TOKENS is 0 (RELATIONSHIP_MAX_OUTPUT_TOKENS)" />
                      <ConfigItem advanced label="Output Tokens (batch)" value={config.relationship_batch_max_output_tokens.toLocaleString()} tooltip="Output budget for Phase 2 batch analysis — standalone, NOT in the inheritance chain. Batches process hundreds of entity pairs per call (RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS)" />
                      <ConfigItem advanced label="Concurrent Relations" value={config.concurrent_relations} tooltip="Phase 1 — per-chunk relationship extraction during document ingestion. Within a single document, this many chunks have their relationships extracted in parallel (the rest queue behind a per-doc semaphore). High frequency, small payloads. Example: a 50-chunk doc at 4 means 4 chunks process at once, 46 wait. This is NOT the same as 'Parallel Batches' below — that one runs in a separate post-ingestion phase (CONCURRENT_RELATIONS)" />
                      <ConfigItem advanced label="Parallel Batches" value={config.parallel_relationship_batches} tooltip="Phase 2 — cross-document batch analysis that runs AFTER all documents finish ingesting. Cortex takes the full entity graph, groups candidate entity pairs into batches (hundreds of pairs per batch), and asks the LLM about each batch in a single call. This knob controls how many of those big batch calls run concurrently. Low frequency, large payloads. This is NOT the same as 'Concurrent Relations' above — different phase, different work unit (PARALLEL_RELATIONSHIP_BATCHES)" />
                    </div>

                    {/* Vision Model */}
                    {config.vision_model_available && (
                      <div className="mb-4 pt-3">
                        <h4 className="text-sm font-medium text-foreground mb-0.5">Vision Model</h4>
                        <p className="text-muted-foreground text-xs mb-2">Analyzes images extracted from documents during ingestion, generating descriptions and running OCR in the background.</p>
                        <ConfigItem label="Model" value={config.vision_model} format="model" tooltip="Vision-capable model used for image analysis during document ingestion (VISION_MODEL)" />
                        <ConfigItem label="API Base" value={config.vision_api_base} format="apiBase" tooltip="API endpoint for the vision model. Defaults to primary API base if not set (VISION_MODEL_API_BASE)" />
                        <ConfigItem advanced label="Output Tokens" value={config.vision_max_output_tokens.toLocaleString()} tooltip="Output budget for vision-model image descriptions. Inherits relationship → extraction → primary when VISION_MAX_OUTPUT_TOKENS is 0 (VISION_MAX_OUTPUT_TOKENS)" />
                        <ConfigItem advanced label="Max Concurrent" value={config.vision_max_concurrent} tooltip="System-wide cap on concurrent vision API calls. Controls how many images are analyzed in parallel across all documents (VISION_MAX_CONCURRENT)" />
                        <ConfigItem advanced label="Min Image Side" value={`${config.vision_min_image_side} px`} tooltip="Skip the vision API call for images smaller than this on either side. PDFs expose bullets, icons, and separators as Docling PictureItems; hosted vision APIs reject sub-64px images with HTTP 400 'did not pass validation checks'. Below the threshold Cortex falls back to Docling's built-in description. Set 0 to disable (VISION_MIN_IMAGE_SIDE)" />
                        <ConfigItem advanced label="Max Image Side" value={`${config.vision_max_image_side} px`} tooltip="Downscale images so the longer side fits this many pixels before the base64 encode. Cortex renders PDF pages at 2× DPI (~2400×1700) — without this cap the base64 payload bloats and some providers tokenize it as text, blowing past context windows. 1568 matches Claude's recommended max side. Set 0 to disable downscaling (VISION_MAX_IMAGE_SIDE)" />
                        <ConfigItem advanced label="JPEG Quality" value={config.vision_jpeg_quality} tooltip="JPEG quality (1–95) when encoding opaque images for the vision API. 85 is visually near-lossless at ~5–10× smaller than PNG. RGBA images still use PNG to preserve alpha (VISION_JPEG_QUALITY)" />
                      </div>
                    )}

                    {/* Embeddings */}
                    <div className="pt-3">
                      <h4 className="text-sm font-medium text-foreground mb-0.5">Embeddings</h4>
                      <p className="text-muted-foreground text-xs mb-2">Converts text into vector representations for semantic search, powering hybrid retrieval across chunks and entities.</p>
                      <ConfigItem label="Model" value={config.embedding_model} format="model" tooltip="Model used to generate vector embeddings for chunks and entities (EMBEDDING_MODEL)" />
                      <ConfigItem label="Dimension" value={config.embedding_dimension} tooltip="Output dimension of the embedding vectors. Must match the model's supported dimensions (EMBEDDING_DIMENSION)" />
                      <ConfigItem label="API Base" value={config.embedding_api_base} format="apiBase" tooltip="API endpoint for the embedding model. Defaults to primary API base if not set (EMBEDDING_API_BASE)" />
                      <ConfigItem advanced label="Max Input Tokens" value={config.embedding_max_input_tokens.toLocaleString()} tooltip="Per-input token cap before sending to the embeddings endpoint. Inputs longer than this are char-truncated client-side (~2.8 chars/token) to avoid HTTP 400 'Input text exceeds the maximum token limit' rejections. Raise for longer-context models e.g. text-embedding-qwen3-8b → 32768 (EMBEDDING_MAX_INPUT_TOKENS)" />
                    </div>
                  </ConfigSection>

                  {/* Document Processing */}
                  <ConfigSection title="Document Processing" icon={FileText} isOpen={openSections.has("documents")} onToggle={() => toggleSection("documents")}>
                    <ConfigItem label="Max File Size" value={`${config.max_file_size_mb} MB`} />
                    <ConfigItem label="Allowed Extensions" value={config.allowed_extensions.join(", ")} />
                    <ConfigItem advanced label="Chunk Size" value={config.chunk_size} />
                    <ConfigItem advanced label="Chunk Overlap" value={config.chunk_overlap} />
                    <ConfigItem advanced label="Chunk Method" value={config.chunk_by} />
                    <ConfigItem advanced label="Sentences per Chunk" value={config.sentences_per_chunk} />
                    <ConfigItem advanced label="Batch Concurrency" value={config.batch_processing_concurrency} tooltip="How many documents are processed through the pipeline simultaneously (BATCH_PROCESSING_CONCURRENCY)" />
                    <ConfigItem advanced label="Thread Workers" value={config.processing_thread_workers} tooltip="Size of the thread pool for CPU-intensive processing operations (PROCESSING_THREAD_WORKERS)" />
                  </ConfigSection>

                  {/* Search Configuration */}
                  <ConfigSection title="Search & RAG" icon={Search} isOpen={openSections.has("search")} onToggle={() => toggleSection("search")}>
                    <ConfigItem label="Hybrid Search" value={config.enable_hybrid_search} type="boolean" />
                    <ConfigItem advanced label="Vector Weight" value={config.vector_weight} />
                    <ConfigItem advanced label="Keyword Weight" value={config.keyword_weight} />
                    <ConfigItem advanced label="Graph Weight" value={config.graph_weight} />
                    <ConfigItem label="Reranking" value={config.enable_reranking} type="boolean" />
                    <ConfigItem label="Reranking Model" value={config.reranking_model} />
                    <ConfigItem label="Agentic RAG" value={config.enable_agentic_rag} type="boolean" />
                    <ConfigItem advanced label="Max Agentic Steps" value={config.max_agentic_steps} />
                    <ConfigItem advanced label="Max Conversation History" value={config.max_conversation_history} />
                  </ConfigSection>

                  {/* Knowledge Graph */}
                  <ConfigSection title="Knowledge Graph" icon={Network} isOpen={openSections.has("graph")} onToggle={() => toggleSection("graph")}>
                    <ConfigItem label="Graph Extraction" value={config.enable_graph_extraction} type="boolean" />
                    <ConfigItem advanced label="Max Graph Hops" value={config.max_graph_hops} />
                    <ConfigItem label="Community Detection" value={config.enable_community_detection} type="boolean" />
                    <ConfigItem advanced label="Min Community Size" value={config.min_community_size} />
                    <ConfigItem advanced label="Max Communities" value={config.max_communities} />
                    <ConfigItem label="Graph Summarization" value={config.enable_graph_summarization} type="boolean" />
                    <ConfigItem label="Entity Resolution" value={config.enable_semantic_entity_resolution} type="boolean" />
                    <ConfigItem advanced label="Similarity Threshold" value={config.entity_similarity_threshold} />
                  </ConfigSection>

                  {/* Collections & Features */}
                  <ConfigSection title="Features & Security" icon={Shield} isOpen={openSections.has("features")} onToggle={() => toggleSection("features")}>
                    <ConfigItem label="Collections" value={config.enable_collections} type="boolean" />
                    <ConfigItem label="Default Collection" value={config.default_collection} />
                    <ConfigItem label="Stream Reasoning Steps" value={config.stream_reasoning_steps} type="boolean" />
                    <ConfigItem label="Show Retrieval Stats" value={config.show_retrieval_stats} type="boolean" />
                    <ConfigItem label="Prompt Security" value={config.prompt_security} type="boolean" />
                  </ConfigSection>

                  {/* Privacy — proves whether prompt/completion content ever leaves this instance */}
                  <ConfigSection title="Privacy" icon={Lock} isOpen={openSections.has("privacy")} onToggle={() => toggleSection("privacy")}>
                    <ConfigItem
                      label="LLM Tracing (Langfuse)"
                      value={config.langfuse_tracing_active}
                      type="boolean"
                      tooltip="Whether LLM/embedding/vision calls are traced to a Langfuse instance at all. When Disabled, nothing is exported anywhere. When Enabled, only structural metadata is sent unless 'Prompt & Content Redaction' below is Disabled (LANGFUSE_BASE_URL + key pair + LANGFUSE_TRACING_ENABLED)."
                    />
                    <ConfigItem
                      label="Prompt & Content Redaction"
                      value={!config.langfuse_log_extended}
                      type="boolean"
                      tooltip="When Enabled, every prompt, completion, tool call, embedding, and image-analysis text is stripped to [REDACTED] before any trace leaves this instance — only structure (model, roles, token counts, cost, latency) is ever exported. This is how you prove the host does not log what users ask or what the models reply. It is on by default; it only turns off when the operator explicitly sets LANGFUSE_LOG_EXTENDED=true for local debugging."
                    />
                  </ConfigSection>

                </div>
                </DisplayFullConfigContext.Provider>
              ) : null}
            </div>
          </div>
        </motion.div>

        {/* Data Management (Import/Export) */}
        <LibraryTransferSection stats={stats} onImportComplete={refreshStats} />

        {/* Danger Zone */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <div className="border border-destructive/30 rounded-xl overflow-hidden">
            {/* Header */}
            <div className="bg-destructive/10 px-6 py-4 border-b border-destructive/30">
              <div className="flex items-center gap-3">
                <AlertTriangle className="w-5 h-5 text-destructive" />
                <h2 className="text-lg font-semibold text-destructive">Danger Zone</h2>
              </div>
            </div>

            {/* Content */}
            <div className="p-6 bg-card">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-foreground font-medium mb-1">System Reset</h3>
                  <p className="text-muted-foreground text-sm">
                    Clear all data from the knowledge base including documents, entities, and files.
                  </p>
                </div>
                <button
                  onClick={() => setShowResetModal(true)}
                  className="flex items-center gap-2 px-4 py-2 bg-destructive/10 hover:bg-destructive/20 text-destructive border border-destructive/30 rounded-lg transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                  <span>Reset System</span>
                </button>
              </div>
            </div>
          </div>
        </motion.div>
      </div>

      {/* Reset Modal */}
      <AnimatePresence>
        {showResetModal && (
          <SystemResetModal
            onClose={() => setShowResetModal(false)}
            onReset={() => {
              // Could refresh stats or redirect here
            }}
          />
        )}
      </AnimatePresence>
    </PageTransition>
  );
}
