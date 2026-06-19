/**
 * @vexa/bot — the COMPOSITION ROOT (P7 worker entrypoint).
 *
 * The ONLY place wiring happens (Seemann): validate the invocation.v1 config from the env,
 * build the real adapters for every port, hand them to the pure orchestrator, run to a
 * terminal lifecycle.v1 state, and exit. The container boots here, works, emits, and dies.
 *
 * ┌─ INCREMENT 2a wires the LIVE redis/HTTP transports for the data/control plane:
 * │    • LifecycleSink  → HTTP POST to inv.meetingApiCallbackUrl (lifecycle.v1, retry/backoff)  ✅ LIVE
 * │    • TranscriptSink → redis stream + pub/sub (transcript.v1)                                ✅ LIVE
 * │    • ActsSource     → redis pub/sub on actsChannel(meetingId) (acts.v1)                     ✅ LIVE
 * │  Still STUBBED for 2b (browser join + capture + recording upload):
 * │    • JoinDriver     → @vexa/join.joinMeeting + @vexa/remote-browser (auth/S3) page          ⏳ TODO(2b)
 * │    • Pipeline       → capture WS → @vexa/{gmeet,mixed}-pipeline → @vexa/transcribe-whisper  ⏳ TODO(2b)
 * │    • RecordingSink  → @vexa/recording assembler → inv.recordingUploadUrl                    ⏳ TODO(2b)
 * └─ The seam exists now (P16 "defer the implementation, not the seam"); the live transports
 *    connect LAZILY (redis is not dialed at construction) so an unreachable redis doesn't crash
 *    the composition root — the orchestrator still drives to a clean terminal `failed`.
 */
import { loadInvocation, InvocationError, type Invocation } from './config.js';
import type { LifecycleEvent } from './contracts.js';
import { createOrchestrator } from './orchestrator.js';
import { createHttpLifecycleSink } from './adapters/lifecycle-http.js';
import { createRedisTranscriptSink, redisClientFrom } from './adapters/transcript-redis.js';
import { createRedisActsSource, redisActsClientFrom } from './adapters/acts-redis.js';
import type {
  JoinDriver,
  Pipeline,
  LifecycleSink,
  TranscriptSink,
  ActsSource,
  RecordingSink,
} from './ports.js';

// ── STUB adapters still pending 2b (browser join + capture + recording upload) ─────────
//
// They satisfy the port contracts so the composition root is wired end-to-end NOW (the
// machine runs against them), but perform no real I/O. Marked clearly so they cannot be
// mistaken for production transports. The lifecycle/transcript/acts transports below are
// LIVE (2a); these three are swapped in 2b.

/** A console-only lifecycle sink — used for self-host (no `meetingApiCallbackUrl`) and as the
 *  pre-config fallback. The live HTTP sink (createHttpLifecycleSink) replaces it when a
 *  callback URL is configured. */
function consoleLifecycleSink(): LifecycleSink {
  return { async emit(e: LifecycleEvent) { console.log(`[bot] lifecycle.v1 ${e.status}${e.completion_reason ? ` (${e.completion_reason})` : ''}${e.failure_stage ? ` @${e.failure_stage}` : ''}`); } };
}

/** TODO(2b: browser join + capture + recording upload): → @vexa/join.joinMeeting over a
 *  @vexa/remote-browser page + admission/removal watchers. */
function stubJoinDriver(_inv: Invocation): JoinDriver {
  return {
    async join(report) { await report('awaiting_admission'); await report('active'); return 'admitted'; },
    onRemoval() { return () => { /* no live removal monitor in the stub */ }; },
    async leave() { /* no live browser to leave */ },
  };
}

/** TODO(2b: browser join + capture + recording upload): → capture WS → @vexa/{gmeet,mixed}-pipeline
 *  → @vexa/transcribe-whisper, pushing to the (now live) TranscriptSink. */
function stubPipeline(_sink: TranscriptSink): Pipeline {
  return { async start() { /* no live capture */ }, async stop() { /* */ } };
}

/** TODO(2b: browser join + capture + recording upload): → @vexa/recording assembler → upload
 *  to inv.recordingUploadUrl. */
function stubRecordingSink(): RecordingSink {
  return { close() { /* no live assembler */ } };
}

/** The meeting id that keys the redis transcript/acts channels (0.11 control-plane convention:
 *  the numeric `meeting_id`). Falls back to the platform native id / connection id when the
 *  numeric id is absent (e.g. self-host paths), so the channels are always well-formed. */
function meetingChannelId(inv: Invocation): string | number {
  return inv.meeting_id ?? inv.nativeMeetingId ?? inv.connectionId ?? 'session';
}

