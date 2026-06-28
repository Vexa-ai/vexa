/**
 * StreamCaptureWriter — the ONE faithful `capture.v1` wire-log writer.
 *
 * A `stream.capture` is the byte-faithful serialization of everything the page
 * produced: every audio frame + event, in arrival order, with capture-time ts
 * intact. Replaying it reproduces the live timeline exactly, so a single file
 * drives the entire downstream chain (pipeline → attribution → delivery) with
 * no meeting. It is the input `gate:replay`, `npm run e2e`, `mixed-replay`, and
 * `attribute-fixture` all read.
 *
 * On-disk record framing (matches the original in pipeline/scripts/live-ingest.ts):
 *
 *     [u8 type 0=audio 1=event][u32LE len][payload]
 *
 * `meta.json` is snake_case for the replay tools: { capture, platform,
 * native_meeting_id, language, topology, sample_rate }.
 *
 * Two ways in, one format out:
 *   • `rawAudio`/`rawEvent` — the WS seam (ingest-server, capture-recorder):
 *     bytes are already the wire form, written verbatim.
 *   • `audio`/`event`       — the in-process seam (the bot): decoded samples
 *     re-serialized via the contract encoder, so the bot's capture is faithful too.
 *
 * This is the shared writer for all three tee points (live-ingest, the bot
 * in-process tee, the ingest-server tee) — one format, no drift.
 */

import * as fs from 'fs';
import * as path from 'path';
import { encodeAudioFrame, encodeEvent, MeetingEvent, CaptureMeta } from './contracts/capture-v1';

const RECORD_AUDIO = 0;
const RECORD_EVENT = 1;
const DEFAULT_SAMPLE_RATE = 16000;
const MIXED_CHANNEL = 999;

export class StreamCaptureWriter {
  readonly outDir: string;
  private stream: fs.WriteStream;
  private meta: CaptureMeta;
  private finalized = false;
  private channels = new Set<number>();
  bytes = 0;
  audioFrames = 0;
  events = 0;

  constructor(outDir: string, meta: CaptureMeta = {}) {
    this.outDir = outDir;
    this.meta = meta;
    fs.mkdirSync(outDir, { recursive: true });
    this.stream = fs.createWriteStream(path.join(outDir, 'stream.capture'));
  }

  /** Faithful framed record: [u8 type][u32LE len][payload]. */
  private writeRecord(type: number, payload: Buffer): void {
    if (this.finalized) return;
    const hdr = Buffer.allocUnsafe(5);
    hdr.writeUInt8(type, 0);
    hdr.writeUInt32LE(payload.length, 1);
    this.stream.write(hdr);
    this.stream.write(payload);
    this.bytes += 5 + payload.length;
  }

  /** WS seam — an audio frame already in wire form (verbatim, no re-encode).
   *  Peek the leading Int32LE speakerIndex so topology inference works on this
   *  path too (else a mixed 999 capture would mislabel as per-participant). */
  rawAudio(payload: Buffer): void {
    if (payload.length >= 4) this.channels.add(payload.readInt32LE(0));
    this.writeRecord(RECORD_AUDIO, payload);
    this.audioFrames++;
  }
  /** WS seam — an event already in wire form (verbatim JSON bytes). */
  rawEvent(payload: Buffer): void { this.writeRecord(RECORD_EVENT, payload); this.events++; }

  /** In-process seam — decoded samples re-serialized to the faithful wire form. */
  audio(speakerIndex: number, ts: number, samples: Float32Array): void {
    this.channels.add(speakerIndex);
    this.writeRecord(RECORD_AUDIO, Buffer.from(encodeAudioFrame(speakerIndex, ts, samples)));
    this.audioFrames++;
  }
  /** In-process seam — a structured event re-serialized to the faithful wire form. */
  event(ev: MeetingEvent): void {
    this.writeRecord(RECORD_EVENT, Buffer.from(encodeEvent(ev), 'utf8'));
    this.events++;
  }

  /** Close the log and write the snake_case meta.json the replay tools read. */
  async finalize(): Promise<string> {
    if (this.finalized) return this.outDir;
    this.finalized = true;
    await new Promise<void>((resolve) => this.stream.end(() => resolve()));
    const meta = {
      capture: 'capture.v1/stream',
      platform: this.meta.platform ?? 'unknown',
      native_meeting_id: this.meta.nativeMeetingId != null ? String(this.meta.nativeMeetingId) : '?',
      language: this.meta.language ?? null,
      topology: this.meta.topology ?? (this.channels.has(MIXED_CHANNEL) ? 'mixed' : 'per-participant'),
      sample_rate: this.meta.sampleRate ?? DEFAULT_SAMPLE_RATE,
    };
    fs.writeFileSync(path.join(this.outDir, 'meta.json'), JSON.stringify(meta, null, 2));
    return this.outDir;
  }
}
