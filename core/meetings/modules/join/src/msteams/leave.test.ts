/**
 * Browser-context leave click — execution-context regression guard (#759,
 * the MS Teams instance of #542's class).
 *
 * The leave click runs INSIDE the page via page.evaluate + document.querySelector,
 * which understands plain CSS only. The pre-#759 array (teamsLeaveSelectors) fed
 * it Playwright-only `:has-text()` entries: querySelector threw SyntaxError on
 * every one, the throws were caught-and-skipped, and every text-labelled
 * fallback — all three confirmation-dialog entries included — was silently dead.
 * The bot could not click a leave-confirmation dialog, and each leave attempt
 * spammed 10 invalid-selector errors that read like a root cause (#432/#432's
 * Teams sibling). Red control on the pre-fix tree: the same dialog fixture
 * returned false with 10/25 selectors throwing.
 *
 * This test drives leaveBrowserClick — the EXACT shared function both consumers
 * serialize into the page — against jsdom fixtures (a real CSS engine, no
 * browser): the confirmation dialog is clickable, priorities hold, and no
 * selector error fires.
 *
 * Run: npx tsx src/msteams/leave.test.ts
 */

import { JSDOM } from 'jsdom';
import { leaveBrowserClick } from '../shared/leave-click';
import { teamsLeaveButtonMatchers } from './selectors';

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
  console.log('\n=== the mechanism (#759): Playwright-only selectors are invalid in browser context ===');

  // Negative control — the exact selector shape the pre-fix array shipped
  // throws in a real CSS engine. This is what killed every dialog fallback,
  // and it proves this fixture's engine genuinely rejects Playwright syntax
  // (so the zero-selector-error assertions below mean something).
  {
    const { dom } = mountDom('<button>Leave</button>');
    let threw = false;
    try { dom.window.document.querySelector('button:has-text("Leave")'); }
    catch { threw = true; }
    check('querySelector(`button:has-text("…")`) throws SyntaxError (the dead-selector mechanism)', threw, true);
  }

  console.log('\n=== A1: leave-confirmation dialog is clickable through the browser-context path ===');

  // The dialog fixture: ONLY a confirmation dialog with a text-labelled Leave
  // button (no aria-label, no id) — no toolbar hangup button. Pre-#759 red:
  // leave returned false here (every matching selector was Playwright-only).
  // Includes the dialog's Close X to pin priority: the confirmation button must
  // win over generic close/cancel.
  {
    const { logs, clicked } = mountDom(`
      <div role="dialog" aria-label="Leave the meeting?">
        <h2>Leave the meeting?</h2>
        <button aria-label="Close" data-fixture-id="close-x"></button>
        <button data-fixture-id="dialog-leave"><span>Leave</span></button>
      </div>`);
    const result = await leaveBrowserClick(teamsLeaveButtonMatchers);
    check('dialog-only fixture → leave returns true', result, true);
    check('the confirmation Leave button was clicked (not the Close X)', clicked.join(','), 'dialog-leave');
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
    // Attributed to the bare `{ text: 'Leave' }` matcher, which precedes the
    // dialog-scoped one in priority order and matches the same button.
    check(
      'click is attributed in the logs',
      logs.some((l) => l === '[leave] clicked leave button via text~"Leave"'),
      true,
    );
  }

  // "End meeting" confirmation dialog — a second text-labelled dialog button
  // that was Playwright-only pre-fix.
  {
    const { logs, clicked } = mountDom(`
      <div role="dialog">
        <button data-fixture-id="end-meeting"><span>End meeting</span></button>
      </div>`);
    const result = await leaveBrowserClick(teamsLeaveButtonMatchers);
    check('End-meeting dialog fixture → leave returns true', result, true);
    check('the End meeting button was clicked', clicked.join(','), 'end-meeting');
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
  }

  console.log('\n=== A3 guards: primary path, visibility, and the no-button case ===');

  // In-call fixture — the primary #hangup-button wins first, before any text
  // matching (unchanged behavior of the shipped Node/fast path's fallback).
  {
    const { logs, clicked } = mountDom(`
      <div role="toolbar">
        <button aria-label="Chat" data-fixture-id="chat"></button>
        <button id="hangup-button" data-fixture-id="hangup"></button>
      </div>`);
    const result = await leaveBrowserClick(teamsLeaveButtonMatchers);
    check('in-call fixture → leave returns true', result, true);
    check('the primary hangup button was clicked', clicked.join(','), 'hangup');
    check(
      'attributed to the primary CSS matcher',
      logs.some((l) => l === '[leave] clicked leave button via button[id="hangup-button"]'),
      true,
    );
  }

  // Visibility guard — a display:none confirmation button must not be clicked.
  {
    const { clicked } = mountDom(`
      <div role="dialog">
        <button style="display: none" data-fixture-id="hidden"><span>Leave</span></button>
      </div>`);
    const result = await leaveBrowserClick(teamsLeaveButtonMatchers);
    check('hidden confirmation button → leave returns false', result, false);
    check('nothing was clicked', clicked.length, 0);
  }

  // No leave affordance at all → false, loudly.
  {
    const { logs, clicked } = mountDom('<div>Meeting chrome, no leave anywhere</div>');
    const result = await leaveBrowserClick(teamsLeaveButtonMatchers);
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
