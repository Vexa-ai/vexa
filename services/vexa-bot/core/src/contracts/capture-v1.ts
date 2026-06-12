/**
 * MIRROR of /contracts/capture/v1/schema.ts — that file is CANONICAL.
 * Kept in-tree until capture-kit/pipeline extract as bricks (MVP2), then
 * both import the contract package directly. Sync on contract change.
 */

/** One per-speaker audio chunk crossing the seam. */
export interface AudioChunk {
  speakerId: string;          // stable per-track id, e.g. "speaker-3"
  speakerIndex: number;       // raw track index from capture
  samples: Float32Array;      // 16 kHz mono PCM
  ts: number;                 // epoch ms when captured
  speakerName?: string;       // resolved name if known (empty until mapped)
}

/** A meeting event crossing the seam (no audio payload). */
export interface MeetingEvent {
  kind: 'speaker-joined' | 'speaker-left' | 'active-speaker' | 'caption'
      | 'segment' | 'lifecycle' | 'track-lock';
  ts: number;
  speaker?: string;
  text?: string;              // caption / segment text (content tier only)
  detail?: Record<string, unknown>;
}

/** Recording-time meta — the selection index (platform, speakers, topology). */
export interface CaptureMeta {
  platform?: string;
  nativeMeetingId?: string | number;
  language?: string | null;
  topology?: 'per-participant' | 'mixed';
  sampleRate?: number;
}

/**
 * The contract-in port. The pipeline implements it (consume), the recorder
 * implements it (tee). `tee()` composes both — the seam emits once.
 */
export interface CaptureV1Sink {
  audioChunk(chunk: AudioChunk): void;
  event(ev: MeetingEvent): void;
  finalize(): void | Promise<void>;
}

/** Compose sinks: emit each message to all. The recorder is just another sink. */
export function tee(...sinks: (CaptureV1Sink | null | undefined)[]): CaptureV1Sink {
  const live = sinks.filter(Boolean) as CaptureV1Sink[];
  return {
    audioChunk: (c) => { for (const s of live) try { s.audioChunk(c); } catch {} },
    event: (e) => { for (const s of live) try { s.event(e); } catch {} },
    finalize: async () => { for (const s of live) try { await s.finalize(); } catch {} },
  };
}
