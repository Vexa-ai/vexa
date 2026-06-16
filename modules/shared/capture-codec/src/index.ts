/**
 * @vexa/capture-codec — the ONE binary-frame + JSON-event serialization shared by
 * both capture lanes (gmeet-capture.v1, mixed-capture.v1). The sender stamps
 * capture-time into every frame; the receiver NEVER restamps. Pure, zero-dep,
 * drift-gated — bot-captured and extension-captured fixtures are byte-identical.
 *
 *   audio frame (binary), two BACKWARD-COMPATIBLE shapes:
 *     no name : [Int32LE track≥0][Float64LE ts][Float32LE pcm…]            (mixed)
 *     w/ name : [Int32LE track|HIGHBIT][Float64LE ts][Int32LE nameLen]
 *               [UTF-8 name, zero-padded to 4B][Float32LE pcm…]           (gmeet)
 *   The high bit of `track` (never set by real track ids 0..1000) flags a named
 *   frame: legacy frames decode unchanged. gmeet binds the glow name HERE at the
 *   source; mixed omits it and names downstream from hints.
 *
 *   event frame (text) : JSON.stringify(MeetingEvent)
 */

/** A meeting event crossing the seam (no audio payload) — chat + lifecycle +
 *  the mixed lane's active-speaker hints all ride this one JSON envelope. */
export interface MeetingEvent {
  kind: 'speaker-joined' | 'speaker-left' | 'active-speaker' | 'caption'
      | 'segment' | 'lifecycle' | 'track-lock' | 'chat';
  ts: number;                 // CAPTURE epoch ms
  speaker?: string;           // active-speaker name / chat sender display name
  text?: string;              // caption / segment / chat text
  detail?: Record<string, unknown>; // active-speaker → {hint:'dom-active'|'dom-outline'|'caption', isEnd, index}
}

const AUDIO_HEADER_BYTES = 12;     // no-name header: Int32 track (4) + Float64 ts (8)
const NAMED_HEADER_BYTES = 16;     // + Int32 nameLen (4), when the name flag is set
const NAME_FLAG = 0x80000000 | 0;  // high bit of track ⇒ trailing name present

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
