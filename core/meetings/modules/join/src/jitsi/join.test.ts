/**
 * Standalone test for buildJitsiMeetingUrl — the URL builder the brick uses
 * before navigating to a Jitsi meeting. Covers the canonical public instance
 * (meet.jit.si), self-hosted deployments, hash-config appending, and the
 * embedder-override-wins rule.
 *
 * Run: npx tsx src/jitsi/join.test.ts
 */

import { buildJitsiMeetingUrl } from './join';

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

console.log('\n=== buildJitsiMeetingUrl — canonical meet.jit.si ===');

expect(
  'plain room — mutes appended',
  buildJitsiMeetingUrl('https://meet.jit.si/MyRoom'),
  'https://meet.jit.si/MyRoom#config.startWithAudioMuted=true&config.startWithVideoMuted=true',
);

expect(
  'bot name — displayName appended as a quoted, URI-encoded JSON string',
  buildJitsiMeetingUrl('https://meet.jit.si/MyRoom', { botName: 'Vexa Bot' }),
  'https://meet.jit.si/MyRoom#config.startWithAudioMuted=true&config.startWithVideoMuted=true&userInfo.displayName=%22Vexa%20Bot%22',
);

console.log('\n=== buildJitsiMeetingUrl — self-hosted deployments ===');

expect(
  'self-hosted host is preserved (never rewritten)',
  buildJitsiMeetingUrl('https://jitsi.example.org/TeamSync'),
  'https://jitsi.example.org/TeamSync#config.startWithAudioMuted=true&config.startWithVideoMuted=true',
);

expect(
  'room on a sub-path is preserved',
  buildJitsiMeetingUrl('https://example.org/jitsi/TeamSync'),
  'https://example.org/jitsi/TeamSync#config.startWithAudioMuted=true&config.startWithVideoMuted=true',
);

expect(
  'trailing slash on the room is tolerated',
  buildJitsiMeetingUrl('https://meet.jit.si/MyRoom/'),
  'https://meet.jit.si/MyRoom/#config.startWithAudioMuted=true&config.startWithVideoMuted=true',
);

console.log('\n=== buildJitsiMeetingUrl — embedder overrides win ===');

expect(
  'existing hash params are preserved, ours appended',
  buildJitsiMeetingUrl('https://meet.jit.si/MyRoom#config.p2p.enabled=false'),
  'https://meet.jit.si/MyRoom#config.p2p.enabled=false&config.startWithAudioMuted=true&config.startWithVideoMuted=true',
);

expect(
  'an explicit startWithAudioMuted override is NOT duplicated',
  buildJitsiMeetingUrl('https://meet.jit.si/MyRoom#config.startWithAudioMuted=false'),
  'https://meet.jit.si/MyRoom#config.startWithAudioMuted=false&config.startWithVideoMuted=true',
);

expect(
  'an explicit displayName override is NOT duplicated',
  buildJitsiMeetingUrl('https://meet.jit.si/MyRoom#userInfo.displayName=%22Custom%22', { botName: 'Vexa' }),
  'https://meet.jit.si/MyRoom#userInfo.displayName=%22Custom%22&config.startWithAudioMuted=true&config.startWithVideoMuted=true',
);

console.log('\n=== buildJitsiMeetingUrl — negative cases ===');

expectThrows(
  'bare origin without a room — throws',
  () => buildJitsiMeetingUrl('https://meet.jit.si/'),
  'Cannot extract room',
);

expectThrows(
  'bare origin without any path — throws',
  () => buildJitsiMeetingUrl('https://meet.jit.si'),
  'Cannot extract room',
);

expectThrows(
  'not a URL — throws',
  () => buildJitsiMeetingUrl('not a url'),
  'Invalid Jitsi meeting URL',
);

console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
