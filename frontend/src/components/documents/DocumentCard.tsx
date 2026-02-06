"use client";

import { useRef } from "react";
import { motion } from "framer-motion";
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
} from "lucide-react";
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

const isProcessing = (status: string) => {
  return status === "processing" || status === "extracting" || status === "pending";
};

const getFileIcon = (fileType: string, isCustomInput?: boolean) => {
  if (isCustomInput) return PenLine;
  if (fileType.includes("pdf")) return FileText;
  if (fileType.includes("spreadsheet") || fileType.includes("excel") || fileType.includes("csv"))
    return FileSpreadsheet;
  if (fileType.includes("image")) return FileImage;
  return File;
};

const getStatusConfig = (status: string) => {
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
  const FileIcon = getFileIcon(doc.file_type, doc.is_custom_input);
  const status = getStatusConfig(doc.processing_status);
  const StatusIcon = status.icon;
  const isCustomInput = doc.is_custom_input === true;

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onReprocessWithFile(doc.id, file);
    }
    e.target.value = "";
  };

  const progressPercent =
    doc.progress_current && doc.progress_total
      ? Math.round((doc.progress_current / doc.progress_total) * 100)
      : 0;

  return (
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
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-medium text-foreground truncate max-w-[40vw]" title={doc.filename}>
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
              </div>
            </div>

            {/* Status badge */}
            <div className={cn("flex items-center gap-1.5 px-2 py-1 rounded-full text-xs", status.bgColor)}>
              <StatusIcon className={cn("w-3 h-3", status.color, status.animate && "animate-spin")} />
              <span className={status.color}>{status.label}</span>
            </div>
          </div>

          {/* Progress bar for processing */}
          {isProcessing(doc.processing_status) && doc.progress_total && doc.progress_total > 0 && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                <span>{doc.progress_message || "Processing..."}</span>
                <span>{progressPercent}%</span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent transition-all duration-300"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          )}

          {/* Error message */}
          {doc.processing_status === "failed" && doc.error_message && (
            <p className="mt-2 text-xs text-muted-foreground line-clamp-2">{doc.error_message}</p>
          )}

          {/* Chunk count for completed */}
          {doc.processing_status === "completed" && (
            <p className="mt-2 text-xs text-muted-foreground">
              {doc.chunk_count} chunk{doc.chunk_count !== 1 ? "s" : ""} indexed
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1">
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
                  className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                  title="Reprocess document"
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
                    accept=".pdf,.doc,.docx,.txt,.md,.csv,.xlsx,.xls"
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
  );
}
