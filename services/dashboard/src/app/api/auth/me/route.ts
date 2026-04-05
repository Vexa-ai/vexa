import { NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Get current user info from token.
 * Auth chain: cookie → VEXA_API_KEY env var.
 * User identity resolved via gateway /auth/me (no direct admin-api access needed).
 */
export async function GET() {
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:8056";

  const cookieStore = await cookies();
  const cookieToken = cookieStore.get("vexa-token")?.value;
  const token = cookieToken || process.env.VEXA_API_KEY || "";

  if (!token) {
    return NextResponse.json(
      { error: "Not authenticated" },
      { status: 401 }
    );
  }

  try {
    // Resolve user identity via gateway /auth/me
    const response = await fetch(`${VEXA_API_URL}/auth/me`, {
      headers: { "X-API-Key": token },
    });

    if (!response.ok) {
      if (cookieToken) cookieStore.delete("vexa-token");
      return NextResponse.json(
        { error: "Invalid token" },
        { status: 401 }
      );
    }

    const data = await response.json();
    const user = {
      id: data.user_id,
      email: data.email || "dev@local",
      name: data.email || "Dev User",
    };

    return NextResponse.json({ authenticated: true, user, token });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to verify authentication" },
      { status: 500 }
    );
  }
}
