"use client";

import { motion } from "framer-motion";
import {
  FileText,
  Upload,
  CheckCircle,
  AlertCircle,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

type UploadStatus = "pending" | "uploading" | "uploaded" | "processing" | "extracting" | "success" | "error";

interface UploadingFile {
  file: File;
  status: UploadStatus;
  message?: string;
  documentId?: string;
  progressCurrent?: number;
  progressTotal?: number;
  progressMessage?: string;
}

interface UploadFileItemProps {
  uploadingFile: UploadingFile;
  index: number;
  onRemove: () => void;
}

const getProgressPercent = (uf: UploadingFile) => {
  if (!uf.progressTotal || uf.progressTotal === 0) return 0;
  return Math.min(100, Math.round((uf.progressCurrent || 0) / uf.progressTotal * 100));
};

export default function UploadFileItem({
  uploadingFile,
  index,
  onRemove,
}: UploadFileItemProps) {
  const uf = uploadingFile;

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.05 }}
      className="p-4"
    >
      <div className="flex items-center gap-4">
        <div
          className={cn(
            "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
            uf.status === "error" ? "bg-destructive/20" : "bg-muted"
          )}
        >
          {uf.status === "pending" && (
            <FileText className="w-5 h-5 text-muted-foreground" />
          )}
          {uf.status === "uploading" && (
            <Loader2 className="w-5 h-5 text-accent animate-spin" />
          )}
          {uf.status === "uploaded" && (
            <Upload className="w-5 h-5 text-accent" />
          )}
          {uf.status === "processing" && (
            <Loader2 className="w-5 h-5 text-accent animate-spin" />
          )}
          {uf.status === "extracting" && (
            <Sparkles className="w-5 h-5 text-accent animate-pulse" />
          )}
          {uf.status === "success" && (
            <CheckCircle className="w-5 h-5 text-accent" />
          )}
          {uf.status === "error" && (
            <AlertCircle className="w-5 h-5 text-destructive" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">
            {uf.file.name}
          </p>
          <p
            className={cn(
              "text-xs",
              uf.status === "error" ? "text-destructive" : "text-muted-foreground"
            )}
          >
            {uf.status === "pending" && "Waiting..."}
            {uf.status === "uploading" && "Uploading..."}
            {uf.status === "uploaded" && (uf.message || "Uploaded - click Start Processing")}
            {uf.status === "processing" && (uf.progressMessage || "Processing...")}
            {uf.status === "extracting" && (uf.progressMessage || "Extracting knowledge graph...")}
            {uf.status === "success" && uf.message}
            {uf.status === "error" && uf.message}
          </p>
        </div>

        {(uf.status === "processing" || uf.status === "extracting") && (
          <span className="text-xs font-medium text-foreground shrink-0">
            {getProgressPercent(uf)}%
          </span>
        )}

        <button
          onClick={onRemove}
          className="p-2 hover:bg-muted rounded-lg transition-colors shrink-0"
        >
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

      {(uf.status === "processing" || uf.status === "extracting") && uf.progressTotal && uf.progressTotal > 0 && (
        <div className="mt-3 ml-14">
          <div className="h-1.5 bg-border rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-accent rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${getProgressPercent(uf)}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
        </div>
      )}
    </motion.div>
  );
}
