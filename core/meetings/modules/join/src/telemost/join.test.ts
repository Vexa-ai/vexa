/**
 * Standalone test for buildTelemostMeetingUrl / isTelemostHost — the URL
 * builder the brick uses before navigating to a Telemost meeting. Covers the
 * public hosts, the 360-for-Business host, canonicalisation (query/hash
 * dropped, host lower-cased), and the refusals (foreign host, no /j/<id>).
 *
 * Run: npx tsx src/telemost/join.test.ts
 */

import { buildTelemostMeetingUrl, isTelemostHost } from './join';

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

console.log('\n=== buildTelemostMeetingUrl — public hosts ===');

expect(
  'canonical link is returned unchanged',
  buildTelemostMeetingUrl('https://telemost.yandex.ru/j/12345678901234'),
  'https://telemost.yandex.ru/j/12345678901234',
);

expect(
  '360-for-Business host is preserved',
  buildTelemostMeetingUrl('https://telemost.360.yandex.ru/j/12345678901234'),
  'https://telemost.360.yandex.ru/j/12345678901234',
);

expect(
  'query and hash are dropped, trailing slash tolerated',
  buildTelemostMeetingUrl('https://telemost.yandex.ru/j/12345678901234/?utm_source=mail#x'),
  'https://telemost.yandex.ru/j/12345678901234',
);

expect(
  'address-bar form /@/j/<id> is canonicalised',
  buildTelemostMeetingUrl('https://telemost.yandex.ru/@/j/12345678901234'),
  'https://telemost.yandex.ru/j/12345678901234',
);

expect(
  'host is lower-cased',
  buildTelemostMeetingUrl('https://Telemost.Yandex.RU/j/12345678901234'),
  'https://telemost.yandex.ru/j/12345678901234',
);

console.log('\n=== buildTelemostMeetingUrl — refusals ===');

expectThrows('foreign host', () => buildTelemostMeetingUrl('https://meet.jit.si/j/123'), 'Not a Telemost host');
expectThrows('look-alike host', () => buildTelemostMeetingUrl('https://telemost.yandex.ru.evil.example/j/123'), 'Not a Telemost host');
expectThrows('no meeting id', () => buildTelemostMeetingUrl('https://telemost.yandex.ru/'), 'expected /j/<digits>');
expectThrows('non-numeric id', () => buildTelemostMeetingUrl('https://telemost.yandex.ru/j/abc'), 'expected /j/<digits>');
expectThrows('not a URL', () => buildTelemostMeetingUrl('telemost 123'), 'Invalid Telemost meeting URL');

console.log('\n=== isTelemostHost ===');

expect('telemost.yandex.ru', isTelemostHost('telemost.yandex.ru'), true);
expect('telemost.360.yandex.ru', isTelemostHost('telemost.360.yandex.ru'), true);
expect('yandex.ru is not telemost', isTelemostHost('yandex.ru'), false);
expect('suffix look-alike is not telemost', isTelemostHost('xtelemost.yandex.ru'), false);

console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
