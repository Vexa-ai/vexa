/** THE ONE WAY A DATA-DRIVEN URL BECOMES A NAVIGATION. A notification's object, a reference's
 *  answer and a claim's arrival all hand the client a URL from the server, and the server built some
 *  of them out of what other people wrote. `location.assign` with such a value would follow a
 *  `javascript:` URL (script in this origin) or another host (a phishing page wearing our click).
 *  `navigateWithin` follows a URL only when it stays on this origin; anything else lands on `/`. */

/** `url` as a path on this origin (`/w/x?y#z`), or null when it would leave it or is not a URL. */
export function pathWithin(url: unknown, origin: string = currentOrigin()): string | null {
  if (typeof url !== "string") return null;
  const raw = url.trim();
  // a site-relative path only: no scheme, no `//host`, no backslash a browser reads as a slash, no
  // control character
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\") || /[\u0000-\u001f\u007f]/.test(raw)) return null;
  let u: URL;
  try { u = new URL(raw, origin); } catch { return null; }
  if (u.origin !== origin) return null;
  return `${u.pathname}${u.search}${u.hash}`;
}

/** Navigate to `url` if it stays on this origin, else to `/`. */
export function navigateWithin(url: unknown): void {
  if (typeof window === "undefined") return;
  window.location.assign(pathWithin(url) ?? "/");
}

function currentOrigin(): string {
  try { return typeof window === "undefined" ? "http://localhost" : window.location.origin || "http://localhost"; } catch { return "http://localhost"; }
}
