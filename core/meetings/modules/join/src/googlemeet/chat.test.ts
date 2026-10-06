/**
 * Browser-context in-meeting chat send — the announce-before-leave path.
 *
 * gmeetChatBrowserSend runs INSIDE the page via page.evaluate: opens the chat
 * panel, fills the composer, clicks send. It exists for the owner-set
 * leave-after bound (user_limit_reached), so the contract asserted here is
 * the one the orchestrator depends on: TRUE only when a line was actually
 * submitted, FALSE — never a throw — when chat is unreachable (a disabled
 * meeting's UI must not block the leave).
 *
 * This drives the EXACT function production serializes, against jsdom
 * fixtures (a real CSS engine, no browser), mirroring leave.test.ts.
 *
 * Run: npx tsx src/googlemeet/chat.test.ts
 */

import { JSDOM } from 'jsdom';
import { gmeetChatBrowserSend } from './chat';
import {
  googleChatInputSelectors,
  googleChatOpenMatchers,
  googleChatSendMatchers,
} from './selectors';

let passed = 0, failed = 0;
function check(name: string, actual: unknown, expected: unknown) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`); failed++; }
}

const selectors = {
  open: googleChatOpenMatchers,
  input: googleChatInputSelectors,
  send: googleChatSendMatchers,
};

/** Mount a jsdom document as the send routine's browser context (same shim set
 *  as leave.test.ts: real box on every element, visibility via computed style). */
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
  (globalThis as any).HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
  (globalThis as any).HTMLInputElement = dom.window.HTMLInputElement;
  (globalThis as any).Event = dom.window.Event;
  (globalThis as any).InputEvent = dom.window.InputEvent;
  (globalThis as any).KeyboardEvent = dom.window.KeyboardEvent;
  return { dom, logs, clicked };
}

const selectorErrors = (logs: string[]) => logs.filter((l) => l.includes('selector failed in browser context'));

(async () => {
  console.log('\n=== announce-before-leave: the chat line reaches the send ===');

  // Closed panel → the routine clicks the toolbar chat button, the panel
  // mounts (the fixture's click listener inserts the composer, like Meet's
  // own panel), the send button fires.
  {
    const { dom, logs, clicked } = mountDom(`
      <div role="toolbar">
        <button aria-label="Chat with everyone" data-fixture-id="chat-open"></button>
        <button aria-label="Leave call" data-fixture-id="leave"></button>
      </div>`);
    const open = dom.window.document.querySelector('[data-fixture-id="chat-open"]')!;
    open.addEventListener('click', () => {
      const panel = dom.window.document.createElement('div');
      panel.setAttribute('data-fixture-id', 'chat-panel');
      panel.innerHTML = `
        <textarea aria-label="Send a message" data-fixture-id="composer"></textarea>
        <button aria-label="Send" data-fixture-id="send"></button>`;
      dom.window.document.body.appendChild(panel);
      panel.querySelector('[data-fixture-id="send"]')!
        .addEventListener('click', () => clicked.push('send'));
    });
    const result = await gmeetChatBrowserSend({ text: 'ZAKI notetaker is leaving', ...selectors });
    check('closed-panel fixture → send returns true', result, true);
    check('the toolbar chat button was clicked first', clicked[0], 'chat-open');
    check('the send button was clicked after it mounted', clicked.includes('send'), true);
    check('the composer received the announcement text',
      (dom.window.document.querySelector('[data-fixture-id="composer"]') as HTMLTextAreaElement).value,
      'ZAKI notetaker is leaving');
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
  }

  // Panel already open (composer present) → no open click, straight to send.
  {
    const { logs, clicked } = mountDom(`
      <div role="toolbar"><button aria-label="Chat with everyone" data-fixture-id="chat-open"></button></div>
      <div data-fixture-id="chat-panel">
        <textarea aria-label="Send a message"></textarea>
        <button aria-label="Send" data-fixture-id="send"></button>
      </div>`);
    const result = await gmeetChatBrowserSend({ text: 'hi', ...selectors });
    check('open-panel fixture → send returns true', result, true);
    check('the open button was NOT clicked (panel already mounted)', clicked.includes('chat-open'), false);
    check('the send button was clicked', clicked.includes('send'), true);
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
  }

  // Chat disabled for the meeting (no affordance, no composer) → false, never a throw.
  {
    const { logs, clicked } = mountDom('<div>Meeting chrome, no chat anywhere</div>');
    const result = await gmeetChatBrowserSend({ text: 'hi', ...selectors });
    check('chat-disabled fixture → send returns false', result, false);
    check('nothing was clicked', clicked.length, 0);
    check('the no-affordance outcome is logged',
      logs.includes('[chat] no chat affordance matched — chat may be disabled for this meeting'), true);
    check('zero selector errors in browser context', selectorErrors(logs).length, 0);
  }

  // Panel opens but no composer mounts (Meet UI drift) → false, bounded.
  {
    const { logs } = mountDom(`
      <div role="toolbar"><button aria-label="Chat with everyone" data-fixture-id="chat-open"></button></div>`);
    const result = await gmeetChatBrowserSend({ text: 'hi', ...selectors });
    check('no-composer-after-open → send returns false', result, false);
    check('the missing composer is logged',
      logs.includes('[chat] chat panel opened but no composer input appeared'), true);
  }

  // Hidden composer (chat collapsed but mounted) must not be written to.
  {
    const { logs } = mountDom(`
      <div data-fixture-id="chat-panel">
        <textarea aria-label="Send a message" style="display: none"></textarea>
      </div>`);
    const result = await gmeetChatBrowserSend({ text: 'hi', ...selectors });
    check('hidden composer → send returns false', result, false);
    check('hidden composer handled cleanly', selectorErrors(logs).length, 0);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
