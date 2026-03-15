import { log } from '../utils';

/**
 * Per-speaker audio buffer with confirmation-based segment emission.
 *
 * Buffer accumulates audio and resubmits the full buffer every interval.
 * Segments are only EMITTED (published to Redis) when confirmed — the
 * beginning of the transcript is stable across consecutive submissions.
 * Buffer advances (trims confirmed audio) and continues with new audio.
 *
 * Hard cap prevents the buffer from growing beyond maxBufferDuration —
 * at the cap, buffer is force-flushed and reset regardless of confirmation.
 */

interface SpeakerBuffer {
  speakerId: string;
  speakerName: string;
  chunks: Float32Array[];
  totalSamples: number;
  lastTranscript: string;
  confirmCount: number;
  inFlight: boolean;
  /** Pending confirmed transcript to emit */
  pendingEmit: string | null;
  /** Wall-clock time (ms) when buffer started accumulating audio */
  bufferStartMs: number;
  /** Number of chunks that were included in the last submission */
  submittedChunkCount: number;
  /** Total samples in the submitted portion */
  submittedSamples: number;
  /** Monotonic sequence number for segment_id generation */
  sequenceNumber: number;
}

export interface SpeakerStreamManagerConfig {
  /** Minimum audio before first submission (seconds). Default: 3 */
  minAudioDuration?: number;
  /** Interval between resubmissions (seconds). Default: 3 */
  submitInterval?: number;
  /** Consecutive matches to confirm (fuzzy). Default: 2 */
  confirmThreshold?: number;
  /** Hard cap — force reset at this duration (seconds). Default: 15 */
  maxBufferDuration?: number;
  /** Sample rate. Default: 16000 */
  sampleRate?: number;
}

export class SpeakerStreamManager {
  private buffers: Map<string, SpeakerBuffer> = new Map();
  private timers: Map<string, ReturnType<typeof setInterval>> = new Map();
  private minAudioDuration: number;
  private submitInterval: number;
  private confirmThreshold: number;
  private maxBufferDuration: number;
  private sampleRate: number;

  /**
   * Called when buffer needs transcription. Receives FULL unconfirmed buffer.
   * Caller must call handleTranscriptionResult() with the result.
   */
  onSegmentReady: ((speakerId: string, speakerName: string, audioBuffer: Float32Array) => void) | null = null;

  /**
   * Called when a segment is CONFIRMED and should be published.
   * Only fires after consecutive identical outputs — not on every resubmission.
   * @param bufferStartMs - wall-clock time when this buffer started accumulating
   * @param bufferEndMs - wall-clock time when the segment was confirmed/flushed
   * @param segmentId - stable ID: {speakerId}:{sequenceNumber}. Drafts + confirmed share the same ID.
   */
  onSegmentConfirmed: ((speakerId: string, speakerName: string, transcript: string, bufferStartMs: number, bufferEndMs: number, segmentId: string) => void) | null = null;

  constructor(config?: SpeakerStreamManagerConfig) {
    this.minAudioDuration = config?.minAudioDuration ?? 3;
    this.submitInterval = config?.submitInterval ?? 3;
    this.confirmThreshold = config?.confirmThreshold ?? 2;
    this.maxBufferDuration = config?.maxBufferDuration ?? 15;
    this.sampleRate = config?.sampleRate ?? 16000;
  }

  addSpeaker(speakerId: string, speakerName: string): void {
    if (this.buffers.has(speakerId)) return;

    this.buffers.set(speakerId, {
      speakerId,
      speakerName,
      chunks: [],
      totalSamples: 0,
      lastTranscript: '',
      confirmCount: 0,
      inFlight: false,
      pendingEmit: null,
      bufferStartMs: Date.now(),
      submittedChunkCount: 0,
      submittedSamples: 0,
      sequenceNumber: 0,
    });

    const timer = setInterval(() => this.trySubmit(speakerId), this.submitInterval * 1000);
    this.timers.set(speakerId, timer);

    log(`[SpeakerStreams] Added speaker "${speakerName}" (${speakerId})`);
  }

