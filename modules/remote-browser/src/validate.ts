/**
 * validate — "are we actually logged in?" for a restored session.
 *
 * Heuristic, deliberately: navigate to the platform's account page and decide from
 * (a) whether we landed on a sign-in URL and (b) whether a known session cookie is
 * present. Both must agree for loggedIn=true. The cookie names / URL markers below
 * are the refinement surface — tighten them per platform once observed live.
 */
import type { Page } from 'playwright';
import { AuthPlatform, LoginStatus } from './types';

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

export async function validateLoggedIn(page: Page, platform: AuthPlatform): Promise<LoginStatus> {
  const checkUrl = ACCOUNT_URLS[platform];
  try {
    await page.goto(checkUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch { /* navigation may stall on heavy SPAs; fall through to URL/cookie read */ }
  await page.waitForTimeout(2500);

  const url = page.url();
  const signedOut = SIGNIN_URL_MARKERS[platform].some((m) => url.includes(m));

  let cookies: Array<{ name: string; value: string }> = [];
  try { cookies = await page.context().cookies() as any; } catch { /* ignore */ }
  const hasAuthCookie = AUTH_COOKIES[platform].some((n) => cookies.some((c) => c.name === n && !!c.value));

  const loggedIn = !signedOut && hasAuthCookie;
  return {
    loggedIn,
    detail: `platform=${platform} url=${url} signedOut=${signedOut} authCookie=${hasAuthCookie}`,
  };
}
