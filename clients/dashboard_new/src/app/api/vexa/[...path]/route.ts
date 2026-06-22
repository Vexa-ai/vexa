import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName, getUserInfoCookieName } from "@/lib/auth-cookies";

/**
 * The REST proxy — the dashboard's one server-side door to the api.v1 gateway.
 *
 * Every browser REST call goes through here so the api key is injected SERVER-SIDE (the browser never
 * holds the REST token; only the WS — which can't set headers — gets it via `/api/config`). Auth order
 * mirrors the config resolver: the login cookie if it still authenticates, else the self-host baked
 * key, so a logged-out self-host dashboard still renders the baked identity's data.
 *
 * Two pragmatic shims the gateway shape requires:
 *   • `GET /meetings` → `GET /bots` (the meeting-api DB list across all statuses), with a
 *     running-containers fallback.
 *   • audio/video responses are streamed back with Range/Content-Range preserved so the recording
 *     players can seek.
 */
export const dynamic = "force-dynamic";
export const revalidate = 0;

async function proxyRequest(
  request: NextRequest,
  params: Promise<{ path: string[] }>,
  method: string
): Promise<NextResponse> {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) {
    return NextResponse.json(
      { error: "VEXA_API_URL is required; dashboard API proxy has no API SSOT" },
      { status: 500 }
    );
  }

  const cookieStore = await cookies();
  let userToken = cookieStore.get(getAuthCookieName())?.value;
  const SELF_HOST_KEY = (process.env.VEXA_API_KEY || "").trim();

  // A cookie token that no longer authenticates would leave a self-host dashboard empty. Validate it
  // once; if dead, drop it and fall back to the self-host key so the dashboard still renders.
  if (userToken && SELF_HOST_KEY && userToken !== SELF_HOST_KEY) {
    try {
      const check = await fetch(`${VEXA_API_URL}/auth/me`, {
        headers: { "X-API-Key": userToken },
        signal: AbortSignal.timeout(4000),
      });
      if (check.status === 401 || check.status === 403) userToken = undefined;
    } catch {
      /* transient gateway blip — keep the cookie rather than lock the user out */
    }
  }

  const VEXA_API_KEY = userToken || SELF_HOST_KEY;
  const { path } = await params;
  const pathString = path.join("/");

  // /meetings list: primary source is GET /bots (DB — all statuses); fall back to running containers.
  if (pathString === "meetings" && method === "GET") {
    try {
      const sp = request.nextUrl.searchParams;
      const qs = new URLSearchParams();
      qs.set("limit", sp.get("limit") || "50");
      qs.set("offset", sp.get("offset") || "0");
      if (sp.get("search")) qs.set("search", sp.get("search")!);
      if (sp.get("status")) qs.set("status", sp.get("status")!);
      if (sp.get("platform")) qs.set("platform", sp.get("platform")!);
      const botsResp = await fetch(`${VEXA_API_URL}/bots?${qs.toString()}`, {
        headers: { "X-API-Key": VEXA_API_KEY },
        signal: AbortSignal.timeout(5000),
      });
      if (botsResp.ok) {
        const data = await botsResp.json();
        return NextResponse.json({ meetings: data.meetings || [], has_more: data.has_more ?? false });
      }
    } catch (e) {
      console.error("[proxy] GET /bots failed, falling back to /bots/status:", e);
    }

    const meetings: Array<Record<string, unknown>> = [];
    try {
      const statusResp = await fetch(`${VEXA_API_URL}/bots/status`, {
        headers: { "X-API-Key": VEXA_API_KEY },
      });
      if (statusResp.ok) {
        const data = await statusResp.json();
        for (const b of data.running_bots || []) {
          if (!b.platform || !b.native_meeting_id) continue;
          const id = b.meeting_id_from_name || b.container_name;
          meetings.push({
            id: parseInt(id) || 0,
            platform: b.platform,
            native_meeting_id: b.native_meeting_id,
            status: b.meeting_status || "active",
            start_time: b.start_time || b.created_at,
            end_time: null,
            data: b.data || {},
            created_at: b.created_at,
          });
        }
      }
    } catch (e) {
      console.error("[proxy] /bots/status failed:", e);
    }
    return NextResponse.json({ meetings });
  }

  if (!VEXA_API_KEY) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const searchParams = request.nextUrl.searchParams.toString();
  const url = `${VEXA_API_URL}/${pathString}${searchParams ? `?${searchParams}` : ""}`;

  const headers: Record<string, string> = { "Content-Type": "application/json", "X-API-Key": VEXA_API_KEY };
  const rangeHeader = request.headers.get("range");
  if (rangeHeader) headers["Range"] = rangeHeader;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    const fetchOptions: RequestInit = { method, headers, signal: controller.signal, cache: "no-store" };
    if (method !== "GET" && method !== "HEAD") {
      const body = await request.text();
      if (body) fetchOptions.body = body;
    }

    const response = await fetch(url, fetchOptions);
    clearTimeout(timeoutId);

    const contentType = response.headers.get("content-type") || "";

    // Stream recording media (audio/video bytes) back with the seek headers preserved.
    if (contentType.includes("audio") || contentType.includes("video") || contentType.includes("octet-stream")) {
      const mediaHeaders = new Headers({ "Cache-Control": "no-store" });
      for (const h of ["content-type", "content-length", "content-range", "accept-ranges", "content-disposition"]) {
        const value = response.headers.get(h);
        if (value) mediaHeaders.set(h, value);
      }
      return new NextResponse(response.body, { status: response.status, headers: mediaHeaders });
    }

    const data = await response.text();
    const upstreamAuthRejected =
      (response.status === 401 || response.status === 403) &&
      /invalid api key|missing api key|not authenticated|unauthorized/i.test(data);

    if (upstreamAuthRejected && userToken) {
      cookieStore.delete(getAuthCookieName());
      cookieStore.delete(getUserInfoCookieName());
      return NextResponse.json(
        { error: "Authentication failed", detail: "Your session may have expired. Please log in again." },
        { status: 401, headers: { "Cache-Control": "no-store" } }
      );
    }

    try {
      return NextResponse.json(JSON.parse(data), {
        status: response.status,
        headers: { "Cache-Control": "no-store" },
      });
    } catch {
      return new NextResponse(data, {
        status: response.status,
        headers: { "Content-Type": contentType, "Cache-Control": "no-store" },
      });
    }
  } catch (error) {
    const err = error as Error;
    if (err.name === "AbortError") {
      return NextResponse.json({ error: "Request timeout" }, { status: 504 });
    }
    return NextResponse.json({ error: `Failed to connect to API: ${err.message}` }, { status: 502 });
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, ctx.params, "GET");
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, ctx.params, "POST");
}
export async function PUT(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, ctx.params, "PUT");
}
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, ctx.params, "DELETE");
}
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, ctx.params, "PATCH");
}
