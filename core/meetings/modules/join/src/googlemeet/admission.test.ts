/**
 * Regression guard for the Google Meet admission detector.
 *
 * 1. #471 false-reject (FIXED): the "Asking to be let in" waiting screen shows
 *    a "Return to home screen" button. It used to be a googleRejectionIndicator,
 *    so `checkForGoogleRejection` classified a normal waiting screen as a host
 *    denial in ~4s (`awaiting_admission_rejected`) — the bot never waited for
 *    the host. Ported from Vexa-ai/vexa#471 (@priitvimberg): the button is now
 *    a WAITING indicator; genuine denials are still caught by the
 *    "denied your request" text patterns.
 *
 * 2. #444 conflation (STILL OPEN, narrowed by #471): googleRejectionIndicators
 *    keeps generic error-page affordances ("Try again", "Retry", "Go back",
 *    "Access denied", …) that also render on Google's bot-block / invalid-state
 *    pages, so a Google-side BLOCK is still thrown as "denial". The CONFLATION
 *    case below documents that remaining bug; when a block/error-vs-denial
 *    distinction lands (à la the Zoom zoom_requires_rtms detector), flip it.
 *
 * 3. #429 Gemini consent gate (Vexa-ai/vexa#454, @thatditsyboy): the
 *    "take notes for me" consent prompt is a pre-admission gate — meeting
 *    controls are visible behind it, so the bot false-reported ACTIVE with 0
 *    transcriptions. `hasConsentPrompt` detects it and
 *    `checkForGoogleAdmissionIndicators` suppresses the admitted signal.
 *
 * 4. #840 denial-vs-captcha (FIXED): Google Meet loads reCAPTCHA Enterprise
 *    INVISIBLY on every join (a background scoring frame), so a `/recaptcha/`
 *    frame sits on the HOST DENIAL screen too. The old detector read frame-URL
 *    presence as "a captcha is on screen" and suppressed the denial forever —
 *    prod meeting 24348 looped `"reCAPTCHA present alongside rejection
 *    indicator … staying for solve"` every 2s and never left `awaiting_admission`.
 *    Now: explicit host-denial copy wins over any captcha, only a VISIBLE
 *    challenge-sized widget counts as a challenge, and the stay-for-solve wager
 *    expires (CAPTCHA_SOLVE_GRACE_MS).
 *
 * This test feeds the detectors a fabricated DOM for each scenario (no browser,
 * no live meeting, no Google).
 *
 * Run: npx tsx src/googlemeet/admission.test.ts
 */

