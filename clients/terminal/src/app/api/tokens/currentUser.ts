/** Resolve the logged-in user's admin-api user_id from the auth cookie — the ONE ownership seam for
 *  the /api/tokens routes.
 *
 *  The admin-api token endpoints are ADMIN-tier, so the terminal calls them with the server's
 *  VEXA_ADMIN_API_KEY (same as the login flow). That makes THIS resolution the security boundary:
 *  the `vexa-token` auth cookie is validated against admin-api's internal oracle (`POST
 *  /internal/validate`) and the VALIDATED {user_id, email} is what every token operation is scoped
 *  to — NEVER anything the client sends (P20). The `vexa-user-info` cookie is deliberately ignored:
 *  httpOnly only stops JS reads, so a hand-crafted Cookie header claiming another user's email
 *  would otherwise mint/list/revoke THAT user's tokens.
 */
import { cookies } from "next/headers";
import { AUTH_COOKIE, validateAuthToken } from "../auth/adminApi";

export type CurrentUser =
  | { ok: true; userId: string | number; email: string }
  | { ok: false; status: number; error: string };

export async function currentUser(): Promise<CurrentUser> {
  let token: string | undefined;
  try {
    token = (await cookies()).get(AUTH_COOKIE)?.value;
  } catch {
    /* outside a request scope */
  }
  if (!token) return { ok: false, status: 401, error: "Not authenticated" };

  return validateAuthToken(token);
}
