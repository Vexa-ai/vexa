import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

const ADMIN_COOKIE_NAME = "vexa-admin-session";
const COOKIE_MAX_AGE = 60 * 60 * 24; // 24 hours

/**
 * Verify admin session from cookie
 */
async function verifyAdminSession(): Promise<boolean> {
  try {
    const cookieStore = await cookies();
    const sessionCookie = cookieStore.get(ADMIN_COOKIE_NAME);

    if (!sessionCookie) {
      return false;
    }

    const sessionData = JSON.parse(
      Buffer.from(sessionCookie.value, "base64").toString()
    );

    // Check if session is expired (24 hours)
    const sessionAge = Date.now() - sessionData.timestamp;
    if (sessionAge > COOKIE_MAX_AGE * 1000) {
      return false;
    }

    return sessionData.authenticated === true;
  } catch {
    return false;
  }
}

/**
 * Admin API Proxy
 * Forwards requests to Vexa Admin API with X-Admin-API-Key header
 * SECURITY: Requires valid admin session cookie
 */
async function proxyRequest(
  request: NextRequest,
  params: Promise<{ path: string[] }>,
  method: string
): Promise<NextResponse> {
  // SECURITY: Verify admin session before proxying
  const isAdminAuthenticated = await verifyAdminSession();
  if (!isAdminAuthenticated) {
    return NextResponse.json(
      { error: "Admin authentication required", code: "ADMIN_AUTH_REQUIRED" },
      { status: 401 }
    );
  }

  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || process.env.VEXA_API_URL || "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json(
      { error: "Admin API key not configured" },
      { status: 500 }
    );
  }

  const { path } = await params;
  const pathString = path.join("/");

  // Build URL with query params
  const searchParams = request.nextUrl.searchParams.toString();
  const url = `${VEXA_ADMIN_API_URL}/admin/${pathString}${searchParams ? `?${searchParams}` : ""}`;

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
  };

  const fetchOptions: RequestInit = {
    method,
    headers,
  };

  // Add body for POST, PUT, PATCH
  if (["POST", "PUT", "PATCH"].includes(method)) {
    try {
      const body = await request.json();
      fetchOptions.body = JSON.stringify(body);
    } catch {
      // No body or invalid JSON - that's ok for some requests
    }
  }

  try {
    const response = await fetch(url, fetchOptions);

    // Handle non-JSON responses
    const contentType = response.headers.get("content-type");
    if (contentType && contentType.includes("application/json")) {
      const data = await response.json();
      return NextResponse.json(data, { status: response.status });
    }

    // Non-JSON response
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: { "Content-Type": contentType || "text/plain" },
    });
  } catch (error) {
    console.error("Admin API proxy error:", error);
    return NextResponse.json(
      { error: "Failed to connect to Admin API", details: (error as Error).message },
      { status: 502 }
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

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "PATCH");
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, params, "DELETE");
}
