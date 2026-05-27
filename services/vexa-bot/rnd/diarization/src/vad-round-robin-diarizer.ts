/**
 * VadRoundRobinDiarizer — MVP0 stub.
 *
 * Uses the bot's production Silero VAD (`services/vad.ts`, imported as-is)
 * for speech-onset detection. On every silence→speech transition, rotates
 * the speaker counter modulo numSpeakers and returns the next label.
 *
 * This is OBVIOUSLY not real diarization — there is no voice discrimination.
 * It is a stub that exercises the Diarizer seam end-to-end so MVP1 can plug
 * in `PyannoteSidecarDiarizer` (real voice clustering) behind the same
 * interface with a one-line swap.
 *
 * Critically: this diarizer NEVER touches buffering, transcription, or
 * segment publishing. Those are the bot's SpeakerStreamManager +
 * TranscriptionClient + SegmentPublisher running downstream of
 * `speakerManager.feedAudio(speakerId, audio)`.
 */

import { SileroVAD, type VadSpeakerState } from '../../../core/src/services/vad';
import type { Diarizer, DiarizerLabel } from './diarizer';

export interface VadRoundRobinConfig {
  /** Number of round-robin labels. Default 2. */
  numSpeakers?: number;
  /** Silero speech threshold (0..1). Default 0.5. */
  vadThreshold?: number;
  /** Silero min silence (ms) before declaring silence. Default 350. */
  minSilenceMs?: number;
}

export class VadRoundRobinDiarizer implements Diarizer {
  public readonly name = 'vad-round-robin (MVP0 stub, bot Silero VAD)';

  private readonly vad: SileroVAD;
  private readonly numSpeakers: number;
  private state: VadSpeakerState;
  private wasInSpeech = false;
  private counter = -1;
  private lastLabel: DiarizerLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };

  private constructor(vad: SileroVAD, numSpeakers: number) {
    this.vad = vad;
    this.numSpeakers = numSpeakers;
    this.state = vad.createSpeakerState();
  }

  static async create(cfg: VadRoundRobinConfig = {}): Promise<VadRoundRobinDiarizer> {
    const vad = await SileroVAD.create(cfg.vadThreshold ?? 0.5, cfg.minSilenceMs ?? 350);
    return new VadRoundRobinDiarizer(vad, cfg.numSpeakers ?? 2);
  }

  async process(audio: Float32Array, _timestampMs: number): Promise<DiarizerLabel> {
    const isSpeech = await this.vad.isSpeechStreaming(audio, this.state);

    if (isSpeech && !this.wasInSpeech) {
      this.counter = (this.counter + 1) % this.numSpeakers;
      const label = `speaker_${this.counter}`;
      this.lastLabel = { speakerId: label, speakerName: label };
    }
    this.wasInSpeech = isSpeech;
    return this.lastLabel;
  }

  reset(): void {
    this.state = this.vad.createSpeakerState();
    this.wasInSpeech = false;
    this.counter = -1;
    this.lastLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };
  }

  /** Expose VAD state for callers who want to skip non-speech feedAudio()
   *  (matches the bot's production pattern for GMeet — index.ts:1693). */
  async isSpeech(audio: Float32Array): Promise<boolean> {
    return this.vad.isSpeechStreaming(audio, this.state);
  }
}
