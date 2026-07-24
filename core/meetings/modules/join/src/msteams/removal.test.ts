/**
 * Regression guard for the Teams removal-monitor false-positive
 * (Vexa-ai/vexa#600).
 *
 * A legitimately-joined-and-admitted Teams bot was evicted ~1.5s after
 * admission because checkForTeamsRemoval() returned true on the FIRST visible
 * `teamsRemovalIndicators` selector, and that list contained generic
 * role/class patterns — `[role="alert"]`, `[role="alertdialog"]`,
 * `.error-message`, `.connection-error`, `.meeting-error`. `[role="alert"]`
 * matches ANY Teams alert region (mute toasts, captions, network blips, the
 * post-join AV-confirmation modal), none of which mean the bot was removed.
 * A benign transient alert therefore tripped a false removal → self-leave →
 * `completed(evicted)`, no transcript.
 *
 * Fix (at the point of introduction, selectors.ts): the removal indicators are
 * now the removal/"meeting ended" TEXT signals only — no generic role/class
 * catch-alls.
 *
 * Fabricated-DOM test in the same style as msteams/modals.test.ts — no
 * browser, no live meeting.
 *
 * Run: npx tsx src/msteams/removal.test.ts
 */

import { checkForTeamsRemoval } from './removal';
import { teamsRemovalIndicators } from './selectors';

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true. checkForTeamsRemoval iterates teamsRemovalIndicators and
 * calls page.locator(sel).first().isVisible() on each.
 */
function mockPage(visible: string[]): any {
  return {
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => visible.includes(sel),
      }),
    }),
  };
}

let passed = 0, failed = 0;
function check(name: string, actual: boolean, expected: boolean) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${actual})`); failed++; }
}

// Generic patterns that must NOT be treated as removal signals.
const GENERIC_ALERTS = [
  '[role="alert"]',
  '[role="alertdialog"]',
  '.error-message',
  '.connection-error',
  '.meeting-error',
];

(async () => {
  console.log('\n=== Teams removal-monitor false-positive (#600) ===');

  // 1. Benign transient alert region on screen (the reported failure: a
  //    `[role="alert"]` toast / AV-confirm modal ~1.5s after admission) must
  //    NOT be classified as a removal.
  check(
    'benign [role="alert"] visible → checkForTeamsRemoval = false',
    await checkForTeamsRemoval(mockPage(['[role="alert"]'])),
    false,
  );

  // 2. None of the generic role/class catch-alls may live in the indicator
  //    list at all — this is the point-of-introduction guard.
  for (const g of GENERIC_ALERTS) {
    check(
      `generic selector NOT in teamsRemovalIndicators: ${g}`,
      teamsRemovalIndicators.includes(g),
      false,
    );
  }

  // 3. A real removal message still trips detection (no over-correction).
  check(
    'real "removed from this meeting" text → checkForTeamsRemoval = true',
    await checkForTeamsRemoval(mockPage(['text=You\'ve been removed from this meeting'])),
    true,
  );
  check(
    'real "Meeting ended" text → checkForTeamsRemoval = true',
    await checkForTeamsRemoval(mockPage(['text=Meeting ended'])),
    true,
  );

  // 4. Nothing visible → not removed.
  check(
    'nothing visible → checkForTeamsRemoval = false',
    await checkForTeamsRemoval(mockPage([])),
    false,
  );

  console.log(`\n  ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
})();
