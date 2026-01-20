"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FolderOpen,
  Plus,
  Trash2,
  Loader2,
  FileText,
  Network,
  ChevronRight,
  ChevronDown,
  Users,
  Sparkles,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Collection, CollectionEntity, Community, TaskProgress } from "@/types";

interface CollectionPanelProps {
  onRefresh?: () => void;
}

export default function CollectionPanel({ onRefresh }: CollectionPanelProps) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [entities, setEntities] = useState<Record<string, CollectionEntity[]>>({});
  const [loadingEntities, setLoadingEntities] = useState<string | null>(null);

  const [communities, setCommunities] = useState<Community[]>([]);
  const [isLoadingCommunities, setIsLoadingCommunities] = useState(false);
  const [isDetecting, setIsDetecting] = useState(false);
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [showCommunities, setShowCommunities] = useState(false);
  const [detectionProgress, setDetectionProgress] = useState<TaskProgress | null>(null);

  const TASK_STORAGE_KEY = "moca_community_detection_task";

  const resumeTaskPolling = async (taskId: string) => {
    setIsDetecting(true);
    setShowCommunities(true);
    try {
      const result = await api.pollTask<{ communities: Community[]; total: number }>(
        taskId,
        (progress) => {
          setDetectionProgress(progress);
        },
        1000
      );
      setCommunities(result.communities);
      localStorage.removeItem(TASK_STORAGE_KEY);
    } catch (error) {
      console.error("Failed to resume task polling:", error);
      localStorage.removeItem(TASK_STORAGE_KEY);
    } finally {
      setIsDetecting(false);
      setDetectionProgress(null);
    }
  };

  useEffect(() => {
    fetchCollections();
    fetchCommunities();
    const savedTaskId = localStorage.getItem(TASK_STORAGE_KEY);
    if (savedTaskId) {
      resumeTaskPolling(savedTaskId);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchCollections = async () => {
    try {
      const data = await api.getCollections();
      setCollections(data.collections);
    } catch (error) {
      console.error("Failed to fetch collections:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchCommunities = async () => {
    setIsLoadingCommunities(true);
    try {
      const data = await api.getCommunities(30);
      setCommunities(data.communities);
    } catch (error) {
      console.error("Failed to fetch communities:", error);
    } finally {
      setIsLoadingCommunities(false);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;

    setIsSubmitting(true);
    try {
      const collection = await api.createCollection({
        name: newName.trim(),
        description: newDescription.trim() || undefined,
      });
      setCollections((prev) => [collection, ...prev]);
      setNewName("");
      setNewDescription("");
      setIsCreating(false);
      onRefresh?.();
    } catch (error) {
      console.error("Failed to create collection:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const collection = collections.find((c) => c.id === id);
    const message = `Delete collection "${collection?.name}"? Documents will be moved to the default collection.`;

    if (!confirm(message)) return;

    setDeletingId(id);
    try {
      await api.deleteCollection(id);
      setCollections((prev) => prev.filter((c) => c.id !== id));
      if (expandedId === id) setExpandedId(null);
      onRefresh?.();
    } catch (error) {
      console.error("Failed to delete collection:", error);
    } finally {
      setDeletingId(null);
    }
  };

  const toggleExpand = async (id: string) => {
    if (expandedId === id) {
      setExpandedId(null);
    } else {
      setExpandedId(id);
      if (!entities[id]) {
        setLoadingEntities(id);
        try {
          const data = await api.getCollectionEntities(id, 50);
          setEntities((prev) => ({ ...prev, [id]: data.entities }));
        } catch (error) {
          console.error("Failed to fetch entities:", error);
        } finally {
          setLoadingEntities(null);
        }
      }
    }
  };

  const handleDetectCommunities = async () => {
    setIsDetecting(true);
    setDetectionProgress(null);
    try {
      const taskStart = await api.detectCommunities(3);
      localStorage.setItem(TASK_STORAGE_KEY, taskStart.task_id);
      const result = await api.pollTask<{ communities: Community[]; total: number }>(
        taskStart.task_id,
        (progress) => {
          setDetectionProgress(progress);
        },
        1000
      );
      setCommunities(result.communities);
      setShowCommunities(true);
      localStorage.removeItem(TASK_STORAGE_KEY);
    } catch (error) {
      console.error("Failed to detect communities:", error);
      localStorage.removeItem(TASK_STORAGE_KEY);
    } finally {
      setIsDetecting(false);
      setDetectionProgress(null);
    }
  };

  const handleSummarizeCommunities = async () => {
    setIsSummarizing(true);
    try {
      await api.summarizeCommunities();
      await fetchCommunities();
    } catch (error) {
      console.error("Failed to summarize communities:", error);
    } finally {
      setIsSummarizing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <Loader2 className="w-8 h-8 text-foreground animate-spin mx-auto mb-4" />
        <p className="text-muted-foreground">Loading collections...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Collections</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Organize documents into collections with scoped knowledge graphs
          </p>
        </div>
        <button
          onClick={() => setIsCreating(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-accent-foreground hover:bg-accent/90 transition-colors"
        >
          <Plus className="w-4 h-4" />
          New Collection
        </button>
      </div>

      <AnimatePresence>
        {isCreating && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="glass rounded-lg p-4 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-foreground">Create Collection</h3>
                <button
                  onClick={() => {
                    setIsCreating(false);
                    setNewName("");
                    setNewDescription("");
                  }}
                  className="p-1 rounded hover:bg-muted text-muted-foreground"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-3">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Collection name"
                  className="w-full px-3 py-2 bg-card rounded-lg text-foreground placeholder:text-muted-foreground border border-border focus:border-foreground focus:outline-none"
                  autoFocus
                />
                <textarea
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="Description (optional)"
                  rows={2}
                  className="w-full px-3 py-2 bg-card rounded-lg text-foreground placeholder:text-muted-foreground border border-border focus:border-foreground focus:outline-none resize-none"
                />
              </div>

              <div className="flex justify-end gap-2">
                <button
                  onClick={() => {
                    setIsCreating(false);
                    setNewName("");
                    setNewDescription("");
                  }}
                  className="px-4 py-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={isSubmitting || !newName.trim()}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-accent-foreground hover:bg-accent/90 disabled:opacity-50 transition-colors"
                >
                  {isSubmitting && <Loader2 className="w-4 h-4 animate-spin" />}
                  Create
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="space-y-3">
        {collections.length === 0 ? (
          <div className="glass rounded-lg p-8 text-center">
            <div className="w-14 h-14 mx-auto rounded-lg bg-accent/20 flex items-center justify-center mb-4">
              <FolderOpen className="w-7 h-7 text-accent" />
            </div>
            <h3 className="text-foreground font-medium mb-2">No Collections Yet</h3>
            <p className="text-muted-foreground text-sm max-w-md mx-auto">
              Create collections to organize your documents and build focused knowledge graphs.
            </p>
          </div>
        ) : (
          collections.map((collection, index) => (
            <motion.div
              key={collection.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.05 }}
              className="glass rounded-lg overflow-hidden"
            >
              <div
                className="p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                onClick={() => toggleExpand(collection.id)}
              >
                <div className="flex items-start gap-4">
                  <div className="w-10 h-10 rounded-lg bg-accent/20 flex items-center justify-center shrink-0">
                    <FolderOpen className="w-5 h-5 text-accent" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-medium text-foreground truncate">
                        {collection.name}
                      </h3>
                      {expandedId === collection.id ? (
                        <ChevronDown className="w-4 h-4 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-muted-foreground" />
                      )}
                    </div>
                    {collection.description && (
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-1">
                        {collection.description}
                      </p>
                    )}
                    <div className="flex items-center gap-4 mt-2">
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <FileText className="w-3.5 h-3.5" />
                        {collection.document_count} documents
                      </span>
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Network className="w-3.5 h-3.5" />
                        {collection.entity_count} entities
                      </span>
                    </div>
                  </div>

                  {collection.id !== "default" && (
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => handleDelete(collection.id)}
                        disabled={deletingId === collection.id}
                        className="p-2 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                        title="Delete collection (documents move to default)"
                      >
                        {deletingId === collection.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Trash2 className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </div>

              <AnimatePresence>
                {expandedId === collection.id && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden border-t border-border"
                  >
                    <div className="p-4 bg-card/30">
                      <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
                        Top Entities
                      </h4>

                      {loadingEntities === collection.id ? (
                        <div className="flex items-center justify-center py-4">
                          <Loader2 className="w-5 h-5 text-accent animate-spin" />
                        </div>
                      ) : entities[collection.id]?.length > 0 ? (
                        <div className="grid gap-2">
                          {entities[collection.id].slice(0, 10).map((entity) => (
                            <div
                              key={entity.name}
                              className="flex items-center gap-3 px-3 py-2 rounded-lg bg-card/50"
                            >
                              <span className="px-2 py-0.5 rounded text-xs font-medium bg-muted text-muted-foreground">
                                {entity.type}
                              </span>
                              <span className="flex-1 text-sm text-foreground truncate">
                                {entity.name}
                              </span>
                              <span className="text-xs text-muted-foreground">
                                {entity.mention_count} mentions
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-sm text-muted-foreground text-center py-4">
                          No entities in this collection yet
                        </p>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          ))
        )}
      </div>

      <div className="pt-6 border-t border-border">
        <div className="flex items-center justify-between mb-4">
          <div
            className="flex items-center gap-2 cursor-pointer"
            onClick={() => setShowCommunities(!showCommunities)}
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
              onClick={handleDetectCommunities}
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
              onClick={handleSummarizeCommunities}
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
                          <h4 className="font-medium text-foreground">
                            {community.name || `Community ${community.id}`}
                          </h4>
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
    </div>
  );
}
