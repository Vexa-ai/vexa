/**
 * L2 — the join-failure taxonomy + its attribution axis (#1059, #1058).
 *
 * Three things are proved here, offline, in milliseconds:
 *
 *  1. **Classification.** Given the signals a real join gathers (verdict, stage, lobby timings
 *     against the issued budget, the platform's own message) the typed reason is the expected one.
 *     Above all: the ~20s refusal and the ~13min lobby expiry that production collapsed into one
 *     `join_failure` come out as two different reasons, from the timing signature alone.
 *  2. **Attribution.** Every typed reason maps to the owner the reliability gate expects — and the
 *     two evidence-driven overrides fire: a platform-policy refusal is exogenous rather than the
 *     host's doing, and an admission wait that ended because our join layer went blind is OURS.
 *     This is the axis `system_failure_rate = failures(system_fault) / fair-chance meetings` reads.
 *  3. **Replayable fixtures, end to end.** A recorded signal sequence per failure class is driven
 *     through the REAL orchestrator with fake ports; the terminal lifecycle.v1 event it emits must
 *     conform to the sealed schema AND carry the expected evidence. These are the regression tapes:
 *     re-running one reproduces the exact classification without a browser or a meeting.
 *
 * Plus the fail-open contract, which is the one that must never bend: evidence that cannot be
 * produced degrades the REPORT, never the run.
 *
 * Run: tsx src/join-evidence.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join as joinPath } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  attributeJoinFailure,
  buildJoinEvidence,
  classifyJoinFailure,
  cleanDetail,
  DETAIL_MAX_CHARS,
  type JoinFailureAttribution,
  type JoinFailureReason,
} from './join-evidence.js';
import { createJoinTimer } from './join-driver.js';
import { createOrchestrator } from './orchestrator.js';
import type { LifecycleEvent } from './contracts.js';
import type { Invocation } from './config.js';
import type { JoinDriver, JoinOutcome, JoinResult, JoinSignals, LifecycleSink } from './ports.js';
import { noopAloneness, noopActs, noopPipeline } from './test-doubles.js';

let passed = 0;
let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? `  — ${detail}` : ''}`); failed++; }
};

// ── lifecycle.v1 validator (ajv against the PUBLISHED schema, loaded by path — P8) ──
const HERE = dirname(fileURLToPath(import.meta.url));
const lcSchema = JSON.parse(
  readFileSync(joinPath(HERE, '..', '..', '..', 'contracts', 'lifecycle.v1', 'lifecycle.schema.json'), 'utf8'),
);
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(lcSchema);
const validateLifecycle: ValidateFunction = ajv.compile({ $ref: `${lcSchema.$id}#/$defs/LifecycleEvent` });

/** The lobby budget the control plane issues in production (`bot_spawn.service.LOBBY_BUDGET_MS`). */
const BUDGET_MS = 600_000;

// ══ 1. classification ═══════════════════════════════════════════════════════════════════════════

console.log('\n=== 1. classification: signals → typed reason ===');

type Case = {
  name: string;
  outcome: Parameters<typeof classifyJoinFailure>[0];
  signals: JoinSignals;
  expect: JoinFailureReason;
};

