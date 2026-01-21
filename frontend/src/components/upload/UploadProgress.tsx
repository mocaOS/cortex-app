"use client";

import { motion, AnimatePresence } from "framer-motion";
import {
  Upload,
  CheckCircle,
  AlertCircle,
  Loader2,
  Play,
} from "lucide-react";
import { cn } from "@/lib/utils";
import UploadFileItem from "./UploadFileItem";

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

interface ProcessingTask {
  taskId: string;
  status: "pending" | "running" | "completed" | "failed";
  current: number;
  total: number;
  message: string;
}

interface UploadProgressProps {
  uploadingFiles: UploadingFile[];
  processingTask: ProcessingTask | null;
  hasFilesToProcess: boolean;
  isUploading: boolean;
  isStartingProcessing: boolean;
  onStartProcessing: () => void;
  onRemoveFile: (file: File) => void;
  onClearCompleted: () => void;
}

export default function UploadProgress({
  uploadingFiles,
  processingTask,
  hasFilesToProcess,
  isUploading,
  isStartingProcessing,
  onStartProcessing,
  onRemoveFile,
  onClearCompleted,
}: UploadProgressProps) {
  const hasCompletedFiles = uploadingFiles.some(
    (f) => f.status === "success" || f.status === "error"
  );

  const uploadedButNotProcessed = uploadingFiles.filter(
    (f) => f.status === "uploaded"
  );

  const statusCounts = {
    pending: uploadingFiles.filter((f) => f.status === "pending" || f.status === "uploading").length,
    uploaded: uploadingFiles.filter((f) => f.status === "uploaded").length,
    processing: uploadingFiles.filter((f) => f.status === "processing" || f.status === "extracting").length,
    success: uploadingFiles.filter((f) => f.status === "success").length,
    error: uploadingFiles.filter((f) => f.status === "error").length,
  };

  return (
    <AnimatePresence>
      {uploadingFiles.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20 }}
          className="glass rounded-lg overflow-hidden"
        >
          {/* Status summary */}
          <div className="p-4 bg-card border-b border-border">
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-4 text-xs flex-wrap">
                {statusCounts.pending > 0 && (
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-foreground animate-pulse" />
                    <span className="text-muted-foreground">
                      Uploading: {statusCounts.pending}
                    </span>
                  </div>
                )}
                {statusCounts.uploaded > 0 && (
                  <div className="flex items-center gap-2">
                    <Upload className="w-3 h-3 text-foreground" />
                    <span className="text-foreground">
                      Uploaded: {statusCounts.uploaded}
                    </span>
                  </div>
                )}
                {statusCounts.processing > 0 && (
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-3 h-3 text-foreground animate-spin" />
                    <span className="text-muted-foreground">
                      Processing: {statusCounts.processing}
                    </span>
                  </div>
                )}
                {statusCounts.success > 0 && (
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-3 h-3 text-foreground" />
                    <span className="text-muted-foreground">
                      Completed: {statusCounts.success}
                    </span>
                  </div>
                )}
                {statusCounts.error > 0 && (
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-3 h-3 text-destructive" />
                    <span className="text-destructive">
                      Failed: {statusCounts.error}
                    </span>
                  </div>
                )}
              </div>

              {hasFilesToProcess && !isUploading && (
                <button
                  onClick={onStartProcessing}
                  disabled={isStartingProcessing}
                  className={cn(
                    "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all",
                    "bg-accent text-accent-foreground",
                    "hover:bg-accent/90",
                    isStartingProcessing && "opacity-50 cursor-not-allowed"
                  )}
                >
                  {isStartingProcessing ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Play className="w-4 h-4" />
                  )}
                  Start Processing ({uploadedButNotProcessed.length} files)
                </button>
              )}
            </div>

            {/* Processing task progress */}
            {processingTask && (
              <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
                <div className="flex items-center justify-between text-xs mb-2">
                  <span className="text-foreground font-medium">
                    {processingTask.status === "running" ? "Processing documents..." :
                     processingTask.status === "completed" ? "Processing complete!" :
                     processingTask.status === "failed" ? "Processing failed" : "Starting..."}
                  </span>
                  <span className="text-muted-foreground">
                    {processingTask.current}/{processingTask.total}
                  </span>
                </div>
                <div className="h-2 bg-border rounded-full overflow-hidden">
                  <motion.div
                    className={cn(
                      "h-full rounded-full",
                      processingTask.status === "completed"
                        ? "bg-accent"
                        : processingTask.status === "failed"
                        ? "bg-destructive"
                        : "bg-accent"
                    )}
                    initial={{ width: 0 }}
                    animate={{
                      width: `${processingTask.total > 0
                        ? Math.round((processingTask.current / processingTask.total) * 100)
                        : 0}%`
                    }}
                    transition={{ duration: 0.3 }}
                  />
                </div>
                <p className="text-xs text-muted-foreground mt-1">{processingTask.message}</p>
              </div>
            )}

            {/* Upload progress bar */}
            {isUploading && (
              <div className="mt-3">
                <div className="h-1 bg-border rounded-full overflow-hidden">
                  <motion.div
                    className="h-full bg-foreground rounded-full"
                    initial={{ width: 0 }}
                    animate={{
                      width: `${Math.round(
                        (uploadingFiles.filter((f) => f.status === "uploaded" || f.status === "error").length /
                          uploadingFiles.length) * 100
                      )}%`
                    }}
                    transition={{ duration: 0.3 }}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-border">
            <h4 className="text-sm font-medium text-foreground">
              {uploadingFiles.length > 3
                ? `Files (${uploadingFiles.length})`
                : "Upload Progress"
              }
            </h4>
            {hasCompletedFiles && (
              <button
                onClick={onClearCompleted}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Clear completed
              </button>
            )}
          </div>

          {/* File list */}
          <div className="divide-y divide-border">
            {uploadingFiles.map((uf, index) => (
              <UploadFileItem
                key={`${uf.file.name}-${index}`}
                uploadingFile={uf}
                index={index}
                onRemove={() => onRemoveFile(uf.file)}
              />
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
