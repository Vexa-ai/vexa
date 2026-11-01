/**
 * The re-knock loop for Meet refusing a knock ("You can't join this video call", refusal.ts).
 *
 * Meet's refusal is flaky (join harness on bbb, 29 Oct 2026: 9 of 22 knocks refused; every refused bot
 * got through on its second or third knock), so a refused knock is retried up to
 * GOOGLE_JOIN_MAX_KNOCKS within the same lobby budget, after GOOGLE_JOIN_REKNOCK_BACKOFF_MS and, for an
 * anonymous bot, from a fresh guest state. Anything else ends the loop untouched.
 *
 * Run: npx tsx src/googlemeet/refusal.test.ts
 */
import {
  knockUntilGoogleAccepts, GoogleJoinRefusedError, GOOGLE_JOIN_MAX_KNOCKS, GOOGLE_JOIN_REFUSED,
  GOOGLE_JOIN_REKNOCK_BACKOFF_MS,
} from './refusal';
import { AdmissionError } from '../shared/admission';
import { waitForAnySelector, waitForLobbyCta } from './join';

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` (${detail})` : ''}`); failed++; }
}

const page: any = {};
const refused = () => new GoogleJoinRefusedError('CreateMeetingDevice answered HTTP 403');
// No real waiting in tests: the re-knock waits are injected.
const fast = { sleep: async () => {} };

(async () => {
  console.log('\n=== knockUntilGoogleAccepts ===');

  // Refused, refused, then the host's lobby admits: three knocks, admitted.
  {
    let knocks = 0;
    const got = await knockUntilGoogleAccepts(page, 900_000, async () => {
      knocks++;
      if (knocks < 3) throw refused();
      return true;
    }, Date.now, fast);
    check('refused twice, then admitted → admitted on knock 3', got === true && knocks === 3, `got=${got} knocks=${knocks}`);
  }

  // Refused every time: stops at the bound and says how many knocks Meet refused.
  {
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => { knocks++; throw refused(); }, Date.now, fast).catch((e) => e);
    check(`refused every knock → stops after ${GOOGLE_JOIN_MAX_KNOCKS}`, knocks === GOOGLE_JOIN_MAX_KNOCKS, `knocks=${knocks}`);
    check('… typed denial', err instanceof GoogleJoinRefusedError && err.outcome === 'denial', String(err));
    check('… message names the refusal and the knock count',
      err?.message?.startsWith(`${GOOGLE_JOIN_REFUSED}: Google Meet refused the join on ${GOOGLE_JOIN_MAX_KNOCKS} of ${GOOGLE_JOIN_MAX_KNOCKS} knocks`) &&
      /never received a request/.test(err.message), err?.message);
  }

  // A host denial, a lobby timeout or a crash is not re-knocked.
  for (const [label, e] of [
    ['host denial', new AdmissionError('denial', 'Bot admission was rejected by meeting admin')],
    ['lobby timeout', new AdmissionError('lobby_timeout', 'still in the waiting room')],
    ['crash', new Error('Target page, context or browser has been closed')],
  ] as const) {
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => { knocks++; throw e; }, Date.now, fast).catch((x) => x);
    check(`${label} → one knock, error passed through unchanged`, knocks === 1 && err === e, `knocks=${knocks}`);
  }

  // Re-knocks share the issued lobby budget (the waits count against it); a budget too short for the
  // next wait ends the loop.
  {
    let clock = 0;
    const budgets: number[] = [];
    const err = await knockUntilGoogleAccepts(page, 60_000, async (remaining) => {
      budgets.push(remaining);
      clock += 10_000;
      throw refused();
    }, () => clock, { sleep: async (ms) => { clock += ms; } }).catch((x) => x);
    // knock 1 at 0 (60 s left), +10 s, wait 15 s → knock 2 at 25 s (35 s left), +10 s; 25 s left < 30 s wait.
    check('re-knock gets the remaining budget, not a fresh one', budgets[0] === 60_000 && budgets[1] === 35_000, JSON.stringify(budgets));
    check('… and the loop stops when the budget cannot hold the next wait', budgets.length === 2 && err instanceof GoogleJoinRefusedError && /on 2 of 2 knocks/.test(err.message), `knocks=${budgets.length} ${err?.message}`);
  }

  // The schedule: 5 knocks, waiting 15, 30, 60, 120 s before knocks 2-5, all inside a 15-min lobby budget.
  {
    let clock = 0;
    const waits: number[] = [];
    const at: number[] = [];
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => {
      at.push(clock);
      clock += 5_000;
      throw refused();
    }, () => clock, { sleep: async (ms) => { waits.push(ms); clock += ms; } }).catch((x) => x);
    check('5 knocks', GOOGLE_JOIN_MAX_KNOCKS === 5 && at.length === 5, `knocks=${at.length}`);
    check('waits 15/30/60/120 s between them', JSON.stringify(waits) === JSON.stringify([15_000, 30_000, 60_000, 120_000]) &&
      JSON.stringify(waits) === JSON.stringify(GOOGLE_JOIN_REKNOCK_BACKOFF_MS), JSON.stringify(waits));
    check('knock 5 starts 245 s after knock 1 (well inside 15 min)', at[4] === 245_000, JSON.stringify(at));
    check('… refusal names 5 of 5 knocks', err instanceof GoogleJoinRefusedError && err.message.startsWith(`${GOOGLE_JOIN_REFUSED}: Google Meet refused the join on 5 of 5 knocks`), err?.message);
  }

  // An anonymous bot re-knocks as a fresh guest: the state is wiped after each wait, before the next
  // knock, never after the last one; a signed-in bot keeps its profile (no wipe at all).
  for (const freshen of [true, false]) {
    const events: string[] = [];
    await knockUntilGoogleAccepts(page, 900_000, async () => { events.push('knock'); throw refused(); }, Date.now, {
      freshen,
      sleep: async () => { events.push('wait'); },
      freshenState: async () => { events.push('fresh'); },
    }).catch(() => {});
    const want = freshen
      ? ['knock', 'wait', 'fresh', 'knock', 'wait', 'fresh', 'knock', 'wait', 'fresh', 'knock', 'wait', 'fresh', 'knock']
      : ['knock', 'wait', 'knock', 'wait', 'knock', 'wait', 'knock', 'wait', 'knock'];
    check(freshen ? 'anonymous bot: wait, wipe, knock; no wipe after the last' : 'signed-in bot: never wiped',
      JSON.stringify(events) === JSON.stringify(want), events.join(','));
  }

  // A refusal then admission waits once and never wipes again.
  {
    const events: string[] = [];
    let knocks = 0;
    const got = await knockUntilGoogleAccepts(page, 900_000, async () => {
      events.push('knock');
      if (++knocks === 1) throw refused();
      return true;
    }, Date.now, { freshen: true, sleep: async () => { events.push('wait'); }, freshenState: async () => { events.push('fresh'); } });
    check('refused once, admitted on knock 2 → one wait, one wipe', got === true && events.join(',') === 'knock,wait,fresh,knock', events.join(','));
  }

  // Admitted on the first knock: no re-knock.
  {
    let knocks = 0;
    const got = await knockUntilGoogleAccepts(page, 60_000, async () => { knocks++; return true; }, Date.now, fast);
    check('admitted on knock 1 → one knock', got === true && knocks === 1, `knocks=${knocks}`);
  }

  // The lobby waits stop on a refusal instead of running out their 120 s / 60 s budget (the pre-click
  // refusal: ResolveMeetingSpace 403 while the bot still waits for the name field).
  {
    const blank: any = {
      locator: () => ({ first: () => ({ isVisible: async () => false }) }),
      waitForTimeout: async () => {},
      screenshot: async () => {},
    };
    let polls = 0;
    const guard = async () => { if (++polls >= 3) throw refused(); };
    const t = Date.now();
    const nameErr = await waitForAnySelector(blank, ['input[type="text"]'], 120_000, 'name input', guard).catch((e) => e);
    check('name-input wait ends on the refusal guard, typed', nameErr instanceof GoogleJoinRefusedError && polls === 3 && Date.now() - t < 5_000, `${nameErr?.name} polls=${polls}`);
    polls = 0;
    const ctaErr = await waitForLobbyCta(blank, ['button:has-text("Ask to join")'], 60_000, 'join button', guard).catch((e) => e);
    check('join-button wait ends on the refusal guard, typed', ctaErr instanceof GoogleJoinRefusedError && polls === 3, `${ctaErr?.name} polls=${polls}`);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
