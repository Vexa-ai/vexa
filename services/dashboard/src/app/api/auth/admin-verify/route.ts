import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import crypto from "crypto";
import {
  ADMIN_COOKIE_NAME,
  ADMIN_SESSION_MAX_AGE_SECONDS,
  checkAdminSessionValue,
  createAdminSessionValue,
} from "@/lib/admin-session";

function isSecureRequest(): boolean {
  return process.env.NEXTAUTH_URL?.startsWith("https://") ||
         process.env.DASHBOARD_URL?.startsWith("https://") ||
         false;
}

function tokensMatch(given: string, expected: string): boolean {
  const a = Buffer.from(given, "utf8");
  const b = Buffer.from(expected, "utf8");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export async function POST(request: NextRequest) {
  try {
    const { token } = await request.json();

    if (!token || typeof token !== "string") {
      return NextResponse.json(
        { error: "Admin token is required" },
        { status: 400 }
      );
    }

    const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

    if (!VEXA_ADMIN_API_KEY) {
      return NextResponse.json(
        { error: "Admin API not configured" },
        { status: 500 }
      );
    }

    // Verify the token matches the configured admin key
    if (!tokensMatch(token, VEXA_ADMIN_API_KEY)) {
      return NextResponse.json(
        { error: "Invalid admin token" },
        { status: 401 }
      );
    }

    // Token is valid - set a secure session cookie
    const cookieStore = await cookies();

    // Create HMAC-signed session value
    const sessionValue = createAdminSessionValue();
    if (!sessionValue) {
      return NextResponse.json(
        { error: "Admin sign-in is not configured (JWT_SECRET is not set)" },
        { status: 503 }
      );
    }

    cookieStore.set(ADMIN_COOKIE_NAME, sessionValue, {
      httpOnly: true,
      secure: isSecureRequest(),
      sameSite: "lax",
      maxAge: ADMIN_SESSION_MAX_AGE_SECONDS,
      path: "/",
    });

    return NextResponse.json({
      success: true,
      message: "Admin authentication successful",
    });
  } catch (error) {
    console.error("Admin verify error:", error);
    return NextResponse.json(
      { error: "Authentication failed" },
      { status: 500 }
    );
  }
}

// Check if admin session is valid
export async function GET() {
  try {
    const cookieStore = await cookies();
    const check = checkAdminSessionValue(cookieStore.get(ADMIN_COOKIE_NAME)?.value);
    if (check.valid) {
      return NextResponse.json({ authenticated: true });
    }
    if (check.reason === "missing") {
      return NextResponse.json({ authenticated: false }, { status: 401 });
    }
    return NextResponse.json({ authenticated: false, reason: check.reason }, { status: 401 });
  } catch (error) {
    console.error("Admin session check error:", error);
    return NextResponse.json({ authenticated: false }, { status: 500 });
  }
}
