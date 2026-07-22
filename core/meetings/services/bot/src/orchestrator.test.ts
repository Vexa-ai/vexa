/**
 * L2 — orchestrator unit harness (ARCHITECTURE.md §5). Drives the meeting state machine
 * OFFLINE with in-memory fakes for EVERY port (no browser, no redis, no STT) and asserts:
 *   • it walks the full lifecycle joining → awaiting_admission → active → completed;
 *   • every emitted event CONFORMS to lifecycle.v1 (validated by ajv against the published
 *     lifecycle.schema.json — P8) and every transition is legal (canTransition);
 *   • the failure paths (join throws, admission rejected, pipeline-start fails) emit the
 *     right failure_stage + completion_reason;
 *   • host removal → completed(evicted); the time cap → max_bot_time_exceeded;
 *   • a fake transcript.v1 segment routes through the pipeline to the TranscriptSink.
 * This is the payoff of ports/adapters: the whole control flow proves in milliseconds.
 * Run: npx tsx src/orchestrator.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createOrchestrator, CONTROL_PLANE_UNREACHABLE, CONTROL_PLANE_UNREACHABLE_EXIT, type MeetingResult } from './orchestrator.js';
import { createLivePipeline } from './pipeline.js';
import { canTransition, type Act, type BotStatus, type LifecycleEvent, type TranscriptSegment } from './contracts.js';
import type { ActsSource, JoinDriver, JoinOutcome, LifecycleSink, TranscriptSink, PrimaryReachability } from './ports.js';
import type { Invocation } from './config.js';
import { noopAloneness, controlledAloneness, noopPipeline, noopActs } from './test-doubles.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── lifecycle.v1 validator (ajv against the PUBLISHED schema, loaded by path) ──
const HERE = dirname(fileURLToPath(import.meta.url));
const LIFECYCLE_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'lifecycle.v1', 'lifecycle.schema.json');
const lcSchema = JSON.parse(readFileSync(LIFECYCLE_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(lcSchema);
const validateLifecycle: ValidateFunction = ajv.compile({ $ref: `${lcSchema.$id}#/$defs/LifecycleEvent` });

// ── fakes ──
const inv = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  redisUrl: 'redis://r:6379', connectionId: 'sess-uid', container_name: 'mtg-abc123-bot',
  nativeMeetingId: 'abc-defg-hij',
  ...over,
});

const recordingSink = (): LifecycleSink & { readonly events: LifecycleEvent[] } => {
  const events: LifecycleEvent[] = [];
  return { events, async emit(e: LifecycleEvent) { events.push(e); } };
};
const mockJoin = (outcome: JoinOutcome, onRemovalRef?: (fire: () => void) => void): JoinDriver => ({
  async join(report) { await report('awaiting_admission'); if (outcome === 'admitted') await report('active'); return outcome; },
  onRemoval(cb) { onRemovalRef?.(cb); return () => { /* */ }; },
  async leave() { /* */ },
  async withdraw() { /* */ },
});

/** A join that BLOCKS in the waiting room (awaiting_admission) until aborted — models the real lobby
 *  wait. `join()` never resolves on its own; only a pre-active stop (which resolves the orchestrator's
 *  abort race) ends it. Records whether `withdraw`/`leave` were invoked. */
const lobbyBlockingJoin = () => {
  const calls = { withdraw: 0, leave: 0, withdrawReason: '' as string };
  const driver: JoinDriver = {
    async join(report) { await report('awaiting_admission'); return new Promise<JoinOutcome>(() => { /* never resolves */ }); },
    onRemoval() { return () => { /* */ }; },
    async leave() { calls.leave++; },
    async withdraw(reason) { calls.withdraw++; calls.withdrawReason = reason; },
  };
  return { driver, calls };
};

const seq = (e: LifecycleEvent[]) => e.map((x) => x.status);
const last = (e: LifecycleEvent[]) => e[e.length - 1];
const allLegal = (s: BotStatus[]) => s.every((st, i) => i === 0 || st === s[i - 1] || canTransition(s[i - 1], st));
const allConform = (e: LifecycleEvent[]) => e.every((ev) => validateLifecycle(ev));

