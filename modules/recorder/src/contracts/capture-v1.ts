/**
 * capture.v1 — the capture→pipeline contract (CANONICAL, single source of truth).
 *
 * ONE data model, THREE serializations that must agree (MANIFEST P2/P4):
 *
 *   1. in-process (bot)   — CaptureV1Sink method calls; AudioChunk.ts set at the
 *                            source. The recorder tees here.
 *   2. WS wire (extension)— encodeAudioFrame / encodeEvent below. The SENDER
 *                            stamps capture-time into every frame; the receiver
 *                            NEVER restamps. Bot-captured and extension-captured
 *                            fixtures are therefore the same capture.v1.
 *   3. fixture (replay)   — audio/<NN>-<channel>.wav + events.jsonl (one
 *                            MeetingEvent per line) + meta.json (CaptureMeta +
 *                            channels[] start/duration). The validator
 *                            (contracts/capture/v1/validate.mjs) is the gate.
 *
 * Topology (meta.topology) selects the downstream strategy: `per-participant`
 * (gmeet, channel = a speaker) or `mixed` (zoom/teams, one diarized channel).
 */

// ───────────────────────────── model ─────────────────────────────

/** One audio chunk crossing the seam, on ONE channel. */
export interface AudioChunk {
  speakerId: string;          // derived per-channel id, e.g. "spk-3" (not on the wire)
  speakerIndex: number;       // CHANNEL id — raw track index (999 = the mixed channel)
  samples: Float32Array;      // 16 kHz mono PCM
  ts: number;                 // CAPTURE epoch ms — set at the source, carried on the wire
  speakerName?: string;       // resolved name if known (empty until attributed)
}

/** A meeting event crossing the seam (no audio payload). */
export interface MeetingEvent {
  kind: 'speaker-joined' | 'speaker-left' | 'active-speaker' | 'caption'
      | 'segment' | 'lifecycle' | 'track-lock' | 'chat';
  ts: number;                 // CAPTURE epoch ms
  speaker?: string;           // chat → sender display name
  text?: string;              // caption / segment / chat text (content tier only)
  detail?: Record<string, unknown>; // active-speaker → {hint:'dom-active'|'dom-outline'|'caption', isEnd, index}; chat → {source:'zoom-chat', scope?}
}

/** Recording-time meta — the selection index + the replay topology. */
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

// ───────────────────────── WS wire codec ─────────────────────────
// The ONE codec both the extension (send) and the ingest/recorder (receive)
// use — so the wire carries the model losslessly, capture-time included.
//
//   audio frame (binary):  [Int32LE speakerIndex][Float64LE ts][Float32LE pcm…]
//   event frame (text)  :  JSON.stringify(MeetingEvent)

const AUDIO_HEADER_BYTES = 12; // Int32 channel (4) + Float64 ts (8)

/** Encode an audio chunk for the wire — capture-time rides in the header. */
export function encodeAudioFrame(speakerIndex: number, ts: number, pcm: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(AUDIO_HEADER_BYTES + pcm.length * 4);
  const view = new DataView(buf);
  view.setInt32(0, speakerIndex, true);
  view.setFloat64(4, ts, true);
  new Float32Array(buf, AUDIO_HEADER_BYTES).set(pcm);
  return buf;
}

/** Decode a wire audio frame. The receiver uses ts as-is — never Date.now(). */
export function decodeAudioFrame(buf: ArrayBufferLike, byteOffset = 0, byteLength?: number):
    { speakerIndex: number; ts: number; samples: Float32Array } | null {
  const len = byteLength ?? (buf as ArrayBuffer).byteLength - byteOffset;
  if (len < AUDIO_HEADER_BYTES) return null;
  const view = new DataView(buf as ArrayBuffer, byteOffset, len);
  const speakerIndex = view.getInt32(0, true);
  const ts = view.getFloat64(4, true);
  const n = (len - AUDIO_HEADER_BYTES) >> 2;
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) samples[i] = view.getFloat32(AUDIO_HEADER_BYTES + i * 4, true);
  return { speakerIndex, ts, samples };
}

/** Encode / decode a meeting event for the wire (text frame). */
export function encodeEvent(ev: MeetingEvent): string { return JSON.stringify(ev); }
export function decodeEvent(json: string): MeetingEvent | null {
  try {
    const e = JSON.parse(json);
    if (typeof e?.kind !== 'string' || typeof e?.ts !== 'number') return null;
    return e as MeetingEvent;
  } catch { return null; }
}
