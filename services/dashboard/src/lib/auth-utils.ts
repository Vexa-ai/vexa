import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";

export type AuthenticatedUser = {
  /** Numeric user id, as a string. */
  id: string;
  email: string;
};

/**
 * Resolve the signed-in user from the auth cookie (an API token).
 *
 * Identity comes only from the gateway's `/auth/me`, which resolves the token
 * itself to its owning user. Nothing the browser sends alongside the token
 * (such as the `vexa-user-info` display cookie) is used to decide who the
 * user is. Every login path — magic link, direct login, Google / Microsoft
 * sign-in, and the hosted webapp's shared-domain session — sets this cookie.
 *
 * Returns null when there is no cookie, the token is not valid, or the
 * gateway cannot be reached.
 */
export async function getAuthenticatedUser(): Promise<AuthenticatedUser | null> {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) return null;

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) return null;

  try {
    const res = await fetch(`${VEXA_API_URL}/auth/me`, {
      headers: { "X-API-Key": token },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { user_id?: unknown; email?: unknown };
    const id =
      typeof data.user_id === "number" && Number.isSafeInteger(data.user_id)
        ? String(data.user_id)
        : typeof data.user_id === "string" && /^\d+$/.test(data.user_id)
          ? data.user_id
          : null;
    if (!id) return null;
    return { id, email: typeof data.email === "string" ? data.email : "" };
  } catch {
    return null;
  }
}

/**
 * Resolve the signed-in user's ID (see `getAuthenticatedUser`).
 * Returns the numeric user ID as a string, or null if unauthenticated.
 */
export async function getAuthenticatedUserId(): Promise<string | null> {
  return (await getAuthenticatedUser())?.id ?? null;
}
