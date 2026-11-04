/**
 * The re-knock loop for Meet refusing a knock ("You can't join this video call", refusal.ts).
 *
 * Meet's refusal is flaky (join harness on bbb, 29 Oct 2026: 9 of 22 knocks refused; every refused bot
 * got through on its second or third knock), so a refused knock is retried up to
 * GOOGLE_JOIN_MAX_KNOCKS (10) within the same lobby budget. Waits between knocks and the fresh-guest wipe
 * exist but are off unless configured (googleKnockConfig). Anything else ends the loop untouched.
 *
 * Run: npx tsx src/googlemeet/refusal.test.ts
 */
import {
  knockUntilGoogleAccepts, GoogleJoinRefusedError, GOOGLE_JOIN_MAX_KNOCKS, GOOGLE_JOIN_REFUSED,
  GOOGLE_JOIN_REKNOCK_BACKOFF_MS, googleKnockConfig, GoogleKnockExpiredError, GOOGLE_MEET_MAX_REQUESTS,
  GOOGLE_GUEST_BLOCKED, refusalMessage,
} from './refusal';
import { AdmissionError } from '../shared/admission';
import { waitForAnySelector, waitForLobbyCta } from './join';

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` (${detail})` : ''}`); failed++; }
}

// A google.rpc.Status as Meet sends it: code (field 1), message (field 2), details (field 3).
const statusBody = (code: number, message: string, typeUrl?: string): Buffer => {
  const str = (field: number, b: Buffer) => Buffer.concat([Buffer.from([(field << 3) | 2, b.length]), b]);
  const parts = [Buffer.from([0x08, code]), str(2, Buffer.from(message))];
  if (typeUrl) parts.push(str(3, Buffer.concat([str(1, Buffer.from(typeUrl)), str(2, Buffer.from([0x10, 0x0b]))])));
  return Buffer.concat(parts);
};

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
    }, () => clock, { backoffMs: [15_000, 30_000], sleep: async (ms) => { clock += ms; } }).catch((x) => x);
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
    }, () => clock, { maxKnocks: 5, backoffMs: [15_000, 30_000, 60_000, 120_000], sleep: async (ms) => { waits.push(ms); clock += ms; } }).catch((x) => x);
    check('configured: 5 knocks', at.length === 5, `knocks=${at.length}`);
    check('configured waits 15/30/60/120 s between them', JSON.stringify(waits) === JSON.stringify([15_000, 30_000, 60_000, 120_000]), JSON.stringify(waits));
    check('knock 5 starts 245 s after knock 1 (well inside 15 min)', at[4] === 245_000, JSON.stringify(at));
    check('… refusal names 5 of 5 knocks', err instanceof GoogleJoinRefusedError && err.message.startsWith(`${GOOGLE_JOIN_REFUSED}: Google Meet refused the join on 5 of 5 knocks`), err?.message);
  }

  // An anonymous bot re-knocks as a fresh guest: the state is wiped after each wait, before the next
  // knock, never after the last one; a signed-in bot keeps its profile (no wipe at all).
  for (const freshen of [true, false]) {
    const events: string[] = [];
    await knockUntilGoogleAccepts(page, 900_000, async () => { events.push('knock'); throw refused(); }, Date.now, {
      maxKnocks: 5, backoffMs: [1],
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
    }, Date.now, { freshen: true, backoffMs: [1], sleep: async () => { events.push('wait'); }, freshenState: async () => { events.push('fresh'); } });
    check('refused once, admitted on knock 2 → one wait, one wipe', got === true && events.join(',') === 'knock,wait,fresh,knock', events.join(','));
  }

  // Defaults: 10 knocks, no extra wait (today's ~11 s spacing), no wipe.
  {
    let knocks = 0;
    const slept: number[] = [];
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => { knocks++; throw refused(); }, Date.now,
      { sleep: async (ms) => { slept.push(ms); } }).catch((e) => e);
    check('default: 10 knocks, never sleeps', GOOGLE_JOIN_MAX_KNOCKS === 10 && knocks === 10 && slept.length === 0, `knocks=${knocks} slept=${slept}`);
    check('… refusal names 10 of 10 knocks', err?.message?.startsWith(`${GOOGLE_JOIN_REFUSED}: Google Meet refused the join on 10 of 10 knocks`), err?.message);
    check('default backoff is empty', GOOGLE_JOIN_REKNOCK_BACKOFF_MS.length === 0, JSON.stringify(GOOGLE_JOIN_REKNOCK_BACKOFF_MS));
  }

  // Configuration: parsing, bounds, defaults. Invalid → that setting's default; never throws.
  {
    const d = googleKnockConfig({});
    check('config: nothing set → 10 knocks, no waits, no wipe', d.maxKnocks === 10 && d.backoffMs.length === 0 && d.wipe === false, JSON.stringify(d));
    const c = googleKnockConfig({ VEXA_GOOGLE_JOIN_MAX_KNOCKS: '7', VEXA_GOOGLE_REKNOCK_BACKOFF_MS: '5000, 10000', VEXA_GOOGLE_REKNOCK_WIPE: 'true' });
    check('config: valid values are used', c.maxKnocks === 7 && JSON.stringify(c.backoffMs) === '[5000,10000]' && c.wipe === true, JSON.stringify(c));
    for (const bad of ['0', '21', '3.5', 'ten', '-1']) {
      check(`config: max knocks "${bad}" → 10`, googleKnockConfig({ VEXA_GOOGLE_JOIN_MAX_KNOCKS: bad }).maxKnocks === 10);
    }
    check('config: max knocks 1 and 20 accepted', googleKnockConfig({ VEXA_GOOGLE_JOIN_MAX_KNOCKS: '1' }).maxKnocks === 1 && googleKnockConfig({ VEXA_GOOGLE_JOIN_MAX_KNOCKS: '20' }).maxKnocks === 20);
    for (const bad of ['abc', '1000,-5', '130000', '1.5']) {
      check(`config: backoff "${bad}" → none`, googleKnockConfig({ VEXA_GOOGLE_REKNOCK_BACKOFF_MS: bad }).backoffMs.length === 0);
    }
    // 10 knocks × 120 s (last value repeats) = 9 waits × 120 s = 1080 s > 600 s total → refused.
    check('config: waits totalling over 600 s → none', googleKnockConfig({ VEXA_GOOGLE_REKNOCK_BACKOFF_MS: '120000' }).backoffMs.length === 0);
    check('config: the same waits fit with 5 knocks (480 s)', googleKnockConfig({ VEXA_GOOGLE_JOIN_MAX_KNOCKS: '5', VEXA_GOOGLE_REKNOCK_BACKOFF_MS: '120000' }).backoffMs.length === 1);
    for (const v of ['0', 'false', 'yes', '']) {
      check(`config: wipe "${v}" → off`, googleKnockConfig({ VEXA_GOOGLE_REKNOCK_WIPE: v }).wipe === false);
    }
    check('config: wipe "1" → on', googleKnockConfig({ VEXA_GOOGLE_REKNOCK_WIPE: '1' }).wipe === true);
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

  console.log('\n=== Meet ended the request unanswered: ask again while the budget lasts ===');

  // Prod, 3-4 Nov 2026: every "No one responded" bot ended at 598-603 s in the lobby of a 900 s budget.
  // The clock is the test's: each knock moves it by the time Meet or our budget took.
  const expired = () => new GoogleKnockExpiredError('Meet showed "No one responded to your request to join the call"');
  const stillWaiting = () => new AdmissionError('lobby_timeout', 'Bot is still in the Google Meet waiting room after timeout — host did not admit');

  // Expired at 600 s, then the host admits the second request.
  {
    let clock = 0;
    const budgets: number[] = [];
    const got = await knockUntilGoogleAccepts(page, 900_000, async (remaining) => {
      budgets.push(remaining);
      if (budgets.length === 1) { clock += 600_000; throw expired(); }
      clock += 30_000;
      return true;
    }, () => clock, fast);
    check('expired at 600 s, then admitted → admitted on request 2', got === true && budgets.length === 2, `got=${got} knocks=${budgets.length}`);
    check('… request 2 gets the 300 s left, not a fresh budget', budgets[1] === 300_000, JSON.stringify(budgets));
  }

  // Expired, then nobody answers request 2 either: our own budget ends it, and the verdict says when
  // each request was open.
  {
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async (remaining) => {
      knocks++;
      if (knocks === 1) { clock += 600_000; throw expired(); }
      clock += remaining;
      throw stillWaiting();
    }, () => clock, fast).catch((e) => e);
    check('expired, then budget ran out → two requests, lobby_timeout', knocks === 2 && err instanceof AdmissionError && err.outcome === 'lobby_timeout', `knocks=${knocks} ${err?.outcome}`);
    check('… names both requests and their times',
      err?.message === 'Bot is still in the Google Meet waiting room after timeout — host did not admit; 2 join requests: 1) 00:00:00Z–00:10:00Z ended by Meet, 2) 00:10:00Z–00:15:00Z lobby budget ran out',
      err?.message);
  }

  // Under 60 s of budget left when Meet ends the request: no new request.
  {
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 650_000, async () => { knocks++; clock += 600_000; throw expired(); }, () => clock, fast).catch((e) => e);
    check('expired with 50 s left → no new request', knocks === 1 && err?.outcome === 'lobby_timeout', `knocks=${knocks}`);
    check('… the verdict keeps Meet\'s expiry words and says why it stopped',
      /^Google Meet ended the join request, nobody admitted the bot: .*; 1 join request: 1\) 00:00:00Z–00:10:00Z ended by Meet; not asking again: 50 s of the lobby budget left$/.test(err?.message ?? ''), err?.message);
  }

  // Meet ending a request within 2 minutes is not its 10-minute expiry: never a loop of requests.
  {
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => { knocks++; clock += 20_000; throw expired(); }, () => clock, fast).catch((e) => e);
    check('ended after 20 s → no new request (no tight loop)', knocks === 1 && /not asking again: Meet ended it after 20 s$/.test(err?.message ?? ''), `knocks=${knocks} ${err?.message}`);
  }

  // A long budget: at most GOOGLE_MEET_MAX_REQUESTS requests.
  {
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 3_600_000, async () => { knocks++; clock += 600_000; throw expired(); }, () => clock, fast).catch((e) => e);
    check(`1 h budget, every request expires → ${GOOGLE_MEET_MAX_REQUESTS} requests, then stop`, knocks === GOOGLE_MEET_MAX_REQUESTS && new RegExp(`not asking again: ${GOOGLE_MEET_MAX_REQUESTS} requests made$`).test(err?.message ?? ''), `knocks=${knocks} ${err?.message}`);
  }

  // A host denial or an unsolved captcha on the second request ends it there: never asked again.
  for (const [label, e] of [
    ['host denial', new AdmissionError('denial', 'Bot admission was rejected by meeting admin')],
    ['captcha', new AdmissionError('denial', 'Bot faced a reCAPTCHA challenge that went unsolved for 120s alongside a rejection screen')],
  ] as const) {
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => {
      knocks++;
      if (knocks === 1) { clock += 600_000; throw expired(); }
      clock += 40_000;
      throw e;
    }, () => clock, fast).catch((x) => x);
    check(`expired, then ${label} → two requests, denial`, knocks === 2 && err instanceof AdmissionError && err.outcome === 'denial', `knocks=${knocks} ${err?.outcome}`);
    check(`… ${label} message kept, requests named`, err?.message === `${e.message}; 2 join requests: 1) 00:00:00Z–00:10:00Z ended by Meet, 2) 00:10:00Z–00:10:40Z denied`, err?.message);
  }

  // A typed subclass after an expiry passes as it is: the embedder checks its type.
  {
    class TypedError extends AdmissionError { constructor() { super('join_failure', 'typed'); } }
    const typed = new TypedError();
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => {
      knocks++;
      if (knocks === 1) { clock += 600_000; throw expired(); }
      throw typed;
    }, () => clock, fast).catch((x) => x);
    check('expired, then a typed AdmissionError subclass → the same error object', knocks === 2 && err === typed, `knocks=${knocks}`);
  }

  // Meet refuses every re-ask after the host let request 1 expire: the expiry is the verdict (the host
  // did see a request), not "the host never received a request".
  {
    let clock = 0;
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => {
      knocks++;
      if (knocks === 1) { clock += 600_000; throw expired(); }
      clock += 10_000;
      throw refused();
    }, () => clock, { ...fast, maxKnocks: 3 }).catch((x) => x);
    check('expired, then 3 refusals → 4 knocks, lobby_timeout (not a refusal)', knocks === 4 && err?.outcome === 'lobby_timeout' && !(err instanceof GoogleJoinRefusedError), `knocks=${knocks} ${err?.name} ${err?.outcome}`);
    check('… message keeps Meet\'s expiry words, names the request and the refusals',
      err?.message === 'Google Meet ended the join request, nobody admitted the bot; 1 join request: 1) 00:00:00Z–00:10:00Z ended by Meet; then Meet refused 3 knocks (CreateMeetingDevice answered HTTP 403)',
      err?.message);
  }

  // A refused knock followed by an expiry and an admission: both loops share one join.
  {
    let clock = 0;
    const seen: string[] = [];
    const got = await knockUntilGoogleAccepts(page, 900_000, async () => {
      if (seen.length === 0) { seen.push('refused'); clock += 10_000; throw refused(); }
      if (seen.length === 1) { seen.push('expired'); clock += 600_000; throw expired(); }
      seen.push('admitted');
      return true;
    }, () => clock, fast);
    check('refused, expired, admitted → admitted on knock 3', got === true && seen.join(',') === 'refused,expired,admitted', seen.join(','));
  }

  console.log('\n=== The 403 body: a meeting that does not admit browser guests (bbb, 4 Nov 2026) ===');

  // Meet's 403 bodies as captured on bbb: a protobuf with the message and an ErrorDetails type URL.
  const policyBody = statusBody(7, 'The requester cannot resolve this meeting due to the client they are joining from', 'type.googleapis.com/google.rtc.meetings.v1.ErrorDetails');
  const flakyBody = statusBody(7, 'The caller does not have permission');
  check('policy body → its message, without the type URL', refusalMessage(policyBody) === 'The requester cannot resolve this meeting due to the client they are joining from', String(refusalMessage(policyBody)));
  check('flaky body → its message', refusalMessage(flakyBody) === 'The caller does not have permission', String(refusalMessage(flakyBody)));
  check('no message field → null', refusalMessage(Buffer.from([0x08, 0x07])) === null);
  check('a truncated body → null, never a throw', refusalMessage(Buffer.from([0x08, 0x07, 0x12, 0x40, 0x41])) === null);
  check('not protobuf (an HTML error page) → null', refusalMessage(Buffer.from('<html>403</html>')) === null);

  const blocked = () => new GoogleJoinRefusedError(`ResolveMeetingSpace answered HTTP 403: ${refusalMessage(policyBody)}`);
  {
    const e = blocked();
    check('guest-blocked refusal is flagged', e.guestBlocked === true && e.outcome === 'denial');
    check('… its message names the meeting\'s setting and the body', /^Google Meet refused the join: this meeting does not admit guests joining from a browser \(ResolveMeetingSpace answered HTTP 403: The requester cannot resolve this meeting due to the client they are joining from\); the host never received a request to admit the bot$/.test(e.message), e.message);
    check('… and leaves out the respawn marker, so meeting-api starts no new pod', !e.message.includes(GOOGLE_JOIN_REFUSED) && e.message.includes(GOOGLE_GUEST_BLOCKED));
    check('flaky refusal with its body: not guest-blocked, marker kept', !new GoogleJoinRefusedError('CreateMeetingDevice answered HTTP 403: The caller does not have permission').guestBlocked
      && new GoogleJoinRefusedError('CreateMeetingDevice answered HTTP 403: The caller does not have permission').message.startsWith(GOOGLE_JOIN_REFUSED));
  }
  {
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => { knocks++; throw blocked(); }, Date.now, fast).catch((e) => e);
    check('guest-blocked on knock 1 → no second knock', knocks === 1 && err instanceof GoogleJoinRefusedError && err.guestBlocked, `knocks=${knocks}`);
  }
  {
    let knocks = 0;
    const err = await knockUntilGoogleAccepts(page, 900_000, async () => { knocks++; throw knocks < 3 ? refused() : blocked(); }, Date.now, fast).catch((e) => e);
    check('flaky, flaky, then guest-blocked → stops on knock 3', knocks === 3 && err?.guestBlocked === true, `knocks=${knocks}`);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
