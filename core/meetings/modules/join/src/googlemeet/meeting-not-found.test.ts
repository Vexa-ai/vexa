/**
 * The Google Meet "meeting space does not exist" screen (#1325) — driven by the
 * first REAL captured Meet DOM in this repository.
 *
 * THE BUG (prod pods vexa-mtg-26920 / 26921, 2026-08-25, one minute apart):
 * a dead meeting code makes Meet's call-setup RPC answer
 * `startupCode: 217 … statusCode = 404 … "Requested meeting space does not
 * exist."`, and the SPA renders an error screen with NO join CTA and NO name
 * input. The bot could not see that screen, so it entered the join-CTA hunt,
 * exhausted the full 60s budget and exited code 1 as a generic `join_failure` —
 * a reason the retry classifier treats as TRANSIENT, so the control plane
 * re-spawned a bot against a code that can never exist.
 *
 * FIXTURE HONESTY, the other way round for once. Every other Meet DOM in this
 * module is FABRICATED and says so (#857). The fixture this file loads is NOT:
 * `fixtures/gmeet-404-meeting-not-found.html` is the error-screen subtree lifted
 * verbatim from a page Google served on 2026-08-25, captured by
 * `scripts/capture-page-dom.ts` inside the hot debug container — the same Xvfb +
 * stealth-chromium shape the bot joins with — against
 * `https://meet.google.com/aaa-bbbb-ccc`, and reproduced identically on a second
 * dead code. Its sidecar `.meta.json` carries the console lines, which match the
 * production pods.
 *
 * WHAT THIS FILE PINS
 *   1. PRE-CTA, and it has to be: on the real page the shipped join-CTA list and
 *      name-input list resolve NOTHING, and the structural scan refuses (two
 *      text buttons). Detection therefore cannot live downstream of a CTA click.
 *   2. The detector fires on the real page, reads the real code (217), and types
 *      it as `meeting_not_found`.
 *   3. The copy constants were imagination. NONE of the eight meeting-not-found
 *      strings in `googleRejectionIndicators` appear on the page Google serves —
 *      which is why the discriminator is `data-startup-code`, not words.
 *   4. It cannot fire on a lobby: the fabricated lobby DOMs, an empty attribute,
 *      and a bare "Return to home screen" button (the #471 WAITING indicator,
 *      which this page also renders) all resolve to "no error screen".
 *   5. An unevidenced startup code still terminates fast, but is NOT named
 *      `meeting_not_found`.
 *
 * No browser, no live meeting, no Google.
 *
 * Run: npx tsx src/googlemeet/meeting-not-found.test.ts
 */

import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { join as pathJoin } from 'path';
import {
  googleJoinButtonSelectors,
  googleAuthJoinCtaSelectors,
  googleNameInputSelectors,
  googleRejectionIndicators,
  googleLobbyIconGlyphSelectors,
  googleLobbyCtaMaxLabelChars,
  googleStartupErrorScreenSelectors,
  googleStartupErrorHeadingSelectors,
  googleMeetingNotFoundStartupCodes,
  googleMeetingNotFoundCopy,
} from './selectors';
import {
  findMeetStartupError, startupErrorToAdmissionError, findLobbyPrimaryCta,
  readMeetStartupError, waitForLobbyCta, waitForAnySelector,
} from './join';
import { AdmissionError } from '../shared/admission';
import type { MeetStartupError } from './join';

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

// ── The REAL capture ────────────────────────────────────────────────────────
const FIXTURE_DIR = pathJoin(__dirname, 'fixtures');
const REAL_404 = readFileSync(pathJoin(FIXTURE_DIR, 'gmeet-404-meeting-not-found.html'), 'utf8');
const REAL_404_META = JSON.parse(
  readFileSync(pathJoin(FIXTURE_DIR, 'gmeet-404-meeting-not-found.meta.json'), 'utf8'),
);

// ── Negative controls ───────────────────────────────────────────────────────
// A minimal English lobby, in the shape join-cta.test.ts fabricates: a name
// input, icon affordances, and an "Ask to join" CTA.
const LOBBY = `<!doctype html><html lang="en"><body>
  <div jscontroller="dyDNGc" class="lobby">
    <input jsname="YPqjbf" type="text" aria-label="Your name" value="">
    <button jsname="hw0c9" aria-label="Turn off microphone"><i class="google-material-icons">mic</i></button>
    <button jsname="psRWwc" aria-label="Turn off camera"><i class="google-material-icons">videocam</i></button>
    <button jsname="Qx7uuf" class="UywwFc-LgbsSe" data-cta="1"><span jsname="V67aGc">Ask to join</span></button>
  </div></body></html>`;

