/**
 * Google Meet lobby primary-CTA location — the #846 gap and its guard rails.
 *
 * THE BUG (#846, prod meetings 24337 / 24339 on v0.12.15, then 3 more on
 * v0.12.16): the bot reached the Meet lobby, exhausted the full 60s join-button
 * budget and exited code 1 with 0 segments —
 * "Could not locate join button by any locale-agnostic or English selector".
 * The selector list's only locale-agnostic entry is
 * `button[jsname]:not([aria-label]):has(span)`, so it can see a lobby ONLY when
 * the CTA carries no accessible label. A non-English lobby whose CTA IS
 * aria-labelled matches nothing in the list: the English literals lose on the
 * language, the structural entry loses on `:not([aria-label])`. Production
 * agrees the structural entries are inert — 7/7 successful joins on 2026-07-20
 * matched the English literal `//button[.//span[text()="Ask to join"]]`.
 *
 * WHAT THIS FILE PINS
 *   1. The gap is real: the shipped selector list locates NOTHING on a lobby
 *      whose CTA is aria-labelled and non-English (and nothing on the variant
 *      whose CTA carries no jsname either — the other way the entry can miss).
 *   2. `findLobbyPrimaryCta` closes it locale-agnostically, and picks the CTA —
 *      not the mic/camera toggles, not the 3-dot menu, not the secondary
 *      joining options.
 *   3. It REFUSES to resolve anything when more than one button qualifies, so
 *      over-inclusion can only ever produce a diagnosable timeout, never a
 *      click on the wrong control (the #600 failure class: a catch-all selector
 *      that matches too much is its own bug).
 *   4. Ordered resolution: the selector list is honoured top-down, so a broad
 *      structural entry can never beat a precise English one on the same DOM.
 *   5. A total miss records the observed URL / html.lang / navigator.language
 *      and the candidate labels into the thrown message, which is what reaches
 *      `meeting.data.last_error` (#846 A4).
 *
 * FIXTURE HONESTY (#857): the lobby DOMs below are FABRICATED, not captured.
 * They are built to be consistent with the production evidence — every selector
 * in the shipped list misses — and they run through jsdom's real CSS engine and
 * real XPath, not a hand-rolled matcher. They prove the LOCATION LOGIC against
 * a real DOM implementation; they cannot prove Google's lobby has this exact
 * shape. Capturing the real lobby subtree is #857's job, and when it lands the
 * fixtures here should be replaced by it.
 *
 * No browser, no live meeting, no Google.
 *
 * Run: npx tsx src/googlemeet/join-cta.test.ts
 */

import { JSDOM } from 'jsdom';
import {
  googleJoinButtonSelectors,
  googleLobbyIconGlyphSelectors,
  googleLobbyCtaMaxLabelChars,
} from './selectors';
import {
  findLobbyPrimaryCta,
  waitForAnySelector,
  waitForLobbyCta,
  STRUCTURAL_CTA_ORIGIN,
} from './join';

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

// ── Lobby fixtures ──────────────────────────────────────────────────────────
// Chrome shared by every variant: the icon affordances a lobby always renders.
// Each is icon-bearing, which is the locale-independent fact the scan keys on.
const LOBBY_ICON_CHROME = `
  <div jscontroller="mdEjVc" class="controls">
    <button jsname="hw0c9" aria-label="MIC_LABEL" data-is-muted="false">
      <i class="google-material-icons" aria-hidden="true">mic</i>
    </button>
    <button jsname="psRWwc" aria-label="CAM_LABEL">
      <i class="google-material-icons" aria-hidden="true">videocam</i>
    </button>
    <button jsname="NakZHc" aria-label="MORE_LABEL">
      <i class="google-material-icons" aria-hidden="true">more_vert</i>
    </button>
  </div>`;

