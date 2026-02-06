"use client";

import { useState, useEffect } from "react";
import { PageTransition } from "@/components/layout";
import { SystemResetModal, ApiKeyManager } from "@/components/admin";
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
  Zap,
  Check,
  X,
} from "lucide-react";
import { logout } from "@/lib/auth";
import { api, clearAdminApiKey } from "@/lib/api";
import type { SystemConfig } from "@/types";

// Helper component for displaying config items
function ConfigItem({ label, value, type = "text" }: { label: string; value: string | number | boolean; type?: "text" | "boolean" | "list" }) {
  if (type === "boolean") {
    return (
      <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
        <span className="text-muted-foreground text-sm">{label}</span>
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
  
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span className="text-foreground text-sm font-mono">{String(value)}</span>
    </div>
  );
}

// Collapsible config section
function ConfigSection({ 
  title, 
  icon: Icon, 
  children,
  isOpen,
  onToggle,
}: { 
  title: string; 
  icon: React.ElementType; 
  children: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border border-border/50 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <Icon className="w-4 h-4 text-accent" />
          <span className="font-medium text-foreground">{title}</span>
        </div>
        <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div className="px-4 py-3 bg-card">
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// Config section IDs for expand/collapse tracking
type ConfigSectionId = "llm" | "embeddings" | "documents" | "search" | "graph" | "features" | "turbo";

export default function AdminPage() {
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [showResetModal, setShowResetModal] = useState(false);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [configError, setConfigError] = useState<string | null>(null);
  const [openSections, setOpenSections] = useState<Set<ConfigSectionId>>(new Set(["llm"]));

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

  const allSectionIds: ConfigSectionId[] = ["llm", "embeddings", "documents", "search", "graph", "features", "turbo"];
  const visibleSectionIds = config?.turbo_mode_available ? allSectionIds : allSectionIds.filter(id => id !== "turbo");
  const allExpanded = visibleSectionIds.every(id => openSections.has(id));

  const toggleAllSections = () => {
    if (allExpanded) {
      setOpenSections(new Set());
    } else {
      setOpenSections(new Set(visibleSectionIds));
    }
  };

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
  }, []);

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
              Manage your MOCA Knowledge Base settings and API access
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
                <div className="space-y-3">
                  {/* LLM Configuration */}
                  <ConfigSection title="LLM Configuration" icon={Brain} isOpen={openSections.has("llm")} onToggle={() => toggleSection("llm")}>
                    <ConfigItem label="Primary Model" value={config.openai_model} />
                    <ConfigItem label="Fast Mode Model" value={config.fast_mode_model} />
                  </ConfigSection>

                  {/* Embedding Configuration */}
                  <ConfigSection title="Embeddings" icon={Database} isOpen={openSections.has("embeddings")} onToggle={() => toggleSection("embeddings")}>
                    <ConfigItem label="Model" value={config.embedding_model} />
                    <ConfigItem label="Dimension" value={config.embedding_dimension} />
                    <ConfigItem label="OpenAI Embeddings" value={config.use_openai_embeddings} type="boolean" />
                  </ConfigSection>

                  {/* Document Processing */}
                  <ConfigSection title="Document Processing" icon={FileText} isOpen={openSections.has("documents")} onToggle={() => toggleSection("documents")}>
                    <ConfigItem label="Max File Size" value={`${config.max_file_size_mb} MB`} />
                    <ConfigItem label="Allowed Extensions" value={config.allowed_extensions.join(", ")} />
                    <ConfigItem label="Chunk Size" value={config.chunk_size} />
                    <ConfigItem label="Chunk Overlap" value={config.chunk_overlap} />
                    <ConfigItem label="Chunk Method" value={config.chunk_by} />
                    <ConfigItem label="Sentences per Chunk" value={config.sentences_per_chunk} />
                    <ConfigItem label="Batch Concurrency" value={config.batch_processing_concurrency} />
                    <ConfigItem label="Thread Workers" value={config.processing_thread_workers} />
                  </ConfigSection>

                  {/* Search Configuration */}
                  <ConfigSection title="Search & RAG" icon={Search} isOpen={openSections.has("search")} onToggle={() => toggleSection("search")}>
                    <ConfigItem label="Hybrid Search" value={config.enable_hybrid_search} type="boolean" />
                    <ConfigItem label="Vector Weight" value={config.vector_weight} />
                    <ConfigItem label="Keyword Weight" value={config.keyword_weight} />
                    <ConfigItem label="Graph Weight" value={config.graph_weight} />
                    <ConfigItem label="Reranking" value={config.enable_reranking} type="boolean" />
                    <ConfigItem label="Reranking Model" value={config.reranking_model} />
                    <ConfigItem label="Agentic RAG" value={config.enable_agentic_rag} type="boolean" />
                    <ConfigItem label="Max Agentic Steps" value={config.max_agentic_steps} />
                    <ConfigItem label="Max Conversation History" value={config.max_conversation_history} />
                  </ConfigSection>

                  {/* Knowledge Graph */}
                  {/* Knowledge Graph */}
                  <ConfigSection title="Knowledge Graph" icon={Network} isOpen={openSections.has("graph")} onToggle={() => toggleSection("graph")}>
                    <ConfigItem label="Graph Extraction" value={config.enable_graph_extraction} type="boolean" />
                    <ConfigItem label="Max Graph Hops" value={config.max_graph_hops} />
                    <ConfigItem label="Concurrent Extractions" value={config.concurrent_extractions} />
                    <ConfigItem label="Community Detection" value={config.enable_community_detection} type="boolean" />
                    <ConfigItem label="Min Community Size" value={config.min_community_size} />
                    <ConfigItem label="Max Communities" value={config.max_communities} />
                    <ConfigItem label="Graph Summarization" value={config.enable_graph_summarization} type="boolean" />
                    <ConfigItem label="Entity Resolution" value={config.enable_semantic_entity_resolution} type="boolean" />
                    <ConfigItem label="Similarity Threshold" value={config.entity_similarity_threshold} />
                  </ConfigSection>

                  {/* Collections & Features */}
                  <ConfigSection title="Features & Security" icon={Shield} isOpen={openSections.has("features")} onToggle={() => toggleSection("features")}>
                    <ConfigItem label="Collections" value={config.enable_collections} type="boolean" />
                    <ConfigItem label="Default Collection" value={config.default_collection} />
                    <ConfigItem label="Stream Reasoning Steps" value={config.stream_reasoning_steps} type="boolean" />
                    <ConfigItem label="Show Retrieval Stats" value={config.show_retrieval_stats} type="boolean" />
                    <ConfigItem label="Prompt Security" value={config.prompt_security} type="boolean" />
                  </ConfigSection>

                  {/* Turbo Mode - only show if available */}
                  {config.turbo_mode_available && (
                    <ConfigSection title="Turbo Mode (GPU)" icon={Zap} isOpen={openSections.has("turbo")} onToggle={() => toggleSection("turbo")}>
                      <ConfigItem label="GPU Type" value={config.compute3_gpu_type} />
                      <ConfigItem label="GPU Count" value={config.compute3_gpu_count} />
                      <ConfigItem label="Model" value={config.compute3_model} />
                      <ConfigItem label="Default Runtime" value={`${Math.round(config.compute3_default_runtime / 60)} min`} />
                    </ConfigSection>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </motion.div>

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
