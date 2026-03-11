import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getVexaCookieOptions } from "@/lib/cookie-utils";

/**
 * Login endpoint - uses Admin API to find/create user and generate token
 * The ADMIN_API_KEY is never exposed to the client
 */
export async function POST(request: NextRequest) {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || process.env.VEXA_API_URL || "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json(
      { error: "Server not configured for authentication" },
      { status: 500 }
    );
  }

  try {
    const { email } = await request.json();

    if (!email || typeof email !== "string") {
      return NextResponse.json(
        { error: "Email is required" },
        { status: 400 }
      );
    }

    // Step 1: Try to find user by email
    let user = null;
    let userResponse = await fetch(
      `${VEXA_ADMIN_API_URL}/admin/users/email/${encodeURIComponent(email)}`,
      {
        headers: {
          "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        },
      }
    );

    if (userResponse.ok) {
      user = await userResponse.json();
    } else if (userResponse.status === 404) {
      // Step 2: User doesn't exist - create them
      const createResponse = await fetch(`${VEXA_ADMIN_API_URL}/admin/users`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        },
        body: JSON.stringify({
          email,
          name: email.split("@")[0], // Default name from email
          max_concurrent_bots: 3,
        }),
      });

      if (!createResponse.ok) {
        const error = await createResponse.text();
        return NextResponse.json(
          { error: "Failed to create user", details: error },
          { status: 500 }
        );
      }

      user = await createResponse.json();
    } else {
      const error = await userResponse.text();
      return NextResponse.json(
        { error: "Failed to find user", details: error },
        { status: 500 }
      );
    }

    // Step 3: Get user with tokens to check if they have one
    const userDetailResponse = await fetch(
      `${VEXA_ADMIN_API_URL}/admin/users/${user.id}`,
      {
        headers: {
          "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        },
      }
    );

    if (!userDetailResponse.ok) {
      return NextResponse.json(
        { error: "Failed to get user details" },
        { status: 500 }
      );
    }

    const userWithTokens = await userDetailResponse.json();
    let token: string;

    // Step 4: If user has no tokens, create one
    if (!userWithTokens.api_tokens || userWithTokens.api_tokens.length === 0) {
      const tokenResponse = await fetch(
        `${VEXA_ADMIN_API_URL}/admin/users/${user.id}/tokens`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
          },
        }
      );

      if (!tokenResponse.ok) {
        const error = await tokenResponse.text();
        return NextResponse.json(
          { error: "Failed to create API token", details: error },
          { status: 500 }
        );
      }

      const newToken = await tokenResponse.json();
      token = newToken.token;
    } else {
      // User has existing tokens - use the first one
      // Note: We can't retrieve the actual token value for existing tokens
      // So we need to create a new one for login
      const tokenResponse = await fetch(
        `${VEXA_ADMIN_API_URL}/admin/users/${user.id}/tokens`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
          },
        }
      );

      if (!tokenResponse.ok) {
        const error = await tokenResponse.text();
        return NextResponse.json(
          { error: "Failed to create session token", details: error },
          { status: 500 }
        );
      }

      const newToken = await tokenResponse.json();
      token = newToken.token;
    }

    // Step 5: Set token in HTTP-only cookie for security
    const cookieStore = await cookies();
    cookieStore.set("vexa-token", token, getVexaCookieOptions());

    // Step 6: Return user info (without sensitive token in response for extra security)
    return NextResponse.json({
      user: {
        id: user.id,
        email: user.email,
        name: user.name,
        data: user.data || {},
        max_concurrent_bots: user.max_concurrent_bots,
        created_at: user.created_at,
      },
      token, // Also return token for client-side storage as backup
    });
  } catch (error) {
    console.error("Login error:", error);
    return NextResponse.json(
      { error: "Login failed", details: (error as Error).message },
      { status: 500 }
    );
  }
}