  feedAudio(speakerId: string, audioData: Float32Array): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return;
    buffer.chunks.push(audioData);
    buffer.totalSamples += audioData.length;
  }

  handleTranscriptionResult(speakerId: string, transcript: string): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) return;

    buffer.inFlight = false;

    if (!transcript || transcript.trim().length === 0) return;

    const trimmed = transcript.trim();

    // Fuzzy match: compare first 80% of the shorter string
    const compareLen = Math.floor(Math.min(trimmed.length, buffer.lastTranscript.length) * 0.8);

    if (compareLen > 20 && trimmed.substring(0, compareLen) === buffer.lastTranscript.substring(0, compareLen)) {
      buffer.confirmCount++;
    } else {
      buffer.lastTranscript = trimmed;
      buffer.confirmCount = 1;
    }

    if (buffer.confirmCount >= this.confirmThreshold) {
      // Confirmed — emit and advance
      buffer.pendingEmit = trimmed;
      this.emitAndReset(buffer);
    }
  }

  removeSpeaker(speakerId: string): void {
    const timer = this.timers.get(speakerId);
    if (timer) clearInterval(timer);
    this.timers.delete(speakerId);

    const buffer = this.buffers.get(speakerId);
    if (buffer && buffer.totalSamples > 0 && buffer.lastTranscript) {
      // Emit whatever we have on exit
      buffer.pendingEmit = buffer.lastTranscript;
      this.emitAndReset(buffer);
    }

    this.buffers.delete(speakerId);
  }

  hasSpeaker(speakerId: string): boolean {
    return this.buffers.has(speakerId);
  }

  updateSpeakerName(speakerId: string, newName: string): boolean {
    const buffer = this.buffers.get(speakerId);
    if (!buffer || buffer.speakerName === newName) return false;
    log(`[SpeakerStreams] Updated speaker name "${buffer.speakerName}" → "${newName}" (${speakerId})`);
    buffer.speakerName = newName;
    return true;
  }

  getSpeakerName(speakerId: string): string | undefined {
    return this.buffers.get(speakerId)?.speakerName;
  }

  /** Get current segment_id for a speaker (for draft publishing). */
  getSegmentId(speakerId: string): string {
    const buffer = this.buffers.get(speakerId);
    const seq = buffer?.sequenceNumber ?? 0;
    return `${speakerId}:${seq}`;
  }

  getActiveSpeakers(): string[] {
    return Array.from(this.buffers.keys());
  }

  /** Get the wall-clock time when the current buffer started accumulating */
  getBufferStartMs(speakerId: string): number {
    return this.buffers.get(speakerId)?.bufferStartMs ?? Date.now();
  }

  removeAll(): void {
    for (const speakerId of Array.from(this.buffers.keys())) {
      this.removeSpeaker(speakerId);
    }
  }

  private trySubmit(speakerId: string): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer || buffer.inFlight) return;

    const audioDurationSec = buffer.totalSamples / this.sampleRate;
    const wallClockDurationSec = (Date.now() - buffer.bufferStartMs) / 1000;

    // Hard cap — force emit based on WALL CLOCK time (not audio duration).
    // Audio duration can be much shorter than wall clock when VAD filters
    // silence or audio arrives in bursts. Without wall-clock cap, buffers
    // accumulate for minutes producing mega-segments.
    if (wallClockDurationSec >= this.maxBufferDuration) {
      if (buffer.lastTranscript) {
        buffer.pendingEmit = buffer.lastTranscript;
      }
      this.emitAndReset(buffer);
      // Still submit if there's audio left after reset
      return;
    }

    if (audioDurationSec >= this.minAudioDuration) {
      this.submitBuffer(buffer);
    }
  }

  private submitBuffer(buffer: SpeakerBuffer): void {
    if (buffer.totalSamples === 0 || !this.onSegmentReady) return;

    buffer.inFlight = true;
    // Record how many chunks are in this submission so emitAndReset
    // knows which chunks to keep (ones that arrived after submission)
    buffer.submittedChunkCount = buffer.chunks.length;
    buffer.submittedSamples = buffer.totalSamples;

    const combined = new Float32Array(buffer.totalSamples);
    let offset = 0;
    for (const chunk of buffer.chunks) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }

    try {
      this.onSegmentReady(buffer.speakerId, buffer.speakerName, combined);
    } catch (err: any) {
      buffer.inFlight = false;
    }
  }

  private emitAndReset(buffer: SpeakerBuffer): void {
    if (buffer.pendingEmit && this.onSegmentConfirmed) {
      const endMs = Date.now();
      const segmentId = `${buffer.speakerId}:${buffer.sequenceNumber}`;
      this.onSegmentConfirmed(buffer.speakerId, buffer.speakerName, buffer.pendingEmit, buffer.bufferStartMs, endMs, segmentId);
      buffer.sequenceNumber++;
    }

    // Keep chunks that arrived AFTER the last submission — they're the
    // beginning of the next segment and must not be discarded.
    const newChunks = buffer.chunks.slice(buffer.submittedChunkCount);
    let newSamples = 0;
    for (const c of newChunks) newSamples += c.length;

    buffer.chunks = newChunks;
    buffer.totalSamples = newSamples;
    buffer.lastTranscript = '';
    buffer.confirmCount = 0;
    buffer.pendingEmit = null;
    buffer.inFlight = false;
    buffer.bufferStartMs = Date.now();
    buffer.submittedChunkCount = 0;
    buffer.submittedSamples = 0;
  }
}
