/**
 * Teams meeting-id lengths — the extension recognises the same 10-16 digit short ids
 * meeting-api and the MCP's link parser accept (parity fact `teams-meeting-id-length`).
 * Pure: no chrome.
 * Run: npx tsx src/meeting.test.ts
 */
import { detectMeeting } from './meeting.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const id = (n: number): string => '1234567890123456789'.slice(0, n);

for (const n of [10, 15, 16]) {
  for (const url of [
    `https://teams.microsoft.com/meet/${id(n)}?p=abc`,
    `https://teams.live.com/meet/${id(n)}`,
    `https://teams.microsoft.com/v2/?meetingjoin=true#/meet/${id(n)}?p=abc`,
  ]) {
    const got = detectMeeting(url);
    check(`${n} digits → teams ${url}`, got?.platform === 'teams' && got.nativeMeetingId === id(n),
      JSON.stringify(got));
  }
}
for (const n of [9, 17]) {
  for (const url of [
    `https://teams.microsoft.com/meet/${id(n)}`,
    `https://teams.microsoft.com/v2/#/meet/${id(n)}`,
  ]) {
    check(`${n} digits → not a meeting ${url}`, detectMeeting(url) === null,
      JSON.stringify(detectMeeting(url)));
  }
}

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log('\nall Teams id checks passed');
