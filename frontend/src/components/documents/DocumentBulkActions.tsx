"use client";

import { useState, useRef, useEffect } from "react";
import {
  Trash2,
  Loader2,
  RefreshCw,
  Square,
  CheckSquare,
  XCircle,
  Clock,
  ArrowRight,
  ChevronDown,
  Download,
} from "lucide-react";
import type { Collection } from "@/types";
import { cn } from "@/lib/utils";

interface DocumentBulkActionsProps {
  selectedCount: number;
  totalCount: number;
  filteredCount: number;
  allFilteredSelected: boolean;
  failedCount: number;
  inProgressCount: number;
  selectedInProgressCount: number;
  isReprocessing: boolean;
  isDeletingSelected: boolean;
  isMoving: boolean;
  isDownloading: boolean;
  availableTargetCollections: Collection[];
  hasFilters: boolean;
  onToggleSelectAll: () => void;
  onSelectFailed: () => void;
  onSelectInProgress: () => void;
  onReprocessSelected: () => void;
  onRestartSelected: () => void;
  onDeleteSelected: () => void;
  onDownloadSelected: () => void;
  onMoveToCollection: (collectionId: string) => void;
}

// Move dropdown component
function MoveDropdown({
  collections,
  onSelect,
  disabled,
}: {
  collections: Collection[];
  onSelect: (id: string) => void;
  disabled: boolean;
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

  if (collections.length === 0) return null;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={disabled}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors",
          "bg-muted/50 hover:bg-muted border border-border disabled:opacity-50"
        )}
      >
        <ArrowRight className="w-4 h-4" />
        <span>Move to...</span>
        <ChevronDown className={cn("w-3 h-3 transition-transform", isOpen && "rotate-180")} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 min-w-[180px] bg-popover border border-border rounded-lg shadow-lg z-50 py-1">
          {collections.map((c) => (
            <button
              key={c.id}
              onClick={() => {
                onSelect(c.id);
                setIsOpen(false);
              }}
              className="w-full flex items-center px-3 py-2 text-sm text-foreground hover:bg-muted transition-colors text-left"
            >
              {c.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function DocumentBulkActions({
  selectedCount,
  filteredCount,
  allFilteredSelected,
  failedCount,
  inProgressCount,
  selectedInProgressCount,
  isReprocessing,
  isDeletingSelected,
  isMoving,
  isDownloading,
  availableTargetCollections,
  onToggleSelectAll,
  onReprocessSelected,
  onDeleteSelected,
  onDownloadSelected,
  onMoveToCollection,
}: DocumentBulkActionsProps) {
  const isLoading = isReprocessing || isDeletingSelected || isMoving || isDownloading;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Select all toggle */}
      <button
        onClick={onToggleSelectAll}
        className={cn(
          "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors",
          allFilteredSelected
            ? "text-accent hover:text-accent/80 hover:bg-accent/10"
            : "text-muted-foreground hover:text-foreground hover:bg-muted"
        )}
      >
        {allFilteredSelected ? (
          <CheckSquare className="w-4 h-4" />
        ) : (
          <Square className="w-4 h-4" />
        )}
        {allFilteredSelected ? "Deselect All" : "Select All"}
      </button>

      {/* Bulk actions when items selected */}
      {selectedCount > 0 && (
        <>
          {/* Reprocess selected */}
          <button
            onClick={onReprocessSelected}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-muted/50 hover:bg-muted border border-border transition-colors disabled:opacity-50"
          >
            {isReprocessing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Reprocess
          </button>

          {/* Download selected */}
          <button
            onClick={onDownloadSelected}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-muted/50 hover:bg-muted border border-border transition-colors disabled:opacity-50"
          >
            {isDownloading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}
            Download
          </button>

          {/* Move to collection */}
          <MoveDropdown
            collections={availableTargetCollections}
            onSelect={onMoveToCollection}
            disabled={isLoading}
          />

          {/* Delete selected */}
          <button
            onClick={onDeleteSelected}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-red-500 hover:bg-red-500/10 transition-colors disabled:opacity-50"
          >
            {isDeletingSelected ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
            Delete ({selectedCount})
          </button>
        </>
      )}

    </div>
  );
}