async function main(): Promise<void> {
  // ── happy: admitted → `leave` act → completed(stopped) ──
  {
    const lc = recordingSink();
    const pipe = noopPipeline();
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const o = createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('admitted'), pipeline: pipe, acts: noopActs((f) => { fireLeave = f; }), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => fireLeave({ action: 'leave' }), 5);
    const res = await runP;
    check('happy: exit 0 / completed', res.exitCode === 0 && res.status === 'completed');
    check('happy: sequence joining→awaiting_admission→active→completed',
      JSON.stringify(seq(lc.events)) === JSON.stringify(['joining', 'awaiting_admission', 'active', 'completed']),
      JSON.stringify(seq(lc.events)));
    check('happy: every transition legal', allLegal(seq(lc.events)));
    check('happy: every event conforms to lifecycle.v1', allConform(lc.events), ajv.errorsText(validateLifecycle.errors));
    check('happy: completion_reason=stopped', last(lc.events).completion_reason === 'stopped');
    check('happy: joining carried container_id', lc.events[0].container_id === 'mtg-abc123-bot');
    check('happy: pipeline started then stopped', pipe.started === false);
  }

  // ── leave via the orchestrator.handle entrypoint (the acts adapter / test surface) ──
  {
    const lc = recordingSink();
    const o = createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('admitted'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => { void o.handle({ action: 'leave' }); }, 5);
    const res = await runP;
    check('handle(leave): completed(stopped)', res.status === 'completed' && last(lc.events).completion_reason === 'stopped');
  }

  // ── join throws → failed(joining/join_failure) ──
  {
    const lc = recordingSink();
    const join: JoinDriver = { async join() { throw new Error('navigation failed'); }, onRemoval() { return () => {}; }, async leave() {}, async withdraw() {} };
    const res = await createOrchestrator(inv(), { lifecycle: lc, join, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() }).run();
    check('join-error: failed / exit 1', res.status === 'failed' && res.exitCode === 1);
    check('join-error: failure_stage=joining', last(lc.events).failure_stage === 'joining');
    check('join-error: completion_reason=join_failure', last(lc.events).completion_reason === 'join_failure');
    // The thrown message is the ONLY channel a join-phase cause has to `last_error`: the sealed
    // CompletionReason enum cannot name platform-specific causes, so a typed brick throw (e.g.
    // @vexa/join's TeamsJoinRedirectError, #915) carries its discriminator in this text.
    check('join-error: the thrown reason text reaches the terminal event',
      String(last(lc.events).reason ?? '').includes('navigation failed'));
    check('join-error: no active emitted', !seq(lc.events).includes('active'));
    check('join-error: events conform', allConform(lc.events));
  }

  // ── admission rejected → failed(awaiting_admission/awaiting_admission_rejected) ──
  {
    const lc = recordingSink();
    const res = await createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('rejected'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() }).run();
    check('rejected: failed', res.status === 'failed');
    check('rejected: failure_stage=awaiting_admission', last(lc.events).failure_stage === 'awaiting_admission');
    check('rejected: completion_reason=awaiting_admission_rejected', last(lc.events).completion_reason === 'awaiting_admission_rejected');
    check('rejected: sequence legal (joining→awaiting_admission→failed)', allLegal(seq(lc.events)));
    check('rejected: events conform', allConform(lc.events));
  }

  // ── admission timeout → failed(awaiting_admission_timeout) ──
  {
    const lc = recordingSink();
    const res = await createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('timeout'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() }).run();
    check('timeout: completion_reason=awaiting_admission_timeout', last(lc.events).completion_reason === 'awaiting_admission_timeout');
  }

  // ── pipeline.start throws → failed(active/...) ──
  {
    const lc = recordingSink();
    const pipe: Pipeline = { async start() { throw new Error('capture init failed'); }, async stop() {} };
    const res = await createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('admitted'), pipeline: pipe, acts: noopActs(), aloneness: noopAloneness() }).run();
    check('pipeline-fail: failed', res.status === 'failed' && res.exitCode === 1);
    check('pipeline-fail: failure_stage=active', last(lc.events).failure_stage === 'active');
    check('pipeline-fail: reached active first', seq(lc.events).includes('active'));
    check('pipeline-fail: events conform', allConform(lc.events));
  }

  // ── host removal while active → completed(evicted) ──
  {
    const lc = recordingSink();
    let fireRemoval: () => void = () => {};
    const join = mockJoin('admitted', (fire) => { fireRemoval = fire; });
    const o = createOrchestrator(inv(), { lifecycle: lc, join, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => fireRemoval(), 5);
    const res = await runP;
    check('removal: completed(evicted)', res.status === 'completed' && last(lc.events).completion_reason === 'evicted');
    check('removal: sequence reached active', seq(lc.events).includes('active'));
  }

  // ── hard time cap → completed(max_bot_time_exceeded) ──
  {
    const lc = recordingSink();
    const o = createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('admitted'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const res = await o.run({ maxActiveMs: 5 });
    check('time-cap: completed(max_bot_time_exceeded)', res.status === 'completed' && last(lc.events).completion_reason === 'max_bot_time_exceeded');
  }

  // ── silence verdict while active → one schema-valid completed(left_alone) ──
  {
    const lc = recordingSink();
    let fireAlone: () => void = () => {};
    let stopped = 0;
    let fireRemoval: () => void = () => {};
    const join = mockJoin('admitted', (fire) => { fireRemoval = fire; });
    const aloneness = controlledAloneness((fire) => { fireAlone = fire; }, () => stopped++);
    const o = createOrchestrator(inv(), {
      lifecycle: lc, join, pipeline: noopPipeline(), acts: noopActs(), aloneness,
    });
    const runP = o.run();
    setTimeout(() => { fireAlone(); fireAlone(); fireRemoval(); }, 5);
    const res = await runP;
    const terminals = lc.events.filter((event) => event.status === 'completed' || event.status === 'failed');
    check('aloneness: completed(left_alone)',
      res.status === 'completed' && res.completionReason === 'left_alone' && last(lc.events).completion_reason === 'left_alone');
    check('aloneness: every lifecycle event conforms', allConform(lc.events), ajv.errorsText(validateLifecycle.errors));
    check('aloneness: double verdict and removal race emit one terminal', terminals.length === 1, JSON.stringify(terminals));
    check('aloneness: teardown stops the source', stopped === 1, `stops=${stopped}`);
  }

  // ── a fake transcript.v1 segment routes through the pipeline → TranscriptSink ──
  {
    const published: TranscriptSegment[] = [];
    const sink: TranscriptSink = { async publish(s) { published.push(s); } };
    // A pipeline that, on start, pushes one segment through the injected sink (what the live
    // gmeet/mixed pipeline does per confirmed utterance). The orchestrator owns start/stop;
    // this asserts the wire from engine → transcript.v1 egress.
    const seg: TranscriptSegment = { segment_id: 'sess-uid:s1:0', speaker: 'Alice', text: 'hello world', start: 0, end: 1.2, completed: true, source: 'glow-bound' };
    const pipe: Pipeline = { async start() { await sink.publish(seg); }, async stop() {} };
    const lc = recordingSink();
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const o = createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('admitted'), pipeline: pipe, acts: noopActs((f) => { fireLeave = f; }), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => fireLeave({ action: 'leave' }), 5);
    await runP;
    check('transcript: one segment reached the sink', published.length === 1 && published[0].text === 'hello world', JSON.stringify(published));

    // and it conforms to transcript.v1 (P8)
    const TX_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'transcript.v1', 'transcript.schema.json');
    const txSchema = JSON.parse(readFileSync(TX_SCHEMA, 'utf8'));
    const ajv2 = new Ajv2020({ strict: false, allErrors: true });
    addFormats(ajv2);
    ajv2.addSchema(txSchema);
    const validateSeg = ajv2.compile({ $ref: `${txSchema.$id}#/$defs/TranscriptSegment` });
    check('transcript: segment conforms to transcript.v1', !!validateSeg(published[0]), ajv2.errorsText(validateSeg.errors));
  }

  // ── REGRESSION (code-review): pipeline.start fails AFTER admission → LEAVE (no ghost bot) ──
  {
    const lc = recordingSink();
    let left = 0;
    const join: JoinDriver = {
      async join(report) { await report('awaiting_admission'); await report('active'); return 'admitted'; },
      onRemoval() { return () => {}; }, async leave() { left++; }, async withdraw() {},
    };
    const pipe: Pipeline = { async start() { throw new Error('capture init failed'); }, async stop() {} };
    const res = await createOrchestrator(inv(), { lifecycle: lc, join, pipeline: pipe, acts: noopActs(), aloneness: noopAloneness() }).run();
    check('pipeline-fail: bot LEFT the meeting (no ghost participant)', left === 1);
    check('pipeline-fail: still failed / exit 1', res.status === 'failed' && res.exitCode === 1);
  }

  // ── REGRESSION: stop() (the SIGTERM seam) ends the active phase → completed(stopped) ──
  {
    const lc = recordingSink();
    const o = createOrchestrator(inv(), { lifecycle: lc, join: mockJoin('admitted'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => o.stop(), 5);
    const res = await runP;
    check('stop(): completed(stopped) — worker is disposable, never hangs after active',
      res.status === 'completed' && last(lc.events).completion_reason === 'stopped');
  }

  // ── REGRESSION: fire-and-forget driver reports stay ORDERED through a slow sink (no reorder) ──
  {
    const events: LifecycleEvent[] = [];
    const slowLc: LifecycleSink = { async emit(e) {
      if (e.status === 'awaiting_admission') await new Promise((r) => setTimeout(r, 12));   // delay the FIRST report
      events.push(e);
    } };
    const join: JoinDriver = {   // fires BOTH reports without awaiting
      async join(report) { void report('awaiting_admission'); void report('active'); return 'admitted'; },
      onRemoval() { return () => {}; }, async leave() {}, async withdraw() {},
    };
    const o = createOrchestrator(inv(), { lifecycle: slowLc, join, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => o.stop(), 30);
    await runP;
    check('reports: serialized in emit order despite a slow awaiting_admission sink',
      JSON.stringify(seq(events).slice(0, 3)) === JSON.stringify(['joining', 'awaiting_admission', 'active']), JSON.stringify(seq(events)));
  }

  // ── Bug 2: stop() at AWAITING_ADMISSION → WITHDRAW the join request (no waiting-room orphan) ──
  // A stop/SIGTERM while the bot is still in the lobby must not merely arm the force-exit watchdog and
  // SIGKILL — that leaves the "asking to join" request live. The orchestrator races the (lobby-blocked)
  // join against a pre-active abort; stop() resolves it, run() calls join.withdraw() to cancel/close the
  // pre-join screen, then terminates failed(awaiting_admission / stopped, exit 0 — a clean user stop).
  {
    const lc = recordingSink();
    const { driver, calls } = lobbyBlockingJoin();
    const o = createOrchestrator(inv(), { lifecycle: lc, join: driver, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => o.stop(), 5);   // stop WHILE blocked in the waiting room
    const res = await runP;
    check('withdraw: join.withdraw() invoked exactly once (join request cancelled)', calls.withdraw === 1, `withdraw=${calls.withdraw}`);
    check('withdraw: reason forwarded to the withdraw', calls.withdrawReason === 'stopped', calls.withdrawReason);
    check('withdraw: reached awaiting_admission then terminated (no active)', seq(lc.events).includes('awaiting_admission') && !seq(lc.events).includes('active'), JSON.stringify(seq(lc.events)));
    check('withdraw: terminal failed(awaiting_admission / stopped), exit 0', res.status === 'failed' && res.exitCode === 0 && last(lc.events).failure_stage === 'awaiting_admission' && last(lc.events).completion_reason === 'stopped', JSON.stringify(last(lc.events)));
    check('withdraw: sequence legal + conforms', allLegal(seq(lc.events)) && allConform(lc.events), ajv.errorsText(validateLifecycle.errors));
    check('withdraw: did NOT SIGKILL-orphan — the run resolved on its own (no watchdog needed)', true);
  }

  // ── #889: a `leave` ACT delivered while BLOCKED in the lobby (awaiting_admission) → WITHDRAW ──
  // The canonical user Stop is a `leave` command on the bot's command channel (meeting-api's stop.py
  // publishes `bot_commands:meeting:{id}` `{action:leave}`). The orchestrator used to subscribe to
  // acts only AFTER admission, so a `leave` arriving while the bot was still knocking in the lobby was
  // never heard (redis pub/sub has no backlog) — and handle()'s `leave` routed to the ACTIVE-phase end
  // (signalEnd), not the pre-active abort. Net: the bot kept "asking to join" after Stop (#889). This
  // asserts (a) the acts subscription is live DURING the lobby and (b) a lobby `leave` withdraws.
  {
    const lc = recordingSink();
    const { driver, calls } = lobbyBlockingJoin();
    let fireLeave: (a: Act) => void = () => {};
    let subscribed = false;
    const acts: ActsSource = {
      subscribe(handler) { subscribed = true; fireLeave = (a) => void handler(a); return () => { /* */ }; },
    };
    const o = createOrchestrator(inv(), { lifecycle: lc, join: driver, pipeline: noopPipeline(), acts, aloneness: noopAloneness() });
    const runP = o.run();
    await new Promise((r) => setTimeout(r, 10));   // let the machine reach awaiting_admission + subscribe
    check('#889: acts subscribed DURING the lobby (before admission)', subscribed, `subscribed=${subscribed}`);
    fireLeave({ action: 'leave' });
    // Bound the wait: with the bug the run never resolves (the leave is dropped) — the sentinel proves it.
    const res = await Promise.race<MeetingResult>([
      runP,
      new Promise<MeetingResult>((r) => setTimeout(() => r({ exitCode: -1, status: 'joining' as BotStatus }), 500)),
    ]);
    check('#889: lobby leave act → withdraw invoked exactly once', calls.withdraw === 1, `withdraw=${calls.withdraw}`);
    check('#889: withdraw reason forwarded (stopped)', calls.withdrawReason === 'stopped', calls.withdrawReason);
    check('#889: terminal failed(awaiting_admission / stopped), exit 0', res.status === 'failed' && res.exitCode === 0 && last(lc.events).failure_stage === 'awaiting_admission' && last(lc.events).completion_reason === 'stopped', JSON.stringify(res) + ' / ' + JSON.stringify(last(lc.events)));
    check('#889: never reached active (withdrew from the lobby)', !seq(lc.events).includes('active'), JSON.stringify(seq(lc.events)));
    check('#889: sequence legal + conforms', allLegal(seq(lc.events)) && allConform(lc.events), ajv.errorsText(validateLifecycle.errors));
  }

  // ── Bug 2 (invariant): stop() while ACTIVE still uses the existing active-leave path, NOT withdraw ──
  {
    const lc = recordingSink();
    // mockJoin('admitted') reports active; withdraw must never be called on the active-stop path.
    let withdrew = 0;
    const join: JoinDriver = {
      async join(report) { await report('awaiting_admission'); await report('active'); return 'admitted'; },
      onRemoval() { return () => {}; }, async leave() {}, async withdraw() { withdrew++; },
    };
    const o = createOrchestrator(inv(), { lifecycle: lc, join, pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => o.stop(), 5);
    const res = await runP;
    check('active-stop: completed(stopped) via the active-leave path (unchanged)', res.status === 'completed' && last(lc.events).completion_reason === 'stopped');
    check('active-stop: withdraw NOT invoked (active leave, not a waiting-room withdraw)', withdrew === 0, `withdrew=${withdrew}`);
  }

  // ── #593 A4: a post-admission subsystem failure does NOT self-evict (the createLivePipeline seam) ──
  // Wire a REAL createLivePipeline whose page-side capture AND engine start both throw, into the
  // orchestrator. Before the fix (inline pipeline, three bare awaits) the first throw rejected
  // pipeline.start() → orchestrator catch → leave('pipeline_start_failed') + failed(active/join_failure)
  // ~immediately (the ~120 ms self-evict). After: start() resolves (faults surfaced loud), the bot
  // reaches active and STAYS until a normal end. NB this deliberately COEXISTS with the earlier
  // "pipeline.start genuinely throws ⇒ leave (no ghost)" test — that invariant is preserved; what
  // changed is that createLivePipeline no longer lets a recoverable subsystem failure BECOME a throw.
  {
    const lc = recordingSink();
    const leaveReasons: string[] = [];
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const join: JoinDriver = {
      async join(report) { await report('awaiting_admission'); await report('active'); return 'admitted'; },
      onRemoval() { return () => {}; },
      async leave(reason) { leaveReasons.push(String(reason)); },
      async withdraw() { /* */ },
    };
    const faults: string[] = [];
    const engine: Pipeline = { async start() { throw new Error('from_pretrained rejected (empty HF cache)'); }, async stop() { /* */ } };
    const pipeline = createLivePipeline({
      startCapture: async () => { throw { isTrusted: true, type: 'error' }; },   // the misdirecting page event
      engine,
      onFault: (stage) => faults.push(stage),
      retry: { attempts: 1, delayMs: 0 },
    });
    const o = createOrchestrator(inv({ platform: 'teams' }), { lifecycle: lc, join, pipeline, acts: noopActs((f) => { fireLeave = f; }), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => fireLeave({ action: 'leave' }), 10);
    const res = await runP;
    check('#593: reached active (post-admission capture handoff did not evict)', seq(lc.events).includes('active'), JSON.stringify(seq(lc.events)));
    check('#593: never emitted failed (no self-evict)', !seq(lc.events).includes('failed'), JSON.stringify(seq(lc.events)));
    check('#593: leave NEVER called with pipeline_start_failed', !leaveReasons.includes('pipeline_start_failed'), leaveReasons.join(','));
    check('#593: ended cleanly via the leave act → completed(stopped)', res.status === 'completed' && last(lc.events).completion_reason === 'stopped', JSON.stringify(last(lc.events)));
    check('#593: both subsystem faults surfaced loud (capture + engine)', faults.includes('capture-start') && faults.includes('engine-start'), faults.join(','));
  }

  // ── #530 reachability gate: BOTH channels down → refuse to join, exit 3, typed terminal ──
  // The FIRST `joining` emit is load-bearing. A sink whose emitReachable reports `unreachable` +
  // a secondary probe that reports redis down ⇒ the bot must NOT navigate to the meeting; it
  // terminates failed(requested) with the control_plane_unreachable attribution and exit 3.
  // RED AT BASE: before the gate, run() ignores reachability and proceeds to join.join().
  {
    const events: LifecycleEvent[] = [];
    const gateSink: LifecycleSink = {
      async emit(e) { events.push(e); },
      async emitReachable(e) { events.push(e); return 'unreachable' as PrimaryReachability; },
    };
    let joinCalls = 0;
    const spyJoin: JoinDriver = {
      async join(report) { joinCalls++; await report('awaiting_admission'); await report('active'); return 'admitted'; },
      onRemoval() { return () => {}; }, async leave() {}, async withdraw() {},
    };
    const res = await createOrchestrator(inv(), {
      lifecycle: gateSink, join: spyJoin, pipeline: noopPipeline(), acts: noopActs(),
      aloneness: noopAloneness(), reachability: { async probeSecondary() { return false; } },
    }).run();
    check('gate both-down: NO join attempted (refused before meeting navigation)', joinCalls === 0, `joinCalls=${joinCalls}`);
    check('gate both-down: exit code 3 (dedicated infra signal)', res.exitCode === CONTROL_PLANE_UNREACHABLE_EXIT, String(res.exitCode));
    check('gate both-down: terminal failed', res.status === 'failed');
    check('gate both-down: failure_stage=requested (distinct from a real join failure)', last(events).failure_stage === 'requested', JSON.stringify(last(events)));
    check('gate both-down: infra_fault=control_plane_unreachable', last(events).infra_fault === CONTROL_PLANE_UNREACHABLE, JSON.stringify(last(events)));
    check('gate both-down: unreachable_channels names both deps', JSON.stringify(last(events).unreachable_channels) === JSON.stringify(['meeting_api_callback', 'redis']), JSON.stringify(last(events).unreachable_channels));
    check('gate both-down: reason text carries the discriminator', (last(events).reason ?? '').startsWith(CONTROL_PLANE_UNREACHABLE), last(events).reason);
    check('gate both-down: exit_code on the event = 3', last(events).exit_code === CONTROL_PLANE_UNREACHABLE_EXIT, String(last(events).exit_code));
    check('gate both-down: terminal event still conforms to lifecycle.v1', allConform(events), ajv.errorsText(validateLifecycle.errors));
    check('gate both-down: never reached active', !seq(events).includes('active'), JSON.stringify(seq(events)));
  }

  // ── #530 gate: primary DOWN but SECONDARY up → proceed (either-channel rule) ──
  {
    const events: LifecycleEvent[] = [];
    const gateSink: LifecycleSink = {
      async emit(e) { events.push(e); },
      async emitReachable(e) { events.push(e); return 'unreachable' as PrimaryReachability; },
    };
    let joinCalls = 0;
    const spyJoin: JoinDriver = {
      async join(report) { joinCalls++; await report('awaiting_admission'); await report('active'); return 'admitted'; },
      onRemoval() { return () => {}; }, async leave() {}, async withdraw() {},
    };
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const o = createOrchestrator(inv(), {
      lifecycle: gateSink, join: spyJoin, pipeline: noopPipeline(), acts: noopActs((f) => { fireLeave = f; }),
      aloneness: noopAloneness(), reachability: { async probeSecondary() { return true; } },   // redis up
    });
    const runP = o.run();
    setTimeout(() => fireLeave({ action: 'leave' }), 5);
    const res = await runP;
    check('gate either-channel: join PROCEEDED (secondary up ⇒ can still report)', joinCalls === 1, `joinCalls=${joinCalls}`);
    check('gate either-channel: completed(stopped), not an infra abort', res.status === 'completed' && res.exitCode === 0, JSON.stringify(res));
    check('gate either-channel: no infra_fault emitted', !events.some((e) => e.infra_fault), JSON.stringify(seq(events)));
  }

  // ── #530 gate: primary REACHABLE (e.g. a 503 mapped to reachable) → proceed, ZERO extra probe ──
  {
    const events: LifecycleEvent[] = [];
    let secondaryProbed = 0;
    const gateSink: LifecycleSink = {
      async emit(e) { events.push(e); },
      async emitReachable(e) { events.push(e); return 'reachable' as PrimaryReachability; },
    };
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const o = createOrchestrator(inv(), {
      lifecycle: gateSink, join: mockJoin('admitted'), pipeline: noopPipeline(), acts: noopActs((f) => { fireLeave = f; }),
      aloneness: noopAloneness(), reachability: { async probeSecondary() { secondaryProbed++; return false; } },
    });
    const runP = o.run();
    setTimeout(() => fireLeave({ action: 'leave' }), 5);
    const res = await runP;
    check('gate reachable: proceeded to completed', res.status === 'completed', JSON.stringify(res));
    check('gate reachable: secondary channel NEVER probed (fast path, zero added latency)', secondaryProbed === 0, `probed=${secondaryProbed}`);
  }

  // ── #865: missing required port fails loud with the port name (not a TypeError on .onAlone) ──
  {
    let threw: unknown;
    try {
      createOrchestrator(inv(), {
        lifecycle: recordingSink(),
        join: mockJoin('admitted'),
        pipeline: noopPipeline(),
        acts: noopActs(),
        // deliberately omit aloneness
      } as Parameters<typeof createOrchestrator>[1]);
    } catch (e) {
      threw = e;
    }
    const msg = threw instanceof Error ? threw.message : String(threw);
    check('missing port: throws', threw instanceof Error, msg);
    check('missing port: names the port', /required port 'aloneness' is missing/.test(msg), msg);
    check('missing port: not a raw property TypeError', !/Cannot read properties of undefined/.test(msg), msg);
  }

  if (failed) { console.error(`\n❌ orchestrator (L2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ orchestrator (L2): the meeting machine drives a schema-valid lifecycle.v1 sequence and routes transcript.v1 — offline, every port faked.');
}

void main();
