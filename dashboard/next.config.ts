import type { NextConfig } from "next";

// The dashboard and the API share one origin: /api/* is proxied to the backend, so the
// session cookie is first-party and no CORS is needed for the dashboard.
if (process.env.NETLIFY && !process.env.BACKEND_URL) {
  throw new Error("Set BACKEND_URL in Netlify (Site configuration > Environment variables) to the backend address, e.g. https://estara-demo-api.onrender.com");
}
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Docker (docker-compose.yml) runs the standalone server; Netlify (free demo, NETLIFY=true
  // during its builds) uses its own Next.js runtime instead.
  output: process.env.NETLIFY ? undefined : "standalone",
  poweredByHeader: false,
  experimental: {
    // The /api proxy buffers request bodies; the default 10MB would silently truncate document
    // uploads (the backend allows 25MB documents and answers too-large files with a clear 422).
    proxyClientMaxBodySize: "30mb",
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
