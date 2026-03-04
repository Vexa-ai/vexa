import { NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Billing status endpoint — returns subscription info for the current user.
 * Used by the sidebar to show plan status in hosted mode.
 * Gracefully returns nulls on any error.
 */
export async function GET() {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({ subscription_status: null });
  }

  try {
    const cookieStore = await cookies();
    const token = cookieStore.get("vexa-token")?.value;

    if (!token) {
      return NextResponse.json({ subscription_status: null });
    }

    // Get user email from SSO cookie
    const userInfoStr = cookieStore.get("vexa-user-info")?.value;
    let email: string | null = null;
    if (userInfoStr) {
      try {
        const userInfo = JSON.parse(userInfoStr);
        email = userInfo.email;
      } catch {}
    }

    if (!email) {
      return NextResponse.json({ subscription_status: null });
    }

    // Fetch user by email from admin API
    const userResponse = await fetch(
      `${VEXA_ADMIN_API_URL}/admin/users/email/${encodeURIComponent(email)}`,
      {
        headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
      }
    );

    if (!userResponse.ok) {
      return NextResponse.json({ subscription_status: null });
    }

    const user = await userResponse.json();
    const data = user.data || {};

    return NextResponse.json({
      subscription_status: data.subscription_status || null,
      subscription_tier: data.subscription_tier || null,
      bot_balance_cents: data.bot_balance_cents ?? null,
      subscription_trial_end: data.subscription_trial_end || null,
    });
  } catch (error) {
    console.error("Billing status error:", error);
    return NextResponse.json({ subscription_status: null });
  }
}
