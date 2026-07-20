"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutGrid,
  Upload,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  ToggleLeft,
  ToggleRight,
  Settings,
  Link as LinkIcon,
  ExternalLink,
  Lock,
  PenLine,
} from "lucide-react";
import Link from "next/link";
import { api, AppInstallError } from "@/lib/api";
import { useIsMounted } from "@/lib/hooks";
import { AppConfigModal } from "./AppConfigModal";
import { AppGrantsModal } from "./AppGrantsModal";
import type { AppInfo, Collection } from "@/types";

/**
 * Admin section for the Apps subsystem (in-instance app hosting).
 *
 * Self-gating like X402Section: probes GET /api/admin/apps once on mount and
 * renders nothing at all when the backend's ENABLE_APPS flag is off (the
 * endpoint 404s) — zero UI traces when disabled.
 */
export function AppsManager() {
  const [apps, setApps] = useState<AppInfo[]>([]);
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedApp, setExpandedApp] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const mounted = useIsMounted();

  // Install
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installIssues, setInstallIssues] = useState<string[] | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Optional collection restriction, applied when the app's manifest declares
  // "user-selected" collection scoping (ignored otherwise by the backend).
  const [collections, setCollections] = useState<Collection[]>([]);
  const [installCollections, setInstallCollections] = useState<string[]>([]);

  // Modals
  const [configModalApp, setConfigModalApp] = useState<AppInfo | null>(null);
  const [grantsModalApp, setGrantsModalApp] = useState<AppInfo | null>(null);

  const fetchApps = useCallback(async () => {
    try {
      const data = await api.listApps();
      if (!mounted.current) return;
      setApps(data);
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : "Failed to load apps");
    }
  }, [mounted]);

  // Gating fetch: only a successful first list marks the feature available.
  useEffect(() => {
    (async () => {
      try {
        const data = await api.listApps();
        if (!mounted.current) return;
        setApps(data);
        setAvailable(true);
        // Non-fatal: collections feed the optional install-time restriction.
        api.getCollections().then(
          (res) => {
            if (mounted.current) setCollections(res.collections);
          },
          () => {},
        );
      } catch {
        // Feature gate fetch failed (ENABLE_APPS off / older backend) — hide.
      } finally {
        if (mounted.current) setLoading(false);
      }
    })();
  }, [mounted]);

  const handleToggleApp = async (app: AppInfo) => {
    setActionLoading(app.id);
    try {
      const updated = await api.updateApp(app.id, { enabled: !app.enabled });
      setApps((prev) => prev.map((a) => (a.id === app.id ? updated : a)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update app");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeleteApp = async (appId: string) => {
    setActionLoading(appId);
    try {
      await api.deleteApp(appId);
      setApps((prev) => prev.filter((a) => a.id !== appId));
      setDeleteConfirm(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete app");
    } finally {
      setActionLoading(null);
    }
  };

  const handleInstall = async (file: File) => {
    setInstalling(true);
    setInstallError(null);
    setInstallIssues(null);
    try {
      const installed = await api.installApp(
        file,
        installCollections.length ? installCollections : undefined,
      );
      if (!mounted.current) return;
      await fetchApps();
      // Open the config wizard right away when the manifest declares config.
      if (installed.config_status === "needs_setup") {
        setConfigModalApp(installed);
      }
    } catch (err) {
      if (!mounted.current) return;
      if (err instanceof AppInstallError) {
        // Show ALL validation issues, not a flattened single line.
        setInstallIssues(err.issues);
      } else {
        setInstallError(err instanceof Error ? err.message : "Installation failed");
      }
    } finally {
      if (mounted.current) setInstalling(false);
    }
  };

  const handleFileSelected = (files: FileList | null) => {
    const file = files?.[0];
    if (file) handleInstall(file);
    // Reset so selecting the same file again re-triggers onChange.
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (installing) return;
    handleFileSelected(e.dataTransfer.files);
  };

  // Hidden until the gating fetch resolves; hidden entirely when the flag is off.
  if (loading || !available) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.112 }}
    >
      <div className="glass rounded-xl overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-border/50">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <LayoutGrid className="w-5 h-5 text-accent" />
              <h2 className="text-lg font-semibold text-foreground">Apps</h2>
              {apps.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  ({apps.filter((a) => a.enabled).length}/{apps.length} active)
                </span>
              )}
            </div>
            <Link
              href="/apps"
              className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground rounded transition-colors"
              title="Open the app launcher"
            >
              <ExternalLink className="w-3 h-3" />
              Open Launcher
            </Link>
          </div>
          <p className="text-muted-foreground text-sm mt-1">
            Sandboxed web apps hosted by this instance with scoped API access
          </p>
        </div>

        <div className="p-6 space-y-4">
          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 text-xs text-red-400 p-2 rounded bg-red-500/10 border border-red-500/20">
              <AlertCircle className="w-3 h-3 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Installed Apps */}
          <div>
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Installed Apps
            </h4>

            {apps.length === 0 ? (
              <p className="text-xs text-muted-foreground py-4 text-center">
                No apps installed. Upload an app package below.
              </p>
            ) : (
              <div className="space-y-1">
                {apps.map((app) => (
                  <div
                    key={app.id}
                    className="border border-border/30 rounded-lg overflow-hidden"
                  >
                    {/* App row */}
                    <div className="flex items-center gap-2 px-3 py-2">
                      <button
                        onClick={() =>
                          setExpandedApp(expandedApp === app.id ? null : app.id)
                        }
                        className="text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {expandedApp === app.id ? (
                          <ChevronDown className="w-3 h-3" />
                        ) : (
                          <ChevronRight className="w-3 h-3" />
                        )}
                      </button>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-foreground truncate">
                            {app.name}
                          </span>
                          <span className="text-[10px] text-muted-foreground font-mono">
                            v{app.version}
                          </span>
                          {/* Key scope badge */}
                          <span
                            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border ${
                              app.key_scope === "read_write"
                                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                                : "bg-blue-500/10 text-blue-400 border-blue-500/20"
                            }`}
                            title={
                              app.key_scope === "read_write"
                                ? "The app's key can read and write"
                                : "The app's key is read-only"
                            }
                          >
                            {app.key_scope === "read_write" ? (
                              <PenLine className="w-2.5 h-2.5" />
                            ) : (
                              <Lock className="w-2.5 h-2.5" />
                            )}
                            {app.key_scope === "read_write" ? "read + write" : "read"}
                          </span>
                          {/* Endpoint chips */}
                          {app.endpoints.map((ep) => (
                            <span
                              key={ep}
                              className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono text-muted-foreground bg-muted border border-border/50"
                            >
                              {ep}
                            </span>
                          ))}
                          {/* Config status badge */}
                          {app.config_status === "needs_setup" && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
                              Needs setup
                            </span>
                          )}
                          {/* Grants count */}
                          {app.sharing_links && app.grants_count > 0 && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-muted-foreground bg-muted border border-border/50">
                              <LinkIcon className="w-2.5 h-2.5" />
                              {app.grants_count}{" "}
                              {app.grants_count === 1 ? "link" : "links"}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate mt-0.5">
                          {app.description}
                          {app.publisher?.name && (
                            <span className="text-muted-foreground/70">
                              {" "}
                              · by {app.publisher.name}
                            </span>
                          )}
                        </p>
                      </div>

                      {/* Enable/disable toggle */}
                      <button
                        onClick={() => handleToggleApp(app)}
                        disabled={actionLoading === app.id}
                        className="shrink-0"
                        title={app.enabled ? "Disable app" : "Enable app"}
                      >
                        {actionLoading === app.id ? (
                          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                        ) : app.enabled ? (
                          <ToggleRight className="w-6 h-6 text-[var(--accent)]" />
                        ) : (
                          <ToggleLeft className="w-6 h-6 text-muted-foreground" />
                        )}
                      </button>
                    </div>

                    {/* Expanded details */}
                    <AnimatePresence>
                      {expandedApp === app.id && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.15 }}
                          className="overflow-hidden"
                        >
                          <div className="px-3 py-2 border-t border-border/30 bg-muted/30 space-y-2">
                            <div className="grid grid-cols-2 gap-2 text-xs">
                              <div>
                                <span className="text-muted-foreground">Publisher: </span>
                                {app.publisher?.url ? (
                                  <a
                                    href={app.publisher.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-[var(--accent)] hover:underline"
                                  >
                                    {app.publisher.name}
                                  </a>
                                ) : (
                                  <span className="text-foreground">
                                    {app.publisher?.name || "Unknown"}
                                  </span>
                                )}
                              </div>
                              {app.installed_at && (
                                <div>
                                  <span className="text-muted-foreground">Installed: </span>
                                  <span className="text-foreground">
                                    {new Date(app.installed_at).toLocaleDateString()}
                                  </span>
                                </div>
                              )}
                              {app.key_prefix && (
                                <div>
                                  <span className="text-muted-foreground">API Key: </span>
                                  <span className="text-foreground font-mono">
                                    {app.key_prefix}…
                                  </span>
                                </div>
                              )}
                              <div>
                                <span className="text-muted-foreground">Collections: </span>
                                <span className="text-foreground">
                                  {app.collections.length > 0
                                    ? app.collections.join(", ")
                                    : "all"}
                                </span>
                              </div>
                              {app.external_hosts.length > 0 && (
                                <div className="col-span-2">
                                  <span className="text-muted-foreground">
                                    External hosts:{" "}
                                  </span>
                                  <span className="text-foreground font-mono">
                                    {app.external_hosts.join(", ")}
                                  </span>
                                </div>
                              )}
                            </div>

                            {/* Actions */}
                            <div className="flex items-center gap-3">
                              {app.enabled && (
                                <Link
                                  href={`/apps/launch/${encodeURIComponent(app.id)}`}
                                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                                >
                                  <ExternalLink className="w-3 h-3" />
                                  Open
                                </Link>
                              )}
                              <button
                                onClick={() => setConfigModalApp(app)}
                                className="flex items-center gap-1 text-xs text-[var(--accent)]/70 hover:text-[var(--accent)] transition-colors"
                              >
                                <Settings className="w-3 h-3" />
                                Configure
                              </button>
                              {app.sharing_links && (
                                <button
                                  onClick={() => setGrantsModalApp(app)}
                                  className="flex items-center gap-1 text-xs text-[var(--accent)]/70 hover:text-[var(--accent)] transition-colors"
                                >
                                  <LinkIcon className="w-3 h-3" />
                                  Share Links
                                </button>
                              )}
                            </div>

                            {deleteConfirm === app.id ? (
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-red-400">
                                  Delete this app and revoke its API key?
                                </span>
                                <button
                                  onClick={() => handleDeleteApp(app.id)}
                                  className="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
                                >
                                  Confirm
                                </button>
                                <button
                                  onClick={() => setDeleteConfirm(null)}
                                  className="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                                >
                                  Cancel
                                </button>
                              </div>
                            ) : (
                              <button
                                onClick={() => setDeleteConfirm(app.id)}
                                className="flex items-center gap-1 text-xs text-red-400/70 hover:text-red-400 transition-colors"
                              >
                                <Trash2 className="w-3 h-3" />
                                Uninstall
                              </button>
                            )}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="border-t border-border/30" />

          {/* Install app */}
          <div>
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Install App
            </h4>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={(e) => handleFileSelected(e.target.files)}
            />
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
              className={`flex items-center justify-center gap-2 px-4 py-5 rounded-lg border border-dashed transition-colors ${
                dragActive
                  ? "border-[var(--accent)]/60 bg-[var(--accent)]/5"
                  : "border-border/50 bg-background"
              }`}
            >
              {installing ? (
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Installing app...
                </span>
              ) : (
                <>
                  <span className="text-xs text-muted-foreground">
                    Drop an app package (.zip) here, or
                  </span>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 transition-colors"
                  >
                    <Upload className="w-3 h-3" />
                    Choose file
                  </button>
                </>
              )}
            </div>
            {collections.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground mr-1">
                  Restrict to collections (apps with user-selected scope)
                </span>
                {collections.map((c) => {
                  const active = installCollections.includes(c.id);
                  return (
                    <button
                      key={c.id}
                      type="button"
                      disabled={installing}
                      onClick={() =>
                        setInstallCollections((prev) =>
                          active ? prev.filter((id) => id !== c.id) : [...prev, c.id],
                        )
                      }
                      className={`px-2 py-0.5 text-[11px] rounded-md border transition-colors ${
                        active
                          ? "border-[var(--accent)]/50 bg-[var(--accent)]/10 text-[var(--accent)]"
                          : "border-border/50 text-muted-foreground hover:border-border"
                      }`}
                    >
                      {c.name}
                    </button>
                  );
                })}
              </div>
            )}
            {installError && (
              <p className="text-xs text-red-400 mt-2">{installError}</p>
            )}
            {installIssues && installIssues.length > 0 && (
              <div className="mt-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 space-y-1">
                <div className="flex items-center gap-2 text-xs font-medium text-red-400">
                  <AlertCircle className="w-3 h-3 shrink-0" />
                  App package failed validation:
                </div>
                <ul className="list-disc list-inside space-y-0.5">
                  {installIssues.map((issue, i) => (
                    <li key={i} className="text-xs text-red-400/90">
                      {issue}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>

        {/* Config modal */}
        <AnimatePresence>
          {configModalApp && (
            <AppConfigModal
              appId={configModalApp.id}
              appName={configModalApp.name}
              onClose={() => setConfigModalApp(null)}
              onSaved={() => {
                setConfigModalApp(null);
                fetchApps();
              }}
            />
          )}
        </AnimatePresence>

        {/* Grants modal */}
        <AnimatePresence>
          {grantsModalApp && (
            <AppGrantsModal
              appId={grantsModalApp.id}
              appName={grantsModalApp.name}
              onClose={() => setGrantsModalApp(null)}
              onChanged={fetchApps}
            />
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
