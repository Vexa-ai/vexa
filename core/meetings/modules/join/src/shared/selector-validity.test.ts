/**
 * Selector-validity gate — every selector array in this module must parse as a
 * VALID selector FOR THE EXECUTION CONTEXT it runs in.
 *
 * WHY: the detector loops (admission / rejection / waiting / removal) wrap each
 * page.locator(sel).isVisible() in try/catch-continue. An INVALID selector
 * (e.g. the former `text*="…"` entries — `text*` is not a Playwright engine)
 * throws InvalidSelectorError on EVERY call and is silently skipped: a dead
 * selector that ships unnoticed, because the fabricated-DOM test mocks treat
 * selector strings as opaque keys and stay green. This gate makes that class
 * of bug fail loudly — no browser needed.
 *
 * Validity is PER EXECUTION CONTEXT, not per engine (#542): `:has-text()`
 * parses fine as a Playwright selector yet throws SyntaxError in
 * document.querySelector, so a browser-context array full of it ships green
 * under a Playwright-only parse while every entry is dead. Hence two lanes:
 *
 * LANE 1 — Playwright (locator) arrays. HOW: playwright-core's server-side
 * `Selectors.parseSelector` performs the exact parse + engine validation the
 * live locator path runs before touching the page. playwright-core is resolved
 * THROUGH the declared `playwright` dependency, so validation always happens
 * against the engine version this module actually ships with (its exports map
 * hides lib/server/selectors.js, hence the two-step package.json resolution +
 * direct file require). SCOPE: exported arrays whose name ends in `Selectors`
 * or `Indicators`. `*Texts` / `*ClassNames` exports are raw strings consumed
 * inside page.evaluate() / textContent matching — NOT locator selectors — and
 * are excluded on purpose.
 *
 * LANE 2 — browser-context arrays. Each platform's selectors.ts DECLARES the
 * arrays it ships into page.evaluate in a `browserContextSelectorArrays`
 * export (names, not values — the declaration travels with the arrays it
 * covers). Those run through document.querySelector, so every entry — a plain
 * CSS string or the `css` field of a `{ css?, text? }` button matcher — must
 * additionally parse as REAL CSS, validated here with jsdom's querySelector (a
 * real CSS engine, no browser needed). `text` fields are raw strings matched
 * against textContent in-page, never parsed as selectors.
 *
 * Run: npx tsx src/shared/selector-validity.test.ts
 */

import * as path from 'path';
import { JSDOM } from 'jsdom';
import * as googlemeet from '../googlemeet/selectors';
import * as msteams from '../msteams/selectors';
import * as zoom from '../zoom/selectors';
import * as jitsi from '../jitsi/selectors';

const playwrightPkgDir = path.dirname(require.resolve('playwright/package.json'));
const coreDir = path.dirname(
  require.resolve('playwright-core/package.json', { paths: [playwrightPkgDir] }),
);
const { Selectors } = require(path.join(coreDir, 'lib', 'server', 'selectors.js'));
const validator = new Selectors([], undefined);

const SELECTOR_ARRAY = /(Selectors|Indicators)$/;
const modules: Record<string, Record<string, unknown>> = { googlemeet, msteams, zoom, jitsi };

let checked = 0;
let arrays = 0;
let invalid = 0;
let gateBroken = false;

for (const [modName, mod] of Object.entries(modules)) {
  for (const [exportName, value] of Object.entries(mod)) {
    if (!SELECTOR_ARRAY.test(exportName) || !Array.isArray(value)) continue;
    arrays++;
    for (const sel of value as string[]) {
      checked++;
      try {
        validator.parseSelector(sel, false);
      } catch (e: any) {
        invalid++;
        console.log(
          `  \x1b[31mINVALID\x1b[0m ${modName}.${exportName} :: ${sel}\n` +
          `          ${String(e?.message || e).split('\n')[0]}`,
        );
      }
    }
  }
}

// Self-check: the gate must never pass by validating nothing (e.g. after an
// export rename stops the suffix filter from matching anything real).
if (arrays < 10 || checked < 100) {
  gateBroken = true;
  console.log(
    `  \x1b[31mFAIL\x1b[0m gate self-check: only ${arrays} arrays / ${checked} selectors ` +
    `matched the (Selectors|Indicators)$ filter — did the export naming change?`,
  );
}

// --- LANE 2: browser-context arrays must be plain CSS (document.querySelector) ---

const cssProbe = new JSDOM('<!doctype html><html><body></body></html>').window.document;

