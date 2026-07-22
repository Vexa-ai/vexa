/**
 * Auth-session guard — pinned in the ACTUAL join path (#756) and locale-proof
 * (#757).
 *
 * joinGoogleMeeting's authenticated branch must:
 *   1. throw the typed AuthSessionError (outcome "auth_session_missing") when
 *      the lobby is a signed-out GUEST lobby (name input rendered) — never
 *      silently downgrade to an anonymous join;
 *   2. proceed (click the CTA / knock) when the account is signed in but not
 *      pre-admitted (no name input);
 *   3. do both on a NON-ENGLISH lobby, where every English-literal selector
 *      misses and only the structural (jsname/attribute) selectors match —
 *      the selectors.ts prod-failure class (ids 13951/13952/14018/14153).
 *
 * The test drives joinGoogleMeeting itself against a fabricated Playwright-Page
 * stand-in (no browser, no live meeting, no Google) — so deleting the guard
 * call inside the join path turns this suite RED offline, which no other suite
 * did (#756).
 *
 * Run: npx tsx src/googlemeet/session.test.ts
 */

import { joinGoogleMeeting, isGoogleSignedOutLobby, AuthSessionError } from './join';
import { googleAuthJoinCtaSelectors, googleSignedOutLobbyProbeSelectors } from './selectors';

// Structural (locale-agnostic) vs English-literal selector split, derived from
// the exported arrays so the fixtures track the shipped selectors.
const isEnglishLiteral = (sel: string) => /has-text|text\(\)=|aria-label="Your name"/.test(sel);
const CTA_STRUCTURAL = googleAuthJoinCtaSelectors.filter(s => !isEnglishLiteral(s));
const CTA_ENGLISH = googleAuthJoinCtaSelectors.filter(isEnglishLiteral);
const PROBE_STRUCTURAL = googleSignedOutLobbyProbeSelectors.filter(s => !isEnglishLiteral(s));
const PROBE_ENGLISH = googleSignedOutLobbyProbeSelectors.filter(isEnglishLiteral);

/**
 * Fabricated Playwright-Page stand-in covering exactly the surface
 * joinGoogleMeeting's authenticated branch touches. `visible` = the selectors
 * that resolve on this page; every click on a resolved handle is recorded in
 * `clicks` (keyed by the selector that produced the handle).
 */
function mockPage(visible: string[]) {
  const clicks: string[] = [];
  const handle = (sel: string) => ({
    click: async () => { clicks.push(sel); },
    isVisible: async () => true,
  });
  const page: any = {
    clicks,
    goto: async () => {},
    bringToFront: async () => {},
    screenshot: async () => {},
    waitForTimeout: async () => {},
    fill: async () => {},
    mouse: { move: async () => {} },
    url: () => 'https://meet.google.com/abc-defg-hij',
    // The lobby-CTA resolvers read the observed locale for the failure
    // diagnostic and run the structural scan through evaluateHandle; neither
    // resolves anything on this fixture, which is driven purely by `visible`.
    evaluate: async () => ({ lang: 'en', nav: 'en-US' }),
    evaluateHandle: async () => ({
      getProperty: async (k: string) => ({
        jsonValue: async () => (k === 'labels' ? [] : null),
        asElement: () => null,
      }),
      dispose: async () => {},
    }),
    waitForSelector: (sel: string, _opts?: any) =>
      visible.includes(sel)
        ? Promise.resolve(handle(sel))
        : Promise.reject(new Error(`mock: ${sel} not on page`)),
    $: async (sel: string) => (visible.includes(sel) ? handle(sel) : null),
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => visible.includes(sel),
        elementHandle: async () => (visible.includes(sel) ? handle(sel) : null),
      }),
    }),
  };
  return page;
}

const authConfig = {
  platform: 'google_meet',
  authenticated: true,
  uiInteractionMode: 'synthetic', // no XTEST layer in the fabricated page
} as any;

const join = (page: any) =>
  joinGoogleMeeting(page, 'https://meet.google.com/abc-defg-hij', 'Vexa Bot', authConfig);

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail = '') {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}

(async () => {
  console.log('\n=== #756 — auth guard pinned in joinGoogleMeeting itself ===');

  // 1. Signed-out ENGLISH guest lobby → typed refusal, no click.
  {
    const page = mockPage([...CTA_ENGLISH, ...PROBE_ENGLISH]);
    const err = await join(page).then(() => null, (e: unknown) => e);
    check(
      'signed-out English lobby → AuthSessionError("auth_session_missing")',
      err instanceof AuthSessionError && err.outcome === 'auth_session_missing',
      `got ${err ? (err as Error).name + ': ' + (err as Error).message : 'RESOLVED (silent anonymous downgrade)'}`,
    );
    check('…and the join CTA was NOT clicked', page.clicks.length === 0, `clicks: ${page.clicks}`);
  }

  // 2. Signed in but not pre-admitted (no name input) → knock proceeds.
  {
    const page = mockPage([...CTA_ENGLISH]);
    const err = await join(page).then(() => null, (e: unknown) => e);
    check('signed-in, not pre-admitted → join proceeds (no error)', err === null, String(err));
    check('…and the CTA was clicked exactly once', page.clicks.length === 1, `clicks: ${page.clicks}`);
  }

  console.log('\n=== #757 — detection is structural: non-English lobby fixtures ===');
  console.log(`  (structural CTA selectors: ${CTA_STRUCTURAL.length}, structural probe selectors: ${PROBE_STRUCTURAL.length})`);
  check('the CTA array leads with structural selectors', CTA_STRUCTURAL.length > 0 && !isEnglishLiteral(googleAuthJoinCtaSelectors[0]));
  check('the probe array leads with structural selectors', PROBE_STRUCTURAL.length > 0 && !isEnglishLiteral(googleSignedOutLobbyProbeSelectors[0]));

  // 3. Signed-out NON-ENGLISH lobby: every English-literal selector misses;
  //    only structural selectors resolve. The guard must still fail CLOSED.
  {
    const page = mockPage([...CTA_STRUCTURAL, ...PROBE_STRUCTURAL]);
    const err = await join(page).then(() => null, (e: unknown) => e);
    check(
      'signed-out non-English lobby → still AuthSessionError (no fail-open)',
      err instanceof AuthSessionError && err.outcome === 'auth_session_missing',
      `got ${err ? (err as Error).name + ': ' + (err as Error).message : 'RESOLVED (fail-open: silent anonymous downgrade)'}`,
    );
    check('…and the join CTA was NOT clicked', page.clicks.length === 0, `clicks: ${page.clicks}`);
  }

  // 4. Signed-in non-English lobby, not pre-admitted → knock proceeds.
  {
    const page = mockPage([...CTA_STRUCTURAL]);
    const err = await join(page).then(() => null, (e: unknown) => e);
    check('signed-in non-English lobby → join proceeds', err === null, String(err));
    check('…and the CTA was clicked exactly once', page.clicks.length === 1, `clicks: ${page.clicks}`);
  }

  console.log('\n=== isGoogleSignedOutLobby unit rows ===');
  check('structural name input alone → signed-out', await isGoogleSignedOutLobby(mockPage(PROBE_STRUCTURAL)));
  check('English name input alone → signed-out', await isGoogleSignedOutLobby(mockPage(PROBE_ENGLISH)));
  check('no name input → NOT signed-out', !(await isGoogleSignedOutLobby(mockPage(CTA_STRUCTURAL))));

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
})();
