import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

export const dynamic = "force-dynamic";
export const revalidate = 0;

async function proxyRequest(
  request: NextRequest,
  params: Promise<{ path: string[] }>,
  method: string
): Promise<NextResponse> {
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:8066";

  // Get user's token from HTTP-only cookie (set during login)
  const cookieStore = await cookies();
  const userToken = cookieStore.get("vexa-token")?.value;

  // Fall back to env variable for backwards compatibility
  const VEXA_API_KEY = userToken || process.env.VEXA_API_KEY || "";

  const { path } = await params;
  let pathString = path.join("/");

  // /meetings list: primary source is GET /bots (meeting-api DB — all statuses).
  // Fallback to /bots/status (running containers only) if /bots fails.
  if (pathString === "meetings" && method === "GET") {
    // Try GET /bots first — returns all meetings from DB (active + completed)
    try {
      const botsResp = await fetch(`${VEXA_API_URL}/bots`, {
        headers: { "X-API-Key": VEXA_API_KEY },
        signal: AbortSignal.timeout(5000),
      });
      if (botsResp.ok) {
        const data = await botsResp.json();
        return NextResponse.json({ meetings: data.meetings || [] });
      }
    } catch (e) {
      console.error("[proxy] GET /bots failed, falling back to /bots/status:", e);
    }

    // Fallback: running containers only (no history)
    const meetings: any[] = [];
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

  const searchParams = request.nextUrl.searchParams.toString();
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