const CASES: Case[] = [
  // — #1058's two populations, told apart by the timing signature and nothing else —
  {
    name: '#1058 Meet: 13min in the lobby against a 10min budget → admission_timeout',
    outcome: 'timeout',
    signals: { reachedLobby: true, timeToLobbyMs: 7_000, timeInLobbyMs: 780_000, totalMs: 787_000, lobbyBudgetMs: BUDGET_MS,
               detail: 'Bot is still in the Google Meet waiting room after timeout — host did not admit' },
    expect: 'admission_timeout',
  },
  {
    name: '#1058 Teams: dead at ~20s having never seen a lobby → never_reached_lobby',
    outcome: 'error',
    signals: { reachedLobby: false, totalMs: 19_600,
               detail: 'Error: Bot failed to join the Teams meeting - no meeting indicators found after polling' },
    expect: 'never_reached_lobby',
  },
  {
    name: 'a lobby exit at 3% of the budget with no denial reason → platform_rejection, not a timeout',
    outcome: 'error',
    signals: { reachedLobby: true, timeInLobbyMs: 19_000, totalMs: 21_000, lobbyBudgetMs: BUDGET_MS,
               detail: 'meeting ended or bot removed from the waiting room' },
    expect: 'platform_rejection',
  },
  {
    name: 'a lobby exit at exactly 90% of the budget is already an expiry (boundary)',
    outcome: 'error',
    signals: { reachedLobby: true, timeInLobbyMs: 540_000, lobbyBudgetMs: BUDGET_MS },
    expect: 'admission_timeout',
  },
  {
    name: 'one millisecond under the 90% boundary is still a rejection',
    outcome: 'error',
    signals: { reachedLobby: true, timeInLobbyMs: 539_999, lobbyBudgetMs: BUDGET_MS },
    expect: 'platform_rejection',
  },
  // — verdicts that are already precise —
  {
    name: 'a typed denial is a rejection whatever the clock says',
    outcome: 'rejected',
    signals: { reachedLobby: true, timeInLobbyMs: 590_000, lobbyBudgetMs: BUDGET_MS,
               detail: 'Bot admission was rejected by meeting admin' },
    expect: 'platform_rejection',
  },
  {
    name: 'a bot-detection block is the platform refusing us → platform_rejection',
    outcome: 'blocked',
    signals: { reachedLobby: false, detail: 'reCAPTCHA challenge presented' },
    expect: 'platform_rejection',
  },
  {
    name: 'a signed-out profile → auth_session_missing',
    outcome: 'auth_missing',
    signals: { reachedLobby: false, detail: 'Browser profile signed out — cannot authenticate with Google.' },
    expect: 'auth_session_missing',
  },
  {
    name: 'a user stop in the lobby → stopped_before_admission',
    outcome: 'stopped',
    signals: { reachedLobby: true, timeInLobbyMs: 12_000, lobbyBudgetMs: BUDGET_MS },
    expect: 'stopped_before_admission',
  },
  // — transport markers outrank the stage buckets —
  {
    name: 'a net:: error names a navigation failure even from inside the lobby',
    outcome: 'error',
    signals: { reachedLobby: true, timeInLobbyMs: 4_000, lobbyBudgetMs: BUDGET_MS,
               detail: 'page.goto: net::ERR_NAME_NOT_RESOLVED at https://meet.google.com/abc' },
    expect: 'navigation_failure',
  },
  {
    name: 'the Teams sign-in redirect → navigation_failure',
    outcome: 'error',
    signals: { reachedLobby: false, totalMs: 6_400,
               detail: 'teams_auth_redirect: https://login.microsoftonline.com/common/oauth2/v2.0/authorize' },
    expect: 'navigation_failure',
  },
  {
    name: 'a control plane down at boot → navigation_failure (we never navigated at all)',
    outcome: 'error',
    signals: { reachedLobby: false,
               detail: 'control_plane_unreachable: control plane unreachable at boot (meeting_api_callback, redis); refused to join' },
    expect: 'navigation_failure',
  },
  // — honest ignorance —
  {
    name: 'a lobby timeout verdict with no measurements at all is still an admission_timeout',
    outcome: 'timeout',
    signals: {},
    expect: 'admission_timeout',
  },
  {
    name: 'a bare error with nothing observed → unknown, never a guess',
    outcome: 'error',
    signals: {},
    expect: 'unknown',
  },
];

for (const c of CASES) {
  const got = classifyJoinFailure(c.outcome, c.signals);
  check(c.name, got === c.expect, `got ${got}`);
}

// ══ 2. attribution ══════════════════════════════════════════════════════════════════════════════

console.log('\n=== 2. attribution: typed reason → who it belongs to ===');

