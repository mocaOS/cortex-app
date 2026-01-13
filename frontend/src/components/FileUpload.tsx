"use client";

import { useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Upload,
  FileText,
  CheckCircle,
  AlertCircle,
  Loader2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface FileUploadProps {
  onUpload: () => void;
}

interface UploadingFile {
  file: File;
  status: "uploading" | "success" | "error";
  message?: string;
  documentId?: string;
}

export default function FileUpload({ onUpload }: FileUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const allowedTypes = [".pdf", ".txt", ".md", ".docx", ".xlsx"];

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const uploadFile = async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || "Upload failed");
      }

      const data = await res.json();
      return { success: true, data };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : "Upload failed",
      };
    }
  };

  const processFiles = async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return allowedTypes.includes(ext);
    });

    if (validFiles.length === 0) return;

    const newUploads: UploadingFile[] = validFiles.map((file) => ({
      file,
      status: "uploading" as const,
    }));

    setUploadingFiles((prev) => [...prev, ...newUploads]);

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      const result = await uploadFile(file);

      setUploadingFiles((prev) =>
        prev.map((uf) =>
          uf.file === file
            ? result.success
              ? {
                  ...uf,
                  status: "success" as const,
                  documentId: result.data.document_id,
                  message: "Processing started",
                }
              : {
                  ...uf,
                  status: "error" as const,
                  message: result.error,
                }
            : uf
        )
      );
    }

    onUpload();
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
    setUploadingFiles((prev) => prev.filter((uf) => uf.status === "uploading"));
  };

  return (
    <div className="space-y-6">
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
            <div className="flex items-center justify-between p-4 border-b border-white/5">
              <h4 className="text-sm font-medium text-white/80">
                Upload Progress
              </h4>
              {uploadingFiles.some((f) => f.status !== "uploading") && (
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
                  className="flex items-center gap-4 p-4"
                >
                  <div
                    className={cn(
                      "w-10 h-10 rounded-lg flex items-center justify-center",
                      uf.status === "uploading" && "bg-ocean-500/20",
                      uf.status === "success" && "bg-mint-500/20",
                      uf.status === "error" && "bg-coral-500/20"
                    )}
                  >
                    {uf.status === "uploading" && (
                      <Loader2 className="w-5 h-5 text-ocean-400 animate-spin" />
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
                        uf.status === "uploading" && "text-white/40",
                        uf.status === "success" && "text-mint-400/70",
                        uf.status === "error" && "text-coral-400/70"
                      )}
                    >
                      {uf.status === "uploading" && "Uploading..."}
                      {uf.status === "success" && uf.message}
                      {uf.status === "error" && uf.message}
                    </p>
                  </div>

                  <button
                    onClick={() => removeFile(uf.file)}
                    className="p-2 hover:bg-white/5 rounded-lg transition-colors"
                  >
                    <X className="w-4 h-4 text-white/40" />
                  </button>
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
