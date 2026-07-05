/**
 * The meeting bot's lifecycle STATE MACHINE — the orchestrator core.
 *
 * Depends ONLY on ports (JoinDriver, Pipeline, ActsSource, RecordingSink) + the contract
 * sinks (LifecycleSink, TranscriptSink) — no Playwright, no redis, no browser. So the
 * whole control flow is unit-testable offline (L2): inject fakes and assert the emitted
 * lifecycle.v1 sequence conforms to the schema + state machine.
 *
 *   joining ─► [join driver: awaiting_admission ─(needs_help)─► active] ─► pipeline.start
 *           ─► subscribe acts ─► await end ─► leave ─► completed
 *
 * End signals while active: an acts.v1 `leave` (→ stopped), host removal (→ evicted), or a
 * timeout (→ left_alone / max_bot_time_exceeded). Any failure short-circuits to `failed`
 * with the right failure_stage + completion_reason. Every transition is guarded by
 * `canTransition` — an illegal emit is a contract violation that throws.
 */
import type { Invocation } from './config.js';
import {
  type BotStatus,
  type CompletionReason,
  type LifecycleEvent,
  type Act,
  canTransition,
} from './contracts.js';
import type {
  JoinDriver,
  JoinOutcome,
  Pipeline,
  LifecycleSink,
  ActsSource,
  RecordingSink,
} from './ports.js';

export interface OrchestratorDeps {
  lifecycle: LifecycleSink;
  join: JoinDriver;
  pipeline: Pipeline;
  acts: ActsSource;
  /** Optional — recording is gated by invocation.recordingEnabled; the core only closes it. */
  recording?: RecordingSink;
}

export interface MeetingResult {
  exitCode: number;
  status: BotStatus;
  completionReason?: CompletionReason;
}

/** Map a non-admitted join verdict to the terminal completion_reason. */
const OUTCOME_FAIL: Record<Exclude<JoinOutcome, 'admitted'>, CompletionReason> = {
  rejected: 'awaiting_admission_rejected',
  timeout: 'awaiting_admission_timeout',
  blocked: 'join_failure',
  error: 'join_failure',
};

export interface RunOptions {
  /** A hard cap on the active phase (ms). Resolves the run with max_bot_time_exceeded.
   *  Defaults to off (0) — the live composition root derives it from automaticLeave. */
  maxActiveMs?: number;
}

/**
 * Build the meeting orchestrator. Returns `run()` (drives the machine to a terminal state)
 * and `handle(act)` (the acts.v1 entrypoint the ActsSource adapter — or a test — feeds).
 */
