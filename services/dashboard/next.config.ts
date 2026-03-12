import type { NextConfig } from "next";
import path from "path";
import fs from "fs";

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

const nextConfig: NextConfig = {
  // Only use standalone output for production builds
  ...(process.env.NODE_ENV === 'production' ? { output: 'standalone' } : {}),
  transpilePackages: ['@vexaai/transcript-rendering'],
  // Ensure Turbopack uses this project as root
  // (avoids picking a parent lockfile and serving nothing)
  turbopack: {
    root: path.resolve(__dirname),
  },
  // Expose app version from vexa VERSION file at build time
  env: {
    NEXT_PUBLIC_APP_VERSION: getVersion(),
  },
};

export default nextConfig;