// Secondary joining options: icon + text. Real buttons, real labels, never the
// admission CTA. They carry an aria-label so the shipped structural selector
// (`:not([aria-label])`) misses them too — matching the production fact that
// the whole list came back empty.
const LOBBY_SECONDARY_OPTIONS = (phone: string, cast: string) => `
  <div class="other-joining-options">
    <button jsname="zTPKAe" aria-label="${phone}">
      <i class="google-material-icons" aria-hidden="true">phone</i><span>${phone}</span>
    </button>
    <button jsname="A9Sbfc" aria-label="${cast}">
      <i class="google-material-icons" aria-hidden="true">cast</i><span>${cast}</span>
    </button>
  </div>`;

function lobby(opts: {
  lang: string;
  ctaText: string;
  ctaAriaLabel?: string;
  ctaJsname?: string;
  micLabel: string;
  camLabel: string;
  moreLabel: string;
  phoneLabel: string;
  castLabel: string;
  extra?: string;
}): string {
  const aria = opts.ctaAriaLabel === undefined ? '' : ` aria-label="${opts.ctaAriaLabel}"`;
  const jsname = opts.ctaJsname === undefined ? '' : ` jsname="${opts.ctaJsname}"`;
  return `<!doctype html><html lang="${opts.lang}"><body>
  <div jscontroller="dyDNGc" class="lobby">
    <input jsname="YPqjbf" type="text" aria-label="name" value="">
    ${LOBBY_ICON_CHROME
      .replace('MIC_LABEL', opts.micLabel)
      .replace('CAM_LABEL', opts.camLabel)
      .replace('MORE_LABEL', opts.moreLabel)}
    <div jscontroller="soHxf" class="cta-row">
      <button${jsname}${aria} class="UywwFc-LgbsSe" data-cta="1">
        <div class="UywwFc-Bz112c"></div><span jsname="V67aGc">${opts.ctaText}</span>
      </button>
    </div>
    ${LOBBY_SECONDARY_OPTIONS(opts.phoneLabel, opts.castLabel)}
    ${opts.extra || ''}
  </div>
</body></html>`;
}

const HU = {
  lang: 'hu',
  ctaText: 'Kérvényezés a csatlakozásra',
  micLabel: 'Mikrofon kikapcsolása',
  camLabel: 'Kamera kikapcsolása',
  moreLabel: 'További beállítások',
  phoneLabel: 'Csatlakozás telefonnal',
  castLabel: 'Értekezlet átküldése',
};
const EN = {
  lang: 'en',
  ctaText: 'Ask to join',
  micLabel: 'Turn off microphone',
  camLabel: 'Turn off camera',
  moreLabel: 'More options',
  phoneLabel: 'Join and use a phone for audio',
  castLabel: 'Cast this meeting',
};

