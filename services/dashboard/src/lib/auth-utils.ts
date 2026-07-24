import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";

export interface AuthenticatedUserIdentity {
  userId: number;
  email: string;
}

/**
 * Resolve the identity bound to the session's API key.
 *
 * The gateway's /auth/me response is the sole identity source: the same token
 * both authenticates the request and selects the user. No independently
 * supplied profile cookie participates in this decision.
 */
export async function getAuthenticatedUserIdentity(): Promise<AuthenticatedUserIdentity | null> {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) return null;

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) return null;

  try {
    const response = await fetch(
      `${VEXA_API_URL.replace(/\/$/, "")}/auth/me`,
      {
        headers: { "X-API-Key": token },
        cache: "no-store",
      }
    );
    if (!response.ok) return null;

    const data = await response.json() as Record<string, unknown>;
    const userId = data.user_id;
    const email = data.email;
    if (
      typeof userId !== "number" ||
      !Number.isSafeInteger(userId) ||
      userId <= 0 ||
      typeof email !== "string" ||
      !email.trim()
    ) {
      return null;
    }
    return { userId, email: email.trim() };
  } catch {
    return null;
  }
}

/**
 * Resolve the authenticated user's ID from the configured auth cookie.
 *
 * Returns the numeric user ID as a string, or null if unauthenticated.
 */
export async function getAuthenticatedUserId(): Promise<string | null> {
  const identity = await getAuthenticatedUserIdentity();
  return identity ? String(identity.userId) : null;
}
