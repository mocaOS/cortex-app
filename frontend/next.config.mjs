/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  experimental: {
    proxyClientMaxBodySize: "5gb",
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
