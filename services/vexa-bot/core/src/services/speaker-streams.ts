import { log } from '../utils';

/**
 * Per-speaker audio buffer.
 * Accumulates Float32 chunks until enough audio is ready for transcription.
 */
interface SpeakerBuffer {
  speakerId: string;
  speakerName: string;
  chunks: Float32Array[];
  totalSamples: number;
}

export interface SpeakerStreamManagerConfig {
  /** Minimum audio duration (seconds) before firing onSegmentReady. Default: 2 */
  minAudioDuration?: number;
  /** Sample rate of incoming audio. Default: 16000 */
  sampleRate?: number;
}

/**
 * Manages multiple per-speaker audio buffers.
 * When a speaker accumulates enough audio, fires onSegmentReady callback
 * with the concatenated Float32Array.
 */
export class SpeakerStreamManager {
  private buffers: Map<string, SpeakerBuffer> = new Map();
  private minAudioDuration: number;
  private sampleRate: number;

  /**
   * Callback fired when a speaker has accumulated enough audio for transcription.
   * Set this before feeding audio.
   */
  onSegmentReady: ((speakerId: string, speakerName: string, audioBuffer: Float32Array) => void) | null = null;

  constructor(config?: SpeakerStreamManagerConfig) {
    this.minAudioDuration = config?.minAudioDuration ?? 2;
    this.sampleRate = config?.sampleRate ?? 16000;
  }

  /**
   * Create a new buffer for a speaker.
   * If the speaker already exists, this is a no-op (logs a warning).
   */
  addSpeaker(speakerId: string, speakerName: string): void {
    if (this.buffers.has(speakerId)) {
      log(`[SpeakerStreams] Speaker "${speakerName}" (${speakerId}) already tracked, ignoring addSpeaker`);
      return;
    }

    this.buffers.set(speakerId, {
      speakerId,
      speakerName,
      chunks: [],
      totalSamples: 0,
    });

    log(`[SpeakerStreams] Added speaker "${speakerName}" (${speakerId})`);
  }

  /**
   * Add audio data to a speaker's buffer.
   * When the buffer reaches minAudioDuration, fires onSegmentReady and resets.
   */
  feedAudio(speakerId: string, audioData: Float32Array): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) {
      log(`[SpeakerStreams] feedAudio called for unknown speaker ${speakerId}, ignoring`);
      return;
    }

    buffer.chunks.push(audioData);
    buffer.totalSamples += audioData.length;

    const durationSec = buffer.totalSamples / this.sampleRate;
    if (durationSec >= this.minAudioDuration) {
      this.flushBuffer(buffer);
    }
  }

  /**
   * Remove a speaker: flush any remaining audio, then clean up the buffer.
   */
  removeSpeaker(speakerId: string): void {
    const buffer = this.buffers.get(speakerId);
    if (!buffer) {
      log(`[SpeakerStreams] removeSpeaker called for unknown speaker ${speakerId}, ignoring`);
      return;
    }

    // Flush whatever is left (even if below minAudioDuration)
    if (buffer.totalSamples > 0) {
      this.flushBuffer(buffer);
    }

    this.buffers.delete(speakerId);
    log(`[SpeakerStreams] Removed speaker "${buffer.speakerName}" (${speakerId})`);
  }

  /**
   * Check whether a speaker is already being tracked.
   */
  hasSpeaker(speakerId: string): boolean {
    return this.buffers.has(speakerId);
  }

  /**
   * Get all currently tracked speaker IDs.
   */
  getActiveSpeakers(): string[] {
    return Array.from(this.buffers.keys());
  }

  /**
   * Remove all speakers: flush remaining audio for each, then clear the map.
   */
  removeAll(): void {
    for (const speakerId of Array.from(this.buffers.keys())) {
      this.removeSpeaker(speakerId);
    }
  }

  /**
   * Concatenate buffered chunks, fire the callback, and reset the buffer.
   */
  private flushBuffer(buffer: SpeakerBuffer): void {
    if (buffer.totalSamples === 0) return;

    const combined = new Float32Array(buffer.totalSamples);
    let offset = 0;
    for (const chunk of buffer.chunks) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }

    // Reset buffer
    buffer.chunks = [];
    buffer.totalSamples = 0;

    // Fire callback
    if (this.onSegmentReady) {
      try {
        this.onSegmentReady(buffer.speakerId, buffer.speakerName, combined);
      } catch (err: any) {
        log(`[SpeakerStreams] onSegmentReady callback error for "${buffer.speakerName}": ${err.message}`);
      }
    }
  }
}
