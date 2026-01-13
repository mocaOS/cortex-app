"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  Trash2,
  Loader2,
  CheckCircle,
  Clock,
  AlertCircle,
  RefreshCw,
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

  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this document?")) return;

    setDeletingId(id);
    try {
      const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
      if (res.ok) {
        setDocuments((prev) => prev.filter((d) => d.id !== id));
        onDelete();
      }
    } catch (error) {
      console.error("Failed to delete document:", error);
    } finally {
      setDeletingId(null);
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
      <div className="flex items-center justify-between">
        <p className="text-sm text-white/50">
          {documents.length} document{documents.length !== 1 ? "s" : ""}
        </p>
        <button
          onClick={fetchDocuments}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-white/50 hover:text-white/70 hover:bg-white/5 transition-all"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      <div className="grid gap-3">
        <AnimatePresence>
          {documents.map((doc, index) => (
            <motion.div
              key={doc.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, x: -100 }}
              transition={{ delay: index * 0.03 }}
              className="glass glass-hover rounded-xl p-5 group"
            >
              <div className="flex items-start gap-4">
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

                    {doc.error_message && (
                      <span className="text-xs text-coral-400/70 truncate max-w-[200px]">
                        {doc.error_message}
                      </span>
                    )}
                  </div>

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
