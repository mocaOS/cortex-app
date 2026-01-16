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
  file_path?: string | null;  // Path to stored original file
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

  // Close dropdowns when clicking outside
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

  // Filtered documents based on collection filter, status filter, and search query
  const filteredDocuments = documents.filter((doc) => {
    // Collection filter
    const matchesCollection = 
      filterCollectionId === null ||
      (filterCollectionId === "none" ? !doc.collection_id : doc.collection_id === filterCollectionId);
    
    // Status filter
    const matchesStatus = (() => {
      if (filterStatus === null) return true;
      if (filterStatus === "in_progress") {
        return doc.processing_status === "processing" || doc.processing_status === "extracting";
      }
      return doc.processing_status === filterStatus;
    })();
    
    // Search filter (case-insensitive search on filename)
    const matchesSearch = 
      searchQuery.trim() === "" ||
      doc.filename.toLowerCase().includes(searchQuery.toLowerCase().trim());
    
    return matchesCollection && matchesStatus && matchesSearch;
  });

  // Get filter label
  const getFilterLabel = () => {
    if (filterCollectionId === null) return "All Collections";
    if (filterCollectionId === "none") return "No Collection";
    const col = collections.find((c) => c.id === filterCollectionId);
    return col?.name || "Unknown";
  };

  // Get status filter label
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

  // Get status counts for filter badges
  const statusCounts = {
    completed: documents.filter((d) => d.processing_status === "completed").length,
    in_progress: documents.filter((d) => ["processing", "extracting"].includes(d.processing_status)).length,
    pending: documents.filter((d) => d.processing_status === "pending").length,
    failed: documents.filter((d) => d.processing_status === "failed").length,
  };

  // Get available collections to move to (exclude current filter if it's a specific collection)
  const availableTargetCollections = collections.filter(
    (c) => filterCollectionId === null || filterCollectionId === "none" || c.id !== filterCollectionId
  );

  // Clear selected IDs that no longer exist
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
      // Deselect all filtered
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        filteredIds.forEach((id) => newSet.delete(id));
        return newSet;
      });
    } else {
      // Select all filtered
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
      const result = await api.moveDocumentsToCollection(selectedArray, targetCollectionId);
      console.log("Move results:", result);
      setSelectedIds(new Set());
      await fetchDocuments();
      await fetchCollections();
      onDelete(); // Refresh stats
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
      console.log("Reprocess results:", result);
      
      // Check for any errors
      const errors = result.results.filter(r => r.status === "error");
      if (errors.length > 0) {
        const errorMsgs = errors.map(e => `${e.document_id}: ${e.message}`).join("\n");
        alert(`Some documents failed to reprocess:\n${errorMsgs}`);
      }
      
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete(); // Refresh stats
    } catch (error) {
      console.error("Failed to reprocess documents:", error);
      alert(`Failed to reprocess: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsReprocessing(false);
    }
  };

  // Restart selected in-progress documents from scratch
  const handleRestartSelected = async () => {
    // Get only in-progress documents with stored files
    const inProgressSelected = documents.filter(
      (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
    );
    
    if (inProgressSelected.length === 0) return;

    const confirmed = confirm(
      `Restart ${inProgressSelected.length} in-progress document(s)? This will cancel current processing and start from scratch.`
    );
    if (!confirmed) return;

    setIsReprocessing(true);
    try {
      const docIds = inProgressSelected.map((d) => d.id);
      const result = await api.reprocessDocuments(docIds);
      console.log("Restart results:", result);
      
      // Check for any errors
      const errors = result.results.filter(r => r.status === "error");
      if (errors.length > 0) {
        const errorMsgs = errors.map(e => `${e.document_id}: ${e.message}`).join("\n");
        alert(`Some documents failed to restart:\n${errorMsgs}`);
      }
      
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete(); // Refresh stats
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
      `Are you sure you want to delete ${selectedIds.size} document(s)? This action cannot be undone.`
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
        const data = await res.json();
        console.log("Delete results:", data);
        setSelectedIds(new Set());
        await fetchDocuments();
        onDelete(); // Refresh stats
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
      onDelete(); // Refresh stats
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

  // Reprocess using stored file (no upload needed)
  const handleReprocessDocument = async (docId: string) => {
    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId);
      await fetchDocuments();
      onDelete(); // Refresh stats
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

  // Restart in-progress document (cancel current processing and start fresh)
  const handleRestartDocument = async (docId: string) => {
    const confirmed = confirm(
      "Restart this document? This will cancel current processing and start from scratch."
    );
    if (!confirmed) return;

    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId);
      await fetchDocuments();
      onDelete(); // Refresh stats
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
    if (input) {
      input.click();
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="w-4 h-4 text-mint-400" />;
      case "processing":
      case "extracting":
        return <Loader2 className="w-4 h-4 text-ocean-400 animate-spin" />;
      case "failed":
        return <AlertCircle className="w-4 h-4 text-coral-400" />;
      default:
        return <Clock className="w-4 h-4 text-white/40" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed":
        return "bg-mint-500/20 text-mint-400";
      case "processing":
        return "bg-ocean-500/20 text-ocean-400";
      case "extracting":
        return "bg-cyan-500/20 text-cyan-400";
      case "failed":
        return "bg-coral-500/20 text-coral-400";
      default:
        return "bg-white/10 text-white/50";
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
  const selectedInFilter = filteredDocuments.filter((d) => selectedIds.has(d.id)).length;

  if (isLoading) {
    return (
      <div className="glass rounded-xl p-12 text-center">
        <Loader2 className="w-8 h-8 text-ocean-400 animate-spin mx-auto mb-4" />
        <p className="text-white/50">Loading documents...</p>
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="glass rounded-xl p-12 text-center">
        <div className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-ocean-500/20 to-cyan-500/20 flex items-center justify-center mb-6">
          <FileText className="w-8 h-8 text-ocean-400/60" />
        </div>
        <h3 className="text-lg font-medium text-white/70 mb-2">
          No Documents Yet
        </h3>
        <p className="text-white/40 max-w-md mx-auto">
          Upload your first document to start building your knowledge base.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/30" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search documents by filename..."
          className={cn(
            "w-full pl-10 pr-10 py-2.5 rounded-xl text-sm",
            "bg-white/5 border border-white/10",
            "text-white placeholder:text-white/30",
            "focus:outline-none focus:border-ocean-500/50 focus:ring-1 focus:ring-ocean-500/20",
            "transition-all"
          )}
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded-full text-white/30 hover:text-white/50 hover:bg-white/5 transition-colors"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>

      {/* Header with actions */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <p className="text-sm text-white/50">
            {filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "" ? (
              <>
                {filteredDocuments.length} of {documents.length} document{documents.length !== 1 ? "s" : ""}
              </>
            ) : (
              <>
                {documents.length} document{documents.length !== 1 ? "s" : ""}
              </>
            )}
            {selectedIds.size > 0 && (
              <span className="text-ocean-400 ml-2">
                ({selectedIds.size} selected)
              </span>
            )}
          </p>

          {/* Collection Filter Dropdown */}
          <div ref={filterRef} className="relative">
            <button
              onClick={() => setIsFilterOpen(!isFilterOpen)}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "border border-white/10 hover:border-white/20",
                filterCollectionId !== null
                  ? "bg-ocean-500/20 text-ocean-400 border-ocean-500/30"
                  : "text-white/50 hover:text-white/70 hover:bg-white/5"
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
                  className="absolute z-50 top-full left-0 mt-1 min-w-[200px] glass rounded-lg border border-white/10 shadow-xl overflow-hidden"
                >
                  <div className="max-h-64 overflow-y-auto">
                    {/* All Collections */}
                    <button
                      onClick={() => {
                        setFilterCollectionId(null);
                        setIsFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterCollectionId === null
                          ? "bg-ocean-500/10 text-ocean-400"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <FolderOpen className="w-4 h-4" />
                      <span className="flex-1 text-left">All Collections</span>
                      {filterCollectionId === null && <CheckCircle className="w-4 h-4" />}
                    </button>

                    {/* No Collection */}
                    <button
                      onClick={() => {
                        setFilterCollectionId("none");
                        setIsFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterCollectionId === "none"
                          ? "bg-ocean-500/10 text-ocean-400"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <X className="w-4 h-4" />
                      <span className="flex-1 text-left">No Collection</span>
                      <span className="text-xs text-white/30">
                        {documents.filter((d) => !d.collection_id).length}
                      </span>
                      {filterCollectionId === "none" && <CheckCircle className="w-4 h-4" />}
                    </button>

                    {/* Divider */}
                    {collections.length > 0 && <div className="border-t border-white/5 my-1" />}

                    {/* Collection options */}
                    {collections.map((col) => (
                      <button
                        key={col.id}
                        onClick={() => {
                          setFilterCollectionId(col.id);
                          setIsFilterOpen(false);
                        }}
                        className={cn(
                          "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                          filterCollectionId === col.id
                            ? "bg-ocean-500/10 text-ocean-400"
                            : "text-white/70 hover:bg-white/5"
                        )}
                      >
                        <FolderOpen className="w-4 h-4" />
                        <span className="flex-1 text-left truncate">{col.name}</span>
                        <span className="text-xs text-white/30">{col.document_count}</span>
                        {filterCollectionId === col.id && <CheckCircle className="w-4 h-4" />}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Status Filter Dropdown */}
          <div ref={statusFilterRef} className="relative">
            <button
              onClick={() => setIsStatusFilterOpen(!isStatusFilterOpen)}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "border border-white/10 hover:border-white/20",
                filterStatus !== null
                  ? filterStatus === "failed"
                    ? "bg-coral-500/20 text-coral-400 border-coral-500/30"
                    : filterStatus === "in_progress"
                    ? "bg-ocean-500/20 text-ocean-400 border-ocean-500/30"
                    : filterStatus === "pending"
                    ? "bg-white/10 text-white/70 border-white/20"
                    : "bg-mint-500/20 text-mint-400 border-mint-500/30"
                  : "text-white/50 hover:text-white/70 hover:bg-white/5"
              )}
            >
              {filterStatus === "completed" ? (
                <CheckCircle className="w-4 h-4" />
              ) : filterStatus === "failed" ? (
                <AlertCircle className="w-4 h-4" />
              ) : filterStatus === "in_progress" ? (
                <Loader2 className="w-4 h-4" />
              ) : filterStatus === "pending" ? (
                <Clock className="w-4 h-4" />
              ) : (
                <Clock className="w-4 h-4" />
              )}
              <span className="hidden sm:inline">{getStatusFilterLabel()}</span>
              <ChevronDown className={cn("w-3 h-3 transition-transform", isStatusFilterOpen && "rotate-180")} />
            </button>

            <AnimatePresence>
              {isStatusFilterOpen && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="absolute z-50 top-full left-0 mt-1 min-w-[180px] glass rounded-lg border border-white/10 shadow-xl overflow-hidden"
                >
                  <div className="max-h-64 overflow-y-auto">
                    {/* All Status */}
                    <button
                      onClick={() => {
                        setFilterStatus(null);
                        setIsStatusFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterStatus === null
                          ? "bg-ocean-500/10 text-ocean-400"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <Clock className="w-4 h-4" />
                      <span className="flex-1 text-left">All Status</span>
                      {filterStatus === null && <CheckCircle className="w-4 h-4" />}
                    </button>

                    <div className="border-t border-white/5 my-1" />

                    {/* Completed */}
                    <button
                      onClick={() => {
                        setFilterStatus("completed");
                        setIsStatusFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterStatus === "completed"
                          ? "bg-mint-500/10 text-mint-400"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <CheckCircle className="w-4 h-4 text-mint-400" />
                      <span className="flex-1 text-left">Completed</span>
                      <span className="text-xs text-white/30">{statusCounts.completed}</span>
                      {filterStatus === "completed" && <CheckCircle className="w-4 h-4" />}
                    </button>

                    {/* In Progress */}
                    <button
                      onClick={() => {
                        setFilterStatus("in_progress");
                        setIsStatusFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterStatus === "in_progress"
                          ? "bg-ocean-500/10 text-ocean-400"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <Loader2 className="w-4 h-4 text-ocean-400" />
                      <span className="flex-1 text-left">In Progress</span>
                      <span className="text-xs text-white/30">{statusCounts.in_progress}</span>
                      {filterStatus === "in_progress" && <CheckCircle className="w-4 h-4" />}
                    </button>

                    {/* Pending */}
                    <button
                      onClick={() => {
                        setFilterStatus("pending");
                        setIsStatusFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterStatus === "pending"
                          ? "bg-white/10 text-white/80"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <Clock className="w-4 h-4 text-white/40" />
                      <span className="flex-1 text-left">Pending</span>
                      <span className="text-xs text-white/30">{statusCounts.pending}</span>
                      {filterStatus === "pending" && <CheckCircle className="w-4 h-4" />}
                    </button>

                    {/* Failed */}
                    <button
                      onClick={() => {
                        setFilterStatus("failed");
                        setIsStatusFilterOpen(false);
                      }}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        filterStatus === "failed"
                          ? "bg-coral-500/10 text-coral-400"
                          : "text-white/70 hover:bg-white/5"
                      )}
                    >
                      <AlertCircle className="w-4 h-4 text-coral-400" />
                      <span className="flex-1 text-left">Failed</span>
                      <span className="text-xs text-white/30">{statusCounts.failed}</span>
                      {filterStatus === "failed" && <CheckCircle className="w-4 h-4" />}
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Clear Filters */}
          {(filterCollectionId !== null || filterStatus !== null) && (
            <button
              onClick={() => {
                setFilterCollectionId(null);
                setFilterStatus(null);
              }}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-white/40 hover:text-white/60 hover:bg-white/5 transition-all"
            >
              <X className="w-3 h-3" />
              Clear Filters
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Select All / Deselect All (for filtered) */}
          <button
            onClick={toggleSelectAll}
            disabled={filteredDocuments.length === 0}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-white/50 hover:text-white/70 hover:bg-white/5 transition-all disabled:opacity-30"
          >
            {allFilteredSelected ? (
              <CheckSquare className="w-4 h-4" />
            ) : (
              <Square className="w-4 h-4" />
            )}
            {allFilteredSelected ? "Deselect All" : "Select All"}
            {filterCollectionId !== null && filteredDocuments.length > 0 && (
              <span className="text-xs text-white/30">({filteredDocuments.length})</span>
            )}
          </button>

          {/* Select All In-Progress */}
          {inProgressDocuments.length > 0 && (
            <button
              onClick={() => {
                const inProgressIds = inProgressDocuments.map((d) => d.id);
                setSelectedIds(new Set(inProgressIds));
              }}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-amber-400/70 hover:text-amber-400 hover:bg-amber-500/10 transition-all"
            >
              <Loader2 className="w-4 h-4" />
              Select In-Progress ({inProgressDocuments.length})
            </button>
          )}

          {/* Select All Failed */}
          {failedDocuments.length > 0 && (
            <button
              onClick={selectAllFailed}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-coral-400/70 hover:text-coral-400 hover:bg-coral-500/10 transition-all"
            >
              <AlertCircle className="w-4 h-4" />
              Select Failed ({failedDocuments.length})
            </button>
          )}

          {/* Reprocess Selected */}
          {selectedIds.size > 0 && (
            <button
              onClick={handleReprocessSelected}
              disabled={isReprocessing}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "bg-ocean-500/20 text-ocean-400 hover:bg-ocean-500/30",
                isReprocessing && "opacity-50 cursor-not-allowed"
              )}
            >
              {isReprocessing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RotateCcw className="w-4 h-4" />
              )}
              Reprocess Selected
            </button>
          )}

          {/* Restart In-Progress Selected */}
          {selectedInProgressCount > 0 && (
            <button
              onClick={handleRestartSelected}
              disabled={isReprocessing}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "bg-amber-500/20 text-amber-400 hover:bg-amber-500/30",
                isReprocessing && "opacity-50 cursor-not-allowed"
              )}
            >
              {isReprocessing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <StopCircle className="w-4 h-4" />
              )}
              Restart In-Progress ({selectedInProgressCount})
            </button>
          )}

          {/* Move to Collection */}
          {selectedIds.size > 0 && availableTargetCollections.length > 0 && (
            <div ref={moveRef} className="relative">
              <button
                onClick={() => setIsMoveOpen(!isMoveOpen)}
                disabled={isMoving}
                className={cn(
                  "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                  "bg-purple-500/20 text-purple-400 hover:bg-purple-500/30",
                  isMoving && "opacity-50 cursor-not-allowed"
                )}
              >
                {isMoving ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <ArrowRight className="w-4 h-4" />
                )}
                Move to...
                <ChevronDown className={cn("w-3 h-3 transition-transform", isMoveOpen && "rotate-180")} />
              </button>

              <AnimatePresence>
                {isMoveOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                    className="absolute z-50 top-full right-0 mt-1 min-w-[200px] glass rounded-lg border border-white/10 shadow-xl overflow-hidden"
                  >
                    <div className="max-h-64 overflow-y-auto">
                      <div className="px-3 py-2 text-xs text-white/40 border-b border-white/5">
                        Move {selectedIds.size} document{selectedIds.size !== 1 ? "s" : ""} to:
                      </div>
                      {availableTargetCollections.map((col) => (
                        <button
                          key={col.id}
                          onClick={() => handleMoveToCollection(col.id)}
                          className="flex items-center gap-2 w-full px-3 py-2 text-sm text-white/70 hover:bg-purple-500/10 hover:text-purple-400 transition-colors"
                        >
                          <FolderOpen className="w-4 h-4" />
                          <span className="flex-1 text-left truncate">{col.name}</span>
                          <span className="text-xs text-white/30">{col.document_count}</span>
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

          {/* Delete Selected */}
          {selectedIds.size > 0 && (
            <button
              onClick={handleDeleteSelected}
              disabled={isDeletingSelected}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all",
                "bg-coral-500/20 text-coral-400 hover:bg-coral-500/30",
                isDeletingSelected && "opacity-50 cursor-not-allowed"
              )}
            >
              {isDeletingSelected ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              Delete Selected ({selectedIds.size})
            </button>
          )}

          {/* Refresh */}
          <button
            onClick={fetchDocuments}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-white/50 hover:text-white/70 hover:bg-white/5 transition-all"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Failed documents summary */}
      {failedDocuments.length > 0 && (
        <div className="glass rounded-xl p-4 border border-coral-500/20 bg-coral-500/5">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-coral-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-medium text-coral-400">
                {failedDocuments.length} document{failedDocuments.length !== 1 ? "s" : ""} failed to process
              </h4>
              <p className="text-xs text-white/40 mt-0.5">
                Click the <RotateCcw className="w-3 h-3 inline mx-0.5" /> button to reprocess, or select multiple and use &quot;Reprocess Selected&quot;.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* No results message */}
      {filteredDocuments.length === 0 && (filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "") && (
        <div className="glass rounded-xl p-8 text-center">
          {searchQuery.trim() !== "" ? (
            <>
              <Search className="w-12 h-12 text-white/20 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-white/70 mb-2">
                No Documents Found
              </h3>
              <p className="text-white/40">
                No documents match &quot;{searchQuery}&quot;
                {filterCollectionId !== null && (
                  <> in the selected collection</>
                )}
                {filterStatus !== null && (
                  <> with status &quot;{getStatusFilterLabel()}&quot;</>
                )}
                . Try a different search term.
              </p>
            </>
          ) : filterStatus !== null ? (
            <>
              {filterStatus === "failed" ? (
                <AlertCircle className="w-12 h-12 text-coral-400/40 mx-auto mb-4" />
              ) : filterStatus === "in_progress" ? (
                <Loader2 className="w-12 h-12 text-ocean-400/40 mx-auto mb-4" />
              ) : filterStatus === "pending" ? (
                <Clock className="w-12 h-12 text-white/20 mx-auto mb-4" />
              ) : (
                <CheckCircle className="w-12 h-12 text-mint-400/40 mx-auto mb-4" />
              )}
              <h3 className="text-lg font-medium text-white/70 mb-2">
                No {getStatusFilterLabel()} Documents
              </h3>
              <p className="text-white/40">
                {filterStatus === "failed"
                  ? "Great! No documents have failed processing."
                  : filterStatus === "in_progress"
                  ? "No documents are currently being processed."
                  : filterStatus === "pending"
                  ? "No documents are waiting in the queue."
                  : "No documents match the selected status filter."}
              </p>
            </>
          ) : (
            <>
              <FolderOpen className="w-12 h-12 text-white/20 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-white/70 mb-2">
                No Documents in This Collection
              </h3>
              <p className="text-white/40">
                {filterCollectionId === "none"
                  ? "All documents have been assigned to collections."
                  : "Try selecting a different collection or upload documents to this collection."}
              </p>
            </>
          )}
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
                "glass glass-hover rounded-xl p-5 group",
                selectedIds.has(doc.id) && "ring-2 ring-ocean-400/50 bg-ocean-500/5",
                doc.processing_status === "failed" && "border border-coral-500/20"
              )}
            >
              <div className="flex items-start gap-4">
                {/* Checkbox */}
                <button
                  onClick={() => toggleSelection(doc.id)}
                  className={cn(
                    "mt-1 w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 transition-all",
                    selectedIds.has(doc.id)
                      ? "bg-ocean-500 border-ocean-500"
                      : "border-white/20 hover:border-white/40"
                  )}
                >
                  {selectedIds.has(doc.id) && (
                    <CheckCircle className="w-3 h-3 text-white" />
                  )}
                </button>

                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-ocean-500/20 to-cyan-500/20 flex items-center justify-center text-2xl shrink-0">
                  {getFileTypeIcon(doc.file_type)}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h4 className="font-medium text-white/90 truncate">
                        {doc.filename}
                      </h4>
                      <p className="text-sm text-white/40 mt-1">
                        {formatDate(doc.upload_date)}
                      </p>
                    </div>

                    <div className="flex items-center gap-1">
                      {/* Restart button for in-progress documents */}
                      {isProcessing(doc.processing_status) && doc.file_path && (
                        <button
                          onClick={() => handleRestartDocument(doc.id)}
                          disabled={reprocessingIds.has(doc.id)}
                          className={cn(
                            "p-2 rounded-lg transition-all duration-200",
                            "hover:bg-amber-500/20 hover:text-amber-400",
                            "text-white/40",
                            reprocessingIds.has(doc.id) && "opacity-50 cursor-not-allowed"
                          )}
                          title="Restart processing from scratch"
                        >
                          {reprocessingIds.has(doc.id) ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <StopCircle className="w-4 h-4" />
                          )}
                        </button>
                      )}

                      {/* Reprocess button for failed/completed documents */}
                      {(doc.processing_status === "failed" || doc.processing_status === "completed") && (
                        <>
                          {/* If file is stored, show simple reprocess button */}
                          {doc.file_path ? (
                            <button
                              onClick={() => handleReprocessDocument(doc.id)}
                              disabled={reprocessingIds.has(doc.id)}
                              className={cn(
                                "p-2 rounded-lg transition-all duration-200",
                                "hover:bg-ocean-500/20 hover:text-ocean-400",
                                "text-white/40",
                                reprocessingIds.has(doc.id) && "opacity-50 cursor-not-allowed"
                              )}
                              title="Reprocess document"
                            >
                              {reprocessingIds.has(doc.id) ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                              ) : (
                                <RotateCcw className="w-4 h-4" />
                              )}
                            </button>
                          ) : (
                            /* No stored file - show upload button for legacy documents */
                            <>
                              <input
                                ref={(el) => {
                                  if (el) fileInputRefs.current.set(doc.id, el);
                                }}
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
                                  "hover:bg-ocean-500/20 hover:text-ocean-400",
                                  "text-white/40",
                                  reprocessingIds.has(doc.id) && "opacity-50 cursor-not-allowed"
                                )}
                                title="Retry with file (no stored file)"
                              >
                                {reprocessingIds.has(doc.id) ? (
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                  <Upload className="w-4 h-4" />
                                )}
                              </button>
                            </>
                          )}
                        </>
                      )}

                      <button
                        onClick={() => handleDelete(doc.id)}
                        disabled={deletingId === doc.id}
                        className={cn(
                          "p-2 rounded-lg transition-all duration-200",
                          "opacity-0 group-hover:opacity-100",
                          "hover:bg-coral-500/20 hover:text-coral-400",
                          "text-white/40"
                        )}
                      >
                        {deletingId === doc.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Trash2 className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 mt-3 flex-wrap">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
                        getStatusColor(doc.processing_status)
                      )}
                    >
                      {getStatusIcon(doc.processing_status)}
                      {doc.processing_status}
                    </span>

                    <span className="text-xs text-white/30">
                      {formatBytes(doc.file_size)}
                    </span>

                    {doc.chunk_count > 0 && (
                      <span className="text-xs text-white/30">
                        {doc.chunk_count} chunks
                      </span>
                    )}

                    {/* Collection badge */}
                    {doc.collection_name && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-purple-500/20 text-purple-400">
                        <FolderOpen className="w-3 h-3" />
                        {doc.collection_name}
                      </span>
                    )}
                  </div>

                  {/* Error message for failed documents */}
                  {doc.processing_status === "failed" && doc.error_message && (
                    <div className="mt-3 p-3 rounded-lg bg-coral-500/10 border border-coral-500/20">
                      <p className="text-xs text-coral-400 break-words">
                        <span className="font-medium">Error: </span>
                        {doc.error_message}
                      </p>
                    </div>
                  )}

                  {/* Progress bar for processing documents */}
                  {isProcessing(doc.processing_status) && doc.progress_total !== undefined && doc.progress_total > 0 && (
                    <div className="mt-3 space-y-1.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-white/50">{doc.progress_message || "Processing..."}</span>
                        <span className="text-ocean-400 font-medium">{getProgressPercent(doc)}%</span>
                      </div>
                      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                        <motion.div
                          className={cn(
                            "h-full rounded-full",
                            doc.processing_status === "extracting" 
                              ? "bg-gradient-to-r from-cyan-500 to-teal-400"
                              : "bg-gradient-to-r from-ocean-500 to-cyan-400"
                          )}
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
