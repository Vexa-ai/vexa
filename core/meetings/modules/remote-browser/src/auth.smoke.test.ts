/**
 * auth.smoke — the pure-logic surface of @vexa/remote-browser, in isolation
 * (no real browser, no network). Two things this brick gets wrong silently if
 * regressed, both contract-level:
 *
 *  1. LAUNCH FLAGS (args.ts) — the authenticated/persistent-context flags must
 *     NOT contain --disable-web-security / --ignore-certificate-errors (Google's
 *     bot layer flags those and blocks the join), MUST disable
 *     AutomationControlled, MUST NOT be incognito, and MUST NOT turn Chromium's
 *     sandbox off (the launch decides that, sandbox.test.ts). The session (VNC+CDP)
 *     flags must carry the CDP debug args so an agent can attach.
 *
 *  2. LOGGED-IN DECISION (validate.ts) — validateLoggedIn returns loggedIn=true
 *     IFF (the URL the page SETTLES on is still the account page) AND (a known auth cookie
 *     has a value). A late redirect, another path, a sign-in page or a page that never settles
 *     is signed out; the cookie is necessary, never sufficient.
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
for (const off of ['--no-sandbox', '--disable-setuid-sandbox']) {
  check(!authArgs.includes(off), `authenticated args must NOT turn the sandbox off (${off})`);
}

const sessionArgs = getBrowserSessionArgs();
for (const bad of [...FORBIDDEN, '--no-sandbox', '--disable-setuid-sandbox']) {
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
/** A stand-in Page on a fake clock: `url()` answers by the time waited so far — `timeline` is a list
 *  of [fromMs, url], the last entry with fromMs <= now wins. */
function makePage(finalUrl: string | Array<[number, string]>, cookies: Cookie[]) {
  const timeline: Array<[number, string]> = typeof finalUrl === 'string' ? [[0, finalUrl]] : finalUrl;
  let now = 0;
  return {
    async goto(_url: string, _opts?: unknown) { /* no-op */ },
    async waitForTimeout(ms: number) { now += ms; },
    url() { let u = ''; for (const [t, v] of timeline) if (t <= now) u = v; return u; },
    context() { return { async cookies() { return cookies; } }; },
  } as any; // structurally satisfies the bits validateLoggedIn touches
}

async function decisionMatrix() {
  const google: AuthPlatform = 'google';
  const goodCookie = AUTH_COOKIES[google][0]; // e.g. 'SID'
  const accountUrl = 'https://myaccount.google.com/'; // not a sign-in URL
  const signinUrl = 'https://accounts.google.com/signin'; // a sign-in marker

  // (a) settled on the account page + has auth cookie → loggedIn
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

  // ── Zoom: the anonymous `_zm_ssid` is set the moment a sign-in page loads ──
  const zoom: AuthPlatform = 'zoom';
  const anon = [{ name: '_zm_ssid', value: 'anon' }];
  const SECRET = 'tok3n-SECRET';

  // (e) late redirect: on the account page at the first read, on sign-in from 4 s → signed out
  r = await validateLoggedIn(makePage([[0, 'https://zoom.us/profile'], [4000, 'https://zoom.us/signin#/login']], anon), zoom);
  check(r.loggedIn === false, `late redirect to sign-in must read signed out; got ${r.detail}`);

  // (f) settled on the account page with the cookie → signed in
  r = await validateLoggedIn(makePage('https://zoom.us/profile', anon), zoom);
  check(r.loggedIn === true, `settled on zoom.us/profile + cookie must read signed in; got ${r.detail}`);

  // (g) settled on another path of the same site (marketing home, no sign-in marker) → signed out
  r = await validateLoggedIn(makePage([[0, 'https://zoom.us/'], [1000, 'https://www.zoom.com/']], anon), zoom);
  check(r.loggedIn === false, `settled on another page of the site must read signed out; got ${r.detail}`);
  r = await validateLoggedIn(makePage('https://zoom.us/profilex', anon), zoom);
  check(r.loggedIn === false, `a path that only starts with the account path is not the account page; got ${r.detail}`);

  // (h) anonymous cookie + sign-in page → signed out
  r = await validateLoggedIn(makePage('https://zoom.us/signin', anon), zoom);
  check(r.loggedIn === false, `anonymous cookie on the sign-in page must read signed out; got ${r.detail}`);

  // (i) the platform's regional host → signed in
  r = await validateLoggedIn(makePage([[0, 'https://zoom.us/profile'], [2000, 'https://us05web.zoom.us/profile']], anon), zoom);
  check(r.loggedIn === true, `regional host us05web.zoom.us/profile must read signed in; got ${r.detail}`);
  check(/host=us05web\.zoom\.us path=\/profile /.test(r.detail ?? ''), `detail names the settled host and path; got ${r.detail}`);

  // a look-alike host outside the platform's domains → signed out
  r = await validateLoggedIn(makePage('https://zoom.us.example.com/profile', anon), zoom);
  check(r.loggedIn === false, `a look-alike host must read signed out; got ${r.detail}`);

  // a page that never settles within the budget → signed out
  const flapping: Array<[number, string]> = [];
  for (let t = 0; t <= 20000; t += 1000) flapping.push([t, `https://zoom.us/profile/${t}`]);
  r = await validateLoggedIn(makePage(flapping, anon), zoom);
  check(r.loggedIn === false && /settled=false/.test(r.detail ?? ''), `a page that never settles must read signed out; got ${r.detail}`);

  // (j) detail carries no query, no fragment and no cookie value
  r = await validateLoggedIn(makePage(`https://us05web.zoom.us/profile?code=${SECRET}#token=${SECRET}`, [{ name: '_zm_ssid', value: SECRET }]), zoom);
  check(!(r.detail ?? '').includes(SECRET) && !(r.detail ?? '').includes('?') && !(r.detail ?? '').includes('#'),
    `detail must carry no query, fragment or value; got ${r.detail}`);
  check(r.loggedIn === true, `the account page with a query still reads signed in; got ${r.detail}`);

  // Teams: the web app's own host teams.cloud.microsoft is its account page; Microsoft sign-in is not
  const teams: AuthPlatform = 'teams';
  const est = [{ name: 'ESTSAUTHPERSISTENT', value: 'x' }];
  r = await validateLoggedIn(makePage('https://teams.cloud.microsoft/v2/', est), teams);
  check(r.loggedIn === true, `teams.cloud.microsoft must read signed in; got ${r.detail}`);
  r = await validateLoggedIn(makePage('https://login.microsoftonline.com/common/oauth2', est), teams);
  check(r.loggedIn === false, `Microsoft sign-in must read signed out; got ${r.detail}`);

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
