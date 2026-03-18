"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { Loader2, Users } from "lucide-react";

interface Community {
  id: number;
  name?: string;
  summary?: string;
  entity_count: number;
  sample_entities?: string[];
}

export default function CommunitiesBrowser() {
  const [communities, setCommunities] = useState<Community[]>([]);
  const [loading, setLoading] = useState(true);

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

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (communities.length === 0) {
    return (
      <div className="text-center py-12">
        <Users className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
        <h3 className="text-lg font-medium mb-2">No Communities Detected</h3>
        <p className="text-muted-foreground">
          Use the Extract &amp; Analyze page to detect entity communities in your knowledge graph.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <span className="text-sm text-muted-foreground">
          {communities.length} communit{communities.length !== 1 ? "ies" : "y"}
        </span>
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
              <span className="text-sm text-muted-foreground">
                {community.entity_count} entities
              </span>
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
    </div>
  );
}
