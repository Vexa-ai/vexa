/**
 * Silero VAD (Voice Activity Detection) for Node.js.
 *
 * Wraps the Silero ONNX model directly via onnxruntime-node.
 * Used to filter silence before sending audio to transcription-service.
 *
 * Usage:
 *   const vad = await SileroVAD.create();
 *   const isSpeech = await vad.isSpeech(float32Audio);
 *   // isSpeech = true → send to transcription
 *   // isSpeech = false → skip (silence)
 */

import { log } from '../utils';

let ort: any = null;

async function getOrt() {
  if (!ort) {
    ort = require('onnxruntime-node');
  }
  return ort;
}

export class SileroVAD {
  private session: any;
  private state: Float32Array;
  private sr: BigInt64Array;
  private threshold: number;

  private constructor(session: any, threshold: number) {
    this.session = session;
    this.state = new Float32Array(2 * 1 * 128); // 2 layers, 1 batch, 128 hidden
    this.sr = new BigInt64Array([BigInt(16000)]);
    this.threshold = threshold;
  }

  static async create(threshold = 0.5): Promise<SileroVAD> {
    const ort = await getOrt();
    const path = require('path');
    const fs = require('fs');

    // Find model file
    const candidates = [
      path.resolve(__dirname, '..', '..', '..', 'node_modules', '@jjhbw', 'silero-vad', 'weights', 'silero_vad.onnx'),
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
    return new SileroVAD(session, threshold);
  }

  /**
   * Check if a chunk of audio contains speech.
   * Input: Float32Array of 256 samples (16ms at 16kHz).
   * Returns: speech probability (0-1).
   */
  async processChunk(audio: Float32Array): Promise<number> {
    const ort = await getOrt();

    const inputTensor = new ort.Tensor('float32', audio, [1, audio.length]);
    const stateTensor = new ort.Tensor('float32', this.state, [2, 1, 128]);
    const srTensor = new ort.Tensor('int64', this.sr, [1]);

    const feeds: Record<string, any> = {
      input: inputTensor,
      state: stateTensor,
      sr: srTensor,
    };

    const results = await this.session.run(feeds);
    const prob = results.output.data[0] as number;

    // Update state for next call
    this.state = new Float32Array(results.stateN.data as Float32Array);

    return prob;
  }

  /**
   * Check if a larger audio buffer contains speech.
   * Processes in 256-sample chunks, returns max probability.
   */
  async isSpeech(audio: Float32Array): Promise<boolean> {
    let maxProb = 0;
    const chunkSize = 256;

    for (let i = 0; i + chunkSize <= audio.length; i += chunkSize) {
      const chunk = audio.slice(i, i + chunkSize);
      const prob = await this.processChunk(chunk);
      if (prob > maxProb) maxProb = prob;
      // Early exit if clearly speech
      if (maxProb > this.threshold) return true;
    }

    return maxProb > this.threshold;
  }

  resetState(): void {
    this.state = new Float32Array(2 * 1 * 128);
  }
}
