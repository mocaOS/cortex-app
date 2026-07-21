"use client";

import { useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  FileSpreadsheet,
  FileImage,
  File,
  Trash2,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  RefreshCw,
  Upload,
  Play,
  PenLine,
  Eye,
  Download,
  BookOpen,
  X,
  AlertTriangle,
  ShieldAlert,
  CirclePause,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { IngestionStepper } from "./IngestionStepper";

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
  source?: string;
  entity_count?: number;
  unembedded_chunk_count?: number;
  injection_flagged?: boolean;
  injection_reason?: string;
  processing_paused?: boolean;
  paused_reason?: string;
  resume_available?: boolean;
}

interface DocumentCardProps {
  doc: Document;
  index: number;
  isSelected: boolean;
  onToggleSelection: (id: string, shiftKey?: boolean) => void;
  onDelete: (id: string) => void;
  onReprocess: (id: string) => void;
  onReprocessWithFile: (id: string, file: File) => void;
  onRestart: (id: string) => void;
  isDeleting: boolean;
  isReprocessing: boolean;
}

const isProcessing = (status: string, doc?: Document) => {
  if (status === "processing" || status === "extracting" || status === "pending") return true;
  if (status === "completed" && doc) {
    const hasImages = (doc.image_progress_total ?? 0) > 0;
    const imagesDone = hasImages && doc.image_progress_current === doc.image_progress_total;
    if (hasImages && !imagesDone) return true;
  }
  return false;
};

// Degraded: completed (and past image analysis) but extraction produced 0
// entities, or chunks are missing embeddings. entity_count must be exactly 0 —
// the backend sends -1 for "unknown" (extraction disabled / pre-backfill).
const isDegradedDoc = (doc: Document): boolean => {
  if (doc.processing_status !== "completed") return false;
  const total = doc.image_progress_total ?? 0;
  if (total > 0 && doc.image_progress_current !== total) return false;
  return doc.entity_count === 0 || (doc.unembedded_chunk_count ?? 0) > 0;
};

const getDegradedReason = (doc: Document): string | null => {
  if (!isDegradedDoc(doc)) return null;
  const reasons: string[] = [];
  if (doc.entity_count === 0) reasons.push("0 entities extracted");
  const missing = doc.unembedded_chunk_count ?? 0;
  if (missing > 0) {
    reasons.push(`${missing} chunk${missing !== 1 ? "s" : ""} missing embeddings`);
  }
  return reasons.join(" · ");
};

// Flagged by the ingestion prompt-injection scan (non-blocking — the document
// is still ingested; this is a visibility signal for operators).
const isInjectionFlagged = (doc: Document): boolean => doc.injection_flagged === true;

const getFileIcon = (fileType: string, isCustomInput?: boolean) => {
  if (isCustomInput) return PenLine;
  if (fileType.includes("pdf")) return FileText;
  if (fileType.includes("epub")) return BookOpen;
  if (fileType.includes("spreadsheet") || fileType.includes("excel") || fileType.includes("csv"))
    return FileSpreadsheet;
  if (fileType.includes("image")) return FileImage;
  return File;
};