// The #471 waiting screen: "Return to home screen" WITHOUT the error container.
// The real 404 page renders that same button, so it must never discriminate.
const WAITING_ROOM = `<!doctype html><html lang="en"><body>
  <div><span>Asking to be let in...</span>
  <button jsname="dqt8Pb"><span jsname="V67aGc">Return to home screen</span></button>
  </div></body></html>`;

// The attribute present but empty — "the screen exists" must mean a real code.
const EMPTY_CODE = `<!doctype html><html lang="en"><body>
  <div class="Fi0Gqc" data-startup-code=""><h1 jsname="r4nke">Something</h1></div></body></html>`;

// A startup-error screen carrying a code we have NOT evidenced.
const UNKNOWN_CODE = `<!doctype html><html lang="en"><body>
  <div class="Fi0Gqc" data-startup-code="150"><h1 jsname="r4nke">You can't join this video call</h1></div>
  </body></html>`;

function mount(html: string): Document {
  const dom = new JSDOM(html, { pretendToBeVisual: true });
  dom.window.Element.prototype.getBoundingClientRect = function (this: Element) {
    const tag = this.tagName.toLowerCase();
    const w = tag === 'button' || tag === 'input' ? 120 : 400;
    const h = tag === 'button' || tag === 'input' ? 36 : 200;
    return { width: w, height: h, top: 0, left: 0, right: w, bottom: h, x: 0, y: 0, toJSON() { return {}; } };
  };
  (globalThis as any).document = dom.window.document;
  return dom.window.document;
}

const SCAN_OPTS = {
  screenSelector: googleStartupErrorScreenSelectors.join(', '),
  headingSelector: googleStartupErrorHeadingSelectors.join(', '),
};
const detect = (html: string) => { mount(html); return findMeetStartupError(SCAN_OPTS); };

