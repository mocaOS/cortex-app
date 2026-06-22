"use client";

import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Globe,
  Loader2,
  CheckCircle,
  AlertCircle,
  Search,
  Link2,
  ChevronDown,
  ChevronRight,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import CollectionSelector from "@/components/CollectionSelector";
import type {
  WebContentFilter,
  WebDiscoverLink,
  TaskProgress,
} from "@/types";

interface WebImportPanelProps {
  collectionId: string | undefined;
  onCollectionChange: (id: string | undefined) => void;
}

interface WebImportTaskResult {
  imported: number;
  failed: number;
  total: number;
  succeeded: { url: string; title: string }[];
  failures: { url: string; error: string }[];
  processing?: Record<string, unknown>;
}

const contentFilters: { value: WebContentFilter; label: string; description: string }[] = [
  { value: "fit", label: "Readable (recommended)", description: "Extracts the main readable content" },
  { value: "raw", label: "Full page", description: "Imports the full page markdown" },
  { value: "bm25", label: "Relevance-ranked", description: "Keeps only content relevant to a query" },
];

/** Parse a textarea blob into a deduplicated, trimmed list of URLs (one per line). */
function parseUrls(text: string): string[] {
  const seen = new Set<string>();
  const urls: string[] = [];
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed);
      urls.push(trimmed);
    }
  }
  return urls;
}

