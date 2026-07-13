"use client";

import { useState, useRef, useEffect } from "react";
import { Filter, ChevronDown, Check, Clock, CheckCircle2, Loader2, XCircle, AlertTriangle, ShieldAlert, FolderOpen, X, Globe } from "lucide-react";
import type { Collection } from "@/types";
import { cn } from "@/lib/utils";

interface Document {
  id: string;
  collection_id?: string | null;
}

interface StatusCounts {
  completed: number;
  in_progress: number;
  pending: number;
  failed: number;
  degraded: number;
  flagged: number;
}

interface DocumentFiltersProps {
  filterCollectionId: string | null;
  onCollectionFilterChange: (id: string | null) => void;
  filterStatus: string | null;
  onStatusFilterChange: (status: string | null) => void;
  filterSource: string | null;
  onSourceFilterChange: (source: string | null) => void;
  sourceCounts: Record<string, number>;
  collections: Collection[];
  documents: Document[];
  statusCounts: StatusCounts;
}

// Custom dropdown component
function Dropdown({
  label,
  value,
  options,
  onChange,
  icon: Icon,
}: {
  label: string;
  value: string | null;
  options: { value: string | null; label: string; count?: number; icon?: React.ElementType }[];
  onChange: (value: string | null) => void;
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
  const displayLabel = selectedOption?.label || label;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors",
          "bg-muted/50 hover:bg-muted border border-border",
          isOpen && "bg-muted ring-2 ring-accent"
        )}
      >
        {Icon && <Icon className="w-4 h-4 text-muted-foreground" />}
        <span className="text-foreground">{displayLabel}</span>
        <ChevronDown className={cn("w-4 h-4 text-muted-foreground transition-transform", isOpen && "rotate-180")} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 min-w-[200px] bg-popover border border-border rounded-lg shadow-lg z-50 py-1">
          {options.map((option) => {
            const OptionIcon = option.icon;
            return (
              <button
                key={option.value ?? "null"}
                onClick={() => {
                  onChange(option.value);
                  setIsOpen(false);
                }}
                className={cn(
                  "w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-muted transition-colors",
                  value === option.value && "bg-muted/50"
                )}
              >
                <div className="flex items-center gap-2">
                  {OptionIcon ? (
                    <OptionIcon className={cn(
                      "w-4 h-4",
                      value === option.value ? "text-accent" : "text-muted-foreground"
                    )} />
                  ) : value === option.value ? (
                    <Check className="w-4 h-4 text-accent" />
                  ) : (
                    <div className="w-4 h-4" />
                  )}
                  <span className="text-foreground">{option.label}</span>
                </div>
                {option.count !== undefined && (
                  <span className="text-muted-foreground">{option.count}</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function DocumentFilters({
  filterCollectionId,
  onCollectionFilterChange,
  filterStatus,
  onStatusFilterChange,
  filterSource,
  onSourceFilterChange,
  sourceCounts,
  collections,
  documents,
  statusCounts,
}: DocumentFiltersProps) {
  // Count documents without collection
  const unassignedCount = documents.filter((d) => !d.collection_id).length;

  const collectionOptions: { value: string | null; label: string; count?: number; icon?: React.ElementType }[] = [
    { value: null, label: "All Collections", icon: Check },
    { value: "none", label: "No Collection", count: unassignedCount, icon: X },
    ...collections.map((c) => ({ value: c.id, label: c.name, count: c.document_count, icon: FolderOpen })),
  ];

  const statusOptions: { value: string | null; label: string; count?: number; icon?: React.ElementType }[] = [
    { value: null, label: "All Status", icon: Clock },
    { value: "completed", label: "Completed", count: statusCounts.completed, icon: CheckCircle2 },
    { value: "in_progress", label: "In Progress", count: statusCounts.in_progress, icon: Loader2 },
    { value: "pending", label: "Pending", count: statusCounts.pending, icon: Clock },
    { value: "failed", label: "Failed", count: statusCounts.failed, icon: XCircle },
    { value: "degraded", label: "Degraded", count: statusCounts.degraded, icon: AlertTriangle },
    // Only offered when the experimental ingestion injection scan has actually
    // flagged something — with the feature off (the default) it never appears.
    ...(statusCounts.flagged > 0
      ? [{ value: "flagged", label: "Injection Flagged", count: statusCounts.flagged, icon: ShieldAlert }]
      : []),
  ];

  const sourceKeys = Object.keys(sourceCounts).sort();
  const sourceOptions: { value: string | null; label: string; count?: number }[] = [
    { value: null, label: "All Sources" },
    ...sourceKeys.map((src) => ({ value: src, label: src, count: sourceCounts[src] })),
  ];

  // Only show source filter if there are multiple distinct sources
  const showSourceFilter = sourceKeys.length > 1;

  return (
    <>
      {/* Collection filter */}
      <Dropdown
        label="All Collections"
        value={filterCollectionId}
        options={collectionOptions}
        onChange={onCollectionFilterChange}
        icon={Filter}
      />

      {/* Status filter */}
      <Dropdown
        label="All Status"
        value={filterStatus}
        options={statusOptions}
        onChange={onStatusFilterChange}
        icon={Clock}
      />

      {/* Source filter - only shown when documents have multiple sources */}
      {showSourceFilter && (
        <Dropdown
          label="All Sources"
          value={filterSource}
          options={sourceOptions}
          onChange={onSourceFilterChange}
          icon={Globe}
        />
      )}
    </>
  );
}
