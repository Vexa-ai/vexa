/**
 * auth.smoke — the pure-logic surface of @vexa/remote-browser, in isolation
 * (no real browser, no network). Two things this brick gets wrong silently if
 * regressed, both contract-level:
 *
 *  1. LAUNCH FLAGS (args.ts) — the authenticated/persistent-context flags must
 *     NOT contain --disable-web-security / --ignore-certificate-errors (Google's
 *     bot layer flags those and blocks the join), MUST disable
 *     AutomationControlled, and MUST NOT be incognito. The session (VNC+CDP)
 *     flags must carry the CDP debug args so an agent can attach.
 *
 *  2. LOGGED-IN DECISION (validate.ts) — validateLoggedIn returns loggedIn=true
 *     IFF (not bounced to a sign-in URL) AND (a known auth cookie is present).
 *     We drive the real function with a stub Playwright Page (url + cookies),
 *     so the AND-matrix is exercised without launching Chromium.
 *
 * Same shape as the mixed/pipeline *.smoke.test.ts (tsx + exit code, no assert lib).
 */
import {
  getAuthenticatedBrowserArgs,
  getBrowserSessionArgs,
  CDP_DEBUG_ARGS,
} from './args';
import { validateLoggedIn, AUTH_COOKIES, AUTH_LOGIN_URLS } from './validate';
import type { AuthPlatform } from './types';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

// ── 1. launch-flag invariants ───────────────────────────────────────────────
const FORBIDDEN = ['--disable-web-security', '--ignore-certificate-errors'];

const authArgs = getAuthenticatedBrowserArgs();
for (const bad of FORBIDDEN) {
  check(!authArgs.includes(bad), `authenticated args must NOT contain ${bad} (detected by bot layer)`);
}
check(authArgs.includes('--disable-blink-features=AutomationControlled'),
  'authenticated args must disable AutomationControlled');
check(!authArgs.includes('--incognito'),
  'authenticated args must NOT be incognito (wipes stored cookies)');
check(authArgs.includes('--no-sandbox'), 'authenticated args must include --no-sandbox (container)');

const sessionArgs = getBrowserSessionArgs();
for (const bad of FORBIDDEN) {
  check(!sessionArgs.includes(bad), `session args must NOT contain ${bad}`);
}
// session mode must expose CDP so an agent can attach over the gateway proxy
for (const cdp of CDP_DEBUG_ARGS) {
  check(sessionArgs.includes(cdp), `session args must carry CDP debug arg ${cdp}`);
}
check(CDP_DEBUG_ARGS.includes('--remote-debugging-port=9222'),
  'CDP_DEBUG_ARGS must bind the debugging port 9222');

// ── 2. validateLoggedIn decision matrix (stub Page) ──────────────────────────
type Cookie = { name: string; value: string };
function makePage(finalUrl: string, cookies: Cookie[]) {
  return {
    async goto(_url: string, _opts?: unknown) { /* no-op */ },
    async waitForTimeout(_ms: number) { /* no-op */ },
    url() { return finalUrl; },
    context() { return { async cookies() { return cookies; } }; },
  } as any; // structurally satisfies the bits validateLoggedIn touches
}

async function decisionMatrix() {
  const google: AuthPlatform = 'google';
  const goodCookie = AUTH_COOKIES[google][0]; // e.g. 'SID'
  const accountUrl = 'https://myaccount.google.com/'; // not a sign-in URL
  const signinUrl = 'https://accounts.google.com/signin'; // a sign-in marker

  // (a) landed on account page + has auth cookie → loggedIn
  let r = await validateLoggedIn(makePage(accountUrl, [{ name: goodCookie, value: 'x' }]), google);
  check(r.loggedIn === true, `expected loggedIn=true on account-url + ${goodCookie}; got ${r.detail}`);

  // (b) bounced to a sign-in URL even WITH a cookie → not loggedIn
  r = await validateLoggedIn(makePage(signinUrl, [{ name: goodCookie, value: 'x' }]), google);
  check(r.loggedIn === false, `expected loggedIn=false when bounced to sign-in url; got ${r.detail}`);

  // (c) on account page but NO auth cookie → not loggedIn
  r = await validateLoggedIn(makePage(accountUrl, [{ name: 'irrelevant', value: 'x' }]), google);
  check(r.loggedIn === false, `expected loggedIn=false with no auth cookie; got ${r.detail}`);

  // (d) cookie present but empty value → not a live session
  r = await validateLoggedIn(makePage(accountUrl, [{ name: goodCookie, value: '' }]), google);
  check(r.loggedIn === false, `expected loggedIn=false for empty-value auth cookie; got ${r.detail}`);

  // sanity: every platform has a login URL + at least one auth cookie defined
  for (const p of ['zoom', 'google', 'teams'] as AuthPlatform[]) {
    check(!!AUTH_LOGIN_URLS[p], `AUTH_LOGIN_URLS missing ${p}`);
    check((AUTH_COOKIES[p] ?? []).length > 0, `AUTH_COOKIES missing entries for ${p}`);
  }
}

async function main() {
  await decisionMatrix();
  if (fails.length) {
    console.log('❌ FAIL —\n  ' + fails.join('\n  '));
    process.exit(1);
  }
  console.log('✅ PASS — launch flags safe (no web-security/cert bypass, no incognito, AutomationControlled off, CDP in session mode); validateLoggedIn AND-matrix correct across google {accounturl×cookie}.');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
