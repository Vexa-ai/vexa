import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import crypto from "crypto";

const ADMIN_COOKIE_NAME = "vexa-admin-session";
const COOKIE_MAX_AGE = 60 * 60 * 24; // 24 hours

function getSigningSecret(): string {
  const secret = process.env.JWT_SECRET;
  if (!secret) {
    throw new Error("JWT_SECRET is not configured");
  }
  return secret;
}

function signCookieValue(payload: string): string {
  const hmac = crypto.createHmac("sha256", getSigningSecret()).update(payload).digest("hex");
  return `${payload}.${hmac}`;
}

function verifyCookieValue(signed: string): string | null {
  const dotIndex = signed.lastIndexOf(".");
  if (dotIndex === -1) return null;
  const payload = signed.substring(0, dotIndex);
  const signature = signed.substring(dotIndex + 1);
  const expected = crypto.createHmac("sha256", getSigningSecret()).update(payload).digest("hex");
  if (!crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) {
    return null;
  }
  return payload;
}

export async function POST(request: NextRequest) {
  try {
    const { token } = await request.json();

    if (!token) {
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
    if (token !== VEXA_ADMIN_API_KEY) {
      return NextResponse.json(
        { error: "Invalid admin token" },
        { status: 401 }
      );
    }

    // Token is valid - set a secure session cookie
    const cookieStore = await cookies();

    // Create HMAC-signed session value
    const payload = Buffer.from(
      JSON.stringify({
        authenticated: true,
        timestamp: Date.now(),
      })
    ).toString("base64");

    const sessionValue = signCookieValue(payload);

    cookieStore.set(ADMIN_COOKIE_NAME, sessionValue, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: COOKIE_MAX_AGE,
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
    const sessionCookie = cookieStore.get(ADMIN_COOKIE_NAME);

    if (!sessionCookie) {
      return NextResponse.json({ authenticated: false }, { status: 401 });
    }

    try {
      // Verify HMAC signature before trusting the payload
      const payload = verifyCookieValue(sessionCookie.value);
      if (!payload) {
        return NextResponse.json({ authenticated: false, reason: "invalid" }, { status: 401 });
      }

      const sessionData = JSON.parse(
        Buffer.from(payload, "base64").toString()
      );

      // Check if session is expired (24 hours)
      const sessionAge = Date.now() - sessionData.timestamp;
      if (sessionAge > COOKIE_MAX_AGE * 1000) {
        return NextResponse.json({ authenticated: false, reason: "expired" }, { status: 401 });
      }

      return NextResponse.json({ authenticated: true });
    } catch {
      return NextResponse.json({ authenticated: false, reason: "invalid" }, { status: 401 });
    }
  } catch (error) {
    console.error("Admin session check error:", error);
    return NextResponse.json({ authenticated: false }, { status: 500 });
  }
}
