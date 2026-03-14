import { cookies } from "next/headers";

/**
 * Resolve the authenticated user's ID from the vexa-token cookie.
 *
 * Uses the vexa-token (an API key) to look up the owning user via the
 * Admin API's user-facing auth endpoint, which resolves token -> user.
 * Falls back to the admin /users/email/ lookup when user-info is available.
 *
 * Returns the numeric user ID as a string, or null if unauthenticated.
 */
export async function getAuthenticatedUserId(): Promise<string | null> {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";

  if (!VEXA_ADMIN_API_KEY) return null;

  const cookieStore = await cookies();
  const token = cookieStore.get("vexa-token")?.value;
  if (!token) return null;

  // Validate the token by calling the API gateway (same as /api/auth/me)
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:18056";
  const verifyRes = await fetch(`${VEXA_API_URL}/meetings`, {
    headers: { "X-API-Key": token },
  });
  if (!verifyRes.ok) return null;

  // Get the user's email from the SSO cookie, then resolve to a user ID
  const userInfoStr = cookieStore.get("vexa-user-info")?.value;
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
