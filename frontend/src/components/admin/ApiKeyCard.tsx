"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  KeyRound,
  ChevronDown,
  BarChart3,
  Trash2,
  ToggleLeft,
  ToggleRight,
  AlertCircle,
  Activity,
  Shield,
} from "lucide-react";
import type { APIKeyWithStats } from "@/types";

interface ApiKeyCardProps {
  apiKey: APIKeyWithStats;
  onRevoke: (keyId: string) => void;
  onActivate: (keyId: string) => void;
  onDelete: (keyId: string) => void;
  onViewAnalytics: (keyId: string) => void;
  isLoading?: boolean;
}

export function ApiKeyCard({
  apiKey,
  onRevoke,
  onActivate,
  onDelete,
  onViewAnalytics,
  isLoading,
}: ApiKeyCardProps) {
  const [showDetails, setShowDetails] = useState(false);

  // Check if this is the protected admin key from .env
  const isProtectedAdminKey = apiKey.id === "admin";
  
  const stats = apiKey.stats;
  const hasStats = stats && stats.total_requests > 0;
  const errorRate = stats && stats.total_requests > 0
    ? ((stats.error_count / stats.total_requests) * 100).toFixed(1)
    : "0";

  // Format time ago
  const formatTimeAgo = (dateStr?: string | null) => {
    if (!dateStr) return "Never";
    
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  // Format large numbers
  const formatNumber = (num: number) => {
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
    return num.toString();
  };

  return (
    <motion.div
      layout
      className={`glass rounded-xl overflow-hidden ${
        !apiKey.is_active ? "opacity-70 border-destructive/30" : ""
      }`}
    >
      {/* Main Row */}
      <div className="p-4 flex items-center gap-4">
        {/* Icon */}
        <div className={`p-2 rounded-lg ${
          apiKey.is_active ? "bg-accent/20" : "bg-muted"
        }`}>
          <KeyRound className={`w-5 h-5 ${
            apiKey.is_active ? "text-accent" : "text-muted-foreground"
          }`} />
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-medium text-foreground truncate">{apiKey.name}</h3>
            {isProtectedAdminKey && (
              <span className="px-2 py-0.5 bg-accent/20 text-accent text-xs rounded-full flex-shrink-0 flex items-center gap-1">
                <Shield className="w-3 h-3" />
                Protected
              </span>
            )}
            {!apiKey.is_active && (
              <span className="px-2 py-0.5 bg-destructive/20 text-destructive text-xs rounded-full flex-shrink-0">
                Revoked
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span className="font-mono bg-muted px-2 py-0.5 rounded text-xs">
              {apiKey.key_prefix}...
            </span>
            {!isProtectedAdminKey && (
              <span className={`px-2 py-0.5 text-xs rounded-full ${
                apiKey.permissions.includes("manage")
                  ? "bg-accent/20 text-accent"
                  : "bg-muted text-muted-foreground"
              }`}>
                {apiKey.permissions.includes("manage") ? "Read/Write" : "Read Only"}
              </span>
            )}
          </div>
        </div>

        {/* Stats Summary */}
        {hasStats && (
          <div className="hidden md:flex items-center gap-4 text-sm">
            <div className="text-center">
              <div className="text-foreground font-medium">
                {formatNumber(stats.total_requests)}
              </div>
              <div className="text-xs text-muted-foreground">Total</div>
            </div>
            <div className="text-center">
              <div className="text-foreground font-medium">
                {formatNumber(stats.requests_today)}
              </div>
              <div className="text-xs text-muted-foreground">Today</div>
            </div>
            {stats.error_count > 0 && (
              <div className="text-center">
                <div className="text-destructive font-medium">{errorRate}%</div>
                <div className="text-xs text-muted-foreground">Errors</div>
              </div>
            )}
          </div>
        )}

        {/* Last Used */}
        <div className="hidden sm:block text-right text-sm">
          <div className="text-muted-foreground text-xs">Last used</div>
          <div className="text-foreground">{formatTimeAgo(apiKey.last_used_at)}</div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => onViewAnalytics(apiKey.id)}
            className="p-2 rounded-lg hover:bg-accent/20 text-muted-foreground hover:text-accent transition-colors"
            title="View analytics"
          >
            <BarChart3 className="w-4 h-4" />
          </button>
          
          <button
            onClick={() => apiKey.is_active ? onRevoke(apiKey.id) : onActivate(apiKey.id)}
            className={`p-2 rounded-lg transition-colors ${
              isProtectedAdminKey
                ? "opacity-30 cursor-not-allowed"
                : apiKey.is_active
                  ? "hover:bg-destructive/20 text-muted-foreground hover:text-destructive"
                  : "hover:bg-accent/20 text-muted-foreground hover:text-accent"
            }`}
            title={isProtectedAdminKey 
              ? "Admin key is protected" 
              : apiKey.is_active ? "Revoke key" : "Activate key"
            }
            disabled={isLoading || isProtectedAdminKey}
          >
            {apiKey.is_active ? (
              <ToggleRight className="w-4 h-4" />
            ) : (
              <ToggleLeft className="w-4 h-4" />
            )}
          </button>

          <button
            onClick={() => setShowDetails(!showDetails)}
            className="p-2 rounded-lg hover:bg-muted text-muted-foreground transition-colors"
          >
            <ChevronDown className={`w-4 h-4 transition-transform ${
              showDetails ? "rotate-180" : ""
            }`} />
          </button>
        </div>
      </div>

      {/* Expandable Details */}
      <AnimatePresence>
        {showDetails && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 pt-2 border-t border-border/50">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {/* Stats Grid */}
                <div className="bg-muted/50 rounded-lg p-3">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                    <Activity className="w-3 h-3" />
                    This Week
                  </div>
                  <div className="text-lg font-medium text-foreground">
                    {formatNumber(stats?.requests_this_week || 0)}
                  </div>
                </div>
                
                <div className="bg-muted/50 rounded-lg p-3">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                    <Activity className="w-3 h-3" />
                    This Month
                  </div>
                  <div className="text-lg font-medium text-foreground">
                    {formatNumber(stats?.requests_this_month || 0)}
                  </div>
                </div>

                <div className="bg-muted/50 rounded-lg p-3">
                  <div className="text-xs text-muted-foreground mb-1">Created</div>
                  <div className="text-sm text-foreground">
                    {new Date(apiKey.created_at).toLocaleDateString()}
                  </div>
                </div>

                <div className="bg-muted/50 rounded-lg p-3">
                  <div className="text-xs text-muted-foreground mb-1">Created By</div>
                  <div className="text-sm text-foreground">{apiKey.created_by}</div>
                </div>
              </div>

              {/* Last Error */}
              {stats?.last_error_message && (
                <div className="mt-3 bg-destructive/10 border border-destructive/20 rounded-lg p-3">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 text-destructive flex-shrink-0 mt-0.5" />
                    <div>
                      <div className="text-xs text-destructive mb-1">
                        Last error: {formatTimeAgo(stats.last_error_at)}
                      </div>
                      <div className="text-sm text-destructive/80">
                        {stats.last_error_message}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Endpoint Breakdown */}
              {stats?.endpoint_breakdown && Object.keys(stats.endpoint_breakdown).length > 0 && (
                <div className="mt-3">
                  <div className="text-xs text-muted-foreground mb-2">Endpoint Usage</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(stats.endpoint_breakdown)
                      .sort(([, a], [, b]) => b - a)
                      .map(([endpoint, count]) => (
                        <span
                          key={endpoint}
                          className="px-2 py-1 bg-muted rounded text-xs text-foreground"
                        >
                          {endpoint}: {formatNumber(count)}
                        </span>
                      ))}
                  </div>
                </div>
              )}

              {/* Delete Button (hidden for protected admin key) */}
              {!isProtectedAdminKey && (
                <div className="mt-4 pt-3 border-t border-border/50 flex justify-end">
                  <button
                    onClick={() => onDelete(apiKey.id)}
                    disabled={isLoading}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm bg-destructive/10 hover:bg-destructive/20 text-destructive rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete Key
                  </button>
                </div>
              )}
              
              {/* Protected notice for admin key */}
              {isProtectedAdminKey && (
                <div className="mt-4 pt-3 border-t border-border/50">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 rounded-lg p-3">
                    <Shield className="w-4 h-4 text-accent" />
                    <span>This is the system admin key and cannot be modified or deleted.</span>
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
