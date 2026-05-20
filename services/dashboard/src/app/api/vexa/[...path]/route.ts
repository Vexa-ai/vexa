import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName, getUserInfoCookieName } from "@/lib/auth-cookies";

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
  const MINIO_INTERNAL_ENDPOINT = (process.env.MINIO_INTERNAL_ENDPOINT || "").trim();

  // Get user's token from HTTP-only cookie (set during login)
  const cookieStore = await cookies();
  const userToken = cookieStore.get(getAuthCookieName())?.value;

  // VEXA_API_KEY from env is used ONLY for the meetings list endpoint
  // (pre-login browsing). All other endpoints require a user cookie.
  const VEXA_API_KEY = userToken || process.env.VEXA_API_KEY || "";

  const { path } = await params;
  const pathString = path.join("/");

  // /meetings list: primary source is GET /bots (meeting-api DB — all statuses).
  // Fallback to /bots/status (running containers only) if /bots fails.
  if (pathString === "meetings" && method === "GET") {
    // Try GET /bots first — returns all meetings from DB (active + completed)
    try {
      const searchParams = request.nextUrl.searchParams;
      const qs = new URLSearchParams();
      qs.set("limit", searchParams.get("limit") || "50");
      qs.set("offset", searchParams.get("offset") || "0");
      if (searchParams.get("search")) qs.set("search", searchParams.get("search")!);
      if (searchParams.get("status")) qs.set("status", searchParams.get("status")!);
      if (searchParams.get("platform")) qs.set("platform", searchParams.get("platform")!);
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

    // Fallback: running containers only (no history)
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

  // Everything else: proxy through api-gateway (handles /transcripts, /recordings, /bots, etc.)
  if (!VEXA_API_KEY) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const upstreamSearchParams = new URLSearchParams(request.nextUrl.searchParams);
  const proxyMasterMedia = method === "GET" && upstreamSearchParams.get("proxy") === "1";
  upstreamSearchParams.delete("proxy");
  const searchParams = upstreamSearchParams.toString();
  const url = `${VEXA_API_URL}/${pathString}${searchParams ? `?${searchParams}` : ""}`;

  const headers: HeadersInit = {
    "Content-Type": "application/json",
  };

  if (VEXA_API_KEY) {
    headers["X-API-Key"] = VEXA_API_KEY;
  }

  const rangeHeader = request.headers.get("range");
  if (rangeHeader) {
    headers["Range"] = rangeHeader;
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    const fetchOptions: RequestInit = {
      method,
      headers,
      signal: controller.signal,
    };

    if (method !== "GET" && method !== "HEAD") {
      const body = await request.text();
      if (body) {
        fetchOptions.body = body;
      }
    }

    const response = await fetch(url, { ...fetchOptions, cache: "no-store" });
    clearTimeout(timeoutId);

    const contentType = response.headers.get("content-type") || "";

    if (proxyMasterMedia && response.ok && contentType.includes("application/json")) {
      const data = (await response.json()) as {
        url?: string;
        download_url?: string;
        raw_url?: string;
        media_file_id?: number | string;
        content_type?: string;
      };

      const match = pathString.match(/^recordings\/(\d+)\/master$/);
      const recordingId = match?.[1] || "";
      const mediaType = upstreamSearchParams.get("type") || "";
      if (!recordingId || !/^(audio|video)$/.test(mediaType)) {
        return NextResponse.json(
          { error: "Invalid master media proxy request" },
          { status: 502, headers: { "Cache-Control": "no-store" } }
        );
      }

      const selectedUrl = data.url || data.download_url || "";
      if (!selectedUrl) {
        return NextResponse.json(
          { error: `No canonical ${mediaType} playback URL for recording ${recordingId}` },
          { status: 404, headers: { "Cache-Control": "no-store" } }
        );
      }

      let mediaUrl: URL;
      const mediaHeaders: HeadersInit = {};
      if (selectedUrl.startsWith("/")) {
        if (!selectedUrl.startsWith(`/recordings/${recordingId}/media/`)) {
          return NextResponse.json(
            { error: "Invalid canonical media URL returned by backend" },
            { status: 502, headers: { "Cache-Control": "no-store" } }
          );
        }
        mediaUrl = new URL(selectedUrl, `${VEXA_API_URL}/`);
        if (VEXA_API_KEY) {
          mediaHeaders["X-API-Key"] = VEXA_API_KEY;
        }
      } else {
        mediaUrl = new URL(selectedUrl);
        const isHostLocalUrl = ["localhost", "127.0.0.1", "[::1]"].includes(mediaUrl.hostname);
        if (isHostLocalUrl && (data.raw_url || data.media_file_id)) {
          const rawUrl = data.raw_url || `/recordings/${recordingId}/media/${data.media_file_id}/raw`;
          mediaUrl = new URL(rawUrl, `${VEXA_API_URL}/`);
          if (VEXA_API_KEY) {
            mediaHeaders["X-API-Key"] = VEXA_API_KEY;
          }
        } else if (isHostLocalUrl && MINIO_INTERNAL_ENDPOINT) {
          return NextResponse.json(
            { error: "Host-local presigned media URL is not proxy-safe; backend raw_url missing" },
            { status: 502, headers: { "Cache-Control": "no-store" } }
          );
        }
      }

      if (rangeHeader) {
        mediaHeaders["Range"] = rangeHeader;
      }
      const mediaResponse = await fetch(mediaUrl, {
        headers: mediaHeaders,
        cache: "no-store",
      });
      const mediaBlob = await mediaResponse.blob();
      const mediaContentType =
        mediaResponse.headers.get("content-type") ||
        data.content_type ||
        "application/octet-stream";
      return new NextResponse(mediaBlob, {
        status: mediaResponse.status,
        headers: {
          "Content-Type": mediaContentType,
          "Content-Length": mediaResponse.headers.get("content-length") || "",
          "Cache-Control": "no-store",
          ...(mediaResponse.headers.get("content-range") && {
            "Content-Range": mediaResponse.headers.get("content-range")!,
          }),
          ...(mediaResponse.headers.get("accept-ranges") && {
            "Accept-Ranges": mediaResponse.headers.get("accept-ranges")!,
          }),
        },
      });
    }

    if (contentType.includes("audio") || contentType.includes("video") || contentType.includes("octet-stream")) {
      const blob = await response.blob();
      return new NextResponse(blob, {
        status: response.status,
        headers: {
          "Content-Type": contentType,
          "Content-Length": response.headers.get("content-length") || "",
          ...(response.headers.get("content-range") && {
            "Content-Range": response.headers.get("content-range")!,
          }),
          ...(response.headers.get("accept-ranges") && {
            "Accept-Ranges": response.headers.get("accept-ranges")!,
          }),
        },
      });
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
    return NextResponse.json(
      { error: `Failed to connect to API: ${err.message}` },
      { status: 502 }
    );
  }
}

export async function GET(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, context.params, "GET");
}

export async function POST(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, context.params, "POST");
}

export async function PUT(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, context.params, "PUT");
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, context.params, "DELETE");
}

export async function PATCH(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, context.params, "PATCH");
}
