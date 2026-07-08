import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  experimental: {
    // Uploads are capped server-side (MAX_FILE_SIZE_MB, default 50MB) and
    // library imports go through the 8MB-chunked upload flow, so the proxy
    // never needs to pass multi-GB bodies.
    proxyClientMaxBodySize: "256mb",
    // Long-running synchronous API calls (e.g. admin system reset) exceed the
    // 30s default rewrite-proxy timeout; match nginx's proxy_read_timeout.
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

// Error tracking (GlitchTip via the Sentry protocol). The wrapper is inert
// without env config; with SENTRY_AUTH_TOKEN set at build time it uploads
// source maps (debug-ID artifact bundles, supported by GlitchTip >= 4.2) so
// production stack traces show original TypeScript. All values come from env —
// this repo is open source and must not hardcode a specific instance.
export default withSentryConfig(nextConfig, {
  sentryUrl: process.env.SENTRY_URL,
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,

  // Verbose upload logs only when actually uploading (Docker/CI builds).
  silent: !process.env.SENTRY_AUTH_TOKEN,
  telemetry: false,

  // Upload a wider set of client chunks for complete stack traces, then strip
  // the .map files so the runner image never serves them publicly.
  widenClientFileUpload: true,
  sourcemaps: {
    deleteSourcemapsAfterUpload: true,
  },

  // Tree-shake Sentry debug-logger statements out of production bundles.
  disableLogger: true,
});
