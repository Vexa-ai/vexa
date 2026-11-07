/**
 * Regression guard for the Google Meet admission detector.
 *
 * 1. #471 false-reject (FIXED): the "Asking to be let in" waiting screen shows
 *    a "Return to home screen" button. It used to be a googleRejectionIndicator,
 *    so `checkForGoogleRejection` classified a normal waiting screen as a host
 *    denial in ~4s (`awaiting_admission_rejected`) — the bot never waited for
 *    the host. Ported from Vexa-ai/vexa#471 (@priitvimberg): the button is now
 *    a WAITING indicator; genuine denials are still caught by the
 *    "denied your request" text patterns.
 *
 * 2. #444 conflation (STILL OPEN, narrowed by #471): googleRejectionIndicators
 *    keeps generic error-page affordances ("Try again", "Retry", "Go back",
 *    "Access denied", …) that also render on Google's bot-block / invalid-state
 *    pages, so a Google-side BLOCK is still thrown as "denial". The CONFLATION
 *    case below documents that remaining bug; when a block/error-vs-denial
 *    distinction lands (à la the Zoom zoom_requires_rtms detector), flip it.
 *
 * 3. #429 Gemini consent gate (Vexa-ai/vexa#454, @thatditsyboy): the
 *    "take notes for me" consent prompt is a pre-admission gate — meeting
 *    controls are visible behind it, so the bot false-reported ACTIVE with 0
 *    transcriptions. `hasConsentPrompt` detects it and
 *    `checkForGoogleAdmissionIndicators` suppresses the admitted signal.
 *
 * 4. #840 denial-vs-captcha (FIXED): Google Meet loads reCAPTCHA Enterprise
 *    INVISIBLY on every join (a background scoring frame), so a `/recaptcha/`
 *    frame sits on the HOST DENIAL screen too. The old detector read frame-URL
 *    presence as "a captcha is on screen" and suppressed the denial forever —
 *    prod meeting 24348 looped `"reCAPTCHA present alongside rejection
 *    indicator … staying for solve"` every 2s and never left `awaiting_admission`.
 *    Now: explicit host-denial copy wins over any captcha, only a VISIBLE
 *    challenge-sized widget counts as a challenge, and the stay-for-solve wager
 *    expires (CAPTCHA_SOLVE_GRACE_MS).
 *
 * This test feeds the detectors a fabricated DOM for each scenario (no browser,
 * no live meeting, no Google).
 *
 * Run: npx tsx src/googlemeet/admission.test.ts
 */

// Namespace import on purpose: this file must also RUN against the pre-#840
// module (which has no CAPTCHA_SOLVE_GRACE_MS) to demonstrate red→green.
import * as admission from './admission';
import { AdmissionError } from '../shared/admission';
import { resetEscalation } from '../shared/escalation';
const {
  checkForGoogleRejection,
  checkForWaitingRoomIndicators,
  checkForGoogleAdmissionIndicators,
  hasConsentPrompt,
  acknowledgeJoinNotice,
  waitForGoogleMeetingAdmission,
  checkForGoogleKnockExpired,
} = admission;
import {
  GoogleJoinRefusedError, GOOGLE_JOIN_REFUSED, watchGoogleJoinRefusal, checkForGoogleJoinRefused,
} from './refusal';
import { setHooks } from '../_host';
const GRACE_MS: number = (admission as any).CAPTCHA_SOLVE_GRACE_MS ?? 120_000;

/**
 * Minimal Playwright-Page stand-in. `visible` = the selectors that resolve
 * isVisible()===true on this page; `captcha` = the reCAPTCHA shape on the page;
 * `participantLabels` = aria-labels returned for [data-participant-id] tiles
 * (drives countRealParticipantTiles). Matches exactly the surface the admission
 * detectors use.
 *
 *   false        — no reCAPTCHA anywhere.
 *   'invisible'  — reCAPTCHA Enterprise's background scoring frame: a
 *                  `/recaptcha/` FRAME exists, its iframe element is
 *                  display:none (isVisible false, no box). This is what sits on
 *                  a real Google Meet page — including the denial screen (#840).
 *   'live'       — an actual challenge: a visible, challenge-sized iframe
 *                  (bframe, 400x580) a human/agent can solve.
 */
