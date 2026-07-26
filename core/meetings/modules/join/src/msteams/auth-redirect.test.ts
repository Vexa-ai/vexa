/**
 * Regression guard for the Teams anonymous-join sign-in redirect (Vexa-ai/vexa#915).
 *
 * Prod v0.12.16, three bots inside an hour: the meetup-join navigation landed on
 * `login.microsoftonline.com/common/oauth2/v2.0/authorize`, and the join flow then spent ~75s
 * pantomiming a join against a sign-in page — "Continue button not found, continuing…",
 * "Camera button not found", "Display name input not found", "Join button not found", and a
 * mic-mute keystroke that "succeeded" — before the admission wait reported
 * `Bot was not admitted into the Teams meeting within the timeout period`. A host-admission
 * timeout for what was an authentication redirect: the host was never asked.
 *
 * Fix (at the point of introduction, join.ts + auth-redirect.ts): the join asserts it is on the
 * meeting before it drives the pre-join, and a sign-in URL is a typed terminal
 * (`TeamsJoinRedirectError`, reasonCode `teams_auth_redirect`) carrying the redacted URL.
 *
 * Fabricated-DOM/URL test in the same style as msteams/removal.test.ts — no browser, no live
 * meeting. A VIRTUAL clock (Date.now + the mock page's waitForTimeout) makes the 45s pre-join
 * wait free, so "how long would this have burned" is an assertable number.
 *
 * Run: npx tsx src/msteams/auth-redirect.test.ts
 */

import {
  TeamsJoinRedirectError,
  TEAMS_AUTH_REDIRECT,
  TEAMS_OFF_MEETING_ORIGIN,
  isMicrosoftLoginUrl,
  isTeamsMeetingUrl,
  redactUrl,
} from './auth-redirect';
import { joinMicrosoftTeams } from './join';

// ── virtual clock: the join flow measures elapsed with Date.now() and sleeps with
//    page.waitForTimeout(), so driving both from one counter runs 45s of waiting instantly.
let vnow = 1_000_000;
const realDateNow = Date.now;
(Date as any).now = () => vnow;

// ── log capture (the join layer logs single-line JSON through console.log) ──
let captured: string[] = [];
const realConsoleLog = console.log;
function startCapture() {
  captured = [];
  console.log = (...a: any[]) => {
    const s = a.map(String).join(' ');
    try { captured.push(JSON.parse(s).msg ?? s); } catch { captured.push(s); }
  };
}
function stopCapture(): string[] { console.log = realConsoleLog; return captured; }

/**
 * Minimal Playwright-Page stand-in. `url` is what page.url() reports (optionally a sequence:
 * the Nth entry is returned from the Nth call onward, modelling a redirect that lands mid-wait).
 * `visible` are substrings; a locator is visible when its selector contains one of them.
 */
function mockPage(url: string | string[], visible: string[] = []): any {
  const urls = Array.isArray(url) ? url : [url];
  let urlCalls = 0;
  const node = (sel: string): any => ({
    first: () => node(sel),
    isVisible: async () => visible.some((v) => sel.includes(v)),
    waitFor: async () => { throw new Error(`timeout waiting for ${sel}`); },
    click: async () => { throw new Error(`no element for ${sel}`); },
    fill: async () => { throw new Error(`no element for ${sel}`); },
    getAttribute: async () => null,
  });
  return {
    url: () => urls[Math.min(urlCalls++, urls.length - 1)],
    goto: async () => {},
    waitForTimeout: async (ms: number) => { vnow += ms; },
    locator: node,
    evaluate: async () => 'media warm-up success (tracks=0)',
    keyboard: { press: async () => {} },
    isClosed: () => false,
  };
}

const BOT_CONFIG = { platform: 'teams', botName: 'Vexa Bot' } as any;

/** The prod URL from #915 (query redacted here as it is in the log). */
const LOGIN_URL =
  'https://login.microsoftonline.com/common/oauth2/v2.0/authorize' +
  '?client_id=5e3ce6c0-REDACTED&redirect_uri=https%3A%2F%2Fteams.microsoft.com%2Fv2%2Fauthv2' +
  '&response_type=code&code_challenge_method=S256';

const MEETUP_JOIN_URL =
  'https://teams.microsoft.com/l/meetup-join/19%3ameeting_REDACTED%40thread.v2/0?context=%7b%7d';

/** Drive joinMicrosoftTeams on a fabricated page; report what it threw and the virtual cost. */
async function driveJoin(page: any, meetingUrl: string) {
  const t0 = vnow;
  startCapture();
  let error: any = null;
  try {
    await joinMicrosoftTeams(page, meetingUrl, 'Vexa Bot', BOT_CONFIG);
  } catch (e) { error = e; }
  const logs = stopCapture();
  return { error, elapsedMs: vnow - t0, logs };
}

