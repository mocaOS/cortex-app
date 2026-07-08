import * as Sentry from "@sentry/nextjs";

// Browser-side error tracking (GlitchTip via the Sentry protocol). Next.js
// loads this file natively on every page (Next 15.3+, Turbopack included).
// NEXT_PUBLIC_SENTRY_DSN is inlined at build time; without it the SDK is
// disabled and this file is inert. Readable stack traces come from the source
// maps uploaded during `next build` (see withSentryConfig in next.config.mjs).
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

Sentry.init({
  dsn,
  enabled: Boolean(dsn),
  environment:
    process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || process.env.NODE_ENV,

  // Errors only: GlitchTip supports performance transactions, but sampling
  // stays off until someone deliberately wants the volume.
  tracesSampleRate: 0,

  // Privacy: no IP addresses / user context beyond what errors carry.
  sendDefaultPii: false,
});

// Instruments App Router navigations (required export since SDK v9.12).
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
