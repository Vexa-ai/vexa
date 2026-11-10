/**
 * validate — "are we actually logged in?" for a restored session.
 *
 * Navigate to the platform's account page, let it settle, and decide from (a) the URL it stays on —
 * it must still be the account page (positive evidence), not a sign-in page, home page or SSO hop —
 * and (b) whether a known session cookie has a value (necessary, never sufficient). Both must agree
 * for loggedIn=true.
 */
import type { Page } from 'playwright';
import { AuthPlatform, LoginStatus } from './types';
import { AUTH_NAVIGATION_DOMAINS, hostAllowed } from './navigation';

/** Where to navigate to probe logged-in state (an auth-gated account page). */
const ACCOUNT_URLS: Record<AuthPlatform, string> = {
  zoom: 'https://zoom.us/profile',
  google: 'https://myaccount.google.com/',
  teams: 'https://teams.microsoft.com/',
};

/** If the final URL contains any of these, we were bounced to a login page. */
const SIGNIN_URL_MARKERS: Record<AuthPlatform, string[]> = {
  zoom: ['/signin', '/login'],
  google: ['accounts.google.com/signin', 'accounts.google.com/ServiceLogin', '/ServiceLogin'],
  teams: ['login.microsoftonline.com', 'login.live.com', '/_#/login'],
};

/** Cookie names whose presence indicates a live session for the platform. */
export const AUTH_COOKIES: Record<AuthPlatform, string[]> = {
  zoom: ['_zm_ssid', 'zm_aid'],
  google: ['SID', '__Secure-1PSID', 'SAPISID'],
  teams: ['ESTSAUTHPERSISTENT', 'ESTSAUTH', 'ESTSAUTHLIGHT'],
};

/** Where to send a human to sign in. */
export const AUTH_LOGIN_URLS: Record<AuthPlatform, string> = {
  zoom: 'https://zoom.us/signin',
  google: 'https://accounts.google.com/',
  teams: 'https://teams.microsoft.com/',
};

/** How long verify lets the account page settle, and how often it reads the URL meanwhile. */
export const SETTLE_FIRST_MS = 2500;
export const SETTLE_STEP_MS = 1000;
/** A URL counts as settled only once this long has passed AND it read the same twice in a row. */
export const SETTLE_MIN_MS = 5000;
export const SETTLE_BUDGET_MS = 10_000;

/**
 * The account pages a signed-in profile STAYS on, per platform: the probe URL itself, plus the page
 * the platform itself moves a signed-in user to (the Teams web app lives on teams.cloud.microsoft).
 * A settled URL is "on the account page" when its host is one of these hosts or a sub-host of one
 * (a regional host such as us05web.zoom.us for zoom.us), the host is one of the platform's own
 * navigation domains (navigation.ts AUTH_NAVIGATION_DOMAINS), its path is the account path or below
 * it (on a segment boundary), and it carries no sign-in marker.
 */
const ACCOUNT_PAGES: Record<AuthPlatform, readonly string[]> = {
  zoom: [ACCOUNT_URLS.zoom],
  google: [ACCOUNT_URLS.google],
  teams: [ACCOUNT_URLS.teams, 'https://teams.cloud.microsoft/'],
};

/** A URL as evidence may name it: host and path only — no query, no fragment, no credentials. */
export function urlEvidence(raw: string): { host: string; path: string } {
  try {
    const u = new URL(raw);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return { host: u.protocol.replace(/:$/, ''), path: '' };
    return { host: u.hostname.toLowerCase(), path: u.pathname };
  } catch { return { host: '', path: '' }; }
}

const underPath = (path: string, prefix: string): boolean => {
  const base = prefix.replace(/\/+$/, '');
  return base === '' || path === base || path.startsWith(`${base}/`);
};

/** Is `url` the platform's account page (positive evidence of a signed-in profile)? */
export function onAccountPage(url: string, platform: AuthPlatform): boolean {
  let u: URL;
  try { u = new URL(url); } catch { return false; }
  if (u.protocol !== 'https:') return false;
  if (SIGNIN_URL_MARKERS[platform].some((m) => url.includes(m))) return false;
  if (!hostAllowed(u.hostname, AUTH_NAVIGATION_DOMAINS[platform])) return false;
  const host = u.hostname.toLowerCase().replace(/\.$/, '');
  return ACCOUNT_PAGES[platform].some((page) => {
    const a = new URL(page);
    return (host === a.hostname || host.endsWith(`.${a.hostname}`)) && underPath(u.pathname, a.pathname);
  });
}

/**
 * Read-only probe: navigate to the account page, let it settle, and judge on the URL it STAYS on
 * (the same URL on two consecutive reads, no sooner than SETTLE_MIN_MS, within a bounded budget). Signed in only when the settled
 * URL is still the account page AND a session cookie name of the platform has a value (the cookie is
 * necessary, never sufficient: Zoom sets an anonymous `_zm_ssid` on its sign-in page). A page that
 * never settles is signed out. `detail` names the platform, the settled host and path, and the
 * booleans — never a query string, a fragment or a cookie value.
 */
export async function validateLoggedIn(page: Page, platform: AuthPlatform): Promise<LoginStatus> {
  const checkUrl = ACCOUNT_URLS[platform];
  try {
    await page.goto(checkUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch { /* navigation may stall on heavy SPAs; fall through to URL/cookie read */ }

  const read = (): string => { try { return page.url(); } catch { return ''; } };
  await page.waitForTimeout(SETTLE_FIRST_MS);
  let waited = SETTLE_FIRST_MS;
  let prev = read();
  let settled: string | null = null;
  while (waited < SETTLE_BUDGET_MS) {
    await page.waitForTimeout(SETTLE_STEP_MS);
    waited += SETTLE_STEP_MS;
    const cur = read();
    if (cur && cur === prev && waited >= SETTLE_MIN_MS) { settled = cur; break; }
    prev = cur;
  }

  const accountPage = settled !== null && onAccountPage(settled, platform);

  let cookies: Array<{ name: string; value: string }> = [];
  try { cookies = await page.context().cookies() as any; } catch { /* ignore */ }
  const hasAuthCookie = AUTH_COOKIES[platform].some((n) => cookies.some((c) => c.name === n && !!c.value));

  const { host, path } = urlEvidence(settled ?? prev);
  return {
    loggedIn: accountPage && hasAuthCookie,
    detail: `platform=${platform} host=${host || '-'} path=${path || '-'} settled=${settled !== null} accountPage=${accountPage} authCookie=${hasAuthCookie}`,
  };
}
