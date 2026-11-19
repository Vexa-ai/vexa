/**
 * Stop Incoming Video — the in-page step and the confirm loop, against a jsdom model of the
 * Zoom web client's footer More menu (markup copied from the live DOM, 2026-10-22).
 *
 * The model: the More button renders the menu grid on click and removes it on a second click or
 * Escape; the incoming-video item toggles its aria-label between "Stop Incoming Video" and
 * "Start Incoming Video" and closes the menu, as Zoom does.
 *
 * Run: npx tsx src/zoom/incoming-video.test.ts
 */

import { JSDOM } from 'jsdom';
import {
  incomingVideoStep,
  stopZoomIncomingVideo,
  ZOOM_INCOMING_VIDEO_SELECTORS as SEL,
} from './incoming-video';

let passed = 0, failed = 0;
function check(name: string, actual: unknown, expected: unknown) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`); failed++; }
}

interface ZoomModel {
  dom: JSDOM;
  /** Clicks on the incoming-video item, in order of the label it carried when clicked. */
  itemClicks: string[];
  moreClicks: number;
  menuOpen(): boolean;
  incomingOff(): boolean;
  escape(): void;
}

/** A footer with (optionally) the More button; the menu renders only while open. */
function zoomModel(opts: { more?: boolean; item?: boolean; incomingOff?: boolean } = {}): ZoomModel {
  const dom = new JSDOM('<!doctype html><html><body><div class="footer"></div></body></html>');
  const doc = dom.window.document;
  const footer = doc.querySelector('.footer')!;
  let off = !!opts.incomingOff;
  let menu: Element | null = null;
  const m: ZoomModel = {
    dom, itemClicks: [], moreClicks: 0,
    menuOpen: () => !!menu,
    incomingOff: () => off,
    escape: () => { menu?.remove(); menu = null; },
  };
  const openMenu = () => {
    menu = doc.createElement('div');
    menu.className = 'more-button__pop-menu';
    menu.innerHTML =
      '<button role="button" tabindex="0" aria-label="Show Captions" class="more-button__item-box">Show Captions</button>' +
      '<button role="button" tabindex="0" aria-label="Settings" class="more-button__item-box">Settings</button>' +
      (opts.item === false ? '' :
        `<button role="button" tabindex="0" aria-disabled="false" aria-label="${off ? 'Start' : 'Stop'} Incoming Video" class="more-button__item-box">` +
        `<span class="more-button__item-icon2"><i class="zmicon zmicon-acc-no-outline" role="img" aria-label="Video Incoming"></i></span>` +
        `${off ? 'Start' : 'Stop'} Incoming Video</button>`);
    doc.body.appendChild(menu);
    const item = menu.querySelector('[aria-label$="Incoming Video"]');
    item?.addEventListener('click', () => {
      m.itemClicks.push(item.getAttribute('aria-label')!);
      off = !off;
      m.escape();
    });
  };
  if (opts.more !== false) {
    const more = doc.createElement('button');
    more.setAttribute('type', 'button');
    more.setAttribute('aria-expanded', 'false');
    more.setAttribute('aria-label', 'More meeting control ');
    more.addEventListener('click', () => { m.moreClicks++; if (menu) m.escape(); else openMenu(); });
    footer.appendChild(more);
  }
  (globalThis as any).document = doc;
  return m;
}

/** The slice of a Playwright Page the confirm loop uses, driving the model in-process. */
function fakePage(m: ZoomModel) {
  return {
    isClosed: () => false,
    evaluate: async (fn: (a: any) => any, arg: any) => fn(arg),
    waitForTimeout: async (_ms: number) => {},
    keyboard: { press: async (k: string) => { if (k === 'Escape') m.escape(); } },
  } as any;
}

(async () => {
  console.log('\n=== selectors are plain CSS and match the live markup ===');
  {
    const m = zoomModel();
    const doc = m.dom.window.document;
    check('More button found by its trailing-space aria-label', !!doc.querySelector(SEL.more), true);
    (doc.querySelector(SEL.more) as HTMLElement).click();
    check('Stop item found once the menu is open', !!doc.querySelector(SEL.stop), true);
    check('the Start selector does not match the Stop item', doc.querySelector(SEL.start), null);
    check('the icon\'s "Video Incoming" label is not taken for the item', doc.querySelector(SEL.stop)?.tagName, 'BUTTON');
  }

  console.log('\n=== incomingVideoStep: one in-page step ===');
  {
    const m = zoomModel();
    check('menu closed → opens the More menu', incomingVideoStep(SEL), 'opened');
    check('…the menu is now open', m.menuOpen(), true);
    check('menu open with Stop → clicks it', incomingVideoStep(SEL), 'stopped');
    check('…incoming video is off', m.incomingOff(), true);
    check('…the item was clicked once, as "Stop"', m.itemClicks.join(','), 'Stop Incoming Video');
    incomingVideoStep(SEL);
    check('reopened menu reads Start → off, nothing clicked', incomingVideoStep(SEL), 'off');
    check('…still exactly one item click', m.itemClicks.length, 1);
  }
  {
    zoomModel({ more: false });
    check('no More button (meeting UI not up) → unavailable', incomingVideoStep(SEL), 'unavailable');
  }

  console.log('\n=== stopZoomIncomingVideo: stop, confirm, close the menu ===');
  {
    const m = zoomModel();
    const out = await stopZoomIncomingVideo(fakePage(m), { attempts: 5, intervalMs: 0, menuSettleMs: 0 });
    check('outcome off', out, 'off');
    check('incoming video is off in the client', m.incomingOff(), true);
    check('Stop clicked exactly once (never toggled back to Start)', m.itemClicks.join(','), 'Stop Incoming Video');
    check('the menu is left closed', m.menuOpen(), false);
  }
  {
    const m = zoomModel({ incomingOff: true });
    const out = await stopZoomIncomingVideo(fakePage(m), { attempts: 5, intervalMs: 0, menuSettleMs: 0 });
    check('already off → off, item never clicked', `${out}/${m.itemClicks.length}`, 'off/0');
    check('…menu left closed', m.menuOpen(), false);
  }
  {
    const m = zoomModel({ item: false });
    const out = await stopZoomIncomingVideo(fakePage(m), { attempts: 3, intervalMs: 0, menuSettleMs: 0 });
    check('client without the item → unavailable after the attempts', out, 'unavailable');
    check('…menu left closed', m.menuOpen(), false);
  }
  {
    const m = zoomModel({ more: false });
    const out = await stopZoomIncomingVideo(fakePage(m), { attempts: 3, intervalMs: 0, menuSettleMs: 0 });
    check('no More button at all → unavailable, no throw', out, 'unavailable');
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed) process.exit(1);
})();
