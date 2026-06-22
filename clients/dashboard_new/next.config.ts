import type { NextConfig } from "next";
import path from "path";

/**
 * Composition-root Next config for dashboard_new.
 *
 * Two rewrites are the same-origin fallback for the streaming surfaces that can't go through the
 * `/api/vexa` REST proxy: `/ws` (the live multiplex — WebSocket upgrade) and `/b/` (per-bot VNC/CDP).
 * Both target the deploy SSOT `VEXA_API_URL` (no baked fallback — a missing SSOT is a hard error).
 * REST is proxied per-request by `src/app/api/vexa/[...path]` so the api key never reaches the browser.
 *
 * `transpilePackages` lets Next compile the workspace bricks (`@vexa/dash-*`) — they ship compiled
 * `dist`, but listing them keeps JSX/ESM interop robust across the monorepo symlinks.
 */
const normalizeBasePath = (value?: string) => {
  const trimmed = (value || "").trim();
  if (!trimmed) return "";
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
};

const basePath = normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH);

const VEXA_API_URL = process.env.VEXA_API_URL;
if (!VEXA_API_URL) {
  throw new Error("VEXA_API_URL is required: dashboard rewrites use the deploy SSOT, not a baked fallback");
}

const nextConfig: NextConfig = {
  // `standalone` is for the container image (built with BUILD_STANDALONE=1 + run via
  // `node .next/standalone/server.js`). `next start` does NOT support standalone output (it then
  // fails to resolve `next/dist/compiled/*` at runtime), so the local walk run must NOT set it.
  ...(process.env.BUILD_STANDALONE === "1" ? { output: "standalone" } : {}),
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  turbopack: { root: path.resolve(__dirname) },
  transpilePackages: [
    "@vexaai/transcript-rendering",
    "@vexa/dash-contracts",
    "@vexa/dash-config",
    "@vexa/dash-api-client",
    "@vexa/dash-ws",
    "@vexa/dash-meeting-state",
    "@vexa/dash-auth",
    "@vexa/dash-transcript-viewer",
    "@vexa/dash-recording-players",
    "@vexa/dash-status-history",
    "@vexa/dash-ws-event-log",
    "@vexa/dash-chat",
    "@vexa/dash-vnc-view",
    "@vexa/dash-meetings-list",
    "@vexa/dash-join-form",
  ],
  async rewrites() {
    return [
      { source: "/b/:path*", destination: `${VEXA_API_URL}/b/:path*` },
      { source: "/ws", destination: `${VEXA_API_URL}/ws` },
    ];
  },
};

export default nextConfig;
