"use client";

import { useState, useEffect, useCallback } from "react";
import {
  FileText,
  BookOpen,
  Network,
  Link2,
  Users,
  FolderOpen,
  Trash2,
  Loader2,
} from "lucide-react";
import StatsCard from "@/components/StatsCard";
import { formatBytes } from "@/lib/utils";
import { api } from "@/lib/api";
import { useAuth } from "./AuthProvider";

interface Stats {
  document_count: number;
  chunk_count: number;
  total_size: number;
  entity_count?: number;
  relationship_count?: number;
  community_count?: number;
  collection_count?: number;
  pending_count?: number;
}

export default function StatsBar() {
  const { isAuthReady } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [isCleaning, setIsCleaning] = useState(false);

  const fetchStats = useCallback(async () => {
    const startTime = Date.now();
    const minAnimationDuration = 2000;
    setStatsLoading(true);
    try {
      const data = await api.getStats();
      setStats(data);
    } catch (error) {
      console.error("Failed to fetch stats:", error);
    } finally {
      const elapsed = Date.now() - startTime;
      const remaining = minAnimationDuration - elapsed;
      if (remaining > 0) {
        setTimeout(() => setStatsLoading(false), remaining);
      } else {
        setStatsLoading(false);
      }
    }
  }, []);

  // Only fetch stats when auth is ready
  useEffect(() => {
    if (!isAuthReady) return;

    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => clearInterval(interval);
  }, [isAuthReady, fetchStats]);

  // Show cleanup button when there are orphaned entities (entities exist but no documents/chunks)
  const hasOrphans = stats && stats.document_count === 0 && ((stats.entity_count ?? 0) > 0 || (stats.relationship_count ?? 0) > 0 || (stats.community_count ?? 0) > 0);

  const handleCleanup = async () => {
    if (!confirm("Clean up all orphaned entities, relationships, and communities? This removes graph data not linked to any document.")) return;
    setIsCleaning(true);
    try {
      await api.cleanupOrphanedEntities();
      await fetchStats();
    } catch (error) {
      console.error("Failed to cleanup:", error);
    } finally {
      setIsCleaning(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-6 w-full">
      {hasOrphans && (
        <div className="mb-4 flex items-center justify-between px-4 py-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
          <p className="text-sm text-yellow-200">
            Orphaned graph data detected — entities and relationships exist without any linked documents.
          </p>
          <button
            onClick={handleCleanup}
            disabled={isCleaning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-yellow-500/20 text-yellow-200 hover:bg-yellow-500/30 transition-colors disabled:opacity-50 shrink-0 ml-4"
          >
            {isCleaning ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
            Clean Up
          </button>
        </div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatsCard
          label="Documents"
          value={stats?.document_count ?? 0}
          icon={FileText}
          loading={statsLoading}
        />
        <StatsCard
          label="Entities"
          value={stats?.entity_count ?? 0}
          icon={Network}
          loading={statsLoading}
        />
        <StatsCard
          label="Relations"
          value={stats?.relationship_count ?? 0}
          icon={Link2}
          loading={statsLoading}
        />
        <StatsCard
          label="Communities"
          value={stats?.community_count ?? 0}
          icon={Users}
          loading={statsLoading}
        />
      </div>
    </div>
  );
}
