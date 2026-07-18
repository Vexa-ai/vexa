/**
 * Browser-context leave click — execution-context regression guard (#542).
 *
 * The leave click runs INSIDE the page via page.evaluate + document.querySelector,
 * which understands plain CSS only. The pre-#542 array fed it Playwright-only
 * `:has-text()` entries: querySelector threw SyntaxError on every one, the
 * throws were caught-and-skipped, and every confirmation-dialog fallback
 * ("Leave meeting", "Just leave the meeting", dialog-scoped Leave/End) was
 * silently dead — the bot could not click a leave-confirmation dialog, and each
 * leave attempt spammed N invalid-selector errors that read like join failures
 * (#432). Red control on the pre-fix tree: the same dialog fixture returned
 * false with 10/20 selectors throwing.
 *
 * This test drives googleLeaveBrowserClick — the EXACT function both consumers
 * serialize into the page — against jsdom fixtures (a real CSS engine, no
 * browser): the confirmation dialog is clickable, priorities hold, and no
 * selector error fires.
 *
 * Run: npx tsx src/googlemeet/leave.test.ts
 */

import { JSDOM } from 'jsdom';
import { googleLeaveBrowserClick } from './leave';
import { googleLeaveButtonMatchers } from './selectors';

let passed = 0, failed = 0;
function check(name: string, actual: unknown, expected: unknown) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`); failed++; }
}

/**
 * Mount a jsdom document as the click routine's browser context. jsdom has no
 * layout, so every element gets a real box; visibility then keys on computed
 * style (display/visibility/opacity), exactly like a rendered page.
 * Returns the collected logBot lines and a `clicked` recorder.
 */
function mountDom(html: string) {
  const dom = new JSDOM(`<!doctype html><html><body>${html}</body></html>`);
  dom.window.Element.prototype.getBoundingClientRect = function () {
    return { width: 120, height: 40, top: 0, left: 0, right: 120, bottom: 40, x: 0, y: 0, toJSON() {} } as any;
  };
  (dom.window.HTMLElement.prototype as any).scrollIntoView = function () {};
  const logs: string[] = [];
  (dom.window as any).logBot = (m: string) => logs.push(m);
  const clicked: string[] = [];
  for (const el of Array.from(dom.window.document.querySelectorAll('button, [role="button"], input'))) {
    el.addEventListener('click', () => clicked.push(el.getAttribute('data-fixture-id') || el.outerHTML));
  }
  (globalThis as any).window = dom.window;
  (globalThis as any).document = dom.window.document;
  (globalThis as any).getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  return { dom, logs, clicked };
}

const selectorErrors = (logs: string[]) => logs.filter((l) => l.includes('selector failed in browser context'));

(async () => {
  console.log('\n=== the mechanism (#542): Playwright-only selectors are invalid in browser context ===');

  // Negative control — the exact selector shape the pre-fix array shipped
  // throws in a real CSS engine. This is what killed every dialog fallback,
  // and it proves this fixture's engine genuinely rejects Playwright syntax
  // (so the zero-selector-error assertions below mean something).
  {
    const { dom } = mountDom('<button>Leave meeting</button>');
    let threw = false;
    try { dom.window.document.querySelector('button:has-text("Leave meeting")'); }
    catch { threw = true; }
    check('querySelector(`button:has-text("…")`) throws SyntaxError (the dead-selector mechanism)', threw, true);
  }

  console.log('\n=== A1: leave-confirmation dialog is clickable through the browser-context path ===');

  // The dialog fixture: ONLY a confirmation dialog — no toolbar leave button.
  // Pre-#542 red: leave returned false here (every matching selector was
  // Playwright-only). Includes the dialog's Close X to pin priority: the
  // confirmation button must win over generic close/cancel.
  {
    const { logs, clicked } = mountDom(`
      <div role="dialog" aria-label="Leave the meeting?">
        <h2>Leave the meeting?</h2>
        <button aria-label="Close" data-fixture-id="close-x"></button>
        <button data-fixture-id="just-leave"><span>Just leave the meeting</span></button>
      </div>`);
    const result = await googleLeaveBrowserClick(googleLeaveButtonMatchers);
    check('dialog-only fixture → leave returns true', result, true);
    check('the confirmation button was clicked (not the Close X)', clicked.join(','), 'just-leave');
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
    // NB: attributed to the "Just leave the meeting" matcher — "Leave meeting"
    // is NOT a substring of "Just leave THE meeting", under these semantics or
    // Playwright's `:has-text()` alike.
    check(
      'click is attributed in the logs',
      logs.includes('[leave] clicked leave button via text~"Just leave the meeting"'),
      true,
    );
  }

  console.log('\n=== A3 guards: primary path, visibility, and the no-button case ===');

  // In-call fixture — the primary aria-label button wins first, before any
  // text matching (unchanged behavior of the shipped path).
  {
    const { logs, clicked } = mountDom(`
      <div role="toolbar">
        <button aria-label="Chat with everyone" data-fixture-id="chat"></button>
        <button aria-label="Leave call" data-fixture-id="leave-call"></button>
      </div>`);
    const result = await googleLeaveBrowserClick(googleLeaveButtonMatchers);
    check('in-call fixture → leave returns true', result, true);
    check('the primary Leave call button was clicked', clicked.join(','), 'leave-call');
    check(
      'attributed to the primary CSS matcher',
      logs.some((l) => l === '[leave] clicked leave button via button[aria-label="Leave call"]'),
      true,
    );
  }

  // Visibility guard — a display:none confirmation button must not be clicked.
  {
    const { clicked } = mountDom(`
      <div role="dialog">
        <button style="display: none" data-fixture-id="hidden"><span>Just leave the meeting</span></button>
      </div>`);
    const result = await googleLeaveBrowserClick(googleLeaveButtonMatchers);
    check('hidden confirmation button → leave returns false', result, false);
    check('nothing was clicked', clicked.length, 0);
  }

  // No leave affordance at all → false, loudly.
  {
    const { logs, clicked } = mountDom('<div>Meeting chrome, no leave anywhere</div>');
    const result = await googleLeaveBrowserClick(googleLeaveButtonMatchers);
    check('no leave affordance → leave returns false', result, false);
    check('nothing was clicked', clicked.length, 0);
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
    check(
      'the no-match outcome is logged',
      logs.includes('[leave] no visible leave button matched any matcher'),
      true,
    );
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
