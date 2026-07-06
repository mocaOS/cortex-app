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

export default nextConfig;
