/** The hidden admin panel's gate — server-side, VERIFIED, fail-closed.
 *
 *  Admin = the logged-in user's email is on the `VEXA_ADMIN_EMAILS` allowlist (comma-separated,
 *  case-insensitive). Unlike the tokens routes' cookie-email seam, the email here is NOT taken
 *  from the client-sendable `vexa-user-info` cookie: the `vexa-token` auth cookie is validated
 *  against admin-api's internal oracle (`POST /internal/validate`, the same X-Internal-Secret
 *  edge the gateway uses) and the VALIDATED email is what the allowlist checks — a hand-crafted
 *  cookie claiming an admin's email gets nothing. Every failure path (no allowlist configured,
 *  no token, oracle unreachable, not allowlisted) returns null; callers answer 404 so the panel
 *  is invisible, not merely forbidden.
 */
import { cookies } from "next/headers";
import { AUTH_COOKIE } from "../auth/adminApi";

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
  const adminApiUrl = (process.env.VEXA_ADMIN_API_URL || "").replace(/\/$/, "");
  const internalSecret = process.env.VEXA_INTERNAL_API_SECRET || "";
  const emails = allowlist();
  if (emails.length === 0 || !adminApiUrl || !internalSecret) return null; // feature off / misconfigured → closed

  let token: string | undefined;
  try {
    token = (await cookies()).get(AUTH_COOKIE)?.value;
  } catch {
    /* outside a request scope */
  }
  if (!token) return null;

  try {
    const res = await fetch(`${adminApiUrl}/internal/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Internal-Secret": internalSecret },
      body: JSON.stringify({ token }),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { user_id?: string | number; email?: string };
    const email = (data.email || "").toLowerCase();
    if (!email || !emails.includes(email)) return null;
    return { email, userId: data.user_id ?? "" };
  } catch {
    return null; // oracle unreachable — fail closed
  }
}
