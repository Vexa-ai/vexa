import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { resolveBrowserConfig } from "@vexa/dash-config";
import { getAuthCookieName } from "@/lib/auth-cookies";

/**
 * The browser runtime-config endpoint.
 *
 * Next only exposes NEXT_PUBLIC_* at build time, but the dashboard's API/WS targets + the auth token
 * are RUNTIME facts (deploy SSOT + the request host + the login cookie). This route gathers those
 * inputs and hands them to `@vexa/dash-config.resolveBrowserConfig` — the pure resolver that owns the
 * loopback/tunnel/gateway-direct vs same-origin decision (Learning #37) and the single token order
 * (cookie || self-host || null). The browser reads `{ wsUrl, authToken }` from here to open `/ws`.
 *
 * Must be dynamic: prerendering would freeze a stale snapshot (authToken=null, internal wsUrl).
 */
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: NextRequest) {
  const internalApiUrl = process.env.VEXA_API_URL;
  if (!internalApiUrl) {
    return NextResponse.json(
      { error: "VEXA_API_URL is required; dashboard runtime config has no API SSOT" },
      { status: 500 }
    );
  }

  const host = request.headers.get("x-forwarded-host") || request.headers.get("host") || "";
  const requestProto = request.headers.get("x-forwarded-proto") === "https" ? "https" : "http";

  const cookieStore = await cookies();
  const cookieToken = cookieStore.get(getAuthCookieName())?.value || null;

  const config = resolveBrowserConfig({
    internalApiUrl,
    configuredPublicApiUrl:
      process.env.VEXA_PUBLIC_API_URL ||
      process.env.NEXT_PUBLIC_VEXA_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "",
    requestHost: host,
    requestProto,
    gatewayHostPort: process.env.API_GATEWAY_HOST_PORT,
    cookieToken,
    selfHostKey: process.env.VEXA_API_KEY || null,
  });

  return NextResponse.json({
    apiUrl: config.apiUrl,
    wsUrl: config.wsUrl,
    authToken: config.authToken,
    defaultBotName: process.env.DEFAULT_BOT_NAME || null,
  });
}
