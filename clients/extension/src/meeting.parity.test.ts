/**
 * Teams native-id parity (card 4faaf434c186): detectMeeting against the vector table meeting-api's
 * link parser is held to as well (core/meetings/services/meeting-api/tests/fixtures/
 * teams-native-id-vectors.json; this copy is kept byte-identical by parity fact
 * teams-native-id-vectors). Each row states the server's answer and the extension's; this suite
 * pins the extension's column, and the two agree on every row that does not name a `gap`.
 * Pure: no chrome. Run: npx tsx src/meeting.parity.test.ts
 */
import { detectMeeting } from './meeting.js';
import table from './teams-native-id-vectors.json';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

type Answer = [string, string] | null;
interface Row { url: string; server: Answer; extension: Answer; gap?: string }

const same = (a: Answer, b: Answer): boolean => JSON.stringify(a) === JSON.stringify(b);

for (const row of (table as { vectors: Row[] }).vectors) {
  const got = detectMeeting(row.url);
  const answer: Answer = got ? [got.platform, got.nativeMeetingId] : null;
  check(`extension column: ${row.url}`, same(answer, row.extension), JSON.stringify(answer));
  if (!row.gap) check(`agrees with meeting-api: ${row.url}`, same(row.server, row.extension), JSON.stringify(row));
}

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  (globalThis as any).process.exit(1);
}
console.log('\nall Teams native-id parity checks passed');
