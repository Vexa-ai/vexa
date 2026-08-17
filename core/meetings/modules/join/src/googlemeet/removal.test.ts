/**
 * Regression guard for Google Meet HOST-REMOVAL detection
 * (Vexa-ai/vexa #1180-class: a kicked bot was never detected).
 *
 * Symptom (witnessed on the TRICOLORS capture shard, v0.12 line): a bot whose
 * host removed it from a Google Meet stayed in the meeting row as `active`
 * forever — the removal monitor never fired, no `completed(evicted)` was
 * emitted, and the recording was never finalized for the notetaker. Root
 * cause: `googleRemovalIndicators` contained only self-leave / connection /
 * generic-role entries — the live Meet host-removal copy ("You were removed
 * from the meeting", "Removed from meeting", "You can't rejoin this meeting")
 * was absent, so `checkForGoogleRemoval()` never returned true and the
 * 1.5s monitor idled.
 *
 * Fabricated-DOM test in the same style as msteams/removal.test.ts — no
 * browser, no live meeting. Covers:
 *   1. every host-removal text trips checkForGoogleRemoval (point-of-introduction).
 *   2. the removal texts are actually present in googleRemovalIndicators
 *      (so a future prune cannot silently delete the fix).
 *   3. startGoogleRemovalMonitor() fires onRemoval EXACTLY ONCE, then stops.
 *   4. a benign page (nothing visible) never fires.
 *
 * Run: npx tsx src/googlemeet/removal.test.ts
 */

import { checkForGoogleRemoval, startGoogleRemovalMonitor } from './removal';
import { googleRemovalIndicators } from './selectors';

// Live Meet copy variants when a host removes the bot (exact + substring +
// typographic-apostrophe forms). Must ALL be detected.
const HOST_REMOVAL_TEXTS = [
  'text="You were removed from the meeting"',
  'text=You were removed from the meeting',
  'text="You\'ve been removed from the meeting"',
  'text=You\'ve been removed from the meeting',
  'text=You’ve been removed from the meeting',
  'text="You have been removed from the meeting"',
  'text=You have been removed from the meeting',
  'text="Removed from meeting"',
  'text=Removed from meeting',
  'text="You can\'t rejoin this meeting"',
  'text=You can\'t rejoin this meeting',
  'text=You can’t rejoin this meeting',
];

// Pre-existing end/leave signals that must keep working after the addition.
const KEPT_END_TEXTS = [
  'text="Meeting ended"',
  'text=Meeting ended',
  'text="This meeting has ended"',
  'text=This meeting has ended',
  'text="The meeting has ended"',
  'text=The meeting has ended',
  'text="Meeting is over"',
  'text=Meeting is over',
  'text="The host has ended the meeting"',
  'text=host has ended the meeting',
  'text="Call ended"',
  'text=Call ended',
  'text="You left the meeting"',
  'text=You left the meeting',
];

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true. checkForGoogleRemoval iterates googleRemovalIndicators
 * and calls page.locator(sel).first().isVisible() on each. The monitor also
 * calls page.evaluate() to dismiss modals — a missing/throwy evaluate must not
 * break detection (the dismiss click is best-effort).
 *
 * `url` is a mutable holder (Playwright's page.url() is synchronous): the
 * monitor anchors the meeting origin at start, so a test can mutate it between
 * ticks to simulate a navigation.
 */
function mockPage(visible: string[], opts: { url?: string; urlThrows?: boolean; evaluateThrows?: boolean } = {}): any {
  const state = { currentUrl: opts.url ?? 'https://meet.google.com/abc-defg-hij' };
  return {
    _state: state,
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => visible.includes(sel),
      }),
    }),
    url: () => {
      if (opts.urlThrows) throw new Error('url unavailable');
      return state.currentUrl;
    },
    evaluate: opts.evaluateThrows
      ? async () => { throw new Error('evaluate failed'); }
      : async () => undefined,
  };
}

let passed = 0, failed = 0;
function check(name: string, actual: boolean, expected: boolean) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${actual})`); failed++; }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

(async () => {
  console.log('\n=== Google Meet host-removal detection ===');

  // 1. Every host-removal text must trip detection.
  for (const t of HOST_REMOVAL_TEXTS) {
    check(
      `host-removal text detected: ${t}`,
      await checkForGoogleRemoval(mockPage([t])),
      true,
    );
  }

  // 2. Pre-existing end/leave signals still detected (no regression).
  for (const t of KEPT_END_TEXTS) {
    check(
      `existing end text still detected: ${t}`,
      await checkForGoogleRemoval(mockPage([t])),
      true,
    );
  }

  // 3. Point-of-introduction guard: the removal texts must be IN the list.
  for (const t of HOST_REMOVAL_TEXTS) {
    check(`removal text present in googleRemovalIndicators: ${t}`, googleRemovalIndicators.includes(t), true);
  }

  // 4. Nothing visible → not removed.
  check(
    'nothing visible → checkForGoogleRemoval = false',
    await checkForGoogleRemoval(mockPage([])),
    false,
  );

  // 5. The monitor fires onRemoval EXACTLY ONCE (interval cleared on detect).
  {
    let fired = 0;
    const stop = startGoogleRemovalMonitor(
      mockPage(['text=You were removed from the meeting']),
      () => { fired++; },
    );
    await sleep(3400); // > 2 ticks (1500ms each): first tick detects, second must not re-fire
    check('monitor fires onRemoval exactly once', fired === 1, true);
    stop();
  }

  // 6. A throwing evaluate (dismiss click) must not prevent detection.
  {
    let fired = 0;
    const stop = startGoogleRemovalMonitor(
      mockPage(['text="Removed from meeting"'], { evaluateThrows: true }),
      () => { fired++; },
    );
    await sleep(2000);
    check('monitor fires despite evaluate failure', fired === 1, true);
    stop();
  }

  // 7. Benign page → monitor never fires.
  {
    let fired = 0;
    const stop = startGoogleRemovalMonitor(mockPage([]), () => { fired++; });
    await sleep(2000);
    check('monitor does not fire on a benign page', fired === 0, true);
    stop();
  }

  // 8. Navigation away from the meeting origin (torn-down tab / redirect) is
  //    treated as removal/end — the alive-but-navigated-away bot must not idle.
  {
    let fired = 0;
    const page = mockPage([]);
    const stop = startGoogleRemovalMonitor(page, () => { fired++; });
    page._state.currentUrl = 'about:blank'; // e.g. the tab was closed by the platform
    await sleep(2000);
    check('navigation off the meeting origin fires removal', fired === 1, true);
    stop();
  }

  // 9. Same-origin navigation (Meet moving between /xyz routes) is NOT a removal.
  {
    let fired = 0;
    const page = mockPage([]);
    const stop = startGoogleRemovalMonitor(page, () => { fired++; });
    page._state.currentUrl = 'https://meet.google.com/other-code-here'; // same origin
    await sleep(2000);
    check('same-origin navigation does not fire removal', fired === 0, true);
    stop();
  }

  // 10. A throwy url() must not break the selector lane (removal text still fires).
  {
    let fired = 0;
    const stop = startGoogleRemovalMonitor(
      mockPage(['text=You were removed from the meeting'], { urlThrows: true }),
      () => { fired++; },
    );
    await sleep(2000);
    check('throwy url() does not block selector detection', fired === 1, true);
    stop();
  }

  console.log(`\n  ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
})();
