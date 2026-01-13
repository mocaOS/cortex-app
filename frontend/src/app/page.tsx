"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Upload,
  Search,
  MessageSquare,
  FileText,
  Database,
  Trash2,
  Loader2,
  CheckCircle,
  AlertCircle,
  Sparkles,
  BookOpen,
  Zap,
  Network,
  Link2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import FileUpload from "@/components/FileUpload";
import SearchPanel from "@/components/SearchPanel";
import DocumentList from "@/components/DocumentList";
import AskPanel from "@/components/AskPanel";
import StatsCard from "@/components/StatsCard";

type Tab = "upload" | "search" | "ask" | "documents";

interface Stats {
  document_count: number;
  chunk_count: number;
  total_size: number;
  entity_count?: number;
  relationship_count?: number;
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("upload");
  const [stats, setStats] = useState<Stats | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const fetchStats = async () => {
    try {
      const res = await fetch("/api/stats");
      if (res.ok) {
        const data = await res.json();
        setStats(data);
      }
    } catch (error) {
      console.error("Failed to fetch stats:", error);
    }
  };

  useEffect(() => {
    fetchStats();
    const interval = setInterval(fetchStats, 15000);
    return () => clearInterval(interval);
  }, [refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);

  const tabs = [
    { id: "upload" as Tab, label: "Upload", icon: Upload },
    { id: "search" as Tab, label: "Search", icon: Search },
    { id: "ask" as Tab, label: "Ask AI", icon: MessageSquare },
    { id: "documents" as Tab, label: "Documents", icon: FileText },
  ];

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-white/5 backdrop-blur-xl bg-black/20 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="relative">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-ocean-500 to-cyan-400 flex items-center justify-center glow">
                  <Database className="w-5 h-5 text-white" />
                </div>
                <div className="absolute -bottom-1 -right-1 w-4 h-4 rounded-full bg-mint-500 border-2 border-[hsl(var(--background))] flex items-center justify-center">
                  <Zap className="w-2 h-2 text-white" />
                </div>
              </div>
              <div>
                <h1 className="text-xl font-bold text-gradient">MOCA</h1>
                <p className="text-xs text-white/40">Knowledge Base</p>
              </div>
            </div>

            <nav className="flex items-center gap-1 glass rounded-full p-1">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    "flex items-center gap-2 px-4 py-2 rounded-full transition-all duration-300",
                    activeTab === tab.id
                      ? "bg-ocean-500/20 text-ocean-400"
                      : "text-white/60 hover:text-white/80 hover:bg-white/5"
                  )}
                >
                  <tab.icon className="w-4 h-4" />
                  <span className="text-sm font-medium hidden sm:inline">
                    {tab.label}
                  </span>
                </button>
              ))}
            </nav>
          </div>
        </div>
      </header>

      {/* Stats Bar */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-5 gap-4">
          <StatsCard
            label="Documents"
            value={stats?.document_count ?? 0}
            icon={FileText}
            color="ocean"
          />
          <StatsCard
            label="Chunks"
            value={stats?.chunk_count ?? 0}
            icon={BookOpen}
            color="cyan"
          />
          <StatsCard
            label="Entities"
            value={stats?.entity_count ?? 0}
            icon={Network}
            color="purple"
          />
          <StatsCard
            label="Relations"
            value={stats?.relationship_count ?? 0}
            icon={Link2}
            color="pink"
          />
          <StatsCard
            label="Storage"
            value={formatBytes(stats?.total_size ?? 0)}
            icon={Database}
            color="teal"
            isText
          />
        </div>
      </div>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 pb-12">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3 }}
          >
            {activeTab === "upload" && <FileUpload onUpload={refresh} />}
            {activeTab === "search" && <SearchPanel />}
            {activeTab === "ask" && <AskPanel />}
            {activeTab === "documents" && (
              <DocumentList key={refreshKey} onDelete={refresh} />
            )}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* Footer */}
      <footer className="border-t border-white/5 py-6 text-center">
        <p className="text-white/30 text-sm">
          Powered by{" "}
          <span className="text-ocean-400">Neo4j</span> +{" "}
          <span className="text-coral-400">Haystack</span>
        </p>
      </footer>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}
