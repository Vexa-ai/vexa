/**
 * Audio pipeline: PCM frames in → VAD-gated diarization → per-speaker buffering
 * → transcription-service → segment events out.
 *
 * Per-speaker buffering: when the diarizer label changes, flush the current
 * speaker's buffer to the transcription service and start a new buffer for
 * the new speaker. This keeps each transcription request scoped to one
 * speaker's audio.
 *
 * Also flushes on a max-duration timer so silent-but-still-current speakers
 * eventually surface their text without waiting for a speaker change.
 */

import { TranscriptionClient } from './transcription-client';
import type { Diarizer } from './diarizer';
import { SAMPLE_RATE, type SegmentEvent } from './ws-protocol';

export interface PipelineConfig {
  diarizer: Diarizer;
  transcription: TranscriptionClient | null;
  /** Max samples to buffer per speaker before forced flush. ~5s at 16kHz. */
  maxBufferSamples?: number;
  /** Wall-clock ms between forced flushes if a single speaker keeps talking. */
  flushIntervalMs?: number;
  /** Called whenever a segment is ready to surface to the dashboard. */
  onSegment: (event: SegmentEvent) => void;
  /** Called on transcription error (non-fatal). */
  onError?: (err: Error) => void;
}

export class DiarizationPipeline {
  private diarizer: Diarizer;
  private transcription: TranscriptionClient | null;
  private maxBufferSamples: number;
  private flushIntervalMs: number;
  private onSegment: (event: SegmentEvent) => void;
  private onError: (err: Error) => void;

  private currentSpeaker = 'speaker_0';
  private currentBuffer: Float32Array[] = [];
  private currentBufferSamples = 0;
  private currentBufferStartMs = 0;
  private lastFlushMs = 0;
  private pendingFlush: Promise<void> | null = null;
  private flushTimer: NodeJS.Timeout | null = null;

  constructor(cfg: PipelineConfig) {
    this.diarizer = cfg.diarizer;
    this.transcription = cfg.transcription;
    this.maxBufferSamples = cfg.maxBufferSamples ?? SAMPLE_RATE * 5; // 5s
    this.flushIntervalMs = cfg.flushIntervalMs ?? 4000;              // 4s wall-clock
    this.onSegment = cfg.onSegment;
    this.onError = cfg.onError ?? ((err) => console.error('[pipeline]', err));
  }

  start(): void {
    if (this.flushTimer) return;
    this.flushTimer = setInterval(() => {
      const now = Date.now();
      if (this.currentBufferSamples > 0 && now - this.lastFlushMs >= this.flushIntervalMs) {
        void this.flush('timer');
      }
    }, 500);
  }

  stop(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
    void this.flush('stop');
  }

  reset(): void {
    this.diarizer.reset();
    this.currentSpeaker = 'speaker_0';
    this.currentBuffer = [];
    this.currentBufferSamples = 0;
    this.currentBufferStartMs = 0;
    this.lastFlushMs = 0;
  }

  /**
   * Feed an audio frame (Float32 PCM, 16kHz mono) into the pipeline.
   * The frame's wall-clock timestamp is when capture observed it.
   */
  async processFrame(frame: Float32Array, wallClockMs: number): Promise<string> {
    const label = await this.diarizer.process(frame, wallClockMs);

    if (label !== this.currentSpeaker && this.currentBufferSamples > 0) {
      await this.flush('speaker-change');
    }

    if (this.currentBufferSamples === 0) {
      this.currentBufferStartMs = wallClockMs;
      this.lastFlushMs = wallClockMs;
    }
    this.currentSpeaker = label;
    this.currentBuffer.push(frame);
    this.currentBufferSamples += frame.length;

    if (this.currentBufferSamples >= this.maxBufferSamples) {
      await this.flush('max-buffer');
    }
    return label;
  }

  private async flush(reason: string): Promise<void> {
    if (this.pendingFlush) {
      await this.pendingFlush;
    }
    if (this.currentBufferSamples === 0) return;

    const speaker = this.currentSpeaker;
    const samples = this.concatenate(this.currentBuffer, this.currentBufferSamples);
    const t0 = this.currentBufferStartMs;
    const t1 = Date.now();

    this.currentBuffer = [];
    this.currentBufferSamples = 0;
    this.lastFlushMs = t1;

    if (!this.transcription) {
      // No transcription backend wired — surface placeholder so the dashboard
      // still demonstrates pipeline shape end-to-end.
      this.onSegment({
        kind: 'segment',
        speaker,
        t0,
        t1,
        text: `[transcription service offline — ${(samples.length / SAMPLE_RATE).toFixed(2)}s of ${speaker}'s audio buffered, reason=${reason}]`,
      });
      return;
    }

    const flushPromise = (async () => {
      try {
        const result = await this.transcription!.transcribe(samples);
        const text = (result.text || '').trim();
        if (text.length > 0) {
          this.onSegment({ kind: 'segment', speaker, t0, t1, text });
        }
      } catch (err: any) {
        this.onError(err);
        this.onSegment({
          kind: 'segment',
          speaker,
          t0,
          t1,
          text: `[transcription error: ${err.message ?? String(err)}]`,
        });
      }
    })();

    this.pendingFlush = flushPromise.finally(() => {
      this.pendingFlush = null;
    });
    await this.pendingFlush;
  }

  private concatenate(chunks: Float32Array[], totalSamples: number): Float32Array {
    const out = new Float32Array(totalSamples);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }
}
