import type { NextConfig } from "next";
import path from "path";

/**
 * Terminal composition root.
 *
 * Same-origin fallbacks for the streaming surfaces that can't go through a REST proxy:
 * `/ws` (the live transcript multiplex) and `/b/` (per-bot VNC/CDP). Both target the deploy SSOT
 * `VEXA_API_URL`. During early scaffolding (no backend) the rewrites are simply omitted so
 * `npm run dev` works against the prototype with no env required.
 */
const VEXA_API_URL = process.env.VEXA_API_URL;

const nextConfig: NextConfig = {
  ...(process.env.BUILD_STANDALONE === "1" ? { output: "standalone" } : {}),
  turbopack: { root: path.resolve(__dirname) },
  async rewrites() {
    return VEXA_API_URL
      ? [
          { source: "/b/:path*", destination: `${VEXA_API_URL}/b/:path*` },
          { source: "/ws", destination: `${VEXA_API_URL}/ws` },
        ]
      : [];
  },
};

export default nextConfig;
