"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { AnimatePresence } from "framer-motion";
import {
  FileText,
  Loader2,
  AlertCircle,
  Search,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Collection } from "@/types";
import { DocumentCard, DocumentFilters, DocumentBulkActions } from "./documents";
import { cn } from "@/lib/utils";

interface Document {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  file_path?: string | null;
  upload_date: string;
  chunk_count: number;
  processing_status: string;
  error_message?: string;
  progress_current?: number;
  progress_total?: number;
  progress_message?: string;
  collection_id?: string | null;
  collection_name?: string | null;
  is_custom_input?: boolean;
  custom_input_type?: string | null;
  custom_topic_hint?: string | null;
}

interface DocumentListProps {
  onDelete: () => void;
}

const isProcessing = (status: string) => {
  return status === "processing" || status === "extracting" || status === "pending";
};

const DOCUMENTS_PER_PAGE = 100;

export default function DocumentList({ onDelete }: DocumentListProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  
  const [documents, setDocuments] = useState<Document[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isReprocessing, setIsReprocessing] = useState(false);
  const [reprocessingIds, setReprocessingIds] = useState<Set<string>>(new Set());
  const [isDeletingSelected, setIsDeletingSelected] = useState(false);
  const [filterCollectionId, setFilterCollectionId] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isMoving, setIsMoving] = useState(false);
  
  // Pagination - read from URL, default to 1
  const currentPage = Math.max(1, parseInt(searchParams.get("page") || "1", 10));
  
  // Update URL when page changes and scroll to top
  const setCurrentPage = useCallback((page: number) => {
    const params = new URLSearchParams(searchParams.toString());
    if (page === 1) {
      params.delete("page");
    } else {
      params.set("page", String(page));
    }
    const queryString = params.toString();
    router.push(queryString ? `${pathname}?${queryString}` : pathname, { scroll: false });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [router, pathname, searchParams]);

  const fetchDocuments = async () => {
    try {
      const res = await fetch("/api/documents");
      if (res.ok) {
        const data = await res.json();
        setDocuments(data.documents);
      }
    } catch (error) {
      console.error("Failed to fetch documents:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchCollections = async () => {
    try {
      const data = await api.getCollections();
      setCollections(data.collections);
    } catch (error) {
      console.error("Failed to fetch collections:", error);
    }
  };

  useEffect(() => {
    fetchDocuments();
    fetchCollections();
    const interval = setInterval(fetchDocuments, 5000);
    return () => clearInterval(interval);
  }, []);

  // Sync selected IDs with existing documents
  useEffect(() => {
    const docIds = new Set(documents.map((d) => d.id));
    setSelectedIds((prev) => {
      const newSet = new Set<string>();
      prev.forEach((id) => {
        if (docIds.has(id)) newSet.add(id);
      });
      return newSet;
    });
  }, [documents]);

  // Track if it's the initial mount to avoid resetting page on first render
  const isInitialMount = useRef(true);
  const prevFilters = useRef({ filterCollectionId, filterStatus, searchQuery });
  
  // Reset to page 1 and clear selections when filters change (but not on initial mount)
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      prevFilters.current = { filterCollectionId, filterStatus, searchQuery };
      return;
    }
    
    // Only reset if filters actually changed
    const filtersChanged = 
      prevFilters.current.filterCollectionId !== filterCollectionId ||
      prevFilters.current.filterStatus !== filterStatus ||
      prevFilters.current.searchQuery !== searchQuery;
    
    if (filtersChanged) {
      // Clear selections when filters change
      setSelectedIds(new Set());
      if (currentPage !== 1) {
        setCurrentPage(1);
      }
    }
    
    prevFilters.current = { filterCollectionId, filterStatus, searchQuery };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterCollectionId, filterStatus, searchQuery]);

  // Filter documents
  const filteredDocuments = documents.filter((doc) => {
    const matchesCollection =
      filterCollectionId === null ||
      (filterCollectionId === "none" ? !doc.collection_id : doc.collection_id === filterCollectionId);

    const matchesStatus = (() => {
      if (filterStatus === null) return true;
      if (filterStatus === "in_progress") {
        return doc.processing_status === "processing" || doc.processing_status === "extracting";
      }
      return doc.processing_status === filterStatus;
    })();

    const searchLower = searchQuery.toLowerCase().trim();
    const matchesSearch =
      searchQuery.trim() === "" ||
      doc.filename.toLowerCase().includes(searchLower) ||
      (doc.custom_topic_hint && doc.custom_topic_hint.toLowerCase().includes(searchLower));

    return matchesCollection && matchesStatus && matchesSearch;
  });
  
  // Pagination calculations
  const totalPages = Math.ceil(filteredDocuments.length / DOCUMENTS_PER_PAGE);
  const validCurrentPage = Math.min(currentPage, Math.max(1, totalPages));
  const startIndex = (validCurrentPage - 1) * DOCUMENTS_PER_PAGE;
  const endIndex = startIndex + DOCUMENTS_PER_PAGE;
  const paginatedDocuments = filteredDocuments.slice(startIndex, endIndex);
  
  // Redirect to valid page if current page is out of bounds
  useEffect(() => {
    if (totalPages > 0 && currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [totalPages, currentPage, setCurrentPage]);

  // Status counts
  const statusCounts = {
    completed: documents.filter((d) => d.processing_status === "completed").length,
    in_progress: documents.filter((d) => ["processing", "extracting"].includes(d.processing_status)).length,
    pending: documents.filter((d) => d.processing_status === "pending").length,
    failed: documents.filter((d) => d.processing_status === "failed").length,
  };

  const availableTargetCollections = collections.filter(
    (c) => filterCollectionId === null || filterCollectionId === "none" || c.id !== filterCollectionId
  );

  const failedDocuments = filteredDocuments.filter((d) => d.processing_status === "failed");
  const inProgressDocuments = filteredDocuments.filter((d) => isProcessing(d.processing_status));
  const selectedInProgressCount = documents.filter(
    (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
  ).length;
  const allFilteredSelected = filteredDocuments.length > 0 && filteredDocuments.every((d) => selectedIds.has(d.id));

  // Action handlers
  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this document?")) return;

    setDeletingId(id);
    try {
      const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
      if (res.ok) {
        setDocuments((prev) => prev.filter((d) => d.id !== id));
        setSelectedIds((prev) => {
          const newSet = new Set(prev);
          newSet.delete(id);
          return newSet;
        });
        onDelete();
      }
    } catch (error) {
      console.error("Failed to delete document:", error);
    } finally {
      setDeletingId(null);
    }
  };

  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  const toggleSelectAll = () => {
    const filteredIds = new Set(filteredDocuments.map((d) => d.id));
    const allSelected = filteredDocuments.every((d) => selectedIds.has(d.id));

    if (allSelected) {
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        filteredIds.forEach((id) => newSet.delete(id));
        return newSet;
      });
    } else {
      setSelectedIds((prev) => new Set([...prev, ...filteredIds]));
    }
  };

  const selectAllFailed = () => {
    const failedIds = filteredDocuments
      .filter((d) => d.processing_status === "failed")
      .map((d) => d.id);
    setSelectedIds(new Set(failedIds));
  };

  const selectInProgress = () => {
    setSelectedIds(new Set(inProgressDocuments.map((d) => d.id)));
  };

  const handleMoveToCollection = async (targetCollectionId: string) => {
    if (selectedIds.size === 0) return;

    const selectedArray = Array.from(selectedIds);
    const targetCollection = collections.find((c) => c.id === targetCollectionId);

    const confirmed = confirm(
      `Move ${selectedIds.size} document(s) to "${targetCollection?.name || 'collection'}"?`
    );
    if (!confirmed) return;

    setIsMoving(true);
    try {
      await api.moveDocumentsToCollection(selectedArray, targetCollectionId);
      setSelectedIds(new Set());
      await fetchDocuments();
      await fetchCollections();
      onDelete();
    } catch (error) {
      console.error("Failed to move documents:", error);
      alert("Failed to move documents");
    } finally {
      setIsMoving(false);
    }
  };

  const handleReprocessSelected = async () => {
    if (selectedIds.size === 0) return;

    const confirmed = confirm(
      `Reprocess ${selectedIds.size} document(s)? Original files are stored - no re-upload needed.`
    );
    if (!confirmed) return;

    setIsReprocessing(true);
    try {
      const result = await api.reprocessDocuments(Array.from(selectedIds));
      const errors = result.results.filter((r: { status: string }) => r.status === "error");
      if (errors.length > 0) {
        const errorMsgs = errors.map((e: { document_id: string; message: string }) => `${e.document_id}: ${e.message}`).join("\n");
        alert(`Some documents failed to queue:\n${errorMsgs}`);
      }
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to reprocess documents:", error);
      alert(`Failed to reprocess: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsReprocessing(false);
    }
  };

  const handleRestartSelected = async () => {
    const inProgressSelected = documents.filter(
      (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
    );

    if (inProgressSelected.length === 0) return;

    const confirmed = confirm(
      `Restart ${inProgressSelected.length} in-progress document(s)?`
    );
    if (!confirmed) return;

    setIsReprocessing(true);
    try {
      const docIds = inProgressSelected.map((d) => d.id);
      await api.reprocessDocuments(docIds);
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to restart documents:", error);
      alert(`Failed to restart: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsReprocessing(false);
    }
  };

  const handleDeleteSelected = async () => {
    if (selectedIds.size === 0) return;

    const confirmed = confirm(
      `Are you sure you want to delete ${selectedIds.size} document(s)?`
    );
    if (!confirmed) return;

    setIsDeletingSelected(true);
    try {
      const res = await fetch("/api/documents/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_ids: Array.from(selectedIds) }),
      });

      if (res.ok) {
        setSelectedIds(new Set());
        await fetchDocuments();
        onDelete();
      }
    } catch (error) {
      console.error("Failed to delete documents:", error);
    } finally {
      setIsDeletingSelected(false);
    }
  };

  const handleReprocessWithFile = async (docId: string, file: File) => {
    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId, file);
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to reprocess document:", error);
      alert(`Reprocess failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setReprocessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(docId);
        return newSet;
      });
    }
  };

  const handleReprocessDocument = async (docId: string) => {
    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId);
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to reprocess document:", error);
      alert(`Reprocess failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setReprocessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(docId);
        return newSet;
      });
    }
  };

  const handleRestartDocument = async (docId: string) => {
    const confirmed = confirm("Restart this document?");
    if (!confirmed) return;

    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId);
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to restart document:", error);
      alert(`Restart failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setReprocessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(docId);
        return newSet;
      });
    }
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <Loader2 className="w-8 h-8 text-foreground animate-spin mx-auto mb-4" />
        <p className="text-muted-foreground">Loading documents...</p>
      </div>
    );
  }

  // Empty state
  if (documents.length === 0) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <div className="w-16 h-16 mx-auto rounded-lg bg-accent/20 flex items-center justify-center mb-6">
          <FileText className="w-8 h-8 text-accent" />
        </div>
        <h3 className="text-lg font-medium text-foreground mb-2">
          No Documents Yet
        </h3>
        <p className="text-muted-foreground max-w-md mx-auto">
          Upload your first document to start building your knowledge base.
        </p>
      </div>
    );
  }

  const hasFilters = filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "";

  return (
    <div className="space-y-3">
      {/* Search bar */}
      <div className="glass rounded-lg">
        <div className="relative">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by filename or topic..."
            className="w-full pl-11 pr-4 py-3 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
        </div>
      </div>

      {/* Unified toolbar with filters and actions */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Document count with selection info */}
        <span className="text-sm text-muted-foreground">
          {filteredDocuments.length} document{filteredDocuments.length !== 1 ? "s" : ""}
          {selectedIds.size > 0 && (
            <span className="text-foreground font-medium"> ({selectedIds.size} selected)</span>
          )}
        </span>

        {/* Filter dropdowns */}
        <DocumentFilters
          filterCollectionId={filterCollectionId}
          onCollectionFilterChange={setFilterCollectionId}
          filterStatus={filterStatus}
          onStatusFilterChange={setFilterStatus}
          collections={collections}
          documents={documents}
          statusCounts={statusCounts}
        />

        {/* Bulk Actions */}
        <DocumentBulkActions
          selectedCount={selectedIds.size}
          totalCount={documents.length}
          filteredCount={filteredDocuments.length}
          allFilteredSelected={allFilteredSelected}
          failedCount={failedDocuments.length}
          inProgressCount={inProgressDocuments.length}
          selectedInProgressCount={selectedInProgressCount}
          isReprocessing={isReprocessing}
          isDeletingSelected={isDeletingSelected}
          isMoving={isMoving}
          availableTargetCollections={availableTargetCollections}
          hasFilters={hasFilters}
          onToggleSelectAll={toggleSelectAll}
          onSelectFailed={selectAllFailed}
          onSelectInProgress={selectInProgress}
          onReprocessSelected={handleReprocessSelected}
          onRestartSelected={handleRestartSelected}
          onDeleteSelected={handleDeleteSelected}
          onMoveToCollection={handleMoveToCollection}
          onRefresh={fetchDocuments}
        />
      </div>

      {/* Failed documents summary */}
      {failedDocuments.length > 0 && (
        <div className="glass rounded-lg p-4 border border-border">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-muted-foreground shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-medium text-foreground">
                {failedDocuments.length} document{failedDocuments.length !== 1 ? "s" : ""} failed
              </h4>
              <p className="text-xs text-muted-foreground mt-0.5">
                Click reprocess to retry, or select multiple and use bulk actions.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* No results */}
      {filteredDocuments.length === 0 && hasFilters && (
        <div className="glass rounded-lg p-8 text-center">
          <Search className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <h3 className="text-lg font-medium text-foreground mb-2">No Documents Found</h3>
          <p className="text-muted-foreground">
            No documents match your current filters.
          </p>
        </div>
      )}

      {/* Document list */}
      <div className="grid gap-3">
        <AnimatePresence>
          {paginatedDocuments.map((doc, index) => (
            <DocumentCard
              key={doc.id}
              doc={doc}
              index={index}
              isSelected={selectedIds.has(doc.id)}
              onToggleSelection={toggleSelection}
              onDelete={handleDelete}
              onReprocess={handleReprocessDocument}
              onReprocessWithFile={handleReprocessWithFile}
              onRestart={handleRestartDocument}
              isDeleting={deletingId === doc.id}
              isReprocessing={reprocessingIds.has(doc.id)}
            />
          ))}
        </AnimatePresence>
      </div>
      
      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between glass rounded-lg px-4 py-3">
          <div className="text-sm text-muted-foreground">
            Showing {startIndex + 1}-{Math.min(endIndex, filteredDocuments.length)} of {filteredDocuments.length} documents
          </div>
          
          <div className="flex items-center gap-1">
            {/* First page */}
            <button
              onClick={() => setCurrentPage(1)}
              disabled={validCurrentPage === 1}
              className={cn(
                "p-1.5 rounded-lg transition-colors",
                validCurrentPage === 1
                  ? "text-muted-foreground/50 cursor-not-allowed"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
              title="First page"
            >
              <ChevronsLeft className="w-4 h-4" />
            </button>
            
            {/* Previous page */}
            <button
              onClick={() => setCurrentPage(validCurrentPage - 1)}
              disabled={validCurrentPage === 1}
              className={cn(
                "p-1.5 rounded-lg transition-colors",
                validCurrentPage === 1
                  ? "text-muted-foreground/50 cursor-not-allowed"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
              title="Previous page"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            
            {/* Page numbers */}
            <div className="flex items-center gap-1 mx-2">
              {(() => {
                const pages: (number | "ellipsis")[] = [];
                const maxVisible = 5;
                
                if (totalPages <= maxVisible + 2) {
                  // Show all pages
                  for (let i = 1; i <= totalPages; i++) pages.push(i);
                } else {
                  // Always show first page
                  pages.push(1);
                  
                  // Calculate range around current page
                  let start = Math.max(2, validCurrentPage - 1);
                  let end = Math.min(totalPages - 1, validCurrentPage + 1);
                  
                  // Adjust range to always show 3 middle pages when possible
                  if (validCurrentPage <= 3) {
                    end = Math.min(4, totalPages - 1);
                  } else if (validCurrentPage >= totalPages - 2) {
                    start = Math.max(2, totalPages - 3);
                  }
                  
                  // Add ellipsis before middle pages if needed
                  if (start > 2) pages.push("ellipsis");
                  
                  // Add middle pages
                  for (let i = start; i <= end; i++) pages.push(i);
                  
                  // Add ellipsis after middle pages if needed
                  if (end < totalPages - 1) pages.push("ellipsis");
                  
                  // Always show last page
                  pages.push(totalPages);
                }
                
                return pages.map((page, idx) =>
                  page === "ellipsis" ? (
                    <span key={`ellipsis-${idx}`} className="px-2 text-muted-foreground">
                      ...
                    </span>
                  ) : (
                    <button
                      key={page}
                      onClick={() => setCurrentPage(page)}
                      className={cn(
                        "min-w-[32px] h-8 px-2 rounded-lg text-sm font-medium transition-colors",
                        page === validCurrentPage
                          ? "bg-accent text-accent-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-muted"
                      )}
                    >
                      {page}
                    </button>
                  )
                );
              })()}
            </div>
            
            {/* Next page */}
            <button
              onClick={() => setCurrentPage(validCurrentPage + 1)}
              disabled={validCurrentPage === totalPages}
              className={cn(
                "p-1.5 rounded-lg transition-colors",
                validCurrentPage === totalPages
                  ? "text-muted-foreground/50 cursor-not-allowed"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
              title="Next page"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            
            {/* Last page */}
            <button
              onClick={() => setCurrentPage(totalPages)}
              disabled={validCurrentPage === totalPages}
              className={cn(
                "p-1.5 rounded-lg transition-colors",
                validCurrentPage === totalPages
                  ? "text-muted-foreground/50 cursor-not-allowed"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
              title="Last page"
            >
              <ChevronsRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