const getStatusConfig = (status: string, doc?: Document) => {
  // Parked on the processing-slot semaphore: nothing is happening yet by
  // design (a burst of API ingests queues instead of fanning out) — show it
  // as waiting, not working.
  if ((status === "processing" || status === "extracting") && doc?.processing_queued) {
    return {
      icon: CirclePause,
      color: "text-muted-foreground",
      bgColor: "bg-foreground/5",
      label: "Queued",
    };
  }
  // Live outage pause: processing is alive but waiting for the LLM endpoint
  // to come back — the run continues automatically once it does.
  if ((status === "processing" || status === "extracting") && doc?.processing_paused) {
    return {
      icon: CirclePause,
      color: "text-amber-400",
      bgColor: "bg-amber-500/10",
      label: "Paused",
    };
  }
  // Failed with a surviving checkpoint: a reprocess resumes where it
  // stopped instead of starting over.
  if (status === "failed" && doc?.resume_available) {
    return {
      icon: CirclePause,
      color: "text-amber-400",
      bgColor: "bg-amber-500/10",
      label: "Interrupted",
    };
  }
  if (status === "completed" && doc) {
    const hasImages = (doc.image_progress_total ?? 0) > 0;
    const imagesDone = hasImages && doc.image_progress_current === doc.image_progress_total;
    if (hasImages && !imagesDone) {
      return {
        icon: Loader2,
        color: "text-muted-foreground",
        bgColor: "bg-muted",
        label: "Analyzing Images",
        animate: true,
      };
    }
    if (isDegradedDoc(doc)) {
      return {
        icon: AlertTriangle,
        color: "text-amber-400",
        bgColor: "bg-amber-500/10",
        label: "Degraded",
      };
    }
  }
  switch (status) {
    case "completed":
      return {
        icon: CheckCircle2,
        color: "text-accent",
        bgColor: "bg-accent/10",
        label: "Completed",
      };
    case "processing":
    case "extracting":
      return {
        icon: Loader2,
        color: "text-muted-foreground",
        bgColor: "bg-muted",
        label: status === "extracting" ? "Extracting" : "Processing",
        animate: true,
      };
    case "pending":
      return {
        icon: Clock,
        color: "text-muted-foreground",
        bgColor: "bg-muted",
        label: "Pending",
      };
    case "failed":
      return {
        icon: XCircle,
        color: "text-muted-foreground",
        bgColor: "bg-muted",
        label: "Failed",
      };
    default:
      return {
        icon: File,
        color: "text-muted-foreground",
        bgColor: "bg-muted",
        label: status,
      };
  }
};

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
};

