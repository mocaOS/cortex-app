"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  Trash2,
  Loader2,
  CheckCircle,
  Clock,
  AlertCircle,
  RefreshCw,
  RotateCcw,
  Square,
  CheckSquare,
  Upload,
  Filter,
  FolderOpen,
  ArrowRight,
  ChevronDown,
  X,
  Search,
  StopCircle,
} from "lucide-react";
import { cn, formatBytes, formatDate, getFileTypeIcon } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Collection } from "@/types";

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
}

interface DocumentListProps {
  onDelete: () => void;
}

export default function DocumentList({ onDelete }: DocumentListProps) {
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
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [isStatusFilterOpen, setIsStatusFilterOpen] = useState(false);
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isMoving, setIsMoving] = useState(false);
  const fileInputRefs = useRef<Map<string, HTMLInputElement>>(new Map());
  const filterRef = useRef<HTMLDivElement>(null);
  const statusFilterRef = useRef<HTMLDivElement>(null);
  const moveRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(event.target as Node)) {
        setIsFilterOpen(false);
      }
      if (statusFilterRef.current && !statusFilterRef.current.contains(event.target as Node)) {
        setIsStatusFilterOpen(false);
      }
      if (moveRef.current && !moveRef.current.contains(event.target as Node)) {
        setIsMoveOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

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
    
    const matchesSearch = 
      searchQuery.trim() === "" ||
      doc.filename.toLowerCase().includes(searchQuery.toLowerCase().trim());
    
    return matchesCollection && matchesStatus && matchesSearch;
  });

  const getFilterLabel = () => {
    if (filterCollectionId === null) return "All Collections";
    if (filterCollectionId === "none") return "No Collection";
    const col = collections.find((c) => c.id === filterCollectionId);
    return col?.name || "Unknown";
  };

  const getStatusFilterLabel = () => {
    switch (filterStatus) {
      case null: return "All Status";
      case "completed": return "Completed";
      case "in_progress": return "In Progress";
      case "failed": return "Failed";
      case "pending": return "Pending";
      default: return filterStatus;
    }
  };

  const statusCounts = {
    completed: documents.filter((d) => d.processing_status === "completed").length,
    in_progress: documents.filter((d) => ["processing", "extracting"].includes(d.processing_status)).length,
    pending: documents.filter((d) => d.processing_status === "pending").length,
    failed: documents.filter((d) => d.processing_status === "failed").length,
  };

  const availableTargetCollections = collections.filter(
    (c) => filterCollectionId === null || filterCollectionId === "none" || c.id !== filterCollectionId
  );

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
    const allFilteredSelected = filteredDocuments.every((d) => selectedIds.has(d.id));
    
    if (allFilteredSelected) {
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

  const handleMoveToCollection = async (targetCollectionId: string) => {
    if (selectedIds.size === 0) return;

    const selectedArray = Array.from(selectedIds);
    const targetCollection = collections.find((c) => c.id === targetCollectionId);
    
    const confirmed = confirm(
      `Move ${selectedIds.size} document(s) to "${targetCollection?.name || 'collection'}"?`
    );
    if (!confirmed) return;

    setIsMoving(true);
    setIsMoveOpen(false);
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
      const errors = result.results.filter(r => r.status === "error");
      if (errors.length > 0) {
        const errorMsgs = errors.map(e => `${e.document_id}: ${e.message}`).join("\n");
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

  const triggerFileUpload = (docId: string) => {
    const input = fileInputRefs.current.get(docId);
    if (input) input.click();
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="w-4 h-4 text-accent" />;
      case "processing":
      case "extracting":
        return <Loader2 className="w-4 h-4 text-accent animate-spin" />;
      case "failed":
        return <AlertCircle className="w-4 h-4 text-destructive" />;
      default:
        return <Clock className="w-4 h-4 text-muted-foreground" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed":
        return "bg-accent/20 text-accent";
      case "processing":
      case "extracting":
        return "bg-accent/10 text-accent";
      case "failed":
        return "bg-destructive/20 text-destructive";
      default:
        return "bg-muted text-muted-foreground";
    }
  };

  const getProgressPercent = (doc: Document) => {
    if (!doc.progress_total || doc.progress_total === 0) return 0;
    return Math.min(100, Math.round((doc.progress_current || 0) / doc.progress_total * 100));
  };

  const isProcessing = (status: string) => {
    return status === "processing" || status === "extracting" || status === "pending";
  };

  const failedDocuments = filteredDocuments.filter((d) => d.processing_status === "failed");
  const inProgressDocuments = filteredDocuments.filter((d) => isProcessing(d.processing_status));
  const selectedInProgressCount = documents.filter(
    (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
  ).length;
  const allFilteredSelected = filteredDocuments.length > 0 && filteredDocuments.every((d) => selectedIds.has(d.id));

  if (isLoading) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <Loader2 className="w-8 h-8 text-foreground animate-spin mx-auto mb-4" />
        <p className="text-muted-foreground">Loading documents...</p>
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <div className="w-16 h-16 mx-auto rounded-lg bg-muted flex items-center justify-center mb-6">
          <FileText className="w-8 h-8 text-muted-foreground" />
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

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search documents by filename..."
          className={cn(
            "w-full pl-10 pr-10 py-2.5 rounded-lg text-sm",
            "bg-card border border-border",
            "text-foreground placeholder:text-muted-foreground",
            "focus:outline-none focus:border-foreground focus:ring-1 focus:ring-foreground/20",
            "transition-all"
          )}
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>

      {/* Header with actions */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <p className="text-sm text-muted-foreground">
            {filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "" ? (
              <>{filteredDocuments.length} of {documents.length} documents</>
            ) : (
              <>{documents.length} document{documents.length !== 1 ? "s" : ""}</>
            )}
            {selectedIds.size > 0 && (
              <span className="text-foreground ml-2">({selectedIds.size} selected)</span>
            )}
          </p>

          {/* Collection Filter */}
          <div ref={filterRef} className="relative">
            <button
              onClick={() => setIsFilterOpen(!isFilterOpen)}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "border border-border hover:border-ring",
                filterCollectionId !== null
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <Filter className="w-4 h-4" />
              <span className="hidden sm:inline">{getFilterLabel()}</span>
              <ChevronDown className={cn("w-3 h-3 transition-transform", isFilterOpen && "rotate-180")} />
            </button>

            <AnimatePresence>
              {isFilterOpen && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="absolute z-50 top-full left-0 mt-1 min-w-[200px] glass rounded-lg border border-border shadow-xl overflow-hidden"
                >
                  <div className="max-h-64 overflow-y-auto">
                    <button
                      onClick={() => { setFilterCollectionId(null); setIsFilterOpen(false); }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterCollectionId === null ? "bg-muted text-foreground" : "text-foreground hover:bg-muted"
                      )}
                    >
                      <FolderOpen className="w-4 h-4" />
                      <span className="flex-1 text-left">All Collections</span>
                      {filterCollectionId === null && <CheckCircle className="w-4 h-4" />}
                    </button>

                    <button
                      onClick={() => { setFilterCollectionId("none"); setIsFilterOpen(false); }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterCollectionId === "none" ? "bg-muted text-foreground" : "text-foreground hover:bg-muted"
                      )}
                    >
                      <X className="w-4 h-4" />
                      <span className="flex-1 text-left">No Collection</span>
                      <span className="text-xs text-muted-foreground">
                        {documents.filter((d) => !d.collection_id).length}
                      </span>
                      {filterCollectionId === "none" && <CheckCircle className="w-4 h-4" />}
                    </button>

                    {collections.length > 0 && <div className="border-t border-border my-1" />}

                    {collections.map((col) => (
                      <button
                        key={col.id}
                        onClick={() => { setFilterCollectionId(col.id); setIsFilterOpen(false); }}
                        className={cn(
                          "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                          filterCollectionId === col.id ? "bg-muted text-foreground" : "text-foreground hover:bg-muted"
                        )}
                      >
                        <FolderOpen className="w-4 h-4" />
                        <span className="flex-1 text-left truncate">{col.name}</span>
                        <span className="text-xs text-muted-foreground">{col.document_count}</span>
                        {filterCollectionId === col.id && <CheckCircle className="w-4 h-4" />}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Status Filter */}
          <div ref={statusFilterRef} className="relative">
            <button
              onClick={() => setIsStatusFilterOpen(!isStatusFilterOpen)}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "border border-border hover:border-ring",
                filterStatus !== null
                  ? filterStatus === "failed"
                    ? "bg-destructive/20 text-destructive"
                    : "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              {filterStatus === "completed" ? <CheckCircle className="w-4 h-4" /> :
               filterStatus === "failed" ? <AlertCircle className="w-4 h-4" /> :
               filterStatus === "in_progress" ? <Loader2 className="w-4 h-4" /> :
               <Clock className="w-4 h-4" />}
              <span className="hidden sm:inline">{getStatusFilterLabel()}</span>
              <ChevronDown className={cn("w-3 h-3 transition-transform", isStatusFilterOpen && "rotate-180")} />
            </button>

            <AnimatePresence>
              {isStatusFilterOpen && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="absolute z-50 top-full left-0 mt-1 min-w-[180px] glass rounded-lg border border-border shadow-xl overflow-hidden"
                >
                  <div className="max-h-64 overflow-y-auto">
                    <button
                      onClick={() => { setFilterStatus(null); setIsStatusFilterOpen(false); }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterStatus === null ? "bg-muted text-foreground" : "text-foreground hover:bg-muted"
                      )}
                    >
                      <Clock className="w-4 h-4" />
                      <span className="flex-1 text-left">All Status</span>
                      {filterStatus === null && <CheckCircle className="w-4 h-4" />}
                    </button>

                    <div className="border-t border-border my-1" />

                    {["completed", "in_progress", "pending", "failed"].map((status) => (
                      <button
                        key={status}
                        onClick={() => { setFilterStatus(status); setIsStatusFilterOpen(false); }}
                        className={cn(
                          "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                          filterStatus === status
                            ? status === "failed" ? "bg-destructive/10 text-destructive" : "bg-muted text-foreground"
                            : "text-foreground hover:bg-muted"
                        )}
                      >
                        {status === "completed" ? <CheckCircle className="w-4 h-4" /> :
                         status === "failed" ? <AlertCircle className="w-4 h-4 text-destructive" /> :
                         status === "in_progress" ? <Loader2 className="w-4 h-4" /> :
                         <Clock className="w-4 h-4" />}
                        <span className="flex-1 text-left capitalize">{status === "in_progress" ? "In Progress" : status}</span>
                        <span className="text-xs text-muted-foreground">
                          {statusCounts[status as keyof typeof statusCounts]}
                        </span>
                        {filterStatus === status && <CheckCircle className="w-4 h-4" />}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {(filterCollectionId !== null || filterStatus !== null) && (
            <button
              onClick={() => { setFilterCollectionId(null); setFilterStatus(null); }}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
            >
              <X className="w-3 h-3" />
              Clear Filters
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={toggleSelectAll}
            disabled={filteredDocuments.length === 0}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-all disabled:opacity-30"
          >
            {allFilteredSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
            {allFilteredSelected ? "Deselect All" : "Select All"}
          </button>

          {inProgressDocuments.length > 0 && (
            <button
              onClick={() => setSelectedIds(new Set(inProgressDocuments.map((d) => d.id)))}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
            >
              <Loader2 className="w-4 h-4" />
              Select In-Progress ({inProgressDocuments.length})
            </button>
          )}

          {failedDocuments.length > 0 && (
            <button
              onClick={selectAllFailed}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-destructive/70 hover:text-destructive hover:bg-destructive/10 transition-all"
            >
              <AlertCircle className="w-4 h-4" />
              Select Failed ({failedDocuments.length})
            </button>
          )}

          {selectedIds.size > 0 && (
            <>
              <button
                onClick={handleReprocessSelected}
                disabled={isReprocessing}
                className={cn(
                  "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                  "bg-muted text-foreground hover:bg-border",
                  isReprocessing && "opacity-50 cursor-not-allowed"
                )}
              >
                {isReprocessing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                Reprocess
              </button>

              {selectedInProgressCount > 0 && (
                <button
                  onClick={handleRestartSelected}
                  disabled={isReprocessing}
                  className={cn(
                    "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                    "bg-muted text-foreground hover:bg-border",
                    isReprocessing && "opacity-50 cursor-not-allowed"
                  )}
                >
                  {isReprocessing ? <Loader2 className="w-4 h-4 animate-spin" /> : <StopCircle className="w-4 h-4" />}
                  Restart ({selectedInProgressCount})
                </button>
              )}

              {availableTargetCollections.length > 0 && (
                <div ref={moveRef} className="relative">
                  <button
                    onClick={() => setIsMoveOpen(!isMoveOpen)}
                    disabled={isMoving}
                    className={cn(
                      "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                      "bg-muted text-foreground hover:bg-border",
                      isMoving && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {isMoving ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
                    Move to...
                    <ChevronDown className={cn("w-3 h-3 transition-transform", isMoveOpen && "rotate-180")} />
                  </button>

                  <AnimatePresence>
                    {isMoveOpen && (
                      <motion.div
                        initial={{ opacity: 0, y: -10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -10 }}
                        className="absolute z-50 top-full right-0 mt-1 min-w-[200px] glass rounded-lg border border-border shadow-xl overflow-hidden"
                      >
                        <div className="max-h-64 overflow-y-auto">
                          <div className="px-3 py-2 text-xs text-muted-foreground border-b border-border">
                            Move {selectedIds.size} document{selectedIds.size !== 1 ? "s" : ""} to:
                          </div>
                          {availableTargetCollections.map((col) => (
                            <button
                              key={col.id}
                              onClick={() => handleMoveToCollection(col.id)}
                              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-muted transition-colors"
                            >
                              <FolderOpen className="w-4 h-4" />
                              <span className="flex-1 text-left truncate">{col.name}</span>
                              <span className="text-xs text-muted-foreground">{col.document_count}</span>
                            </button>
                          ))}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )}

              <button
                onClick={handleDeleteSelected}
                disabled={isDeletingSelected}
                className={cn(
                  "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                  "bg-destructive/20 text-destructive hover:bg-destructive/30",
                  isDeletingSelected && "opacity-50 cursor-not-allowed"
                )}
              >
                {isDeletingSelected ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                Delete ({selectedIds.size})
              </button>
            </>
          )}

          <button
            onClick={fetchDocuments}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-all"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Failed documents summary */}
      {failedDocuments.length > 0 && (
        <div className="glass rounded-lg p-4 border border-destructive/20 bg-destructive/5">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-destructive shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-medium text-destructive">
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
      {filteredDocuments.length === 0 && (filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "") && (
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
          {filteredDocuments.map((doc, index) => (
            <motion.div
              key={doc.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, x: -100 }}
              transition={{ delay: index * 0.03 }}
              className={cn(
                "glass glass-hover rounded-lg p-5 group",
                selectedIds.has(doc.id) && "ring-2 ring-accent/50 bg-accent/5",
                doc.processing_status === "failed" && "border border-destructive/20"
              )}
            >
              <div className="flex items-start gap-4">
                <button
                  onClick={() => toggleSelection(doc.id)}
                  className={cn(
                    "mt-1 w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 transition-all",
                    selectedIds.has(doc.id)
                      ? "bg-accent border-accent"
                      : "border-border hover:border-accent/50"
                  )}
                >
                  {selectedIds.has(doc.id) && (
                    <CheckCircle className="w-3 h-3 text-accent-foreground" />
                  )}
                </button>

                <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center text-2xl shrink-0">
                  {getFileTypeIcon(doc.file_type)}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h4 className="font-medium text-foreground truncate">{doc.filename}</h4>
                      <p className="text-sm text-muted-foreground mt-1">{formatDate(doc.upload_date)}</p>
                    </div>

                    <div className="flex items-center gap-1">
                      {isProcessing(doc.processing_status) && doc.file_path && (
                        <button
                          onClick={() => handleRestartDocument(doc.id)}
                          disabled={reprocessingIds.has(doc.id)}
                          className={cn(
                            "p-2 rounded-lg transition-all duration-200",
                            "hover:bg-muted text-muted-foreground hover:text-foreground",
                            reprocessingIds.has(doc.id) && "opacity-50 cursor-not-allowed"
                          )}
                          title="Restart"
                        >
                          {reprocessingIds.has(doc.id) ? <Loader2 className="w-4 h-4 animate-spin" /> : <StopCircle className="w-4 h-4" />}
                        </button>
                      )}

                      {(doc.processing_status === "failed" || doc.processing_status === "completed") && (
                        doc.file_path ? (
                          <button
                            onClick={() => handleReprocessDocument(doc.id)}
                            disabled={reprocessingIds.has(doc.id)}
                            className={cn(
                              "p-2 rounded-lg transition-all duration-200",
                              "hover:bg-muted text-muted-foreground hover:text-foreground",
                              reprocessingIds.has(doc.id) && "opacity-50 cursor-not-allowed"
                            )}
                            title="Reprocess"
                          >
                            {reprocessingIds.has(doc.id) ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                          </button>
                        ) : (
                          <>
                            <input
                              ref={(el) => { if (el) fileInputRefs.current.set(doc.id, el); }}
                              type="file"
                              className="hidden"
                              accept=".pdf,.txt,.md,.markdown"
                              onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) {
                                  handleReprocessWithFile(doc.id, file);
                                  e.target.value = "";
                                }
                              }}
                            />
                            <button
                              onClick={() => triggerFileUpload(doc.id)}
                              disabled={reprocessingIds.has(doc.id)}
                              className={cn(
                                "p-2 rounded-lg transition-all duration-200",
                                "hover:bg-muted text-muted-foreground hover:text-foreground",
                                reprocessingIds.has(doc.id) && "opacity-50 cursor-not-allowed"
                              )}
                              title="Upload file"
                            >
                              {reprocessingIds.has(doc.id) ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                            </button>
                          </>
                        )
                      )}

                      <button
                        onClick={() => handleDelete(doc.id)}
                        disabled={deletingId === doc.id}
                        className={cn(
                          "p-2 rounded-lg transition-all duration-200",
                          "opacity-0 group-hover:opacity-100",
                          "hover:bg-destructive/20 hover:text-destructive text-muted-foreground"
                        )}
                      >
                        {deletingId === doc.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 mt-3 flex-wrap">
                    <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium", getStatusColor(doc.processing_status))}>
                      {getStatusIcon(doc.processing_status)}
                      {doc.processing_status}
                    </span>
                    <span className="text-xs text-muted-foreground">{formatBytes(doc.file_size)}</span>
                    {doc.chunk_count > 0 && <span className="text-xs text-muted-foreground">{doc.chunk_count} chunks</span>}
                    {doc.collection_name && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-muted text-muted-foreground">
                        <FolderOpen className="w-3 h-3" />
                        {doc.collection_name}
                      </span>
                    )}
                  </div>

                  {doc.processing_status === "failed" && doc.error_message && (
                    <div className="mt-3 p-3 rounded-lg bg-destructive/10 border border-destructive/20">
                      <p className="text-xs text-destructive break-words">
                        <span className="font-medium">Error: </span>{doc.error_message}
                      </p>
                    </div>
                  )}

                  {isProcessing(doc.processing_status) && doc.progress_total !== undefined && doc.progress_total > 0 && (
                    <div className="mt-3 space-y-1.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">{doc.progress_message || "Processing..."}</span>
                        <span className="text-foreground font-medium">{getProgressPercent(doc)}%</span>
                      </div>
                      <div className="h-1.5 bg-border rounded-full overflow-hidden">
                        <motion.div
                          className="h-full bg-accent rounded-full"
                          initial={{ width: 0 }}
                          animate={{ width: `${getProgressPercent(doc)}%` }}
                          transition={{ duration: 0.3 }}
                        />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
