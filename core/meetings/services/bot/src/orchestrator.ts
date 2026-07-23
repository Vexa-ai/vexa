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
  isTerminal,
} from './contracts.js';
import type {
  JoinDriver,
  JoinOutcome,
  JoinResult,
  Pipeline,
  LifecycleSink,
  ActsSource,
  AlonenessSource,
  RecordingSink,
  ControlPlaneProbe,
} from './ports.js';

export interface OrchestratorDeps {
  lifecycle: LifecycleSink;
  join: JoinDriver;
  pipeline: Pipeline;
  acts: ActsSource;
  aloneness: AlonenessSource;
  /** Optional — recording is gated by invocation.recordingEnabled; the core only closes it. */
  recording?: RecordingSink;
  /** Optional (#530) — the SECONDARY control-plane channel probe (redis), consulted only when
   *  the first `joining` emit is unreachable on the primary channel. ABSENT ⇒ the secondary is
   *  treated as reachable (conservative: never abort on an un-probeable channel), so the gate
   *  never turns a missing probe into a join refusal. */
  reachability?: ControlPlaneProbe;
  /** Optional — what DEGRADED the meeting without ending it (today: STT refusing every chunk).
   *  Consulted once, when a TERMINAL event is built, and merged onto it. A meeting whose STT
   *  backend was dead used to reach `completed` indistinguishable from a silent room: the faults
   *  were typed and attributed all the way to the composition root and then died in a
   *  console.error, so the emptiest possible transcript arrived with no reason attached (the #807
   *  shape). Rides lifecycle.v1 exactly as `infra_fault` does — the contract is
   *  additionalProperties:true, so this is additive.
   *  Returns `undefined` when nothing degraded. MUST NOT throw. */
  degraded?: () => Record<string, unknown> | undefined;
}

/** The dedicated non-zero exit code for a pre-join control-plane-unreachable abort (#530). On
 *  k8s each bot is a bare Pod (`--restart=Never`, `k8s_backend.py`) — the terminal exit code IS
 *  the attributable signal an operator reads in one `kubectl describe`, distinct from a real
 *  join failure (exit 1). */
export const CONTROL_PLANE_UNREACHABLE_EXIT = 3;

/** The infra-fault tag / reason-text discriminator for the control-plane-unreachable terminal
 *  (#530). NOT a lifecycle.v1 CompletionReason — the sealed enum stays untouched; attribution
 *  rides `failure_stage:"requested"` + `infra_fault` + this text + exit 3 (the C2 fork-4
 *  no-seal-bump path: existing reason + liberal reason-text, LifecycleEvent additionalProperties:true). */
export const CONTROL_PLANE_UNREACHABLE = 'control_plane_unreachable';

export interface MeetingResult {
  exitCode: number;
  status: BotStatus;
  completionReason?: CompletionReason;
}

/** Normalize a driver's join return — a bare `JoinOutcome` or a `JoinResult` — into `{ outcome,
 *  reason? }` so the orchestrator has ONE shape to reason about (and the reason text, when the
 *  driver supplied one, survives to the terminal lifecycle row). */
function normalizeJoin(r: JoinOutcome | JoinResult): JoinResult {
  return typeof r === 'string' ? { outcome: r } : r;
}

/** Map a non-admitted join verdict to the terminal completion_reason. */
const OUTCOME_FAIL: Record<Exclude<JoinOutcome, 'admitted'>, CompletionReason> = {
  rejected: 'awaiting_admission_rejected',
  timeout: 'awaiting_admission_timeout',
  blocked: 'join_failure',
  auth_missing: 'auth_session_missing',
  error: 'join_failure',
};

export interface RunOptions {
  /** A hard cap on the active phase (ms). Resolves the run with max_bot_time_exceeded.
   *  Defaults to off (0) — the live composition root derives it from automaticLeave. */
  maxActiveMs?: number;
  /** Maximum time the active-phase pipeline may hold terminal lifecycle truth during teardown.
   *  The live pipeline has tighter per-stage bounds; this outer guard also covers injected and
   *  future Pipeline implementations that never resolve stop(). */
  pipelineStopTimeoutMs?: number;
}

