/** Upgrade a signed-in browser's credential to a SESSION, in place (hotfix 2026-10-18).
 *
 *  A browser that signed in before this deployment could mint sessions still holds the old
 *  `terminal-login` KEY in its httpOnly auth cookie, and every decision that needs a session (a card
 *  press, D13) was refused: "Couldn't send your answer", with nothing the person could do about it.
 *  Here the terminal trades that cookie's key for a session for the SAME user — admin-api's
 *  `POST /internal/sessions/upgrade`, which upgrades only the exact legacy sign-in key and retires it
 *  — and the caller sets the new value on the browser's cookie.
 *
 *  What binds it to a person at the browser (D13's intent): the only credential ever offered is the
 *  one in this browser's own httpOnly cookie; a Company-mode tab (the admin account's key, the
 *  person's own waiting aside) is never upgraded; a delegation (`vxd_`) never; and admin-api refuses
 *  anything but a legacy login key — the terminal's self-serve `/api/tokens` cannot mint that name,
 *  so an API key a script holds is not one. Not a route file. */
import { cookies } from "next/headers";
import { AUTH_COOKIE, upgradeLoginSession } from "./adminApi";
import { PERSON_COOKIE } from "./companyMode";

/** One upgrade per old key at a time: a page's parallel requests share it, and for a short while
 *  after, a request still carrying the old key (inside admin-api's grace) gets the same session. */
const inflight = new Map<string, Promise<string | null>>();
const SHARE_MS = 60_000;

/** The session this browser's cookie key was upgraded to, or null when it is not upgradable (no
 *  cookie, a Company-mode tab, a delegation, not a legacy login key, admin-api refused). */
export async function upgradeBrowserSession(): Promise<string | null> {
  let token: string | undefined;
  let companyMode = false;
  try {
    const jar = await cookies();
    token = jar.get(AUTH_COOKIE)?.value;
    companyMode = !!jar.get(PERSON_COOKIE)?.value;
  } catch {
    return null;
  }
  if (!token || companyMode || token.startsWith("vxd_")) return null;
  let pending = inflight.get(token);
  if (!pending) {
    const key = token;
    pending = upgradeLoginSession(key).then((r) => {
      if (r.ok && r.data?.token) {
        console.info(`[terminal-auth] session upgrade: user ${r.data.user_id ?? "?"} moved from a login key to a session`);
        return r.data.token;
      }
      console.warn(`[terminal-auth] session upgrade refused (${r.status}): ${(r.error || "").slice(0, 200)}`);
      return null;
    }).catch(() => null);
    inflight.set(key, pending);
    setTimeout(() => inflight.delete(key), SHARE_MS).unref?.();
  }
  return pending;
}

/** The auth cookie carrying `token`, with the same attributes every sign-in door writes. */
export function sessionCookieHeader(token: string): string {
  const secure = (process.env.TERMINAL_URL || "").startsWith("https://")
    || (process.env.NEXTAUTH_URL || "").startsWith("https://");
  return `${AUTH_COOKIE}=${token}; Path=/; Max-Age=${60 * 60 * 24 * 30}; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`;
}

/** Is this upstream refusal "a session is required" (agent-api's D13 refusal)? */
export function isSessionRequired(status: number, body: string): boolean {
  if (status !== 403) return false;
  try {
    const d = (JSON.parse(body) as { detail?: { refused?: unknown } })?.detail;
    return !!d && typeof d === "object" && d.refused === "session_required";
  } catch {
    return false;
  }
}
