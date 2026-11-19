/**
 * The Zoom pre-join after the `?wpk=` redirect — car 21c.
 *
 * THE BUG: 3 of 7 bot spawns on 2026-10-21 failed with a 30 s `waitForSelector(#input-for-name, …)`
 * timeout on `…/wc/<id>/join?wpk=…`; retries minutes later joined. Reproduced live: the page at the
 * `?wpk=` URL was not the pre-join but Zoom's self-refreshing hold page, "The host has another
 * meeting in progress … The page will refresh in 5 seconds", served while the host's account was
 * in a different meeting. The join knew one not-yet page (title "Error - Zoom") and read the page
 * once, 2 s after navigation; anything else went straight to the 30 s name wait, which this page
 * outlives (it opens when the host's other meeting ends, minutes later).
 *
 * The documents below are CAPTURED from app.zoom.us on 2026-10-21 (trimmed to the parts the join
 * reads; meeting ids and tokens replaced): the "Joining Meeting..." shell at the first URL, the hold
 * page, and the React pre-join. A fake page swaps them on a virtual clock the way Zoom does.
 *
 * No browser, no live meeting. Run: npx tsx src/zoom/prejoin.test.ts
 */
import { JSDOM } from 'jsdom';
import * as fs from 'fs';
import * as path from 'path';
import { joinZoomMeeting, ZOOM_PREJOIN_HELD, ZOOM_NAME_INPUT_MISSING } from './join';
import { AdmissionError } from '../shared/admission';
import {
  classifyZoomPreJoin,
  snapshotZoomPreJoin,
  ZOOM_PREJOIN_SELECTORS,
  type ZoomPreJoinSnapshot,
} from './prejoin-state';

let vnow = 1_000_000;
(Date as any).now = () => vnow;