export const DEFAULT_PIPELINE_STOP_TIMEOUT_MS = 9_000;
export const PLATFORM_LEAVE_TIMEOUT_MS = 8_000;

/** Required ports — missing any of these used to surface as a raw TypeError deep in `run()`. */
const REQUIRED_PORTS = ['lifecycle', 'join', 'pipeline', 'acts', 'aloneness'] as const;

function assertRequiredPorts(deps: OrchestratorDeps): void {
  for (const name of REQUIRED_PORTS) {
    if (deps[name] == null) {
      throw new Error(`orchestrator: required port '${name}' is missing`);
    }
  }
}

/** Best-effort does not mean unbounded. A teardown promise may keep running after the deadline,
 *  but it has rejection handlers attached and can no longer withhold the typed terminal event. */
async function stopPipelineWithin(pipeline: Pipeline, timeoutMs: number): Promise<void> {
  const boundedMs = Number.isFinite(timeoutMs) && timeoutMs > 0
    ? timeoutMs
    : DEFAULT_PIPELINE_STOP_TIMEOUT_MS;
  console.error(`[bot] orchestrator: pipeline-stop started (deadline=${boundedMs}ms)`);

  let timer: ReturnType<typeof setTimeout> | null = null;
  const settled = Promise.resolve()
    .then(() => pipeline.stop())
    .then(
      () => ({ kind: 'finished' as const }),
      (error: unknown) => ({ kind: 'fault' as const, error }),
    );
  const deadline = new Promise<{ kind: 'timeout' }>((resolve) => {
    timer = setTimeout(() => resolve({ kind: 'timeout' }), boundedMs);
  });
  const outcome = await Promise.race([settled, deadline]);
  if (timer) clearTimeout(timer);

  if (outcome.kind === 'finished') {
    console.error('[bot] orchestrator: pipeline-stop finished');
  } else if (outcome.kind === 'fault') {
    console.error(`[bot] orchestrator: pipeline-stop fault (continuing terminal path): ${String(outcome.error)}`);
  } else {
    console.error(`[bot] orchestrator: pipeline-stop timed out after ${boundedMs}ms (continuing terminal path)`);
  }
}

/**
 * Build the meeting orchestrator. Returns `run()` (drives the machine to a terminal state)
 * and `handle(act)` (the acts.v1 entrypoint the ActsSource adapter — or a test — feeds).
 */
