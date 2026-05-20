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
  const configuredPublicApiUrl =
    process.env.VEXA_PUBLIC_API_URL ||
    process.env.NEXT_PUBLIC_VEXA_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "";

  const wsUrlFromHttpBase = (baseUrl: string) => {
    const trimmed = baseUrl.replace(/\/+$/, "");
    const wsProto = trimmed.startsWith("https://") ? "wss" : "ws";
    return `${wsProto}://${trimmed.replace(/^https?:\/\//, "")}/ws`;
  };

  const isLoopbackHost = (hostname: string) =>
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "0.0.0.0" ||
    hostname === "::1";

  const host = request.headers.get("x-forwarded-host") || request.headers.get("host")!;
  const requestHostname = host.split(":")[0];
  let publicApiUrl = configuredPublicApiUrl;
  if (configuredPublicApiUrl) {
    try {
      const configured = new URL(configuredPublicApiUrl);
      if (isLoopbackHost(configured.hostname) && !isLoopbackHost(requestHostname)) {
        configured.hostname = requestHostname;
        publicApiUrl = configured.toString().replace(/\/+$/, "");
      }
    } catch {
      publicApiUrl = configuredPublicApiUrl;
    }
  }

  // Browser-facing API config is the runtime SSOT. Next.js rewrites are a
  // same-origin fallback only: their target is compiled into the image, so they
  // cannot be the source of truth for portable Helm deployments.
  const appUrl = process.env.NEXT_PUBLIC_APP_URL;
  const proto = request.headers.get('x-forwarded-proto') === 'https' ? 'wss' : 'ws';
  let wsUrl: string;
  if (publicApiUrl) {
    wsUrl = wsUrlFromHttpBase(publicApiUrl);
  } else if (appUrl && !appUrl.includes('localhost')) {
    wsUrl = wsUrlFromHttpBase(appUrl);
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
