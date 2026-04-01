"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { Loader2, Search, Filter, Network, ChevronDown, Check, X, ArrowRight, ChevronLeft, ChevronRight, Pencil, Merge } from "lucide-react";
import { cn } from "@/lib/utils";
import type { EntityDetails } from "@/types";

interface Entity {
  name: string;
  type: string;
  description: string;
  mention_count: number;
}

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
        <div className="absolute top-full right-0 mt-1 min-w-[140px] bg-popover border border-border rounded-lg shadow-lg z-50 py-1 max-h-64 overflow-y-auto">
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
  compact = false,
}: {
  currentPage: number;
  totalPages: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  compact?: boolean;
}) {
  if (totalPages <= 1) return null;

  const startItem = (currentPage - 1) * ITEMS_PER_PAGE + 1;
  const endItem = Math.min(currentPage * ITEMS_PER_PAGE, totalItems);

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

export default function EntitiesBrowser() {
  const router = useRouter();
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [entityTypes, setEntityTypes] = useState<string[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [entityDetails, setEntityDetails] = useState<EntityDetails | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [editingDescription, setEditingDescription] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useBodyScrollLock(!!selectedEntity);

  const handleOpenDetail = async (entity: Entity) => {
    setSelectedEntity(entity);
    setEntityDetails(null);
    setDetailLoading(true);
    setEditingName(false);
    setEditingDescription(false);
    setSaveError(null);
    try {
      const details = await api.getEntityDetails(entity.name, 1);
      setEntityDetails(details);
    } catch {
      setEntityDetails(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const handleCloseDetail = () => {
    setSelectedEntity(null);
    setEditingName(false);
    setEditingDescription(false);
    setSaveError(null);
  };

  const handleSaveEntity = async (updates: { name?: string; description?: string }) => {
    if (!selectedEntity) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await api.updateEntity(selectedEntity.name, updates);
      // Update the selected entity with new values
      const updated = { ...selectedEntity, name: result.name, description: result.description };
      setSelectedEntity(updated);
      // Update the entity in the list
      setEntities(prev => prev.map(e =>
        e.name === selectedEntity.name ? { ...e, name: result.name, description: result.description } : e
      ));
      setEditingName(false);
      setEditingDescription(false);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to save";
      setSaveError(message);
    } finally {
      setSaving(false);
    }
  };

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [debouncedSearch, typeFilter]);

  // Fetch entity types once
  useEffect(() => {
    api.getEntityTypes().then(res => setEntityTypes(res.types)).catch(() => {});
  }, []);

  // Fetch entities from server
  useEffect(() => {
    const fetchEntities = async () => {
      setFetching(true);
      try {
        const response = await api.getEntitiesPaginated({
          skip: (currentPage - 1) * ITEMS_PER_PAGE,
          limit: ITEMS_PER_PAGE,
          search: debouncedSearch || undefined,
          entityType: typeFilter || undefined,
        });
        setEntities(response.entities);
        setTotalItems(response.total);
      } catch (error) {
        console.error("Failed to fetch entities:", error);
      } finally {
        setLoading(false);
        setFetching(false);
      }
    };
    fetchEntities();
  }, [currentPage, debouncedSearch, typeFilter]);

  const totalPages = Math.ceil(totalItems / ITEMS_PER_PAGE);

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
            ...entityTypes.map((type) => ({ value: type, label: type })),
          ]}
        />
        <span className="text-sm text-muted-foreground whitespace-nowrap">
          {totalItems} entit{totalItems !== 1 ? "ies" : "y"}
        </span>
        <Pagination
          currentPage={currentPage}
          totalPages={totalPages}
          totalItems={totalItems}
          onPageChange={setCurrentPage}
          compact
        />
      </div>

      <div className={cn("grid gap-3 transition-opacity", fetching && "opacity-60")}>
        {entities.map((entity, idx) => (
          <div
            key={idx}
            onClick={() => handleOpenDetail(entity)}
            className="p-4 bg-card border border-border rounded-lg hover:border-accent/50 transition-colors cursor-pointer"
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
              <div className="flex flex-col items-end gap-2">
                <div className="text-right">
                  <p className="text-sm font-medium">{entity.mention_count}</p>
                  <p className="text-xs text-muted-foreground">mentions</p>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={(e) => { e.stopPropagation(); router.push(`/deduplicate?entity=${encodeURIComponent(entity.name)}`); }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-muted hover:bg-accent hover:text-accent-foreground"
                    title="Find duplicates for this entity"
                  >
                    <Merge className="w-4 h-4" />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleExploreEntity(entity.name); }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-muted hover:bg-accent hover:text-accent-foreground"
                  >
                    <Network className="w-4 h-4" />
                    Graph
                  </button>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        totalItems={totalItems}
        onPageChange={setCurrentPage}
      />

      {entities.length === 0 && !loading && !debouncedSearch && !typeFilter && (
        <div className="text-center py-12">
          <Network className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">No Entities Yet</h3>
          <p className="text-muted-foreground">
            Entities are extracted when documents are processed. Use the Extract &amp; Analyze page to get started.
          </p>
        </div>
      )}
      {entities.length === 0 && !loading && (debouncedSearch || typeFilter) && (
        <div className="text-center py-12 text-muted-foreground">
          No entities found matching your criteria.
        </div>
      )}

      {/* Detail Modal */}
      {selectedEntity && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={handleCloseDetail}>
          <div className="bg-card border border-border rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                {editingName ? (
                  <form
                    className="flex items-center gap-2 flex-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const trimmed = editName.trim();
                      if (trimmed && trimmed !== selectedEntity.name) {
                        handleSaveEntity({ name: trimmed });
                      } else {
                        setEditingName(false);
                      }
                    }}
                  >
                    <input
                      autoFocus
                      type="text"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="flex-1 px-2 py-1 bg-muted border border-border rounded-lg text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-accent"
                      disabled={saving}
                    />
                    <button type="submit" disabled={saving} className="p-1 hover:bg-muted rounded-lg transition-colors text-accent">
                      <Check className="w-4 h-4" />
                    </button>
                    <button type="button" onClick={() => setEditingName(false)} className="p-1 hover:bg-muted rounded-lg transition-colors">
                      <X className="w-4 h-4" />
                    </button>
                  </form>
                ) : (
                  <>
                    <h3 className="font-semibold truncate">{selectedEntity.name}</h3>
                    <button
                      onClick={() => { setEditName(selectedEntity.name); setEditingName(true); setEditingDescription(false); }}
                      className="p-1 hover:bg-muted rounded-lg transition-colors text-muted-foreground hover:text-foreground flex-shrink-0"
                      title="Edit name"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
                {!editingName && (
                  <span className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground flex-shrink-0">
                    {selectedEntity.type}
                  </span>
                )}
              </div>
              <button onClick={handleCloseDetail} className="p-1 hover:bg-muted rounded-lg transition-colors ml-2">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 space-y-4">
              {saveError && (
                <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                  {saveError}
                </div>
              )}

              <div>
                <div className="flex items-center gap-2 mb-1">
                  <h4 className="text-sm font-medium">Description</h4>
                  {!editingDescription && (
                    <button
                      onClick={() => { setEditDescription(selectedEntity.description || ""); setEditingDescription(true); setEditingName(false); }}
                      className="p-0.5 hover:bg-muted rounded transition-colors text-muted-foreground hover:text-foreground"
                      title="Edit description"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                  )}
                </div>
                {editingDescription ? (
                  <div className="space-y-2">
                    <textarea
                      autoFocus
                      value={editDescription}
                      onChange={(e) => setEditDescription(e.target.value)}
                      rows={3}
                      className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent resize-y"
                      disabled={saving}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          if (editDescription !== (selectedEntity.description || "")) {
                            handleSaveEntity({ description: editDescription });
                          } else {
                            setEditingDescription(false);
                          }
                        }}
                        disabled={saving}
                        className="px-3 py-1 text-sm rounded-lg bg-accent text-accent-foreground hover:bg-accent/90 transition-colors disabled:opacity-50"
                      >
                        {saving ? "Saving..." : "Save"}
                      </button>
                      <button
                        onClick={() => setEditingDescription(false)}
                        className="px-3 py-1 text-sm rounded-lg bg-muted hover:bg-muted/80 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {selectedEntity.description || <span className="italic">No description</span>}
                  </p>
                )}
              </div>

              <div className="text-sm text-muted-foreground">
                {selectedEntity.mention_count} mentions across documents
              </div>

              {detailLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
              ) : entityDetails ? (
                <>
                  {entityDetails.relationships.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Relationships ({entityDetails.relationships.length})</h4>
                      <div className="space-y-1.5 max-h-48 overflow-y-auto">
                        {entityDetails.relationships.map((rel, idx) => (
                          <div key={idx} className="text-sm text-muted-foreground flex items-center gap-1.5 flex-wrap">
                            <span className="text-foreground">{rel.source}</span>
                            <span className="px-1.5 py-0.5 text-xs bg-accent/20 text-accent rounded">{rel.type}</span>
                            <ArrowRight className="w-3 h-3 flex-shrink-0" />
                            <span className="text-foreground">{rel.target}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {entityDetails.entities.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Related Entities ({entityDetails.entities.length})</h4>
                      <div className="flex flex-wrap gap-1">
                        {entityDetails.entities.map((e, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleOpenDetail({ name: e.name, type: e.type, description: e.description, mention_count: 0 })}
                            className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
                          >
                            {e.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {entityDetails.chunks.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Mentioned In ({entityDetails.chunks.length} chunks)</h4>
                      <div className="space-y-2 max-h-40 overflow-y-auto">
                        {entityDetails.chunks.slice(0, 5).map((chunk, idx) => (
                          <div key={idx} className="p-2 bg-muted/50 rounded-lg">
                            <p className="text-xs text-muted-foreground mb-1">{chunk.filename}</p>
                            <p className="text-sm line-clamp-2">{chunk.content}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : null}

              <div className="flex gap-2 pt-2 border-t border-border">
                <button
                  onClick={() => { handleCloseDetail(); handleExploreEntity(selectedEntity.name); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-muted hover:bg-accent hover:text-accent-foreground"
                >
                  <Network className="w-4 h-4" />
                  View in Graph
                </button>
                <button
                  onClick={() => { handleCloseDetail(); router.push(`/deduplicate?entity=${encodeURIComponent(selectedEntity.name)}`); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-muted hover:bg-accent hover:text-accent-foreground"
                >
                  <Merge className="w-4 h-4" />
                  Deduplicate
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