// #846 variant: Hungarian lobby, CTA carries BOTH jsname and an accessible label.
const HU_LABELLED_CTA = lobby({ ...HU, ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf' });
// The other way the shipped entry misses: CTA with an accessible label and no jsname.
const HU_NO_JSNAME_CTA = lobby({ ...HU, ctaAriaLabel: HU.ctaText });
// Negative control: today's happy path — English lobby, unlabelled CTA.
const EN_PLAIN_CTA = lobby({ ...EN, ctaJsname: 'Qx7uuf' });
// The shape production actually wins on (7/7 joins on 2026-07-20 matched the
// English literal, never the structural entry): English lobby, aria-labelled CTA.
const EN_LABELLED_CTA = lobby({ ...EN, ctaAriaLabel: EN.ctaText, ctaJsname: 'Qx7uuf' });
// Over-match guard: a second text-only button in the same lobby.
const HU_AMBIGUOUS = lobby({
  ...HU, ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf',
  extra: '<div class="dlg"><button jsname="Pw3Ldc">Mégse</button></div>',
});
// Prose button: long text is not a CTA label.
const HU_PROSE_ONLY = lobby({
  ...HU, ctaText: 'x', ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf',
  extra: '',
}).replace(
  '<span jsname="V67aGc">x</span>',
  '<span jsname="V67aGc">A csatlakozáshoz fogadd el a felvételkészítésre vonatkozó feltételeket</span>',
);

// ── jsdom harness ───────────────────────────────────────────────────────────
// The scan reads `document` (it must, to survive serialization into
// page.evaluate), so the document under test is installed as the global. Layout
// is stubbed because jsdom does not lay out: every element is 120x40 unless it
// opts out with data-offscreen, which reproduces the invisible-button case.
function mount(html: string): Document {
  const dom = new JSDOM(html);
  const El = dom.window.Element as any;
  El.prototype.getBoundingClientRect = function () {
    const off = this.getAttribute && this.getAttribute('data-offscreen') !== null;
    const w = off ? 0 : 120, h = off ? 0 : 40;
    return { width: w, height: h, top: 0, left: 0, right: w, bottom: h, x: 0, y: 0, toJSON() { return {}; } };
  };
  (globalThis as any).document = dom.window.document;
  return dom.window.document;
}

const SCAN_OPTS = {
  iconGlyphSelector: googleLobbyIconGlyphSelectors.join(', '),
  maxLabelChars: googleLobbyCtaMaxLabelChars,
};
const scan = (html: string) => { mount(html); return findLobbyPrimaryCta(SCAN_OPTS); };

/**
 * Evaluate one SHIPPED selector against a real DOM. Playwright's engines are
 * emulated exactly where jsdom has no equivalent:
 *   `//…`            → document.evaluate (jsdom implements XPath natively)
 *   `:has-text("x")` → substring, case-insensitive, whitespace-normalized
 *   everything else  → jsdom's real CSS engine (`:has()`, `:not()` included)
 */
function selectorMatches(doc: Document, selector: string): Element[] {
  if (selector.startsWith('//')) {
    const r = doc.evaluate(selector, doc, null, 7 /* ORDERED_NODE_SNAPSHOT_TYPE */, null);
    const out: Element[] = [];
    for (let i = 0; i < r.snapshotLength; i++) out.push(r.snapshotItem(i) as Element);
    return out;
  }
  const m = /^(.*):has-text\("(.+)"\)$/.exec(selector);
  if (m) {
    const needle = m[2].toLowerCase();
    return Array.from(doc.querySelectorAll(m[1] || '*')).filter((el) =>
      (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase().includes(needle));
  }
  return Array.from(doc.querySelectorAll(selector));
}

/** What the SHIPPED selector list alone resolves on this DOM, in list order. */
function resolveBySelectorsOnly(html: string): { el: Element | null; selector: string | null } {
  const doc = mount(html);
  for (const sel of googleJoinButtonSelectors) {
    const hits = selectorMatches(doc, sel);
    if (hits.length > 0) return { el: hits[0], selector: sel };
  }
  return { el: null, selector: null };
}

const isCta = (el: Element | null) => el !== null && el.getAttribute('data-cta') === '1';

(async () => {
  console.log('\n=== 1. The #846 gap — the shipped selector list is blind to an aria-labelled non-English CTA ===');
  {
    const hu = resolveBySelectorsOnly(HU_LABELLED_CTA);
    assert(hu.el === null,
      'hu lobby, aria-labelled CTA: NO selector in googleJoinButtonSelectors matches (this is the reported failure)');

    const huNoJsname = resolveBySelectorsOnly(HU_NO_JSNAME_CTA);
    assert(huNoJsname.el === null,
      'hu lobby, CTA without jsname: NO selector matches either (the other way the structural entry misses)');

    // The English literal is what production actually wins on — pin it, so a
    // future edit cannot quietly remove the only entry that works today.
    const en = resolveBySelectorsOnly(EN_PLAIN_CTA);
    assert(isCta(en.el), `en lobby: selector list still resolves the CTA (via ${en.selector})`);

    const enLabelled = resolveBySelectorsOnly(EN_LABELLED_CTA);
    assert(isCta(enLabelled.el) && enLabelled.selector === '//button[.//span[text()="Ask to join"]]',
      'en lobby, aria-labelled CTA: resolved by the English literal — the entry production wins on 7/7');
  }

  console.log('\n=== 2. findLobbyPrimaryCta closes it — locale-agnostically, on the CTA itself ===');
  {
    const hu = scan(HU_LABELLED_CTA);
    assert(isCta(hu.el), 'hu lobby, aria-labelled CTA: the scan resolves the CTA (red→green for #846 A1)');
    assert(hu.labels.length === 1 && hu.labels[0] === HU.ctaText,
      `exactly one candidate, and it is the CTA label ("${hu.labels[0]}")`);

    const huNoJsname = scan(HU_NO_JSNAME_CTA);
    assert(isCta(huNoJsname.el), 'hu lobby, CTA without jsname: the scan resolves it too (no attribute dependency)');

    const en = scan(EN_PLAIN_CTA);
    assert(isCta(en.el), 'en lobby: the scan agrees with the English selector — same element (A3 negative control)');

    const enLabelled = scan(EN_LABELLED_CTA);
    assert(isCta(enLabelled.el), 'en lobby, aria-labelled CTA: the scan lands on the same element the literal does');
  }

  console.log('\n=== 3. Over-match guards — what the scan must NOT pick ===');
  {
    const hu = scan(HU_LABELLED_CTA);
    assert(!hu.labels.some((t) => [HU.micLabel, HU.camLabel, HU.moreLabel].includes(t)),
      'mic / camera / 3-dot menu are never candidates (icon-only, in any language)');
    assert(!hu.labels.some((t) => [HU.phoneLabel, HU.castLabel].includes(t)),
      'secondary joining options are never candidates (icon + text, in any language)');

    const ambiguous = scan(HU_AMBIGUOUS);
    assert(ambiguous.el === null,
      'two text-only buttons → resolves NOTHING (refuses to guess; cannot click the wrong control)');
    assert(ambiguous.labels.length === 2 && ambiguous.labels.includes('Mégse'),
      `ambiguity is reported, not swallowed: [${ambiguous.labels.join(' | ')}]`);

    const prose = scan(HU_PROSE_ONLY);
    assert(prose.el === null, 'a long prose button is not a CTA label (over maxLabelChars)');

    // An icon button whose glyph renders as bare ligature text (no <i>/<svg> to
    // key on) must not read as a labelled button. Underscored ligatures are
    // filtered outright; a single-word one survives the filter and is caught by
    // the uniqueness rule instead. Either way it never gets clicked.
    const ligature = scan(lobby({
      ...HU, ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf',
      extra: '<button jsname="Zz1" aria-label="Feliratok">closed_caption</button>',
    }));
    assert(isCta(ligature.el) && !ligature.labels.includes('closed_caption'),
      'an underscored material ligature is filtered out; the CTA is still resolved');

    const bareLigature = scan(lobby({
      ...HU, ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf',
      extra: '<button jsname="Zz1" aria-label="Mikrofon">mic</button>',
    }));
    assert(bareLigature.el === null && bareLigature.labels.includes('mic'),
      'a single-word ligature becomes a second candidate → the pick is refused, not guessed');

    const offscreen = scan(lobby({ ...HU, ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf' })
      .replace('data-cta="1"', 'data-cta="1" data-offscreen'));
    assert(offscreen.el === null, 'a zero-size (not rendered) CTA is not resolved — visibility still gates the pick');

    const disabled = scan(lobby({ ...HU, ctaAriaLabel: HU.ctaText, ctaJsname: 'Qx7uuf' })
      .replace('data-cta="1"', 'data-cta="1" disabled'));
    assert(disabled.el === null, 'a disabled CTA is not resolved');

    const iconsOnly = scan(`<!doctype html><html lang="hu"><body>${
      LOBBY_ICON_CHROME.replace('MIC_LABEL', HU.micLabel).replace('CAM_LABEL', HU.camLabel).replace('MORE_LABEL', HU.moreLabel)
    }</body></html>`);
    assert(iconsOnly.el === null && iconsOnly.labels.length === 0,
      'a lobby of icon buttons only → no candidates at all (the scan is not a catch-all)');
  }

  console.log('\n=== 4. Ordered resolution — the selector list is honoured top-down ===');
  {
    // Both the structural entry and the English literal are "visible" here. The
    // resolver must return the EARLIER one deterministically; the previous
    // parallel race made this a coin flip, which is how a broad entry can beat a
    // precise one (#600's failure class).
    const both = [googleJoinButtonSelectors[0], '//button[.//span[text()="Ask to join"]]'];
    const hit = await waitForAnySelector(mockPage({ visible: both }) as any, googleJoinButtonSelectors, 5000, 'join button');
    assert(hit.selector === googleJoinButtonSelectors[0],
      `earlier selector wins when several match ("${hit.selector}")`);

    // And a later entry still wins when it is the only match.
    const late = await waitForAnySelector(
      mockPage({ visible: ['button:has-text("Join now")'] }) as any, googleJoinButtonSelectors, 5000, 'join button');
    assert(late.selector === 'button:has-text("Join now")', 'a later-only match is still resolved');

    // The selector list is checked BEFORE the scan on every poll, so the scan
    // cannot overrule a lobby the list can name.
    const listWins = await waitForLobbyCta(
      mockPage({ visible: ['button:has-text("Ask to join")'], scanLabels: ['Ask to join'], scanHits: true }) as any,
      googleJoinButtonSelectors, 5000, 'join button');
    assert(listWins.selector === 'button:has-text("Ask to join")',
      'the structural scan never overrules a selector the list can name');
  }

  console.log('\n=== 5. A total miss is diagnosable from last_error alone (#846 A4) ===');
  {
    let message = '';
    try {
      await waitForLobbyCta(
        mockPage({ visible: [], scanLabels: ['Kérvényezés a csatlakozásra', 'Mégse'], lang: 'hu', nav: 'hu-HU' }) as any,
        googleJoinButtonSelectors, 900, 'join button');
    } catch (e: any) { message = String(e?.message || e); }

    assert(/^Could not locate join button by any locale-agnostic or English selector after 900ms/.test(message),
      'the historical error prefix is preserved verbatim (prod monitoring greps it)');
    assert(/html\.lang=hu/.test(message) && /navigator\.language=hu-HU/.test(message),
      'the observed UI locale is recorded in the error');
    assert(/url=https:\/\/meet\.google\.com\//.test(message), 'the observed URL is recorded in the error');
    assert(/Mégse/.test(message) && /Kérvényezés/.test(message),
      'the visible text-button labels are recorded — the next occurrence is a one-look diagnosis');
    console.log(`    last_error.reason would read: ${message}`);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();

// ── Minimal Playwright-Page stand-in ────────────────────────────────────────
// Only the surface the resolvers touch: ordered locator visibility, the
// structural scan handle, the failure screenshot and the locale probe.
function mockPage(m: {
  visible: string[]; scanLabels?: string[]; scanHits?: boolean; lang?: string; nav?: string;
}) {
  const el = { __element: true };
  return {
    url: () => 'https://meet.google.com/abc-defg-hij',
    locator: (sel: string) => ({
      first: () => ({
        isVisible: async () => m.visible.includes(sel),
        elementHandle: async () => (m.visible.includes(sel) ? el : null),
      }),
    }),
    waitForTimeout: async (_ms: number) => { await new Promise((r) => setTimeout(r, 1)); },
    screenshot: async (_o: any) => {},
    evaluate: async (_fn: any) => ({ lang: m.lang || 'en', nav: m.nav || 'en-US' }),
    evaluateHandle: async (_fn: any, _opts: any) => ({
      getProperty: async (k: string) => ({
        jsonValue: async () => (k === 'labels' ? (m.scanLabels || []) : null),
        asElement: () => (k === 'el' && m.scanHits ? el : null),
      }),
      dispose: async () => {},
    }),
  };
}
