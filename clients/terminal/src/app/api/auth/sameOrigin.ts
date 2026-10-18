/** The CSRF guard every form POST that signs somebody in shares: the emailed link's redeem
 *  (`./redeem/route.ts`) and the invite's one-click join (`../join/accept/route.ts`).
 *
 *  Only this instance's own page may press the button: `Sec-Fetch-Site` (when sent) must be
 *  `same-origin`, `Origin` (when sent) must be one of this instance's origins, and a POST carrying
 *  neither is refused. Otherwise another site could post its own token from a victim's browser and
 *  sign them in as somebody else (login CSRF). */
import type { NextRequest } from "next/server";

/** This instance's own origins: the configured public URL(s) and the one the request arrived on. */
export function ownOrigins(request: NextRequest): Set<string> {
  const out = new Set<string>();
  for (const u of [process.env.TERMINAL_URL, process.env.NEXTAUTH_URL]) {
    try { if (u) out.add(new URL(u).origin); } catch { /* not a URL — ignored */ }
  }
  const h = request.headers;
  const host = h?.get("x-forwarded-host") || h?.get("host");
  if (host) {
    const proto = (h?.get("x-forwarded-proto") || request.nextUrl.protocol.replace(/:$/, "") || "https").split(",")[0].trim();
    out.add(`${proto}://${host.split(",")[0].trim()}`);
  }
  try { out.add(request.nextUrl.origin); } catch { /* ignored */ }
  return out;
}

export function sameOriginPost(request: NextRequest): boolean {
  const h = request.headers;
  const site = h?.get("sec-fetch-site");
  const origin = h?.get("origin");
  if (site && site !== "same-origin") return false;
  if (origin) return origin !== "null" && ownOrigins(request).has(origin);
  return site === "same-origin";
}

/** Cookies are Secure iff the deploy is HTTPS (mirrors login/ and the OAuth callback). */
export function isSecureRequest(): boolean {
  return (
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    (process.env.NEXTAUTH_URL || "").startsWith("https://")
  );
}
