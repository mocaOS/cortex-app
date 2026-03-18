"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { GraphEdge } from "@/types";
import { Loader2, Search, Filter, Network, Share2, ChevronDown, Check, X, ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const ITEMS_PER_PAGE = 50;

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
        <div className="absolute top-full right-0 mt-1 min-w-[180px] bg-popover border border-border rounded-lg shadow-lg z-50 py-1 max-h-64 overflow-y-auto">
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

function Pagination({
  currentPage,
  totalPages,
  totalItems,
  onPageChange,
}: {
  currentPage: number;
  totalPages: number;
  totalItems: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;

  const startItem = (currentPage - 1) * ITEMS_PER_PAGE + 1;
  const endItem = Math.min(currentPage * ITEMS_PER_PAGE, totalItems);

  return (
    <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
      <span className="text-sm text-muted-foreground">
        {startItem}-{endItem} of {totalItems}
      </span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage <= 1}
          className="p-1.5 rounded-lg hover:bg-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
          let page: number;
          if (totalPages <= 7) {
            page = i + 1;
          } else if (currentPage <= 4) {
            page = i + 1;
          } else if (currentPage >= totalPages - 3) {
            page = totalPages - 6 + i;
          } else {
            page = currentPage - 3 + i;
          }
          return (
            <button
              key={page}
              onClick={() => onPageChange(page)}
              className={cn(
                "w-8 h-8 rounded-lg text-sm transition-colors",
                currentPage === page ? "bg-accent text-accent-foreground" : "hover:bg-muted"
              )}
            >
              {page}
            </button>
          );
        })}
        <button
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage >= totalPages}
          className="p-1.5 rounded-lg hover:bg-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

export default function RelationshipsBrowser() {
  const router = useRouter();
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);

  const fetchRelationships = useCallback(async () => {
    try {
      const data = await api.getGraphVisualization(10000, false);
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

  // Reset to page 1 when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, typeFilter]);

  const uniqueTypes = useMemo(() => {
    return [...new Set(edges.map((e) => e.type))].sort();
  }, [edges]);

  const filteredEdges = useMemo(() => {
    let result = edges;
    if (typeFilter) {
      result = result.filter((e) => e.type === typeFilter);
    }
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (e) =>
          e.source.toLowerCase().includes(query) ||
          e.target.toLowerCase().includes(query) ||
          (e.description || "").toLowerCase().includes(query)
      );
    }
    return result;
  }, [edges, searchQuery, typeFilter]);

  const totalPages = Math.ceil(filteredEdges.length / ITEMS_PER_PAGE);
  const paginatedEdges = filteredEdges.slice(
    (currentPage - 1) * ITEMS_PER_PAGE,
    currentPage * ITEMS_PER_PAGE
  );

  const handleExploreEntity = (entityName: string) => {
    router.push(`/explore?tab=graph&entity=${encodeURIComponent(entityName)}`);
  };

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
        <Dropdown
          value={typeFilter}
          onChange={(val) => setTypeFilter(val)}
          icon={Filter}
          options={[
            { value: "", label: "All Types" },
            ...uniqueTypes.map((type) => ({ value: type, label: type })),
          ]}
        />
        <span className="text-sm text-muted-foreground whitespace-nowrap">
          {filteredEdges.length} relationship{filteredEdges.length !== 1 ? "s" : ""}
        </span>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <button onClick={() => setCurrentPage((p) => Math.max(1, p - 1))} disabled={currentPage <= 1} className="p-1.5 rounded-lg hover:bg-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs text-muted-foreground w-12 text-center">{currentPage}/{totalPages}</span>
            <button onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))} disabled={currentPage >= totalPages} className="p-1.5 rounded-lg hover:bg-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>

      <div className="grid gap-3">
        {paginatedEdges.map((edge, idx) => (
          <div
            key={idx}
            onClick={() => setSelectedEdge(edge)}
            className="p-4 bg-card border border-border rounded-lg hover:border-accent/50 transition-colors cursor-pointer"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="font-medium truncate">{edge.source}</span>
                  <ArrowRight className="w-3 h-3 text-muted-foreground flex-shrink-0" />
                  <span className="font-medium truncate">{edge.target}</span>
                  <span className="px-2 py-0.5 text-xs bg-accent/20 text-accent rounded-full font-medium">
                    {edge.type}
                  </span>
                </div>
                {edge.description && (
                  <p className="text-sm text-muted-foreground line-clamp-2">
                    {edge.description}
                  </p>
                )}
              </div>
              {edge.weight != null && (
                <div className="flex flex-col items-end gap-2 flex-shrink-0">
                  <div className="text-right">
                    <p className="text-sm font-medium">{edge.weight}</p>
                    <p className="text-xs text-muted-foreground">weight</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        totalItems={filteredEdges.length}
        onPageChange={setCurrentPage}
      />

      {filteredEdges.length === 0 && edges.length === 0 && (
        <div className="text-center py-12">
          <Share2 className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Relationships Yet</h3>
          <p className="text-muted-foreground">
            Use the Extract &amp; Analyze page to discover relationships between entities.
          </p>
        </div>
      )}
      {filteredEdges.length === 0 && edges.length > 0 && (
        <div className="text-center py-12 text-muted-foreground">
          No relationships found matching your criteria.
        </div>
      )}

      {/* Detail Modal */}
      {selectedEdge && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={() => setSelectedEdge(null)}>
          <div className="bg-card border border-border rounded-xl shadow-xl max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h3 className="font-semibold">Relationship Details</h3>
              <button onClick={() => setSelectedEdge(null)} className="p-1 hover:bg-muted rounded-lg transition-colors">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div className="flex items-center gap-2 flex-wrap">
                <button onClick={() => { setSelectedEdge(null); handleExploreEntity(selectedEdge.source); }} className="font-medium text-accent hover:underline">
                  {selectedEdge.source}
                </button>
                <ArrowRight className="w-4 h-4 text-muted-foreground" />
                <button onClick={() => { setSelectedEdge(null); handleExploreEntity(selectedEdge.target); }} className="font-medium text-accent hover:underline">
                  {selectedEdge.target}
                </button>
              </div>

              <div className="flex items-center gap-3">
                <span className="px-2 py-1 text-xs bg-accent/20 text-accent rounded-full font-medium">
                  {selectedEdge.type}
                </span>
                {selectedEdge.weight != null && (
                  <span className="text-sm text-muted-foreground">
                    Weight: {selectedEdge.weight}/10
                  </span>
                )}
              </div>

              {selectedEdge.description && (
                <div>
                  <h4 className="text-sm font-medium mb-1">Description</h4>
                  <p className="text-sm text-muted-foreground">{selectedEdge.description}</p>
                </div>
              )}

              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => { setSelectedEdge(null); handleExploreEntity(selectedEdge.source); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-muted hover:bg-accent hover:text-accent-foreground"
                >
                  <Network className="w-4 h-4" />
                  View {selectedEdge.source}
                </button>
                <button
                  onClick={() => { setSelectedEdge(null); handleExploreEntity(selectedEdge.target); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-muted hover:bg-accent hover:text-accent-foreground"
                >
                  <Network className="w-4 h-4" />
                  View {selectedEdge.target}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
