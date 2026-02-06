"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { X, AlertTriangle, Loader2, Trash2, Check } from "lucide-react";
import { api } from "@/lib/api";
import type { SystemResetRequest, SystemResetResponse } from "@/types";

interface SystemResetModalProps {
  onClose: () => void;
  onReset?: () => void;
}

export function SystemResetModal({ onClose, onReset }: SystemResetModalProps) {
  // Deletion options state
  const [deleteDocuments, setDeleteDocuments] = useState(true);
  const [deleteUploadedFiles, setDeleteUploadedFiles] = useState(true);
  const [deleteCustomInputs, setDeleteCustomInputs] = useState(true);
  const [deleteCollections, setDeleteCollections] = useState(true);
  const [deleteApiKeys, setDeleteApiKeys] = useState(false);

  // Confirmation input state
  const [confirmText, setConfirmText] = useState("");
  
  // Operation state
  const [isResetting, setIsResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SystemResetResponse | null>(null);

  const isConfirmValid = confirmText === "DELETE";
  const hasSelection = deleteDocuments || deleteUploadedFiles || deleteCustomInputs || deleteCollections || deleteApiKeys;
  const canReset = isConfirmValid && hasSelection && !isResetting;

  const handleReset = async () => {
    if (!canReset) return;

    setIsResetting(true);
    setError(null);

    try {
      const request: SystemResetRequest = {
        delete_documents: deleteDocuments,
        delete_uploaded_files: deleteUploadedFiles,
        delete_custom_inputs: deleteCustomInputs,
        delete_collections: deleteCollections,
        delete_api_keys: deleteApiKeys,
      };

      const response = await api.resetSystem(request);
      setResult(response);
      onReset?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reset system");
    } finally {
      setIsResetting(false);
    }
  };

  // If we have a result, show the success screen
  if (result) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50 p-4"
        onClick={onClose}
      >
        <motion.div
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.9, opacity: 0 }}
          className="bg-card rounded-xl border border-border p-6 max-w-md w-full"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="text-center mb-6">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-accent/20 mb-4">
              <Check className="w-7 h-7 text-accent" />
            </div>
            <h2 className="text-xl font-bold text-foreground">System Reset Complete</h2>
            <p className="text-muted-foreground mt-2">{result.message}</p>
          </div>

          {/* Summary of what was deleted */}
          <div className="bg-muted rounded-lg p-4 mb-6 space-y-2 text-sm">
            {result.documents_deleted > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Documents deleted:</span>
                <span className="text-foreground font-medium">{result.documents_deleted}</span>
              </div>
            )}
            {result.entities_removed > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Entities removed:</span>
                <span className="text-foreground font-medium">{result.entities_removed}</span>
              </div>
            )}
            {result.communities_removed > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Communities removed:</span>
                <span className="text-foreground font-medium">{result.communities_removed}</span>
              </div>
            )}
            {result.collections_deleted > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Collections deleted:</span>
                <span className="text-foreground font-medium">{result.collections_deleted}</span>
              </div>
            )}
            {result.api_keys_deleted > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">API keys deleted:</span>
                <span className="text-foreground font-medium">{result.api_keys_deleted}</span>
              </div>
            )}
            {result.uploaded_files_deleted > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Uploaded files deleted:</span>
                <span className="text-foreground font-medium">{result.uploaded_files_deleted}</span>
              </div>
            )}
            {result.custom_inputs_deleted > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Custom inputs deleted:</span>
                <span className="text-foreground font-medium">{result.custom_inputs_deleted}</span>
              </div>
            )}
            {result.processing_cancelled > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Processing tasks cancelled:</span>
                <span className="text-foreground font-medium">{result.processing_cancelled}</span>
              </div>
            )}
          </div>

          <button
            onClick={onClose}
            className="w-full py-2.5 bg-accent hover:bg-accent/90 text-accent-foreground rounded-lg transition-colors"
          >
            Done
          </button>
        </motion.div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="bg-card rounded-xl border border-destructive/30 p-6 max-w-2xl w-full"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-destructive/20">
              <AlertTriangle className="w-6 h-6 text-destructive" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-foreground">System Reset</h2>
              <p className="text-sm text-muted-foreground">This action cannot be undone</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Warning */}
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-4 mb-6">
          <p className="text-sm text-destructive">
            <strong>Warning:</strong> You are about to permanently delete data from your knowledge base. 
            This operation cannot be undone. Please review your selections carefully.
          </p>
        </div>

        {/* Selection checkboxes */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-foreground mb-3">
            Select what to delete:
          </label>

          <div className="grid grid-cols-2 gap-3">
            {/* Documents */}
            <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
              <input
                type="checkbox"
                checked={deleteDocuments}
                onChange={(e) => setDeleteDocuments(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
              />
              <div className="flex-1">
                <div className="font-medium text-foreground text-sm">Documents & Knowledge Graph</div>
                <div className="text-xs text-muted-foreground">
                  Documents, entities, relationships
                </div>
              </div>
            </label>

            {/* Uploaded Files */}
            <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
              <input
                type="checkbox"
                checked={deleteUploadedFiles}
                onChange={(e) => setDeleteUploadedFiles(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
              />
              <div className="flex-1">
                <div className="font-medium text-foreground text-sm">Uploaded Files</div>
                <div className="text-xs text-muted-foreground">
                  Original files stored on disk
                </div>
              </div>
            </label>

            {/* Custom Inputs */}
            <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
              <input
                type="checkbox"
                checked={deleteCustomInputs}
                onChange={(e) => setDeleteCustomInputs(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
              />
              <div className="flex-1">
                <div className="font-medium text-foreground text-sm">Custom Inputs</div>
                <div className="text-xs text-muted-foreground">
                  Q&A, text, and markdown content
                </div>
              </div>
            </label>

            {/* Collections */}
            <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
              <input
                type="checkbox"
                checked={deleteCollections}
                onChange={(e) => setDeleteCollections(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
              />
              <div className="flex-1">
                <div className="font-medium text-foreground text-sm">Collections</div>
                <div className="text-xs text-muted-foreground">
                  All except default collection
                </div>
              </div>
            </label>

            {/* API Keys - Extra warning - spans both columns */}
            <label className="col-span-2 flex items-start gap-3 p-3 bg-destructive/5 border border-destructive/20 rounded-lg cursor-pointer hover:bg-destructive/10 transition-colors">
              <input
                type="checkbox"
                checked={deleteApiKeys}
                onChange={(e) => setDeleteApiKeys(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
              />
              <div className="flex-1">
                <div className="font-medium text-destructive text-sm">API Keys</div>
                <div className="text-xs text-destructive/80">
                  All API keys will be deleted. You will need to create new ones.
                </div>
              </div>
            </label>
          </div>
        </div>

        {/* Confirmation input */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-foreground mb-2">
            Type <span className="font-mono font-bold text-destructive">DELETE</span> to confirm:
          </label>
          <input
            type="text"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type DELETE"
            className="w-full px-4 py-2.5 bg-background border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-destructive/50 focus:border-destructive"
            disabled={isResetting}
          />
          {confirmText && !isConfirmValid && (
            <p className="text-sm text-destructive mt-1">
              Please type DELETE exactly (case-sensitive)
            </p>
          )}
        </div>

        {/* Error message */}
        {error && (
          <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-destructive text-sm mb-6">
            {error}
          </div>
        )}

        {/* Action buttons */}
        <div className="flex gap-3">
          <button
            onClick={onClose}
            disabled={isResetting}
            className="flex-1 py-2.5 bg-muted hover:bg-muted/80 text-foreground rounded-lg transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleReset}
            disabled={!canReset}
            className="flex-1 py-2.5 bg-destructive hover:bg-destructive/90 disabled:opacity-50 disabled:cursor-not-allowed text-destructive-foreground rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            {isResetting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Resetting...
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                Reset System
              </>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