export default function WebImportPanel({
  collectionId,
  onCollectionChange,
}: WebImportPanelProps) {
  const [urlsText, setUrlsText] = useState("");
  const [contentFilter, setContentFilter] = useState<WebContentFilter>("fit");
  const [query, setQuery] = useState("");

  // Discover sub-flow
  const [discoverUrl, setDiscoverUrl] = useState("");
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [discovered, setDiscovered] = useState<WebDiscoverLink[] | null>(null);
  const [discoverDomain, setDiscoverDomain] = useState("");
  const [selectedLinks, setSelectedLinks] = useState<Set<string>>(new Set());
  const [showDiscover, setShowDiscover] = useState(false);

  // Submission / progress
  const [isImporting, setIsImporting] = useState(false);
  const [progress, setProgress] = useState<TaskProgress | null>(null);
  const [result, setResult] = useState<WebImportTaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFailures, setShowFailures] = useState(false);

  const urls = useMemo(() => parseUrls(urlsText), [urlsText]);

  const handleDiscover = async () => {
    const url = discoverUrl.trim();
    if (!url) return;

    setIsDiscovering(true);
    setDiscoverError(null);
    setDiscovered(null);
    setSelectedLinks(new Set());

    try {
      const res = await api.webDiscover(url);
      setDiscovered(res.links);
      setDiscoverDomain(res.domain);
    } catch (e) {
      setDiscoverError(e instanceof Error ? e.message : "Failed to discover links");
    } finally {
      setIsDiscovering(false);
    }
  };

  const toggleLink = (url: string) => {
    setSelectedLinks((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const allDiscoveredSelected =
    discovered !== null && discovered.length > 0 && selectedLinks.size === discovered.length;

  const toggleSelectAll = () => {
    if (!discovered) return;
    if (allDiscoveredSelected) {
      setSelectedLinks(new Set());
    } else {
      setSelectedLinks(new Set(discovered.map((l) => l.url)));
    }
  };

  const appendSelectedLinks = () => {
    if (selectedLinks.size === 0) return;
    const existing = new Set(urls);
    const toAdd = Array.from(selectedLinks).filter((u) => !existing.has(u));
    if (toAdd.length === 0) return;
    setUrlsText((prev) => {
      const base = prev.trimEnd();
      return base ? `${base}\n${toAdd.join("\n")}` : toAdd.join("\n");
    });
    setSelectedLinks(new Set());
  };

  const handleImport = async () => {
    if (urls.length === 0) {
      setError("Please add at least one URL");
      return;
    }

    setIsImporting(true);
    setError(null);
    setResult(null);
    setProgress(null);

    try {
      const res = await api.webImport({
        urls,
        collection_id: collectionId,
        content_filter: contentFilter,
        query: contentFilter === "bm25" && query.trim() ? query.trim() : undefined,
      });

      const taskResult = await api.pollTask<WebImportTaskResult>(
        res.task_id,
        (p) => setProgress(p)
      );

      setResult(taskResult);
      // Clear the URL list on success so the panel is ready for a new batch.
      setUrlsText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Web import failed");
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Collection Selector */}
      <div className="space-y-2 relative z-40">
        <label className="text-sm font-medium text-foreground">Collection</label>
        <CollectionSelector
          value={collectionId}
          onChange={onCollectionChange}
          allowCreate={true}
          autoSelectDefault={true}
        />
      </div>

      {/* URL list */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium text-foreground">URLs</label>
          <span className="text-xs text-muted-foreground">
            {urls.length} {urls.length === 1 ? "URL" : "URLs"}
          </span>
        </div>
        <textarea
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
          placeholder={"https://example.com/page\nhttps://example.com/another-page"}
          rows={6}
          className="w-full px-4 py-3 bg-card border border-border rounded-xl text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors resize-none font-mono text-sm"
        />
        <p className="text-xs text-muted-foreground">One URL per line.</p>
      </div>

      {/* Discover links sub-flow */}
      <div className="space-y-3 rounded-xl border border-border bg-card/50 p-4">
        <button
          type="button"
          onClick={() => setShowDiscover((v) => !v)}
          className="flex items-center gap-2 w-full text-left text-sm font-medium text-foreground"
        >
          {showDiscover ? (
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted-foreground" />
          )}
          <Link2 className="w-4 h-4 text-accent" />
          Discover links
          <span className="text-xs text-muted-foreground font-normal">(optional)</span>
        </button>

        <AnimatePresence initial={false}>
          {showDiscover && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="space-y-3 overflow-hidden"
            >
              <p className="text-xs text-muted-foreground">
                Crawl a page to find links you can add to the import list.
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={discoverUrl}
                  onChange={(e) => setDiscoverUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleDiscover();
                    }
                  }}
                  placeholder="https://example.com"
                  className="flex-1 px-4 py-2.5 bg-card border border-border rounded-xl text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors text-sm"
                />
                <button
                  type="button"
                  onClick={handleDiscover}
                  disabled={isDiscovering || !discoverUrl.trim()}
                  className={cn(
                    "flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border transition-all shrink-0 text-sm font-medium",
                    "border-accent/50 bg-accent/10 text-accent hover:bg-accent/20",
                    "disabled:opacity-50 disabled:cursor-not-allowed"
                  )}
                >
                  {isDiscovering ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Search className="w-4 h-4" />
                  )}
                  Discover
                </button>
              </div>

              <AnimatePresence mode="wait">
                {discoverError && (
                  <motion.div
                    initial={{ opacity: 0, y: -6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    className="flex items-start gap-2 p-3 rounded-xl bg-red-500/10 border border-red-500/20"
                  >
                    <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                    <p className="text-sm text-red-400">{discoverError}</p>
                  </motion.div>
                )}
              </AnimatePresence>

              {discovered && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">
                      {discovered.length} links found
                      {discoverDomain ? ` on ${discoverDomain}` : ""}
                    </span>
                    {discovered.length > 0 && (
                      <button
                        type="button"
                        onClick={toggleSelectAll}
                        className="text-xs px-2 py-1 rounded-md bg-muted hover:bg-muted/80 text-foreground transition-colors"
                      >
                        {allDiscoveredSelected ? "Deselect all" : "Select all"}
                      </button>
                    )}
                  </div>

                  {discovered.length === 0 ? (
                    <div className="text-center py-4 text-sm text-muted-foreground">
                      No links found on this page
                    </div>
                  ) : (
                    <div className="space-y-1 max-h-60 overflow-y-auto pr-1">
                      {discovered.map((link) => {
                        const checked = selectedLinks.has(link.url);
                        return (
                          <button
                            key={link.url}
                            type="button"
                            onClick={() => toggleLink(link.url)}
                            className={cn(
                              "flex items-start gap-3 w-full p-2.5 rounded-lg border text-left transition-all",
                              checked
                                ? "border-accent bg-accent/10"
                                : "border-border bg-card hover:border-muted-foreground"
                            )}
                          >
                            <span
                              className={cn(
                                "mt-0.5 flex items-center justify-center w-4 h-4 rounded border shrink-0 transition-colors",
                                checked
                                  ? "bg-accent border-accent text-accent-foreground"
                                  : "border-border"
                              )}
                            >
                              {checked && <CheckCircle className="w-3.5 h-3.5" />}
                            </span>
                            <span className="flex-1 min-w-0">
                              <span className="block text-sm text-foreground truncate">
                                {link.title || link.url}
                              </span>
                              <span className="block text-xs text-muted-foreground truncate">
                                {link.url}
                              </span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {discovered.length > 0 && (
                    <button
                      type="button"
                      onClick={appendSelectedLinks}
                      disabled={selectedLinks.size === 0}
                      className={cn(
                        "w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all",
                        "bg-accent/10 border border-accent/50 text-accent hover:bg-accent/20",
                        "disabled:opacity-50 disabled:cursor-not-allowed"
                      )}
                    >
                      Add {selectedLinks.size} selected to list
                    </button>
                  )}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Content filter */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-foreground">Content filter</label>
        <div className="relative">
          <select
            value={contentFilter}
            onChange={(e) => setContentFilter(e.target.value as WebContentFilter)}
            className="w-full appearance-none px-4 py-3 pr-10 bg-card border border-border rounded-xl text-foreground focus:outline-none focus:border-accent transition-colors text-sm"
          >
            {contentFilters.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
        </div>
        <p className="text-xs text-muted-foreground">
          {contentFilters.find((f) => f.value === contentFilter)?.description}
        </p>
      </div>

      {/* BM25 query field */}
      <AnimatePresence mode="wait">
        {contentFilter === "bm25" && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="space-y-2 overflow-hidden"
          >
            <label className="text-sm font-medium text-foreground flex items-center gap-2">
              Relevance query
              <span className="text-xs text-muted-foreground font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g., pricing and plans"
              className="w-full px-4 py-3 bg-card border border-border rounded-xl text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors text-sm"
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Progress bar */}
      <AnimatePresence mode="wait">
        {isImporting && progress && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="space-y-2 p-4 rounded-xl bg-accent/10 border border-accent/20"
          >
            <div className="flex items-center justify-between text-sm">
              <span className="text-foreground truncate pr-2">
                {progress.message || "Importing..."}
              </span>
              <span className="text-muted-foreground shrink-0">
                {Math.round(progress.progress_percent)}%
              </span>
            </div>
            <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-accent transition-all duration-300"
                style={{ width: `${Math.min(100, Math.max(0, progress.progress_percent))}%` }}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Error state */}
      <AnimatePresence mode="wait">
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="flex items-start gap-3 p-4 rounded-xl bg-red-500/10 border border-red-500/20"
          >
            <AlertCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-red-400">Error</p>
              <p className="text-sm text-muted-foreground">{error}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Success summary */}
      <AnimatePresence mode="wait">
        {result && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="space-y-3 p-4 rounded-xl bg-green-500/10 border border-green-500/20"
          >
            <div className="flex items-start gap-3">
              <CheckCircle className="w-5 h-5 text-green-400 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <p className="text-sm font-medium text-green-400">Import complete</p>
                <p className="text-sm text-muted-foreground">
                  Imported {result.imported} of {result.total}
                  {result.failed > 0 ? `, ${result.failed} failed` : ""}.
                </p>
              </div>
            </div>

            {result.failures.length > 0 && (
              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => setShowFailures((v) => !v)}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showFailures ? (
                    <ChevronDown className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5" />
                  )}
                  {result.failures.length} failed{" "}
                  {result.failures.length === 1 ? "URL" : "URLs"}
                </button>
                <AnimatePresence initial={false}>
                  {showFailures && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="overflow-hidden"
                    >
                      <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
                        {result.failures.map((f, i) => (
                          <div
                            key={`${f.url}-${i}`}
                            className="rounded-lg bg-muted/50 px-3 py-2"
                          >
                            <p className="text-xs text-foreground truncate">{f.url}</p>
                            <p className="text-xs text-muted-foreground">{f.error}</p>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}

            <button
              type="button"
              onClick={() => {
                setResult(null);
                setProgress(null);
                setShowFailures(false);
              }}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="w-3.5 h-3.5" />
              Dismiss
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Submit button */}
      <button
        type="button"
        onClick={handleImport}
        disabled={isImporting || urls.length === 0}
        className={cn(
          "w-full flex items-center justify-center gap-2 px-6 py-3 rounded-xl font-medium transition-all",
          "bg-accent text-accent-foreground hover:bg-accent/90",
          "disabled:opacity-50 disabled:cursor-not-allowed"
        )}
      >
        {isImporting ? (
          <>
            <Loader2 className="w-5 h-5 animate-spin" />
            Importing...
          </>
        ) : (
          <>
            <Globe className="w-5 h-5" />
            Import {urls.length > 0 ? `${urls.length} ` : ""}
            {urls.length === 1 ? "page" : "pages"}
          </>
        )}
      </button>
    </div>
  );
}
