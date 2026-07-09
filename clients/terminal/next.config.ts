import type { NextConfig } from "next";
import path from "path";

/**
 * Terminal composition root.
 *
 * Same-origin fallback for `/b/` (per-bot VNC/CDP), targeting the deploy SSOT `VEXA_API_URL`.
 * During early scaffolding (no backend) the rewrite is simply omitted so `npm run dev` works
 * against the prototype with no env required.
 *
 * NOTE: `/ws` is intentionally NOT rewritten here — Next.js rewrites proxy HTTP only and do not
 * carry the WebSocket upgrade. The custom server (server.mjs) handles the `/ws` upgrade directly,
 * proxying it to the gateway with the server-side api_key. A rewrite here would shadow that path.
 */
const VEXA_API_URL = process.env.VEXA_API_URL;

const nextConfig: NextConfig = {
  ...(process.env.BUILD_STANDALONE === "1" ? { output: "standalone" } : {}),
  turbopack: { root: path.resolve(__dirname) },
  async rewrites() {
    return VEXA_API_URL
      ? [
          { source: "/b/:path*", destination: `${VEXA_API_URL}/b/:path*` },
        ]
      : [];
  },
};

export default nextConfig;
