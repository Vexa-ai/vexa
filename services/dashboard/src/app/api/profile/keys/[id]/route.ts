import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * DELETE /api/profile/keys/:id — revoke an API key via admin API
 */
export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ error: "Admin API not configured" }, { status: 503 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get("vexa-token")?.value;
  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const { id } = await params;

  try {
    const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/tokens/${id}`, {
      method: "DELETE",
      headers: {
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
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
