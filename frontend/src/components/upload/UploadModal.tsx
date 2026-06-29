"use client";

import { useState, useCallback, useEffect } from "react";
import { X, FolderOpen } from "lucide-react";
import CollectionSelector from "../CollectionSelector";
import UploadZone from "./UploadZone";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";

const ALLOWED_TYPES = [
  ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
  ".html", ".htm",
  ".txt", ".md", ".mdx", ".markdown", ".rst",
  ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
  ".wav", ".mp3", ".webvtt", ".vtt",
  ".tex", ".latex",
  ".xml",
];

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onFilesSelected: (files: File[], collectionId?: string) => void;
}

export default function UploadModal({ isOpen, onClose, onFilesSelected }: UploadModalProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [selectedCollection, setSelectedCollection] = useState<string | undefined>(undefined);

  useBodyScrollLock(isOpen);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, onClose]);

  const processFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return ALLOWED_TYPES.includes(ext);
    });

    if (validFiles.length === 0) return;

    onFilesSelected(validFiles, selectedCollection);
    onClose();
  }, [selectedCollection, onFilesSelected, onClose]);

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

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
        onClick={onClose}
      />

      <div
        className="relative w-full max-w-3xl mx-4 bg-card border border-border rounded-2xl shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="Upload Documents"
      >
        <div className="flex items-center justify-between p-6 border-b border-border">
          <h2 className="text-lg font-semibold text-foreground">Upload Documents</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <FolderOpen className="w-4 h-4" />
              <span>Upload to:</span>
            </div>
            <CollectionSelector
              value={selectedCollection}
              onChange={setSelectedCollection}
              allowCreate={true}
              autoSelectDefault={true}
              className="w-64"
            />
          </div>

          <UploadZone
            isDragging={isDragging}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onFileSelect={handleFileSelect}
          />
        </div>
      </div>
    </div>
  );
}
