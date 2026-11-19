/** TEST USERS — app.dev tooling (#1783). The founder signs in as test accounts again and again; this
 *  hands an instance administrator a one-time sign-in link for an `@example.test` account, minted
 *  exactly as the emailed door mints one (`magicToken.ts`, 15 minutes, single use). Redeeming it goes
 *  through the same `/api/auth/redeem` and the same `signinAdmission` as every door: nothing here
 *  admits anybody, sets a password, or skips a check.
 *
 *  Three refusals, before anything else: the deployment has not switched it on (`VEXA_DEV_TEST_USERS=1`
 *  — the Helm chart and Lite never set it; an absent route answers 404), the caller is not a signed-in
 *  instance administrator (403), the address is not on the reserved test domain (400). Every link
 *  minted is logged with who asked for which address. */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { AUTH_COOKIE, validateAuthToken } from "./adminApi";

export const TEST_DOMAIN = "example.test";
export const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

export const testUsersEnabled = (): boolean => (process.env.VEXA_DEV_TEST_USERS || "").trim() === "1";

export function isTestAddress(email: unknown): email is string {
  if (typeof email !== "string") return false;
  const e = email.trim().toLowerCase();
  return /^[a-z0-9._+-]+@example\.test$/.test(e);
}

/** `tester-<slug>@example.test` from a name. */
export function testAddressFor(name: string): string | null {
  const slug = String(name ?? "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
  return slug ? `tester-${slug}@${TEST_DOMAIN}` : null;
}

/** The gate every test-user route passes first: switched on, and an instance administrator. */
export async function adminGate(): Promise<{ ok: true; email: string } | { ok: false; res: NextResponse }> {
  if (!testUsersEnabled()) return { ok: false, res: NextResponse.json({ error: "Not found" }, { status: 404, headers: NO_STORE }) };
  const token = (await cookies()).get(AUTH_COOKIE)?.value;
  if (!token) return { ok: false, res: NextResponse.json({ error: "Sign in first" }, { status: 401, headers: NO_STORE }) };
  const v = await validateAuthToken(token);
  if (!v.ok || v.isAdmin !== true) {
    return { ok: false, res: NextResponse.json({ error: "Only an instance administrator can use test users" }, { status: 403, headers: NO_STORE }) };
  }
  return { ok: true, email: v.email };
}

/** Where a link points: the configured public URL, as the emailed door builds it. */
export function publicBase(): string | null {
  const configured = (process.env.NEXTAUTH_URL || process.env.TERMINAL_URL || "").trim();
  try {
    const u = new URL(configured);
    if ((u.protocol !== "https:" && u.protocol !== "http:") || u.username || u.password) return null;
    return `${u.origin}${u.pathname}`.replace(/\/+$/, "");
  } catch { return null; }
}
