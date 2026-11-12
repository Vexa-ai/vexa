/**
 * Google Meet «Continue as guest» pre-join step (seen in prod 12 Nov 2026: 12 joins failed with
 * «Could not locate name input … after 120000ms»; an outside user posted the screen the same morning).
 *
 * WHAT THIS FILE PINS
 *   1. When the guest step is on screen, the bot presses «Continue as guest» once, then finds the name field.
 *   2. When the guest step is absent, nothing is pressed (the handler is inert on today's normal lobby).
 *   3. It is pressed at most once per page, even if the button stays visible.
 *   4. A name-field miss is never blind: the thrown message carries the visible text-button labels.
 *
 * The page is a stand-in (no browser, no Google): it models only visibility by selector and a click.
 * Run: npx tsx src/googlemeet/guest-step.test.ts
 */
import { googleContinueAsGuestSelectors, googleNameInputSelectors } from './selectors';
import { waitForAnySelector, pressContinueAsGuestIfPresent } from './join';

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  PASS  ${msg}`); } else { failed++; console.log(`  FAIL  ${msg}`); }
}

const GUEST = googleContinueAsGuestSelectors[0];
const NAME = googleNameInputSelectors[0];

function mockPage(o: { guest: boolean; nameAfterGuest: boolean; guestSticks?: boolean; labels?: string[] }) {
  const state = { guestVisible: o.guest, nameVisible: !o.guest && o.nameAfterGuest };
  const el = { __element: true };
  return {
    state,
    url: () => 'https://meet.google.com/abc-defg-hij',
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => (sel === GUEST ? state.guestVisible : sel === NAME ? state.nameVisible : false),
        elementHandle: async () => el,
      }),
    }),
    waitForTimeout: async (_ms: number) => { await new Promise((r) => setTimeout(r, 1)); },
    screenshot: async (_o: any) => {},
    evaluate: async (_fn: any) => ({ lang: 'en', nav: 'en-US' }),
    evaluateHandle: async (_fn: any, _opts: any) => ({
      getProperty: async (k: string) => ({
        jsonValue: async () => (k === 'labels' ? (o.labels || []) : null),
        asElement: () => null,
      }),
      dispose: async () => {},
    }),
  };
}

(async () => {
  // 1. guest step present → pressed once → name field found
  {
    const page = mockPage({ guest: true, nameAfterGuest: true });
    const clicks: string[] = [];
    const click = async (_h: any, label: string) => {
      clicks.push(label);
      page.state.guestVisible = false; page.state.nameVisible = true;
    };
    const hit = await waitForAnySelector(page as any, googleNameInputSelectors, 2000, 'name input',
      async () => { await pressContinueAsGuestIfPresent(page as any, click); }, true);
    assert(clicks.length === 1 && clicks[0] === 'continue_as_guest', 'guest step present: «Continue as guest» pressed once');
    assert(hit.selector === NAME, 'guest step present: the name field is found after the press');
  }
  // 2. guest step absent → nothing pressed
  {
    const page = mockPage({ guest: false, nameAfterGuest: true });
    const clicks: string[] = [];
    const hit = await waitForAnySelector(page as any, googleNameInputSelectors, 2000, 'name input',
      async () => { await pressContinueAsGuestIfPresent(page as any, async (_h: any, l: string) => { clicks.push(l); }); }, true);
    assert(clicks.length === 0, 'guest step absent: nothing pressed');
    assert(hit.selector === NAME, 'guest step absent: the name field is found as before');
  }
  // 3. button stays visible → pressed at most once per page
  {
    const page = mockPage({ guest: true, nameAfterGuest: false });
    let n = 0;
    try {
      await waitForAnySelector(page as any, googleNameInputSelectors, 60, 'name input',
        async () => { await pressContinueAsGuestIfPresent(page as any, async () => { n++; }); }, true);
    } catch { /* expected miss */ }
    assert(n === 1, `sticky guest button: pressed once, not ${n} times`);
  }
  // 4. a name-field miss records the visible text-button labels
  {
    const page = mockPage({ guest: false, nameAfterGuest: false, labels: ['Continue as guest', 'Sign in'] });
    let msg = '';
    try { await waitForAnySelector(page as any, googleNameInputSelectors, 30, 'name input', undefined, true); }
    catch (e: any) { msg = String(e?.message || e); }
    assert(msg.startsWith('Could not locate name input'), 'name miss: keeps the prefix prod monitoring greps');
    assert(msg.includes('"Continue as guest"') && msg.includes('"Sign in"'), 'name miss: the visible button labels are in the message');
  }
  console.log(`${passed} passed, ${failed} failed`);
  if (failed) process.exit(1);
})();
