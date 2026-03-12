import { NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Public configuration endpoint that exposes runtime environment variables to the client.
 * This solves the Next.js limitation where NEXT_PUBLIC_* vars are only available at build time.
 * Also returns the user's auth token for WebSocket authentication.
 */
export async function GET() {
  const apiUrl = process.env.VEXA_API_URL || "http://localhost:18056";

  // Derive WebSocket URL from API URL (can be overridden with NEXT_PUBLIC_VEXA_WS_URL)
  let wsUrl = process.env.NEXT_PUBLIC_VEXA_WS_URL;

  if (!wsUrl) {
    // Convert http(s) to ws(s)
    wsUrl = apiUrl.replace(/^https:\/\//, 'wss://').replace(/^http:\/\//, 'ws://');
    // Append /ws if not already there
    wsUrl = wsUrl.endsWith('/ws') ? wsUrl : `${wsUrl.replace(/\/$/, '')}/ws`;
  }

  // Get user's auth token from cookie for WebSocket authentication
  const cookieStore = await cookies();
  const authToken = cookieStore.get("vexa-token")?.value;

  // Get default bot name from environment (optional)
  const defaultBotName = process.env.DEFAULT_BOT_NAME || null;

  // Hosted mode flags (read at runtime, not build time)
  const hostedMode = process.env.NEXT_PUBLIC_HOSTED_MODE === "true";
  const webappUrl = process.env.NEXT_PUBLIC_WEBAPP_URL || "https://vexa.ai";

  // Public API URL for client-facing configs (MCP, docs, etc.)
  // Falls back to VEXA_PUBLIC_API_URL -> NEXT_PUBLIC_VEXA_API_URL -> apiUrl
  const publicApiUrl = process.env.VEXA_PUBLIC_API_URL
    || process.env.NEXT_PUBLIC_VEXA_API_URL
    || apiUrl;

  return NextResponse.json({
    wsUrl,
    apiUrl,
    publicApiUrl,
    authToken: authToken || null,
    defaultBotName,
    hostedMode,
    webappUrl,
  });
}