const ATTRIBUTION: Array<[JoinFailureReason, JoinFailureAttribution]> = [
  ['platform_rejection', 'host_action'],
  ['admission_timeout', 'host_action'],
  ['auth_session_missing', 'system_fault'],
  ['never_reached_lobby', 'system_fault'],
  ['navigation_failure', 'system_fault'],
  ['stopped_before_admission', 'user_action'],
  ['unknown', 'unknown'],
];

for (const [reason, expected] of ATTRIBUTION) {
  const got = attributeJoinFailure(reason);
  check(`${reason} → ${expected}`, got === expected, `got ${got}`);
}

check(
  'a Zoom automated-join block reattributes host_action → exogenous_platform',
  attributeJoinFailure(
    'platform_rejection',
    '[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins',
  ) === 'exogenous_platform',
);
check(
  'a captcha wall is exogenous, not the host declining',
  attributeJoinFailure('platform_rejection', 'reCAPTCHA challenge presented') === 'exogenous_platform',
);
check(
  'an admission wait that ended because OUR selectors went blind is a system_fault',
  attributeJoinFailure('admission_timeout', 'no meeting indicators found after polling') === 'system_fault',
);
check(
  'a plain lobby expiry stays with the host (no override without evidence)',
  attributeJoinFailure('admission_timeout', 'host did not admit within the waiting-room timeout') === 'host_action',
);
check(
  'the override is scoped: the same admission-flow words do not move a REJECTION',
  attributeJoinFailure('platform_rejection', 'no meeting indicators found') === 'host_action',
);
check(
  'a user stop is never a system fault, whatever the detail says',
  attributeJoinFailure('stopped_before_admission', 'no meeting indicators found; net::ERR_FAILED') === 'user_action',
);

// The gate metric's whole point: only ONE of the two #1058 populations is ours.
check(
  'GATE: the Meet lobby expiry does NOT count against the system-failure rate',
  buildJoinEvidence('timeout', 'awaiting_admission', CASES[0].signals)?.attribution === 'host_action',
);
check(
  'GATE: the Teams never-reached-lobby DOES count against it',
  buildJoinEvidence('error', 'joining', CASES[1].signals)?.attribution === 'system_fault',
);

// ══ 3. the persisted block ══════════════════════════════════════════════════════════════════════

console.log('\n=== 3. the evidence block that gets persisted ===');

const meetTimeout = buildJoinEvidence('timeout', 'awaiting_admission', CASES[0].signals)!;
check('carries the typed reason', meetTimeout.reason === 'admission_timeout');
check('carries the attribution', meetTimeout.attribution === 'host_action');
check('carries the stage', meetTimeout.stage === 'awaiting_admission');
check('is tagged first-hand (source=bot)', meetTimeout.source === 'bot');
check('carries all three stage timings', meetTimeout.timings?.time_to_lobby_ms === 7_000
  && meetTimeout.timings?.time_in_lobby_ms === 780_000 && meetTimeout.timings?.total_ms === 787_000);
check('carries the budget the wait is measured against', meetTimeout.lobby_budget_ms === BUDGET_MS);
check('carries the platform signal verbatim', meetTimeout.detail?.includes('host did not admit') === true);

const noTimings = buildJoinEvidence('error', 'joining', { reachedLobby: false, detail: 'x' })!;
check('an unmeasured timing is ABSENT, never a fabricated zero', noTimings.timings === undefined);
check('an unmeasured budget is absent too', noTimings.lobby_budget_ms === undefined);

const longDetail = buildJoinEvidence('error', 'joining', { detail: 'E'.repeat(5_000) })!;
check('an oversized platform signal is capped, not dropped',
  (longDetail.detail?.length ?? 0) === DETAIL_MAX_CHARS && longDetail.detail!.endsWith('…'));

// ══ 4. the driver's stopwatch ═══════════════════════════════════════════════════════════════════

console.log('\n=== 4. the join stopwatch (the discriminator #1058 needed) ===');

