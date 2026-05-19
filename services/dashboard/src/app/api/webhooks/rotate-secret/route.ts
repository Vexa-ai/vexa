import { NextRequest, NextResponse } from "next/server";
import crypto from "crypto";
import { getAuthenticatedUserId } from "@/lib/auth-utils";

/**
 * POST /api/webhooks/rotate-secret — rotate webhook signing secret
 */
export async function POST(request: NextRequest) {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ error: "Admin API not configured" }, { status: 503 });
  }

  // Resolve user from authenticated token instead of client-supplied userId
  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {

    // Get current user data
    const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
      cache: "no-store",
    });

    if (!userRes.ok) {
      return NextResponse.json({ error: "User not found" }, { status: 404 });
    }

    const userData = await userRes.json();
    const currentData = userData.data || {};

    // Generate new signing secret
    const newSecret = `whsec_${crypto.randomBytes(24).toString("base64url")}`;

    // Update user data
    const updatedData = { ...currentData, webhook_secret: newSecret };

    const updateRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
      },
      body: JSON.stringify({ data: updatedData }),
    });

    if (!updateRes.ok) {
      const putRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        },
        body: JSON.stringify({ data: updatedData }),
      });
      if (!putRes.ok) {
        return NextResponse.json({ error: "Failed to rotate secret" }, { status: 500 });
      }
    }

    return NextResponse.json({ signing_secret: newSecret });
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message },
      { status: 500 }
    );
  }
}
