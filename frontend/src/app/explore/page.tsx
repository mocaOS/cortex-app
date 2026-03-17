"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { api } from "@/lib/api";
import type { GraphData, GraphNode, GraphEdge } from "@/types";
import { KnowledgeGraph } from "@/components/explore";
import { Loader2, RefreshCw, Search, Filter, Share2, Users, Network, Layers, ChevronDown, Check, X, Sparkles, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

type TabType = "graph" | "entities" | "relationships" | "communities";

// Search result from entity search
interface EntitySearchResult {
  name: string;
  type: string;
  description: string;
  score: number;
}

// Custom dropdown component matching project style
function Dropdown<T extends string | number>({
  value,
  options,
  onChange,
  icon: Icon,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  icon?: React.ElementType;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const selectedOption = options.find((o) => o.value === value);
  const displayLabel = selectedOption?.label || "";

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors",
          "bg-muted/50 hover:bg-muted border border-border",
          isOpen && "bg-muted ring-2 ring-accent"
        )}
      >
        {Icon && <Icon className="w-4 h-4 text-muted-foreground" />}
        <span className="text-foreground">{displayLabel}</span>
        <ChevronDown className={cn("w-4 h-4 text-muted-foreground transition-transform", isOpen && "rotate-180")} />
      </button>

      {isOpen && (
        <div className="absolute top-full right-0 mt-1 min-w-[140px] bg-popover border border-border rounded-lg shadow-lg z-50 py-1">
          {options.map((option) => (
            <button
              key={String(option.value)}
              onClick={() => {
                onChange(option.value);
                setIsOpen(false);
              }}
              className={cn(
                "w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-muted transition-colors",
                value === option.value && "bg-muted/50"
              )}
            >
              <span className="text-foreground">{option.label}</span>
              {value === option.value && <Check className="w-4 h-4 text-accent" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface TabProps {
  active: TabType;
  onChange: (tab: TabType) => void;
}

function TabNav({ active, onChange }: TabProps) {
  const tabs: { id: TabType; label: string; icon: React.ElementType }[] = [
    { id: "graph", label: "Knowledge Graph", icon: Network },
    { id: "entities", label: "Entities", icon: Layers },
    { id: "relationships", label: "Relationships", icon: Share2 },
    { id: "communities", label: "Communities", icon: Users },
  ];

  return (
    <div className="flex border-b border-border">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            "flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors relative",
            active === tab.id
              ? "text-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <tab.icon className="w-4 h-4" />
          {tab.label}
          {active === tab.id && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent" />
          )}
        </button>
      ))}
    </div>
  );
}

interface Entity {
  name: string;
  type: string;
  description: string;
  mention_count: number;
}

interface Relationship {
  source: string;
  target: string;
  type: string;
  description?: string;
}

interface Community {
  id: number;
  name?: string;
  summary?: string;
  entity_count: number;
  sample_entities?: string[];
}

interface EntitiesPanelProps {
  onExploreEntity: (entityName: string) => void;
  selectedEntities: string[];
}

function EntitiesPanel({ onExploreEntity, selectedEntities }: EntitiesPanelProps) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);

  useEffect(() => {
    const fetchEntities = async () => {
      try {
        const response = await api.getEntities(typeFilter || undefined, 100);
        setEntities(response.entities);
      } catch (error) {
        console.error("Failed to fetch entities:", error);
      } finally {
        setLoading(false);
      }
    };
    fetchEntities();
  }, [typeFilter]);

  const filteredEntities = useMemo(() => {
    if (!searchQuery) return entities;
    const query = searchQuery.toLowerCase();
    return entities.filter(
      (e) =>
        e.name.toLowerCase().includes(query) ||
        e.description.toLowerCase().includes(query)
    );
  }, [entities, searchQuery]);

  const uniqueTypes = useMemo(() => {
    return [...new Set(entities.map((e) => e.type))];
  }, [entities]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search entities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <Dropdown
          value={typeFilter || ""}
          onChange={(val) => setTypeFilter(val || null)}
          icon={Filter}
          options={[
            { value: "", label: "All Types" },
            ...uniqueTypes.map((type) => ({ value: type, label: type })),
          ]}
        />
      </div>

      <div className="grid gap-3">
        {filteredEntities.map((entity, idx) => {
          const isSelected = selectedEntities.includes(entity.name);
          return (
            <div
              key={idx}
              className={cn(
                "p-4 bg-card border rounded-lg transition-colors",
                isSelected ? "border-accent" : "border-border hover:border-accent/50"
              )}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-medium truncate">{entity.name}</h3>
                    <span className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                      {entity.type}
                    </span>
                    {isSelected && (
                      <span className="px-2 py-0.5 text-xs bg-accent/20 text-accent rounded-full flex items-center gap-1">
                        <Sparkles className="w-3 h-3" />
                        Selected
                      </span>
                    )}
                  </div>
                  {entity.description && (
                    <p className="text-sm text-muted-foreground line-clamp-2">
                      {entity.description}
                    </p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-2">
                  <div className="text-right">
                    <p className="text-sm font-medium">{entity.mention_count}</p>
                    <p className="text-xs text-muted-foreground">mentions</p>
                  </div>
                  <button
                    onClick={() => onExploreEntity(entity.name)}
                    className={cn(
                      "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
                      isSelected
                        ? "bg-accent text-accent-foreground"
                        : "bg-muted hover:bg-accent hover:text-accent-foreground"
                    )}
                  >
                    <Network className="w-4 h-4" />
                    {isSelected ? "View Graph" : "Explore"}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {filteredEntities.length === 0 && (
        <div className="text-center py-12 text-muted-foreground">
          No entities found matching your criteria.
        </div>
      )}
    </div>
  );
}

function RelationshipsPanel({ edges, onRefresh }: { edges: GraphEdge[]; onRefresh?: () => void }) {
  const [searchQuery, setSearchQuery] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [taskMessage, setTaskMessage] = useState<string | null>(null);

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

  const handleAnalyzeRelationships = async () => {
    try {
      setAnalyzing(true);
      setTaskMessage("Starting relationship analysis...");
      const result = await api.analyzeRelationships();
      const taskId = result.task_id;

      // Poll task status
      const poll = async () => {
        try {
          const status = await api.getTaskStatus(taskId);
          setTaskMessage(status.message || `Progress: ${status.progress}%`);
          if (status.status === "completed") {
            setTaskMessage(null);
            setAnalyzing(false);
            onRefresh?.();
          } else if (status.status === "failed") {
            setTaskMessage(`Failed: ${status.message}`);
            setAnalyzing(false);
          } else {
            setTimeout(poll, 3000);
          }
        } catch {
          setTaskMessage(null);
          setAnalyzing(false);
        }
      };
      setTimeout(poll, 2000);
    } catch (error) {
      console.error("Failed to analyze relationships:", error);
      setTaskMessage(null);
      setAnalyzing(false);
    }
  };

  return (
    <div className="p-6">
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
      </div>

      {taskMessage && (
        <div className="mb-4 p-3 bg-accent/10 border border-accent/20 rounded-lg text-sm text-accent">
          {taskMessage}
        </div>
      )}

      {edges.length === 0 && !analyzing ? (
        <div className="text-center py-12">
          <Share2 className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Relationships Found</h3>
          <p className="text-muted-foreground mb-4">
            Run relationship analysis to discover connections between entities across your documents.
          </p>
          <button
            onClick={handleAnalyzeRelationships}
            className="px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
          >
            Analyze Relationships
          </button>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
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

function CommunitiesPanel() {
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

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="p-6">
      {communities.length === 0 ? (
        <div className="text-center py-12">
          <Users className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Communities Detected</h3>
          <p className="text-muted-foreground mb-4">
            Run community detection to discover entity clusters in your knowledge graph.
          </p>
          <button
            onClick={async () => {
              try {
                setLoading(true);
                await api.detectCommunities(3);
                await fetchCommunities();
              } catch (error) {
                console.error("Failed to detect communities:", error);
              } finally {
                setLoading(false);
              }
            }}
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

export default function ExplorePage() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>("graph");
  const [nodeLimit, setNodeLimit] = useState(100);
  const [includeNeighbors, setIncludeNeighbors] = useState(true);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<EntitySearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showSearchResults, setShowSearchResults] = useState(false);
  const [selectedEntities, setSelectedEntities] = useState<string[]>([]);
  const [searchGraphData, setSearchGraphData] = useState<GraphData | null>(null);
  const [isLoadingSubgraph, setIsLoadingSubgraph] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchContainerRef = useRef<HTMLDivElement>(null);
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Close search results when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (searchContainerRef.current && !searchContainerRef.current.contains(event.target as Node)) {
        setShowSearchResults(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Debounced entity search
  useEffect(() => {
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    if (searchQuery.trim().length < 2) {
      setSearchResults([]);
      setShowSearchResults(false);
      return;
    }

    setIsSearching(true);
    searchTimeoutRef.current = setTimeout(async () => {
      try {
        const response = await api.searchEntities(searchQuery.trim());
        setSearchResults(response.results);
        setShowSearchResults(true);
      } catch (err) {
        console.error("Search failed:", err);
        setSearchResults([]);
      } finally {
        setIsSearching(false);
      }
    }, 300);

    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, [searchQuery]);

  // Fetch subgraph when entities are selected
  const fetchSubgraph = useCallback(async (entityNames: string[]) => {
    if (entityNames.length === 0) {
      setSearchGraphData(null);
      return;
    }

    setIsLoadingSubgraph(true);
    setError(null);
    try {
      const data = await api.getGraphSubgraph(entityNames, true);
      setSearchGraphData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load subgraph");
    } finally {
      setIsLoadingSubgraph(false);
    }
  }, []);

  // Handle selecting an entity from search results
  const handleSelectEntity = useCallback((entity: EntitySearchResult) => {
    setSelectedEntities((prev) => {
      // Toggle selection
      if (prev.includes(entity.name)) {
        const newSelection = prev.filter((n) => n !== entity.name);
        fetchSubgraph(newSelection);
        return newSelection;
      } else {
        const newSelection = [...prev, entity.name];
        fetchSubgraph(newSelection);
        return newSelection;
      }
    });
    setShowSearchResults(false);
    setSearchQuery("");
  }, [fetchSubgraph]);

  // Clear search and show full graph
  const clearSearch = useCallback(() => {
    setSelectedEntities([]);
    setSearchGraphData(null);
    setSearchQuery("");
    setSearchResults([]);
    setShowSearchResults(false);
  }, []);

  // Handle exploring an entity from the Entities panel
  const handleExploreEntity = useCallback((entityName: string) => {
    // Add to selected entities if not already there
    setSelectedEntities((prev) => {
      if (!prev.includes(entityName)) {
        const newSelection = [...prev, entityName];
        fetchSubgraph(newSelection);
        return newSelection;
      }
      // If already selected, just fetch the subgraph again
      fetchSubgraph(prev);
      return prev;
    });
    // Switch to the Knowledge Graph tab
    setActiveTab("graph");
  }, [fetchSubgraph]);

  const fetchGraphData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getGraphVisualization(nodeLimit, includeNeighbors);
      setGraphData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load graph data");
    } finally {
      setLoading(false);
    }
  }, [nodeLimit, includeNeighbors]);

  useEffect(() => {
    fetchGraphData();
  }, [fetchGraphData]);

  // Use search graph data when entities are selected, otherwise use full graph
  const activeGraphData = selectedEntities.length > 0 ? searchGraphData : graphData;

  const nodes: GraphNode[] = useMemo(() => {
    if (!activeGraphData?.nodes) return [];
    return activeGraphData.nodes;
  }, [activeGraphData]);

  const edges: GraphEdge[] = useMemo(() => {
    if (!activeGraphData?.edges) return [];
    return activeGraphData.edges;
  }, [activeGraphData]);

  const isGraphLoading = loading || isLoadingSubgraph;

  return (
    <div className="py-6">
      {/* Header Row */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-3xl font-bold">Explore Knowledge Graph</h1>
          <p className="text-muted-foreground mt-1">
            Visualize and explore entities, relationships, and communities
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={includeNeighbors}
              onChange={(e) => setIncludeNeighbors(e.target.checked)}
              className="w-4 h-4 rounded border-border bg-muted accent-accent"
            />
            Include neighbors
          </label>
          <Dropdown
            value={nodeLimit}
            onChange={setNodeLimit}
            icon={Network}
            options={[
              { value: 100, label: "100 nodes" },
              { value: 500, label: "500 nodes" },
              { value: 2000, label: "2,000 nodes" },
              { value: 5000, label: "5,000 nodes" },
              { value: 10000, label: "10,000 nodes" },
            ]}
          />
          <button
            onClick={() => {
              if (selectedEntities.length > 0) {
                fetchSubgraph(selectedEntities);
              } else {
                fetchGraphData();
              }
            }}
            disabled={isGraphLoading}
            className="flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn("w-4 h-4", isGraphLoading && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>

      {/* Search Row */}
      <div className="flex items-center gap-3 mb-6">
        {/* Entity Search */}
        <div className="relative" ref={searchContainerRef}>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              ref={searchInputRef}
              type="text"
              placeholder="Search entities to explore..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => searchResults.length > 0 && setShowSearchResults(true)}
              className={cn(
                "w-80 pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-sm",
                "focus:outline-none focus:ring-2 focus:ring-accent transition-all",
                isSearching && "pr-10"
              )}
            />
            {isSearching && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-muted-foreground" />
            )}
          </div>

          {/* Search Results Dropdown */}
          {showSearchResults && searchResults.length > 0 && (
            <div className="absolute top-full left-0 w-96 mt-1 bg-popover border border-border rounded-lg shadow-lg z-50 max-h-80 overflow-y-auto">
              <div className="p-2 border-b border-border">
                <p className="text-xs text-muted-foreground">
                  {searchResults.length} entities found - Click to view connections
                </p>
              </div>
              {searchResults.map((result, idx) => (
                <button
                  key={idx}
                  onClick={() => handleSelectEntity(result)}
                  className={cn(
                    "w-full flex items-start gap-3 p-3 hover:bg-muted transition-colors text-left",
                    selectedEntities.includes(result.name) && "bg-accent/10"
                  )}
                >
                  <div
                    className="w-2 h-2 rounded-full mt-1.5 flex-shrink-0"
                    style={{
                      backgroundColor:
                        result.type === "Person" ? "#F79767" :
                        result.type === "Organization" ? "#57C7E3" :
                        result.type === "Concept" ? "#DA7194" :
                        result.type === "Technology" ? "#6DCE9E" :
                        result.type === "Location" ? "#FFC454" :
                        "#4C8EDA"
                    }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium truncate">{result.name}</span>
                      <span className="px-1.5 py-0.5 text-xs bg-muted rounded text-muted-foreground">
                        {result.type}
                      </span>
                      {selectedEntities.includes(result.name) && (
                        <Check className="w-3 h-3 text-accent" />
                      )}
                    </div>
                    {result.description && (
                      <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                        {result.description}
                      </p>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {(result.score * 100).toFixed(0)}%
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Selected Entities Pills */}
        {selectedEntities.length > 0 && (
          <>
            <div className="h-6 w-px bg-border" />
            <div className="flex items-center gap-2 flex-wrap">
              {selectedEntities.map((name) => (
                <span
                  key={name}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-accent/20 text-accent text-sm rounded-full"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  <span className="truncate max-w-[150px]">{name}</span>
                  <button
                    onClick={() => {
                      const newSelection = selectedEntities.filter((n) => n !== name);
                      setSelectedEntities(newSelection);
                      fetchSubgraph(newSelection);
                    }}
                    className="hover:text-accent-foreground transition-colors ml-1"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </span>
              ))}
              <button
                onClick={clearSearch}
                className="text-sm text-muted-foreground hover:text-foreground transition-colors px-2 py-1 hover:bg-muted rounded-lg"
              >
                Clear all
              </button>
            </div>
          </>
        )}
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <TabNav active={activeTab} onChange={setActiveTab} />

        {isGraphLoading && activeTab === "graph" ? (
          <div className="flex items-center justify-center h-[600px]">
            <div className="text-center">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground mx-auto mb-4" />
              <p className="text-muted-foreground">
                {isLoadingSubgraph ? "Loading entity connections..." : "Loading knowledge graph..."}
              </p>
            </div>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-[600px]">
            <div className="text-center">
              <p className="text-red-400 mb-4">{error}</p>
              <button
                onClick={() => {
                  if (selectedEntities.length > 0) {
                    fetchSubgraph(selectedEntities);
                  } else {
                    fetchGraphData();
                  }
                }}
                className="px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
              >
                Try Again
              </button>
            </div>
          </div>
        ) : nodes.length === 0 && activeTab === "graph" ? (
          <div className="flex items-center justify-center h-[600px]">
            <div className="text-center">
              <Network className="w-16 h-16 mx-auto mb-4 text-muted-foreground" />
              <h3 className="text-xl font-medium mb-2">
                {selectedEntities.length > 0 ? "No Connections Found" : "No Entities Found"}
              </h3>
              <p className="text-muted-foreground max-w-md">
                {selectedEntities.length > 0 
                  ? "The selected entities have no connections in the knowledge graph."
                  : "Upload and process documents to extract entities and build your knowledge graph."}
              </p>
              {selectedEntities.length > 0 && (
                <button
                  onClick={clearSearch}
                  className="mt-4 px-4 py-2 bg-muted text-foreground rounded-lg text-sm font-medium hover:bg-muted/80 transition-colors"
                >
                  Show Full Graph
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            {activeTab === "graph" && (
              <div className="h-[600px] relative">
                {/* Search mode indicator */}
                {selectedEntities.length > 0 && (
                  <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 bg-accent/90 text-foreground px-4 py-2 rounded-full text-sm font-medium shadow-lg flex items-center gap-2">
                    <Sparkles className="w-4 h-4" />
                    Showing connections for {selectedEntities.length} {selectedEntities.length === 1 ? "entity" : "entities"}
                    <button
                      onClick={clearSearch}
                      className="ml-2 p-1 hover:bg-white/20 rounded-full transition-colors"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                )}
                <KnowledgeGraph nodes={nodes} edges={edges} stats={activeGraphData?.stats} />
              </div>
            )}
            {activeTab === "entities" && (
              <EntitiesPanel 
                onExploreEntity={handleExploreEntity}
                selectedEntities={selectedEntities}
              />
            )}
            {activeTab === "relationships" && <RelationshipsPanel edges={edges} onRefresh={fetchGraphData} />}
            {activeTab === "communities" && <CommunitiesPanel />}
          </>
        )}
      </div>
    </div>
  );
}
