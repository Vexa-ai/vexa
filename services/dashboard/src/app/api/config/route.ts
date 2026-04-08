import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Public configuration endpoint that exposes runtime environment variables to the client.
 * This solves the Next.js limitation where NEXT_PUBLIC_* vars are only available at build time.
 * Also returns the user's auth token for WebSocket authentication.
 */
export async function GET(request: NextRequest) {
  const apiUrl = process.env.VEXA_API_URL || "http://localhost:18056";
  const decisionListenerUrl =
    process.env.NEXT_PUBLIC_DECISION_LISTENER_URL || "http://localhost:8765";

  // WS goes through the dashboard via Next.js rewrite — derive from public app URL or request host
  const appUrl = process.env.NEXT_PUBLIC_APP_URL;
  let wsUrl: string;
  if (appUrl) {
    const wsProto = appUrl.startsWith('https') ? 'wss' : 'ws';
    const wsHost = appUrl.replace(/^https?:\/\//, '');
    wsUrl = `${wsProto}://${wsHost}/ws`;
  } else {
    const host = request.headers.get('host')!;
    const proto = request.headers.get('x-forwarded-proto') === 'https' ? 'wss' : 'ws';
    wsUrl = `${proto}://${host}/ws`;
  }

  // Auth token for WebSocket: same fallback chain as the HTTP proxy in /api/vexa/[...path].
  // cookie (logged-in user) → VEXA_API_KEY env var (self-hosted service token)
  const cookieStore = await cookies();
  const authToken = cookieStore.get("vexa-token")?.value
    || process.env.VEXA_API_KEY
    || null;

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
    decisionListenerUrl,
    authToken: authToken || null,
    defaultBotName,
    hostedMode,
    webappUrl,
  });
}
