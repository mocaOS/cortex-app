"use client";

import { motion, AnimatePresence } from "framer-motion";
import {
  Users,
  Network,
  Sparkles,
  Loader2,
  ChevronRight,
  ChevronDown,
  Trash2,
} from "lucide-react";
import type { Community, TaskProgress } from "@/types";

interface CommunitySectionProps {
  communities: Community[];
  isLoadingCommunities: boolean;
  isDetecting: boolean;
  isSummarizing: boolean;
  showCommunities: boolean;
  detectionProgress: TaskProgress | null;
  onToggleShow: () => void;
  onDetect: () => void;
  onSummarize: () => void;
  onDelete?: (id: number) => void;
  onDeleteAll?: () => void;
  isDeleting?: number | null;
}

export default function CommunitySection({
  communities,
  isLoadingCommunities,
  isDetecting,
  isSummarizing,
  showCommunities,
  detectionProgress,
  onToggleShow,
  onDetect,
  onSummarize,
  onDelete,
  onDeleteAll,
  isDeleting,
}: CommunitySectionProps) {
  return (
    <div className="pt-6 border-t border-border">
      <div className="flex items-center justify-between mb-4">
        <div
          className="flex items-center gap-2 cursor-pointer"
          onClick={onToggleShow}
        >
          <Users className="w-5 h-5 text-foreground" />
          <h2 className="text-lg font-semibold text-foreground">Entity Communities</h2>
          <span className="text-sm text-muted-foreground">({communities.length})</span>
          {showCommunities ? (
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted-foreground" />
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onDetect}
            disabled={isDetecting}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-muted text-foreground hover:bg-border disabled:opacity-50 transition-colors"
          >
            {isDetecting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Network className="w-4 h-4" />
            )}
            Detect
          </button>
          <button
            onClick={onSummarize}
            disabled={isSummarizing || communities.length === 0}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-muted text-foreground hover:bg-border disabled:opacity-50 transition-colors"
          >
            {isSummarizing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Sparkles className="w-4 h-4" />
            )}
            Summarize
          </button>
          {onDeleteAll && communities.length > 0 && (
            <button
              onClick={onDeleteAll}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-red-400 hover:bg-red-500/10 transition-colors"
            >
              <Trash2 className="w-4 h-4" />
              Delete All
            </button>
          )}
        </div>
      </div>

      <AnimatePresence>
        {isDetecting && detectionProgress && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-4"
          >
            <div className="glass rounded-lg p-4">
              <div className="flex items-center gap-3 mb-3">
                <Loader2 className="w-5 h-5 text-foreground animate-spin" />
                <div className="flex-1">
                  <p className="text-sm text-foreground">{detectionProgress.message}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {detectionProgress.progress_current > 0 && detectionProgress.progress_total > 0
                      ? `Step ${detectionProgress.progress_current} of ${detectionProgress.progress_total}`
                      : "Initializing..."}
                  </p>
                </div>
                <span className="text-sm font-medium text-foreground">
                  {Math.round(detectionProgress.progress_percent)}%
                </span>
              </div>
              <div className="h-2 bg-border rounded-full overflow-hidden">
                <motion.div
                  className="h-full bg-accent"
                  initial={{ width: 0 }}
                  animate={{ width: `${detectionProgress.progress_percent}%` }}
                  transition={{ duration: 0.3 }}
                />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showCommunities && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            {isLoadingCommunities ? (
              <div className="glass rounded-lg p-8 text-center">
                <Loader2 className="w-6 h-6 text-accent animate-spin mx-auto mb-2" />
                <p className="text-muted-foreground text-sm">Loading communities...</p>
              </div>
            ) : communities.length === 0 ? (
              <div className="glass rounded-lg p-8 text-center">
                <Users className="w-8 h-8 text-accent/50 mx-auto mb-3" />
                <h3 className="text-foreground font-medium mb-2">No Communities Detected</h3>
                <p className="text-muted-foreground text-sm max-w-md mx-auto">
                  Click &quot;Detect&quot; to find groups of related entities in your knowledge graph.
                </p>
              </div>
            ) : (
              <div className="grid gap-3">
                {communities.map((community, index) => (
                  <motion.div
                    key={community.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: index * 0.03 }}
                    className="glass rounded-lg p-4"
                  >
                    <div className="flex items-start gap-3">
                      <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center shrink-0">
                        <span className="text-sm font-medium text-foreground">
                          {community.id}
                        </span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <h4 className="font-medium text-foreground">
                            {community.name || `Community ${community.id}`}
                          </h4>
                          {onDelete && (
                            <button
                              onClick={() => onDelete(community.id)}
                              disabled={isDeleting === community.id}
                              className="p-1 rounded hover:bg-red-500/10 text-muted-foreground hover:text-red-400 transition-colors disabled:opacity-50"
                              title="Delete community"
                            >
                              {isDeleting === community.id ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <Trash2 className="w-3.5 h-3.5" />
                              )}
                            </button>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {community.entity_count} entities
                        </p>
                        {community.summary && (
                          <p className="text-sm text-muted-foreground mt-2 line-clamp-2">
                            {community.summary}
                          </p>
                        )}
                        {community.sample_entities && community.sample_entities.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mt-2">
                            {community.sample_entities.slice(0, 5).map((name) => (
                              <span
                                key={name}
                                className="px-2 py-0.5 rounded-full bg-muted text-xs text-muted-foreground"
                              >
                                {name}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
