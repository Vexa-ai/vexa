/**
 * `resolvePlatform` matches the HOSTNAME, exactly or as a dotted subdomain — never a substring
 * of the URL.
 *
 * The Google Meet and Teams branches used to ask `meetingUrl.includes("meet.google.com")` and
 * `meetingUrl.includes("teams.microsoft.com") || …("teams.live.com")` — a test against the whole
 * URL string, which any host satisfies by carrying the platform's name in its query string, its
 * path, or as a prefix of a domain the caller registered. The resolved platform picks the join
 * flow, its selectors, and its anonymous-join assumptions, so it has to be a fact about the host.
 *
 * Same rule, same shape as `msteams/auth-redirect.test.ts`'s lookalike assertions, which already
 * guard `isMicrosoftLoginUrl` / `isTeamsMeetingUrl`; all of them now run through the one helper
 * in `shared/host-match.ts`.
 *
 * Run: npx tsx core/meetings/modules/join/src/__tests__/resolvePlatform.test.ts
 */

import { resolvePlatform, type Platform } from '../index';

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}

/** resolvePlatform(url) → the platform, or the string 'THREW' when it refused. */
function resolved(url: string): Platform | 'THREW' {
  try { return resolvePlatform(url); } catch { return 'THREW'; }
}

// ── Hosts that are NOT the platform, but carry its name ───────────────────────
// Every one of these resolved to a platform under the substring test (or would have, had the
// zoom/jitsi branches been written the same way). None may resolve to anything.
const LOOKALIKES: [string, string][] = [
  ['https://evil.example/?x=meet.google.com', 'platform name in the query string'],
  ['https://evil.example/meet.google.com/abc-defg-hij', 'platform name in the path'],
  ['https://evil.example/#meet.google.com', 'platform name in the fragment'],
  ['https://meet.google.com.attacker.example/abc-defg-hij', 'platform name as a domain prefix'],
  ['https://notmeet.google.com/abc-defg-hij', 'suffix without the label boundary'],
  ['https://meet.google.com@attacker.example/abc-defg-hij', 'platform name in the userinfo'],
  ['https://evil.example/?x=teams.microsoft.com', 'teams name in the query string'],
  ['https://teams.microsoft.com.evil.example/l/meetup-join/19%3ameeting_abc%40thread.v2/0', 'teams as a domain prefix'],
  ['https://teams.live.com.evil.example/meet/33832851446746', 'teams.live as a domain prefix'],
  ['https://notteams.live.com/meet/33832851446746', 'teams suffix without the label boundary'],
  ['https://teams.microsoft.com.us/l/meetup-join/19%3ameeting_abc%40thread.v2/0', 'lookalike TLD swap'],
  ['https://zoom.us.attacker.example/j/12345678901', 'zoom as a domain prefix'],
  ['https://notzoom.us/j/12345678901', 'zoom suffix without the label boundary'],
  ['https://meet.jit.si.attacker.example/VexaStandup', 'jitsi as a domain prefix'],
  ['https://8x8.vc.attacker.example/VexaStandup', '8x8 as a domain prefix'],
];

// ── Hosts that ARE the platform. Every shape the resolver accepted before, plus the gov/DoD and
//    cloud.microsoft Teams hosts it now shares with the join flow's own host list. ────────────
const REAL: [string, Platform][] = [
  ['https://meet.google.com/abc-defg-hij', 'google_meet'],
  ['https://meet.google.com/abc-defg-hij?authuser=0', 'google_meet'],
  ['https://MEET.GOOGLE.COM/abc-defg-hij', 'google_meet'],
  ['https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc123%40thread.v2/0?context=%7b%7d', 'teams'],
  ['https://teams.microsoft.com/meet/33832851446746?p=abc', 'teams'],
  ['https://emea.teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0', 'teams'],
  ['https://teams.live.com/meet/33832851446746?p=abc', 'teams'],
  // Aligned with msteams/auth-redirect.ts's TEAMS_HOST_SUFFIXES — the join flow already treats
  // these as "on the meeting", so the resolver no longer refuses what the flow supports.
  ['https://gov.teams.microsoft.us/l/meetup-join/19%3ameeting_abc%40thread.v2/0', 'teams'],
  ['https://dod.teams.microsoft.us/l/meetup-join/19%3ameeting_abc%40thread.v2/0', 'teams'],
  ['https://teams.cloud.microsoft/v2/?meetingjoin=true', 'teams'],
  ['https://zoom.us/j/12345678901', 'zoom'],
  ['https://us02web.zoom.us/j/12345678901?pwd=xyz', 'zoom'],
  ['https://company.zoom.us/j/12345678901', 'zoom'],
  ['https://meet.jit.si/VexaStandup', 'jitsi'],
  ['https://8x8.vc/vpaas-magic-cookie-abc123/VexaStandup', 'jitsi'],
  ['https://call.8x8.vc/VexaStandup', 'jitsi'],
];

// ── Neither: no host to speak of. Refused, as before. ────────────────────────
const GARBAGE = ['', 'not a url', 'meeting', 'https://', 'https://example.com/join/1234'];

function main() {
  console.log('\n=== resolvePlatform matches the hostname, not a substring of the URL ===\n');

  console.log('  -- lookalike hosts are refused --');
  for (const [url, why] of LOOKALIKES) {
    const got = resolved(url);
    check(`${why}: ${url}`, got === 'THREW', `resolved to "${got}"`);
  }

  console.log('\n  -- real platform hosts still resolve --');
  for (const [url, want] of REAL) {
    const got = resolved(url);
    check(`${want}: ${url}`, got === want, `resolved to "${got}"`);
  }

  console.log('\n  -- unrecognized input is refused --');
  for (const url of GARBAGE) {
    const got = resolved(url);
    check(`refused: ${JSON.stringify(url)}`, got === 'THREW', `resolved to "${got}"`);
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main();
