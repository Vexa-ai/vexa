/**
 * Standalone test for buildZoomWebClientUrl — the URL parser the bot uses
 * before navigating to a Zoom meeting. Covers v0.10.5 white-label / enterprise
 * portal support (LFX, AWS Chime, Bloomberg, etc.).
 *
 * Run: npx tsx services/vexa-bot/core/src/platforms/zoom/web/join.test.ts
 */

import { buildZoomWebClientUrl } from './join';

let passed = 0;
let failed = 0;

function expect(name: string, actual: any, expected: any) {
  if (actual === expected) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  } else {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
    console.log(`        expected: ${JSON.stringify(expected)}`);
    console.log(`        actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

function expectThrows(name: string, fn: () => any, msgMatch?: string) {
  try {
    fn();
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected throw, got value)`);
    failed++;
  } catch (e: any) {
    if (msgMatch && !String(e.message).includes(msgMatch)) {
      console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (wrong message: ${e.message})`);
      failed++;
      return;
    }
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  }
}

console.log('\n=== buildZoomWebClientUrl — canonical Zoom URLs ===');

expect(
  'us05web subdomain',
  buildZoomWebClientUrl('https://us05web.zoom.us/j/85173157171?pwd=secret'),
  'https://app.zoom.us/wc/85173157171/join?pwd=secret',
);

expect(
  'plain zoom.us',
  buildZoomWebClientUrl('https://zoom.us/j/84335626851?pwd=abc123'),
  'https://app.zoom.us/wc/84335626851/join?pwd=abc123',
);

expect(
  'no passcode',
  buildZoomWebClientUrl('https://zoom.us/j/84335626851'),
  'https://app.zoom.us/wc/84335626851/join',
);

expect(
  'already web client URL — passthrough',
  buildZoomWebClientUrl('https://app.zoom.us/wc/85173157171/join?pwd=secret'),
  'https://app.zoom.us/wc/85173157171/join?pwd=secret',
);

expect(
  'events.zoom.us — passthrough',
  buildZoomWebClientUrl('https://events.zoom.us/ejl/AbCdEf123'),
  'https://events.zoom.us/ejl/AbCdEf123',
);

console.log('\n=== buildZoomWebClientUrl — v0.10.5 white-label passthrough ===');

// The exact URL the user reported. We deliberately do NOT rewrite it —
// the LFX portal often shows an extra page (T&C / guest-name confirm /
// captcha) before redirecting to Zoom, and a human VNC'd into the bot's
// browser needs to be able to click through it. Canonical zoom.us paths
// stay rewritten because they have no portal layer.
const LFX_URL =
  'https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=c9e528a8-3852-4b82-89c2-96d6f22526ad';

expect(
  'LFX zoom-portal — passthrough so user can VNC into portal page',
  buildZoomWebClientUrl(LFX_URL),
  LFX_URL,
);

expect(
  'corporate subdomain (amazon.zoom.us with /j/) — canonical wins',
  buildZoomWebClientUrl('https://amazon.zoom.us/j/85173157171?pwd=corp'),
  'https://app.zoom.us/wc/85173157171/join?pwd=corp',
);

expect(
  'white-label /m/ path — passthrough',
  buildZoomWebClientUrl('https://corp.example.com/m/85173157171?password=xyz'),
  'https://corp.example.com/m/85173157171?password=xyz',
);

expect(
  'white-label without passcode — passthrough',
  buildZoomWebClientUrl('https://portal.example.org/meeting/96088138284'),
  'https://portal.example.org/meeting/96088138284',
);

expect(
  'tricky: zoom-lfx.platform.linuxfoundation.org is NOT *.zoom.us',
  // Substring "zoom" in hostname — must NOT count as canonical
  buildZoomWebClientUrl('https://zoom-something.example.com/meeting/96088138284'),
  'https://zoom-something.example.com/meeting/96088138284',
);

console.log('\n=== buildZoomWebClientUrl — negative cases (canonical-only) ===');

// White-label URLs are no longer parsed — they pass through. Only canonical
// zoom.us / *.zoom.us URLs that we attempted to rewrite can throw.
expectThrows(
  'canonical zoom.us without /j/ — throws',
  () => buildZoomWebClientUrl('https://zoom.us/some-other-path'),
  'Cannot extract meeting ID',
);

expect(
  'unknown host without numeric — passthrough (not our concern)',
  buildZoomWebClientUrl('https://example.com/just-a-bare-page'),
  'https://example.com/just-a-bare-page',
);

// ============================================================================
// v0.10.5 audio_join_failed escalation — structural regression check
// ============================================================================
//
// prepare.ts wraps a 3-attempt audio-join loop in zoom-web. When all 3
// attempts fail, the bot was silently flipping to "active in DB, 0
// transcripts ever produced" because the per-speaker capture pipeline
// found 0 <audio> elements (computer audio was never joined). v0.10.5
// commit 37316d6 introduced an explicit escalation: instead of silent
// failure, the bot calls callNeedsHumanHelpCallback() with a structured
// "audio_join_failed:" prefix so the dashboard surfaces a VNC link.
//
// The full-flow test would require playwright Page mocks for selectors
// + waitForTimeout + locator, plus mocking the `callNeedsHumanHelpCallback`
// network call — high friction for a regression-coverage gate. A
// source-shape check is the right tier: it pins the escalation to its
// source location + critical message-shape strings, catches deletion or
// downgrade in code review, and runs in milliseconds.

import * as fs from 'fs';
import * as path from 'path';

console.log('\n=== audio_join_failed escalation — structural regression check ===');

function expectFileContains(
  name: string,
  filePath: string,
  needle: string | RegExp,
) {
  let body: string;
  try {
    body = fs.readFileSync(filePath, 'utf-8');
  } catch (e: any) {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (cannot read ${filePath}: ${e.message})`);
    failed++;
    return;
  }
  const ok = typeof needle === 'string' ? body.includes(needle) : needle.test(body);
  if (ok) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  } else {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
    console.log(`        needle: ${needle}`);
    console.log(`        in:     ${filePath}`);
    failed++;
  }
}