{
  let t = 1_000;
  const timer = createJoinTimer(() => t);
  t = 9_000;
  timer.markLobby();
  t = 15_000;
  timer.markLobby();              // a repeated lobby report must not move the mark
  t = 789_000;
  const s = timer.signals();
  check('time-to-lobby measures from join start to the FIRST lobby sighting', s.timeToLobbyMs === 8_000);
  check('a repeated awaiting_admission does not restart the lobby clock', s.timeToLobbyMs === 8_000);
  check('time-in-lobby measures from the lobby sighting to the verdict', s.timeInLobbyMs === 780_000);
  check('total measures the whole attempt', s.totalMs === 788_000);
  check('reachedLobby is true once the lobby was seen', s.reachedLobby === true);
}
{
  let t = 0;
  const timer = createJoinTimer(() => t);
  t = 19_600;
  const s = timer.signals({ detail: 'no meeting indicators found' });
  check('a bot that never saw a lobby reports reachedLobby=false (a POSITIVE finding)', s.reachedLobby === false);
  check('…and no lobby timings at all', s.timeToLobbyMs === undefined && s.timeInLobbyMs === undefined);
  check('…but still a total (the ~20s Teams signature)', s.totalMs === 19_600);
  check('caller-supplied signals ride along', s.detail === 'no meeting indicators found');
}

// ══ 5. replayable fixtures — one per failure class, through the REAL orchestrator ════════════════

console.log('\n=== 5. replayable fixtures: recorded signal sequences → terminal evidence ===');

const invocation = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  redisUrl: 'redis://r:6379', connectionId: 'sess-fixture', container_name: 'mtg-fixture-bot',
  nativeMeetingId: 'abc-defg-hij',
  automaticLeave: { waitingRoomTimeout: BUDGET_MS },
  ...over,
});

const recordingSink = (): LifecycleSink & { readonly events: LifecycleEvent[] } => {
  const events: LifecycleEvent[] = [];
  return { events, async emit(e: LifecycleEvent) { events.push(e); } };
};

/** A join driver replaying ONE recorded attempt: the intermediate states it reported, then either a
 *  `JoinResult` or a throw — exactly the two shapes the real driver produces. */
const replayJoin = (tape: {
  reports?: ('awaiting_admission' | 'needs_help')[];
  result?: JoinResult;
  throws?: unknown;
  lastSignals?: JoinSignals;
}): JoinDriver => ({
  async join(report) {
    for (const r of tape.reports ?? []) await report(r);
    if (tape.throws !== undefined) throw tape.throws;
    return tape.result as JoinResult;
  },
  onRemoval() { return () => { /* */ }; },
  async leave() { /* */ },
  async withdraw() { /* */ },
  lastSignals: () => tape.lastSignals,
});

type Fixture = {
  name: string;
  driver: JoinDriver;
  expectReason: JoinFailureReason;
  expectAttribution: JoinFailureAttribution;
  expectStage: 'requested' | 'joining' | 'awaiting_admission';
};