// jsdom's selector engine compiles lazily: with no candidate elements for a
// selector's indexable parts (tag/attribute/class/id), querySelector returns
// null WITHOUT parsing the pseudo-classes — an invalid entry would pass. So
// seed the probe with elements carrying every tag/attribute/class/id the
// selector mentions before querying; then a parse is guaranteed.
const cssError = (sel: string): string | null => {
  let m: RegExpExecArray | null;
  const tags = new Set(['div']);
  const tagRe = /(?:^|[\s>+~,(])([a-zA-Z][a-zA-Z0-9-]*)/g;
  while ((m = tagRe.exec(sel))) tags.add(m[1].toLowerCase());
  const attrs: Array<[string, string]> = [];
  const attrRe = /\[\s*([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:[*^$|~]?=\s*("([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]/g;
  while ((m = attrRe.exec(sel))) attrs.push([m[1], m[3] ?? m[4] ?? m[5] ?? 'probe']);
  const classes: string[] = [];
  const classRe = /\.([a-zA-Z_-][a-zA-Z0-9_-]*)/g;
  while ((m = classRe.exec(sel))) classes.push(m[1]);
  const idRe = /#([a-zA-Z_-][a-zA-Z0-9_-]*)/.exec(sel);
  const host = cssProbe.createElement('div');
  for (const t of tags) {
    let el: Element;
    try { el = cssProbe.createElement(t); } catch { continue; }
    for (const [a, v] of attrs) { try { el.setAttribute(a, v); } catch { /* unseedable attr name */ } }
    if (classes.length) el.setAttribute('class', classes.join(' '));
    if (idRe) el.setAttribute('id', idRe[1]);
    host.appendChild(el);
  }
  cssProbe.body.appendChild(host);
  try { cssProbe.querySelector(sel); return null; }
  catch (e: any) { return String(e?.message || e).split('\n')[0]; }
  finally { host.remove(); }
};

let cssArrays = 0;
let cssChecked = 0;

for (const [modName, mod] of Object.entries(modules)) {
  const declared = (mod as Record<string, unknown>).browserContextSelectorArrays;
  if (declared === undefined) continue;
  for (const arrayName of declared as string[]) {
    const value = (mod as Record<string, unknown>)[arrayName];
    if (!Array.isArray(value)) {
      // A stale declaration would silently shrink the gate's coverage — red it.
      invalid++;
      console.log(
        `  \x1b[31mINVALID\x1b[0m ${modName}.browserContextSelectorArrays names ` +
        `'${arrayName}', but no such array is exported (stale declaration)`,
      );
      continue;
    }
    cssArrays++;
    for (const entry of value as Array<string | { css?: string; text?: string }>) {
      const css = typeof entry === 'string' ? entry : entry?.css;
      const text = typeof entry === 'string' ? undefined : entry?.text;
      if (css === undefined && text === undefined) {
        invalid++;
        console.log(`  \x1b[31mINVALID\x1b[0m ${modName}.${arrayName} :: empty matcher (no css, no text)`);
        continue;
      }
      if (css === undefined) continue; // pure text matcher — raw string, not a selector
      cssChecked++;
      const err = cssError(css);
      if (err !== null) {
        invalid++;
        console.log(
          `  \x1b[31mINVALID(css)\x1b[0m ${modName}.${arrayName} :: ${css}\n` +
          `          browser-context array, but not valid CSS: ${err}`,
        );
      }
    }
  }
}

// Negative control: the CSS lane must actually reject Playwright-only syntax —
// if this probe ever passes, the lane has gone blind and the gate is broken.
if (cssError('button:has-text("probe")') === null) {
  gateBroken = true;
  console.log(
    '  \x1b[31mFAIL\x1b[0m css-lane self-check: `:has-text()` was accepted as CSS — ' +
    'the browser-context lane no longer rejects Playwright-only syntax',
  );
}

// Self-check: at least one browser-context array must be declared and carry
// CSS entries (the leave matchers) — a deleted declaration must not pass green.
if (cssArrays < 1 || cssChecked < 10) {
  gateBroken = true;
  console.log(
    `  \x1b[31mFAIL\x1b[0m css-lane self-check: only ${cssArrays} declared arrays / ` +
    `${cssChecked} css entries — did browserContextSelectorArrays disappear?`,
  );
}

console.log(
  `\n=== selector-validity: ${checked} playwright selectors in ${arrays} arrays + ` +
  `${cssChecked} css entries in ${cssArrays} browser-context arrays — ` +
  `${invalid} invalid ===`,
);
process.exit(invalid > 0 || gateBroken ? 1 : 0);
