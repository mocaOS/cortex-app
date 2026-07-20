"use client";

import { useState, useEffect, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, AlertCircle } from "lucide-react";
import { PageTransition } from "@/components/layout";
import { api } from "@/lib/api";
import { useIsMounted } from "@/lib/hooks";
import type { AppInfo } from "@/types";

/**
 * Launcher host: embeds an installed app in a sandboxed iframe and brokers
 * its short-lived API tokens.
 *
 * Lives at /apps/launch/{appId} (NOT /apps/{appId}) because the /apps/{appId}/*
 * path space is rewritten to the backend's static app serving — see the
 * routing-split comment in next.config.mjs.
 *
 * Security model (do not weaken):
 * - sandbox WITHOUT allow-same-origin → the app runs on an opaque origin with
 *   no cookie/localStorage access to the Cortex UI.
 * - The app never receives a real API key. It postMessages
 *   {type:"cortex:ready"} / {type:"cortex:token:renew"}; we exchange our
 *   session for a short-lived app token (POST /api/apps/{id}/token) and post
 *   {type:"cortex:token", token} back. targetOrigin must be "*" because the
 *   sandboxed frame's origin is opaque ("null").
 */
export default function AppLaunchPage() {
  const params = useParams<{ appId: string }>();
  const appId = decodeURIComponent(params.appId);

  const [app, setApp] = useState<AppInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const mounted = useIsMounted();

  useEffect(() => {
    (async () => {
      try {
        const apps = await api.listApps();
        if (!mounted.current) return;
        const found = apps.find((a) => a.id === appId);
        if (!found) {
          setError("App not found.");
        } else if (!found.enabled) {
          setError("This app is disabled. Enable it in Settings first.");
        } else {
          setApp(found);
        }
      } catch (err) {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : "Failed to load app");
        }
      } finally {
        if (mounted.current) setLoading(false);
      }
    })();
  }, [appId, mounted]);

  // Token handshake: reply to the app's ready/renew messages with a fresh
  // short-lived app token.
  useEffect(() => {
    if (!app) return;
    const handler = async (e: MessageEvent) => {
      const frame = iframeRef.current;
      // Only trust messages coming from our own iframe's window.
      if (!frame || e.source !== frame.contentWindow) return;
      const type =
        e.data && typeof e.data === "object"
          ? (e.data as { type?: unknown }).type
          : undefined;
      if (type !== "cortex:ready" && type !== "cortex:token:renew") return;
      try {
        const res = await api.issueAppToken(app.id);
        frame.contentWindow?.postMessage(
          { type: "cortex:token", token: res.token },
          "*"
        );
      } catch (err) {
        // Token issuance failed (e.g. session expired) — the app will retry
        // via cortex:token:renew; a 401 already bounced us to /login.
        console.warn("Failed to issue app token:", err);
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [app]);

  // Point the iframe at the app's entry file rather than /apps/{id}/ — Next's
  // trailing-slash normalization (308 to the no-slash form) would ping-pong
  // with the backend's 307 back to the slash form. Relative asset paths still
  // resolve against /apps/{id}/ either way.
  const appSrc = app
    ? `/apps/${encodeURIComponent(app.id)}/${app.entry || "index.html"}`
    : "";

  return (
    <PageTransition>
      <div className="flex flex-col h-[calc(100vh-320px)] min-h-[480px]">
        {/* Header row */}
        <div className="flex items-center gap-3 mb-3">
          <Link
            href="/apps"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Apps
          </Link>
          {app && (
            <>
              <span className="text-muted-foreground/50">/</span>
              <span className="text-sm font-medium text-foreground">{app.name}</span>
            </>
          )}
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-accent" />
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex items-center gap-2 text-sm text-red-400 p-4 rounded-lg bg-red-500/10 border border-red-500/20">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          </div>
        ) : app ? (
          <iframe
            ref={iframeRef}
            title={app.name}
            src={appSrc}
            sandbox="allow-scripts allow-forms allow-downloads"
            className="flex-1 w-full rounded-xl border border-border/50 bg-card"
          />
        ) : null}
      </div>
    </PageTransition>
  );
}
