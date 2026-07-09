/** The hidden admin panel's gate — server-side, VERIFIED, fail-closed.
 *
 *  Admin = the DB-backed role (`users.data.is_admin`, bootstrap-claimed by the first sign-in on
 *  a fresh instance and surfaced as `is_admin` by admin-api's validate oracle) OR the
 *  `VEXA_ADMIN_EMAILS` allowlist (comma-separated, case-insensitive) kept as an operator
 *  override. The identity is NOT taken from the client-sendable `vexa-user-info` cookie: the
 *  `vexa-token` auth cookie is validated against admin-api's internal oracle (validateAuthToken
 *  — the same seam the /api/tokens routes scope ownership through) and the VALIDATED email/role
 *  is what gets checked — a hand-crafted cookie claiming an admin's email gets nothing. Every
 *  failure path (no token, oracle unreachable or misconfigured, neither role nor allowlisted)
 *  returns null; callers answer 404 so the panel is invisible, not merely forbidden.
 */
import { cookies } from "next/headers";
import { AUTH_COOKIE, validateAuthToken } from "../auth/adminApi";

function allowlist(): string[] {
  return (process.env.VEXA_ADMIN_EMAILS || "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
}

export interface AdminIdentity {
  email: string;
  userId: string | number;
}

/** The verified admin identity, or null (callers must 404 on null). */
export async function requireAdmin(): Promise<AdminIdentity | null> {
  let token: string | undefined;
  try {
    token = (await cookies()).get(AUTH_COOKIE)?.value;
  } catch {
    /* outside a request scope */
  }
  if (!token) return null;

  const validated = await validateAuthToken(token);
  if (!validated.ok) return null; // invalid token, oracle unreachable, or misconfigured — fail closed
  const email = validated.email.toLowerCase();
  if (!validated.isAdmin && !allowlist().includes(email)) return null;
  return { email, userId: validated.userId };
}
