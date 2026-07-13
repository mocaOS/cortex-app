"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useModalDismiss } from "@/lib/hooks";
import type { DuplicateGroup, MergeEntitiesResponse, MergeHistoryEntry, Stats, EntityDetails } from "@/types";
import { Loader2, Search, Star, Merge, X, AlertTriangle, Check, ChevronDown, ChevronUp, History, Info, ArrowLeft, Plus, Eye, Network, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

// =============================================================================
// Inline Entity Search — add entities to a group
// =============================================================================

function EntitySearchInline({
  existingNames,
  onAdd,
}: {
  existingNames: Set<string>;
  onAdd: (entity: { name: string; type: string; description: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<{ name: string; type: string; description: string; score: number }>>([]);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  useEffect(() => {
    if (!query || query.length < 2) {
      setResults([]);
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await api.searchEntities(query);
        setResults(res.results.filter((r) => !existingNames.has(r.name)));
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, existingNames]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:bg-muted transition-colors ml-auto"
        title="Add entity to this group"
      >
        <Plus className="w-3.5 h-3.5" />
        Add
      </button>
    );
  }

  return (
    <div ref={containerRef} className="relative ml-auto w-80">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
        <input
          type="text"
          autoFocus
          placeholder="Search entities to add..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full pl-9 pr-8 py-1.5 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent"
        />
        <button
          onClick={() => { setOpen(false); setQuery(""); setResults([]); }}
          className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-muted-foreground hover:text-foreground"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {(results.length > 0 || searching) && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-popover border border-border rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto">
          {searching && results.length === 0 && (
            <div className="flex items-center justify-center py-3">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          )}
          {results.map((r) => (
            <button
              key={r.name}
              onClick={() => {
                onAdd({ name: r.name, type: r.type, description: r.description });
                setQuery("");
                setResults([]);
                setOpen(false);
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-muted transition-colors text-left"
            >
              <Plus className="w-3.5 h-3.5 text-accent flex-shrink-0" />
              <span className="font-medium truncate">{r.name}</span>
              <span className="px-1.5 py-0.5 text-xs bg-muted rounded-full text-muted-foreground flex-shrink-0">
                {r.type}
              </span>
            </button>
          ))}
          {!searching && query.length >= 2 && results.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-3">No matching entities found</p>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// History Modal — list + detail in one overlay
// =============================================================================

function HistoryModal({
  history,
  loading,
  selectedEntry,
  onSelectEntry,
  onClose,
}: {
  history: MergeHistoryEntry[];
  loading: boolean;
  selectedEntry: MergeHistoryEntry | null;
  onSelectEntry: (entry: MergeHistoryEntry | null) => void;
  onClose: () => void;
}) {
  const [searchQuery, setSearchQuery] = useState("");

  useBodyScrollLock(true);
  const dialogRef = useModalDismiss<HTMLDivElement>(onClose);

  const filtered = searchQuery
    ? history.filter((e) => {
        const q = searchQuery.toLowerCase();
        return (
          e.canonical_name.toLowerCase().includes(q) ||
          e.merged_names.some((n) => n.toLowerCase().includes(q)) ||
          e.entities_snapshot.some((s) => s.name.toLowerCase().includes(q))
        );
      })
    : history;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-card border border-border rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border flex-shrink-0">
          {selectedEntry ? (
            <div className="flex items-center gap-2">
              <button
                onClick={() => onSelectEntry(null)}
                className="p-1 hover:bg-muted rounded-lg transition-colors"
              >
                <ArrowLeft className="w-4 h-4" />
              </button>
              <Merge className="w-4 h-4 text-muted-foreground" />
              <h3 className="font-semibold">{selectedEntry.canonical_name}</h3>
              <span className="text-xs text-muted-foreground">
                +{selectedEntry.merged_count} merged
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <History className="w-4 h-4 text-muted-foreground" />
              <h3 className="font-semibold">Merge History</h3>
              <span className="text-xs text-muted-foreground">
                {history.length} merge{history.length !== 1 ? "s" : ""}
              </span>
            </div>
          )}
          <button onClick={onClose} className="p-1 hover:bg-muted rounded-lg transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1">
          {selectedEntry ? (
            /* Detail view */
            <div className="p-4 space-y-4">
              <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                <span>
                  {new Date(selectedEntry.merged_at).toLocaleDateString(undefined, {
                    month: "short", day: "numeric", year: "numeric",
                    hour: "2-digit", minute: "2-digit",
                  })}
                </span>
                {selectedEntry.relationships_retargeted > 0 && (
                  <span>{selectedEntry.relationships_retargeted} relationships retargeted</span>
                )}
                {selectedEntry.chunks_relinked > 0 && (
                  <span>{selectedEntry.chunks_relinked} chunks relinked</span>
                )}
              </div>

              <div className="space-y-1.5">
                {selectedEntry.entities_snapshot.map((entity) => (
                  <div
                    key={entity.name}
                    className={cn(
                      "flex items-center gap-3 p-3 rounded-lg",
                      entity.is_canonical ? "bg-amber-500/5 border border-amber-500/30" : "bg-muted/30"
                    )}
                  >
                    {entity.is_canonical && <Star className="w-3.5 h-3.5 text-amber-500 fill-amber-500 flex-shrink-0" />}
                    {!entity.is_canonical && <span className="w-3.5 text-muted-foreground/40 flex-shrink-0 text-center">&rarr;</span>}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={cn("font-medium truncate", entity.is_canonical && "text-amber-600 dark:text-amber-400")}>
                          {entity.name}
                        </span>
                        <span className="px-1.5 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                          {entity.type}
                        </span>
                      </div>
                      {entity.description && (
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{entity.description}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground flex-shrink-0">
                      <span>{entity.mention_count} mentions</span>
                      <span>{entity.relationship_count} rels</span>
                    </div>
                  </div>
                ))}
              </div>

              {selectedEntry.merged_description && (
                <div>
                  <h4 className="text-sm font-medium mb-1">Generated Description</h4>
                  <p className="text-sm text-muted-foreground">{selectedEntry.merged_description}</p>
                </div>
              )}
            </div>
          ) : (
            /* List view */
            <div className="p-4">
              {/* Search */}
              <div className="relative mb-3">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search merges..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent"
                />
              </div>

              {loading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                </div>
              ) : filtered.length === 0 ? (
                <p className="text-center text-sm text-muted-foreground py-8">
                  {searchQuery ? "No merges matching your search." : "No merge history yet."}
                </p>
              ) : (
                <div className="space-y-1">
                  {filtered.map((entry) => (
                    <div
                      key={entry.id}
                      onClick={() => onSelectEntry(entry)}
                      className="flex items-center justify-between p-3 rounded-lg hover:bg-muted/50 transition-colors cursor-pointer"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Merge className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                        <span className="font-medium truncate">{entry.canonical_name}</span>
                        <span className="text-xs text-muted-foreground flex-shrink-0">
                          +{entry.merged_count}
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-4">
                        {new Date(entry.merged_at).toLocaleDateString(undefined, {
                          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                        })}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

interface MergeResult {
  group_index: number;
  response: MergeEntitiesResponse;
}

export default function DeduplicationView() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const focusEntity = searchParams.get("entity");
  const [groups, setGroups] = useState<DuplicateGroup[]>([]);
  const [loading, setLoading] = useState(false);
  // 0..1 while a slow server-side scan reports progress, null otherwise
  const [scanProgress, setScanProgress] = useState<number | null>(null);
  const [scanned, setScanned] = useState(false);
  const [threshold, setThreshold] = useState(0.85);
  const [mergingKey, setMergingKey] = useState<string | null>(null);
  const [dismissedKeys, setDismissedKeys] = useState<Set<string>>(new Set());
  // Track the focused entity for filtering after scan
  const [focusEntityName, setFocusEntityName] = useState<string | null>(focusEntity);
  const autoScannedRef = useRef(false);

  // Hydrate dismissed keys from localStorage after mount
  useEffect(() => {
    const stored = localStorage.getItem("dedup_dismissed");
    if (stored) {
      try { setDismissedKeys(new Set(JSON.parse(stored))); } catch { /* ignore */ }
    }
  }, []);
  const [lastMergeResult, setLastMergeResult] = useState<MergeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // All per-group state keyed by stable group key (sorted entity names)
  const [canonicalOverrides, setCanonicalOverrides] = useState<Record<string, string>>({});
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [removedEntities, setRemovedEntities] = useState<Record<string, Set<string>>>({});
  // Track entities manually added by the user to groups
  const [addedEntities, setAddedEntities] = useState<Record<string, Array<{ name: string; type: string; description: string }>>>({});
  // Merge history
  const [history, setHistory] = useState<MergeHistoryEntry[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedHistoryEntry, setSelectedHistoryEntry] = useState<MergeHistoryEntry | null>(null);
  const [communitiesNeedRedetection, setCommunitiesNeedRedetection] = useState(false);
  // Track merges done in this session for the notice
  const [mergesDoneThisSession, setMergesDoneThisSession] = useState(0);

  // Entity detail inspection modal
  const [inspectEntity, setInspectEntity] = useState<string | null>(null);
  const [inspectDetails, setInspectDetails] = useState<EntityDetails | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);

  useBodyScrollLock(!!inspectEntity);

  const handleInspect = async (entityName: string) => {
    setInspectEntity(entityName);
    setInspectDetails(null);
    setInspectLoading(true);
    try {
      const details = await api.getEntityDetails(entityName, 1);
      setInspectDetails(details);
    } catch {
      setInspectDetails(null);
    } finally {
      setInspectLoading(false);
    }
  };

  const fetchHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const response = await api.getMergeHistory();
      setHistory(response.history);
    } catch {
      // silently fail — history is non-critical
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  // Load history on mount and check community staleness
  useEffect(() => {
    fetchHistory();
    api.getStats().then((stats: Stats) => {
      const lastMerge = stats.last_entity_merge_at;
      const lastDetection = stats.last_community_detection_at;
      const communityCount = stats.community_count ?? 0;
      if (communityCount > 0 && lastMerge) {
        const mergeDate = new Date(lastMerge).getTime();
        const detectionDate = lastDetection ? new Date(lastDetection).getTime() : 0;
        setCommunitiesNeedRedetection(mergeDate > detectionDate);
      }
    }).catch(() => {});
  }, [fetchHistory]);

  // Auto-scan when navigated with ?entity= param
  useEffect(() => {
    if (!focusEntity || autoScannedRef.current) return;
    autoScannedRef.current = true;

    const autoScan = async () => {
      setLoading(true);
      setError(null);
      try {
        // Use a lower threshold to find more potential matches
        const response = await api.suggestDuplicates(0.75, 100, {
          onProgress: setScanProgress,
        });
        const filtered = response.groups.filter((g) => !dismissedKeys.has(getGroupKey(g)));

        // Find groups containing the focused entity
        const matchingGroups = filtered.filter((g) =>
          g.entities.some((e) => e.name.toLowerCase() === focusEntity.toLowerCase())
        );

        if (matchingGroups.length > 0) {
          // Show only groups containing the focused entity, expanded
          setGroups(matchingGroups);
          setScanned(true);
          setExpandedGroups(new Set(matchingGroups.map(getGroupKey)));
        } else {
          // No duplicate groups found — create a standalone group with just
          // this entity so the user can manually add candidates via search.
          try {
            const searchRes = await api.searchEntities(focusEntity);
            const focusResult = searchRes.results.find(
              (r) => r.name.toLowerCase() === focusEntity.toLowerCase()
            );
            const focusInfo = focusResult
              ? { name: focusResult.name, type: focusResult.type, description: focusResult.description, mention_count: 0, relationship_count: 0 }
              : { name: focusEntity, type: "Unknown", description: "", mention_count: 0, relationship_count: 0 };

            const manualGroup: DuplicateGroup = {
              suggested_canonical: focusInfo.name,
              entities: [focusInfo],
              similarity: 0,
              method: "manual",
            };
            setGroups([manualGroup]);
            setScanned(true);
            const key = getGroupKey(manualGroup);
            setExpandedGroups(new Set([key]));
          } catch {
            setGroups([]);
            setScanned(true);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to scan for duplicates");
      } finally {
        setLoading(false);
        setScanProgress(null);
      }
    };

    autoScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusEntity]);

  // Clear focus when user manually rescans
  const clearFocus = () => {
    setFocusEntityName(null);
    if (focusEntity) {
      router.replace("/deduplicate", { scroll: false });
    }
  };

  const getGroupKey = (group: DuplicateGroup) => {
    return group.entities.map((e) => e.name).sort().join("|");
  };

  const handleScan = useCallback(async () => {
    clearFocus();
    setLoading(true);
    setError(null);
    setLastMergeResult(null);
    setRemovedEntities({});
    setCanonicalOverrides({});
    setAddedEntities({});
    try {
      const response = await api.suggestDuplicates(threshold, 100, {
        refresh: true,
        onProgress: setScanProgress,
      });
      // Filter out dismissed groups
      const filtered = response.groups.filter((g) => !dismissedKeys.has(getGroupKey(g)));
      setGroups(filtered);
      setScanned(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to scan for duplicates");
    } finally {
      setLoading(false);
      setScanProgress(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threshold, dismissedKeys]);

  const expandNextGroup = useCallback((removedKey: string, currentGroups: DuplicateGroup[]) => {
    const idx = currentGroups.findIndex((g) => getGroupKey(g) === removedKey);
    const remaining = currentGroups.filter((g) => getGroupKey(g) !== removedKey);
    if (remaining.length === 0) return;
    const nextIdx = Math.min(idx, remaining.length - 1);
    const nextKey = getGroupKey(remaining[nextIdx]);
    setExpandedGroups((prev) => {
      if (prev.has(nextKey)) return prev;
      const next = new Set(prev);
      next.delete(removedKey);
      next.add(nextKey);
      return next;
    });
  }, []);

  const handleMerge = useCallback(async (group: DuplicateGroup) => {
    const key = getGroupKey(group);
    const removed = removedEntities[key];
    const added = addedEntities[key] || [];
    const canonical = canonicalOverrides[key] || group.suggested_canonical;
    const allNames = [
      ...group.entities.map((e) => e.name),
      ...added.map((a) => a.name),
    ];
    const mergeNames = allNames.filter((name) => name !== canonical && !removed?.has(name));

    setMergingKey(key);
    setError(null);
    try {
      const response = await api.mergeEntities({ canonical, merge: mergeNames });
      setLastMergeResult({ group_index: 0, response });
      expandNextGroup(key, groups);
      setGroups((prev) => prev.filter((g) => getGroupKey(g) !== key));
      setCanonicalOverrides((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setRemovedEntities((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setAddedEntities((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      fetchHistory();
      setMergesDoneThisSession((prev) => prev + 1);
      setCommunitiesNeedRedetection(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Merge failed");
    } finally {
      setMergingKey(null);
    }
  }, [groups, canonicalOverrides, removedEntities, addedEntities, fetchHistory, expandNextGroup]);

  const handleDismiss = useCallback((group: DuplicateGroup) => {
    const key = getGroupKey(group);
    const newDismissed = new Set(dismissedKeys);
    newDismissed.add(key);
    setDismissedKeys(newDismissed);
    localStorage.setItem("dedup_dismissed", JSON.stringify([...newDismissed]));
    expandNextGroup(key, groups);
    setGroups((prev) => prev.filter((g) => getGroupKey(g) !== key));
  }, [dismissedKeys, groups, expandNextGroup]);


  const handleSetCanonical = (key: string, entityName: string) => {
    setCanonicalOverrides((prev) => ({ ...prev, [key]: entityName }));
  };

  const handleRemoveFromGroup = (group: DuplicateGroup, entityName: string) => {
    const key = getGroupKey(group);
    const added = addedEntities[key] || [];
    const currentRemoved = removedEntities[key] || new Set();

    // All entities (original + added), excluding already-removed and the one being removed now
    const allAfterRemoval = [
      ...group.entities.map((e) => e.name),
      ...added.map((a) => a.name),
    ].filter((name) => name !== entityName && !currentRemoved.has(name));

    // If removing the canonical, reassign to the first remaining entity
    const canonical = canonicalOverrides[key] || group.suggested_canonical;
    if (entityName === canonical && allAfterRemoval.length > 0) {
      setCanonicalOverrides((prev) => ({ ...prev, [key]: allAfterRemoval[0] }));
    }

    // Add to removed set
    setRemovedEntities((prev) => {
      const groupRemoved = new Set(prev[key] || []);
      groupRemoved.add(entityName);
      return { ...prev, [key]: groupRemoved };
    });

    // Drop the group only if fewer than 2 entities remain (nothing to merge)
    if (allAfterRemoval.length < 2) {
      setGroups((prev) => prev.filter((g) => getGroupKey(g) !== key));
      setRemovedEntities((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setAddedEntities((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  const toggleExpanded = (key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const clearDismissed = () => {
    setDismissedKeys(new Set());
    localStorage.removeItem("dedup_dismissed");
  };

  return (
    <div>
      {/* Header / Controls */}
      <div className="flex items-center gap-4 mb-6 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-sm text-muted-foreground whitespace-nowrap">Similarity:</label>
          <select
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="px-2 py-1.5 bg-muted border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value={0.75}>75% (Loose)</option>
            <option value={0.80}>80%</option>
            <option value={0.85}>85% (Default)</option>
            <option value={0.90}>90%</option>
            <option value={0.95}>95% (Strict)</option>
          </select>
        </div>

        <button
          onClick={handleScan}
          disabled={loading}
          className={cn(
            "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
            "bg-accent text-accent-foreground hover:bg-accent/90",
            loading && "opacity-70 cursor-not-allowed"
          )}
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Search className="w-4 h-4" />
          )}
          {loading
            ? scanProgress !== null
              ? `Scanning… ${Math.round(scanProgress * 100)}%`
              : "Scanning…"
            : scanned
              ? "Re-scan"
              : "Scan for Duplicates"}
        </button>

        {scanned && (
          <span className="text-sm text-muted-foreground">
            {groups.length} potential duplicate group{groups.length !== 1 ? "s" : ""} found
          </span>
        )}

        {dismissedKeys.size > 0 && (
          <button
            onClick={clearDismissed}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors underline"
          >
            Reset {dismissedKeys.size} dismissed
          </button>
        )}

        {history.length > 0 && (
          <button
            onClick={() => { setHistoryOpen(true); setSelectedHistoryEntry(null); }}
            className="flex items-center gap-1.5 ml-auto px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          >
            <History className="w-3.5 h-3.5" />
            History ({history.length})
          </button>
        )}
      </div>

      {/* Focus entity banner */}
      {focusEntityName && scanned && (
        <div className="flex items-center gap-2 p-3 mb-4 bg-accent/10 border border-accent/30 rounded-lg text-sm">
          <Search className="w-4 h-4 flex-shrink-0 text-accent" />
          <span>
            Showing deduplication candidates for <strong>{focusEntityName}</strong>
          </span>
          <button
            onClick={() => { clearFocus(); handleScan(); }}
            className="ml-auto text-xs text-muted-foreground hover:text-foreground underline transition-colors"
          >
            Show all duplicates
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-3 mb-4 bg-destructive/10 border border-destructive/30 rounded-lg text-sm text-destructive">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          {error}
          <button onClick={() => setError(null)} className="ml-auto p-0.5 hover:bg-destructive/20 rounded">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {/* Last merge result toast */}
      {lastMergeResult && (
        <div className="flex items-center gap-2 p-3 mb-4 bg-green-500/10 border border-green-500/30 rounded-lg text-sm text-green-600 dark:text-green-400">
          <Check className="w-4 h-4 flex-shrink-0" />
          Merged {lastMergeResult.response.merged.length} entit{lastMergeResult.response.merged.length !== 1 ? "ies" : "y"} into &quot;{lastMergeResult.response.canonical}&quot;
          {lastMergeResult.response.relationships_retargeted > 0 && (
            <> &mdash; {lastMergeResult.response.relationships_retargeted} relationships consolidated</>
          )}
          {lastMergeResult.response.chunks_relinked > 0 && (
            <>, {lastMergeResult.response.chunks_relinked} chunks relinked</>
          )}
          <button onClick={() => setLastMergeResult(null)} className="ml-auto p-0.5 hover:bg-green-500/20 rounded">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {/* Community re-detection notice */}
      {communitiesNeedRedetection && (
        <div className="flex items-center gap-2 p-3 mb-4 bg-yellow-500/10 border border-yellow-500/20 rounded-lg text-sm text-yellow-200">
          <Info className="w-4 h-4 flex-shrink-0 text-yellow-400" />
          <span>
            Entities have been merged since the last community detection.
            When you&apos;re done deduplicating, <Link href="/extract" className="underline hover:text-yellow-100">re-detect communities</Link> on the Generate Graph page to update groupings.
          </span>
        </div>
      )}

      {/* Duplicate groups */}
      {groups.length > 0 && (
        <div className="space-y-3">
          {groups.map((group) => {
            const key = getGroupKey(group);
            const removed = removedEntities[key];
            const added = addedEntities[key] || [];
            const allEntities = [
              ...group.entities,
              ...added.map((a) => ({ ...a, mention_count: 0, relationship_count: 0 })),
            ];
            const visibleEntities = allEntities.filter((e) => !removed?.has(e.name));
            const canonical = canonicalOverrides[key] || group.suggested_canonical;
            const isExpanded = expandedGroups.has(key);
            const isMerging = mergingKey === key;

            return (
              <div
                key={getGroupKey(group)}
                className={cn(
                  "border border-border rounded-lg bg-card transition-colors",
                  isMerging && "opacity-70"
                )}
              >
                <div
                  className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => toggleExpanded(key)}
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="flex items-center gap-2 min-w-0">
                      <Star className="w-4 h-4 text-amber-500 flex-shrink-0" />
                      <span className="font-medium truncate">{canonical}</span>
                      <span className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground flex-shrink-0">
                        {visibleEntities.length} entities
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground flex-shrink-0">
                      {Math.round(group.similarity * 100)}% similar
                    </span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 ml-4">
                    {isExpanded ? (
                      <ChevronUp className="w-4 h-4 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    )}
                  </div>
                </div>

                {isExpanded && (
                  <div className="px-4 pb-4 border-t border-border pt-3">
                    <p className="text-xs text-muted-foreground mb-3">
                      Click the star to change canonical. Click X to exclude an entity from this merge.
                    </p>
                    <div className="space-y-2">
                      {visibleEntities.map((entity) => {
                        const isCanonical = entity.name === canonical;
                        return (
                          <div
                            key={entity.name}
                            className={cn(
                              "flex items-center gap-3 p-3 rounded-lg border transition-colors cursor-pointer",
                              isCanonical
                                ? "border-amber-500/50 bg-amber-500/5"
                                : "border-border hover:border-muted-foreground/30"
                            )}
                            onClick={() => handleSetCanonical(key, entity.name)}
                          >
                            <div className={cn(
                              "flex-shrink-0",
                              isCanonical ? "text-amber-500" : "text-muted-foreground/30"
                            )}>
                              <Star className={cn("w-4 h-4", isCanonical && "fill-amber-500")} />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className={cn("font-medium truncate", isCanonical && "text-amber-600 dark:text-amber-400")}>
                                  {entity.name}
                                </span>
                                <span className="px-1.5 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                                  {entity.type}
                                </span>
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleInspect(entity.name); }}
                                  className="p-0.5 rounded text-muted-foreground/40 hover:text-foreground hover:bg-muted transition-colors"
                                  title="Inspect entity details"
                                >
                                  <Eye className="w-3.5 h-3.5" />
                                </button>
                              </div>
                              {entity.description && (
                                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                                  {entity.description}
                                </p>
                              )}
                            </div>
                            <div className="flex items-center gap-4 text-xs text-muted-foreground flex-shrink-0">
                              <span>{entity.relationship_count} rels</span>
                              <span>{entity.mention_count} mentions</span>
                            </div>
                            <button
                              onClick={(e) => { e.stopPropagation(); handleRemoveFromGroup(group, entity.name); }}
                              className="flex-shrink-0 p-2.5 -m-1 rounded-lg text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 transition-colors"
                              title="Remove from this group"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                      <button
                        onClick={() => handleMerge(group)}
                        disabled={mergingKey !== null}

                        className={cn(
                          "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
                          "bg-accent text-accent-foreground hover:bg-accent/90",
                          mergingKey !== null && "opacity-50 cursor-not-allowed"
                        )}
                      >
                        {isMerging ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Merge className="w-3.5 h-3.5" />
                        )}
                        {isMerging ? "Merging..." : "Merge"}
                      </button>
                      <button
                        onClick={() => handleDismiss(group)}
                        disabled={isMerging}
                        className={cn(
                          "px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:bg-muted transition-colors",
                          isMerging && "opacity-50 cursor-not-allowed"
                        )}
                      >
                        Dismiss
                      </button>
                      <EntitySearchInline
                        existingNames={new Set(visibleEntities.map((e) => e.name))}
                        onAdd={(entity) => {
                          setAddedEntities((prev) => ({
                            ...prev,
                            [key]: [...(prev[key] || []), entity],
                          }));
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Empty states */}
      {!scanned && !loading && (
        <div className="text-center py-16">
          <Merge className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <h3 className="text-lg font-medium mb-2">Entity Deduplication</h3>
          <p className="text-muted-foreground max-w-md mx-auto">
            Scan your knowledge graph for duplicate entities that fuzzy resolution missed.
            Review suggestions and merge duplicates to consolidate relationships and improve graph quality.
          </p>
        </div>
      )}

      {scanned && groups.length === 0 && !loading && (
        <div className="text-center py-16">
          <Check className="w-12 h-12 mx-auto mb-4 text-green-500" />
          <h3 className="text-lg font-medium mb-2">No Duplicates Found</h3>
          <p className="text-muted-foreground">
            No potential duplicate entities detected at {Math.round(threshold * 100)}% similarity threshold.
            {dismissedKeys.size > 0 && " (Some groups may be dismissed.)"}
          </p>
        </div>
      )}

      {/* History Modal */}
      {historyOpen && (
        <HistoryModal
          history={history}
          loading={historyLoading}
          selectedEntry={selectedHistoryEntry}
          onSelectEntry={setSelectedHistoryEntry}
          onClose={() => { setHistoryOpen(false); setSelectedHistoryEntry(null); }}
        />
      )}

      {/* Entity Inspect Modal */}
      {inspectEntity && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={() => setInspectEntity(null)}>
          <div className="bg-card border border-border rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h3 className="font-semibold truncate">{inspectEntity}</h3>
              <button onClick={() => setInspectEntity(null)} className="p-1 hover:bg-muted rounded-lg transition-colors ml-2">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 space-y-4">
              {inspectLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
              ) : inspectDetails ? (
                <>
                  {inspectDetails.type && (
                    <span className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground">
                      {inspectDetails.type}
                    </span>
                  )}

                  {inspectDetails.description && (
                    <div>
                      <h4 className="text-sm font-medium mb-1">Description</h4>
                      <p className="text-sm text-muted-foreground">{inspectDetails.description}</p>
                    </div>
                  )}

                  {inspectDetails.relationships.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Relationships ({inspectDetails.relationships.length})</h4>
                      <div className="space-y-1.5 max-h-48 overflow-y-auto">
                        {inspectDetails.relationships.map((rel, idx) => (
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

                  {inspectDetails.entities.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Related Entities ({inspectDetails.entities.length})</h4>
                      <div className="flex flex-wrap gap-1">
                        {inspectDetails.entities.map((e, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleInspect(e.name)}
                            className="px-2 py-0.5 text-xs bg-muted rounded-full text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
                          >
                            {e.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {inspectDetails.chunks.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium mb-2">Mentioned In ({inspectDetails.chunks.length} chunks)</h4>
                      <div className="space-y-2 max-h-40 overflow-y-auto">
                        {inspectDetails.chunks.slice(0, 5).map((chunk, idx) => (
                          <div key={idx} className="p-2 bg-muted/50 rounded-lg">
                            <p className="text-xs text-muted-foreground mb-1">{chunk.filename}</p>
                            <p className="text-sm line-clamp-2">{chunk.content}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-4">Failed to load entity details.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
