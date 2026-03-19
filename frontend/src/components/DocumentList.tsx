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
  Upload,
  CheckCircle2,
  XCircle,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Collection } from "@/types";
import { DocumentCard, DocumentFilters, DocumentBulkActions } from "./documents";
import { UploadModal } from "./upload";
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
  image_progress_current?: number;
  image_progress_total?: number;
  image_progress_message?: string;
  collection_id?: string | null;
  collection_name?: string | null;
  is_custom_input?: boolean;
  custom_input_type?: string | null;
  custom_topic_hint?: string | null;
}

type UploadFileStatus = "uploading" | "uploaded" | "error";

interface UploadingFileEntry {
  id: string;
  file: File;
  status: UploadFileStatus;
  message?: string;
}

interface DocumentListProps {
  onDelete: () => void;
}

const hasUnfinishedImages = (doc: Document) => {
  const total = doc.image_progress_total ?? 0;
  return total > 0 && doc.image_progress_current !== total;
};

const effectiveStatus = (doc: Document): string => {
  if (doc.processing_status === "completed" && hasUnfinishedImages(doc)) {
    return "in_progress";
  }
  if (doc.processing_status === "processing" || doc.processing_status === "extracting") {
    return "in_progress";
  }
  return doc.processing_status;
};

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
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFileEntry[]>([]);
  const [isStartingProcessing, setIsStartingProcessing] = useState(false);

  // Track last clicked index for shift-click range selection
  const lastClickedIndex = useRef<number | null>(null);
  
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
      const data = await api.getDocuments();
      setDocuments(data.documents);
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

  const handleFilesSelected = useCallback(async (files: File[], collectionId?: string) => {
    // Create entries for each file
    const entries: UploadingFileEntry[] = files.map((file, i) => ({
      id: `upload-${Date.now()}-${i}`,
      file,
      status: "uploading" as const,
    }));
    setUploadingFiles((prev) => [...prev, ...entries]);

    const CONCURRENCY_LIMIT = 10;
    const queue = [...entries];
    let allDone = 0;

    const uploadNext = async () => {
      const entry = queue.shift();
      if (!entry) return;

      try {
        await api.uploadFile(entry.file, collectionId, false);
        setUploadingFiles((prev) =>
          prev.map((uf) =>
            uf.id === entry.id ? { ...uf, status: "uploaded" as const, message: "Uploaded" } : uf
          )
        );
      } catch (error) {
        setUploadingFiles((prev) =>
          prev.map((uf) =>
            uf.id === entry.id
              ? { ...uf, status: "error" as const, message: error instanceof Error ? error.message : "Upload failed" }
              : uf
          )
        );
      }
      allDone++;

      // Refresh document list after each upload to show new docs
      await fetchDocuments();

      await uploadNext();
    };

    const workers = [];
    for (let i = 0; i < Math.min(CONCURRENCY_LIMIT, entries.length); i++) {
      workers.push(uploadNext());
    }
    await Promise.all(workers);

    // All uploads done — clear uploading entries after a short delay
    setTimeout(() => {
      setUploadingFiles((prev) => prev.filter((uf) => uf.status === "error"));
    }, 1500);

    fetchDocuments();
  }, []);

  const handleStartProcessing = async () => {
    setIsStartingProcessing(true);
    try {
      await api.processPendingDocuments();
      await fetchDocuments();
    } catch (error) {
      console.error("Failed to start processing:", error);
    } finally {
      setIsStartingProcessing(false);
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
      return effectiveStatus(doc) === filterStatus;
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

  // Status counts (using effective status so image-analyzing docs count as in_progress)
  const statusCounts = {
    completed: documents.filter((d) => effectiveStatus(d) === "completed").length,
    in_progress: documents.filter((d) => effectiveStatus(d) === "in_progress").length,
    pending: documents.filter((d) => effectiveStatus(d) === "pending").length,
    failed: documents.filter((d) => effectiveStatus(d) === "failed").length,
  };

  const availableTargetCollections = collections.filter(
    (c) => filterCollectionId === null || filterCollectionId === "none" || c.id !== filterCollectionId
  );

  const failedDocuments = filteredDocuments.filter((d) => effectiveStatus(d) === "failed");
  const inProgressDocuments = filteredDocuments.filter((d) => effectiveStatus(d) === "in_progress" || effectiveStatus(d) === "pending");
  const selectedInProgressCount = documents.filter(
    (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
  ).length;
  const allFilteredSelected = filteredDocuments.length > 0 && filteredDocuments.every((d) => selectedIds.has(d.id));

  // Action handlers
  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this document?")) return;

    setDeletingId(id);
    try {
      await api.deleteDocument(id);
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(id);
        return newSet;
      });
      onDelete();
    } catch (error) {
      console.error("Failed to delete document:", error);
    } finally {
      setDeletingId(null);
    }
  };

  const toggleSelection = (id: string, shiftKey?: boolean) => {
    const currentIndex = filteredDocuments.findIndex((d) => d.id === id);
    
    // Handle shift-click range selection
    if (shiftKey && lastClickedIndex.current !== null && currentIndex !== -1) {
      const start = Math.min(lastClickedIndex.current, currentIndex);
      const end = Math.max(lastClickedIndex.current, currentIndex);
      
      // Get all document IDs in the range
      const rangeIds = filteredDocuments.slice(start, end + 1).map((d) => d.id);
      
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        // Add all documents in the range
        rangeIds.forEach((docId) => newSet.add(docId));
        return newSet;
      });
    } else {
      // Normal toggle
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        if (newSet.has(id)) {
          newSet.delete(id);
        } else {
          newSet.add(id);
        }
        return newSet;
      });
    }
    
    // Update last clicked index for next shift-click
    if (currentIndex !== -1) {
      lastClickedIndex.current = currentIndex;
    }
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
      await api.deleteDocuments(Array.from(selectedIds));
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete();
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
      <>
        <div className="glass rounded-lg p-12 text-center">
          <div className="w-16 h-16 mx-auto rounded-lg bg-accent/20 flex items-center justify-center mb-6">
            <FileText className="w-8 h-8 text-accent" />
          </div>
          <h3 className="text-lg font-medium text-foreground mb-2">
            No Documents Yet
          </h3>
          <p className="text-muted-foreground max-w-md mx-auto mb-6">
            Upload your first document to start building your knowledge base.
          </p>
          <button
            onClick={() => setShowUploadModal(true)}
            className="inline-flex items-center gap-2 px-6 py-3 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
          >
            <Upload className="w-4 h-4" />
            Upload Documents
          </button>
        </div>
        <UploadModal
          isOpen={showUploadModal}
          onClose={() => setShowUploadModal(false)}
          onFilesSelected={handleFilesSelected}
        />
      </>
    );
  }

  const hasFilters = filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "";

  return (
    <div className="space-y-3">
      {/* Search bar + Upload button */}
      <div className="flex items-center gap-3">
        <div className="glass rounded-lg flex-1">
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
        <button
          onClick={() => setShowUploadModal(true)}
          className="flex items-center gap-2 px-4 py-3 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors shrink-0"
        >
          <Upload className="w-4 h-4" />
          Upload
        </button>
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

      {/* Uploading files */}
      {uploadingFiles.length > 0 && (
        <div className="grid gap-2">
          {uploadingFiles.map((uf) => (
            <div
              key={uf.id}
              className={cn(
                "glass rounded-lg p-4 border transition-all duration-200",
                uf.status === "error" ? "border-destructive/30" : "border-border"
              )}
            >
              <div className="flex items-center gap-4">
                <div className="p-2 rounded-lg bg-muted shrink-0">
                  {uf.status === "uploading" ? (
                    <Loader2 className="w-5 h-5 text-muted-foreground animate-spin" />
                  ) : uf.status === "uploaded" ? (
                    <CheckCircle2 className="w-5 h-5 text-accent" />
                  ) : (
                    <XCircle className="w-5 h-5 text-destructive" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-medium text-foreground truncate">{uf.file.name}</h4>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {uf.status === "uploading"
                      ? "Uploading..."
                      : uf.status === "uploaded"
                        ? "Uploaded"
                        : uf.message || "Upload failed"}
                  </p>
                </div>
                {uf.status === "uploading" && (
                  <div className="px-2 py-1 rounded-full bg-muted text-xs text-muted-foreground">
                    <Upload className="w-3 h-3 inline mr-1" />
                    Uploading
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Generate Graph banner */}
      {(statusCounts.pending > 0 || statusCounts.in_progress > 0) && (
        <div className="glass rounded-lg p-4 border border-border flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            {statusCounts.in_progress > 0
              ? `Processing ${statusCounts.in_progress + statusCounts.pending} document${statusCounts.in_progress + statusCounts.pending !== 1 ? "s" : ""}...`
              : `${statusCounts.pending} document${statusCounts.pending !== 1 ? "s" : ""} ready to process`}
          </div>
          {statusCounts.in_progress > 0 ? (
            <div className="flex items-center gap-2 px-4 py-2 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              Processing...
            </div>
          ) : (
            <button
              onClick={() => router.push("/extract")}
              className="flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              Generate Graph
            </button>
          )}
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

      {/* Upload Modal */}
      <UploadModal
        isOpen={showUploadModal}
        onClose={() => setShowUploadModal(false)}
        onFilesSelected={handleFilesSelected}
      />
    </div>
  );
}