const FIXTURES: Fixture[] = [
  {
    // The #1058 Google Meet population, recorded: reached the lobby, waited out the budget.
    name: 'meet-admission-timeout',
    driver: replayJoin({
      reports: ['awaiting_admission'],
      result: {
        outcome: 'timeout',
        reason: 'Bot is still in the Google Meet waiting room after timeout — host did not admit',
        signals: { reachedLobby: true, timeToLobbyMs: 7_000, timeInLobbyMs: 780_000, totalMs: 787_000,
                   lobbyBudgetMs: BUDGET_MS,
                   detail: 'Bot is still in the Google Meet waiting room after timeout — host did not admit' },
      },
    }),
    expectReason: 'admission_timeout',
    expectAttribution: 'host_action',
    expectStage: 'awaiting_admission',
  },
  {
    // The #1058 Teams population, recorded: dead at ~20s, no lobby ever rendered.
    name: 'teams-never-reached-lobby',
    driver: replayJoin({
      throws: new Error('Bot failed to join the Teams meeting - no meeting indicators found after polling'),
      lastSignals: { reachedLobby: false, totalMs: 19_600,
                     detail: 'Bot failed to join the Teams meeting - no meeting indicators found after polling' },
    }),
    expectReason: 'never_reached_lobby',
    expectAttribution: 'system_fault',
    expectStage: 'joining',
  },
  {
    name: 'host-denied-admission',
    driver: replayJoin({
      reports: ['awaiting_admission'],
      result: { outcome: 'rejected', reason: 'Bot admission was rejected by meeting admin',
                signals: { reachedLobby: true, timeInLobbyMs: 31_000, totalMs: 38_000, lobbyBudgetMs: BUDGET_MS,
                           detail: 'Bot admission was rejected by meeting admin' } },
    }),
    expectReason: 'platform_rejection',
    expectAttribution: 'host_action',
    expectStage: 'awaiting_admission',
  },
  {
    name: 'zoom-blocks-automated-joins',
    driver: replayJoin({
      result: { outcome: 'rejected',
                reason: '[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins and requires Zoom RTMS',
                signals: { reachedLobby: false, totalMs: 12_100,
                           detail: '[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins' } },
    }),
    expectReason: 'platform_rejection',
    expectAttribution: 'exogenous_platform',
    expectStage: 'joining',
  },
  {
    name: 'signed-out-profile',
    driver: replayJoin({
      result: { outcome: 'auth_missing', reason: 'Browser profile signed out — cannot authenticate with Google.',
                signals: { reachedLobby: false, totalMs: 9_400,
                           detail: 'Browser profile signed out — cannot authenticate with Google.' } },
    }),
    expectReason: 'auth_session_missing',
    expectAttribution: 'system_fault',
    expectStage: 'joining',
  },
  {
    name: 'navigation-never-landed',
    driver: replayJoin({
      throws: new Error('page.goto: net::ERR_NAME_NOT_RESOLVED at https://meet.google.com/abc-defg-hij'),
      lastSignals: { reachedLobby: false, totalMs: 3_200,
                     detail: 'page.goto: net::ERR_NAME_NOT_RESOLVED' },
    }),
    expectReason: 'navigation_failure',
    expectAttribution: 'system_fault',
    expectStage: 'joining',
  },
];

