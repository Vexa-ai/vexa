/**
 * Teams in-meeting mic control — the selectors a voice bot reads before it speaks (#1866).
 *
 * Live (Teams web, Oct 2026): the toolbar toggle was `<button aria-label="Mute mic">` with none of the
 * older ids, so the bot found no control and refused every line (`mic_unverified`). These fixtures run
 * teamsMicButtonSelectors through jsdom's CSS engine, as page.evaluate does.
 *
 * Run: npx tsx src/msteams/mic.test.ts
 */

import { JSDOM } from 'jsdom';
import { teamsMicButtonSelectors } from './selectors';

let passed = 0, failed = 0;
function check(name: string, actual: unknown, expected: unknown) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`); failed++; }
}

/** The aria-label of the first element the selectors match, in selector order. */
function firstMatch(html: string): string | null {
  const doc = new JSDOM(`<!doctype html><html><body>${html}</body></html>`).window.document;
  for (const sel of teamsMicButtonSelectors) {
    const el = doc.querySelector(sel);
    if (el) return el.getAttribute('aria-label') ?? el.id;
  }
  return null;
}

const decoys = '<button aria-label="Open audio options"></button><button aria-label="Mic options"></button>';

check('current toolbar, mic on: "Mute mic" is found', firstMatch(`${decoys}<button aria-label="Mute mic"></button>`), 'Mute mic');
check('current toolbar, mic off: "Unmute mic" is found', firstMatch(`${decoys}<button aria-label="Unmute mic"></button>`), 'Unmute mic');
check('shortcut-labelled toggle is found', firstMatch(`${decoys}<button aria-label="Unmute (Ctrl+Shift+M)"></button>`), 'Unmute (Ctrl+Shift+M)');
check('legacy #microphone-button is still found', firstMatch(`<button id="microphone-button" aria-label="Mute"></button>`), 'Mute');
check('the options buttons alone match nothing', firstMatch(decoys), null);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