let passed = 0, failed = 0;
function check(name: string, actual: boolean, expected: boolean) {
  if (actual === expected) { realConsoleLog(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { realConsoleLog(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${actual})`); failed++; }
}

/** The five steps the flow used to run against a sign-in page. */
const PANTOMIME = ['Step 3:', 'Step 4:', 'Step 5:', 'Step 6:', 'Step 6b:'];

(async () => {
  realConsoleLog('\n=== Teams anonymous join redirected to the Microsoft sign-in page (#915) ===');

  // ── 1. URL classification (the point of introduction) ──────────────────────
  check('prod OAuth authorize URL → isMicrosoftLoginUrl', isMicrosoftLoginUrl(LOGIN_URL), true);
  for (const u of [
    'https://login.microsoftonline.us/common/oauth2/v2.0/authorize',
    'https://login.live.com/oauth20_authorize.srf',
    'https://login.microsoft.com/common/',
    'https://login.windows.net/common/oauth2/authorize',
  ]) check(`sign-in host → isMicrosoftLoginUrl: ${new URL(u).hostname}`, isMicrosoftLoginUrl(u), true);

  for (const u of [MEETUP_JOIN_URL, 'https://teams.live.com/meet/REDACTED', 'https://teams.cloud.microsoft/v2/']) {
    check(`Teams host is NOT a sign-in host: ${new URL(u).hostname}`, isMicrosoftLoginUrl(u), false);
    check(`Teams host → isTeamsMeetingUrl: ${new URL(u).hostname}`, isTeamsMeetingUrl(u), true);
  }
  for (const u of [
    'https://login.microsoftonline.com.evil.example/oauth2',
    'https://login.microsoftonline.com@evil.example/oauth2',
    'https://evil-login.microsoftonline.com/oauth2',
  ]) {
    check(
      `lookalike host is NOT a Microsoft sign-in host: ${new URL(u).hostname}`,
      isMicrosoftLoginUrl(u),
      false,
    );
  }
  check('garbage url → isMicrosoftLoginUrl = false', isMicrosoftLoginUrl('not a url'), false);

  // The tenant/client ids and the meetup-join payload in redirect_uri are customer data; the
  // reported URL keeps origin+path and drops the query.
  check(
    'reported URL drops the query string',
    redactUrl(LOGIN_URL) === 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    true,
  );

  // ── 2. The reported failure: sign-in redirect present at first URL read ────
  {
    const { error, elapsedMs, logs } = await driveJoin(mockPage(LOGIN_URL), MEETUP_JOIN_URL);
    check(
      'sign-in redirect → throws TeamsJoinRedirectError',
      error instanceof TeamsJoinRedirectError, true,
    );
    check(
      `typed reason is "${TEAMS_AUTH_REDIRECT}"`,
      error?.reasonCode === TEAMS_AUTH_REDIRECT, true,
    );
    check(
      'the reason text is NOT an admission/pre-join timeout',
      /admission|pre-join readiness|timeout period/i.test(String(error?.message ?? '')), false,
    );
    const observedHost = (() => {
      try {
        return new URL(error instanceof TeamsJoinRedirectError ? error.observedUrl : '').hostname;
      } catch {
        return null;
      }
    })();
    check('the typed error records the exact sign-in host', observedHost === 'login.microsoftonline.com', true);
    check('the reason text carries no query string', String(error?.message ?? '').includes('client_id'), false);
    // The RED cost was 45 700ms here (plus 30 000ms of admission polling after it).
    check(`fails fast: ${elapsedMs}ms << 45000ms pre-join wait`, elapsedMs < 5000, true);
    check(
      'never logs the misleading "pre-join readiness" timeout',
      logs.some((l) => l.includes('pre-join readiness')), false,
    );
    for (const step of PANTOMIME) {
      check(`no pantomime against the sign-in page: ${step}`, logs.some((l) => l.startsWith(step)), false);
    }
  }

  // ── 3. The redirect lands MID-WAIT (client-side nav after the pre-join wait began) ──
  {
    // Teams host for the first two reads, then bounced to sign-in.
    const page = mockPage([MEETUP_JOIN_URL, MEETUP_JOIN_URL, LOGIN_URL]);
    const { error, elapsedMs } = await driveJoin(page, MEETUP_JOIN_URL);
    check(
      'redirect during the pre-join wait → still typed teams_auth_redirect',
      error instanceof TeamsJoinRedirectError && error.reasonCode === TEAMS_AUTH_REDIRECT, true,
    );
    check(`mid-wait redirect fails fast too: ${elapsedMs}ms`, elapsedMs < 5000, true);
  }

  // ── 4. Negative control: a real anonymous pre-join screen still joins ──────
  {
    const page = mockPage(MEETUP_JOIN_URL, ['Join now']);
    const { error, logs } = await driveJoin(page, MEETUP_JOIN_URL);
    check('healthy pre-join → no throw', error === null, true);
    check('healthy pre-join → readiness reached', logs.some((l) => l.includes('pre-join controls are ready')), true);
    check('healthy pre-join → the flow still runs its steps', logs.some((l) => l.startsWith('Step 6:')), true);
  }

  // ── 5. Negative control: a SLOW pre-join on the meeting host stays advisory ──
  //     (no over-correction — a Teams page that is merely late is not a redirect)
  {
    const page = mockPage(MEETUP_JOIN_URL);
    const { error, logs, elapsedMs } = await driveJoin(page, MEETUP_JOIN_URL);
    check('slow pre-join ON the Teams host → no throw', error === null, true);
    check(
      'slow pre-join → the advisory readiness timeout still logs',
      logs.some((l) => l.includes('pre-join readiness')), true,
    );
    check(`slow pre-join → the full wait is still spent: ${elapsedMs}ms`, elapsedMs > 45000, true);
  }

  // ── 6. Any other non-meeting origin is terminal too, and says which ────────
  {
    const page = mockPage('https://www.microsoft.com/en-us/microsoft-teams/download-app');
    const { error } = await driveJoin(page, MEETUP_JOIN_URL);
    check(
      'non-Teams, non-sign-in origin → teams_off_meeting_origin',
      error instanceof TeamsJoinRedirectError && error.reasonCode === TEAMS_OFF_MEETING_ORIGIN, true,
    );
  }

  // ── 7. An embedder-supplied host we were POINTED at stays advisory ─────────
  //     (a vanity/white-label Teams entry point is not a redirect — we are where we asked to be)
  {
    const vanity = 'https://meet.contoso.example/join/REDACTED';
    const page = mockPage(vanity);
    const { error } = await driveJoin(page, vanity);
    check('still on the requested host → no throw', error === null, true);
  }

  (Date as any).now = realDateNow;
  realConsoleLog(`\n  ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
})();
