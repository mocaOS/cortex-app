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
  AlertCircle,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Collection, CollectionEntity, Community } from "@/types";

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

  // Community detection state
  const [communities, setCommunities] = useState<Community[]>([]);
  const [isLoadingCommunities, setIsLoadingCommunities] = useState(false);
  const [isDetecting, setIsDetecting] = useState(false);
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [showCommunities, setShowCommunities] = useState(false);

  useEffect(() => {
    fetchCollections();
    fetchCommunities();
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

  const handleDelete = async (id: string, deleteDocuments: boolean) => {
    const collection = collections.find((c) => c.id === id);
    const message = deleteDocuments
      ? `Delete collection "${collection?.name}" and ALL its documents? This cannot be undone.`
      : `Delete collection "${collection?.name}"? Documents will be kept.`;

    if (!confirm(message)) return;

    setDeletingId(id);
    try {
      await api.deleteCollection(id, deleteDocuments);
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
    try {
      const data = await api.detectCommunities(3);
      setCommunities(data.communities);
    } catch (error) {
      console.error("Failed to detect communities:", error);
    } finally {
      setIsDetecting(false);
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
      <div className="glass rounded-xl p-12 text-center">
        <Loader2 className="w-8 h-8 text-ocean-400 animate-spin mx-auto mb-4" />
        <p className="text-white/50">Loading collections...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white/90">Collections</h2>
          <p className="text-sm text-white/40 mt-1">
            Organize documents into collections with scoped knowledge graphs
          </p>
        </div>
        <button
          onClick={() => setIsCreating(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-ocean-500/20 text-ocean-400 hover:bg-ocean-500/30 transition-colors"
        >
          <Plus className="w-4 h-4" />
          New Collection
        </button>
      </div>

      {/* Create form */}
      <AnimatePresence>
        {isCreating && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="glass rounded-xl p-4 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-white/80">Create Collection</h3>
                <button
                  onClick={() => {
                    setIsCreating(false);
                    setNewName("");
                    setNewDescription("");
                  }}
                  className="p-1 rounded hover:bg-white/5 text-white/40"
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
                  className="w-full px-3 py-2 bg-white/5 rounded-lg text-white placeholder:text-white/30 border border-white/10 focus:border-ocean-500/50 focus:outline-none"
                  autoFocus
                />
                <textarea
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="Description (optional)"
                  rows={2}
                  className="w-full px-3 py-2 bg-white/5 rounded-lg text-white placeholder:text-white/30 border border-white/10 focus:border-ocean-500/50 focus:outline-none resize-none"
                />
              </div>

              <div className="flex justify-end gap-2">
                <button
                  onClick={() => {
                    setIsCreating(false);
                    setNewName("");
                    setNewDescription("");
                  }}
                  className="px-4 py-2 rounded-lg text-white/50 hover:text-white/70 hover:bg-white/5 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={isSubmitting || !newName.trim()}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-ocean-500 text-white hover:bg-ocean-600 disabled:opacity-50 transition-colors"
                >
                  {isSubmitting && <Loader2 className="w-4 h-4 animate-spin" />}
                  Create
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Collections list */}
      <div className="space-y-3">
        {collections.length === 0 ? (
          <div className="glass rounded-xl p-8 text-center">
            <div className="w-14 h-14 mx-auto rounded-xl bg-gradient-to-br from-ocean-500/20 to-cyan-500/20 flex items-center justify-center mb-4">
              <FolderOpen className="w-7 h-7 text-ocean-400/60" />
            </div>
            <h3 className="text-white/70 font-medium mb-2">No Collections Yet</h3>
            <p className="text-white/40 text-sm max-w-md mx-auto">
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
              className="glass rounded-xl overflow-hidden"
            >
              {/* Collection header */}
              <div
                className="p-4 cursor-pointer hover:bg-white/[0.02] transition-colors"
                onClick={() => toggleExpand(collection.id)}
              >
                <div className="flex items-start gap-4">
                  <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-ocean-500/20 to-cyan-500/20 flex items-center justify-center shrink-0">
                    <FolderOpen className="w-5 h-5 text-ocean-400" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-medium text-white/90 truncate">
                        {collection.name}
                      </h3>
                      {expandedId === collection.id ? (
                        <ChevronDown className="w-4 h-4 text-white/40" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-white/40" />
                      )}
                    </div>
                    {collection.description && (
                      <p className="text-sm text-white/40 mt-1 line-clamp-1">
                        {collection.description}
                      </p>
                    )}
                    <div className="flex items-center gap-4 mt-2">
                      <span className="flex items-center gap-1.5 text-xs text-white/40">
                        <FileText className="w-3.5 h-3.5" />
                        {collection.document_count} documents
                      </span>
                      <span className="flex items-center gap-1.5 text-xs text-white/40">
                        <Network className="w-3.5 h-3.5" />
                        {collection.entity_count} entities
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => handleDelete(collection.id, false)}
                      disabled={deletingId === collection.id}
                      className="p-2 rounded-lg text-white/40 hover:text-coral-400 hover:bg-coral-500/10 transition-colors"
                      title="Delete collection only"
                    >
                      {deletingId === collection.id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Trash2 className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>
              </div>

              {/* Expanded content */}
              <AnimatePresence>
                {expandedId === collection.id && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden border-t border-white/5"
                  >
                    <div className="p-4 bg-white/[0.01]">
                      <h4 className="text-xs font-medium text-white/50 uppercase tracking-wider mb-3">
                        Top Entities
                      </h4>

                      {loadingEntities === collection.id ? (
                        <div className="flex items-center justify-center py-4">
                          <Loader2 className="w-5 h-5 text-ocean-400 animate-spin" />
                        </div>
                      ) : entities[collection.id]?.length > 0 ? (
                        <div className="grid gap-2">
                          {entities[collection.id].slice(0, 10).map((entity) => (
                            <div
                              key={entity.name}
                              className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white/[0.02]"
                            >
                              <span
                                className={cn(
                                  "px-2 py-0.5 rounded text-xs font-medium",
                                  entity.type === "Person" && "bg-purple-500/20 text-purple-400",
                                  entity.type === "Organization" && "bg-blue-500/20 text-blue-400",
                                  entity.type === "Technology" && "bg-cyan-500/20 text-cyan-400",
                                  entity.type === "Concept" && "bg-pink-500/20 text-pink-400",
                                  entity.type === "Location" && "bg-green-500/20 text-green-400",
                                  !["Person", "Organization", "Technology", "Concept", "Location"].includes(entity.type) &&
                                    "bg-white/10 text-white/60"
                                )}
                              >
                                {entity.type}
                              </span>
                              <span className="flex-1 text-sm text-white/80 truncate">
                                {entity.name}
                              </span>
                              <span className="text-xs text-white/30">
                                {entity.mention_count} mentions
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-sm text-white/40 text-center py-4">
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

      {/* Communities Section */}
      <div className="pt-6 border-t border-white/5">
        <div className="flex items-center justify-between mb-4">
          <div
            className="flex items-center gap-2 cursor-pointer"
            onClick={() => setShowCommunities(!showCommunities)}
          >
            <Users className="w-5 h-5 text-purple-400" />
            <h2 className="text-lg font-semibold text-white/90">Entity Communities</h2>
            <span className="text-sm text-white/40">({communities.length})</span>
            {showCommunities ? (
              <ChevronDown className="w-4 h-4 text-white/40" />
            ) : (
              <ChevronRight className="w-4 h-4 text-white/40" />
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleDetectCommunities}
              disabled={isDetecting}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 disabled:opacity-50 transition-colors"
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
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 disabled:opacity-50 transition-colors"
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
          {showCommunities && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              {isLoadingCommunities ? (
                <div className="glass rounded-xl p-8 text-center">
                  <Loader2 className="w-6 h-6 text-purple-400 animate-spin mx-auto mb-2" />
                  <p className="text-white/40 text-sm">Loading communities...</p>
                </div>
              ) : communities.length === 0 ? (
                <div className="glass rounded-xl p-8 text-center">
                  <Users className="w-8 h-8 text-purple-400/50 mx-auto mb-3" />
                  <h3 className="text-white/70 font-medium mb-2">No Communities Detected</h3>
                  <p className="text-white/40 text-sm max-w-md mx-auto">
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
                      className="glass rounded-xl p-4"
                    >
                      <div className="flex items-start gap-3">
                        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500/20 to-pink-500/20 flex items-center justify-center shrink-0">
                          <span className="text-sm font-medium text-purple-400">
                            {community.id}
                          </span>
                        </div>
                        <div className="flex-1 min-w-0">
                          <h4 className="font-medium text-white/90">
                            {community.name || `Community ${community.id}`}
                          </h4>
                          <p className="text-xs text-white/40 mt-0.5">
                            {community.entity_count} entities
                          </p>
                          {community.summary && (
                            <p className="text-sm text-white/60 mt-2 line-clamp-2">
                              {community.summary}
                            </p>
                          )}
                          {community.sample_entities && community.sample_entities.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mt-2">
                              {community.sample_entities.slice(0, 5).map((name) => (
                                <span
                                  key={name}
                                  className="px-2 py-0.5 rounded-full bg-white/5 text-xs text-white/50"
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