export function createOrchestrator(inv: Invocation, deps: OrchestratorDeps) {
  const base: { connection_id: string; container_id?: string } = {
    connection_id: inv.connectionId ?? '',
    ...(inv.container_name ? { container_id: inv.container_name } : {}),
  };
  const recordingKey = `${inv.platform}/${inv.nativeMeetingId ?? inv.connectionId ?? 'session'}`;

  let cur: BotStatus = 'joining';

  const emit = async (status: BotStatus, extra: Partial<LifecycleEvent> = {}): Promise<void> => {
    if (status !== cur && !canTransition(cur, status)) {
      throw new Error(`lifecycle.v1: illegal transition ${cur} → ${status}`);
    }
    cur = status;
    await deps.lifecycle.emit({ ...base, status, ...extra });
  };

  // The end signal: a `leave` act, host removal, or a timeout all resolve the active phase.
  let signalEnd: ((r: CompletionReason) => void) | null = null;
  const ended = new Promise<CompletionReason>((res) => { signalEnd = res; });

  // The PRE-ACTIVE abort signal (Bug 2 — the waiting-room-orphan fix): a stop/SIGTERM that arrives
  // while the bot is still `joining`/`awaiting_admission` (blocked inside `deps.join.join()`, waiting
  // in the lobby) must not merely arm the force-exit watchdog and SIGKILL — that leaves the join
  // request live, so Google Meet still shows the bot "asking to join". `stop()` resolves this before
  // `active`; `run()` races the join phase against it and, on abort, WITHDRAWS (cancel the ask-to-join
  // / close the pre-join tab) before emitting terminal.
  let signalAbort: ((r: CompletionReason) => void) | null = null;
  const aborted = new Promise<CompletionReason>((res) => { signalAbort = res; });

  // acts.v1 dispatch. `leave` ends the run; reconfigure + voice acts are handled by the
  // live pipeline adapter (no-op for the machine; voice agent is DEFERRED this increment).
  async function handle(act: Act): Promise<void> {
    if (act.action === 'leave') signalEnd?.('stopped');
  }

  async function run(opts: RunOptions = {}): Promise<MeetingResult> {
    await emit('joining', base.container_id ? { container_id: base.container_id } : {});

    // ── join → admission ──
    // Serialize the driver's intermediate reports so lifecycle.v1 events POST in order even
    // when the driver fire-and-forgets, and SURFACE a contract-illegal transition (log) rather
    // than silently dropping it.
    let reportChain: Promise<void> = Promise.resolve();
    const report = (s: BotStatus): Promise<void> => {
      reportChain = reportChain.then(() => emit(s)).catch((e) => {
        console.error(`[bot] lifecycle report '${s}' rejected: ${String(e)}`);
      });
      return reportChain;
    };
    let outcome: JoinOutcome;
    try {
      // Race the (possibly long, lobby-blocked) join against a pre-active abort. A stop/SIGTERM in the
      // waiting room resolves `aborted` → we stop waiting, WITHDRAW the join request, and terminate —
      // rather than SIGKILLing a bot that is still asking to join (the waiting-room orphan). The race
      // yields a tagged result so the abort branch narrows cleanly (no symbol-vs-JoinOutcome union).
      const raced = await Promise.race<{ aborted: false; outcome: JoinOutcome } | { aborted: true }>([
        deps.join.join(report).then((o) => ({ aborted: false as const, outcome: o })),
        aborted.then(() => ({ aborted: true as const })),
      ]);
      if (raced.aborted) {
        // WITHDRAW before exit (Bug 2): cancel the ask-to-join / close the pre-join tab so the join
        // request is dropped — bounded + best-effort (the platform withdraw itself caps its clicks;
        // the guaranteed fallback closes the page). The bot never reached active, so the terminal is
        // `failed` (stage = the pre-active stage it was stopped in), attributed to the user stop.
        await Promise.race([
          deps.join.withdraw('stopped').catch(() => { /* best-effort */ }),
          new Promise<void>((resolve) => setTimeout(resolve, 8000)),
        ]);
        const stage = cur === 'awaiting_admission' ? 'awaiting_admission' : 'joining';
        await emit('failed', {
          failure_stage: stage, completion_reason: 'stopped',
          reason: 'stopped while awaiting admission (withdrew the join request)', exit_code: 0,
        });
        return { exitCode: 0, status: 'failed', completionReason: 'stopped' };
      }
      outcome = raced.outcome;
      await reportChain;   // flush in-flight reports before deciding admission
    } catch (e) {
      await emit('failed', { failure_stage: 'joining', completion_reason: 'join_failure', reason: String(e), exit_code: 1 });
      return { exitCode: 1, status: 'failed', completionReason: 'join_failure' };
    }
    if (outcome !== 'admitted') {
      const reason = OUTCOME_FAIL[outcome];
      await emit('failed', { failure_stage: 'awaiting_admission', completion_reason: reason, exit_code: 1 });
      return { exitCode: 1, status: 'failed', completionReason: reason };
    }
    if (cur !== 'active') await emit('active');   // the join driver may already have reported active

    // ── active: start the engine, wire removal + acts + the optional time cap ──
    try {
      await deps.pipeline.start();
    } catch (e) {
      // Already admitted (the browser is seated in the meeting) → LEAVE before exiting, or we
      // strand a ghost participant. Best-effort; never masks the failure.
      deps.recording?.close(recordingKey);
      await deps.join.leave('pipeline_start_failed').catch(() => { /* best-effort */ });
      await emit('failed', { failure_stage: 'active', completion_reason: 'join_failure', reason: String(e), exit_code: 1 });
      return { exitCode: 1, status: 'failed', completionReason: 'join_failure' };
    }
    const stopRemoval = deps.join.onRemoval(() => signalEnd?.('evicted'));
    const unsubscribe = deps.acts.subscribe(handle);
    const cap = opts.maxActiveMs && opts.maxActiveMs > 0
      ? setTimeout(() => signalEnd?.('max_bot_time_exceeded'), opts.maxActiveMs)
      : null;

    const reason = await ended;

    // ── graceful teardown (best-effort; never masks the completion reason) ──
    if (cap) clearTimeout(cap);
    unsubscribe();
    stopRemoval();
    await deps.pipeline.stop().catch(() => { /* best-effort */ });
    deps.recording?.close(recordingKey);
    // Bound the leave: a hung platform leave (e.g. a slow Zoom web-client teardown) must not stall
    // the disposable worker past its SIGKILL grace — that would cut off the recording-master
    // assembly + the `completed` callback flush. Best-effort, raced against an 8s cap.
    await Promise.race([
      deps.join.leave(reason).catch(() => { /* best-effort */ }),
      new Promise<void>((resolve) => setTimeout(resolve, 8000)),
    ]);

    console.error(`[bot] orchestrator: emitting completed (reason=${reason}, from=${cur})`);
    try {
      await emit('completed', { completion_reason: reason, exit_code: 0 });
      console.error('[bot] orchestrator: completed emitted + flushed');
    } catch (e) {
      console.error(`[bot] orchestrator: completed emit THREW: ${String(e)}`);
      throw e;
    }
    return { exitCode: 0, status: 'completed', completionReason: reason };
  }

  /** Trigger a graceful end — wired to SIGTERM/SIGINT at the composition root so the worker is
   *  disposable (P7). If the bot is already `active`, this ends the active phase (leave → flush →
   *  completed). If it is still PRE-ACTIVE (`joining`/`awaiting_admission`, blocked in the lobby),
   *  it instead aborts the join so `run()` WITHDRAWS the waiting-room request rather than being
   *  SIGKILLed into a lobby orphan (Bug 2). After the run ended both are no-ops (resolvers fired). */
  function stop(reason: CompletionReason = 'stopped'): void {
    if (cur === 'active') {
      signalEnd?.(reason);
    } else {
      // Pre-active: unblock the lobby wait so run() can withdraw + terminate. (Idempotent: a promise
      // resolver only fires once, and once active this branch is never taken.)
      signalAbort?.(reason);
    }
  }

  return { run, handle, stop };
}
