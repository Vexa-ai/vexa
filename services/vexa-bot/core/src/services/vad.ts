/**
 * Silero VAD (Voice Activity Detection) for Node.js.
 *
 * Wraps the Silero ONNX model via onnxruntime-node.
 *
 * Two modes:
 * 1. Batch: `isSpeech(buffer)` — check if a buffer contains speech (legacy, used in tests)
 * 2. Streaming: per-speaker state with hysteresis for real-time speech/silence transitions
 *
 * Streaming mode uses 512-sample windows at 16kHz (32ms each) with a 64-sample
 * context buffer carried between calls. Each speaker gets their own LSTM state
 * to prevent cross-contamination.
 *
 * Reference: @jjhbw/silero-vad library (lib.js) and Silero VAD Python iterator.
 */

import { log } from '../utils';

let ort: any = null;

async function getOrt() {
  if (!ort) {
    ort = require('onnxruntime-node');
  }
  return ort;
}

/** Correct window size for 16kHz audio (per Silero spec) */
const WINDOW_SIZE = 512;   // 32ms at 16kHz
/** Context samples prepended to each window for boundary continuity */
const CONTEXT_SIZE = 64;
const SAMPLE_RATE = 16000;

/**
 * Per-speaker VAD state for streaming mode.
 * Each speaker gets their own LSTM state and hysteresis tracker.
 */
export interface VadSpeakerState {
  /** LSTM hidden state — carried between processChunk calls */
  lstmState: Float32Array;
  /** Last CONTEXT_SIZE samples from previous call — prepended to next window */
  context: Float32Array;
  /** Whether we're currently in a speech region */
  triggered: boolean;
  /** Sample position where silence was first detected (for min-silence check) */
  tempEnd: number;
  /** Total samples processed (for timing) */
  currentSample: number;
}

export class SileroVAD {
  private session: any;
  private threshold: number;
  private negThreshold: number;
  /** Minimum silence duration (samples) before speech_end fires */
  private minSilenceSamples: number;
  /** Reusable input buffer (CONTEXT_SIZE + WINDOW_SIZE) */
  private inputBuffer: Float32Array;
  /** Reusable sr tensor */
  private srTensor: any;

  private constructor(session: any, threshold: number, minSilenceDurationMs: number) {
    this.session = session;
    this.threshold = threshold;
    this.negThreshold = Math.max(threshold - 0.15, 0.01);
    this.minSilenceSamples = (SAMPLE_RATE * minSilenceDurationMs) / 1000;
    this.inputBuffer = new Float32Array(CONTEXT_SIZE + WINDOW_SIZE);
    this.srTensor = null; // initialized lazily with ort
  }

  static async create(threshold = 0.6, minSilenceDurationMs = 250): Promise<SileroVAD> {
    const ort = await getOrt();
    const path = require('path');
    const fs = require('fs');

    const candidates = [
      path.resolve(__dirname, '..', '..', 'node_modules', '@jjhbw', 'silero-vad', 'weights', 'silero_vad.onnx'),
      path.resolve(__dirname, '..', '..', '..', 'node_modules', '@jjhbw', 'silero-vad', 'weights', 'silero_vad.onnx'),
      '/app/vexa-bot/core/node_modules/@jjhbw/silero-vad/weights/silero_vad.onnx',
      '/app/silero_vad.onnx',
    ];

    let modelPath = '';
    for (const p of candidates) {
      if (fs.existsSync(p)) { modelPath = p; break; }
    }

    if (!modelPath) {
      throw new Error('Silero VAD model not found');
    }

    const session = await ort.InferenceSession.create(modelPath);
    log(`[VAD] Silero model loaded from ${modelPath}`);
    return new SileroVAD(session, threshold, minSilenceDurationMs);
  }

  /** Create a fresh per-speaker VAD state */
  createSpeakerState(): VadSpeakerState {
    return {
      lstmState: new Float32Array(2 * 1 * 128),
      context: new Float32Array(CONTEXT_SIZE),
      triggered: false,
      tempEnd: 0,
      currentSample: 0,
    };
  }

