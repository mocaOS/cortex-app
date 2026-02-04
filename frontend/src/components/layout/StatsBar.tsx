"use client";

import { useState, useEffect, useCallback } from "react";
import {
  FileText,
  BookOpen,
  Network,
  Link2,
  Users,
  FolderOpen,
  Database,
  Clock,
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
    const interval = setInterval(fetchStats, 15000);
    return () => clearInterval(interval);
  }, [isAuthReady, fetchStats]);

  return (
    <div className="max-w-7xl mx-auto px-6 py-6 w-full">
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
        <StatsCard
          label="Documents"
          value={stats?.document_count ?? 0}
          icon={FileText}
          loading={statsLoading}
        />
        <StatsCard
          label="Chunks"
          value={stats?.chunk_count ?? 0}
          icon={BookOpen}
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
        <StatsCard
          label="Collections"
          value={stats?.collection_count ?? 0}
          icon={FolderOpen}
          loading={statsLoading}
        />
        <StatsCard
          label="Storage"
          value={formatBytes(stats?.total_size ?? 0)}
          icon={Database}
          isText
          loading={statsLoading}
        />
        <StatsCard
          label="Pending"
          value={stats?.pending_count ?? 0}
          icon={Clock}
          loading={statsLoading}
        />
      </div>
    </div>
  );
}
