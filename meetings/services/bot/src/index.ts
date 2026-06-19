/**
 * @vexa/bot — the COMPOSITION ROOT (P7 worker entrypoint).
 *
 * The ONLY place wiring happens (Seemann): validate the invocation.v1 config from the env,
 * build the real adapters for every port, hand them to the pure orchestrator, run to a
 * terminal lifecycle.v1 state, and exit. The container boots here, works, emits, and dies.
 *
 * ┌─ THIS INCREMENT delivers the gate-green CORE: config + ports + the orchestrator state
 * │  machine + L2 tests. The live TRANSPORTS below are STUBBED placeholders (clearly marked
 * │  TODO) — the next increment swaps each stub for its real adapter:
 * │    • JoinDriver     → @vexa/join.joinMeeting + @vexa/remote-browser (auth/S3) page
 * │    • Pipeline       → capture WS → @vexa/{gmeet,mixed}-pipeline → @vexa/transcribe-whisper
 * │    • TranscriptSink → redis stream publish (transcript.v1)
 * │    • LifecycleSink  → HTTP POST to inv.meetingApiCallbackUrl (lifecycle.v1, retry/backoff)
 * │    • ActsSource     → redis pub/sub on actsChannel(inv.meeting_id) (acts.v1)
 * │    • RecordingSink  → @vexa/recording assembler → inv.recordingUploadUrl
 * └─ The seam exists now (P16 "defer the implementation, not the seam"); only the body is TODO.
 */
import { loadInvocation, InvocationError, type Invocation } from './config.js';
import type { LifecycleEvent } from './contracts.js';
import { createOrchestrator } from './orchestrator.js';
import type {
  JoinDriver,
  Pipeline,
  LifecycleSink,
  TranscriptSink,
  ActsSource,
  RecordingSink,
} from './ports.js';

// ── STUB adapters (next increment: replace each body with the live transport) ──────────
//
// They satisfy the port contracts so the composition root is wired end-to-end NOW (the
// machine runs against them), but perform no real I/O. Marked clearly so they cannot be
// mistaken for production transports.

/** TODO(live): → HTTP POST to inv.meetingApiCallbackUrl with retry/backoff (lifecycle-http). */
function stubLifecycleSink(): LifecycleSink {
  return { async emit(e: LifecycleEvent) { console.log(`[bot] lifecycle.v1 ${e.status}${e.completion_reason ? ` (${e.completion_reason})` : ''}${e.failure_stage ? ` @${e.failure_stage}` : ''}`); } };
}

/** TODO(live): → @vexa/join.joinMeeting over a @vexa/remote-browser page + admission/removal watchers. */
function stubJoinDriver(_inv: Invocation): JoinDriver {
  return {
    async join(report) { await report('awaiting_admission'); await report('active'); return 'admitted'; },
    onRemoval() { return () => { /* no live removal monitor in the stub */ }; },
    async leave() { /* no live browser to leave */ },
  };
}

/** TODO(live): → capture WS → @vexa/{gmeet,mixed}-pipeline → @vexa/transcribe-whisper, pushing to TranscriptSink. */
function stubPipeline(_sink: TranscriptSink): Pipeline {
  return { async start() { /* no live capture */ }, async stop() { /* */ } };
}

/** TODO(live): → redis stream publish of transcript.v1 segments. */
function stubTranscriptSink(): TranscriptSink {
  return { async publish() { /* no live bus */ } };
}

/** TODO(live): → redis pub/sub subscriber on actsChannel(inv.meeting_id) (acts.v1). */
function stubActsSource(): ActsSource {
  return { subscribe() { return () => { /* no live bus */ }; } };
}

/** TODO(live): → @vexa/recording assembler → upload to inv.recordingUploadUrl. */
function stubRecordingSink(): RecordingSink {
  return { close() { /* no live assembler */ } };
}

export async function main(env: NodeJS.ProcessEnv = process.env): Promise<number> {
  // ── validate config (P14: fail fast) ──
  let inv: Invocation;
  try {
    inv = loadInvocation(env);
  } catch (e) {
    if (e instanceof InvocationError) {
      // No valid connection_id to attribute the failure to → emit a best-effort terminal
      // event and exit non-zero. (The live LifecycleSink would POST validation_error.)
      console.error(`[bot] FATAL ${e.message}`);
      stubLifecycleSink().emit({ connection_id: env.VEXA_CONNECTION_ID ?? '', status: 'failed', failure_stage: 'requested', completion_reason: 'validation_error', reason: e.message, exit_code: 1 }).catch(() => {});
      return 1;
    }
    throw e;
  }

  // ── wire the adapters (STUBS this increment) → the pure orchestrator ──
  const transcript = stubTranscriptSink();
  const orchestrator = createOrchestrator(inv, {
    lifecycle: stubLifecycleSink(),
    join: stubJoinDriver(inv),
    pipeline: stubPipeline(transcript),
    acts: stubActsSource(),
    recording: inv.recordingEnabled ? stubRecordingSink() : undefined,
  });

  const result = await orchestrator.run();
  return result.exitCode;
}

// Worker entrypoint: boot, work, emit, die (P7).
if (import.meta.url === `file://${process.argv[1]}`) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e); process.exit(1); });
}
