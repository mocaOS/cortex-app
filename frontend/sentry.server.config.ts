import * as Sentry from "@sentry/nextjs";

// Server-side (Node.js) error tracking for the Next.js process: Server
// Components, route handlers, and proxy.ts errors reported via
// `onRequestError` in src/instrumentation.ts. Reads the DSN at runtime
// (SENTRY_DSN), falling back to the build-time public DSN so a single value
// can drive both sides. No DSN → SDK disabled.
const dsn = process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN;

Sentry.init({
  dsn,
  enabled: Boolean(dsn),
  environment:
    process.env.SENTRY_ENVIRONMENT ||
    process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ||
    process.env.NODE_ENV,
  tracesSampleRate: 0,
  sendDefaultPii: false,
});
