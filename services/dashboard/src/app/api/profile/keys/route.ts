import { NextRequest, NextResponse } from "next/server";
import { getAuthenticatedUserId } from "@/lib/auth-utils";

const getAdminConfig = () => {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";
  return { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY };
};

/**
 * GET /api/profile/keys — list user's API keys via admin API
 */
export async function GET(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ keys: [] });
  }

  // Resolve user from authenticated token instead of client-supplied userId
  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
      cache: "no-store",
    });

    if (!response.ok) {
      return NextResponse.json({ keys: [] });
    }

    const userData = await response.json();
    const keys = (userData.api_tokens || []).map(
      (t: { id: number; token: string; created_at: string }) => ({
        id: String(t.id),
        token: t.token,
        created_at: t.created_at,
      })
    );

    return NextResponse.json({ keys });
  } catch {
    return NextResponse.json({ keys: [] });
  }
}

/**
 * POST /api/profile/keys — create a new API key via admin API
 */
export async function POST(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ error: "Admin API not configured" }, { status: 503 });
  }

  // Resolve user from authenticated token instead of client-supplied userId
  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const body = await request.json();
    const scope = body.scope; // "bot" or "tx"

    const url = scope
      ? `${VEXA_ADMIN_API_URL}/admin/users/${userId}/tokens?scope=${encodeURIComponent(scope)}`
      : `${VEXA_ADMIN_API_URL}/admin/users/${userId}/tokens`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
      },
      body: JSON.stringify({}),
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      return NextResponse.json(
        { error: "Failed to create API key", ...errData },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message },
      { status: 500 }
    );
  }
}
