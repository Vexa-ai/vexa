/**
 * Voice note — the bot as a buffer (#1783).
 *
 * A voice note has no meeting to join. The person's browser streams capture.v1 audio frames through
 * the gateway to meeting-api, which appends each one to the note's Redis stream
 * (invocation.v1 `voiceNote.audioStream`). This file is everything the bot does differently for one:
 *
 *   • the SOURCE reads that stream in order and feeds each frame to the lane, keeping the frame's own
 *     capture time (the receiver never restamps, capture.v1);
 *   • the JOIN DRIVER admits at once — there is nobody to admit us and nothing to leave;
 *   • the PIPELINE starts the lane, then the source, and on stop drains the source before the lane
 *     flushes, so the last words said before Stop are transcribed.
 *
 * Everything else — the lane, STT, the transcript and lifecycle sinks, the orchestrator — is the
 * meeting bot's own, unchanged. The stream is the buffer: audio sent while the bot is still booting
 * waits there.
 *
 * Pure: the Redis read is a port (`AudioStreamReader`); the live adapter is
 * `adapters/voice-note-redis.ts`.
 */
import { decodeAudioFrame } from '@vexa/capture-codec';
import type { Invocation } from './config.js';
import type { JoinDriver, Pipeline } from './ports.js';

/** One stream entry: a capture.v1 audio frame, the end of the note, or a keepalive (the person is
 *  still there — a paused note — so the idle timer starts again). */
export interface StreamEntry {
  id: string;
  frame?: Uint8Array;
  end?: boolean;
  alive?: boolean;
}

/** XREAD over the note's stream: the entries after `afterId`, waiting up to `blockMs` for one. */
export interface AudioStreamReader {
  read(afterId: string, blockMs: number): Promise<StreamEntry[]>;
}

export interface VoiceNoteSourceOptions {
  reader: AudioStreamReader;
  /** One decoded frame for the lane: mono 16 kHz PCM and its capture epoch ms. */
  feed: (pcm: Float32Array, tsMs: number) => void;
  /** The browser said Stop: the end marker was read. Fires once. */
  onEnd: () => void;
  /** Neither audio nor an end for `idleMs`: the browser went away. Fires once. */
  onIdle: () => void;
  idleMs: number;
  /** XREAD BLOCK per read (default 1000 ms). */
  blockMs?: number;
  log?: (m: string) => void;
  now?: () => number;
}

export interface VoiceNoteSource {
  start(): void;
  /** Read on until the end marker, an empty read or `drainMs`, then stop. */
  stop(drainMs?: number): Promise<void>;
  stats(): { frames: number; dropped: number; audioSeconds: number; ended: boolean };
}

const SAMPLE_RATE = 16_000;
const DEFAULT_DRAIN_MS = 5_000;

export function createVoiceNoteSource(opts: VoiceNoteSourceOptions): VoiceNoteSource {
  const now = opts.now ?? Date.now;
  const blockMs = opts.blockMs ?? 1000;
  const log = opts.log ?? (() => { /* quiet */ });
  let lastId = '0-0';
  let frames = 0, dropped = 0, samples = 0;
  let ended = false, idled = false;
  let stopping = false, drainDeadline = 0;
  let lastActivity = now();
  let loop: Promise<void> | null = null;

  const consume = (e: StreamEntry): void => {
    lastId = e.id;
    if (e.end) {
      ended = true;
      opts.onEnd();
      return;
    }
    if (e.alive) {
      lastActivity = now();
      return;
    }
    const bytes = e.frame;
    const decoded = bytes && bytes.byteLength >= 12
      ? decodeAudioFrame(bytes.buffer, bytes.byteOffset, bytes.byteLength)
      : null;
    if (!decoded) {
      dropped++;
      log(`[bot] voice note: dropped entry ${e.id} (not a capture.v1 audio frame)`);
      return;
    }
    frames++;
    samples += decoded.samples.length;
    lastActivity = now();
    opts.feed(decoded.samples, decoded.ts);
  };

  const run = async (): Promise<void> => {
    while (!ended) {
      const draining = stopping;
      if (draining && now() >= drainDeadline) return;
      let batch: StreamEntry[];
      try {
        batch = await opts.reader.read(lastId, draining ? Math.min(blockMs, 200) : blockMs);
      } catch (e) {
        log(`[bot] voice note: stream read failed: ${String(e)}`);
        if (draining) return;
        await new Promise((r) => setTimeout(r, Math.min(blockMs, 1000)));
        continue;
      }
      for (const e of batch) {
        if (ended) break;
        consume(e);
      }
      if (draining && batch.length === 0) return;
      if (!ended && !idled && !stopping && now() - lastActivity > opts.idleMs) {
        idled = true;
        opts.onIdle();
      }
    }
  };

  return {
    start() {
      if (!loop) loop = run().catch((e) => log(`[bot] voice note: reader stopped: ${String(e)}`));
    },
    async stop(drainMs = DEFAULT_DRAIN_MS) {
      stopping = true;
      drainDeadline = now() + drainMs;
      if (!loop) return;
      // A bounded wait: a reader stuck past the drain window must not hold teardown.
      await Promise.race([loop, new Promise<void>((r) => setTimeout(r, drainMs + blockMs + 500))]);
    },
    stats() {
      return { frames, dropped, audioSeconds: samples / SAMPLE_RATE, ended };
    },
  };
}

/** Nobody admits us and there is nothing to leave: a voice note is active the moment it starts. */
export function createVoiceNoteJoinDriver(): JoinDriver {
  return {
    async join() { return 'admitted'; },
    onRemoval() { return () => { /* nothing removes a voice note */ }; },
    async leave() { /* nothing to leave */ },
    async withdraw() { /* nothing to withdraw */ },
  };
}

/** The orchestrator's Pipeline over a voice note: the lane first, then the audio; on stop the audio
 *  drains before the lane flushes its last turn. */
export function createVoiceNotePipeline(deps: {
  source: Pick<VoiceNoteSource, 'start' | 'stop'>;
  engine: Pipeline;
}): Pipeline {
  return {
    async start() {
      await deps.engine.start();
      deps.source.start();
    },
    async stop() {
      await deps.source.stop().catch(() => { /* best-effort */ });
      await deps.engine.stop().catch(() => { /* best-effort */ });
    },
  };
}

/** The active-phase backstop: the note's cap plus a minute. meeting-api already refuses audio past
 *  the cap; this only guarantees the bot cannot outlive it. */
export function voiceNoteMaxActiveMs(inv: Invocation): number {
  return ((inv.voiceNote?.maxDurationSec ?? 0) + 60) * 1000;
}

/** How long the source waits with no audio before it ends the note. */
export function voiceNoteIdleMs(inv: Invocation): number {
  return (inv.voiceNote?.idleTimeoutSec ?? 120) * 1000;
}