const formatDate = (dateString: string) => {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const isMarkdownFile = (filename: string) => {
  return /\.md$/i.test(filename);
};

export function DocumentCard({
  doc,
  index,
  isSelected,
  onToggleSelection,
  onDelete,
  onReprocess,
  onReprocessWithFile,
  onRestart,
  isDeleting,
  isReprocessing,
}: DocumentCardProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showMarkdownViewer, setShowMarkdownViewer] = useState(false);
  const [markdownContent, setMarkdownContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);
  const [isFetchingFile, setIsFetchingFile] = useState(false);
  const fetchingFileRef = useRef(false);

  useBodyScrollLock(showMarkdownViewer);

  const FileIcon = getFileIcon(doc.file_type, doc.is_custom_input);
  const status = getStatusConfig(doc.processing_status, doc);
  const StatusIcon = status.icon;
  const isCustomInput = doc.is_custom_input === true;
  const degradedReason = getDegradedReason(doc);
  const isPaused =
    (doc.processing_status === "processing" || doc.processing_status === "extracting") &&
    doc.processing_paused === true;
  const isInterrupted = doc.processing_status === "failed" && doc.resume_available === true;

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onReprocessWithFile(doc.id, file);
    }
    e.target.value = "";
  };

  const handleView = useCallback(async () => {
    if (!doc.file_path) return;

    if (isMarkdownFile(doc.filename)) {
      setLoadingContent(true);
      setShowMarkdownViewer(true);
      try {
        const blob = await api.getDocumentFileBlob(doc.id);
        setMarkdownContent(await blob.text());
      } catch {
        setMarkdownContent("*Failed to load document content.*");
      } finally {
        setLoadingContent(false);
      }
    } else {
      // Non-markdown files (PDF, DOCX, …) don't render reliably from a blob
      // tab — download them so the user's system opens them with the
      // default application. window.open can't send the X-API-Key header,
      // so fetch as an authenticated blob first.
      if (fetchingFileRef.current) return;
      fetchingFileRef.current = true;
      setIsFetchingFile(true);
      try {
        const blob = await api.getDocumentFileBlob(doc.id);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = doc.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } catch {
        /* button stays usable; nothing to show on failure */
      } finally {
        fetchingFileRef.current = false;
        setIsFetchingFile(false);
      }
    }
  }, [doc.id, doc.filename, doc.file_path]);

  const imageProgressPercent =
    doc.image_progress_current && doc.image_progress_total
      ? Math.round((doc.image_progress_current / doc.image_progress_total) * 100)
      : 0;

  const hasImageProgress = (doc.image_progress_total ?? 0) > 0;
  const imageAnalysisDone = hasImageProgress && doc.image_progress_current === doc.image_progress_total;

  return (
    <>
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ delay: index * 0.02 }}
      className={cn(
        "glass rounded-lg p-4 transition-all duration-200 border",
        isSelected ? "border-muted-foreground" : "border-border"
      )}
    >
      <div className="flex items-start gap-4">
        {/* Circular checkbox */}
        <button
          onClick={(e) => onToggleSelection(doc.id, e.shiftKey)}
          className={cn(
            "w-6 h-6 rounded-full border-2 flex items-center justify-center shrink-0 mt-0.5 transition-colors",
            isSelected
              ? "bg-accent border-accent"
              : "border-muted-foreground/50 hover:border-muted-foreground"
          )}
        >
          {isSelected && (
            <CheckCircle2 className="w-4 h-4 text-background" />
          )}
        </button>

        {/* File icon */}
        <div className={cn("p-2 rounded-lg shrink-0", isCustomInput ? "bg-accent/10" : "bg-muted")}>
          <FileIcon className={cn("w-5 h-5", isCustomInput ? "text-accent" : "text-muted-foreground")} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <h4 className="text-sm font-medium text-foreground truncate" title={doc.filename}>
                  {doc.filename}
                </h4>
                {isCustomInput && (
                  <span className="text-[10px] uppercase font-medium px-1.5 py-0.5 rounded bg-accent/20 text-accent shrink-0">
                    {doc.custom_input_type || "custom"}
                  </span>
                )}
              </div>
              {isCustomInput && doc.custom_topic_hint && (
                <p className="text-xs text-muted-foreground truncate mt-0.5" title={doc.custom_topic_hint}>
                  {doc.custom_topic_hint}
                </p>
              )}
              <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                <span>{formatFileSize(doc.file_size)}</span>
                <span>•</span>
                <span>{formatDate(doc.upload_date)}</span>
                {doc.collection_name && (
                  <>
                    <span>•</span>
                    <span className="text-accent">{doc.collection_name}</span>
                  </>
                )}
                {doc.source && doc.source !== "upload" && (
                  <>
                    <span>•</span>
                    <span className="text-muted-foreground/70">{doc.source}</span>
                  </>
                )}
              </div>
            </div>

            {/* Status badge */}
            <div
              className={cn("flex items-center gap-1.5 px-2 py-1 rounded-full text-xs", status.bgColor)}
              title={(isPaused ? doc.paused_reason : undefined) ?? degradedReason ?? undefined}
            >
              <StatusIcon className={cn("w-3 h-3", status.color, status.animate && "animate-spin")} />
              <span className={status.color}>{status.label}</span>
            </div>
          </div>

          {/* Phase timeline while the text pipeline runs */}
          {(doc.processing_status === "processing" || doc.processing_status === "extracting") && (
            <div className="mt-3">
              <IngestionStepper doc={doc} />
            </div>
          )}

          {/* Live outage pause: the run is alive and re-probing the LLM
              endpoint; it continues automatically once the endpoint is back */}
          {isPaused && (
            <p className="mt-2 flex items-center gap-1 text-xs text-amber-400/90">
              <CirclePause className="w-3 h-3 shrink-0" />
              {doc.paused_reason || "Waiting for the LLM endpoint..."} — continues automatically
            </p>
          )}

          {/* Image analysis progress (post-text-pipeline; while processing the
              stepper renders its own parallel image row) */}
          {doc.processing_status === "completed" && hasImageProgress && !imageAnalysisDone && (
            <div className="mt-2">
              <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                <span className="flex items-center gap-1">
                  <FileImage className="w-3 h-3" />
                  {doc.image_progress_message || "Analyzing images..."}
                </span>
                <span>{imageProgressPercent}%</span>
              </div>
              <div className="h-1 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500/70 transition-all duration-300"
                  style={{ width: `${imageProgressPercent}%` }}
                />
              </div>
            </div>
          )}

          {/* Error message */}
          {doc.processing_status === "failed" && doc.error_message && (
            <p className="mt-2 text-xs text-muted-foreground line-clamp-2">{doc.error_message}</p>
          )}

          {/* Interrupted with a surviving checkpoint */}
          {isInterrupted && (
            <p className="mt-2 flex items-center gap-1 text-xs text-amber-400/90">
              <CirclePause className="w-3 h-3 shrink-0" />
              Progress is checkpointed — reprocess to resume from where it stopped
            </p>
          )}

          {/* Degraded reason */}
          {degradedReason && (
            <p className="mt-2 flex items-center gap-1 text-xs text-amber-400/90">
              <AlertTriangle className="w-3 h-3 shrink-0" />
              {degradedReason} — reprocess to retry
            </p>
          )}

          {/* Prompt-injection scan flag (non-blocking) */}
          {isInjectionFlagged(doc) && (
            <p className="mt-2 flex items-center gap-1 text-xs text-red-400/90">
              <ShieldAlert className="w-3 h-3 shrink-0" />
              Possible prompt injection detected{doc.injection_reason ? ` — ${doc.injection_reason}` : ""}
            </p>
          )}

          {/* Pending status */}
          {doc.processing_status === "pending" && (
            <div className="mt-2 text-xs text-muted-foreground">
              {doc.progress_message
                ? `${doc.progress_message} — waiting for a processing slot`
                : "Unprocessed"}
            </div>
          )}

          {/* Chunk count for completed */}
          {doc.processing_status === "completed" && doc.chunk_count > 0 && (
            <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
              <span>{doc.chunk_count} chunk{doc.chunk_count !== 1 ? "s" : ""} indexed</span>
              {hasImageProgress && imageAnalysisDone && (
                <span className="flex items-center gap-1">
                  <FileImage className="w-3 h-3" />
                  {doc.image_progress_total} image{doc.image_progress_total !== 1 ? "s" : ""} analyzed
                </span>
              )}
              {hasImageProgress && !imageAnalysisDone && (
                <span className="flex items-center gap-1 text-blue-500/70">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  {doc.image_progress_message}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1">
          {/* View (markdown) / download (everything else) button */}
          {doc.file_path && (
            <button
              onClick={handleView}
              disabled={isFetchingFile}
              className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              title={isMarkdownFile(doc.filename) ? "View document" : "Download document"}
            >
              {isFetchingFile ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : isMarkdownFile(doc.filename) ? (
                <Eye className="w-4 h-4" />
              ) : (
                <Download className="w-4 h-4" />
              )}
            </button>
          )}

          {/* Restart button for in-progress documents */}
          {isProcessing(doc.processing_status) && doc.file_path && (
            <button
              onClick={() => onRestart(doc.id)}
              disabled={isReprocessing}
              className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              title="Restart processing"
            >
              {isReprocessing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
            </button>
          )}

          {/* Reprocess button for failed/completed */}
          {(doc.processing_status === "failed" || doc.processing_status === "completed") && (
            <>
              {doc.file_path ? (
                <button
                  onClick={() => onReprocess(doc.id)}
                  disabled={isReprocessing}
                  className={cn(
                    "p-2 rounded-lg hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50",
                    isInterrupted ? "text-amber-400" : "text-muted-foreground"
                  )}
                  title={isInterrupted ? "Resume from checkpoint" : "Reprocess document"}
                >
                  {isReprocessing ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <RefreshCw className="w-4 h-4" />
                  )}
                </button>
              ) : (
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    className="hidden"
                    onChange={handleFileSelect}
                    accept=".pdf,.doc,.docx,.txt,.md,.mdx,.csv,.xlsx,.xls,.epub"
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isReprocessing}
                    className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                    title="Upload file to reprocess"
                  >
                    {isReprocessing ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Upload className="w-4 h-4" />
                    )}
                  </button>
                </>
              )}
            </>
          )}

          {/* Delete button */}
          <button
            onClick={() => onDelete(doc.id)}
            disabled={isDeleting}
            className="p-2 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50"
            title="Delete document"
          >
            {isDeleting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>
    </motion.div>

    {/* Markdown Viewer Modal */}
    <AnimatePresence>
      {showMarkdownViewer && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={() => setShowMarkdownViewer(false)}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className="bg-card border border-border rounded-xl w-full max-w-4xl max-h-[85vh] flex flex-col shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0">
              <h3 className="text-sm font-medium text-foreground truncate" title={doc.filename}>
                {doc.filename}
              </h3>
              <button
                onClick={() => setShowMarkdownViewer(false)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4">
              {loadingContent ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <MarkdownRenderer content={markdownContent || ""} />
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
    </>
  );
}