type CaptchaMode = false | 'invisible' | 'live';
const RECAPTCHA_IFRAME = 'iframe[src*="recaptcha"]';

function mockPage(visible: string[], captcha: CaptchaMode = false, participantLabels: string[] = [], url = 'https://meet.google.com/abc-defg-hij', clicks: string[] = []): any {
  const captchaIframes: { visible: boolean; box: { x: number; y: number; width: number; height: number } | null }[] =
    captcha === 'live' ? [{ visible: true, box: { x: 300, y: 120, width: 400, height: 580 } }]
      : captcha === 'invisible' ? [{ visible: false, box: null }]
        : [];
  const iframeEl = (i: number) => ({
    isVisible: async () => !!captchaIframes[i]?.visible,
    boundingBox: async () => captchaIframes[i]?.box ?? null,
    getAttribute: async () => null,
  });
  return {
    locator: (sel: string) => sel === RECAPTCHA_IFRAME
      ? {
        first: () => iframeEl(0),
        nth: (i: number) => iframeEl(i),
        count: async () => captchaIframes.length,
        evaluateAll: async () => [],
      }
      : {
        first: () => ({
          isVisible: async () => visible.includes(sel),
          getAttribute: async () => null,
          // A click is recorded and dismisses every dialog on the page (a join notice closes on
          // "Join now"), so the next probe sees what is behind it.
          click: async () => {
            clicks.push(sel);
            for (let i = visible.length - 1; i >= 0; i--) if (/dialog|take notes/.test(visible[i])) visible.splice(i, 1);
          },
        }),
        count: async () => (visible.includes(sel) ? 1 : 0),
        evaluateAll: async () => participantLabels,
      },
    mouse: { move: async () => {} },
    url: () => url,
    screenshot: async () => {},
    waitForTimeout: async (_ms: number) => {},
    frames: () => (captcha
      ? [{ url: () => 'https://meet.google.com/' }, { url: () => 'https://www.google.com/recaptcha/enterprise/anchor?ar=1' }]
      : [{ url: () => 'https://meet.google.com/' }]),
  };
}

