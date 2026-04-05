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

const SAMPLE_RATE = 16000;
const CHANNELS = 1;
const BITS_PER_SAMPLE = 16;

interface TrackState {
  speakerName: string;
  chunks: Float32Array[];
  totalSamples: number;
}

export class RawCaptureService {
  private outputDir: string;
  private audioDir: string;
  private eventsPath: string;
  private tracks: Map<number, TrackState> = new Map();
  private fileCounter = 0;
  private eventsLines: string[] = [];
  private finalized = false;

  constructor(meetingId: string | number) {
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
   * Flush all tracks and write final events. Called at bot shutdown.
   */
  finalize(): string {
    if (this.finalized) return this.outputDir;
    this.finalized = true;

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