export function createOrchestrator(inv: Invocation, deps: OrchestratorDeps) {
  assertRequiredPorts(deps);
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
    // A terminal event is the LAST thing anyone hears from this bot — if the meeting was degraded,
    // it says so here or the reason dies with the container. A reporter fault must never change the
    // exit path (P18: report, but never at the cost of the report itself).
    let degraded: Record<string, unknown> | undefined;
    if (isTerminal(status) && deps.degraded) {
      try { degraded = deps.degraded(); } catch { degraded = undefined; }
    }
    await deps.lifecycle.emit({ ...base, status, ...extra, ...(degraded ?? {}) });
  };

  // The load-bearing FIRST emit (#530). `cur` is already `joining` (the initial state), so this
  // needs no transition guard. When the sink can report reachability we consult it; otherwise the
  // event is emitted normally and treated as reachable (self-host / test sinks have no gate).
  const emitJoining = async (extra: Partial<LifecycleEvent>): Promise<boolean> => {
    const ev: LifecycleEvent = { ...base, status: 'joining', ...extra };
    if (deps.lifecycle.emitReachable) return (await deps.lifecycle.emitReachable(ev)) === 'reachable';
    await deps.lifecycle.emit(ev);
    return true;
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

  // acts.v1 dispatch. A `leave` command routes through `stop()` — the SAME phase-aware decision the
  // SIGTERM seam uses — so a Stop is honored NO MATTER the phase it arrives in: ACTIVE ⇒ graceful
  // leave; PRE-ACTIVE (still knocking in the lobby) ⇒ abort the join → WITHDRAW the ask-to-join
  // request (#889). Routing `leave` straight to `signalEnd` used to only end the ACTIVE phase, so a
  // lobby `leave` did nothing. reconfigure + voice acts are handled by the live pipeline adapter
  // (no-op for the machine; voice agent is DEFERRED this increment).
  async function handle(act: Act): Promise<void> {
    if (act.action === 'leave') stop('stopped');
  }

  async function run(opts: RunOptions = {}): Promise<MeetingResult> {
    // ── reachability gate (#530, P18) — the FIRST lifecycle emit is LOAD-BEARING ──
    // The `joining` event must be sent regardless; we consult its delivery verdict. Reachable ⇒
    // ZERO added latency (the secondary channel is never probed). Primary unreachable ⇒ probe the
    // secondary (redis); BOTH down ⇒ refuse to join BEFORE any meeting navigation and terminate
    // with the dedicated infra signal — rather than proceeding toward a human meeting the bot can
    // never report about (the 2026-07-09 fresh-node signature: opaque join_failure / stuck-requested).
    const joiningExtra: Partial<LifecycleEvent> = base.container_id ? { container_id: base.container_id } : {};
    const primaryReachable = await emitJoining(joiningExtra);
    if (!primaryReachable) {
      // Either-channel rule: EITHER channel up ⇒ the bot can still report ⇒ proceed. Absent probe
      // ⇒ treated as reachable (never abort on an un-probeable channel).
      const secondaryReachable = deps.reachability ? await deps.reachability.probeSecondary() : true;
      if (!secondaryReachable) {
        // BOTH control-plane channels unreachable → refuse to join. Attempt the terminal on
        // whichever channel answers (likely none — that is fine; on k8s the pod exit code carries
        // it). Use the raw sink (never-throw) and mark `cur` terminal ourselves; the emit here is
        // best-effort and must not mask the exit.
        const unreachable = ['meeting_api_callback', 'redis'];
        cur = 'failed';
        await deps.lifecycle.emit({
          ...base,
          status: 'failed',
          failure_stage: 'requested',
          completion_reason: 'join_failure',
          infra_fault: CONTROL_PLANE_UNREACHABLE,
          unreachable_channels: unreachable,
          reason: `${CONTROL_PLANE_UNREACHABLE}: control plane unreachable at boot (${unreachable.join(', ')}); refused to join`,
          exit_code: CONTROL_PLANE_UNREACHABLE_EXIT,
        }).catch(() => { /* the channel that would carry this is the one that is down */ });
        return { exitCode: CONTROL_PLANE_UNREACHABLE_EXIT, status: 'failed', completionReason: 'join_failure' };
      }
    }

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
    // Subscribe to the command bus BEFORE the join race (#889). A user Stop is delivered as a `leave`
    // command on the bot's channel; when it arrives while the bot is still knocking in the lobby, the
    // orchestrator must ALREADY be listening (redis pub/sub has no backlog) — handle() then routes it
    // through stop() → the pre-active abort race → withdraw. Subscribing only AFTER admission (the old
    // placement) dropped every lobby-phase leave, so a Stop in the waiting room left the bot asking to
    // join. `unsubscribe()` is called on every exit path (pre-active + active) below.
    const unsubscribe = deps.acts.subscribe(handle);
    let outcome: JoinOutcome;
    let joinReason: string | undefined;
    try {
      // Race the (possibly long, lobby-blocked) join against a pre-active abort. A stop/SIGTERM in the
      // waiting room resolves `aborted` → we stop waiting, WITHDRAW the join request, and terminate —
      // rather than SIGKILLing a bot that is still asking to join (the waiting-room orphan). The race
      // yields a tagged result so the abort branch narrows cleanly (no symbol-vs-JoinOutcome union).
      const raced = await Promise.race<{ aborted: false; result: JoinResult } | { aborted: true }>([
        deps.join.join(report).then((o) => ({ aborted: false as const, result: normalizeJoin(o) })),
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
        unsubscribe();
        return { exitCode: 0, status: 'failed', completionReason: 'stopped' };
      }
      outcome = raced.result.outcome;
      joinReason = raced.result.reason;
      await reportChain;   // flush in-flight reports before deciding admission
    } catch (e) {
      unsubscribe();
      await emit('failed', { failure_stage: 'joining', completion_reason: 'join_failure', reason: String(e), exit_code: 1 });
      return { exitCode: 1, status: 'failed', completionReason: 'join_failure' };
    }
    if (outcome !== 'admitted') {
      const reason = OUTCOME_FAIL[outcome];
      unsubscribe();
      // ALWAYS stamp a human `reason` text (#926). A non-admitted verdict carries a completion_reason
      // enum, but the terminal row also needs the human cause or meeting-api synthesizes the
      // uninformative "Bot exited with code 1; reason: None". Prefer the driver's own message
      // (the AdmissionError text — e.g. the Zoom "auth_required" / "host not started" cause); fall
      // back to a derived line so NO reasonless terminal can ever leave this branch.
      const reasonText = joinReason ?? `join ended without admission: ${outcome} → ${reason}`;
      await emit('failed', { failure_stage: 'awaiting_admission', completion_reason: reason, reason: reasonText, exit_code: 1 });
      return { exitCode: 1, status: 'failed', completionReason: reason };
    }
    if (cur !== 'active') await emit('active');   // the join driver may already have reported active

    // ── active: start the engine, wire removal + aloneness + the optional time cap (acts already subscribed) ──
    try {
      await deps.pipeline.start();
    } catch (e) {
      // Already admitted (the browser is seated in the meeting) → LEAVE before exiting, or we
      // strand a ghost participant. Best-effort; never masks the failure.
      deps.recording?.close(recordingKey);
      await deps.join.leave('pipeline_start_failed').catch(() => { /* best-effort */ });
      unsubscribe();
      await emit('failed', { failure_stage: 'active', completion_reason: 'join_failure', reason: String(e), exit_code: 1 });
      return { exitCode: 1, status: 'failed', completionReason: 'join_failure' };
    }
    const stopRemoval = deps.join.onRemoval(() => signalEnd?.('evicted'));
    const stopAloneness = deps.aloneness.onAlone(() => signalEnd?.('left_alone'));
    const cap = opts.maxActiveMs && opts.maxActiveMs > 0
      ? setTimeout(() => signalEnd?.('max_bot_time_exceeded'), opts.maxActiveMs)
      : null;

    const reason = await ended;

    // ── graceful teardown (best-effort; never masks the completion reason) ──
    if (cap) clearTimeout(cap);
    unsubscribe();
    stopAloneness();
    stopRemoval();
    await stopPipelineWithin(deps.pipeline, opts.pipelineStopTimeoutMs ?? DEFAULT_PIPELINE_STOP_TIMEOUT_MS);
    try {
      deps.recording?.close(recordingKey);
    } catch (e) {
      console.error(`[bot] orchestrator: recording-close fault (continuing terminal path): ${String(e)}`);
    }
    // Bound the leave: a hung platform leave (e.g. a slow Zoom web-client teardown) must not stall
    // the disposable worker past its SIGKILL grace — that would cut off the recording-master
    // assembly + the `completed` callback flush. Best-effort, raced against an 8s cap.
    await Promise.race([
      deps.join.leave(reason).catch(() => { /* best-effort */ }),
      new Promise<void>((resolve) => setTimeout(resolve, PLATFORM_LEAVE_TIMEOUT_MS)),
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