let passed = 0, failed = 0;
async function check(name: string, actual: boolean, expected: boolean, detail?: string) {
  if (actual === expected) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name} (expected ${expected}, got ${detail ?? actual})`); failed++; }
}

/**
 * Outcome of a shipped admission wait, with a WATCHDOG: if no verdict lands within
 * `watchdogMs` the wait is still polling — which is exactly the #840 failure mode
 * (`awaiting_admission` forever), so it must read as a distinct, failing outcome.
 */
async function outcomeOf(run: Promise<unknown>, watchdogMs = 10_000): Promise<string> {
  const watchdog = new Promise<string>(resolve => {
    const t = setTimeout(() => resolve('NO-VERDICT (still polling after watchdog)'), watchdogMs);
    (t as any).unref?.();
  });
  return Promise.race([
    run.then(
      () => 'resolved',
      (e: any) => (e instanceof AdmissionError ? `AdmissionError:${e.outcome}` : `Error:${e.message}`),
    ),
    watchdog,
  ]);
}

(async () => {
  console.log('\n=== Google Meet rejection detector — #471 fix + remaining #444 conflation ===');

  // 1. #471 FIXED — the waiting screen's "Return to home screen" button alone is
  //    NOT a denial anymore. Before the fix this false-rejected in ~4s.
  await check(
    '#471 waiting screen ("Return to home screen", no denial text) → NOT a denial (fixed)',
    await checkForGoogleRejection(mockPage(['button:has-text("Return to home screen")'])),
    false,
  );

  // 1b. #471 — the button now counts as a WAITING indicator, so the polling loop
  //     keeps treating the screen as a lobby instead of an unknown state.
  await check(
    '#471 "Return to home screen" → recognized as waiting-room indicator',
    await checkForWaitingRoomIndicators(mockPage(['button:has-text("Return to home screen")'])),
    true,
  );

  // 2. REMAINING #444 CONFLATION — a Google ERROR/BLOCK page's "Try again"
  //    affordance (no host-denial text, no live challenge) is still classified as
  //    a denial. #471 narrowed the conflation but did not close it; flip this to
  //    `false` when a block/error-vs-denial distinction lands.
  await check(
    'CONFLATION (#444, still open): Google error/block page ("Try again") → reported as DENIAL (the remaining bug)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'])),
    true, // current buggy behavior — a non-host-rejection is thrown as "denial" → awaiting_admission_rejected
  );

  // 3. CONTRAST — a genuine host denial. SHOULD be a rejection (correct).
  await check(
    'genuine host denial ("denied your request") → rejection (correct)',
    await checkForGoogleRejection(mockPage(['text=denied your request'])),
    true,
  );

  // 4. GUARD — a LIVE reCAPTCHA challenge alongside an AMBIGUOUS error affordance:
  //    treated as bot-detection, NOT a denial (keeps the bot on the page for a
  //    human solve). NEGATIVE CONTROL (a) for #840 — must never regress.
  await check(
    'NEG-CTRL (a): live reCAPTCHA + "Try again", no denial copy → NOT a denial (stay-for-solve preserved)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'], 'live')),
    false,
  );

  // 5. CLEAN lobby — no rejection text at all → not a rejection (correct).
  await check(
    'clean waiting-room (no rejection text) → not a rejection (correct)',
    await checkForGoogleRejection(mockPage([])),
    false,
  );

  console.log('\n=== #840 — host denial vs. reCAPTCHA (prod meeting 24348) ===');

  // 6. THE #840 BUG — the exact prod page: host denial copy ("denied your
  //    request") on a page that ALSO carries Meet's invisible reCAPTCHA
  //    Enterprise scoring frame. Pre-fix this returned false forever (the 2s
  //    "staying for solve" loop, meeting stuck awaiting_admission).
  await check(
    '#840 host denial + invisible reCAPTCHA scoring frame → REJECTED (explicit denial wins)',
    await checkForGoogleRejection(mockPage(['text=denied your request'], 'invisible')),
    true,
  );

  // 6b. Same rule against a LIVE challenge: solving a captcha cannot undo a host's
  //     "no", so explicit denial copy still wins.
  await check(
    '#840 host denial + LIVE reCAPTCHA challenge → REJECTED (explicit denial still wins)',
    await checkForGoogleRejection(mockPage(['text=denied your request'], 'live')),
    true,
  );

  // 6c. Other explicit denial copy, same page shape.
  await check(
    '#840 "weren’t allowed to join" + invisible reCAPTCHA → REJECTED',
    await checkForGoogleRejection(mockPage(['text=weren’t allowed to join'], 'invisible')),
    true,
  );

  // 6c-bis. Order-independence: the denial screen also carries the generic "Try again"
  //     affordance. The explicit-denial pass runs first, so the verdict does not depend
  //     on where each selector sits in googleRejectionIndicators.
  await check(
    '#840 denial copy + "Try again" + live challenge → REJECTED (explicit pass runs first)',
    await checkForGoogleRejection(
      mockPage(['text=denied your request', 'button:has-text("Try again")'], 'live'),
    ),
    true,
  );

  // 6d. NEGATIVE CONTROL (b) — a plain denial with no captcha at all still rejects.
  await check(
    'NEG-CTRL (b): host denial, no reCAPTCHA anywhere → REJECTED',
    await checkForGoogleRejection(mockPage(['text=denied your request'])),
    true,
  );

  // 7. The detector no longer reads Meet's INVISIBLE scoring frame as a challenge:
  //    an ambiguous affordance next to it is not a solvable captcha, so the bot
  //    concludes instead of waiting for a solve that can never happen.
  await check(
    '#840 invisible scoring frame is NOT a challenge ("Try again" concludes, no stay-for-solve)',
    await checkForGoogleRejection(mockPage(['button:has-text("Try again")'], 'invisible')),
    true,
  );

  console.log('\n=== #840 — the stay-for-solve wager is BOUNDED ===');

  // 8. A live challenge suppresses the ambiguous indicator only within the grace
  //    window; past it the bot concludes terminal instead of polling forever.
  const boundedPage = mockPage(['button:has-text("Try again")'], 'live');
  const t0 = 1_000_000;
  await check(
    `bound: t+0s — live challenge suppresses (stay for solve)`,
    await checkForGoogleRejection(boundedPage, t0),
    false,
  );
  await check(
    `bound: t+${Math.round(GRACE_MS / 2000)}s (inside grace) — still staying for solve`,
    await checkForGoogleRejection(boundedPage, t0 + GRACE_MS / 2),
    false,
  );
  await check(
    `bound: t+${Math.round(GRACE_MS / 1000) + 1}s (past grace) — concludes TERMINAL, never loops forever`,
    await checkForGoogleRejection(boundedPage, t0 + GRACE_MS + 1_000),
    true,
  );

  // 8b. The clock is per-page and restarts after the page stops showing the
  //     indicator — a later, independent challenge gets its full grace.
  const freshPage = mockPage(['button:has-text("Try again")'], 'live');
  await check(
    'bound: a different page starts its own grace window (no shared clock)',
    await checkForGoogleRejection(freshPage, t0 + GRACE_MS * 10),
    false,
  );

  console.log('\n=== #840 — the SHIPPED wait concludes TERMINAL on the prod page shape ===');

  // 9. Altitude of the claim: not just the detector, the shipped
  //    waitForGoogleMeetingAdmission. Meeting 24348's page — stale lobby text +
  //    "denied your request" + Meet's invisible reCAPTCHA frame — must throw the TYPED
  //    AdmissionError("denial"), which the JoinDriver maps to `rejected` →
  //    `awaiting_admission_rejected` (PERMANENT, no re-knock). Pre-fix it never threw:
  //    the poll looped to the 10-minute timeout, so the meeting sat `awaiting_admission`.
  //    The watchdog below is the "loops forever" detector — a verdict must arrive.
  {
    resetEscalation();
    const deniedPage = mockPage(
      ['text=Asking to be let in', 'text=denied your request'],
      'invisible',
    );
    const got = await outcomeOf(waitForGoogleMeetingAdmission(deniedPage, 20_000, {} as any));
    check(
      "#840 shipped waitForGoogleMeetingAdmission(denial + invisible captcha) → AdmissionError('denial')",
      got === "AdmissionError:denial",
      true,
      got,
    );
  }

  console.log('\n=== Gemini "take notes" consent gate (#454 / issue #429) ===');

  // 9. Detector fires on the consent prompt copy.
  await check(
    'consent prompt visible → hasConsentPrompt = true',
    await hasConsentPrompt(mockPage(['text=take notes for me'])),
    true,
  );
  await check(
    'no consent prompt → hasConsentPrompt = false',
    await hasConsentPrompt(mockPage([])),
    false,
  );

  // 10. THE #429 BUG — meeting controls (real participant tiles) visible BEHIND
  //    the consent dialog must NOT read as admitted; the bot is not truly in the
  //    call until a human accepts/declines.
  await check(
    'consent prompt + participant tiles → admission SUPPRESSED (no false ACTIVE)',
    await checkForGoogleAdmissionIndicators(
      mockPage(['text=take notes for me'], false, ['John Doe']),
    ),
    false,
  );

  // 11. CONTROL — same participant tiles without the consent prompt → admitted.
  await check(
    'participant tiles, no consent prompt → admitted (control)',
    await checkForGoogleAdmissionIndicators(mockPage([], false, ['John Doe'])),
    true,
  );

  console.log('\n=== Meet join notices: "Join now" (#1251, #1727; founder waiver of #429, 28 Oct 2026) ===');

  const GEMINI_JOIN = '[role="dialog"]:has-text("taking notes") :is(button, [role="button"]):has-text("Join now")';
  const SHARED_JOIN = '[role="dialog"]:has-text("will be shared with") :is(button, [role="button"]):has-text("Join now")';

  // 12a. "Gemini is taking notes" (Leave / Join now) over the call → the bot clicks "Join now",
  //      the dialog closes, and the participant tiles behind it now read as admitted.
  {
    const clicks: string[] = [];
    const page = mockPage(['[role="dialog"]:has-text("taking notes")', GEMINI_JOIN], false, ['John Doe'], undefined, clicks);
    await check('Gemini notice + tiles → admitted after "Join now"', await checkForGoogleAdmissionIndicators(page), true);
    await check('… the click was "Join now" inside the notice', clicks.length === 1 && clicks[0] === GEMINI_JOIN, true, JSON.stringify(clicks));
  }
  // 12b. Another notetaker's notice (#1727): "Your call audio and video will be shared with Read AI".
  {
    const clicks: string[] = [];
    const page = mockPage(['[role="dialog"]:has-text("will be shared with")', SHARED_JOIN], false, ['John Doe'], undefined, clicks);
    await check('"will be shared with" notice → detected', await hasConsentPrompt(mockPage(['[role="dialog"]:has-text("will be shared with")'])), true);
    await check('"will be shared with" notice + tiles → admitted after "Join now"', await checkForGoogleAdmissionIndicators(page), true);
    await check('… the click was "Join now" inside that notice', clicks.length === 1 && clicks[0] === SHARED_JOIN, true, JSON.stringify(clicks));
  }
  // 12c. CONTROL — a prompt with no "Join now" ("take notes for me" asks to START notes) is not
  //      clicked and still suppresses admission for a human (#429 holds there).
  {
    const clicks: string[] = [];
    const page = mockPage(['text=take notes for me'], false, ['John Doe'], undefined, clicks);
    await check('"take notes for me" (no Join now) → nothing clicked', await acknowledgeJoinNotice(page), false);
    await check('… and admission stays suppressed', await checkForGoogleAdmissionIndicators(page), false);
    await check('… no click at all', clicks.length === 0, true, JSON.stringify(clicks));
  }
  // 12d. CONTROL — the pre-join screen's own "Join now" (not inside a notice) is never matched.
  await check(
    'pre-join "Join now" outside any notice → not a join notice',
    await acknowledgeJoinNotice(mockPage(['button:has-text("Join now")'])),
    false,
  );
  // 12e. The waiting loop answers the notice too, then admits.
  {
    resetEscalation();
    const clicks: string[] = [];
    const page = mockPage(['[role="dialog"]:has-text("taking notes")', GEMINI_JOIN], false, ['John Doe'], undefined, clicks);
    const got = await outcomeOf(waitForGoogleMeetingAdmission(page, 20_000, {} as any));
    check('waitForGoogleMeetingAdmission with a Gemini notice → resolved (admitted)', got === 'resolved', true, got);
  }

  console.log('\n=== Meet ended the knock unanswered ("No one responded", lobby test 27 Oct 2026) ===');

  // 12. About 10 min into an unanswered knock Meet replaces the lobby with "No one responded to
  //     your request to join the call" (plus a "Return to home screen" button), then redirects
  //     to workspace.google.com. Pre-fix neither matched anything: the wait ran to its 900 s
  //     budget and threw join_failure "no meeting indicators found", which join-evidence
  //     attributes to us (system_fault). It is the host not answering: lobby_timeout.
  {
    resetEscalation();
    const page = mockPage(['text=No one responded to your request to join', 'button:has-text("Return to home screen")']);
    const got = await outcomeOf(waitForGoogleMeetingAdmission(page, 20_000, {} as any));
    check('"No one responded" screen → AdmissionError(\'lobby_timeout\')', got === 'AdmissionError:lobby_timeout', true, got);
  }
  {
    resetEscalation();
    const page = mockPage([], false, [], 'https://workspace.google.com/products/meet/');
    const got = await outcomeOf(waitForGoogleMeetingAdmission(page, 20_000, {} as any));
    check('redirected to workspace.google.com → AdmissionError(\'lobby_timeout\')', got === 'AdmissionError:lobby_timeout', true, got);
  }
  // Controls: a sign-in redirect and a normal lobby are NOT an expired knock.
  await check(
    'accounts.google.com page → not an expired knock (control)',
    (await checkForGoogleKnockExpired(mockPage([], false, [], 'https://accounts.google.com/signin'))) === null,
    true,
  );
  await check(
    'normal lobby ("Asking to be let in" + "Return to home screen") → not an expired knock (control)',
    (await checkForGoogleKnockExpired(mockPage(['text=Asking to be let in', 'button:has-text("Return to home screen")']))) === null,
    true,
  );

  console.log('\n=== Meet refused the knock ("You can\'t join this video call", prod meeting 34824) ===');

  // 13. Right after "Ask to join" Meet answers CreateMeetingDevice with 403 and shows "You can't join
  //     this video call" plus "Return to home screen". Pre-fix that button read as the waiting room:
  //     awaiting_admission, then 900 s of polling a page with nothing behind it (34824: join_failure
  //     "no meeting indicators found", attributed system_fault). It is Meet refusing the join.
  const REFUSED_COPY = "text=You can't join this video call";
  {
    resetEscalation();
    const page = mockPage([REFUSED_COPY, 'button:has-text("Return to home screen")']);
    const got = await outcomeOf(waitForGoogleMeetingAdmission(page, 20_000, {} as any));
    check('"You can\'t join this video call" + "Return to home screen" → AdmissionError(\'denial\')', got === 'AdmissionError:denial', true, got);
    const err = await waitForGoogleMeetingAdmission(mockPage([REFUSED_COPY, 'button:has-text("Return to home screen")']), 20_000, {} as any).catch((e) => e);
    check('… typed GoogleJoinRefusedError naming google_meet_join_refused', err instanceof GoogleJoinRefusedError && err.message.startsWith(GOOGLE_JOIN_REFUSED), true, String(err?.message));
  }
  // The same page must never report awaiting_admission first (the host has nothing to admit).
  {
    resetEscalation();
    const states: string[] = [];
    setHooks({ onState: (s) => { states.push(s); } });
    const page = mockPage([REFUSED_COPY, 'button:has-text("Return to home screen")']);
    await waitForGoogleMeetingAdmission(page, 20_000, {} as any).catch(() => {});
    setHooks({});
    check('refused knock → no awaiting_admission state reported', states.includes('awaiting_admission'), false, JSON.stringify(states));
  }
  // The refusal screen can render between a poll's refusal check and its waiting-room check (live,
  // b-v2a-4, 29 Oct 17:04:52Z): the waiting-room predicate itself must not read it as the lobby.
  await check(
    'refusal screen ("You can\'t join" + "Return to home screen") → not the waiting room',
    await checkForWaitingRoomIndicators(mockPage([REFUSED_COPY, 'button:has-text("Return to home screen")'])),
    false,
  );
  await check(
    'normal lobby ("Asking to be let in" + "Return to home screen") → waiting room (control)',
    await checkForWaitingRoomIndicators(mockPage(['text=Asking to be let in', 'button:has-text("Return to home screen")'])),
    true,
  );
  // The typographic apostrophe Meet also uses.
  {
    resetEscalation();
    const page = mockPage(['text=You can’t join this video call']);
    const got = await outcomeOf(waitForGoogleMeetingAdmission(page, 20_000, {} as any));
    check('"You can’t join" (typographic apostrophe) → AdmissionError(\'denial\')', got === 'AdmissionError:denial', true, got);
  }
  // The network signal alone (screen already gone): CreateMeetingDevice 403, then the 60 s redirect
  // to workspace.google.com, where no waiting-room or admission indicator matches and the wait would
  // run its whole budget. The refusal must end it: no request ever reached the host.
  {
    resetEscalation();
    const listeners: ((r: any) => void)[] = [];
    const page = mockPage([], false, [], 'https://workspace.google.com/products/meet/');
    page.on = (ev: string, fn: (r: any) => void) => { if (ev === 'response') listeners.push(fn); };
    watchGoogleJoinRefusal(page);
    for (const fn of listeners) fn({ status: () => 403, url: () => 'https://meet.google.com/$rpc/google.rtc.meetings.v1.MeetingDeviceService/CreateMeetingDevice' });
    const err = await waitForGoogleMeetingAdmission(page, 20_000, {} as any).catch((e) => e);
    check('CreateMeetingDevice 403 + workspace redirect → refused, not lobby_timeout', err instanceof GoogleJoinRefusedError, true, `${err?.name}: ${err?.message}`);
    check('… evidence names the 403', /CreateMeetingDevice answered HTTP 403/.test(err?.message || ''), true, String(err?.message));
  }
  // The same refusal one step earlier: ResolveMeetingSpace answers 403 during the lobby load, before
  // any click (harness 29 Oct; self-hosted meeting 121's log). A 404 there is a code that does not exist.
  {
    const listeners: ((r: any) => void)[] = [];
    const page = mockPage([]);
    page.on = (ev: string, fn: (r: any) => void) => { if (ev === 'response') listeners.push(fn); };
    watchGoogleJoinRefusal(page);
    for (const fn of listeners) fn({ status: () => 403, url: () => 'https://meet.google.com/$rpc/google.rtc.meetings.v1.MeetingSpaceService/ResolveMeetingSpace' });
    const why = await checkForGoogleJoinRefused(page);
    await check('ResolveMeetingSpace 403 on lobby load → refused, evidence names the call', why === 'ResolveMeetingSpace answered HTTP 403', true, String(why));
  }
  {
    const listeners: ((r: any) => void)[] = [];
    const page = mockPage([]);
    page.on = (ev: string, fn: (r: any) => void) => { if (ev === 'response') listeners.push(fn); };
    watchGoogleJoinRefusal(page);
    for (const fn of listeners) fn({ status: () => 404, url: () => 'https://meet.google.com/$rpc/google.rtc.meetings.v1.MeetingSpaceService/ResolveMeetingSpace' });
    await check('ResolveMeetingSpace 404 (no such meeting) → not a refusal (control)', (await checkForGoogleJoinRefused(page)) === null, true);
  }
  // Controls: other responses and the normal lobby are not a refusal.
  {
    const listeners: ((r: any) => void)[] = [];
    const page = mockPage(['text=Asking to be let in', 'button:has-text("Return to home screen")']);
    page.on = (ev: string, fn: (r: any) => void) => { if (ev === 'response') listeners.push(fn); };
    watchGoogleJoinRefusal(page);
    for (const fn of listeners) fn({ status: () => 200, url: () => 'https://meet.google.com/$rpc/google.rtc.meetings.v1.MeetingDeviceService/CreateMeetingDevice' });
    for (const fn of listeners) fn({ status: () => 403, url: () => 'https://meet.google.com/$rpc/google.rtc.meetings.v1.SomethingElse/Get' });
    await check('normal lobby + CreateMeetingDevice 200 + an unrelated 403 → not refused (control)', (await checkForGoogleJoinRefused(page)) === null, true);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
