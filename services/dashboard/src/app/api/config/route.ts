import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";

/**
 * Public configuration endpoint that exposes runtime environment variables to the client.
 * This solves the Next.js limitation where NEXT_PUBLIC_* vars are only available at build time.
 * Also returns the user's auth token for WebSocket authentication.
 */
export async function GET(request: NextRequest) {
  const apiUrl = process.env.VEXA_API_URL;
  if (!apiUrl) {
    return NextResponse.json(
      { error: "VEXA_API_URL is required; dashboard runtime config has no API SSOT" },
      { status: 500 }
    );
  }
  const decisionListenerUrl =
    process.env.NEXT_PUBLIC_DECISION_LISTENER_URL || "http://localhost:8765";

  // WS goes through the dashboard via Next.js rewrite — derive from request host.
  // Explicit NEXT_PUBLIC_APP_URL takes precedence, but localhost is ignored for remote access.
  const appUrl = process.env.NEXT_PUBLIC_APP_URL;
  const host = request.headers.get('host')!;
  const proto = request.headers.get('x-forwarded-proto') === 'https' ? 'wss' : 'ws';
  let wsUrl: string;
  if (appUrl && !appUrl.includes('localhost')) {
    const wsProto = appUrl.startsWith('https') ? 'wss' : 'ws';
    const wsHost = appUrl.replace(/^https?:\/\//, '');
    wsUrl = `${wsProto}://${wsHost}/ws`;
  } else {
    wsUrl = `${proto}://${host}/ws`;
  }

  // Auth token for WebSocket: cookie first; self-hosted service token only when explicitly configured.
  const cookieStore = await cookies();
  const authToken = cookieStore.get(getAuthCookieName())?.value
    || process.env.VEXA_API_KEY
    || null;

  // Get default bot name from environment (optional)
  const defaultBotName = process.env.DEFAULT_BOT_NAME || null;

  // Hosted mode flags (read at runtime, not build time)
  const hostedMode = process.env.NEXT_PUBLIC_HOSTED_MODE === "true";
  const webappUrl = process.env.NEXT_PUBLIC_WEBAPP_URL || "https://vexa.ai";

  // Public API URL for client-facing configs (MCP, browser session links, docs).
  // If not explicitly configured, clients should use same-origin dashboard routes.
  const publicApiUrl =
    process.env.VEXA_PUBLIC_API_URL ||
    process.env.NEXT_PUBLIC_VEXA_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "";

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