/** Playwright selector semantics against a real DOM (same emulation as join-cta.test.ts). */
function selectorMatches(doc: Document, selector: string): Element[] {
  if (selector.startsWith('//')) {
    const r = doc.evaluate(selector, doc, null, 7, null);
    const out: Element[] = [];
    for (let i = 0; i < r.snapshotLength; i++) out.push(r.snapshotItem(i) as Element);
    return out;
  }
  const has = /^(.*):has-text\("(.+)"\)$/.exec(selector);
  if (has) {
    const needle = has[2].toLowerCase();
    return Array.from(doc.querySelectorAll(has[1] || '*')).filter((el) =>
      (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase().includes(needle));
  }
  const text = /^text=("?)(.+?)\1$/.exec(selector);
  if (text) {
    const needle = text[2].toLowerCase();
    return Array.from(doc.querySelectorAll('*')).filter((el) =>
      (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase().includes(needle));
  }
  try { return Array.from(doc.querySelectorAll(selector)); } catch { return []; }
}

const anyMatch = (doc: Document, list: string[]) =>
  list.filter((sel) => selectorMatches(doc, sel).length > 0);

(async () => {
  console.log('\n=== 0. The fixture is a real capture, not a story about one ===');
  {
    assert(REAL_404_META.capturedAt === '2026-08-25' && /capture-page-dom\.ts/.test(REAL_404_META.harness),
      `provenance recorded: captured ${REAL_404_META.capturedAt} by ${REAL_404_META.harness}`);
    assert(REAL_404_META.console.some((l: string) => l.includes('status of 404')),
      'the capture carries the browser 404 — the same console line the prod pods printed');
    assert(REAL_404_META.console.filter((l: string) => l.includes('ConnectError')).length > 0 &&
           REAL_404_META.console.filter((l: string) => l.includes('DisconnectedError')).length > 0,
      'and the ConnectError / DisconnectedError pair from vexa-mtg-26920 / 26921');
    assert(REAL_404_META.reproducedOn.length >= 2,
      `deterministic: reproduced on ${REAL_404_META.reproducedOn.length} distinct dead codes`);
    const body = REAL_404.replace(/^<!--[\s\S]*?-->\s*/, '');   // the provenance header is ours, not Google's
    assert(!/<script/i.test(body) && !/<style/i.test(body),
      'the checked-in subtree carries no <script> and no <style> — nothing executable, nothing secret');
  }

  console.log('\n=== 1. WHY IT MUST BE PRE-CTA — the real page offers the join path nothing to find ===');
  {
    const doc = mount(REAL_404);

    // Worse than "matches nothing": the list's broad locale-agnostic backstop
    // matches, and what it matches is the button that LEAVES the meeting.
    const exact = anyMatch(doc, googleJoinButtonSelectors.filter((s) => !s.includes(':not([aria-label])')));
    assert(exact.length === 0,
      'no EXACT join-CTA selector matches the real 404 page (there is no CTA to match)');

    const backstop = 'button[jsname]:not([aria-label]):has(span)';
    const hits = selectorMatches(doc, backstop);
    const firstText = hits.length > 0 ? (hits[0].textContent || '').replace(/\s+/g, ' ').trim() : '';
    assert(googleJoinButtonSelectors.includes(backstop) && firstText === 'Return to home screen',
      'but the SHIPPED structural backstop matches — and its first hit is "Return to home screen", ' +
      'the control that navigates OFF the meeting. A list-first order does not waste 60s, it mis-clicks');
    assert(googleAuthJoinCtaSelectors.includes(backstop),
      'the authenticated CTA list carries the same backstop, so the authenticated path had the same exposure');

    const name = anyMatch(doc, googleNameInputSelectors);
    assert(name.length === 0,
      'NO entry in googleNameInputSelectors matches either — the page has no <input> at all');

    const scan = findLobbyPrimaryCta({
      iconGlyphSelector: googleLobbyIconGlyphSelectors.join(', '),
      maxLabelChars: googleLobbyCtaMaxLabelChars,
    });
    assert(scan.el === null && scan.labels.length === 2,
      `the structural scan refuses too — two text buttons, [${scan.labels.join(' | ')}]`);

    // Everything downstream of a CTA click is therefore unreachable on this page.
    assert(scan.labels.includes('Return to home screen'),
      'and the page DOES render "Return to home screen" — the #471 WAITING indicator, on a dead meeting');
  }

  console.log('\n=== 2. The copy constants were imagination (the finding that forced a structural signal) ===');
  {
    const doc = mount(REAL_404);
    const notFoundCopy = googleRejectionIndicators.filter((s) =>
      /meeting not found|invalid meeting|meeting link expired|unable to join|access denied|can't join the meeting|meeting has ended/i.test(s));
    assert(notFoundCopy.length >= 8,
      `googleRejectionIndicators carries ${notFoundCopy.length} meeting-not-found copy entries`);
    const hits = anyMatch(doc, notFoundCopy);
    assert(hits.length === 0,
      'and NOT ONE of them matches the page Google actually serves — the whole list would have missed');
    assert(googleMeetingNotFoundCopy.some((c) => REAL_404.includes(c)),
      `the real copy is "${googleMeetingNotFoundCopy[0]}" — captured, not guessed`);
  }

  console.log('\n=== 3. The detector fires on the real page, and reads the real code ===');
  {
    const e = detect(REAL_404);
    assert(e.code === '217', `data-startup-code read as "${e.code}" — the same 217 the prod gRPC body printed`);
    assert(e.heading === 'Check your meeting code', `heading captured for the diagnostic: "${e.heading}"`);
    assert(googleMeetingNotFoundStartupCodes.includes(e.code!),
      '217 is in the evidenced meeting-not-found code set');

    const err = startupErrorToAdmissionError(e, 'https://meet.google.com/aaa-bbbb-ccc');
    assert(err instanceof AdmissionError && err.outcome === 'meeting_not_found',
      'typed as AdmissionError("meeting_not_found") — NOT the generic join_failure prod recorded');
    assert(err.message.includes('217') && err.message.includes('Check your meeting code') &&
           err.message.includes('aaa-bbbb-ccc'),
      'the message carries code + Meet\'s own words + the url, so meeting.data.last_error diagnoses alone');
    assert(/retry cannot help/i.test(err.message),
      'and says why no retry can help — the reason this reason is PERMANENT');
  }

  console.log('\n=== 4. It cannot fire on a joinable page (the expensive direction to get wrong) ===');
  {
    assert(detect(LOBBY).code === null, 'an English lobby with a CTA → no error screen');
    assert(detect(WAITING_ROOM).code === null,
      '"Return to home screen" alone (the #471 waiting screen) → no error screen; the button never discriminates');
    assert(detect(EMPTY_CODE).code === null, 'data-startup-code="" → not an error screen (a code must be a code)');
    assert(detect('<!doctype html><html><body><div>nothing here</div></body></html>').code === null,
      'an unrelated page → no error screen');

    // And the lobby still resolves its CTA — detection changed nothing about joining.
    const doc = mount(LOBBY);
    assert(anyMatch(doc, googleJoinButtonSelectors).length > 0,
      'the lobby CTA still resolves by the shipped list (the join path is untouched)');
  }

  console.log('\n=== 5. An unevidenced startup code: fail fast, but do not name what we have not seen ===');
  {
    const e = detect(UNKNOWN_CODE);
    assert(e.code === '150', 'an unknown startup-error screen is still recognised as one');
    const err = startupErrorToAdmissionError(e, 'https://meet.google.com/xxx-yyyy-zzz');
    assert(err.outcome === 'join_failure',
      'but is typed join_failure, not meeting_not_found — we have not earned that name for code 150');
    assert(err.message.includes('150') && /no join control/.test(err.message),
      'while still saying what it saw, so the next occurrence is one look away from a diagnosis');
  }

  console.log('\n=== 6. Ordering, on a page object — the guard runs BEFORE the selector list resolves anything ===');
  {
    // A page that is the real 404 screen AND on which the shipped backstop selector
    // is visible: exactly the production shape. If the list won the race, the join
    // would click "Return to home screen"; the guard must win instead.
    const backstop = 'button[jsname]:not([aria-label]):has(span)';
    const errorScreenPage = (over: Partial<MeetStartupError> = {}) => ({
      url: () => 'https://meet.google.com/aaa-bbbb-ccc',
      locator: (sel: string) => ({
        first: () => ({
          isVisible: async () => sel === backstop,
          elementHandle: async () => (sel === backstop ? { __element: true } : null),
        }),
      }),
      waitForTimeout: async () => { await new Promise((r) => setTimeout(r, 1)); },
      screenshot: async () => {},
      evaluate: async () => ({ code: '217', heading: 'Check your meeting code', body: '', ...over }),
      evaluateHandle: async () => ({
        getProperty: async () => ({ jsonValue: async () => [], asElement: () => null }),
        dispose: async () => {},
      }),
    });

    let thrown: any = null;
    try {
      await waitForLobbyCta(errorScreenPage() as any, googleJoinButtonSelectors, 5000, 'join button');
    } catch (e) { thrown = e; }
    assert(thrown instanceof AdmissionError && thrown.outcome === 'meeting_not_found',
      'waitForLobbyCta throws meeting_not_found even though the backstop selector IS visible — no mis-click');

    thrown = null;
    try {
      await waitForAnySelector(errorScreenPage() as any, googleNameInputSelectors, 5000, 'name input');
    } catch (e) { thrown = e; }
    assert(thrown instanceof AdmissionError && thrown.outcome === 'meeting_not_found',
      'the 120s name-input wait terminates on the same guard, in one poll rather than two minutes');

    // Fail-open: a page whose evaluate returns something unrecognised must NOT abort a join.
    const junkPage = {
      url: () => 'https://meet.google.com/abc-defg-hij',
      evaluate: async () => ({ lang: 'en', nav: 'en-US' }),   // the shape observedPageContext returns
    };
    assert((await readMeetStartupError(junkPage as any)).code === null,
      'an unrecognised evaluate result → no error screen (the guard fails OPEN, never closed)');
    const throwingPage = { url: () => 'x', evaluate: async () => { throw new Error('navigating'); } };
    assert((await readMeetStartupError(throwingPage as any)).code === null,
      'an evaluate that throws mid-navigation → no error screen either');
  }

  console.log(`\n${failed === 0 ? '\x1b[32m' : '\x1b[31m'}${passed} passed, ${failed} failed\x1b[0m\n`);
  process.exit(failed === 0 ? 0 : 1);
})();