async function runFixtures(): Promise<void> {
  for (const f of FIXTURES) {
    const lifecycle = recordingSink();
    const orch = createOrchestrator(invocation(), {
      lifecycle, join: f.driver, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness(),
    });
    const result = await orch.run();
    const terminal = lifecycle.events[lifecycle.events.length - 1];
    const ev = terminal.join_evidence;
    check(`[${f.name}] terminal is failed`, terminal.status === 'failed' && result.status === 'failed');
    check(`[${f.name}] the terminal event still conforms to sealed lifecycle.v1`,
      validateLifecycle(terminal) === true, JSON.stringify(validateLifecycle.errors));
    check(`[${f.name}] carries join_evidence at all (the #1059 regression)`, ev !== undefined);
    check(`[${f.name}] reason = ${f.expectReason}`, ev?.reason === f.expectReason, `got ${ev?.reason}`);
    check(`[${f.name}] attribution = ${f.expectAttribution}`,
      ev?.attribution === f.expectAttribution, `got ${ev?.attribution}`);
    check(`[${f.name}] stage = ${f.expectStage}`, ev?.stage === f.expectStage, `got ${ev?.stage}`);
    check(`[${f.name}] a human reason text is stamped (never null)`,
      typeof terminal.reason === 'string' && terminal.reason.length > 0);
    check(`[${f.name}] the raw platform signal is preserved`,
      typeof ev?.detail === 'string' && ev!.detail!.length > 0);
  }

  // The user-stop fixture takes the ABORT path: the join blocks in the lobby and a `leave` act
  // arrives while the bot is still knocking. It must NOT be attributed to the system.
  {
    const lifecycle = recordingSink();
    let fireAct: ((a: { action: 'leave' }) => void) | undefined;
    const driver: JoinDriver = {
      async join(report) { await report('awaiting_admission'); return new Promise<JoinOutcome>(() => { /* blocks */ }); },
      onRemoval() { return () => { /* */ }; },
      async leave() { /* */ },
      async withdraw() { /* */ },
      lastSignals: () => ({ reachedLobby: true, timeToLobbyMs: 5_000, timeInLobbyMs: 41_000, totalMs: 46_000,
                            lobbyBudgetMs: BUDGET_MS }),
    };
    const orch = createOrchestrator(invocation(), {
      lifecycle, join: driver, pipeline: noopPipeline(),
      acts: noopActs((fire) => { fireAct = fire as (a: { action: 'leave' }) => void; }),
      aloneness: noopAloneness(),
    });
    const running = orch.run();
    await new Promise((r) => setTimeout(r, 5));
    fireAct?.({ action: 'leave' });
    await running;
    const terminal = lifecycle.events[lifecycle.events.length - 1];
    const ev = terminal.join_evidence;
    check('[user-stop-in-lobby] terminal conforms to sealed lifecycle.v1', validateLifecycle(terminal) === true);
    check('[user-stop-in-lobby] reason = stopped_before_admission', ev?.reason === 'stopped_before_admission',
      `got ${ev?.reason}`);
    check('[user-stop-in-lobby] attribution = user_action — NOT a system failure',
      ev?.attribution === 'user_action', `got ${ev?.attribution}`);
    check('[user-stop-in-lobby] the lobby timings survive the abort path',
      ev?.timings?.time_in_lobby_ms === 41_000);
  }

  // ══ 6. fail-open ═══════════════════════════════════════════════════════════════════════════════

  console.log('\n=== 6. fail-open: a broken reporter must not change the run ===');

  {
    const lifecycle = recordingSink();
    // A driver whose evidence channel is hostile: `lastSignals` throws. The run must still end
    // exactly as it would have, with a conformant terminal — evidence is the only thing that degrades.
    const driver: JoinDriver = {
      async join() { throw new Error('browser exploded'); },
      onRemoval() { return () => { /* */ }; },
      async leave() { /* */ },
      async withdraw() { /* */ },
      lastSignals() { throw new Error('evidence channel is broken'); },
    };
    const orch = createOrchestrator(invocation(), {
      lifecycle, join: driver, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness(),
    });
    const result = await orch.run();
    const terminal = lifecycle.events[lifecycle.events.length - 1];
    check('[fail-open] the run still terminates failed/join_failure',
      result.status === 'failed' && result.completionReason === 'join_failure');
    check('[fail-open] the exit code is unchanged', result.exitCode === 1);
    check('[fail-open] the terminal event still conforms', validateLifecycle(terminal) === true);
    check('[fail-open] the human reason text still lands', terminal.reason?.includes('browser exploded') === true);
    check('[fail-open] evidence degrades to a classification without signals, not to a throw',
      terminal.join_evidence === undefined || typeof terminal.join_evidence.reason === 'string');
  }

  check('a garbage detail never throws', cleanDetail({ nope: true } as unknown) === undefined);
  check('an unknown outcome degrades to unknown, not an exception',
    classifyJoinFailure('nonsense' as never, {}) === 'unknown');
  check('an unknown reason attributes to unknown',
    attributeJoinFailure('nonsense' as never) === 'unknown');
  check('NaN / negative timings are discarded rather than persisted',
    buildJoinEvidence('error', 'joining', { totalMs: Number.NaN, timeInLobbyMs: -5 })?.timings === undefined);

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  if (failed > 0) process.exit(1);
  console.log('\n✅ join-evidence (L2): the taxonomy classifies, the attribution axis is gate-ready, every failure class replays, and the reporter is fail-open.');
}

void runFixtures();
