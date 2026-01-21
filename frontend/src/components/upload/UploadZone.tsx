"use client";

import { useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { Upload } from "lucide-react";
import { cn } from "@/lib/utils";

const ALLOWED_TYPES = [".pdf", ".txt", ".md", ".docx", ".xlsx"];

interface UploadZoneProps {
  isDragging: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onFileSelect: (files: FileList) => void;
}

export default function UploadZone({
  isDragging,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileSelect,
}: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      onFileSelect(e.target.files);
    }
  };

  return (
    <motion.div
      className={cn(
        "relative rounded-lg border-2 border-dashed transition-all duration-300",
        "p-12 text-center cursor-pointer group overflow-hidden",
        isDragging
          ? "border-foreground bg-muted"
          : "border-border hover:border-muted-foreground hover:bg-muted/30"
      )}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      whileHover={{ scale: 1.005 }}
      whileTap={{ scale: 0.995 }}
    >
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        multiple
        accept={ALLOWED_TYPES.join(",")}
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
          {ALLOWED_TYPES.map((type) => (
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
  );
}
