/**
 * The bot's PORTS (hexagonal) — the seams the orchestrator core depends on, so the whole
 * control flow is offline-provable (L2). The core NEVER imports Playwright / redis / http /
 * a browser; it speaks only these interfaces + the contract types. The real transports are
 * ADAPTERS injected at the composition root (src/index.ts):
 *
 *   JoinDriver      → @vexa/join + @vexa/remote-browser   (a real browser joins the meeting)
 *   Pipeline        → @vexa/{gmeet,mixed}-pipeline + @vexa/transcribe-whisper + @vexa/recording
 *   TranscriptSink  → redis stream / bus (transcript.v1 egress)
 *   LifecycleSink   → HTTP callback to meeting-api (lifecycle.v1)
 *   ActsSource      → redis pub/sub subscriber (acts.v1)
 *   RecordingSink   → @vexa/recording assembler → upload
 *
 * The L2 harness substitutes in-memory FAKES for every one of these (no client libs needed).
 */
import type { BotStatus, LifecycleEvent, Act, TranscriptSegment } from './contracts.js';

/** The outcome of the join+admission attempt (an Anti-Corruption verdict, P5 — the
 *  platform's many failure modes translated into the bot's vocabulary). */
export type JoinOutcome = 'admitted' | 'rejected' | 'timeout' | 'blocked' | 'error';

/** Drives the platform join. The real adapter wraps @vexa/join.joinMeeting + admission
 *  watchers + the removal monitor over a @vexa/remote-browser page. */
export interface JoinDriver {
  /** Join + await admission. `report` fires on each intermediate lifecycle state
   *  (awaiting_admission / needs_help / active). Resolves with the verdict. */
  join(report: (s: BotStatus) => void | Promise<void>): Promise<JoinOutcome>;
  /** Watch for being removed from the meeting while active; returns a stop fn. */
  onRemoval(cb: () => void): () => void;
  /** Leave the meeting (best-effort; never throws fatally). */
  leave(reason: string): Promise<void>;
}

/** The capture → lane → STT → transcript/recording engine. The orchestrator starts/stops
 *  it; the real impl wires @vexa/{gmeet,mixed}-pipeline + capture + STT; the L2 fake is a
 *  no-op that records start/stop. */
export interface Pipeline {
  start(): Promise<void>;
  stop(): Promise<void>;
}

/** transcript.v1 egress — the engine pushes speaker-attributed segments here; the real
 *  adapter publishes them to the redis stream / bus consumed by the collector. */
export interface TranscriptSink {
  publish(segment: TranscriptSegment): Promise<void>;
}

/** lifecycle.v1 egress — the orchestrator emits one status report per transition. The real
 *  adapter POSTs to meeting-api's callback; the L2 fake records the sequence to assert. */
export interface LifecycleSink {
  emit(event: LifecycleEvent): Promise<void>;
}

/** acts.v1 ingress — the control plane's command bus. The real adapter subscribes to the
 *  redis pub/sub channel; the L2 fake lets the test drive acts directly. Returns an
 *  unsubscribe fn. */
export interface ActsSource {
  subscribe(handler: (act: Act) => void | Promise<void>): () => void;
}

/** recording.v1 sink — accumulates capture chunks and assembles the master. The real
 *  adapter is @vexa/recording's assembler → upload; the orchestrator only signals close. */
export interface RecordingSink {
  close(key: string): void;
}
