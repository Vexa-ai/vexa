import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import crypto from "crypto";
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
 * GET /api/webhooks/config — fetch webhook configuration from user data
 */
export async function GET(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json(null, { status: 404 });
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
      return NextResponse.json(null, { status: 404 });
    }

    const userData = await response.json();
    const data = userData.data || {};

    // Extract webhook config from user data
    const secret = data.webhook_secret || null;
    const config = {
      endpoint_url: data.webhook_url || "",
      signing_secret_masked: secret ? "****" + secret.slice(-4) : null,
      events: data.webhook_events || {
        "meeting.completed": true,
        "meeting.started": false,
        "bot.failed": false,
        "meeting.status_change": false,
      },
    };

    return NextResponse.json(config);
  } catch {
    return NextResponse.json(null, { status: 404 });
  }
}

/**
 * PUT /api/webhooks/config — save webhook configuration to user data
 */
export async function PUT(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ error: "Admin API not configured" }, { status: 503 });
  }

  // Resolve user from authenticated token instead of client-supplied userId
  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get("vexa-token")?.value;

  try {
    const body = await request.json();

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

    // Use user-provided secret, keep existing, or auto-generate
    let secret = body.signing_secret || currentData.webhook_secret;
    if (!secret && body.endpoint_url) {
      secret = `whsec_${crypto.randomBytes(24).toString("base64url")}`;
    }

    // Merge webhook config into user data
    const updatedData = {
      ...currentData,
      webhook_url: body.endpoint_url || "",
      webhook_secret: secret,
      webhook_events: body.events || currentData.webhook_events || {},
    };

    // Update user data via admin API
    const updateRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
      },
      body: JSON.stringify({ data: updatedData }),
    });

    if (!updateRes.ok) {
      // Try PUT if PATCH is not supported
      const putRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        },
        body: JSON.stringify({ data: updatedData }),
      });

      if (!putRes.ok) {
        return NextResponse.json({ error: "Failed to save config" }, { status: 500 });
      }
    }

    // Also update the gateway's webhook URL for this user
    const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:18056";
    if (token) {
      await fetch(`${VEXA_API_URL}/user/webhook`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": token,
        },
        body: JSON.stringify({ webhook_url: body.endpoint_url || "" }),
      }).catch(() => {});
    }

    return NextResponse.json({
      endpoint_url: body.endpoint_url || "",
      signing_secret_masked: secret ? "****" + secret.slice(-4) : null,
      events: body.events || currentData.webhook_events || {},
    });
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message },
      { status: 500 }
    );
  }
}
