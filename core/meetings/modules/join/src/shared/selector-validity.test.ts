/**
 * Selector-validity gate — every selector array in this module must parse as a
 * VALID Playwright selector.
 *
 * WHY: the detector loops (admission / rejection / waiting / removal) wrap each
 * page.locator(sel).isVisible() in try/catch-continue. An INVALID selector
 * (e.g. the former `text*="…"` entries — `text*` is not a Playwright engine)
 * throws InvalidSelectorError on EVERY call and is silently skipped: a dead
 * selector that ships unnoticed, because the fabricated-DOM test mocks treat
 * selector strings as opaque keys and stay green. This gate makes that class
 * of bug fail loudly — no browser needed.
 *
 * HOW: playwright-core's server-side `Selectors.parseSelector` performs the
 * exact parse + engine validation the live locator path runs before touching
 * the page. playwright-core is resolved THROUGH the declared `playwright`
 * dependency, so validation always happens against the engine version this
 * module actually ships with (its exports map hides lib/server/selectors.js,
 * hence the two-step package.json resolution + direct file require).
 *
 * SCOPE: exported arrays whose name ends in `Selectors` or `Indicators` are
 * Playwright locator selectors. `*Texts` / `*ClassNames` exports are raw
 * strings consumed inside page.evaluate() / textContent matching — NOT
 * locator selectors — and are excluded on purpose.
 *
 * Run: npx tsx src/shared/selector-validity.test.ts
 */

import * as path from 'path';
import * as googlemeet from '../googlemeet/selectors';
import * as msteams from '../msteams/selectors';
import * as zoom from '../zoom/selectors';

const playwrightPkgDir = path.dirname(require.resolve('playwright/package.json'));
const coreDir = path.dirname(
  require.resolve('playwright-core/package.json', { paths: [playwrightPkgDir] }),
);
const { Selectors } = require(path.join(coreDir, 'lib', 'server', 'selectors.js'));
const validator = new Selectors([], undefined);

const SELECTOR_ARRAY = /(Selectors|Indicators)$/;
const modules: Record<string, Record<string, unknown>> = { googlemeet, msteams, zoom };

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

console.log(
  `\n=== selector-validity: ${checked} selectors in ${arrays} arrays — ` +
  `${checked - invalid} valid, ${invalid} invalid ===`,
);
process.exit(invalid > 0 || gateBroken ? 1 : 0);
