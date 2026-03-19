"use client";

import { useState, useEffect, useCallback, useMemo, useRef, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { GraphData, GraphNode, GraphEdge } from "@/types";
import { KnowledgeGraph, EntitiesBrowser, RelationshipsBrowser, CommunitiesBrowser } from "@/components/explore";
import AskPanel from "@/components/AskPanel";
import { Loader2, RefreshCw, Search, Network, ChevronDown, Check, X, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

type TabType = "graph" | "entities" | "relationships" | "communities" | "research" | "chat";

const validTabs: TabType[] = ["graph", "entities", "relationships", "communities", "research", "chat"];

// Search result from entity search
interface EntitySearchResult {
  name: string;
  type: string;
  description: string;
  score: number;
  connection_count?: number;
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

// Helper to parse and validate tab from URL
function getTabFromUrl(param: string | null): TabType {
  if (param && validTabs.includes(param as TabType)) {
    return param as TabType;
  }
  return "graph";
}



function ExplorePageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nodeLimit, setNodeLimit] = useState(2000);
  const [includeNeighbors] = useState(true);
  const [hideDisconnected, setHideDisconnected] = useState(true);
  
  // Get active tab and optional entity focus from URL params
  const activeTab = getTabFromUrl(searchParams.get("tab"));
  const initialEntity = searchParams.get("entity");
  
  // Update URL when tab changes
  const setActiveTab = useCallback((tab: TabType) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", tab);
    router.push(`/explore?${params.toString()}`, { scroll: false });
  }, [searchParams, router]);

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

  const edges: GraphEdge[] = useMemo(() => {
    if (!activeGraphData?.edges) return [];
    return activeGraphData.edges;
  }, [activeGraphData]);

  const nodes: GraphNode[] = useMemo(() => {
    if (!activeGraphData?.nodes) return [];
    if (!hideDisconnected) return activeGraphData.nodes;
    const connectedIds = new Set<string>();
    for (const edge of edges) {
      connectedIds.add(edge.source);
      connectedIds.add(edge.target);
    }
    return activeGraphData.nodes.filter((n) => connectedIds.has(n.id));
  }, [activeGraphData, hideDisconnected, edges]);

  const isGraphLoading = loading || isLoadingSubgraph;

  // Render non-graph tabs
  if (activeTab === "research" || activeTab === "chat") {
    return (
      <div className="py-6">
        <AskPanel initialMode={activeTab} />
      </div>
    );
  }

  if (activeTab === "entities") {
    return (
      <div className="py-6">
        <EntitiesBrowser />
      </div>
    );
  }

  if (activeTab === "relationships") {
    return (
      <div className="py-6">
        <RelationshipsBrowser />
      </div>
    );
  }

  if (activeTab === "communities") {
    return (
      <div className="py-6">
        <CommunitiesBrowser />
      </div>
    );
  }

  // Render Knowledge Graph tab with all its controls
  return (
    <div className="py-6">
      {/* Controls Row - Only for Knowledge Graph */}
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
                    {result.connection_count ?? 0} links
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

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
          className="flex items-center gap-2 px-3 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn("w-4 h-4", isGraphLoading && "animate-spin")} />
        </button>

        <label className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideDisconnected}
            onChange={(e) => setHideDisconnected(e.target.checked)}
            className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
          />
          Hide disconnected entities
        </label>

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
        {isGraphLoading ? (
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
        ) : nodes.length === 0 ? (
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
            <KnowledgeGraph nodes={nodes} edges={edges} stats={activeGraphData?.stats} initialEntity={initialEntity} />
          </div>
        )}
      </div>
    </div>
  );
}

// Loading fallback for Suspense
function ExplorePageLoading() {
  return (
    <div className="py-6">
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    </div>
  );
}

export default function ExplorePage() {
  return (
    <Suspense fallback={<ExplorePageLoading />}>
      <ExplorePageContent />
    </Suspense>
  );
}