// Namespace import on purpose: this file must also RUN against the pre-#840
// module (which has no CAPTCHA_SOLVE_GRACE_MS) to demonstrate red→green.
import * as admission from './admission';
import { AdmissionError } from '../shared/admission';
import { resetEscalation } from '../shared/escalation';
const {
  checkForGoogleRejection,
  checkForWaitingRoomIndicators,
  checkForGoogleAdmissionIndicators,
  hasConsentPrompt,
  waitForGoogleMeetingAdmission,
} = admission;
const GRACE_MS: number = (admission as any).CAPTCHA_SOLVE_GRACE_MS ?? 120_000;

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true on this page; `captcha` = the reCAPTCHA shape on the page;
 * `participantLabels` = aria-labels returned for [data-participant-id] tiles
 * (drives countRealParticipantTiles). Matches exactly the surface the admission
 * detectors use.
 *
 *   false        — no reCAPTCHA anywhere.
 *   'invisible'  — reCAPTCHA Enterprise's background scoring frame: a
 *                  `/recaptcha/` FRAME exists, its iframe element is
 *                  display:none (isVisible false, no box). This is what sits on
 *                  a real Google Meet page — including the denial screen (#840).
 *   'live'       — an actual challenge: a visible, challenge-sized iframe
 *                  (bframe, 400x580) a human/agent can solve.
 */
type CaptchaMode = false | 'invisible' | 'live';
const RECAPTCHA_IFRAME = 'iframe[src*="recaptcha"]';

function mockPage(visible: string[], captcha: CaptchaMode = false, participantLabels: string[] = []): any {
  const captchaIframes: { visible: boolean; box: { x: number; y: number; width: number; height: number } | null }[] =
    captcha === 'live' ? [{ visible: true, box: { x: 300, y: 120, width: 400, height: 580 } }]
      : captcha === 'invisible' ? [{ visible: false, box: null }]
        : [];
  const iframeEl = (i: number) => ({
    isVisible: async () => !!captchaIframes[i]?.visible,
    boundingBox: async () => captchaIframes[i]?.box ?? null,
    getAttribute: async () => null,
  });
  return {
    locator: (sel: string) => sel === RECAPTCHA_IFRAME
      ? {
        first: () => iframeEl(0),
        nth: (i: number) => iframeEl(i),
        count: async () => captchaIframes.length,
        evaluateAll: async () => [],
      }
      : {
        first: () => ({
          isVisible: async () => visible.includes(sel),
          getAttribute: async () => null,
        }),
        count: async () => (visible.includes(sel) ? 1 : 0),
        evaluateAll: async () => participantLabels,
      },
    mouse: { move: async () => {} },
    url: () => 'https://meet.google.com/abc-defg-hij',
    screenshot: async () => {},
    waitForTimeout: async (_ms: number) => {},
    frames: () => (captcha
      ? [{ url: () => 'https://meet.google.com/' }, { url: () => 'https://www.google.com/recaptcha/enterprise/anchor?ar=1' }]
      : [{ url: () => 'https://meet.google.com/' }]),
  };
}

let passed = 0, failed = 0;
async function check(name: string, actual: boolean, expected: boolean, detail?: string) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${detail ?? actual})`); failed++; }
}

/**
 * Outcome of a shipped admission wait, with a WATCHDOG: if no verdict lands within
 * `watchdogMs` the wait is still polling — which is exactly the #840 failure mode
 * (`awaiting_admission` forever), so it must read as a distinct, failing outcome.
 */
async function outcomeOf(run: Promise<unknown>, watchdogMs = 10_000): Promise<string> {
  const watchdog = new Promise<string>(resolve => {
    const t = setTimeout(() => resolve('NO-VERDICT (still polling after watchdog)'), watchdogMs);
    (t as any).unref?.();
  });
  return Promise.race([
    run.then(
      () => 'resolved',
      (e: any) => (e instanceof AdmissionError ? `AdmissionError:${e.outcome}` : `Error:${e.message}`),
    ),
    watchdog,
  ]);
}

(async () => {
  console.log('\n=== Google Meet rejection detector — #471 fix + remaining #444 conflation ===');

  // 1. #471 FIXED — the waiting screen's "Return to home screen" button alone is
  //    NOT a denial anymore. Before the fix this false-rejected in ~4s.
  await check(
    '#471 waiting screen ("Return to home screen", no denial text) → NOT a denial (fixed)',
    await checkForGoogleRejection(mockPage(['button:has-text("Return to home screen")'])),
    false,
  );

  // 1b. #471 — the button now counts as a WAITING indicator, so the polling loop
  //     keeps treating the screen as a lobby instead of an unknown state.
  await check(
    '#471 "Return to home screen" → recognized as waiting-room indicator',
    await checkForWaitingRoomIndicators(mockPage(['button:has-text("Return to home screen")'])),
    true,
  );

  // 2. REMAINING #444 CONFLATION — a Google ERROR/BLOCK page's "Try again"
  //    affordance (no host-denial text, no live challenge) is still classified as
  //    a denial. #471 narrowed the conflation but did not close it; flip this to
  //    `false` when a block/error-vs-denial distinction lands.
  await check(
    'CONFLATION (#444, still open): Google error/block page ("Try again") → reported as DENIAL (the remaining bug)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'])),
    true, // current buggy behavior — a non-host-rejection is thrown as "denial" → awaiting_admission_rejected
  );

  // 3. CONTRAST — a genuine host denial. SHOULD be a rejection (correct).
  await check(
    'genuine host denial ("denied your request") → rejection (correct)',
    await checkForGoogleRejection(mockPage(['text=denied your request'])),
    true,
  );

  // 4. GUARD — a LIVE reCAPTCHA challenge alongside an AMBIGUOUS error affordance:
  //    treated as bot-detection, NOT a denial (keeps the bot on the page for a
  //    human solve). NEGATIVE CONTROL (a) for #840 — must never regress.
  await check(
    'NEG-CTRL (a): live reCAPTCHA + "Try again", no denial copy → NOT a denial (stay-for-solve preserved)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'], 'live')),
    false,
  );

  // 5. CLEAN lobby — no rejection text at all → not a rejection (correct).
  await check(
    'clean waiting-room (no rejection text) → not a rejection (correct)',
    await checkForGoogleRejection(mockPage([])),
    false,
  );

  console.log('\n=== #840 — host denial vs. reCAPTCHA (prod meeting 24348) ===');

  // 6. THE #840 BUG — the exact prod page: host denial copy ("denied your
  //    request") on a page that ALSO carries Meet's invisible reCAPTCHA
  //    Enterprise scoring frame. Pre-fix this returned false forever (the 2s
  //    "staying for solve" loop, meeting stuck awaiting_admission).
  await check(
    '#840 host denial + invisible reCAPTCHA scoring frame → REJECTED (explicit denial wins)',
    await checkForGoogleRejection(mockPage(['text=denied your request'], 'invisible')),
    true,
  );

  // 6b. Same rule against a LIVE challenge: solving a captcha cannot undo a host's
  //     "no", so explicit denial copy still wins.
  await check(
    '#840 host denial + LIVE reCAPTCHA challenge → REJECTED (explicit denial still wins)',
    await checkForGoogleRejection(mockPage(['text=denied your request'], 'live')),
    true,
  );

  // 6c. Other explicit denial copy, same page shape.
  await check(
    '#840 "weren’t allowed to join" + invisible reCAPTCHA → REJECTED',
    await checkForGoogleRejection(mockPage(['text=weren’t allowed to join'], 'invisible')),
    true,
  );

  // 6c-bis. Order-independence: the denial screen also carries the generic "Try again"
  //     affordance. The explicit-denial pass runs first, so the verdict does not depend
  //     on where each selector sits in googleRejectionIndicators.
  await check(
    '#840 denial copy + "Try again" + live challenge → REJECTED (explicit pass runs first)',
    await checkForGoogleRejection(
      mockPage(['text=denied your request', 'button:has-text("Try again")'], 'live'),
    ),
    true,
  );

  // 6d. NEGATIVE CONTROL (b) — a plain denial with no captcha at all still rejects.
  await check(
    'NEG-CTRL (b): host denial, no reCAPTCHA anywhere → REJECTED',
    await checkForGoogleRejection(mockPage(['text=denied your request'])),
    true,
  );

  // 7. The detector no longer reads Meet's INVISIBLE scoring frame as a challenge:
  //    an ambiguous affordance next to it is not a solvable captcha, so the bot
  //    concludes instead of waiting for a solve that can never happen.
  await check(
    '#840 invisible scoring frame is NOT a challenge ("Try again" concludes, no stay-for-solve)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'], 'invisible')),
    true,
  );

  console.log('\n=== #840 — the stay-for-solve wager is BOUNDED ===');

  // 8. A live challenge suppresses the ambiguous indicator only within the grace
  //    window; past it the bot concludes terminal instead of polling forever.
  const boundedPage = mockPage(['button:has-text("Try again")'], 'live');
  const t0 = 1_000_000;
  await check(
    `bound: t+0s — live challenge suppresses (stay for solve)`,
    await checkForGoogleRejection(boundedPage, t0),
    false,
  );
  await check(
    `bound: t+${Math.round(GRACE_MS / 2000)}s (inside grace) — still staying for solve`,
    await checkForGoogleRejection(boundedPage, t0 + GRACE_MS / 2),
    false,
  );
  await check(
    `bound: t+${Math.round(GRACE_MS / 1000) + 1}s (past grace) — concludes TERMINAL, never loops forever`,
    await checkForGoogleRejection(boundedPage, t0 + GRACE_MS + 1_000),
    true,
  );

  // 8b. The clock is per-page and restarts after the page stops showing the
  //     indicator — a later, independent challenge gets its full grace.
  const freshPage = mockPage(['button:has-text("Try again")'], 'live');
  await check(
    'bound: a different page starts its own grace window (no shared clock)',
    await checkForGoogleRejection(freshPage, t0 + GRACE_MS * 10),
    false,
  );

  console.log('\n=== #840 — the SHIPPED wait concludes TERMINAL on the prod page shape ===');

  // 9. Altitude of the claim: not just the detector, the shipped
  //    waitForGoogleMeetingAdmission. Meeting 24348's page — stale lobby text +
  //    "denied your request" + Meet's invisible reCAPTCHA frame — must throw the TYPED
  //    AdmissionError("denial"), which the JoinDriver maps to `rejected` →
  //    `awaiting_admission_rejected` (PERMANENT, no re-knock). Pre-fix it never threw:
  //    the poll looped to the 10-minute timeout, so the meeting sat `awaiting_admission`.
  //    The watchdog below is the "loops forever" detector — a verdict must arrive.
  {
    resetEscalation();
    const deniedPage = mockPage(
      ['text=Asking to be let in', 'text=denied your request'],
      'invisible',
    );
    const got = await outcomeOf(waitForGoogleMeetingAdmission(deniedPage, 20_000, {} as any));
    check(
      "#840 shipped waitForGoogleMeetingAdmission(denial + invisible captcha) → AdmissionError('denial')",
      got === "AdmissionError:denial",
      true,
      got,
    );
  }

  console.log('\n=== Gemini "take notes" consent gate (#454 / issue #429) ===');

  // 9. Detector fires on the consent prompt copy.
  await check(
    'consent prompt visible → hasConsentPrompt = true',
    await hasConsentPrompt(mockPage(['text=take notes for me'])),
    true,
  );
  await check(
    'no consent prompt → hasConsentPrompt = false',
    await hasConsentPrompt(mockPage([])),
    false,
  );

  // 10. THE #429 BUG — meeting controls (real participant tiles) visible BEHIND
  //    the consent dialog must NOT read as admitted; the bot is not truly in the
  //    call until a human accepts/declines.
  await check(
    'consent prompt + participant tiles → admission SUPPRESSED (no false ACTIVE)',
    await checkForGoogleAdmissionIndicators(
      mockPage(['text=take notes for me'], false, ['John Doe']),
    ),
    false,
  );

  // 11. CONTROL — same participant tiles without the consent prompt → admitted.
  await check(
    'participant tiles, no consent prompt → admitted (control)',
    await checkForGoogleAdmissionIndicators(mockPage([], false, ['John Doe'])),
    true,
  );

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
