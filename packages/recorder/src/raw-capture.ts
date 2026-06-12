/**
 * RawCaptureService — dumps per-speaker audio WAVs + DOM events to disk
 * for offline replay via production-replay.test.ts.
 *
 * Enabled by RAW_CAPTURE=true env var.
 *
 * Output format:
 *   /tmp/raw-capture-{meetingId}/
 *     audio/
 *       01-speakername.wav     # 16kHz mono Int16 PCM
 *       01-speakername.txt     # ground truth (empty placeholder)
 *     events.txt               # timestamped DOM events
 */

import * as fs from 'fs';
import * as path from 'path';
import { CaptureV1Sink, AudioChunk, MeetingEvent } from './contracts/capture-v1';
import { uploadCaptureToS3 } from './s3-upload';

const SAMPLE_RATE = 16000;
const CHANNELS = 1;
const BITS_PER_SAMPLE = 16;

interface TrackState {
  speakerName: string;
  chunks: Float32Array[];
  totalSamples: number;
}

export interface CaptureMeta {
  platform?: string;
  nativeMeetingId?: string | number;
  language?: string | null;
  task?: string | null;
  botVersion?: string;
}

/**
 * RecorderSink — the recorder (MANIFEST P5) as a CaptureV1Sink implementation.
 * Tee'd onto the capture→pipeline seam: serializes capture.v1 to disk + S3.
 * (Class name kept RawCaptureService for call-site stability; it IS the recorder.)
 */
export class RawCaptureService implements CaptureV1Sink {
  private outputDir: string;
  private audioDir: string;
  private eventsPath: string;
  private tracks: Map<number, TrackState> = new Map();
  private fileCounter = 0;
  private eventsLines: string[] = [];
  private finalized = false;
  private meta: CaptureMeta;
  private startedAt = new Date().toISOString();
  private speakersSeen: Map<string, number> = new Map(); // name -> total samples
  private connectionEvents = 0;

  constructor(meetingId: string | number, meta: CaptureMeta = {}) {
    this.meta = meta;
    this.outputDir = `/tmp/raw-capture-${meetingId}`;
    this.audioDir = path.join(this.outputDir, 'audio');
    this.eventsPath = path.join(this.outputDir, 'events.txt');

    fs.mkdirSync(this.audioDir, { recursive: true });
    // Create empty events file
    fs.writeFileSync(this.eventsPath, '');
  }

  get outputPath(): string {
    return this.outputDir;
  }

  /**
   * Feed audio samples for a track. Called from handlePerSpeakerAudioData.
   */
  feedAudio(trackIndex: number, audioData: Float32Array, speakerName: string): void {
    if (this.finalized) return;
    if (speakerName) {
      this.speakersSeen.set(speakerName, (this.speakersSeen.get(speakerName) || 0) + audioData.length);
    }

    let track = this.tracks.get(trackIndex);

    // If speaker changed on this track, flush the old data first
    if (track && speakerName && track.speakerName !== speakerName && track.speakerName !== '') {
      this.flushTrack(trackIndex);
      track = undefined;
    }

    if (!track) {
      track = {
        speakerName: speakerName || `speaker-${trackIndex}`,
        chunks: [],
        totalSamples: 0,
      };
      this.tracks.set(trackIndex, track);
    }

    // Update name if we got a better one
    if (speakerName && track.speakerName === `speaker-${trackIndex}`) {
      track.speakerName = speakerName;
    }

    track.chunks.push(new Float32Array(audioData));
    track.totalSamples += audioData.length;
  }

  /**
   * Log a speaker change event (from DOM polling or speaker identity).
   */
  logSpeakerEvent(fromSpeaker: string | null, toSpeaker: string): void {
    if (this.finalized) return;
    const ts = new Date().toISOString();
    const from = fromSpeaker || '(none)';
    const line = `${ts} [SPEAKER] Speaker change: ${from} → ${toSpeaker} (Guest)`;
    this.eventsLines.push(line);
    this.appendEventsFile(line);
  }

  /**
   * Log a track lock event.
   */
  logTrackLock(trackIndex: number, speakerName: string): void {
    if (this.finalized) return;
    const ts = new Date().toISOString();
    const line = `${ts} [LOCK] Track ${trackIndex} → "${speakerName}" LOCKED PERMANENTLY`;
    this.eventsLines.push(line);
    this.appendEventsFile(line);
  }

  /**
   * Log a confirmed segment event.
   */
  logSegmentConfirmed(speakerName: string, text: string): void {
    if (this.finalized) return;
    const ts = new Date().toISOString();
    const line = `${ts} [SEGMENT] "${speakerName}": ${text}`;
    this.eventsLines.push(line);
    this.appendEventsFile(line);
  }

  /**
   * Log a connection-lifecycle event (ws connect/disconnect/reconnect, stt stalls).
   * Part of the envelope spec: prime suspects in real-world silences.
   */
  logLifecycle(kind: string, detail?: string): void {
    if (this.finalized) return;
    this.connectionEvents++;
    const line = `${new Date().toISOString()} [LIFECYCLE] ${kind}${detail ? ` ${detail}` : ''}`;
    this.eventsLines.push(line);
    this.appendEventsFile(line);
  }

