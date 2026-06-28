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
 * Topology (meta.topology) selects the downstream strategy. `mixed` (zoom/teams)
 * is one diarized channel resolved downstream. For `per-participant` the channel
 * is NOT a stable speaker — Google Meet rotates a small pool of remote channels
 * across talkers — so identity rides on AudioChunk.speakerName (the glow name
 * bound at the source), never on the channel index.
 */

// ───────────────────────────── model ─────────────────────────────

/** One audio chunk crossing the seam, on ONE channel. */
export interface AudioChunk {
  speakerId: string;          // derived per-channel id, e.g. "spk-3" (not on the wire)
  speakerIndex: number;       // CHANNEL id — raw track index (999 = the mixed channel)
  samples: Float32Array;      // 16 kHz mono PCM
  ts: number;                 // CAPTURE epoch ms — set at the source, carried on the wire
  speakerName?: string;       // speaker name bound AT THE SOURCE when known (gmeet:
                              //   the glow name lit at `ts`). Empty ⇒ attribute
                              //   downstream. Carried in-process AND on the wire.
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
//   audio frame (binary), two BACKWARD-COMPATIBLE shapes:
//     no name : [Int32LE speakerIndex≥0][Float64LE ts][Float32LE pcm…]
//     w/ name : [Int32LE speakerIndex|HIGHBIT][Float64LE ts][Int32LE nameLen]
//               [UTF-8 name, zero-padded to 4B][Float32LE pcm…]
//   The high bit of speakerIndex (never set by real channel ids 0..1000) flags a
//   named frame: legacy frames decode unchanged and named frames are unambiguous,
//   so this is an ADDITIVE v1 change (no version bump, old fixtures still replay).
//   Name padding keeps the PCM 4-byte aligned. gmeet binds the glow name HERE at
//   the source; mixed (zoom/teams) omits it and resolves downstream.
//
//   event frame (text)  :  JSON.stringify(MeetingEvent)

const AUDIO_HEADER_BYTES = 12;     // legacy/no-name header: Int32 channel (4) + Float64 ts (8)
const NAMED_HEADER_BYTES = 16;     // + Int32 nameLen (4), when the name flag is set
const NAME_FLAG = 0x80000000 | 0;  // high bit of speakerIndex ⇒ trailing name present

/** Encode an audio chunk for the wire — capture-time rides in the header, and the
 *  source-bound speaker name (gmeet glow) rides after it when present. */
export function encodeAudioFrame(speakerIndex: number, ts: number, pcm: Float32Array, speakerName?: string): ArrayBuffer {
  const name = speakerName && speakerName.length ? speakerName : '';
  if (!name) {
    const buf = new ArrayBuffer(AUDIO_HEADER_BYTES + pcm.length * 4);
    const view = new DataView(buf);
    view.setInt32(0, speakerIndex, true);
    view.setFloat64(4, ts, true);
    new Float32Array(buf, AUDIO_HEADER_BYTES).set(pcm);
    return buf;
  }
  const nameBytes = new TextEncoder().encode(name);
  const padded = (nameBytes.length + 3) & ~3;   // keep the PCM 4-byte aligned
  const buf = new ArrayBuffer(NAMED_HEADER_BYTES + padded + pcm.length * 4);
  const view = new DataView(buf);
  view.setInt32(0, speakerIndex | NAME_FLAG, true);
  view.setFloat64(4, ts, true);
  view.setInt32(12, nameBytes.length, true);
  new Uint8Array(buf, NAMED_HEADER_BYTES, nameBytes.length).set(nameBytes);
  new Float32Array(buf, NAMED_HEADER_BYTES + padded).set(pcm);
  return buf;
}

/** Decode a wire audio frame. The receiver uses ts as-is — never Date.now().
 *  speakerName is present only for named (high-bit) frames. */
export function decodeAudioFrame(buf: ArrayBufferLike, byteOffset = 0, byteLength?: number):
    { speakerIndex: number; ts: number; samples: Float32Array; speakerName?: string } | null {
  const len = byteLength ?? (buf as ArrayBuffer).byteLength - byteOffset;
  if (len < AUDIO_HEADER_BYTES) return null;
  const view = new DataView(buf as ArrayBuffer, byteOffset, len);
  const raw = view.getInt32(0, true);
  const ts = view.getFloat64(4, true);
  if ((raw & NAME_FLAG) === 0) {                 // legacy/no-name frame — decode unchanged
    const n = (len - AUDIO_HEADER_BYTES) >> 2;
    const samples = new Float32Array(n);
    for (let i = 0; i < n; i++) samples[i] = view.getFloat32(AUDIO_HEADER_BYTES + i * 4, true);
    return { speakerIndex: raw, ts, samples };
  }
  if (len < NAMED_HEADER_BYTES) return null;     // named frame
  const speakerIndex = raw & 0x7fffffff;
  const nameLen = view.getInt32(12, true);
  if (nameLen < 0) return null;
  const padded = (nameLen + 3) & ~3;
  const pcmStart = NAMED_HEADER_BYTES + padded;
  if (len < pcmStart) return null;
  const speakerName = new TextDecoder().decode(new Uint8Array(buf as ArrayBuffer, byteOffset + NAMED_HEADER_BYTES, nameLen));
  const n = (len - pcmStart) >> 2;
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) samples[i] = view.getFloat32(pcmStart + i * 4, true);
  return { speakerIndex, ts, samples, speakerName };
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
