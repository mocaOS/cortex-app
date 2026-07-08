import * as Sentry from "@sentry/nextjs";

// Edge-runtime counterpart of sentry.server.config.ts. Nothing in this app
// opts into the edge runtime today (proxy.ts runs on Node in Next 16), but
// registering it keeps error tracking intact if a route ever does.
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
