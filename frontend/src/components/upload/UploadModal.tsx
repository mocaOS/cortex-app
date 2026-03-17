"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { X, FolderOpen } from "lucide-react";
import { api } from "@/lib/api";
import CollectionSelector from "../CollectionSelector";
import UploadZone from "./UploadZone";
import UploadProgress from "./UploadProgress";

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

const ALLOWED_TYPES = [
  ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
  ".html", ".htm",
  ".txt", ".md", ".markdown", ".rst",
  ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
  ".wav", ".mp3", ".webvtt", ".vtt",
  ".tex", ".latex",
  ".xml",
];

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUploadComplete: () => void;
}

export default function UploadModal({ isOpen, onClose, onUploadComplete }: UploadModalProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string | undefined>(undefined);
  const [isStartingProcessing, setIsStartingProcessing] = useState(false);

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, onClose]);

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
        onUploadComplete();
        onClose();
      }
    } catch (error) {
      console.error("Failed to start processing:", error);
      alert(`Failed to start processing: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsStartingProcessing(false);
    }
  };

  const processFiles = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return ALLOWED_TYPES.includes(ext);
    });

    if (validFiles.length === 0) return;

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
  }, [uploadFile]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

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
  };

  const uploadedButNotProcessed = uploadingFiles.filter(
    (f) => f.status === "uploaded"
  );
  const hasFilesToProcess = uploadedButNotProcessed.length > 0;
  const isUploading = uploadingFiles.some((f) => f.status === "pending" || f.status === "uploading");

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-3xl max-h-[90vh] overflow-y-auto mx-4 bg-card border border-border rounded-2xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-border">
          <h2 className="text-lg font-semibold text-foreground">Upload Documents</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
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
            processingTask={null}
            hasFilesToProcess={hasFilesToProcess}
            isUploading={isUploading}
            isStartingProcessing={isStartingProcessing}
            onStartProcessing={startProcessing}
            onRemoveFile={removeFile}
            onClearCompleted={clearCompleted}
          />
        </div>
      </div>
    </div>
  );
}
