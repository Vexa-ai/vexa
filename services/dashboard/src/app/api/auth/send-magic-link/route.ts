import { NextRequest, NextResponse } from "next/server";
import jwt from "jsonwebtoken";
import { sendMagicLinkEmail } from "@/lib/email";
import { getRegistrationConfig, validateEmailForRegistration } from "@/lib/registration";
import { findUserByEmail, createUser, createUserToken } from "@/lib/vexa-admin-api";
import { cookies } from "next/headers";
import { getVexaCookieOptions } from "@/lib/cookie-utils";

const JWT_SECRET = process.env.JWT_SECRET || process.env.VEXA_ADMIN_API_KEY || "default-secret-change-me";
const MAGIC_LINK_EXPIRY = "15m"; // 15 minutes

/**
 * Check if SMTP is configured
 */
function isSmtpConfigured(): boolean {
  const smtpHost = process.env.SMTP_HOST;
  const smtpUser = process.env.SMTP_USER;
  const smtpPass = process.env.SMTP_PASS;
  return !!(smtpHost && smtpUser && smtpPass);
}

/**
 * Check if user exists in Vexa API
 */
async function checkUserExists(email: string): Promise<{ exists: boolean; error?: string }> {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || process.env.VEXA_API_URL || "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) {
    return { exists: false };
  }

  try {
    const response = await fetch(
      `${VEXA_ADMIN_API_URL}/admin/users/email/${encodeURIComponent(email)}`,
      {
        headers: {
          "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
        },
        signal: AbortSignal.timeout(10000),
      }
    );

    if (response.ok) {
      return { exists: true };
    }

    if (response.status === 404) {
      return { exists: false };
    }

    if (response.status === 401 || response.status === 403) {
      return {
        exists: false,
        error: "Invalid admin API key. Please check VEXA_ADMIN_API_KEY configuration.",
      };
    }

    return { exists: false };
  } catch (error) {
    const message = (error as Error).message;
    if (message.includes("timeout") || message.includes("abort")) {
      return {
        exists: false,
        error: "Cannot reach Vexa API. Please check VEXA_API_URL configuration.",
      };
    }
    return { exists: false, error: `API error: ${message}` };
  }
}

/**
 * Direct login - authenticate user without email verification
 * Used when SMTP is not configured
 */
async function handleDirectLogin(email: string): Promise<NextResponse> {
  // Find or create user
  const findResult = await findUserByEmail(email);

  let user;
  let isNewUser = false;

  if (findResult.success && findResult.data) {
    user = findResult.data;
  } else if (findResult.error?.code === "NOT_FOUND") {
    // Check registration restrictions
    const config = getRegistrationConfig();
    const validationError = validateEmailForRegistration(email, false, config);

    if (validationError) {
      return NextResponse.json(
        { error: validationError },
        { status: 403 }
      );
    }

    // Create new user
    const createResult = await createUser({ email });

    if (!createResult.success || !createResult.data) {
      return NextResponse.json(
        { error: createResult.error?.message || "Failed to create user" },
        { status: 500 }
      );
    }

    user = createResult.data;
    isNewUser = true;
  } else if (findResult.error) {
    return NextResponse.json(
      { error: findResult.error.message, code: findResult.error.code },
      { status: 503 }
    );
  }

  // Create API token
  const tokenResult = await createUserToken(user!.id);

  if (!tokenResult.success || !tokenResult.data) {
    return NextResponse.json(
      { error: tokenResult.error?.message || "Failed to create session" },
      { status: 500 }
    );
  }

  const apiToken = tokenResult.data.token;

  // Set cookie
  const cookieStore = await cookies();
  cookieStore.set("vexa-token", apiToken, getVexaCookieOptions());

  // Return direct login response
  return NextResponse.json({
    success: true,
    mode: "direct",
    isNewUser,
    user: {
      id: user!.id,
      email: user!.email,
      name: user!.name,
      max_concurrent_bots: user!.max_concurrent_bots,
      created_at: user!.created_at,
    },
    token: apiToken,
  });
}

/**
 * Send magic link endpoint - sends an email with verification link
 * OR directly authenticates if SMTP is not configured
 */
export async function POST(request: NextRequest) {
  // Check Admin API configuration first
  const adminApiKey = process.env.VEXA_ADMIN_API_KEY;

  if (!adminApiKey || adminApiKey === "your_admin_api_key_here") {
    return NextResponse.json(
      { error: "Authentication service not configured. Please set VEXA_ADMIN_API_KEY.", code: "ADMIN_API_NOT_CONFIGURED" },
      { status: 503 }
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

    // Validate email format
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      return NextResponse.json(
        { error: "Invalid email format" },
        { status: 400 }
      );
    }

    // Check if SMTP is configured
    const smtpEnabled = isSmtpConfigured();

    if (!smtpEnabled) {
      // Direct login mode - no email verification
      console.log("SMTP not configured, using direct login mode for:", email);
      return handleDirectLogin(email);
    }

    // Magic Link mode - send email verification
    // Check if user exists (also validates API connectivity)
    const userCheck = await checkUserExists(email);

    if (userCheck.error) {
      return NextResponse.json(
        { error: userCheck.error, code: "API_ERROR" },
        { status: 503 }
      );
    }

    // Check registration restrictions before sending email
    const config = getRegistrationConfig();
    const validationError = validateEmailForRegistration(email, userCheck.exists, config);

    if (validationError) {
      return NextResponse.json(
        { error: validationError },
        { status: 403 }
      );
    }

    // Generate JWT token with email
    const token = jwt.sign(
      { email, type: "magic-link" },
      JWT_SECRET,
      { expiresIn: MAGIC_LINK_EXPIRY }
    );

    // Build magic link URL
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ||
                   (request.headers.get("origin") ||
                    `${request.headers.get("x-forwarded-proto") || "http"}://${request.headers.get("host")}`);

    const magicLink = `${baseUrl}/auth/verify?token=${encodeURIComponent(token)}`;

    // Send email
    try {
      await sendMagicLinkEmail(email, magicLink);
    } catch (emailError) {
      console.error("Failed to send magic link email:", emailError);
      const errorMessage = (emailError as Error).message;

      // Provide more specific error messages
      if (errorMessage.includes("ENOTFOUND") || errorMessage.includes("getaddrinfo")) {
        return NextResponse.json(
          { error: "Cannot reach email server. Please check SMTP_HOST configuration.", code: "SMTP_UNREACHABLE" },
          { status: 503 }
        );
      }

      if (errorMessage.includes("auth") || errorMessage.includes("535") || errorMessage.includes("Invalid login")) {
        return NextResponse.json(
          { error: "Email authentication failed. Please check SMTP_USER and SMTP_PASS configuration.", code: "SMTP_AUTH_FAILED" },
          { status: 503 }
        );
      }

      return NextResponse.json(
        { error: "Failed to send email. Please try again later.", code: "SMTP_ERROR", details: errorMessage },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      mode: "magic-link",
      message: "Magic link sent to your email",
    });
  } catch (error) {
    console.error("Send magic link error:", error);
    return NextResponse.json(
      { error: "An unexpected error occurred. Please try again.", details: (error as Error).message },
      { status: 500 }
    );
  }
}
