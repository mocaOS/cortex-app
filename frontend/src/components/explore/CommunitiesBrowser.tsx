"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useModalDismiss } from "@/lib/hooks";
import type { Community } from "@/types";
import { Loader2, Search, Users, Network, X, ChevronLeft, ChevronRight, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

const ITEMS_PER_PAGE = 25;

function Pagination({
  currentPage,
  totalPages,
  totalItems,
  itemsPerPage,
  onPageChange,
  compact = false,
}: {
  currentPage: number;
  totalPages: number;
  totalItems: number;
  itemsPerPage: number;
  onPageChange: (page: number) => void;
  compact?: boolean;
}) {
  if (totalPages <= 1) return null;

  const startItem = (currentPage - 1) * itemsPerPage + 1;
  const endItem = Math.min(currentPage * itemsPerPage, totalItems);

  return (
    <div className={cn(
      "flex items-center",
      compact ? "ml-auto" : "justify-between mt-4"
    )}>
      {!compact && (
        <span className="text-sm text-muted-foreground">
          {startItem}-{endItem} of {totalItems}
        </span>
      )}
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

// Clean up summary text — strip JSON artifacts and chain-of-thought
function cleanSummary(summary: string | undefined): string {
  if (!summary) return "";
  let text = summary;
  if (text.startsWith("{")) {
    try {
      const parsed = JSON.parse(text);
      if (parsed.summary) return parsed.summary;
    } catch {
      const nameMatch = text.match(/"summary"\s*:\s*"([^"]+)"/);
      if (nameMatch) return nameMatch[1];
    }
  }
  for (const marker of ["Looking at", "The entities", "This cluster", "These entities", "Key entities"]) {
    const idx = text.indexOf(marker);
    if (idx > 0 && idx < 100) {
      text = text.substring(idx);
      break;
    }
  }
  return text;
}

export default function CommunitiesBrowser() {
  const router = useRouter();
  const [communities, setCommunities] = useState<Community[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [selectedCommunity, setSelectedCommunity] = useState<Community | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailReqId = useRef(0);

  useBodyScrollLock(!!selectedCommunity);

  const handleCloseDetail = () => {
    setSelectedCommunity(null);
    setDetailError(null);
  };

  const dialogRef = useModalDismiss<HTMLDivElement>(handleCloseDetail);

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Reset to page 1 when search changes
  useEffect(() => {
    setCurrentPage(1);
  }, [debouncedSearch]);

  // Fetch communities from server
  useEffect(() => {
    const fetchCommunities = async () => {
      setFetching(true);
      try {
        const response = await api.getCommunitiesPaginated({
          skip: (currentPage - 1) * ITEMS_PER_PAGE,
          limit: ITEMS_PER_PAGE,
          search: debouncedSearch || undefined,
        });
        setCommunities(response.communities);
        setTotalItems(response.total);
      } catch (error) {
        console.error("Failed to fetch communities:", error);
      } finally {
        setLoading(false);
        setFetching(false);
      }
    };
    fetchCommunities();
  }, [currentPage, debouncedSearch]);

  const totalPages = Math.ceil(totalItems / ITEMS_PER_PAGE);

  const handleOpenDetail = async (community: Community) => {
    setSelectedCommunity(community);
    setDetailError(null);
    if (!community.entities || community.entities.length === 0) {
      const reqId = ++detailReqId.current;
      setDetailLoading(true);
      try {
        const detail = await api.getCommunity(community.id);
        if (reqId !== detailReqId.current) return;
        setSelectedCommunity(detail);
      } catch (error) {
        console.error("Failed to fetch community details:", error);
        if (reqId !== detailReqId.current) return;
        setDetailError(error instanceof Error ? error.message : "Failed to load community details");
      } finally {
        if (reqId === detailReqId.current) {
          setDetailLoading(false);
        }
      }
    }
  };

  const handleExploreEntity = (entityName: string) => {
    setSelectedCommunity(null);
    setDetailError(null);
    router.push(`/explore?tab=graph&entity=${encodeURIComponent(entityName)}`);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!loading && totalItems === 0 && !debouncedSearch) {
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
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search communities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <span className="text-sm text-muted-foreground whitespace-nowrap">
          {totalItems} communit{totalItems !== 1 ? "ies" : "y"}
        </span>
        <Pagination
          currentPage={currentPage}
          totalPages={totalPages}
          totalItems={totalItems}
          itemsPerPage={ITEMS_PER_PAGE}
          onPageChange={setCurrentPage}
          compact
        />
      </div>

      <div className={cn("grid gap-3 transition-opacity", fetching && "opacity-60")}>
        {communities.map((community) => (
          <div
            key={community.id}
            onClick={() => handleOpenDetail(community)}
            className="p-4 bg-card border border-border rounded-lg hover:border-accent/50 transition-colors cursor-pointer"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-medium truncate">
                    {community.name || `Community ${community.id}`}
                  </h3>
                  <span className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                    {community.entity_count} entities
                  </span>
                </div>
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {cleanSummary(community.summary)}
                </p>
              </div>
              {community.sample_entities && community.sample_entities.length > 0 && (
                <div className="flex flex-wrap gap-1 max-w-[200px] justify-end flex-shrink-0">
                  {community.sample_entities.slice(0, 3).map((entity, idx) => (
                    <span
                      key={idx}
                      className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground truncate max-w-[120px]"
                    >
                      {entity}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        totalItems={totalItems}
        itemsPerPage={ITEMS_PER_PAGE}
        onPageChange={setCurrentPage}
      />

      {communities.length === 0 && !loading && debouncedSearch && (
        <div className="text-center py-12 text-muted-foreground">
          No communities found matching your search.
        </div>
      )}

      {/* Detail Modal */}
      {selectedCommunity && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={handleCloseDetail}>
          <div
            ref={dialogRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            className="bg-card border border-border rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto focus:outline-none"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div>
                <h3 className="font-semibold">{selectedCommunity.name || `Community ${selectedCommunity.id}`}</h3>
                <p className="text-xs text-muted-foreground">{selectedCommunity.entity_count} entities</p>
              </div>
              <button onClick={handleCloseDetail} className="p-1 hover:bg-muted rounded-lg transition-colors">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 space-y-4">
              {selectedCommunity.summary && (
                <div>
                  <h4 className="text-sm font-medium mb-1">Summary</h4>
                  <p className="text-sm text-muted-foreground">{cleanSummary(selectedCommunity.summary)}</p>
                </div>
              )}

              {detailLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
              ) : detailError ? (
                <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
                  <AlertCircle className="w-6 h-6 text-red-400" />
                  <p className="text-sm text-muted-foreground">{detailError}</p>
                  <button
                    onClick={() => handleOpenDetail(selectedCommunity)}
                    className="px-3 py-1.5 text-sm rounded-lg bg-muted hover:bg-accent hover:text-accent-foreground transition-colors"
                  >
                    Retry
                  </button>
                </div>
              ) : (
                <>
                  {selectedCommunity.entities && selectedCommunity.entities.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Members ({selectedCommunity.entities.length})</h4>
                      <div className="space-y-2 max-h-60 overflow-y-auto">
                        {selectedCommunity.entities.map((entity, idx) => (
                          <div
                            key={idx}
                            className="flex items-center justify-between p-2 bg-muted/50 rounded-lg hover:bg-muted transition-colors"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="font-medium text-sm truncate">{entity.name}</span>
                              <span className="px-1.5 py-0.5 text-xs bg-background rounded text-muted-foreground flex-shrink-0">
                                {entity.type}
                              </span>
                            </div>
                            <button
                              onClick={() => handleExploreEntity(entity.name)}
                              className="p-1 hover:bg-accent hover:text-accent-foreground rounded transition-colors flex-shrink-0"
                            >
                              <Network className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {(!selectedCommunity.entities || selectedCommunity.entities.length === 0) && selectedCommunity.sample_entities && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Sample Entities</h4>
                      <div className="flex flex-wrap gap-1">
                        {selectedCommunity.sample_entities.map((entity, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleExploreEntity(entity)}
                            className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
                          >
                            {entity}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
