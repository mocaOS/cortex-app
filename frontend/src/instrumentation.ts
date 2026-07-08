import * as Sentry from "@sentry/nextjs";

// Next.js instrumentation hook: registers the Sentry SDK for the server (and
// edge, if a route ever opts into it) at process startup. The configs live at
// the project root (frontend/sentry.*.config.ts), per @sentry/nextjs
// convention. No-ops when no DSN is configured.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("../sentry.server.config");
  }

  if (process.env.NEXT_RUNTIME === "edge") {
    await import("../sentry.edge.config");
  }
}

// Captures errors from Server Components, route handlers, and proxy.ts.
export const onRequestError = Sentry.captureRequestError;
