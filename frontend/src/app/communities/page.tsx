"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { Loader2, Users, Trash2 } from "lucide-react";

interface Community {
  id: number;
  name?: string;
  summary?: string;
  entity_count: number;
  sample_entities?: string[];
}

export default function CommunitiesPage() {
  const [communities, setCommunities] = useState<Community[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<number | null>(null);

  const fetchCommunities = useCallback(async () => {
    try {
      const response = await api.getCommunities(50);
      setCommunities(response.communities);
    } catch (error) {
      console.error("Failed to fetch communities:", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCommunities();
  }, [fetchCommunities]);

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this community? Entities will be unlinked but not deleted.")) return;
    setDeleting(id);
    try {
      await api.deleteCommunity(id);
      setCommunities((prev) => prev.filter((c) => c.id !== id));
    } catch (error) {
      console.error("Failed to delete community:", error);
    } finally {
      setDeleting(null);
    }
  };

  const handleDeleteAll = async () => {
    if (!confirm(`Delete all ${communities.length} communities? Entities will be unlinked but not deleted.`)) return;
    setLoading(true);
    try {
      await api.deleteAllCommunities();
      setCommunities([]);
    } catch (error) {
      console.error("Failed to delete all communities:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleDetectCommunities = async () => {
    try {
      setLoading(true);
      await api.detectCommunities(3);
      await fetchCommunities();
    } catch (error) {
      console.error("Failed to detect communities:", error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="py-6">
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  return (
    <div className="py-6">
      {communities.length === 0 ? (
        <div className="text-center py-12">
          <Users className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Communities Detected</h3>
          <p className="text-muted-foreground mb-4">
            Run community detection to discover entity clusters in your knowledge graph.
          </p>
          <button
            onClick={handleDetectCommunities}
            className="px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
          >
            Detect Communities
          </button>
        </div>
      ) : (
        <>
          <div className="flex justify-end mb-4">
            <button
              onClick={handleDeleteAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-red-400 hover:bg-red-500/10 transition-colors"
            >
              <Trash2 className="w-4 h-4" />
              Delete All
            </button>
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {communities.map((community) => (
              <div
                key={community.id}
                className="p-4 bg-card border border-border rounded-lg hover:border-accent/50 transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-medium">
                    {community.name || `Community ${community.id}`}
                  </h3>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">
                      {community.entity_count} entities
                    </span>
                    <button
                      onClick={() => handleDelete(community.id)}
                      disabled={deleting === community.id}
                      className="p-1 rounded hover:bg-red-500/10 text-muted-foreground hover:text-red-400 transition-colors disabled:opacity-50"
                      title="Delete community"
                    >
                      {deleting === community.id ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                </div>
                {community.summary && (
                  <p className="text-sm text-muted-foreground line-clamp-3 mb-3">
                    {community.summary}
                  </p>
                )}
                {community.sample_entities && community.sample_entities.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {community.sample_entities.slice(0, 5).map((entity, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground"
                      >
                        {entity}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
