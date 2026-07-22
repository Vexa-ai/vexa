/**
 * #816 hardening — an unknown platform is REFUSED by the dispatch, never silently driven
 * through the Google Meet join flow.
 *
 * The dispatch's else-branch used to BE the google_meet branch, so any platform string outside
 * the known set ran Google Meet selectors against an arbitrary URL and failed minutes later with
 * misattributed selector timeouts. Observed with `browser_session` (sealed in api.v1, absent from
 * this layer) at the 0.12 prod cutover.
 *
 * Run: npx tsx core/meetings/modules/join/src/__tests__/unknownPlatform.test.ts
 */

import { joinMeeting } from '../index';

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}

// A page that fails LOUD if any join flow touches it — the refusal must precede all page use.
const explodingPage: any = new Proxy({}, {
  get: (_t, prop) => {
    if (prop === 'then') return undefined; // not a thenable
    return () => { throw new Error(`page.${String(prop)} was called — a join flow RAN`); };
  },
});

async function main() {
  try {
    await joinMeeting(explodingPage, {
      meetingUrl: 'http://irrelevant.example/x',
      platform: 'browser_session' as any,
    });
    check('unknown platform is refused', false, 'joinMeeting resolved');
  } catch (e: any) {
    check('unknown platform is refused with a typed message, before any page interaction',
      /Unsupported platform 'browser_session'/.test(e.message), e.message);
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
