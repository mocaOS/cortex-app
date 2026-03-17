"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import type { GraphEdge } from "@/types";
import { Loader2, Search, Share2, Trash2 } from "lucide-react";

export default function RelationshipsPage() {
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [taskMessage, setTaskMessage] = useState<string | null>(null);
  const [discoveredCount, setDiscoveredCount] = useState(0);
  const initialEdgeCount = useRef(0);

  const fetchRelationships = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await api.getGraphVisualization(1000, false);
      setEdges(data.edges || []);
      return data.edges?.length || 0;
    } catch (error) {
      console.error("Failed to fetch relationships:", error);
      return 0;
    } finally {
      if (!silent) setLoading(false);
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

  const handleDeleteRelationships = async () => {
    if (!confirm("Delete all relationships? This cannot be undone.")) return;
    try {
      const result = await api.deleteAllRelationships();
      setTaskMessage(`Deleted ${result.relationships_deleted} relationships.`);
      await fetchRelationships();
      setTimeout(() => setTaskMessage(null), 3000);
    } catch (error) {
      console.error("Failed to delete relationships:", error);
    }
  };

  const handleAnalyzeRelationships = async () => {
    try {
      setAnalyzing(true);
      setDiscoveredCount(0);
      initialEdgeCount.current = edges.length;
      setTaskMessage("Starting relationship analysis...");
      const result = await api.analyzeRelationships();
      const taskId = result.task_id;

      // Poll task status and fetch relationships for live updates
      const poll = async () => {
        try {
          const status = await api.getTaskStatus(taskId);
          
          // Fetch relationships to show live progress
          const currentCount = await fetchRelationships(true);
          const newlyDiscovered = currentCount - initialEdgeCount.current;
          setDiscoveredCount(newlyDiscovered);
          
          // Update message with discovered count
          const progressMsg = status.message || `Progress: ${status.progress_percent}%`;
          const countMsg = newlyDiscovered > 0 ? ` (${newlyDiscovered} new relationships found)` : "";
          setTaskMessage(progressMsg + countMsg);
          
          if (status.status === "completed") {
            const finalCount = await fetchRelationships(true);
            const totalNew = finalCount - initialEdgeCount.current;
            setTaskMessage(`Completed! ${totalNew} relationships discovered.`);
            setTimeout(() => {
              setTaskMessage(null);
              setAnalyzing(false);
              setDiscoveredCount(0);
            }, 3000);
          } else if (status.status === "failed") {
            setTaskMessage(`Failed: ${status.message}`);
            setAnalyzing(false);
          } else {
            setTimeout(poll, 2000);
          }
        } catch {
          setTaskMessage(null);
          setAnalyzing(false);
        }
      };
      setTimeout(poll, 1500);
    } catch (error) {
      console.error("Failed to analyze relationships:", error);
      setTaskMessage(null);
      setAnalyzing(false);
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
        <button
          onClick={handleAnalyzeRelationships}
          disabled={analyzing}
          className="flex items-center gap-1.5 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          {analyzing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Share2 className="w-4 h-4" />
          )}
          {analyzing ? "Analyzing..." : "Analyze Relationships"}
        </button>
        {edges.length > 0 && !analyzing && (
          <button
            onClick={handleDeleteRelationships}
            className="flex items-center gap-1.5 px-4 py-2 bg-destructive text-destructive-foreground rounded-lg text-sm font-medium hover:bg-destructive/90 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            Delete All
          </button>
        )}
      </div>

      {taskMessage && (
        <div className="mb-4 p-4 bg-accent/10 border border-accent/20 rounded-lg">
          <div className="flex items-center gap-3">
            {analyzing && <Loader2 className="w-4 h-4 animate-spin text-accent" />}
            <span className="text-sm text-accent">{taskMessage}</span>
          </div>
          {analyzing && discoveredCount > 0 && (
            <div className="mt-2 text-xs text-muted-foreground">
              Relationships are being added to the table below in real-time.
            </div>
          )}
        </div>
      )}

      {edges.length === 0 && !analyzing ? (
        <div className="text-center py-12">
          <Share2 className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Relationships Found</h3>
          <p className="text-muted-foreground">
            Click &quot;Analyze Relationships&quot; above to discover connections between entities across your documents.
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
