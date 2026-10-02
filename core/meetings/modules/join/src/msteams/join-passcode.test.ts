/**
 * The Teams join URL carries the meeting passcode; the Teams join LOGS must not (#892 A4).
 *
 * A Teams meeting addressed by its short id joins at `…/meet/<id>?p=<passcode>` — the credential
 * is IN the URL, because that is where Teams puts it. `page.goto` therefore has to receive the
 * whole thing. Everything else that touches that string is read by humans and shipped off-box:
 * container logs, `last_error`, triage dashboards. So the two live in tension and the split has
 * to be proven, not asserted in a comment — the pre-#892 code logged `meetingUrl` verbatim, which
 * was harmless only for as long as no constructed URL had a query.
 *
 * This drives the SHIPPED `joinMicrosoftTeams` against a page that reports the Microsoft sign-in
 * host, so the flow terminates at step 1b — after the navigation and its log line, before the
 * pre-join hunt. The assertions are a matched pair on purpose: the passcode must be in what the
 * browser was told to open AND absent from what was written down. A one-sided version of this
 * test would pass if the passcode simply went missing everywhere, which is the bug it guards.
 *
 * Run: npx tsx src/msteams/join-passcode.test.ts
 */

import { joinMicrosoftTeams } from './join';

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail = '') {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}

const PASSCODE = 'X8hcQVTnGNpGelJLSv';
const MEETING_URL = `https://teams.microsoft.com/meet/397421056486982?p=${PASSCODE}`;
// A sign-in landing ends the flow at step 1b, right after the navigation we are inspecting.
const LOGIN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?redirect_uri=x';

/** A Page that records where it was sent and then claims to be on the sign-in host. */
function fakePage() {
  const navigated: string[] = [];
  const page: any = {
    goto: async (url: string) => { navigated.push(url); },
    waitForTimeout: async () => {},
    url: () => LOGIN_URL,
    locator: () => ({ first: () => ({ waitFor: async () => { throw new Error('absent'); }, click: async () => {} }) }),
  };
  return { page, navigated };
}

(async () => {
  console.log('\n=== #892 A4: the passcode reaches the browser, never the log ===');

  const { page, navigated } = fakePage();
  const logs: string[] = [];
  const realLog = console.log;
  console.log = (...args: unknown[]) => { logs.push(args.map(String).join(' ')); };
  let threw = false;
  try {
    await joinMicrosoftTeams(page, MEETING_URL, 'Vexa', { platform: 'teams', passcode: PASSCODE });
  } catch { threw = true; } finally { console.log = realLog; }

  check('the flow terminated on the sign-in host (step 1b reached)', threw);
  check(
    'the browser was sent to the passcode-bearing URL',
    navigated.length === 1 && navigated[0] === MEETING_URL,
    JSON.stringify(navigated),
  );

  const leaked = logs.filter((l) => l.includes(PASSCODE));
  check('no log line carries the passcode', leaked.length === 0, JSON.stringify(leaked));

  // The redaction must not have swallowed the line — triage still needs to see WHERE the bot went.
  const navLine = logs.find((l) => l.includes('Navigating to Teams meeting'));
  check(
    'the navigation is still logged, as origin + path',
    !!navLine && navLine.includes('https://teams.microsoft.com/meet/397421056486982'),
    String(navLine),
  );

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
})();
