import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import jwt from "jsonwebtoken";
import { getRegistrationConfig, validateEmailForRegistration } from "@/lib/registration";
import { findUserByEmail, createUser, createUserToken, type ApiError } from "@/lib/vexa-admin-api";
import { getVexaCookieOptions } from "@/lib/cookie-utils";

const JWT_SECRET = process.env.JWT_SECRET || process.env.VEXA_ADMIN_API_KEY || "default-secret-change-me";

interface MagicLinkPayload {
  email: string;
  type: string;
}

interface VerifyErrorResponse {
  error: string;
  code?: string;
  details?: string;
  canRetry?: boolean;
}

/**
 * Create a standardized error response
 */
function errorResponse(
  message: string,
  status: number,
  code?: string,
  details?: string,
  canRetry = false
): NextResponse<VerifyErrorResponse> {
  return NextResponse.json(
    { error: message, code, details, canRetry },
    { status }
  );
}

/**
 * Convert API error to HTTP response
 */
function apiErrorToResponse(apiError: ApiError, context: string): NextResponse<VerifyErrorResponse> {
  const canRetry = ["TIMEOUT", "NETWORK_ERROR", "SERVICE_UNAVAILABLE", "SERVER_ERROR"].includes(apiError.code);

  // Add context to the message
  const message = `${context}: ${apiError.message}`;

  // Map API error codes to appropriate HTTP status
  const statusMap: Record<string, number> = {
    NOT_CONFIGURED: 503,
    UNAUTHORIZED: 401,
    FORBIDDEN: 403,
    NOT_FOUND: 404,
    CONFLICT: 409,
    VALIDATION_ERROR: 400,
    RATE_LIMITED: 429,
    SERVER_ERROR: 502,
    SERVICE_UNAVAILABLE: 503,
    TIMEOUT: 504,
    NETWORK_ERROR: 503,
    DNS_ERROR: 503,
    CONNECTION_REFUSED: 503,
  };

  const status = statusMap[apiError.code] || 500;

  return errorResponse(message, status, apiError.code, apiError.details, canRetry);
}

/**
 * Verify magic link token and complete login
 */
export async function POST(request: NextRequest) {
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  // Check configuration first
  if (!VEXA_ADMIN_API_KEY || VEXA_ADMIN_API_KEY === "your_admin_api_key_here") {
    return errorResponse(
      "Authentication service not configured. Please contact the administrator.",
      503,
      "NOT_CONFIGURED"
    );
  }

  try {
    const body = await request.json();
    const { token } = body;

    if (!token || typeof token !== "string") {
      return errorResponse("Verification token is required", 400, "MISSING_TOKEN");
    }

    // Step 1: Verify JWT token
    let payload: MagicLinkPayload;
    try {
      payload = jwt.verify(token, JWT_SECRET) as MagicLinkPayload;
    } catch (jwtError) {
      const err = jwtError as jwt.JsonWebTokenError;
      if (err.name === "TokenExpiredError") {
        return errorResponse(
          "This link has expired. Please request a new one.",
          401,
          "TOKEN_EXPIRED"
        );
      }
      return errorResponse(
        "Invalid verification link. Please request a new one.",
        401,
        "INVALID_TOKEN"
      );
    }

    if (payload.type !== "magic-link") {
      return errorResponse("Invalid token type", 401, "INVALID_TOKEN_TYPE");
    }

    const email = payload.email;

    // Step 2: Try to find existing user
    const findResult = await findUserByEmail(email);

    let user;
    let isNewUser = false;

    if (findResult.success && findResult.data) {
      // User exists
      user = findResult.data;
    } else if (findResult.error?.code === "NOT_FOUND") {
      // User doesn't exist - check if registration is allowed
      isNewUser = true;
    } else if (findResult.error) {
      // API error occurred
      return apiErrorToResponse(findResult.error, "Failed to verify account");
    }

    // Step 3: Create new user if needed
    if (isNewUser) {
      // Check registration restrictions
      const config = getRegistrationConfig();
      const validationError = validateEmailForRegistration(email, false, config);

      if (validationError) {
        return errorResponse(validationError, 403, "REGISTRATION_BLOCKED");
      }

      // Create the user
      const createResult = await createUser({ email });

      if (!createResult.success || !createResult.data) {
        if (createResult.error) {
          // Check if user was created in a race condition
          if (createResult.error.code === "CONFLICT") {
            // User was created between our check and create, try to find again
            const retryFind = await findUserByEmail(email);
            if (retryFind.success && retryFind.data) {
              user = retryFind.data;
            } else {
              return apiErrorToResponse(createResult.error, "Failed to create account");
            }
          } else {
            return apiErrorToResponse(createResult.error, "Failed to create account");
          }
        } else {
          return errorResponse("Failed to create account", 500, "CREATE_FAILED");
        }
      } else {
        user = createResult.data;
      }
    }

    // Step 4: Create API token for the user
    const tokenResult = await createUserToken(user!.id);

    if (!tokenResult.success || !tokenResult.data) {
      if (tokenResult.error) {
        return apiErrorToResponse(tokenResult.error, "Failed to create session");
      }
      return errorResponse("Failed to create session", 500, "TOKEN_CREATE_FAILED");
    }

    const apiToken = tokenResult.data.token;

    // Step 5: Set token in HTTP-only cookie
    const cookieStore = await cookies();
    cookieStore.set("vexa-token", apiToken, getVexaCookieOptions());

    // Step 6: Return success with user info
    return NextResponse.json({
      success: true,
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
  } catch (error) {
    console.error("Verify error:", error);
    const err = error as Error;

    // Check for JSON parse errors
    if (err.message.includes("JSON")) {
      return errorResponse("Invalid request format", 400, "INVALID_REQUEST");
    }

    return errorResponse(
      "An unexpected error occurred. Please try again.",
      500,
      "INTERNAL_ERROR",
      err.message,
      true
    );
  }
}
