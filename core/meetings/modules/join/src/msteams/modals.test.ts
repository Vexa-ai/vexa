/**
 * Regression guard for the Teams "Continue without audio or video" confirm
 * modal handling (Vexa-ai/vexa#467, @lucasantoro97).
 *
 * In the anonymous "light meeting" flow, Teams pops "Are you sure you don't
 * want audio or video?" AFTER the Join-now click. Undismissed it blocks the
 * join, and the pre-join "Join now" button it pins in the DOM is counted as a
 * waiting-room indicator by admission.ts — so the bot looped
 * "Still in Teams waiting room…" forever. modals.ts is the single dismiss
 * point, called post-Join-now (join.ts) and inside the admission wait loops.
 *
 * Fabricated-DOM test in the same style as googlemeet/admission.test.ts — no
 * browser, no live meeting.
 *
 * Run: npx tsx src/msteams/modals.test.ts
 */

import { dismissTeamsAvConfirmModal, isTeamsAvConfirmModalVisible } from './modals';
import { teamsContinueWithoutMediaSelectors } from './selectors';

// modals.ts locates the modal via the JOINED selector list — mirror that key.
const MODAL_SELECTOR = teamsContinueWithoutMediaSelectors.join(', ');

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true; clicks on visible selectors are counted in `clicks`.
 */
function mockPage(visible: string[]): any {
  const clicks: string[] = [];
  return {
    clicks,
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => visible.includes(sel),
        click: async () => { clicks.push(sel); },
      }),
    }),
    waitForTimeout: async () => {},
  };
}

let passed = 0, failed = 0;
function check(name: string, actual: boolean, expected: boolean) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${actual})`); failed++; }
}

(async () => {
  console.log('\n=== Teams AV-confirm modal handling (#467) ===');

  // 1. Modal on screen → detected.
  check(
    'modal visible → isTeamsAvConfirmModalVisible = true',
    await isTeamsAvConfirmModalVisible(mockPage([MODAL_SELECTOR])),
    true,
  );

  // 2. No modal → not detected.
  check(
    'no modal → isTeamsAvConfirmModalVisible = false',
    await isTeamsAvConfirmModalVisible(mockPage([])),
    false,
  );

  // 3. Modal on screen → dismissed (click issued, returns true).
  const pageWithModal = mockPage([MODAL_SELECTOR]);
  const dismissed = await dismissTeamsAvConfirmModal(pageWithModal);
  check('modal visible → dismissTeamsAvConfirmModal returns true', dismissed, true);
  check(
    'modal visible → dismiss click was issued on the modal button',
    pageWithModal.clicks.includes(MODAL_SELECTOR),
    true,
  );

  // 4. No modal → nothing clicked, returns false (safe to call in every loop
  //    iteration of the admission wait).
  const pageWithoutModal = mockPage([]);
  check('no modal → dismissTeamsAvConfirmModal returns false', await dismissTeamsAvConfirmModal(pageWithoutModal), false);
  check('no modal → no click issued', pageWithoutModal.clicks.length === 0, true);

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
