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

console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