  /**
   * Process a single 512-sample window through the ONNX model with speaker-specific state.
   * Prepends the context buffer (64 samples) for boundary continuity.
   * Updates the speaker's LSTM state and context in-place.
   */
  private async processWindow(window: Float32Array, state: VadSpeakerState): Promise<number> {
    const ort = await getOrt();

    // Build input: context (64) + window (512) = 576 samples
    this.inputBuffer.set(state.context, 0);
    this.inputBuffer.set(window, CONTEXT_SIZE);

    const inputTensor = new ort.Tensor('float32', this.inputBuffer, [1, CONTEXT_SIZE + WINDOW_SIZE]);
    const stateTensor = new ort.Tensor('float32', state.lstmState, [2, 1, 128]);
    if (!this.srTensor) {
      this.srTensor = new ort.Tensor('int64', new BigInt64Array([BigInt(SAMPLE_RATE)]), [1]);
    }

    const results = await this.session.run({
      input: inputTensor,
      state: stateTensor,
      sr: this.srTensor,
    });

    const prob = results.output.data[0] as number;

    // Update speaker state
    state.lstmState = new Float32Array(results.stateN.data as Float32Array);
    // Carry last CONTEXT_SIZE samples as context for next call
    state.context.set(this.inputBuffer.subarray(this.inputBuffer.length - CONTEXT_SIZE));

    return prob;
  }

  /**
   * Process an audio chunk (typically 4096 samples = 256ms from browser ScriptProcessor)
   * through a speaker's VAD state. Returns whether the speaker is currently in speech.
   *
   * Uses hysteresis: speech starts at `threshold` (0.5), ends when probability
   * stays below `negThreshold` (0.35) for `minSilenceDurationMs` (100ms).
   *
   * @param audio - Raw audio chunk (any size, will be processed in 512-sample windows)
   * @param state - Per-speaker VAD state (mutated in place)
   * @returns true if speaker is in speech (should feed to buffer), false if silence
   */
  async isSpeechStreaming(audio: Float32Array, state: VadSpeakerState): Promise<boolean> {
    for (let i = 0; i + WINDOW_SIZE <= audio.length; i += WINDOW_SIZE) {
      const window = audio.subarray(i, i + WINDOW_SIZE);
      const prob = await this.processWindow(window, state);
      state.currentSample += WINDOW_SIZE;

      // Hysteresis logic (matches Silero VADIterator / @jjhbw getSpeechTimestamps)
      if (prob >= this.threshold && state.tempEnd) {
        // Was in tentative silence, but speech resumed — cancel silence detection
        state.tempEnd = 0;
      }

      if (prob >= this.threshold && !state.triggered) {
        // Speech start
        state.triggered = true;
      }

      if (prob < this.negThreshold && state.triggered) {
        // Possible speech end — start counting silence duration
        if (!state.tempEnd) {
          state.tempEnd = state.currentSample;
        }
        if (state.currentSample - state.tempEnd >= this.minSilenceSamples) {
          // Confirmed speech end — silence lasted long enough
          state.triggered = false;
          state.tempEnd = 0;
        }
      }
    }

    return state.triggered;
  }

  /**
   * Legacy batch API: check if a buffer contains speech.
   * Processes in WINDOW_SIZE (512) sample chunks using a temporary state.
   * Used in tests and backward-compat paths.
   */
  async isSpeech(audio: Float32Array): Promise<boolean> {
    const tempState = this.createSpeakerState();
    let maxProb = 0;

    for (let i = 0; i + WINDOW_SIZE <= audio.length; i += WINDOW_SIZE) {
      const window = audio.subarray(i, i + WINDOW_SIZE);
      const prob = await this.processWindow(window, tempState);
      if (prob > maxProb) maxProb = prob;
      if (maxProb > this.threshold) return true;
    }

    return maxProb > this.threshold;
  }

  resetState(): void {
    // Legacy — only relevant if using shared state (deprecated)
  }
}