const PREPARE_TS = path.join(__dirname, 'prepare.ts');

// Escalation entrypoint must exist + import must be present.
expectFileContains(
  'prepare.ts imports callNeedsHumanHelpCallback',
  PREPARE_TS,
  /callNeedsHumanHelpCallback/,
);

// The audio-join loop must escalate on failure.
expectFileContains(
  'prepare.ts contains audio_join_failed escalation log',
  PREPARE_TS,
  'audio_join_failed',
);

// The escalation message must explain the consequence (no audio elements
// → zero transcripts) so the dashboard's needs_human_help panel has
// actionable context.
expectFileContains(
  'prepare.ts escalation message references missing-audio consequence',
  PREPARE_TS,
  /no\s*<audio>\s*elements|zero\s+transcripts|Without computer audio/i,
);

// The escalation must be wrapped in try/catch so a callback failure does
// not crash the bot mid-meeting.
expectFileContains(
  'prepare.ts wraps callNeedsHumanHelpCallback in try/catch',
  PREPARE_TS,
  /try\s*\{[^}]*callNeedsHumanHelpCallback[\s\S]*?\}\s*catch/,
);

// The escalation must surface VNC instructions so the human knows what
// action to take.
expectFileContains(
  'prepare.ts escalation message references VNC link surface',
  PREPARE_TS,
  /VNC/,
);

// The escalation block must follow the 3-attempt audio-join loop —
// reverting to "fall through silently" is the regression we care about.
expectFileContains(
  'prepare.ts escalation gates on audioJoined === false',
  PREPARE_TS,
  /if\s*\(\s*!\s*audioJoined\s*\)/,
);

console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
