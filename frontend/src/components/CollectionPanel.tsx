"use client";

import { useState, useEffect } from "react";
import { AnimatePresence } from "framer-motion";
import { FolderOpen, Plus, Loader2, AlertCircle, X } from "lucide-react";
import { api } from "@/lib/api";
import { useIsMounted } from "@/lib/hooks";
import type { Collection, CollectionEntity, Community, TaskProgress } from "@/types";
import { CollectionCard, CreateCollectionForm, CommunitySection } from "./collections";

interface CollectionPanelProps {
  onRefresh?: () => void;
}

export default function CollectionPanel({ onRefresh }: CollectionPanelProps) {
  const mounted = useIsMounted();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [error, setError] = useState<string | null>(null);
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
  const [deletingCommunityId, setDeletingCommunityId] = useState<number | null>(null);

  const TASK_STORAGE_KEY = "cortex_community_detection_task";

  const resumeTaskPolling = async (taskId: string) => {
    setIsDetecting(true);
    setShowCommunities(true);
    try {
      const result = await api.pollTask<{ communities: Community[]; total: number }>(
        taskId,
        (progress) => {
          if (!mounted.current) return;
          setDetectionProgress(progress);
        },
        1000
      );
      if (!mounted.current) return;
      setCommunities(result.communities);
      localStorage.removeItem(TASK_STORAGE_KEY);
    } catch (err) {
      console.error("Failed to resume task polling:", err);
      localStorage.removeItem(TASK_STORAGE_KEY);
      if (mounted.current) setError(err instanceof Error ? err.message : "Failed to resume community detection");
    } finally {
      if (mounted.current) {
        setIsDetecting(false);
        setDetectionProgress(null);
      }
    }
  };

  useEffect(() => {
    fetchCollections();
    fetchCommunities();
    const savedTaskId = localStorage.getItem(TASK_STORAGE_KEY);
    if (savedTaskId) {
      resumeTaskPolling(savedTaskId);
    }
  }, []);

  const fetchCollections = async () => {
    try {
      const data = await api.getCollections();
      setCollections(data.collections);
    } catch (err) {
      console.error("Failed to fetch collections:", err);
      setError(err instanceof Error ? err.message : "Failed to load collections");
    } finally {
      setIsLoading(false);
    }
  };

  const fetchCommunities = async () => {
    setIsLoadingCommunities(true);
    try {
      const data = await api.getCommunities(30);
      setCommunities(data.communities);
    } catch (err) {
      console.error("Failed to fetch communities:", err);
      setError(err instanceof Error ? err.message : "Failed to load communities");
    } finally {
      setIsLoadingCommunities(false);
    }
  };

  const handleDeleteCommunity = async (id: number) => {
    if (!confirm("Delete this community? Entities will be unlinked but not deleted.")) return;
    setDeletingCommunityId(id);
    try {
      await api.deleteCommunity(id);
      setCommunities((prev) => prev.filter((c) => c.id !== id));
    } catch (err) {
      console.error("Failed to delete community:", err);
      setError(err instanceof Error ? err.message : "Failed to delete community");
    } finally {
      setDeletingCommunityId(null);
    }
  };

  const handleDeleteAllCommunities = async () => {
    if (!confirm(`Delete all ${communities.length} communities? Entities will be unlinked but not deleted.`)) return;
    setIsLoadingCommunities(true);
    try {
      await api.deleteAllCommunities();
      setCommunities([]);
    } catch (err) {
      console.error("Failed to delete all communities:", err);
      setError(err instanceof Error ? err.message : "Failed to delete communities");
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
    } catch (err) {
      console.error("Failed to create collection:", err);
      setError(err instanceof Error ? err.message : "Failed to create collection");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRename = async (id: string, name: string) => {
    try {
      const updated = await api.updateCollection(id, { name });
      setCollections((prev) =>
        prev.map((c) => (c.id === id ? { ...c, name: updated.name } : c))
      );
    } catch (err) {
      console.error("Failed to rename collection:", err);
      setError(err instanceof Error ? err.message : "Failed to rename collection");
      throw err;
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
    } catch (err) {
      console.error("Failed to delete collection:", err);
      setError(err instanceof Error ? err.message : "Failed to delete collection");
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
        } catch (err) {
          console.error("Failed to fetch entities:", err);
          setError(err instanceof Error ? err.message : "Failed to load collection entities");
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
          if (!mounted.current) return;
          setDetectionProgress(progress);
        },
        1000
      );
      if (!mounted.current) return;
      setCommunities(result.communities);
      setShowCommunities(true);
      localStorage.removeItem(TASK_STORAGE_KEY);
    } catch (err) {
      console.error("Failed to detect communities:", err);
      localStorage.removeItem(TASK_STORAGE_KEY);
      if (mounted.current) setError(err instanceof Error ? err.message : "Failed to detect communities");
    } finally {
      if (mounted.current) {
        setIsDetecting(false);
        setDetectionProgress(null);
      }
    }
  };

  const handleSummarizeCommunities = async () => {
    setIsSummarizing(true);
    try {
      await api.summarizeCommunities();
      await fetchCommunities();
    } catch (err) {
      console.error("Failed to summarize communities:", err);
      setError(err instanceof Error ? err.message : "Failed to summarize communities");
    } finally {
      setIsSummarizing(false);
    }
  };

  const handleCancelCreate = () => {
    setIsCreating(false);
    setNewName("");
    setNewDescription("");
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

      {error && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-destructive flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span className="text-sm">{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-destructive hover:text-destructive/80">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <AnimatePresence>
        {isCreating && (
          <CreateCollectionForm
            name={newName}
            description={newDescription}
            onNameChange={setNewName}
            onDescriptionChange={setNewDescription}
            onSubmit={handleCreate}
            onCancel={handleCancelCreate}
            isSubmitting={isSubmitting}
          />
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
            <CollectionCard
              key={collection.id}
              collection={collection}
              index={index}
              isExpanded={expandedId === collection.id}
              isDeleting={deletingId === collection.id}
              isLoadingEntities={loadingEntities === collection.id}
              entities={entities[collection.id] || []}
              onToggleExpand={() => toggleExpand(collection.id)}
              onDelete={() => handleDelete(collection.id)}
              onRename={(name) => handleRename(collection.id, name)}
            />
          ))
        )}
      </div>

      <CommunitySection
        communities={communities}
        isLoadingCommunities={isLoadingCommunities}
        isDetecting={isDetecting}
        isSummarizing={isSummarizing}
        showCommunities={showCommunities}
        detectionProgress={detectionProgress}
        onToggleShow={() => setShowCommunities(!showCommunities)}
        onDetect={handleDetectCommunities}
        onSummarize={handleSummarizeCommunities}
        onDelete={handleDeleteCommunity}
        onDeleteAll={handleDeleteAllCommunities}
        isDeleting={deletingCommunityId}
      />
    </div>
  );
}
