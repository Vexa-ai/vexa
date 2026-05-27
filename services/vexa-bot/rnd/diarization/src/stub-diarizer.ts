/**
 * VadRoundRobinDiarizer — MVP0 stub.
 *
 * On every silence→speech transition, advance the speaker counter modulo
 * numSpeakers and emit `speaker_${counter}` until the next transition.
 *
 * VAD is a self-contained RMS-energy gate with hysteresis. This is
 * intentional for MVP0 to keep the harness dependency footprint minimal
 * and avoid the bot's Silero/ONNX model-path coupling. MVP1 swaps the
 * whole diarizer for `PyannoteSidecarDiarizer`, which brings real
 * segmentation + embedding-based diarization (and pyannote's own VAD)
 * inside a Python child process — at which point this stub is retired.
 *
 * The point at MVP0 is NOT to diarize accurately. It is to prove the
 * seam contract end-to-end (audio → diarizer → label → transcription →
 * dashboard) with a single, obviously-stub implementation behind the
 * `Diarizer` interface.
 */

import type { Diarizer } from './diarizer';

export interface VadRoundRobinConfig {
  /** Number of round-robin speakers. Default 2. */
  numSpeakers?: number;
  /** RMS threshold above which a frame counts as speech (0..1). Default 0.012. */
  speechThreshold?: number;
  /** RMS threshold below which speech turns off (hysteresis). Default 0.006. */
  silenceThreshold?: number;
  /** Min consecutive ms below silenceThreshold before declaring silence. Default 350ms. */
  minSilenceMs?: number;
  /** Sample rate of incoming audio. Default 16000. */
  sampleRate?: number;
}

export class VadRoundRobinDiarizer implements Diarizer {
  public readonly name = 'vad-round-robin (MVP0 stub, RMS-energy VAD)';

  private readonly numSpeakers: number;
  private readonly speechThreshold: number;
  private readonly silenceThreshold: number;
  private readonly minSilenceSamples: number;
  private readonly sampleRate: number;

  private wasInSpeech = false;
  private counter = -1;
  private silenceSampleAccumulator = 0;
  private lastLabel = 'speaker_0';

  constructor(cfg: VadRoundRobinConfig = {}) {
    this.numSpeakers = cfg.numSpeakers ?? 2;
    this.speechThreshold = cfg.speechThreshold ?? 0.012;
    this.silenceThreshold = cfg.silenceThreshold ?? 0.006;
    this.sampleRate = cfg.sampleRate ?? 16000;
    const minSilenceMs = cfg.minSilenceMs ?? 350;
    this.minSilenceSamples = Math.floor((minSilenceMs / 1000) * this.sampleRate);
  }

  /** Synchronous; the Diarizer interface allows async to fit future backends. */
  async process(audio: Float32Array, _timestampMs: number): Promise<string> {
    const rms = computeRMS(audio);
    if (rms >= this.speechThreshold) {
      this.silenceSampleAccumulator = 0;
      if (!this.wasInSpeech) {
        this.counter = (this.counter + 1) % this.numSpeakers;
        this.lastLabel = `speaker_${this.counter}`;
        this.wasInSpeech = true;
      }
    } else if (rms <= this.silenceThreshold) {
      this.silenceSampleAccumulator += audio.length;
      if (this.wasInSpeech && this.silenceSampleAccumulator >= this.minSilenceSamples) {
        this.wasInSpeech = false;
      }
    } else {
      // Between thresholds — neither confirm speech nor confirm silence.
      // Hold previous state, but don't accumulate silence either.
    }
    return this.lastLabel;
  }

  reset(): void {
    this.wasInSpeech = false;
    this.counter = -1;
    this.silenceSampleAccumulator = 0;
    this.lastLabel = 'speaker_0';
  }
}

function computeRMS(audio: Float32Array): number {
  if (audio.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < audio.length; i++) {
    const s = audio[i];
    sum += s * s;
  }
  return Math.sqrt(sum / audio.length);
}
