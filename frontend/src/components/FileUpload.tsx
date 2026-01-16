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

// Processing task state
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
  
  // Keep track of document IDs to poll in a ref (stable reference)
  const documentIdsToPolRef = useRef<Map<string, boolean>>(new Map());

  const allowedTypes = [".pdf", ".txt", ".md", ".docx", ".xlsx"];

  // Update the ref when uploadingFiles changes
  useEffect(() => {
    const newMap = new Map<string, boolean>();
    uploadingFiles.forEach((uf) => {
      if ((uf.status === "processing" || uf.status === "extracting") && uf.documentId) {
        newMap.set(uf.documentId, true);
      }
    });
    documentIdsToPolRef.current = newMap;
  }, [uploadingFiles]);

  // Single stable polling interval - runs every 3 seconds
  useEffect(() => {
    const poll = async () => {
      const docIds = Array.from(documentIdsToPolRef.current.keys());
      
      if (docIds.length === 0) return;

      // Poll all documents in parallel
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

      // Update state based on results
      setUploadingFiles((prev) =>
        prev.map((f) => {
          if (!f.documentId) return f;
          
          const docIndex = docIds.indexOf(f.documentId);
          if (docIndex === -1) return f;
          
          const doc = results[docIndex];
          if (!doc) return f;

          if (doc.processing_status === "completed") {
            // Remove from polling list
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
            // Remove from polling list
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

    // Start polling interval (poll every 20 seconds)
    pollingRef.current = setInterval(poll, 20000);
    
    // Initial poll after a short delay
    const initialPollTimeout = setTimeout(poll, 500);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      clearTimeout(initialPollTimeout);
    };
  }, []); // Empty deps - only run once on mount

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  // Upload file without starting processing (for bulk uploads)
  const uploadFile = async (file: File) => {
    try {
      // Upload with start_processing=false for bulk uploads
      const data = await api.uploadFile(file, selectedCollection, false);
      return { success: true, data };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : "Upload failed",
      };
    }
  };

  // Start processing all pending documents
  const startProcessing = async () => {
    setIsStartingProcessing(true);
    try {
      // Use backend's configured BATCH_PROCESSING_CONCURRENCY (default: 10)
      const result = await api.processPendingDocuments();
      
      if (result.task_id) {
        setProcessingTask({
          taskId: result.task_id,
          status: "running",
          current: 0,
          total: result.pending_count,
          message: result.message,
        });
        
        // Start polling for task progress
        pollTaskProgress(result.task_id);
      }
    } catch (error) {
      console.error("Failed to start processing:", error);
      alert(`Failed to start processing: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsStartingProcessing(false);
    }
  };

  // Poll for task progress
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

        // Update individual file statuses based on task progress
        if (status.status === "running") {
          // Mark files as processing
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
          
          // Refresh document list to get final statuses
          onUpload();
          
          // Mark all uploaded files as needing status check
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

    // Poll immediately, then every 2 seconds
    poll();
    taskPollingRef.current = setInterval(poll, 2000);
  };

  // Cleanup task polling on unmount
  useEffect(() => {
    return () => {
      if (taskPollingRef.current) {
        clearInterval(taskPollingRef.current);
      }
    };
  }, []);

  // Upload files with concurrency limit for bulk uploads (100+ files)
  // Files are uploaded but NOT processed until user clicks "Start Processing"
  const processFiles = async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return allowedTypes.includes(ext);
    });

    if (validFiles.length === 0) return;

    // Reset processing task if there was one
    setProcessingTask(null);

    // Add all files to state at once with "pending" status
    const newUploads: UploadingFile[] = validFiles.map((file) => ({
      file,
      status: "pending" as const,
    }));

    setUploadingFiles((prev) => [...prev, ...newUploads]);

    // Concurrent upload with limit (10 at a time for fast uploads)
    const CONCURRENCY_LIMIT = 10;
    const uploadQueue = [...validFiles];
    const activeUploads: Promise<void>[] = [];
    let uploadedCount = 0;

    const uploadNext = async () => {
      if (uploadQueue.length === 0) return;
      
      const file = uploadQueue.shift()!;
      
      // Mark as uploading
      setUploadingFiles((prev) =>
        prev.map((uf) =>
          uf.file === file ? { ...uf, status: "uploading" as const } : uf
        )
      );
      
      const result = await uploadFile(file);
      uploadedCount++;

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

      // Process next in queue
      await uploadNext();
    };

    // Start concurrent uploads
    for (let i = 0; i < Math.min(CONCURRENCY_LIMIT, validFiles.length); i++) {
      activeUploads.push(uploadNext());
    }

    // Wait for all uploads to complete
    await Promise.all(activeUploads);

    // Notify parent that files were uploaded (but not processed yet)
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

  // Check if there are files waiting to be processed
  const uploadedButNotProcessed = uploadingFiles.filter(
    (f) => f.status === "uploaded"
  );
  const hasFilesToProcess = uploadedButNotProcessed.length > 0 && !processingTask;
  const isUploading = uploadingFiles.some((f) => f.status === "pending" || f.status === "uploading");

  return (
    <div className="space-y-6">
      {/* Collection Selector */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-sm text-white/50">
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
          "relative rounded-2xl border-2 border-dashed transition-all duration-300",
          "p-12 text-center cursor-pointer group overflow-hidden",
          isDragging
            ? "border-ocean-500 bg-ocean-500/10"
            : "border-white/10 hover:border-white/20 hover:bg-white/[0.02]"
        )}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        whileHover={{ scale: 1.005 }}
        whileTap={{ scale: 0.995 }}
      >
        {/* Background decoration */}
        <div className="absolute inset-0 bg-gradient-to-br from-ocean-500/5 via-transparent to-cyan-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

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
              "w-20 h-20 mx-auto rounded-2xl flex items-center justify-center mb-6",
              "bg-gradient-to-br from-ocean-500/20 to-cyan-500/20",
              "border border-ocean-500/30"
            )}
            animate={isDragging ? { scale: 1.1 } : { scale: 1 }}
          >
            <Upload
              className={cn(
                "w-10 h-10 transition-colors duration-300",
                isDragging ? "text-ocean-400" : "text-ocean-500/70"
              )}
            />
          </motion.div>

          <h3 className="text-xl font-semibold text-white/90 mb-2">
            {isDragging ? "Drop files here" : "Upload Documents"}
          </h3>
          <p className="text-white/50 mb-4">
            Drag and drop files or click to browse
          </p>
          <div className="flex items-center justify-center gap-2 flex-wrap">
            {allowedTypes.map((type) => (
              <span
                key={type}
                className="px-3 py-1 rounded-full bg-white/5 text-white/40 text-xs font-mono"
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
            className="glass rounded-xl overflow-hidden"
          >
            {/* Summary for bulk uploads */}
            {uploadingFiles.length > 0 && (
              <div className="p-4 bg-white/[0.02] border-b border-white/5">
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div className="flex items-center gap-4 text-xs flex-wrap">
                    {uploadingFiles.filter(f => f.status === "pending" || f.status === "uploading").length > 0 && (
                      <div className="flex items-center gap-2">
                        <div className="w-2 h-2 rounded-full bg-ocean-400 animate-pulse" />
                        <span className="text-white/60">
                          Uploading: {uploadingFiles.filter(f => f.status === "pending" || f.status === "uploading").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "uploaded").length > 0 && (
                      <div className="flex items-center gap-2">
                        <Upload className="w-3 h-3 text-purple-400" />
                        <span className="text-purple-400/80">
                          Uploaded: {uploadingFiles.filter(f => f.status === "uploaded").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "processing" || f.status === "extracting").length > 0 && (
                      <div className="flex items-center gap-2">
                        <Loader2 className="w-3 h-3 text-ocean-400 animate-spin" />
                        <span className="text-white/60">
                          Processing: {uploadingFiles.filter(f => f.status === "processing" || f.status === "extracting").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "success").length > 0 && (
                      <div className="flex items-center gap-2">
                        <CheckCircle className="w-3 h-3 text-mint-400" />
                        <span className="text-white/60">
                          Completed: {uploadingFiles.filter(f => f.status === "success").length}
                        </span>
                      </div>
                    )}
                    {uploadingFiles.filter(f => f.status === "error").length > 0 && (
                      <div className="flex items-center gap-2">
                        <AlertCircle className="w-3 h-3 text-coral-400" />
                        <span className="text-coral-400/80">
                          Failed: {uploadingFiles.filter(f => f.status === "error").length}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Start Processing button */}
                  {hasFilesToProcess && !isUploading && (
                    <button
                      onClick={startProcessing}
                      disabled={isStartingProcessing}
                      className={cn(
                        "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all",
                        "bg-gradient-to-r from-ocean-500 to-cyan-500 text-white",
                        "hover:from-ocean-400 hover:to-cyan-400",
                        "shadow-lg shadow-ocean-500/25",
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
                  <div className="mt-3 p-3 rounded-lg bg-ocean-500/10 border border-ocean-500/20">
                    <div className="flex items-center justify-between text-xs mb-2">
                      <span className="text-ocean-400 font-medium">
                        {processingTask.status === "running" ? "Processing documents..." : 
                         processingTask.status === "completed" ? "Processing complete!" :
                         processingTask.status === "failed" ? "Processing failed" : "Starting..."}
                      </span>
                      <span className="text-white/60">
                        {processingTask.current}/{processingTask.total}
                      </span>
                    </div>
                    <div className="h-2 bg-white/5 rounded-full overflow-hidden">
                      <motion.div
                        className={cn(
                          "h-full rounded-full",
                          processingTask.status === "completed" 
                            ? "bg-gradient-to-r from-mint-500 to-green-400"
                            : processingTask.status === "failed"
                            ? "bg-gradient-to-r from-coral-500 to-red-400"
                            : "bg-gradient-to-r from-ocean-500 to-cyan-400"
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
                    <p className="text-xs text-white/40 mt-1">{processingTask.message}</p>
                  </div>
                )}

                {/* Upload progress bar (only during upload phase) */}
                {isUploading && (
                  <div className="mt-3">
                    <div className="h-1 bg-white/5 rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-gradient-to-r from-ocean-500 to-purple-400 rounded-full"
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
            <div className="flex items-center justify-between p-4 border-b border-white/5">
              <h4 className="text-sm font-medium text-white/80">
                {uploadingFiles.length > 3 
                  ? `Files (${uploadingFiles.length})` 
                  : "Upload Progress"
                }
              </h4>
              {hasCompletedFiles && (
                <button
                  onClick={clearCompleted}
                  className="text-xs text-white/40 hover:text-white/60 transition-colors"
                >
                  Clear completed
                </button>
              )}
            </div>

            <div className="divide-y divide-white/5">
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
                        uf.status === "pending" && "bg-white/10",
                        uf.status === "uploading" && "bg-ocean-500/20",
                        uf.status === "uploaded" && "bg-purple-500/20",
                        uf.status === "processing" && "bg-ocean-500/20",
                        uf.status === "extracting" && "bg-cyan-500/20",
                        uf.status === "success" && "bg-mint-500/20",
                        uf.status === "error" && "bg-coral-500/20"
                      )}
                    >
                      {uf.status === "pending" && (
                        <FileText className="w-5 h-5 text-white/40" />
                      )}
                      {uf.status === "uploading" && (
                        <Loader2 className="w-5 h-5 text-ocean-400 animate-spin" />
                      )}
                      {uf.status === "uploaded" && (
                        <Upload className="w-5 h-5 text-purple-400" />
                      )}
                      {uf.status === "processing" && (
                        <Loader2 className="w-5 h-5 text-ocean-400 animate-spin" />
                      )}
                      {uf.status === "extracting" && (
                        <Sparkles className="w-5 h-5 text-cyan-400 animate-pulse" />
                      )}
                      {uf.status === "success" && (
                        <CheckCircle className="w-5 h-5 text-mint-400" />
                      )}
                      {uf.status === "error" && (
                        <AlertCircle className="w-5 h-5 text-coral-400" />
                      )}
                    </div>

                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white/80 truncate">
                        {uf.file.name}
                      </p>
                      <p
                        className={cn(
                          "text-xs",
                          uf.status === "pending" && "text-white/30",
                          uf.status === "uploading" && "text-white/40",
                          uf.status === "uploaded" && "text-purple-400/70",
                          uf.status === "processing" && "text-ocean-400/70",
                          uf.status === "extracting" && "text-cyan-400/70",
                          uf.status === "success" && "text-mint-400/70",
                          uf.status === "error" && "text-coral-400/70"
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
                      <span className="text-xs font-medium text-ocean-400 shrink-0">
                        {getProgressPercent(uf)}%
                      </span>
                    )}

                    <button
                      onClick={() => removeFile(uf.file)}
                      className="p-2 hover:bg-white/5 rounded-lg transition-colors shrink-0"
                    >
                      <X className="w-4 h-4 text-white/40" />
                    </button>
                  </div>

                  {/* Progress bar for processing files */}
                  {(uf.status === "processing" || uf.status === "extracting") && uf.progressTotal && uf.progressTotal > 0 && (
                    <div className="mt-3 ml-14">
                      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                        <motion.div
                          className={cn(
                            "h-full rounded-full",
                            uf.status === "extracting"
                              ? "bg-gradient-to-r from-cyan-500 to-teal-400"
                              : "bg-gradient-to-r from-ocean-500 to-cyan-400"
                          )}
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
