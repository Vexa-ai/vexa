/**
 * Replication of the Google Meet admission "denial conflation" failure mode.
 *
 * PROD SYMPTOM: a large share of Google Meet bots fail with
 * `awaiting_admission_rejected` (AdmissionError "denial"). But that bucket is
 * ambiguous — `checkForGoogleRejection` keys on `googleRejectionIndicators`,
 * which are BROAD: "Return to home screen", "Try again", "Retry", "Go back",
 * "can't join this call", "Unable to join", "Access denied", … Those same
 * affordances render on Google's *generic error / bot-block* pages, NOT only on
 * a host denial. So a Google-side BLOCK or join-error (the unhandled #444) is
 * thrown as "denial" and reported as a host rejection — indistinguishable.
 *
 * This test feeds the detector a fabricated DOM for each scenario (no browser,
 * no live meeting, no Google) and shows the conflation: a Google ERROR/BLOCK
 * page (no host-denial text, no reCAPTCHA) is classified as a rejection.
 *
 * Run: npx tsx src/googlemeet/admission.test.ts
 * When the fix lands (a block/error-vs-denial distinction, à la the Zoom
 * zoom_requires_rtms detector), the CONFLATION case below flips to expecting a
 * NON-denial (blocked/unknown) outcome — this file is its regression guard.
 */

import { checkForGoogleRejection } from './admission';

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true on this page; `recaptcha` = whether a /recaptcha/ frame is
 * present. Matches exactly the surface checkForGoogleRejection + hasRecaptchaChallenge use.
 */
function mockPage(visible: string[], recaptcha = false): any {
  return {
    locator: (sel: string) => ({ first: () => ({ isVisible: async () => visible.includes(sel) }) }),
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
  console.log('\n=== Google Meet rejection detector — denial conflation repro ===');

  // 1. THE BUG — Google "couldn't join" ERROR/BLOCK page (not a host denial, no reCAPTCHA).
  //    The page shows a "Return to home screen" + "Try again" affordance, exactly like
  //    Google's bot-block / invalid-state pages. Current code → classified as rejection.
  await check(
    'CONFLATION: Google error/block page ("Return to home screen"/"Try again") → reported as DENIAL (the bug)',
    await checkForGoogleRejection(mockPage(['button:has-text("Return to home screen")', 'button:has-text("Try again")'])),
    true, // current buggy behavior — a non-host-rejection is thrown as "denial" → awaiting_admission_rejected
  );

  // 2. CONTRAST — a genuine host denial. SHOULD be a rejection (correct).
  await check(
    'genuine host denial ("denied your request") → rejection (correct)',
    await checkForGoogleRejection(mockPage(['text*="denied your request"'])),
    true,
  );

  // 3. GUARD — reCAPTCHA present alongside the "Return to home screen" affordance:
  //    treated as bot-detection, NOT a denial (the one case the code DOES handle).
  await check(
    'reCAPTCHA + "Return to home screen" → NOT a denial (bot-detection guard works)',
    await checkForGoogleRejection(mockPage(['button:has-text("Return to home screen")'], /*recaptcha*/ true)),
    false,
  );

  // 4. CLEAN lobby — no rejection text at all → not a rejection (correct).
  await check(
    'clean waiting-room (no rejection text) → not a rejection (correct)',
    await checkForGoogleRejection(mockPage([])),
    false,
  );

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  console.log('  ► Case 1 passing = the conflation is REAL: a Google error/block page is\n' +
              '    bucketed as awaiting_admission_rejected, hiding #444 blocks under "host rejected".');
  process.exit(failed > 0 ? 1 : 0);
})();