/** Derive the hard active-phase cap (ms) from invocation.v1 `automaticLeave`.
 *
 *  `orchestrator.run({ maxActiveMs })` is a HARD ceiling on the active phase that resolves to
 *  `completed(max_bot_time_exceeded)` — a backstop so a bot can never live forever (the granular
 *  empty-room / waiting-room timeouts that map to left_alone/startup_alone are driven by the
 *  Pipeline/JoinDriver in 2b). We size the ceiling off the configured timeouts, taking the
 *  LARGEST so it never fires before a normal everyone-left / no-one-joined exit would:
 *    max(everyoneLeftTimeout=120s, noOneJoinedTimeout=600s, waitingRoomTimeout=300s) + a margin.
 *  Defaults (per the invocation.v1 schema) → 600s + 60s margin = 660s when automaticLeave is unset. */
function deriveMaxActiveMs(inv: Invocation): number {
  const al = inv.automaticLeave ?? {};
  const everyoneLeft = al.everyoneLeftTimeout ?? 120_000;
  const noOneJoined = al.noOneJoinedTimeout ?? 600_000;
  const waitingRoom = al.waitingRoomTimeout ?? 300_000;
  const MARGIN_MS = 60_000; // give the granular timeouts room to fire first
  return Math.max(everyoneLeft, noOneJoined, waitingRoom) + MARGIN_MS;
}

export async function main(env: NodeJS.ProcessEnv = process.env): Promise<number> {
  // ── validate config (P14: fail fast) ──
  let inv: Invocation;
  try {
    inv = loadInvocation(env);
  } catch (e) {
    if (e instanceof InvocationError) {
      // No valid connection_id to attribute the failure to → emit a best-effort terminal
      // event and exit non-zero. We have no validated callbackUrl yet, so this goes to the
      // console sink. (The live HTTP sink would POST validation_error once a URL is known.)
      console.error(`[bot] FATAL ${e.message}`);
      consoleLifecycleSink().emit({ connection_id: env.VEXA_CONNECTION_ID ?? '', status: 'failed', failure_stage: 'requested', completion_reason: 'validation_error', reason: e.message, exit_code: 1 }).catch(() => {});
      return 1;
    }
    throw e;
  }

  // ── build the LIVE transports (2a) + the still-stubbed bricks (2b) → the pure orchestrator ──
  const meetingId = meetingChannelId(inv);

  // lifecycle.v1: HTTP POST to meeting-api when a callback URL is configured; console-only for
  // self-host (no callback). The HTTP sink retries/backs off and never throws out of emit.
  const lifecycle: LifecycleSink = inv.meetingApiCallbackUrl
    ? createHttpLifecycleSink({ callbackUrl: inv.meetingApiCallbackUrl, internalSecret: inv.internalSecret })
    : consoleLifecycleSink();

  // transcript.v1 + acts.v1: redis. Connect LAZILY — constructing the clients does NOT dial
  // redis, so an unreachable broker doesn't crash the composition root; the first publish/
  // subscribe surfaces the error and the orchestrator drives to a clean terminal `failed`.
  const transcriptClient = redisClientFrom(inv.redisUrl);
  const actsClient = redisActsClientFrom(inv.redisUrl);
  const transcript = createRedisTranscriptSink({ client: transcriptClient, meetingId });
  const acts = createRedisActsSource({ client: actsClient, meetingId });

  const orchestrator = createOrchestrator(inv, {
    lifecycle,
    join: stubJoinDriver(inv),            // TODO(2b: browser join + capture + recording upload)
    pipeline: stubPipeline(transcript),   // TODO(2b: browser join + capture + recording upload)
    acts,
    recording: inv.recordingEnabled ? stubRecordingSink() : undefined,  // TODO(2b)
  });

  // Disposability (P7): a termination signal ends the active phase gracefully (leave →
  // completed) so the container never hangs after `active`. Wire before run(); unwire after.
  const onSignal = () => orchestrator.stop('stopped');
  process.once('SIGTERM', onSignal);
  process.once('SIGINT', onSignal);
  try {
    const result = await orchestrator.run({ maxActiveMs: deriveMaxActiveMs(inv) });
    return result.exitCode;
  } finally {
    process.off('SIGTERM', onSignal);
    process.off('SIGINT', onSignal);
    // Quit the redis connections on teardown (best-effort — a quit failure must not change the
    // exit code; they may never have connected if redis was unreachable).
    await transcriptClient.quit().catch(() => { /* best-effort */ });
    await actsClient.quit().catch(() => { /* best-effort */ });
  }
}

// Worker entrypoint: boot, work, emit, die (P7).
if (import.meta.url === `file://${process.argv[1]}`) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e); process.exit(1); });
}
