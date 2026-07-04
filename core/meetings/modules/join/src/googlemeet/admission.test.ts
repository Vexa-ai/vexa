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
 * This test feeds the detectors a fabricated DOM for each scenario (no browser,
 * no live meeting, no Google).
 *
 * Run: npx tsx src/googlemeet/admission.test.ts
 */

import {
  checkForGoogleRejection,
  checkForWaitingRoomIndicators,
  checkForGoogleAdmissionIndicators,
  hasConsentPrompt,
} from './admission';

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true on this page; `recaptcha` = whether a /recaptcha/ frame is
 * present; `participantLabels` = aria-labels returned for [data-participant-id]
 * tiles (drives countRealParticipantTiles). Matches exactly the surface the
 * admission detectors use.
 */
function mockPage(visible: string[], recaptcha = false, participantLabels: string[] = []): any {
  return {
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => visible.includes(sel),
        getAttribute: async () => null,
      }),
      count: async () => (visible.includes(sel) ? 1 : 0),
      evaluateAll: async () => participantLabels,
    }),
    mouse: { move: async () => {} },
    frames: () => (recaptcha
      ? [{ url: () => 'https://www.google.com/recaptcha/enterprise/anchor?ar=1' }]
      : [{ url: () => 'https://meet.google.com/' }]),
  };
}

let passed = 0, failed = 0;
async function check(name: string, actual: boolean, expected: boolean) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${actual})`); failed++; }
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
  //    affordance (no host-denial text, no reCAPTCHA) is still classified as a
  //    denial. #471 narrowed the conflation but did not close it; flip this to
  //    `false` when a block/error-vs-denial distinction lands.
  await check(
    'CONFLATION (#444, still open): Google error/block page ("Try again") → reported as DENIAL (the remaining bug)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'])),
    true, // current buggy behavior — a non-host-rejection is thrown as "denial" → awaiting_admission_rejected
  );

  // 3. CONTRAST — a genuine host denial. SHOULD be a rejection (correct).
  await check(
    'genuine host denial ("denied your request") → rejection (correct)',
    await checkForGoogleRejection(mockPage(['text*="denied your request"'])),
    true,
  );

  // 4. GUARD — reCAPTCHA present alongside an error affordance: treated as
  //    bot-detection, NOT a denial (keeps the bot on the page for a human solve).
  await check(
    'reCAPTCHA + "Try again" → NOT a denial (bot-detection guard works)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'], /*recaptcha*/ true)),
    false,
  );

  // 5. CLEAN lobby — no rejection text at all → not a rejection (correct).
  await check(
    'clean waiting-room (no rejection text) → not a rejection (correct)',
    await checkForGoogleRejection(mockPage([])),
    false,
  );

  console.log('\n=== Gemini "take notes" consent gate (#454 / issue #429) ===');

  // 6. Detector fires on the consent prompt copy.
  await check(
    'consent prompt visible → hasConsentPrompt = true',
    await hasConsentPrompt(mockPage(['text*="take notes for me"'])),
    true,
  );
  await check(
    'no consent prompt → hasConsentPrompt = false',
    await hasConsentPrompt(mockPage([])),
    false,
  );

  // 7. THE #429 BUG — meeting controls (real participant tiles) visible BEHIND
  //    the consent dialog must NOT read as admitted; the bot is not truly in the
  //    call until a human accepts/declines.
  await check(
    'consent prompt + participant tiles → admission SUPPRESSED (no false ACTIVE)',
    await checkForGoogleAdmissionIndicators(
      mockPage(['text*="take notes for me"'], false, ['John Doe']),
    ),
    false,
  );

  // 8. CONTROL — same participant tiles without the consent prompt → admitted.
  await check(
    'participant tiles, no consent prompt → admitted (control)',
    await checkForGoogleAdmissionIndicators(mockPage([], false, ['John Doe'])),
    true,
  );

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
