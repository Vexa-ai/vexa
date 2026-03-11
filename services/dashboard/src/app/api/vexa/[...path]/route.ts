import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

async function proxyRequest(
  request: NextRequest,
  params: Promise<{ path: string[] }>,
  method: string
): Promise<NextResponse> {
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:18056";

  // Get user's token from HTTP-only cookie (set during login)
  const cookieStore = await cookies();
  const userToken = cookieStore.get("vexa-token")?.value;

  // Fall back to env variable for backwards compatibility
  const VEXA_API_KEY = userToken || process.env.VEXA_API_KEY || "";

  const { path } = await params;
  const pathString = path.join("/");
  const url = `${VEXA_API_URL}/${pathString}`;

  const headers: HeadersInit = {
    "Content-Type": "application/json",
  };

  if (VEXA_API_KEY) {
    headers["X-API-Key"] = VEXA_API_KEY;
  }

  // Forward Range header for audio/video seeking support
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

    const response = await fetch(url, fetchOptions);
    clearTimeout(timeoutId);

    // Handle empty responses
    const contentType = response.headers.get("content-type");
    if (response.status === 204) {
      return new NextResponse(null, { status: response.status });
    }

    // Stream binary responses (audio, video, octet-stream) directly
    if (contentType && !contentType.includes("application/json")) {
      const responseHeaders = new Headers();
      // Forward relevant headers for media streaming
      for (const key of ["content-type", "content-length", "content-disposition",
        "accept-ranges", "content-range"]) {
        const value = response.headers.get(key);
        if (value) responseHeaders.set(key, value);
      }
      return new NextResponse(response.body, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const isTimeout = error instanceof DOMException && error.name === "AbortError";
    console.error(`Proxy ${isTimeout ? "timeout" : "error"} for ${method} ${url}:`, error);
    return NextResponse.json(
      { error: isTimeout ? "Backend request timed out" : "Failed to connect to Vexa API",
        details: (error as Error).message },
      { status: isTimeout ? 504 : 502 }
    );
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "GET");
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "POST");
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "PUT");
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "DELETE");
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "PATCH");
}
