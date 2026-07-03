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

/** One captured-signal.v1 frame as it crosses the capture-bridge tap — the VERBATIM raw
 *  signal a live bug rides on, BEFORE it enters the pipeline. Mirrors the `@vexa/capture-codec`
 *  binary frame shape (the JSONL tape's per-frame record), so a stored stream replays through
 *  the EXACT pipeline offline (O-TEL-2). `pcm` is base64 of the Float32 PCM bytes (little-endian),
 *  exactly what the codec puts on the wire. `lane` distinguishes the gmeet per-channel path from
 *  the single mixed stream. The sink derives `seq`/`rms` if the bridge doesn't supply them. */
export interface CapturedFrame {
  seq?: number;                     // monotone per-session frame ordinal (sink assigns if absent)
  ts: number;                       // CAPTURE epoch ms — carried from the frame, NEVER restamped
  speakerIndex: number;             // CHANNEL id (999 = mixed, 1000 = the local "You" mic)
  speakerName?: string;             // glow name bound at capture (gmeet), when known
  hint?: string;                    // mixed-lane "who is lit" hint name (active-speaker), when present
  pcm: string;                      // base64 of the Float32 PCM bytes (LE) — codec wire payload
  pcm_len: number;                  // PCM sample count (Float32 elements)
  rms?: number;                     // root-mean-square level (sink computes if absent)
  lane: 'gmeet' | 'mixed';          // which pipeline lane this frame feeds
}

/** TelemetrySink port — the OPTIONAL dual-sink the capture bridge tees raw frames into, BEFORE
 *  the pipeline. The real adapter persists captured-signal.v1 (file/store); when unset the tap is
 *  a single undefined-check (zero overhead — the proven O6 capture path is never altered). The
 *  pipeline path is wholly independent of whether this is present. */
export interface TelemetrySink {
  /** Tee one raw capture frame. MUST NOT throw into the capture path (the bridge calls it
   *  fire-and-forget); the adapter swallows + logs its own faults. */
  captureFrame(frame: CapturedFrame): void;
}
