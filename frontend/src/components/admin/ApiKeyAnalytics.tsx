"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Loader2,
  TrendingUp,
  AlertCircle,
  Activity,
  BarChart3,
  Calendar,
  CalendarDays,
  CalendarRange,
  Zap,
  Key,
  Clock
} from "lucide-react";
import { api } from "@/lib/api";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useIsMounted, useModalDismiss } from "@/lib/hooks";
import type { APIKeyWithStats, APIKeyUsageHistoryResponse, APIKeyStats } from "@/types";
import { UsageLineChart, EndpointBarChart } from "./UsageChart";

interface ApiKeyAnalyticsProps {
  apiKey: APIKeyWithStats;
  onClose: () => void;
}

// Color palette for endpoint breakdown
const ENDPOINT_COLORS = [
  "#f59e0b", // amber
  "#10b981", // emerald
  "#3b82f6", // blue
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#f97316", // orange
  "#84cc16", // lime
];

export function ApiKeyAnalytics({ apiKey, onClose }: ApiKeyAnalyticsProps) {
  useBodyScrollLock(true);
  const mounted = useIsMounted();
  const dialogRef = useModalDismiss(onClose);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<APIKeyStats | null>(apiKey.stats || null);
  const [history, setHistory] = useState<APIKeyUsageHistoryResponse | null>(null);
  const [historyDays, setHistoryDays] = useState(30);

  // Monotonic request id: a slower earlier range response must not overwrite a
  // newer one when the user switches ranges (7D/14D/30D/90D) quickly.
  const requestSeq = useRef(0);

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey.id, historyDays]);

  const fetchData = async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);

    try {
      const [statsRes, historyRes] = await Promise.all([
        api.getApiKeyStats(apiKey.id),
        api.getApiKeyUsageHistory(apiKey.id, historyDays),
      ]);

      // Discard if a newer request started or we unmounted.
      if (!mounted.current || seq !== requestSeq.current) return;
      setStats(statsRes);
      setHistory(historyRes);
    } catch (err) {
      if (!mounted.current || seq !== requestSeq.current) return;
      setError(err instanceof Error ? err.message : "Failed to load analytics");
    } finally {
      if (mounted.current && seq === requestSeq.current) setLoading(false);
    }
  };

  // Format large numbers
  const formatNumber = (num: number) => {
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
    return num.toString();
  };

  // Calculate error rate
  const errorRate = stats && stats.total_requests > 0
    ? ((stats.error_count / stats.total_requests) * 100).toFixed(2)
    : "0";

  // Get sorted endpoint data
  const sortedEndpoints = stats?.endpoint_breakdown 
    ? Object.entries(stats.endpoint_breakdown).sort(([, a], [, b]) => b - a)
    : [];

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <motion.div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        initial={{ scale: 0.95, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.95, opacity: 0, y: 20 }}
        transition={{ type: "spring", damping: 25, stiffness: 300 }}
        className="bg-gradient-to-b from-card to-card/95 rounded-2xl border border-border/50 shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-hidden focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header with gradient background */}
        <div className="relative px-6 py-5 border-b border-border/50 bg-gradient-to-r from-accent/10 via-accent/5 to-transparent">
          <div className="absolute inset-0 bg-grid-white/[0.02] pointer-events-none" />
          <div className="relative flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-xl bg-accent/20 border border-accent/30">
                <Key className="w-5 h-5 text-accent" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-foreground tracking-tight">{apiKey.name}</h2>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-muted/80 transition-colors text-muted-foreground hover:text-foreground"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="overflow-y-auto max-h-[calc(90vh-88px)] p-6">
          <AnimatePresence mode="wait">
            {loading ? (
              <motion.div 
                key="loading"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-col items-center justify-center py-16 gap-3"
              >
                <div className="relative">
                  <div className="absolute inset-0 bg-accent/20 blur-xl rounded-full" />
                  <Loader2 className="w-10 h-10 animate-spin text-accent relative" />
                </div>
                <span className="text-sm text-muted-foreground">Loading analytics...</span>
              </motion.div>
            ) : error ? (
              <motion.div 
                key="error"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-col items-center gap-3 text-destructive py-12"
              >
                <div className="p-3 rounded-full bg-destructive/10">
                  <AlertCircle className="w-6 h-6" />
                </div>
                <span className="text-sm">{error}</span>
              </motion.div>
            ) : (
              <motion.div
                key="content"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="space-y-6"
              >
                {/* Stats Grid */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {[
                    { 
                      label: "Total Requests", 
                      value: stats?.total_requests || 0, 
                      icon: Zap,
                      iconColor: "text-amber-500"
                    },
                    { 
                      label: "Today", 
                      value: stats?.requests_today || 0, 
                      icon: Calendar,
                      iconColor: "text-emerald-500"
                    },
                    { 
                      label: "This Week", 
                      value: stats?.requests_this_week || 0, 
                      icon: CalendarDays,
                      iconColor: "text-blue-500"
                    },
                    { 
                      label: "This Month", 
                      value: stats?.requests_this_month || 0, 
                      icon: CalendarRange,
                      iconColor: "text-violet-500"
                    },
                  ].map((stat, index) => (
                    <motion.div
                      key={stat.label}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.05 }}
                      className="relative overflow-hidden rounded-xl border border-border/50 bg-card/50 p-4"
                    >
                      <div className="flex items-center gap-2 mb-3">
                        <stat.icon className={`w-4 h-4 ${stat.iconColor}`} />
                        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                          {stat.label}
                        </span>
                      </div>
                      <div className="text-3xl font-bold text-foreground tabular-nums">
                        {formatNumber(stat.value)}
                      </div>
                    </motion.div>
                  ))}
                </div>

                {/* Error Stats */}
                {stats && stats.error_count > 0 && (
                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                    className="rounded-xl border border-destructive/30 bg-gradient-to-r from-destructive/10 to-transparent p-4"
                  >
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-destructive/20">
                        <AlertCircle className="w-5 h-5 text-destructive" />
                      </div>
                      <div className="flex-1">
                        <div className="text-foreground font-medium">
                          {stats.error_count.toLocaleString()} errors
                          <span className="text-destructive/80 ml-2">({errorRate}% error rate)</span>
                        </div>
                        {stats.last_error_message && (
                          <div className="text-sm text-muted-foreground mt-0.5 truncate">
                            Last: {stats.last_error_message}
                          </div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}

                {/* Usage Chart */}
                <motion.div 
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.25 }}
                  className="rounded-xl border border-border/50 bg-card/50 overflow-hidden"
                >
                  <div className="flex items-center justify-between px-5 py-4 border-b border-border/50">
                    <div className="flex items-center gap-2">
                      <Activity className="w-4 h-4 text-accent" />
                      <h3 className="font-medium text-foreground">Usage Trend</h3>
                    </div>
                    <div className="flex items-center gap-1 p-1 bg-muted/50 rounded-lg">
                      {[
                        { days: 7, label: "7D" },
                        { days: 14, label: "14D" },
                        { days: 30, label: "30D" },
                        { days: 90, label: "90D" },
                      ].map((option) => (
                        <button
                          key={option.days}
                          onClick={() => setHistoryDays(option.days)}
                          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                            historyDays === option.days
                              ? "bg-accent text-accent-foreground shadow-sm"
                              : "text-muted-foreground hover:text-foreground hover:bg-muted"
                          }`}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="p-5">
                    <UsageLineChart data={history?.history || []} height={220} />
                  </div>
                </motion.div>

                {/* Endpoint Breakdown */}
                <div className="grid md:grid-cols-5 gap-4">
                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.3 }}
                    className="md:col-span-3 rounded-xl border border-border/50 bg-card/50 overflow-hidden"
                  >
                    <div className="flex items-center gap-2 px-5 py-4 border-b border-border/50">
                      <BarChart3 className="w-4 h-4 text-accent" />
                      <h3 className="font-medium text-foreground">Requests by Endpoint</h3>
                    </div>
                    <div className="p-5">
                      <EndpointBarChart data={stats?.endpoint_breakdown || {}} height={180} />
                    </div>
                  </motion.div>

                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.35 }}
                    className="md:col-span-2 rounded-xl border border-border/50 bg-card/50 overflow-hidden"
                  >
                    <div className="flex items-center gap-2 px-5 py-4 border-b border-border/50">
                      <Clock className="w-4 h-4 text-accent" />
                      <h3 className="font-medium text-foreground">Breakdown</h3>
                    </div>
                    <div className="p-4">
                      {sortedEndpoints.length > 0 ? (
                        <div className="space-y-3">
                          {sortedEndpoints.map(([endpoint, count], index) => {
                            const total = stats?.total_requests || 1;
                            const percentage = (count / total) * 100;
                            const color = ENDPOINT_COLORS[index % ENDPOINT_COLORS.length];
                            return (
                              <div key={endpoint} className="space-y-1.5">
                                <div className="flex items-center justify-between text-sm">
                                  <div className="flex items-center gap-2">
                                    <div 
                                      className="w-2.5 h-2.5 rounded-full" 
                                      style={{ backgroundColor: color }}
                                    />
                                    <span className="text-foreground font-medium capitalize">
                                      {endpoint}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <span className="text-foreground font-semibold tabular-nums">
                                      {formatNumber(count)}
                                    </span>
                                    <span className="text-muted-foreground text-xs tabular-nums w-12 text-right">
                                      {percentage.toFixed(1)}%
                                    </span>
                                  </div>
                                </div>
                                <div className="h-1.5 bg-muted/50 rounded-full overflow-hidden">
                                  <motion.div
                                    initial={{ width: 0 }}
                                    animate={{ width: `${percentage}%` }}
                                    transition={{ duration: 0.5, delay: index * 0.1 }}
                                    className="h-full rounded-full"
                                    style={{ backgroundColor: color }}
                                  />
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                          <BarChart3 className="w-8 h-8 mb-2 opacity-50" />
                          <span className="text-sm">No endpoint data</span>
                        </div>
                      )}
                    </div>
                  </motion.div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </motion.div>
  );
}
