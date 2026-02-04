"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { FolderOpen } from "lucide-react";
import { api } from "@/lib/api";
import CollectionSelector from "./CollectionSelector";
import { UploadZone, UploadProgress } from "./upload";

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

const ALLOWED_TYPES = [".pdf", ".txt", ".md", ".docx", ".xlsx"];

export default function FileUpload({ onUpload }: FileUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string | undefined>(undefined);
  const [processingTask, setProcessingTask] = useState<ProcessingTask | null>(null);
  const [isStartingProcessing, setIsStartingProcessing] = useState(false);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const taskPollingRef = useRef<NodeJS.Timeout | null>(null);
  const documentIdsToPolRef = useRef<Map<string, boolean>>(new Map());

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
            return await api.getDocument(docId);
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

  const uploadFile = useCallback(async (file: File) => {
    try {
      const data = await api.uploadFile(file, selectedCollection, false);
      return { success: true, data };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : "Upload failed",
      };
    }
  }, [selectedCollection]);

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

  const processFiles = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return ALLOWED_TYPES.includes(ext);
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
  }, [uploadFile, onUpload]);

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

  const handleFileSelect = (files: FileList) => {
    processFiles(files);
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
      <UploadZone
        isDragging={isDragging}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onFileSelect={handleFileSelect}
      />

      {/* Upload Progress */}
      <UploadProgress
        uploadingFiles={uploadingFiles}
        processingTask={processingTask}
        hasFilesToProcess={hasFilesToProcess}
        isUploading={isUploading}
        isStartingProcessing={isStartingProcessing}
        onStartProcessing={startProcessing}
        onRemoveFile={removeFile}
        onClearCompleted={clearCompleted}
      />
    </div>
  );
}
