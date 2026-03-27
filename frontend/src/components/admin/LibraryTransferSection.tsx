"use client";

import { useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Download,
  Upload,
  HardDrive,
  FileArchive,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Stats, TaskProgress } from "@/types";
import { formatBytes } from "@/lib/utils";

interface LibraryTransferSectionProps {
  stats: Stats | null;
  onImportComplete?: () => void;
}

type ExportState = "idle" | "exporting" | "ready" | "downloading" | "error";
type ImportState = "idle" | "importing" | "done" | "error";

export function LibraryTransferSection({ stats, onImportComplete }: LibraryTransferSectionProps) {
  // Export state
  const [exportState, setExportState] = useState<ExportState>("idle");
  const [exportTaskId, setExportTaskId] = useState<string | null>(null);
  const [exportProgress, setExportProgress] = useState<TaskProgress | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  // Import state
  const [importState, setImportState] = useState<ImportState>("idle");
  const [importMode, setImportMode] = useState<"clean" | "replace">("clean");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importProgress, setImportProgress] = useState<TaskProgress | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // ==========================================================================
  // Export
  // ==========================================================================

  const startExport = async () => {
    try {
      setExportState("exporting");
      setExportError(null);
      setExportProgress(null);

      const { task_id } = await api.startLibraryExport();
      setExportTaskId(task_id);

      // Poll for progress
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getTaskStatus(task_id);
          setExportProgress(status);

          if (status.status === "completed") {
            stopPolling();
            setExportState("ready");
          } else if (status.status === "failed") {
            stopPolling();
            setExportError(status.error || "Export failed");
            setExportState("error");
          }
        } catch {
          stopPolling();
          setExportError("Lost connection to server");
          setExportState("error");
        }
      }, 2000);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Failed to start export");
      setExportState("error");
    }
  };

  const downloadExport = async () => {
    if (!exportTaskId) return;
    try {
      setExportState("downloading");
      await api.downloadLibraryExport(exportTaskId);
      setExportState("idle");
      setExportTaskId(null);
      setExportProgress(null);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Download failed");
      setExportState("error");
    }
  };

  // ==========================================================================
  // Import
  // ==========================================================================

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".zip")) {
      setImportFile(file);
      setImportError(null);
    } else {
      setImportError("Please select a .zip file");
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setImportFile(file);
      setImportError(null);
    }
  };

  const startImport = async () => {
    if (!importFile) return;
    if (importMode === "replace" && confirmText !== "DELETE") return;

    try {
      setImportState("importing");
      setImportError(null);
      setImportProgress(null);
      setImportResult(null);

      const { task_id } = await api.startLibraryImport(importFile, importMode);

      // Poll for progress
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getTaskStatus(task_id);
          setImportProgress(status);

          if (status.status === "completed") {
            stopPolling();
            setImportResult(status.result || null);
            setImportState("done");
            // Clear client-side caches (same as system reset)
            localStorage.removeItem("dedup_dismissed");
            localStorage.removeItem("moca_community_detection_task");
            sessionStorage.removeItem("regenerateStep");
            sessionStorage.removeItem("regenerateStartedAt");
            sessionStorage.removeItem("regenerateTaskId");
            onImportComplete?.();
          } else if (status.status === "failed") {
            stopPolling();
            setImportError(status.error || "Import failed");
            setImportState("error");
          }
        } catch {
          stopPolling();
          setImportError("Lost connection to server");
          setImportState("error");
        }
      }, 2000);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Failed to start import");
      setImportState("error");
    }
  };

  const resetImport = () => {
    setImportState("idle");
    setImportFile(null);
    setImportError(null);
    setImportProgress(null);
    setImportResult(null);
    setConfirmText("");
  };

  // ==========================================================================
  // Render helpers
  // ==========================================================================

  const progressPercent = (p: TaskProgress | null) =>
    p ? Math.round(p.progress_percent || 0) : 0;

  const hasData = stats && ((stats.document_count ?? 0) > 0 || (stats.entity_count ?? 0) > 0);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.15 }}
    >
      <div className="border border-border rounded-xl overflow-hidden">
        {/* Header */}
        <div className="bg-card px-6 py-4 border-b border-border">
          <div className="flex items-center gap-3">
            <HardDrive className="w-5 h-5 text-muted-foreground" />
            <div>
              <h2 className="text-lg font-semibold text-foreground">Data Management</h2>
              <p className="text-muted-foreground text-sm">Export or import your entire library including documents and knowledge graph</p>
            </div>
          </div>
        </div>

        {/* Content: two cards */}
        <div className="p-6 bg-card grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* Export Card */}
          <div className="border border-border rounded-lg p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Download className="w-4 h-4 text-muted-foreground" />
              <h3 className="font-medium text-foreground">Export Library</h3>
            </div>
            <p className="text-muted-foreground text-sm leading-relaxed">
              Download your entire library as a portable archive. Includes all documents, entities, relationships, communities, and embeddings.
            </p>

            {/* Stats summary */}
            {stats && (
              <div className="grid grid-cols-3 gap-2 text-sm">
                <div className="bg-background rounded-md px-3 py-2 text-center">
                  <div className="text-foreground font-mono font-medium">{stats.document_count ?? 0}</div>
                  <div className="text-muted-foreground text-xs">Documents</div>
                </div>
                <div className="bg-background rounded-md px-3 py-2 text-center">
                  <div className="text-foreground font-mono font-medium">{stats.entity_count ?? 0}</div>
                  <div className="text-muted-foreground text-xs">Entities</div>
                </div>
                <div className="bg-background rounded-md px-3 py-2 text-center">
                  <div className="text-foreground font-mono font-medium">{stats.relationship_count ?? 0}</div>
                  <div className="text-muted-foreground text-xs">Relations</div>
                </div>
              </div>
            )}

            {/* Export progress */}
            <AnimatePresence mode="wait">
              {exportState === "exporting" && exportProgress && (
                <motion.div
                  key="export-progress"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="space-y-2"
                >
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span>{exportProgress.message}</span>
                  </div>
                  <div className="w-full bg-background rounded-full h-2">
                    <div
                      className="bg-accent h-2 rounded-full transition-all duration-300"
                      style={{ width: `${progressPercent(exportProgress)}%` }}
                    />
                  </div>
                </motion.div>
              )}

              {exportState === "ready" && (
                <motion.div
                  key="export-ready"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex items-center gap-2 text-sm text-green-500"
                >
                  <CheckCircle2 className="w-4 h-4" />
                  <span>Export ready{exportProgress?.result?.file_size ? ` (${formatBytes(exportProgress.result.file_size as number)})` : ""}</span>
                </motion.div>
              )}

              {exportState === "error" && (
                <motion.div
                  key="export-error"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex items-center gap-2 text-sm text-destructive"
                >
                  <AlertTriangle className="w-4 h-4" />
                  <span>{exportError}</span>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Export button */}
            {exportState === "idle" || exportState === "error" ? (
              <button
                onClick={startExport}
                disabled={!hasData}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-accent/10 hover:bg-accent/20 text-accent border border-accent/30 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Download className="w-4 h-4" />
                <span>Export Library</span>
              </button>
            ) : exportState === "exporting" ? (
              <button
                disabled
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-muted text-muted-foreground border border-border rounded-lg cursor-not-allowed"
              >
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Exporting...</span>
              </button>
            ) : exportState === "ready" ? (
              <button
                onClick={downloadExport}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-green-500/10 hover:bg-green-500/20 text-green-500 border border-green-500/30 rounded-lg transition-colors"
              >
                <FileArchive className="w-4 h-4" />
                <span>Download Export</span>
              </button>
            ) : exportState === "downloading" ? (
              <button
                disabled
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-muted text-muted-foreground border border-border rounded-lg cursor-not-allowed"
              >
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Downloading...</span>
              </button>
            ) : null}
          </div>

          {/* Import Card */}
          <div className="border border-border rounded-lg p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Upload className="w-4 h-4 text-muted-foreground" />
              <h3 className="font-medium text-foreground">Import Library</h3>
            </div>
            <p className="text-muted-foreground text-sm leading-relaxed">
              Restore a library export from another MOCA instance. Imports all documents, knowledge graph data, and embeddings.
            </p>

            {importState === "idle" && (
              <>
                {/* Mode selector */}
                <div className="space-y-2">
                  <label className="text-sm text-muted-foreground">Import mode</label>
                  <div className="flex gap-3">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="importMode"
                        value="clean"
                        checked={importMode === "clean"}
                        onChange={() => { setImportMode("clean"); setConfirmText(""); }}
                        className="accent-accent"
                      />
                      <span className="text-sm text-foreground">Clean import</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="importMode"
                        value="replace"
                        checked={importMode === "replace"}
                        onChange={() => setImportMode("replace")}
                        className="accent-accent"
                      />
                      <span className="text-sm text-foreground">Replace all</span>
                    </label>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {importMode === "clean"
                      ? "Requires an empty instance. Reset existing data first if needed."
                      : "Deletes all existing data before importing. This cannot be undone."}
                  </p>
                </div>

                {/* File drop zone */}
                <div
                  onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
                  onDragLeave={() => setIsDragOver(false)}
                  onDrop={handleFileDrop}
                  onClick={() => fileInputRef.current?.click()}
                  className={`
                    border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors
                    ${isDragOver ? "border-accent bg-accent/5" : "border-border hover:border-muted-foreground/50"}
                    ${importFile ? "border-green-500/30 bg-green-500/5" : ""}
                  `}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".zip"
                    onChange={handleFileSelect}
                    className="hidden"
                  />
                  {importFile ? (
                    <div className="flex items-center justify-center gap-2">
                      <FileArchive className="w-5 h-5 text-green-500" />
                      <span className="text-sm text-foreground">{importFile.name}</span>
                      <span className="text-xs text-muted-foreground">({formatBytes(importFile.size)})</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); setImportFile(null); }}
                        className="ml-2 text-muted-foreground hover:text-foreground"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-1">
                      <FileArchive className="w-8 h-8 text-muted-foreground/50 mx-auto" />
                      <p className="text-sm text-muted-foreground">Drop export ZIP here or click to browse</p>
                    </div>
                  )}
                </div>

                {/* Replace mode confirmation */}
                {importMode === "replace" && importFile && (
                  <div className="space-y-2">
                    <label className="text-sm text-destructive font-medium">
                      Type DELETE to confirm replacing all data
                    </label>
                    <input
                      type="text"
                      value={confirmText}
                      onChange={(e) => setConfirmText(e.target.value)}
                      placeholder="DELETE"
                      className="w-full px-3 py-2 bg-background border border-destructive/30 rounded-lg text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-destructive"
                    />
                  </div>
                )}

                {/* Import button */}
                <button
                  onClick={startImport}
                  disabled={!importFile || (importMode === "replace" && confirmText !== "DELETE")}
                  className={`
                    w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg transition-colors
                    disabled:opacity-50 disabled:cursor-not-allowed
                    ${importMode === "replace"
                      ? "bg-destructive/10 hover:bg-destructive/20 text-destructive border border-destructive/30"
                      : "bg-accent/10 hover:bg-accent/20 text-accent border border-accent/30"}
                  `}
                >
                  <Upload className="w-4 h-4" />
                  <span>{importMode === "replace" ? "Replace & Import" : "Import Library"}</span>
                </button>
              </>
            )}

            {/* Import progress */}
            {importState === "importing" && (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>{importProgress?.message || "Starting import..."}</span>
                </div>
                <div className="w-full bg-background rounded-full h-2">
                  <div
                    className="bg-accent h-2 rounded-full transition-all duration-300"
                    style={{ width: `${progressPercent(importProgress)}%` }}
                  />
                </div>
              </div>
            )}

            {/* Import done */}
            {importState === "done" && importResult && (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-green-500">
                  <CheckCircle2 className="w-4 h-4" />
                  <span className="text-sm font-medium">Import complete</span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  {(importResult.documents_imported as number) > 0 && (
                    <div className="text-muted-foreground">{importResult.documents_imported as number} documents</div>
                  )}
                  {(importResult.entities_imported as number) > 0 && (
                    <div className="text-muted-foreground">{importResult.entities_imported as number} entities</div>
                  )}
                  {(importResult.relationships_imported as number) > 0 && (
                    <div className="text-muted-foreground">{importResult.relationships_imported as number} relationships</div>
                  )}
                  {(importResult.communities_imported as number) > 0 && (
                    <div className="text-muted-foreground">{importResult.communities_imported as number} communities</div>
                  )}
                  {(importResult.files_imported as number) > 0 && (
                    <div className="text-muted-foreground">{importResult.files_imported as number} files</div>
                  )}
                </div>
                {/* Warnings */}
                {(importResult.warnings as string[])?.length > 0 && (
                  <div className="space-y-1">
                    {(importResult.warnings as string[]).map((w, i) => (
                      <div key={i} className="flex items-start gap-2 text-xs text-yellow-500">
                        <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                        <span>{w}</span>
                      </div>
                    ))}
                  </div>
                )}
                <button
                  onClick={resetImport}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-muted hover:bg-muted/80 text-foreground border border-border rounded-lg transition-colors text-sm"
                >
                  Done
                </button>
              </div>
            )}

            {/* Import error */}
            {importState === "error" && (
              <div className="space-y-3">
                <div className="flex items-start gap-2 text-sm text-destructive">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span>{importError}</span>
                </div>
                <button
                  onClick={resetImport}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-muted hover:bg-muted/80 text-foreground border border-border rounded-lg transition-colors text-sm"
                >
                  Try Again
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
