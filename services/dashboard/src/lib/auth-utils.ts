import { cookies } from "next/headers";
import { getAuthCookieName, getUserInfoCookieName } from "@/lib/auth-cookies";

/**
 * Resolve the authenticated user's ID from the configured auth cookie.
 *
 * Uses the auth cookie (an API key) to look up the owning user via the
 * Admin API's user-facing auth endpoint, which resolves token -> user.
 * Resolves through the configured admin /users/email/ endpoint when user-info is available.
 *
 * Returns the numeric user ID as a string, or null if unauthenticated.
 */
export async function getAuthenticatedUserId(): Promise<string | null> {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL;
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_URL || !VEXA_ADMIN_API_KEY) return null;

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) return null;

  // Validate the token by calling the API gateway (same as /api/auth/me)
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) return null;
  const verifyRes = await fetch(`${VEXA_API_URL}/meetings`, {
    headers: { "X-API-Key": token },
  });
  if (!verifyRes.ok) return null;

  // Get the user's email from the SSO cookie, then resolve to a user ID
  const userInfoStr = cookieStore.get(getUserInfoCookieName())?.value;
  if (!userInfoStr) return null;

  let email: string;
  try {
    const userInfo = JSON.parse(userInfoStr);
    email = userInfo.email;
    if (!email) return null;
  } catch {
    return null;
  }

  // Look up user by email using the admin API key (server-side only)
  try {
    const res = await fetch(
      `${VEXA_ADMIN_API_URL}/admin/users/email/${encodeURIComponent(email)}`,
      {
        headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
        cache: "no-store",
      }
    );
    if (!res.ok) return null;
    const user = await res.json();
    return user.id != null ? String(user.id) : null;
  } catch {
    return null;
  }
}

/**
 * Return the email bound to the already-validated hosted session.
 *
 * The cookie is only trusted after the API token succeeds against the gateway.
 * Account routes use this value to resolve/upsert through the stock Admin API's
 * supported email contract instead of inventing a user-by-id read.
 */
export async function getAuthenticatedUserEmail(): Promise<string | null> {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) return null;

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) return null;

  try {
    const verifyRes = await fetch(`${VEXA_API_URL}/meetings`, {
      headers: { "X-API-Key": token },
    });
    if (!verifyRes.ok) return null;
  } catch {
    return null;
  }

  const userInfoStr = cookieStore.get(getUserInfoCookieName())?.value;
  if (!userInfoStr) return null;

  try {
    const userInfo = JSON.parse(userInfoStr) as { email?: unknown };
    return typeof userInfo.email === "string" && userInfo.email
      ? userInfo.email
      : null;
  } catch {
    return null;
  }
}
