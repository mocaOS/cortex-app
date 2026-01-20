"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Upload,
  FileText,
  CheckCircle,
  AlertCircle,
  Loader2,
  X,
  Sparkles,
  FolderOpen,
  Play,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import CollectionSelector from "./CollectionSelector";

interface FileUploadProps {
  onUpload: () => void;
}

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

export default function FileUpload({ onUpload }: FileUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string | undefined>(undefined);
  const [processingTask, setProcessingTask] = useState<ProcessingTask | null>(null);
  const [isStartingProcessing, setIsStartingProcessing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const taskPollingRef = useRef<NodeJS.Timeout | null>(null);
  const documentIdsToPolRef = useRef<Map<string, boolean>>(new Map());

  const allowedTypes = [".pdf", ".txt", ".md", ".docx", ".xlsx"];

  useEffect(() => {
    const newMap = new Map<string, boolean>();
    uploadingFiles.forEach((uf) => {
      if ((uf.status === "processing" || uf.status === "extracting") && uf.documentId) {
        newMap.set(uf.documentId, true);
      }
    });
    documentIdsToPolRef.current = newMap;
  }, [uploadingFiles]);

  useEffect(() => {
    const poll = async () => {
      const docIds = Array.from(documentIdsToPolRef.current.keys());
      if (docIds.length === 0) return;

      const results = await Promise.all(
        docIds.map(async (docId) => {
          try {
            const res = await fetch(`/api/documents/${docId}`);
            if (!res.ok) return null;
            return await res.json();
          } catch {
            return null;
          }
        })
      );

      setUploadingFiles((prev) =>
        prev.map((f) => {
          if (!f.documentId) return f;
          const docIndex = docIds.indexOf(f.documentId);
          if (docIndex === -1) return f;
          const doc = results[docIndex];
          if (!doc) return f;

          if (doc.processing_status === "completed") {
            documentIdsToPolRef.current.delete(f.documentId);
            return {
              ...f,
              status: "success" as const,
              message: `Completed! ${doc.chunk_count} chunks, ready for search`,
              progressCurrent: 100,
              progressTotal: 100,
              progressMessage: "Complete!",
            };
          } else if (doc.processing_status === "failed") {
            documentIdsToPolRef.current.delete(f.documentId);
            return {
              ...f,
              status: "error" as const,
              message: doc.error_message || "Processing failed",
            };
          } else {
            return {
              ...f,
              status: doc.processing_status as "processing" | "extracting",
              progressCurrent: doc.progress_current || 0,
              progressTotal: doc.progress_total || 100,
              progressMessage: doc.progress_message || "Processing...",
            };
          }
        })
      );
    };

    pollingRef.current = setInterval(poll, 20000);
    const initialPollTimeout = setTimeout(poll, 500);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      clearTimeout(initialPollTimeout);
    };
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const uploadFile = async (file: File) => {
    try {
      const data = await api.uploadFile(file, selectedCollection, false);
      return { success: true, data };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : "Upload failed",
      };
    }
  };

  const startProcessing = async () => {
    setIsStartingProcessing(true);
    try {
      const result = await api.processPendingDocuments();
      if (result.task_id) {
        setProcessingTask({
          taskId: result.task_id,
          status: "running",
          current: 0,
          total: result.pending_count,
          message: result.message,
        });
        pollTaskProgress(result.task_id);
      }
    } catch (error) {
      console.error("Failed to start processing:", error);
      alert(`Failed to start processing: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsStartingProcessing(false);
    }
  };

  const pollTaskProgress = (taskId: string) => {
    if (taskPollingRef.current) {
      clearInterval(taskPollingRef.current);
    }

    const poll = async () => {
      try {
        const status = await api.getTaskStatus(taskId);
        setProcessingTask({
          taskId,
          status: status.status,
          current: status.progress_current,
          total: status.progress_total,
          message: status.message,
        });

        if (status.status === "running") {
          setUploadingFiles((prev) =>
            prev.map((uf) => {
              if (uf.status === "uploaded") {
                return { ...uf, status: "processing" as const, progressMessage: "Queued..." };
              }
              return uf;
            })
          );
        }

        if (status.status === "completed" || status.status === "failed") {
          if (taskPollingRef.current) {
            clearInterval(taskPollingRef.current);
            taskPollingRef.current = null;
          }
          onUpload();
          setUploadingFiles((prev) =>
            prev.map((uf) => {
              if (uf.status === "processing" || uf.status === "uploaded") {
                return { ...uf, status: "success" as const, message: "Processing complete" };
              }
              return uf;
            })
          );
        }
      } catch (error) {
        console.error("Failed to poll task status:", error);
      }
    };

    poll();
    taskPollingRef.current = setInterval(poll, 2000);
  };

  useEffect(() => {
    return () => {
      if (taskPollingRef.current) {
        clearInterval(taskPollingRef.current);
      }
    };
  }, []);

  const processFiles = async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return allowedTypes.includes(ext);
    });

    if (validFiles.length === 0) return;

    setProcessingTask(null);

    const newUploads: UploadingFile[] = validFiles.map((file) => ({
      file,
      status: "pending" as const,
    }));

    setUploadingFiles((prev) => [...prev, ...newUploads]);

    const CONCURRENCY_LIMIT = 10;
    const uploadQueue = [...validFiles];
    const activeUploads: Promise<void>[] = [];

    const uploadNext = async () => {
      if (uploadQueue.length === 0) return;
      const file = uploadQueue.shift()!;

      setUploadingFiles((prev) =>
        prev.map((uf) =>
          uf.file === file ? { ...uf, status: "uploading" as const } : uf
        )
      );

      const result = await uploadFile(file);

      setUploadingFiles((prev) =>
        prev.map((uf) =>
          uf.file === file
            ? result.success && result.data
              ? {
                  ...uf,
                  status: "uploaded" as const,
                  documentId: result.data.document_id,
                  message: "Uploaded - waiting to process",
                }
              : {
                  ...uf,
                  status: "error" as const,
                  message: result.error,
                }
            : uf
        )
      );

      await uploadNext();
    };

    for (let i = 0; i < Math.min(CONCURRENCY_LIMIT, validFiles.length); i++) {
      activeUploads.push(uploadNext());
    }

    await Promise.all(activeUploads);
    onUpload();
  };

  const getProgressPercent = (uf: UploadingFile) => {
    if (!uf.progressTotal || uf.progressTotal === 0) return 0;
    return Math.min(100, Math.round((uf.progressCurrent || 0) / uf.progressTotal * 100));
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      if (e.dataTransfer.files) {
        processFiles(e.dataTransfer.files);
      }
    },
    [processFiles]
  );

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      processFiles(e.target.files);
    }
  };

  const removeFile = (file: File) => {
    setUploadingFiles((prev) => prev.filter((uf) => uf.file !== file));
  };

  const clearCompleted = () => {
    setUploadingFiles((prev) => prev.filter((uf) => 
      uf.status === "pending" || uf.status === "uploading" || uf.status === "uploaded" || 
      uf.status === "processing" || uf.status === "extracting"
    ));
    setProcessingTask(null);
  };

  const hasCompletedFiles = uploadingFiles.some(
    (f) => f.status === "success" || f.status === "error"
  );

  const uploadedButNotProcessed = uploadingFiles.filter(
    (f) => f.status === "uploaded"
  );
  const hasFilesToProcess = uploadedButNotProcessed.length > 0 && !processingTask;
  const isUploading = uploadingFiles.some((f) => f.status === "pending" || f.status === "uploading");

  return (
    <div className="space-y-6">
      {/* Collection Selector */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <FolderOpen className="w-4 h-4" />
          <span>Upload to:</span>
        </div>
        <CollectionSelector
          value={selectedCollection}
          onChange={setSelectedCollection}
          allowCreate={true}
          className="w-64"
        />
      </div>

      {/* Upload Zone */}
      <motion.div
        className={cn(
          "relative rounded-lg border-2 border-dashed transition-all duration-300",
          "p-12 text-center cursor-pointer group overflow-hidden",
          isDragging
            ? "border-foreground bg-muted"
            : "border-border hover:border-muted-foreground hover:bg-muted/30"
        )}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        whileHover={{ scale: 1.005 }}
        whileTap={{ scale: 0.995 }}
      >
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          multiple
          accept={allowedTypes.join(",")}
          onChange={handleFileSelect}
        />

        <div className="relative z-10">
          <motion.div
            className={cn(
              "w-20 h-20 mx-auto rounded-lg flex items-center justify-center mb-6",
              isDragging ? "bg-accent/20 border border-accent" : "bg-muted border border-border"
            )}
            animate={isDragging ? { scale: 1.1 } : { scale: 1 }}
          >
            <Upload
              className={cn(
                "w-10 h-10 transition-colors duration-300",
                isDragging ? "text-accent" : "text-muted-foreground"
              )}
            />
          </motion.div>

          <h3 className="text-xl font-semibold text-foreground mb-2">
            {isDragging ? "Drop files here" : "Upload Documents"}
          </h3>
          <p className="text-muted-foreground mb-4">
            Drag and drop files or click to browse
          </p>
          <div className="flex items-center justify-center gap-2 flex-wrap">
            {allowedTypes.map((type) => (
              <span
                key={type}
                className="px-3 py-1 rounded-full bg-muted text-muted-foreground text-xs font-mono"
              >
                {type}
              </span>
            ))}
          </div>
        </div>
      </motion.div>

      {/* Upload Progress */}
      <AnimatePresence>
        {uploadingFiles.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            className="glass rounded-lg overflow-hidden"
          >
            {uploadingFiles.length > 0 && (
              <div className="p-4 bg-card border-b border-border">
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div className="flex items-center gap-4 text-xs flex-wrap">
                    {uploadingFiles.filter(f => f.status === "pending" || f.status === "uploading").length > 0 && (
                      <div className="flex items-center gap-2">
                        <div className="w-2 h-2 rounded-full bg-foreground animate-pulse" />
                        <span className="text-muted-foreground">
                          Uploading: {uploadingFiles.filter(f => f.status === "pending" || f.status === "uploading").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "uploaded").length > 0 && (
                      <div className="flex items-center gap-2">
                        <Upload className="w-3 h-3 text-foreground" />
                        <span className="text-foreground">
                          Uploaded: {uploadingFiles.filter(f => f.status === "uploaded").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "processing" || f.status === "extracting").length > 0 && (
                      <div className="flex items-center gap-2">
                        <Loader2 className="w-3 h-3 text-foreground animate-spin" />
                        <span className="text-muted-foreground">
                          Processing: {uploadingFiles.filter(f => f.status === "processing" || f.status === "extracting").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "success").length > 0 && (
                      <div className="flex items-center gap-2">
                        <CheckCircle className="w-3 h-3 text-foreground" />
                        <span className="text-muted-foreground">
                          Completed: {uploadingFiles.filter(f => f.status === "success").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "error").length > 0 && (
                      <div className="flex items-center gap-2">
                        <AlertCircle className="w-3 h-3 text-destructive" />
                        <span className="text-destructive">
                          Failed: {uploadingFiles.filter(f => f.status === "error").length}
                        </span>
                      </div>
                    )}
                  </div>

                  {hasFilesToProcess && !isUploading && (
                    <button
                      onClick={startProcessing}
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

                {isUploading && (
                  <div className="mt-3">
                    <div className="h-1 bg-border rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-foreground rounded-full"
                        initial={{ width: 0 }}
                        animate={{ 
                          width: `${Math.round(
                            (uploadingFiles.filter(f => f.status === "uploaded" || f.status === "error").length / 
                             uploadingFiles.length) * 100
                          )}%` 
                        }}
                        transition={{ duration: 0.3 }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h4 className="text-sm font-medium text-foreground">
                {uploadingFiles.length > 3 
                  ? `Files (${uploadingFiles.length})` 
                  : "Upload Progress"
                }
              </h4>
              {hasCompletedFiles && (
                <button
                  onClick={clearCompleted}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  Clear completed
                </button>
              )}
            </div>

            <div className="divide-y divide-border">
              {uploadingFiles.map((uf, index) => (
                <motion.div
                  key={`${uf.file.name}-${index}`}
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
                      onClick={() => removeFile(uf.file)}
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
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
