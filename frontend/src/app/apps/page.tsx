"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { LayoutGrid, Loader2, ExternalLink } from "lucide-react";
import { PageTransition } from "@/components/layout";
import { api } from "@/lib/api";
import { useIsMounted } from "@/lib/hooks";
import type { AppInfo } from "@/types";

/**
 * App launcher grid: every enabled installed app as a tile.
 *
 * The backend serves only each app's dist/ bundle (no icon endpoint), so the
 * tile shows the app's first letter instead of a fetched icon. Tiles link to
 * the iframe host page at /apps/launch/{id} — the /apps/{id}/* path space
 * itself is rewritten to the backend (see next.config.mjs).
 */
export default function AppsPage() {
  const [apps, setApps] = useState<AppInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const mounted = useIsMounted();

  useEffect(() => {
    (async () => {
      try {
        const data = await api.listApps();
        if (!mounted.current) return;
        setApps(data.filter((a) => a.enabled));
      } catch {
        // Apps disabled (404) or fetch failed — nothing links here when the
        // flag is off, but the route still resolves; show a neutral state.
        if (mounted.current) setUnavailable(true);
      } finally {
        if (mounted.current) setLoading(false);
      }
    })();
  }, [mounted]);

  return (
    <PageTransition>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-3xl font-bold text-foreground mb-2">Apps</h1>
          <p className="text-muted-foreground">
            Launch apps installed on this instance
          </p>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-accent" />
          </div>
        ) : unavailable ? (
          <div className="glass rounded-xl p-10 text-center">
            <LayoutGrid className="w-8 h-8 mx-auto mb-3 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Apps are not enabled on this instance.
            </p>
          </div>
        ) : apps.length === 0 ? (
          <div className="glass rounded-xl p-10 text-center">
            <LayoutGrid className="w-8 h-8 mx-auto mb-3 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No apps enabled yet. Install and enable apps in{" "}
              <Link href="/admin" className="text-[var(--accent)] hover:underline">
                Settings
              </Link>
              .
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {apps.map((app, i) => (
              <motion.div
                key={app.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.05 * i }}
              >
                <div className="glass rounded-xl p-5 h-full flex flex-col">
                  <div className="flex items-center gap-3 mb-3">
                    {/* Letter tile — the backend serves only dist/, no icon endpoint */}
                    <div className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] text-lg font-semibold shrink-0">
                      {(app.name || app.id).charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <h2 className="text-sm font-semibold text-foreground truncate">
                        {app.name}
                      </h2>
                      <p className="text-[11px] text-muted-foreground truncate">
                        v{app.version}
                        {app.publisher?.name ? ` · ${app.publisher.name}` : ""}
                      </p>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2 flex-1">
                    {app.description}
                  </p>
                  <div className="mt-4">
                    <Link
                      href={`/apps/launch/${encodeURIComponent(app.id)}`}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 transition-colors"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Open
                    </Link>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </PageTransition>
  );
}
