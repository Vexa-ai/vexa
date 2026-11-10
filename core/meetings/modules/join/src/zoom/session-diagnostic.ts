/**
 * Evidence about a Zoom sign-in, from the browser's cookie jar — the NAMED sign-in cookies only.
 *
 * For telling "the saved session never reached Chromium", "it reached it but expired / was session-only
 * and dropped" and "it reached the web client and Zoom still treated us as a guest", a signed-in bot logs
 * this line at launch (before any navigation), at the Zoom page, and just before write-back.
 *
 * A cookie VALUE is a credential and never leaves this function: per candidate name and per cookie
 * domain the line holds the lifetime (session-only / expires in / EXPIRED), the value's LENGTH and the
 * httpOnly / secure flags. Names not found are listed once as absent. Totals: cookies per Zoom domain and
 * how many of them are session-only. Only cookies of Zoom domains are read; a name or domain is cut to
 * 40 characters of [A-Za-z0-9_.-] (anything else becomes `?`). No URL, path or query is ever named.
 */
export interface JarCookie {
  name: string;
  domain: string;
  value?: string;
  expires?: number;
  httpOnly?: boolean;
  secure?: boolean;
}

/** The Zoom cookies a signed-in web client may rest on. Any other Zoom cookie whose name matches /sso/i
 *  is a candidate too. */
export const ZOOM_SIGNIN_COOKIE_NAMES: readonly string[] = [
  '_zm_ssid', 'zm_aid', 'zm_haid', '_zm_kms', 'zm_jwt', 'cred', '_zm_page_auth', 'zm_cluster',
];

const ZOOM_COOKIE_DOMAIN = /(^|\.)(zoom\.us|zoom\.com|zoomgov\.com)$/;

const safe = (s: string): string => String(s || '').slice(0, 40).replace(/[^A-Za-z0-9_.-]/g, '?');

/** `expires` is seconds since the epoch; -1 (or anything <= 0, or absent) is a session-only cookie. */
export function cookieLifetime(expires: number | undefined, nowSec: number): string {
  if (typeof expires !== 'number' || !(expires > 0)) return 'session-only';
  const left = expires - nowSec;
  if (left <= 0) return 'EXPIRED';
  const days = Math.floor(left / 86400);
  return days >= 1 ? `expires in ${days}d` : `expires in ${Math.max(1, Math.ceil(left / 3600))}h`;
}

export function describeZoomSignInCookies(cookies: readonly JarCookie[], nowSec = Date.now() / 1000): string {
  const zoom = cookies.filter((c) => ZOOM_COOKIE_DOMAIN.test(String(c.domain || '').toLowerCase().replace(/^\./, '')));
  const isCandidate = (name: string): boolean => ZOOM_SIGNIN_COOKIE_NAMES.includes(name) || /sso/i.test(name);

  const found: string[] = [];
  const seen = new Set<string>();
  for (const c of zoom
    .filter((c) => isCandidate(String(c.name || '')))
    .sort((a, b) => `${a.name}@${a.domain}`.localeCompare(`${b.name}@${b.domain}`))) {
    seen.add(String(c.name));
    found.push(`${safe(c.name)}@${safe(c.domain)}: ${cookieLifetime(c.expires, nowSec)}, `
      + `valueLength=${String(c.value ?? '').length}, httpOnly=${!!c.httpOnly}, secure=${!!c.secure}`);
  }
  const absent = ZOOM_SIGNIN_COOKIE_NAMES.filter((n) => !seen.has(n));

  const totals = new Map<string, { all: number; sessionOnly: number }>();
  for (const c of zoom) {
    const d = safe(String(c.domain || '').toLowerCase());
    const t = totals.get(d) ?? { all: 0, sessionOnly: 0 };
    t.all++;
    if (cookieLifetime(c.expires, nowSec) === 'session-only') t.sessionOnly++;
    totals.set(d, t);
  }
  const totalsLine = [...totals.entries()].sort(([a], [b]) => a.localeCompare(b))
    .map(([d, t]) => `${d} ${t.all} (session-only ${t.sessionOnly})`).join(' | ');

  return `zoom sign-in cookies (no values): ${found.length ? found.join('; ') : 'none'}`
    + `; absent: ${absent.length ? absent.join(' ') : 'none'}`
    + `; zoom cookies per domain: ${totalsLine || 'none'}`;
}
