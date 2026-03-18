"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { api } from "@/lib/api";
import type { GraphEdge } from "@/types";
import { Loader2, Search, Share2 } from "lucide-react";

export default function RelationshipsBrowser() {
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");

  const fetchRelationships = useCallback(async () => {
    try {
      const data = await api.getGraphVisualization(1000, false);
      setEdges(data.edges || []);
    } catch (error) {
      console.error("Failed to fetch relationships:", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRelationships();
  }, [fetchRelationships]);

  const filteredEdges = useMemo(() => {
    if (!searchQuery) return edges;
    const query = searchQuery.toLowerCase();
    return edges.filter(
      (e) =>
        e.source.toLowerCase().includes(query) ||
        e.target.toLowerCase().includes(query) ||
        e.type.toLowerCase().includes(query)
    );
  }, [edges, searchQuery]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search relationships..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <span className="text-sm text-muted-foreground">
          {edges.length} relationship{edges.length !== 1 ? "s" : ""}
        </span>
      </div>

      {edges.length === 0 ? (
        <div className="text-center py-12">
          <Share2 className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Relationships Yet</h3>
          <p className="text-muted-foreground">
            Use the Extract &amp; Analyze page to discover relationships between entities.
          </p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto bg-card border border-border rounded-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-3 px-4 font-medium text-muted-foreground">Source</th>
                  <th className="text-left py-3 px-4 font-medium text-muted-foreground">Relationship</th>
                  <th className="text-left py-3 px-4 font-medium text-muted-foreground">Target</th>
                </tr>
              </thead>
              <tbody>
                {filteredEdges.map((edge, idx) => (
                  <tr key={idx} className="border-b border-border/50 hover:bg-muted/50">
                    <td className="py-3 px-4">{edge.source}</td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-1 bg-accent/20 text-accent rounded-full text-xs font-medium">
                        {edge.type}
                      </span>
                    </td>
                    <td className="py-3 px-4">{edge.target}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {filteredEdges.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">
              No relationships found matching your search.
            </div>
          )}
        </>
      )}
    </div>
  );
}
