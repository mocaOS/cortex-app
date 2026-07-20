"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Link as LinkIcon,
  X,
  Loader2,
  AlertCircle,
  AlertTriangle,
  Copy,
  Check,
  Plus,
  Ban,
} from "lucide-react";
import { api } from "@/lib/api";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import type { AppGrant, AppGrantCreateResponse } from "@/types";

interface AppGrantsModalProps {
  appId: string;
  appName: string;
  onClose: () => void;
  /** Called whenever the grant list changes so the parent can refresh counts. */
  onChanged?: () => void;
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export function AppGrantsModal({
  appId,
  appName,
  onClose,
  onChanged,
}: AppGrantsModalProps) {
  const [grants, setGrants] = useState<AppGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create form
  const [label, setLabel] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [expiresHours, setExpiresHours] = useState("");
  const [creating, setCreating] = useState(false);

  // One-time share URL (shown once after creation)
  const [created, setCreated] = useState<AppGrantCreateResponse | null>(null);
  const [copied, setCopied] = useState(false);

  // Revoke
  const [revokeConfirm, setRevokeConfirm] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  useBodyScrollLock(true);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const fetchGrants = useCallback(async () => {
    try {
      const data = await api.listAppGrants(appId);
      setGrants(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load share links");
    } finally {
      setLoading(false);
    }
  }, [appId]);

  useEffect(() => {
    fetchGrants();
  }, [fetchGrants]);

  const shareUrl = created
    ? `${typeof window !== "undefined" ? window.location.origin : ""}${created.share_path}`
    : "";

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    setCreated(null);
    setCopied(false);
    try {
      const hours = parseInt(expiresHours, 10);
      const grant = await api.createAppGrant(appId, {
        label: label.trim(),
        role,
        ...(Number.isFinite(hours) && hours > 0 ? { expires_hours: hours } : {}),
      });
      setCreated(grant);
      setLabel("");
      setExpiresHours("");
      await fetchGrants();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create share link");
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard unavailable (e.g. non-secure context) — the URL stays
      // visible and selectable for manual copy.
    }
  };

  const handleRevoke = async (grantId: string) => {
    setRevoking(grantId);
    setError(null);
    try {
      await api.revokeAppGrant(appId, grantId);
      setRevokeConfirm(null);
      await fetchGrants();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke share link");
    } finally {
      setRevoking(null);
    }
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50 p-4"
        onClick={onClose}
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-card rounded-xl border border-border p-6 max-w-xl w-full max-h-[80vh] overflow-y-auto"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--accent)]/10">
                <LinkIcon className="w-5 h-5 text-[var(--accent)]" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-foreground">
                  Share Links — {appName}
                </h3>
                <p className="text-xs text-muted-foreground">
                  Grant access to this app without a Cortex login
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 text-xs text-red-400 p-3 rounded-lg bg-red-500/10 border border-red-500/20 mb-4">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* One-time share URL */}
          {created && (
            <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 space-y-2">
              <div className="flex items-center gap-2 text-xs font-medium text-amber-400">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                Copy this link now — it will not be shown again.
              </div>
              <div className="flex items-center gap-2">
                <code className="flex-1 min-w-0 px-2.5 py-1.5 text-xs font-mono rounded bg-background border border-border/50 text-foreground truncate select-all">
                  {shareUrl}
                </code>
                <button
                  onClick={handleCopy}
                  className="shrink-0 flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 transition-colors"
                >
                  {copied ? (
                    <>
                      <Check className="w-3 h-3" /> Copied
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" /> Copy
                    </>
                  )}
                </button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Anyone with this link can use the app as{" "}
                <span className="text-foreground font-medium">{created.role}</span>
                {created.expires_at
                  ? ` until ${formatDate(created.expires_at)}`
                  : " with no expiry"}
                . Revoke it below at any time.
              </p>
            </div>
          )}

          {/* Create grant */}
          <div className="mb-4">
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              New Share Link
            </h4>
            <div className="flex flex-wrap gap-2">
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label (e.g. Marketing team)"
                className="flex-1 min-w-[140px] px-3 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50"
                onKeyDown={(e) => e.key === "Enter" && !creating && handleCreate()}
              />
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as "viewer" | "editor")}
                className="px-2 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground focus:outline-none focus:border-[var(--accent)]/50"
                aria-label="Grant role"
              >
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
              </select>
              <input
                type="number"
                min={1}
                value={expiresHours}
                onChange={(e) => setExpiresHours(e.target.value)}
                placeholder="Expiry (h)"
                title="Optional expiry in hours — leave empty for no expiry"
                className="w-24 px-2 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50"
              />
              <button
                onClick={handleCreate}
                disabled={creating}
                className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {creating ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Plus className="w-3 h-3" />
                )}
                Create
              </button>
            </div>
          </div>

          {/* Divider */}
          <div className="border-t border-border/30 mb-4" />

          {/* Grants list */}
          <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
            Existing Links
          </h4>
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : grants.length === 0 ? (
            <p className="text-xs text-muted-foreground py-4 text-center">
              No share links yet. Create one above.
            </p>
          ) : (
            <div className="space-y-1">
              {grants.map((grant) => (
                <div
                  key={grant.id}
                  className={`flex items-center gap-2 px-3 py-2 rounded border border-border/30 ${
                    grant.revoked ? "opacity-50" : ""
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-foreground truncate">
                        {grant.label || "Unnamed link"}
                      </span>
                      <span
                        className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${
                          grant.role === "editor"
                            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                            : "bg-blue-500/10 text-blue-400 border-blue-500/20"
                        }`}
                      >
                        {grant.role}
                      </span>
                      {grant.revoked && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/10 text-red-400 border border-red-500/20">
                          <Ban className="w-2.5 h-2.5" />
                          Revoked
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      Created {formatDate(grant.created_at)}
                      {grant.expires_at
                        ? ` · Expires ${formatDate(grant.expires_at)}`
                        : " · No expiry"}
                    </p>
                  </div>

                  {!grant.revoked &&
                    (revokeConfirm === grant.id ? (
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          onClick={() => handleRevoke(grant.id)}
                          disabled={revoking === grant.id}
                          className="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 disabled:opacity-50 transition-colors"
                        >
                          {revoking === grant.id ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            "Confirm"
                          )}
                        </button>
                        <button
                          onClick={() => setRevokeConfirm(null)}
                          className="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setRevokeConfirm(grant.id)}
                        className="shrink-0 flex items-center gap-1 text-xs text-red-400/70 hover:text-red-400 transition-colors"
                      >
                        <Ban className="w-3 h-3" />
                        Revoke
                      </button>
                    ))}
                </div>
              ))}
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
