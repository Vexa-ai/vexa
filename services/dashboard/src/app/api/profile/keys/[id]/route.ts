import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";
import { getAuthenticatedUserId } from "@/lib/auth-utils";

/**
 * DELETE /api/profile/keys/:id — revoke an authenticated user's own API key via admin API
 */
export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || "";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_URL || !VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ error: "Admin API URL/key not configured" }, { status: 503 });
  }

  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const { id } = await params;

  try {
    const userResponse = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
      cache: "no-store",
    });

    if (!userResponse.ok) {
      return NextResponse.json({ error: "Failed to verify API key ownership" }, { status: userResponse.status });
    }

    const userData = await userResponse.json();
    const ownsToken = (userData.api_tokens || []).some(
      (apiToken: { id: number | string }) => String(apiToken.id) === String(id)
    );

    if (!ownsToken) {
      return NextResponse.json({ error: "API key not found" }, { status: 404 });
    }

    const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/tokens/${id}`, {
      method: "DELETE",
      headers: {
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        "X-API-Key": token,
      },
    });

    if (!response.ok) {
      return NextResponse.json(
        { error: "Failed to revoke API key" },
        { status: response.status }
      );
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message },
      { status: 500 }
    );
  }
}
