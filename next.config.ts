import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Only use standalone output for production builds
  ...(process.env.NODE_ENV === 'production' ? { output: 'standalone' } : {}),
  transpilePackages: ['vexa-transcript-rendering'],
  // Ensure Turbopack uses this project as root
  // (avoids picking a parent lockfile and serving nothing)
  turbopack: {
    root: path.resolve(__dirname),
  },
  // Note: Don't use env block here as it overrides .env.local values at build time
  // Environment variables are loaded automatically from .env.local at runtime
};

export default nextConfig;
