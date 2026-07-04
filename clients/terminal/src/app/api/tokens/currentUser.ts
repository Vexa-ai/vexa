/** Resolve the logged-in user's admin-api user_id from the auth cookies — the ONE ownership seam for
 *  the /api/tokens routes.
 *
 *  The admin-api token endpoints are ADMIN-tier, so the terminal calls them with the server's
 *  VEXA_ADMIN_API_KEY (same as the login flow). That makes THIS resolution the security boundary:
 *  the user_id every token operation is scoped to comes from the httpOnly `vexa-user-info` cookie's
 *  email → findUserByEmail — NEVER from anything the client sends (P20).
 */
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE, findUserByEmail } from "../auth/adminApi";

export type CurrentUser =
  | { ok: true; userId: string | number; email: string }
  | { ok: false; status: number; error: string };

export async function currentUser(): Promise<CurrentUser> {
  let token: string | undefined;
  let info: string | undefined;
  try {
    const store = await cookies();
    token = store.get(AUTH_COOKIE)?.value;
    info = store.get(USER_INFO_COOKIE)?.value;
  } catch {
    /* outside a request scope */
  }
  if (!token || !info) return { ok: false, status: 401, error: "Not authenticated" };

  let email: string | undefined;
  try {
    ({ email } = JSON.parse(info) as { email?: string });
  } catch {
    /* malformed cookie */
  }
  if (!email) return { ok: false, status: 401, error: "Not authenticated" };

  const found = await findUserByEmail(email);
  if (!found.ok || !found.data) {
    return { ok: false, status: found.notFound ? 401 : found.status || 503, error: found.error || "Unknown user" };
  }
  return { ok: true, userId: found.data.id, email };
}
