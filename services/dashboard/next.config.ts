import type { NextConfig } from "next";
import path from "path";
import fs from "fs";

const normalizeBasePath = (value?: string) => {
  if (!value) return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
};

const basePath = normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH);

// Read version from vexa monorepo root VERSION file
function getVersion(): string {
  const candidates = [
    path.resolve(__dirname, "../../VERSION"),       // services/dashboard -> vexa root
    path.resolve(__dirname, "VERSION"),              // local fallback
  ];
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, "utf-8").trim();
    } catch {}
  }
  return "dev";
}

const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:8066";

const nextConfig: NextConfig = {
  // Only use standalone output for production builds
  ...(process.env.NODE_ENV === 'production' ? { output: 'standalone' } : {}),
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  // Ensure Turbopack uses this project as root
  // (avoids picking a parent lockfile and serving nothing)
  turbopack: {
    root: path.resolve(__dirname),
  },
  // Expose app version from vexa VERSION file at build time
  env: {
    NEXT_PUBLIC_APP_VERSION: getVersion(),
  },
  // Allow dev access from nginx-proxied domains
  allowedDevOrigins: ["https://dashboard.dev.vexa.ai"],
  // Proxy /b/ routes to the agentic gateway for VNC/CDP (supports WebSocket upgrade)
  async rewrites() {
    return [
      {
        source: "/b/:path*",
        destination: `${VEXA_API_URL}/b/:path*`,
      },
      {
        source: "/ws",
        destination: `${VEXA_API_URL}/ws`,
      },
    ];
  },
};

export default nextConfig;
