/**
 * OnnxLocalDiarizer — MVP1 real-diarization, ONNX-in-Node edition.
 *
 * Runs entirely in the Node process. No Python sidecar, no PyTorch. Uses:
 *
 *   - @huggingface/transformers' WeSpeakerFeatureExtractor for mel-fbank
 *     features (matches WeSpeaker's exact preprocessing config)
 *   - onnx-community/wespeaker-voxceleb-resnet34-LM (quantized, ~6.6 MB)
 *     for 256-dim speaker embeddings, executed via onnxruntime-node
 *   - OnlineSpeakerClustering (TypeScript, this package) for stable
 *     speaker IDs across the session
 *
 * Cost vs the previous Python sidecar:
 *   - Container image:  +~7 MB ONNX model (vs +~1.1 GB Python venv)
 *   - RAM per bot:      +~50-100 MiB (vs +~530 MiB Python+torch)
 *   - Cold start:       +~200 ms model load (vs +~8 s torch import)
 *   - CPU per region:   ~5-20 ms per embedding (vs ~35 ms PyTorch)
 *   - Process model:    single Node process (vs bot + Python child + IPC)
 *
 * The diarizer's `process(audio, ts)` accumulates audio in a per-speaker
 * inference window. On every silence→speech transition (detected via a
 * small local energy gate; we'll swap for bot's Silero VAD when this is
 * folded into the bot), it commits the just-ended utterance to the
 * embedding model and online-clustering. Frames inside an utterance get
 * the speaker label assigned to that utterance.
 */

import path from 'path';
import { fileURLToPath } from 'url';

import {
  AutoModel,
  AutoProcessor,
  Tensor,
  env as transformersEnv,
  type PreTrainedModel,
  type Processor,
} from '@huggingface/transformers';

import type { Diarizer, DiarizerLabel } from './diarizer';
import { OnlineSpeakerClustering } from './online-clustering';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const MODEL_ID = 'onnx-community/wespeaker-voxceleb-resnet34-LM';

export interface OnnxLocalDiarizerConfig {
  /** Cosine distance threshold for online clustering. Default 0.40. */
  newSpeakerThreshold?: number;
  /** Min utterance length to embed. Shorter utterances emit the previous
   *  speaker label and don't update the clusterer (their embeddings are
   *  too noisy to trust). Default 600 ms. */
  minUtteranceMs?: number;
  /** Max utterance length before forcing an embed-commit. Default 8000 ms. */
  maxUtteranceMs?: number;
  /** RMS threshold above which a frame counts as speech. Default 0.012. */
  speechRmsThreshold?: number;
  /** RMS threshold below which speech turns off (hysteresis). Default 0.006. */
  silenceRmsThreshold?: number;
  /** Min consecutive silent ms before declaring speech end. Default 350. */
  minSilenceMs?: number;
  /** Optional upper bound on number of speakers. */
  maxSpeakers?: number;
}

const SAMPLE_RATE = 16_000;

export class OnnxLocalDiarizer implements Diarizer {
  public readonly name = 'onnx-local (wespeaker-resnet34-LM via transformers.js, TS clustering)';

  private model: PreTrainedModel;
  private processor: Processor;
  private clustering: OnlineSpeakerClustering;

  private readonly minUtteranceSamples: number;
  private readonly maxUtteranceSamples: number;
  private readonly speechRms: number;
  private readonly silenceRms: number;
  private readonly minSilenceSamples: number;