  // ─── CaptureV1Sink (the contract port) ───────────────────────────────
  /** contract: an audio chunk crossed the seam. */
  audioChunk(c: AudioChunk): void {
    this.feedAudio(c.speakerIndex, c.samples, c.speakerName || '');
  }

  /** contract: a meeting event crossed the seam. */
  event(e: MeetingEvent): void {
    if (this.finalized) return;
    switch (e.kind) {
      case 'speaker-joined':
      case 'active-speaker':
        if (e.speaker) this.logSpeakerEvent(null, e.speaker); break;
      case 'segment':
        if (e.speaker) this.logSegmentConfirmed(e.speaker, e.text || ''); break;
      case 'lifecycle':
        this.logLifecycle(String(e.detail?.what ?? 'event'), e.text); break;
      case 'track-lock':
        if (typeof e.detail?.trackIndex === 'number' && e.speaker)
          this.logTrackLock(e.detail.trackIndex as number, e.speaker); break;
    }
  }

  /**
   * Flush all tracks, write meta.json, ship to the training corpus (S3).
   * The recorder owns its sink — the bot service does NOT (MANIFEST P5:
   * recording is the recorder's job, not smeared into the assembly).
   */
  async finalizeAndUpload(meetingId: string | number): Promise<string> {
    const dir = this.flushToDisk();
    uploadCaptureToS3(dir, { platform: this.meta.platform, meetingId });
    return dir;
  }

  /** contract: finalize the recording (void). */
  finalize(): void { this.flushToDisk(); }

  /**
   * Flush all tracks + write meta.json, return the output dir.
   */
  private flushToDisk(): string {
    if (this.finalized) return this.outputDir;
    this.finalized = true;

    // meta.json — the selection index: query captures by platform / speakers / date
    // without any database (S3 prefix partitioning + this file is the whole index).
    try {
      const speakers = Array.from(this.speakersSeen.entries()).map(([name, samples]) => ({
        name, duration_s: Math.round((samples / SAMPLE_RATE) * 10) / 10,
      }));
      const metaOut = {
        capture: "capture.v1/raw",
        provenance: process.env.RAW_CAPTURE_PROVENANCE || "prod-full",
        platform: this.meta.platform || null,
        native_meeting_id: this.meta.nativeMeetingId ?? null,
        language: this.meta.language ?? null,
        task: this.meta.task ?? null,
        bot_version: this.meta.botVersion || process.env.BOT_IMAGE_TAG || null,
        topology: "per-participant",
        sample_rate: SAMPLE_RATE,
        started_at: this.startedAt,
        ended_at: new Date().toISOString(),
        num_speakers: speakers.length,
        speakers,
        connection_events: this.connectionEvents,
        event_lines: this.eventsLines.length,
      };
      fs.writeFileSync(path.join(this.outputDir, 'meta.json'), JSON.stringify(metaOut, null, 2));
    } catch { /* meta is best-effort; never block shutdown */ }

    // Flush all remaining tracks
    for (const trackIndex of this.tracks.keys()) {
      this.flushTrack(trackIndex);
    }

    return this.outputDir;
  }

  private flushTrack(trackIndex: number): void {
    const track = this.tracks.get(trackIndex);
    if (!track || track.totalSamples === 0) {
      this.tracks.delete(trackIndex);
      return;
    }

    this.fileCounter++;
    const idx = String(this.fileCounter).padStart(2, '0');
    const safeName = this.sanitizeName(track.speakerName);
    const wavPath = path.join(this.audioDir, `${idx}-${safeName}.wav`);
    const txtPath = path.join(this.audioDir, `${idx}-${safeName}.txt`);

    // Merge chunks into one Float32Array
    const merged = new Float32Array(track.totalSamples);
    let offset = 0;
    for (const chunk of track.chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }

    // Write WAV: 16kHz mono 16-bit PCM
    const pcmBuffer = this.float32ToInt16PCM(merged);
    const header = this.createWavHeader(pcmBuffer.length);
    fs.writeFileSync(wavPath, Buffer.concat([header, pcmBuffer]));

    // Write empty ground truth placeholder
    fs.writeFileSync(txtPath, '');

    // Clear track
    this.tracks.delete(trackIndex);
  }

  private appendEventsFile(line: string): void {
    fs.appendFileSync(this.eventsPath, line + '\n');
  }

  private sanitizeName(name: string): string {
    return name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'unknown';
  }

  private createWavHeader(dataSize: number): Buffer {
    const header = Buffer.alloc(44);
    const byteRate = SAMPLE_RATE * CHANNELS * (BITS_PER_SAMPLE / 8);
    const blockAlign = CHANNELS * (BITS_PER_SAMPLE / 8);

    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataSize, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);           // PCM
    header.writeUInt16LE(CHANNELS, 22);
    header.writeUInt32LE(SAMPLE_RATE, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE(blockAlign, 32);
    header.writeUInt16LE(BITS_PER_SAMPLE, 34);
    header.write('data', 36);
    header.writeUInt32LE(dataSize, 40);

    return header;
  }

  private float32ToInt16PCM(float32Data: Float32Array): Buffer {
    const buffer = Buffer.alloc(float32Data.length * 2);
    for (let i = 0; i < float32Data.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Data[i]));
      const val = s < 0 ? s * 0x8000 : s * 0x7FFF;
      buffer.writeInt16LE(Math.round(val), i * 2);
    }
    return buffer;
  }
}