let passed = 0, failed = 0;
const realConsoleLog = console.log;
function check(name: string, ok: boolean, detail = '') {
  if (ok) { realConsoleLog(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { realConsoleLog(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}
let captured: string[] = [];
function startCapture() {
  captured = [];
  console.log = (...a: any[]) => {
    const s = a.map(String).join(' ');
    try { captured.push(JSON.parse(s).msg ?? s); } catch { captured.push(s); }
  };
}
function stopCapture(): string[] { console.log = realConsoleLog; return captured; }

const FIX = path.join(__dirname, '__fixtures__');
const html = (f: string) => fs.readFileSync(path.join(FIX, f), 'utf8');
const SHELL = html('zoom-joining-shell.html');
const HOLD = html('zoom-hold-host-meeting-in-progress.html');
const PREJOIN = html('zoom-prejoin-react.html');

const FIRST_URL = 'https://app.zoom.us/wc/70000000001/join?pwd=FakeTestPass.1';
const WPK_URL = 'https://app.zoom.us/wc/70000000001/join?wpk=wcpk%7B0%7Dfake&_x_zm_rtaid=fake';

function snap(markup: string, url = WPK_URL): ZoomPreJoinSnapshot {
  const dom = new JSDOM(markup, { url });
  const g = globalThis as any;
  const saved = { document: g.document, location: g.location, getComputedStyle: g.getComputedStyle };
  g.document = dom.window.document; g.location = dom.window.location;
  g.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  try { return snapshotZoomPreJoin(ZOOM_PREJOIN_SELECTORS); }
  finally { Object.assign(g, saved); }
}

/**
 * A page that plays Zoom's sequence on the virtual clock: the shell at the first URL, the `?wpk=`
 * replacement after `redirectMs`, then the hold page until `holdMs` has passed (the page "reloads"
 * every 5 s while held), then the pre-join.
 */
function zoomPage(opts: { redirectMs: number; holdMs: number }) {
  let navAt = vnow;
  const current = () => {
    const t = vnow - navAt;
    if (t < opts.redirectMs) return { markup: SHELL, url: FIRST_URL };
    if (t < opts.redirectMs + opts.holdMs) return { markup: HOLD, url: WPK_URL };
    return { markup: PREJOIN, url: WPK_URL };
  };
  const doc = () => { const c = current(); return new JSDOM(c.markup, { url: c.url }).window; };
  const typed: string[] = [];
  const clicked: string[] = [];
  let joinClicked = false;
  const node = (sel: string): any => ({
    first: () => node(sel),
    isVisible: async () => { try { return !!doc().document.querySelector(sel); } catch { return false; } },
    click: async () => { clicked.push(sel); },
    fill: async () => {},
    getAttribute: async () => null,
    waitFor: async () => {},
    inputValue: async () => '',
  });
  const page: any = {
    goto: async () => { navAt = vnow; },
    url: () => current().url,
    title: async () => doc().document.title,
    waitForTimeout: async (ms: number) => { vnow += ms; },
    // Playwright's waitForSelector: poll the live page until the selector matches or the time is up.
    waitForSelector: async (sel: string, o: { timeout?: number } = {}) => {
      const until = vnow + (o.timeout ?? 30_000);
      while (vnow <= until) {
        if (doc().document.querySelector(sel)) return {};
        vnow += 100;
      }
      throw new Error(`page.waitForSelector: Timeout ${o.timeout}ms exceeded waiting for ${sel}`);
    },
    waitForFunction: async () => {},
    evaluate: async (fn: any, arg?: any) => {
      if (typeof fn !== 'function') return undefined;
      const w = doc();
      const g = globalThis as any;
      const saved = { document: g.document, location: g.location, getComputedStyle: g.getComputedStyle };
      g.document = w.document; g.location = w.location; g.getComputedStyle = w.getComputedStyle.bind(w);
      try {
        const src = fn.toString();
        if (src.includes('btn.click()')) { joinClicked = true; return true; }
        return fn(arg);
      } finally { Object.assign(g, saved); }
    },
    locator: node,
    keyboard: { type: async (t: string) => { typed.push(t); }, press: async () => {} },
    isClosed: () => false,
  };
  return { page, typed, clicked, joined: () => joinClicked };
}

async function run(opts: { redirectMs: number; holdMs: number }) {
  const z = zoomPage(opts);
  const t0 = vnow;
  let error: any = null;
  startCapture();
  try { await joinZoomMeeting(z.page, FIRST_URL, 'Vexa', { platform: 'zoom', meetingUrl: FIRST_URL } as any); }
  catch (e) { error = e; }
  const logs = stopCapture();
  return { error, logs, typed: z.typed, joined: z.joined(), elapsedMs: vnow - t0 };
}

(async () => {
  realConsoleLog('\n=== the captured pages read as what they are ===');
  {
    const shell = classifyZoomPreJoin(snap(SHELL, FIRST_URL));
    check('the "Joining Meeting..." shell is pending (no name field yet)', shell.kind === 'pending', JSON.stringify(shell));
    const hold = classifyZoomPreJoin(snap(HOLD));
    check('the hold page is a hold', hold.kind === 'hold', JSON.stringify(hold));
    check('its headline is read', hold.kind === 'hold' && hold.headline === 'The host has another meeting in progress',
      JSON.stringify(hold));
    const loc = classifyZoomPreJoin(snap(HOLD.replace('The host has another meeting in progress', 'Der Host hat ein anderes Meeting')));
    check('a localised headline is still a hold (matched by the reload control, not the words)', loc.kind === 'hold', JSON.stringify(loc));
    check('the hold page is not read as "sign in to join" (its "sign in to start meeting" link)',
      classifyZoomPreJoin(snap(HOLD)).kind !== 'auth_required');
    const pre = classifyZoomPreJoin(snap(PREJOIN));
    check('the React pre-join has the name field', pre.kind === 'name_field', JSON.stringify(pre));
    const hidden = classifyZoomPreJoin(snap(PREJOIN.replace('id="input-for-name"', 'id="input-for-name" style="display:none"')));
    check('a hidden name input is not a name field', hidden.kind !== 'name_field', JSON.stringify(hidden));
    const err = classifyZoomPreJoin({ url: FIRST_URL, title: 'Error - Zoom', nameField: false, hold: null, text: 'This meeting link is invalid (3,001)' });
    check('"Error - Zoom" is host-not-started', err.kind === 'host_not_started');
    const auth = classifyZoomPreJoin({ url: FIRST_URL, title: 'Zoom', nameField: false, hold: null, text: 'Sign in to join this meeting' });
    check('a sign-in page is auth_required', auth.kind === 'auth_required');
    check('an unreadable page (navigation in flight) is pending', classifyZoomPreJoin(null).kind === 'pending');
  }

  realConsoleLog('\n=== the witness case: held 3 min on "another meeting in progress", then the pre-join ===');
  {
    const r = await run({ redirectMs: 1500, holdMs: 3 * 60_000 });
    check('no error (the join waits the hold out)', r.error === null, String(r.error?.message ?? r.error));
    check('the name was typed', r.typed.includes('Vexa'), JSON.stringify(r.typed));
    check('Join was clicked', r.joined);
    check('the hold was logged once, with its headline',
      r.logs.filter((l) => l.includes('self-refreshing page ("The host has another meeting in progress")')).length === 1,
      JSON.stringify(r.logs.filter((l) => /hold|self-refreshing/i.test(l))));
    check('the join finished within a few seconds of the page opening', r.elapsedMs < 3 * 60_000 + 15_000, `${r.elapsedMs}ms`);
  }

  realConsoleLog('\n=== a slow redirect (the shell lives 6 s) still finds the name field ===');
  {
    const r = await run({ redirectMs: 6000, holdMs: 0 });
    check('no error', r.error === null, String(r.error?.message ?? r.error));
    check('the name was typed', r.typed.includes('Vexa'));
  }

  realConsoleLog('\n=== a hold that never opens ends typed, host-side, after the 10-min budget ===');
  {
    const r = await run({ redirectMs: 1500, holdMs: Infinity });
    check('an AdmissionError', r.error instanceof AdmissionError, String(r.error));
    check('outcome lobby_timeout (host-side, a legitimate retry)', r.error?.outcome === 'lobby_timeout', r.error?.outcome);
    check(`the message carries ${ZOOM_PREJOIN_HELD} and the headline`,
      String(r.error?.message).includes(`${ZOOM_PREJOIN_HELD}:`) && String(r.error?.message).includes('another meeting in progress'),
      String(r.error?.message));
    check('the message says the page was the ?wpk= one, without its token',
      String(r.error?.message).includes('after the ?wpk= redirect') && !String(r.error?.message).includes('wcpk'),
      String(r.error?.message));
    check('it gave up at ~10 min, not at 30 s', r.elapsedMs >= 10 * 60_000 && r.elapsedMs < 11 * 60_000, `${r.elapsedMs}ms`);
    check('nothing was typed', r.typed.length === 0);
  }

  realConsoleLog('\n=== a page that is none of the known ones ends typed after 30 s, with what it showed ===');
  {
    const r = await run({ redirectMs: Infinity, holdMs: 0 });
    check('an AdmissionError', r.error instanceof AdmissionError, String(r.error));
    check('outcome join_failure (ours to look at)', r.error?.outcome === 'join_failure', r.error?.outcome);
    check(`the message leads with ${ZOOM_NAME_INPUT_MISSING} and quotes the page`,
      String(r.error?.message).includes(`${ZOOM_NAME_INPUT_MISSING}:`) && String(r.error?.message).includes('Joining Meeting'),
      String(r.error?.message));
    check('the message does not carry the passcode', !String(r.error?.message).includes('FakeTestPass'), String(r.error?.message));
    check('it gave up at ~30 s', r.elapsedMs >= 30_000 && r.elapsedMs < 40_000, `${r.elapsedMs}ms`);
  }

  realConsoleLog(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
})();
