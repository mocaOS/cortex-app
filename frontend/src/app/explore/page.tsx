"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { api } from "@/lib/api";
import type { GraphData, GraphNode, GraphEdge } from "@/types";
import { KnowledgeGraph } from "@/components/explore";
import { Loader2, RefreshCw, Search, Filter, Share2, Users, Network, Layers, ChevronDown, Check } from "lucide-react";
import { cn } from "@/lib/utils";

type TabType = "graph" | "entities" | "relationships" | "communities";

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

function EntitiesPanel() {
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
        {filteredEntities.map((entity, idx) => (
          <div
            key={idx}
            className="p-4 bg-card border border-border rounded-lg hover:border-accent/50 transition-colors"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-medium truncate">{entity.name}</h3>
                  <span className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                    {entity.type}
                  </span>
                </div>
                {entity.description && (
                  <p className="text-sm text-muted-foreground line-clamp-2">
                    {entity.description}
                  </p>
                )}
              </div>
              <div className="text-right">
                <p className="text-sm font-medium">{entity.mention_count}</p>
                <p className="text-xs text-muted-foreground">mentions</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {filteredEntities.length === 0 && (
        <div className="text-center py-12 text-muted-foreground">
          No entities found matching your criteria.
        </div>
      )}
    </div>
  );
}

function RelationshipsPanel({ edges }: { edges: GraphEdge[] }) {
  const [searchQuery, setSearchQuery] = useState("");

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
      </div>

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
          No relationships found.
        </div>
      )}
    </div>
  );
}

function CommunitiesPanel() {
  const [communities, setCommunities] = useState<Community[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchCommunities = async () => {
      try {
        const response = await api.getCommunities(50);
        setCommunities(response.communities);
      } catch (error) {
        console.error("Failed to fetch communities:", error);
      } finally {
        setLoading(false);
      }
    };
    fetchCommunities();
  }, []);

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
                // Refetch after triggering detection
                const response = await api.getCommunities(50);
                setCommunities(response.communities);
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

  const nodes: GraphNode[] = useMemo(() => {
    if (!graphData?.nodes) return [];
    return graphData.nodes;
  }, [graphData]);

  const edges: GraphEdge[] = useMemo(() => {
    if (!graphData?.edges) return [];
    return graphData.edges;
  }, [graphData]);

  return (
    <div className="py-6">
      <div className="flex items-center justify-between mb-6">
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
            onClick={fetchGraphData}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn("w-4 h-4", loading && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <TabNav active={activeTab} onChange={setActiveTab} />

        {loading && activeTab === "graph" ? (
          <div className="flex items-center justify-center h-[600px]">
            <div className="text-center">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground mx-auto mb-4" />
              <p className="text-muted-foreground">Loading knowledge graph...</p>
            </div>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-[600px]">
            <div className="text-center">
              <p className="text-red-400 mb-4">{error}</p>
              <button
                onClick={fetchGraphData}
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
              <h3 className="text-xl font-medium mb-2">No Entities Found</h3>
              <p className="text-muted-foreground max-w-md">
                Upload and process documents to extract entities and build your knowledge graph.
              </p>
            </div>
          </div>
        ) : (
          <>
            {activeTab === "graph" && (
              <div className="h-[600px]">
                <KnowledgeGraph nodes={nodes} edges={edges} stats={graphData?.stats} />
              </div>
            )}
            {activeTab === "entities" && <EntitiesPanel />}
            {activeTab === "relationships" && <RelationshipsPanel edges={edges} />}
            {activeTab === "communities" && <CommunitiesPanel />}
          </>
        )}
      </div>
    </div>
  );
}