  /** Audio of the current utterance — appended on each speech frame, embedded
   *  + cleared on each silence transition. */
  private utteranceChunks: Float32Array[] = [];
  private utteranceSamples = 0;
  private utteranceStartTs: number | null = null;
  private inSpeech = false;
  private silenceSampleAccumulator = 0;
  private lastLabel: DiarizerLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };

  private constructor(
    model: PreTrainedModel,
    processor: Processor,
    cfg: OnnxLocalDiarizerConfig,
  ) {
    this.model = model;
    this.processor = processor;
    this.clustering = new OnlineSpeakerClustering({
      newSpeakerThreshold: cfg.newSpeakerThreshold,
      maxSpeakers: cfg.maxSpeakers,
    });
    this.minUtteranceSamples = Math.floor(((cfg.minUtteranceMs ?? 600) / 1000) * SAMPLE_RATE);
    this.maxUtteranceSamples = Math.floor(((cfg.maxUtteranceMs ?? 8000) / 1000) * SAMPLE_RATE);
    this.speechRms = cfg.speechRmsThreshold ?? 0.012;
    this.silenceRms = cfg.silenceRmsThreshold ?? 0.006;
    this.minSilenceSamples = Math.floor(((cfg.minSilenceMs ?? 350) / 1000) * SAMPLE_RATE);
  }

  static async create(cfg: OnnxLocalDiarizerConfig = {}): Promise<OnnxLocalDiarizer> {
    transformersEnv.allowLocalModels = true;
    transformersEnv.allowRemoteModels = true; // first run downloads from HF; cached after

    console.log(`[onnx-diarizer] loading processor (mel-fbank) for ${MODEL_ID}...`);
    const processor = await AutoProcessor.from_pretrained(MODEL_ID);
    console.log(`[onnx-diarizer] loading model (quantized ONNX, ~6.6 MB)...`);
    const model = await AutoModel.from_pretrained(MODEL_ID, { dtype: 'q8' });
    console.log(`[onnx-diarizer] model ready (transformers.js handles ONNX inference internally)`);
    return new OnnxLocalDiarizer(model, processor, cfg);
  }

  /** Synchronous energy-based VAD on a single audio frame. RMS gate with
   *  hysteresis. Returns `true` if currently in a speech turn after
   *  processing this frame. Mutates internal speech-state. */
  private updateVadAndAccumulate(audio: Float32Array): boolean {
    const rms = computeRms(audio);
    if (rms >= this.speechRms) {
      this.silenceSampleAccumulator = 0;
      this.inSpeech = true;
    } else if (rms <= this.silenceRms) {
      this.silenceSampleAccumulator += audio.length;
      if (this.inSpeech && this.silenceSampleAccumulator >= this.minSilenceSamples) {
        this.inSpeech = false;
      }
    }
    // Between thresholds: hold previous state.
    return this.inSpeech;
  }

  private async embedUtterance(utteranceAudio: Float32Array): Promise<Float32Array | null> {
    // Processor turns raw PCM into the mel-fbank input the model expects.
    const inputs = await this.processor(utteranceAudio, { sampling_rate: SAMPLE_RATE });
    // Model.forward gives back { last_hidden_state: Tensor(1, 256) }.
    const outputs = (await this.model(inputs)) as { [k: string]: Tensor };
    const out = outputs.last_hidden_state ?? outputs[Object.keys(outputs)[0]];
    if (!out) {
      console.error(`[onnx-diarizer] model returned no output (got keys ${Object.keys(outputs)})`);
      return null;
    }
    const emb = new Float32Array(out.data as Float32Array);
    return l2Normalize(emb);
  }

  async process(audio: Float32Array, timestampMs: number): Promise<DiarizerLabel> {
    const wasInSpeech = this.inSpeech;
    const isInSpeech = this.updateVadAndAccumulate(audio);

    if (isInSpeech) {
      if (!wasInSpeech) {
        // Speech start
        this.utteranceStartTs = timestampMs;
        this.utteranceChunks = [];
        this.utteranceSamples = 0;
      }
      this.utteranceChunks.push(audio);
      this.utteranceSamples += audio.length;

      // Cap-and-commit on long single-speaker monologue so we keep emitting
      // updated labels and don't accumulate unbounded audio.
      if (this.utteranceSamples >= this.maxUtteranceSamples) {
        await this.commitUtterance();
      }
    } else if (wasInSpeech && !isInSpeech) {
      // Speech end — commit the just-finished utterance.
      await this.commitUtterance();
    }

    return this.lastLabel;
  }

  private async commitUtterance(): Promise<void> {
    if (this.utteranceSamples < this.minUtteranceSamples) {
      // Too short — drop without clustering. Lastlabel stays as previous.
      this.utteranceChunks = [];
      this.utteranceSamples = 0;
      this.utteranceStartTs = null;
      return;
    }

    const combined = concatFloat32(this.utteranceChunks, this.utteranceSamples);
    this.utteranceChunks = [];
    this.utteranceSamples = 0;
    const utteranceTs = this.utteranceStartTs;
    this.utteranceStartTs = null;

    try {
      const emb = await this.embedUtterance(combined);
      if (!emb) return;
      const assignment = this.clustering.assign(emb);
      this.lastLabel = {
        speakerId: assignment.speakerId,
        speakerName: assignment.speakerId,
      };
      console.log(
        `[onnx-diarizer] commit utterance ` +
          `dur=${(combined.length / SAMPLE_RATE).toFixed(2)}s ` +
          `→ ${assignment.speakerId} ` +
          `(dist=${assignment.distance.toFixed(3)}, ` +
          `new=${assignment.isNew}, total=${this.clustering.size()})`,
      );
    } catch (err: any) {
      console.error(`[onnx-diarizer] embed/cluster failed: ${err.message}`);
    }
  }

  reset(): void {
    this.utteranceChunks = [];
    this.utteranceSamples = 0;
    this.utteranceStartTs = null;
    this.inSpeech = false;
    this.silenceSampleAccumulator = 0;
    this.lastLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };
    this.clustering.reset();
  }
}

function computeRms(audio: Float32Array): number {
  if (audio.length === 0) return 0;
  let sumSq = 0;
  for (let i = 0; i < audio.length; i++) {
    const s = audio[i];
    sumSq += s * s;
  }
  return Math.sqrt(sumSq / audio.length);
}

function concatFloat32(chunks: Float32Array[], total: number): Float32Array {
  const out = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

function l2Normalize(v: Float32Array): Float32Array {
  let sumSq = 0;
  for (let i = 0; i < v.length; i++) sumSq += v[i] * v[i];
  const norm = Math.sqrt(sumSq);
  if (norm < 1e-8) return v;
  const out = new Float32Array(v.length);
  for (let i = 0; i < v.length; i++) out[i] = v[i] / norm;
  return out;
}
