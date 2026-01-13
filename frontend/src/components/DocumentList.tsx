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
} from "lucide-react";
import { cn, formatBytes, formatDate, getFileTypeIcon } from "@/lib/utils";

interface Document {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  upload_date: string;
  chunk_count: number;
  processing_status: string;
  error_message?: string;
  progress_current?: number;
  progress_total?: number;
  progress_message?: string;
}

interface DocumentListProps {
  onDelete: () => void;
}

export default function DocumentList({ onDelete }: DocumentListProps) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isReprocessing, setIsReprocessing] = useState(false);
  const [reprocessingIds, setReprocessingIds] = useState<Set<string>>(new Set());
  const fileInputRefs = useRef<Map<string, HTMLInputElement>>(new Map());

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

  useEffect(() => {
    fetchDocuments();
    const interval = setInterval(fetchDocuments, 5000);
    return () => clearInterval(interval);
  }, []);

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
    if (selectedIds.size === documents.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(documents.map((d) => d.id)));
    }
  };

  const selectAllFailed = () => {
    const failedIds = documents
      .filter((d) => d.processing_status === "failed")
      .map((d) => d.id);
    setSelectedIds(new Set(failedIds));
  };

  const handleReprocessSelected = async () => {
    if (selectedIds.size === 0) return;

    const confirmed = confirm(
      `Reprocess ${selectedIds.size} document(s)? This will clear their chunks and require re-uploading the files.`
    );
    if (!confirmed) return;

    setIsReprocessing(true);
    try {
      const res = await fetch("/api/documents/reprocess", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_ids: Array.from(selectedIds) }),
      });

      if (res.ok) {
        const data = await res.json();
        console.log("Reprocess results:", data);
        setSelectedIds(new Set());
        await fetchDocuments();
        onDelete(); // Refresh stats
      }
    } catch (error) {
      console.error("Failed to reprocess documents:", error);
    } finally {
      setIsReprocessing(false);
    }
  };

  const handleReprocessWithFile = async (docId: string, file: File) => {
    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`/api/documents/${docId}/reprocess`, {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        await fetchDocuments();
        onDelete(); // Refresh stats
      } else {
        const error = await res.json();
        alert(`Reprocess failed: ${error.detail || "Unknown error"}`);
      }
    } catch (error) {
      console.error("Failed to reprocess document:", error);
      alert("Failed to reprocess document");
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

  const failedDocuments = documents.filter((d) => d.processing_status === "failed");

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
      {/* Header with actions */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <p className="text-sm text-white/50">
            {documents.length} document{documents.length !== 1 ? "s" : ""}
            {selectedIds.size > 0 && (
              <span className="text-ocean-400 ml-2">
                ({selectedIds.size} selected)
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Select All / Deselect All */}
          <button
            onClick={toggleSelectAll}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-white/50 hover:text-white/70 hover:bg-white/5 transition-all"
          >
            {selectedIds.size === documents.length ? (
              <CheckSquare className="w-4 h-4" />
            ) : (
              <Square className="w-4 h-4" />
            )}
            {selectedIds.size === documents.length ? "Deselect All" : "Select All"}
          </button>

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
                Select failed documents and reprocess them, or use the retry button on each document.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Document list */}
      <div className="grid gap-3">
        <AnimatePresence>
          {documents.map((doc, index) => (
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
                      {/* Reprocess with file button for failed/pending documents */}
                      {(doc.processing_status === "failed" || doc.processing_status === "pending") && (
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
                            title="Retry with file"
                          >
                            {reprocessingIds.has(doc.id) ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Upload className="w-4 h-4" />
                            )}
                          </button>
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
                  {isProcessing(doc.processing_status) && doc.progress_total > 0 && (
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
