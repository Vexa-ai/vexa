/**
 * The re-knock loop for Meet refusing a knock ("You can't join this video call", refusal.ts).
 *
 * Meet's refusal is flaky (join harness on bbb, 29 Oct 2026: 9 of 22 knocks refused; every refused bot
 * got through on its second or third knock), so a refused knock is retried up to
 * GOOGLE_JOIN_MAX_KNOCKS within the same lobby budget. Anything else ends the loop untouched.
 *
 * Run: npx tsx src/googlemeet/refusal.test.ts
 */
import {
  knockUntilGoogleAccepts, GoogleJoinRefusedError, GOOGLE_JOIN_MAX_KNOCKS, GOOGLE_JOIN_REFUSED,
} from './refusal';
import { AdmissionError } from '../shared/admission';

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` (${detail})` : ''}`); failed++; }
}

const page: any = {};
const refused = () => new GoogleJoinRefusedError('CreateMeetingDevice answered HTTP 403');

(async () => {
  console.log('\n=== knockUntilGoogleAccepts ===');

  // Refused, refused, then the host's lobby admits: three knocks, admitted.
  {
    let knocks = 0;
    const got = await knockUntilGoogleAccepts(page, 60_000, async () => {
      knocks++;
      if (knocks < 3) throw refused();
      return true;
    });
    check('refused twice, then admitted → admitted on knock 3', got === true && knocks === 3, `got=${got} knocks=${knocks}`);
  }

  // Refused every time: stops at the bound and says how many knocks Meet refused.
  {
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 60_000, async () => { knocks++; throw refused(); }).catch((e) => e);
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
    const err = await knockUntilGoogleAccepts(page, 60_000, async () => { knocks++; throw e; }).catch((x) => x);
    check(`${label} → one knock, error passed through unchanged`, knocks === 1 && err === e, `knocks=${knocks}`);
  }

  // Re-knocks share the issued lobby budget; an exhausted budget ends the loop.
  {
    let clock = 0;
    const budgets: number[] = [];
    const err = await knockUntilGoogleAccepts(page, 30_000, async (remaining) => {
      budgets.push(remaining);
      clock += 20_000;
      throw refused();
    }, () => clock).catch((x) => x);
    check('re-knock gets the remaining budget, not a fresh one', budgets[0] === 30_000 && budgets[1] === 10_000, JSON.stringify(budgets));
    check('… and the loop stops when the budget is spent', budgets.length === 2 && err instanceof GoogleJoinRefusedError, `knocks=${budgets.length}`);
  }

  // Admitted on the first knock: no re-knock.
  {
    let knocks = 0;
    const got = await knockUntilGoogleAccepts(page, 60_000, async () => { knocks++; return true; });
    check('admitted on knock 1 → one knock', got === true && knocks === 1, `knocks=${knocks}`);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
