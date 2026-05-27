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
  /** Cosine distance threshold for online clustering. Default 0.50.
   *  Lower → more conservative (don't split same voice); higher → more
   *  aggressive splitting. Tuned for fp32 wespeaker-resnet34-LM on tab audio. */
  newSpeakerThreshold?: number;
  /** Min utterance length to *cluster* (i.e. emit a label). Default 600 ms. */
  minUtteranceMs?: number;
  /** Min utterance length to *seed a brand new centroid*. Stricter than
   *  `minUtteranceMs` because short first-impressions (intros, breaths)
   *  contaminate the centroid for the rest of the session. Default 1500 ms. */
  minSeedUtteranceMs?: number;
  /** Max utterance length before forcing an embed-commit. Default 8000 ms. */
  maxUtteranceMs?: number;
  /** RMS threshold above which a frame counts as speech. Default 0.012. */
  speechRmsThreshold?: number;
  /** RMS threshold below which speech turns off (hysteresis). Default 0.006. */
  silenceRmsThreshold?: number;
  /** Min consecutive silent ms before declaring speech end. Default 350. */
  minSilenceMs?: number;
  /** Optional HARD cap on speakers. Default unset (no cap). The hint from
   *  meeting context (tile count, roster) goes here ONLY when it's reliable;
   *  otherwise leave unset and let online clustering allocate freely.
   *  Wiring this from NUM_SPEAKERS broke MVP1's first demo — capped clusters
   *  forced wrong assignments. */
  maxSpeakers?: number;
  /** Mid-utterance change-point detection — handles "interruption with no
   *  silence gap" case where speaker B cuts speaker A off without a clean
   *  VAD boundary. While a speech segment is accumulating, every
   *  `changePointCheckIntervalMs` of new audio, compute embeddings on the
   *  first `changePointHeadTailMs` (head) and the last `changePointHeadTailMs`
   *  (tail). If they're > `changePointDistThreshold` apart in cosine
   *  distance, commit head+middle as utterance N and restart the buffer
   *  with the tail as utterance N+1. Set interval to 0 to disable. */
  changePointCheckIntervalMs?: number;
  changePointHeadTailMs?: number;
  changePointDistThreshold?: number;
  /** Optional callback fired on every committed utterance (after embedding +
   *  cluster assignment). Used by the eval pipeline to capture diarizer
   *  decisions without parsing console.log. Fields:
   *    - speakerId: the assigned cluster ID
   *    - tStartMs: utterance start timestamp (per the frames the caller fed)
   *    - tEndMs: utterance end timestamp
   *    - centroidDist: cosine distance to assigned centroid (NaN if first-ever or seed-blocked)
   *    - turnDist: cosine distance to previous committed utterance (NaN if first)
   *    - isNew: true iff this commit allocated a brand-new cluster
   *    - dbSize: total clusters after this commit
   *    - seedAllowed: did the utterance pass the min-seed duration gate
   */
  onCommit?: (ev: CommitEvent) => void;
}

export interface CommitEvent {
  speakerId: string;
  tStartMs: number;
  tEndMs: number;
  centroidDist: number;
  turnDist: number;
  isNew: boolean;
  dbSize: number;
  seedAllowed: boolean;
}

const SAMPLE_RATE = 16_000;

export class OnnxLocalDiarizer implements Diarizer {
  public readonly name = 'onnx-local (wespeaker-resnet34-LM via transformers.js, TS clustering)';

  private model: PreTrainedModel;
  private processor: Processor;
  private clustering: OnlineSpeakerClustering;

  private readonly minUtteranceSamples: number;
  private readonly minSeedUtteranceSamples: number;
  private readonly maxUtteranceSamples: number;
  private readonly speechRms: number;
  private readonly silenceRms: number;
  private readonly minSilenceSamples: number;
  private readonly changePointCheckIntervalSamples: number;
  private readonly changePointHeadTailSamples: number;
  private readonly changePointDistThreshold: number;

  /** Audio of the current utterance — appended on each speech frame, embedded
   *  + cleared on each silence transition. */
  private utteranceChunks: Float32Array[] = [];
  private utteranceSamples = 0;
  private utteranceStartTs: number | null = null;
  /** Sample-count high-water mark of the most-recent change-point check.
   *  Used so we only re-check after another `changePointCheckIntervalSamples`
   *  of new audio have accumulated. */
  private samplesAtLastCpCheck = 0;
  private inSpeech = false;
  private silenceSampleAccumulator = 0;
  private lastLabel: DiarizerLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };
  /** Embedding of the most-recently-committed utterance. Used purely for the
   *  turn-distance diagnostic — we report how far the current utterance is
   *  from the previous one, so we can see conversation dynamics in the log
   *  even when the clusterer assigns both to the same speaker_N. */
  private lastUtteranceEmb: Float32Array | null = null;
  /** Cumulative transitive map of speaker_N → final_speaker_N after merges.
   *  When two clusters get merged (e.g., a noisy short utterance allocated
   *  a spurious cluster that later evidence merged back), we record the
   *  mapping so consumers can rewrite past commit labels. Resolved
   *  transitively: if A→B then later B→C, lookups for A return C. */
  private labelRewrites = new Map<string, string>();

  private readonly onCommit?: (ev: CommitEvent) => void;

  private constructor(
    model: PreTrainedModel,
    processor: Processor,
    cfg: OnnxLocalDiarizerConfig,
  ) {
    this.model = model;
    this.processor = processor;
    this.onCommit = cfg.onCommit;
    this.clustering = new OnlineSpeakerClustering({
      // 0.45 with the second-nearest-gap rule. Single threshold alone
      // can't separate "mixed-voice noise" (~0.55, multiple speakers
      // overlapping) from "same-gender new speaker" (~0.55 too). The
      // gap-rule resolves it: new-cluster allocation requires either
      // (a) clear gap between nearest and second-nearest centroids,
      // meaning the embedding is distinctively close to nothing, OR
      // (b) very-far-from-everything (≥ veryFarThreshold).
      newSpeakerThreshold: cfg.newSpeakerThreshold ?? 0.45,
      maxSpeakers: cfg.maxSpeakers,
    });
    // 500ms. minUtt=1000 dropped too much (short turns went unembedded
    // entirely → kept previous label → wrong attribution for panel_b's
    // many short turns). Iter 8 settings (500/3000/4000/200) gave the
    // best useful-metric score so far (3/4).
    this.minUtteranceSamples = Math.floor(((cfg.minUtteranceMs ?? 500) / 1000) * SAMPLE_RATE);
    this.minSeedUtteranceSamples = Math.floor(((cfg.minSeedUtteranceMs ?? 3000) / 1000) * SAMPLE_RATE);
    // With change-point detection active, allow longer utterances. The
    // change-point check splits at speaker changes anyway, and longer
    // buffers give it the head/tail material it needs to fire reliably.
    // Previous 4000ms was force-committing BEFORE change-point could run
    // on interruption-heavy corpora — change-point fires earliest at
    // (2 * headTail + headTail/2) ≈ 3.75s, so cap must be well above that.
    this.maxUtteranceSamples = Math.floor(((cfg.maxUtteranceMs ?? 10000) / 1000) * SAMPLE_RATE);
    this.speechRms = cfg.speechRmsThreshold ?? 0.012;
    this.silenceRms = cfg.silenceRmsThreshold ?? 0.006;
    this.minSilenceSamples = Math.floor(((cfg.minSilenceMs ?? 200) / 1000) * SAMPLE_RATE);
    // Change-point detection (interruption-without-silence handler).
    // Defaults tuned for the eval suite's interruption corpora:
    //   - 2000ms check interval: balance compute (~2 embeds/check) vs latency
    //     to detecting a mid-utterance change
    //   - 1500ms head/tail: long enough for stable per-speaker embeddings,
    //     short enough to leave a "middle" buffer that can absorb the
    //     transition without contaminating either head or tail
    //   - 0.45 threshold: same as newSpeakerThreshold; below this the two
    //     halves are the same speaker continuing
    this.changePointCheckIntervalSamples = Math.floor(((cfg.changePointCheckIntervalMs ?? 2000) / 1000) * SAMPLE_RATE);
    this.changePointHeadTailSamples = Math.floor(((cfg.changePointHeadTailMs ?? 1500) / 1000) * SAMPLE_RATE);
    this.changePointDistThreshold = cfg.changePointDistThreshold ?? 0.45;
  }

  static async create(cfg: OnnxLocalDiarizerConfig = {}): Promise<OnnxLocalDiarizer> {
    transformersEnv.allowLocalModels = true;
    transformersEnv.allowRemoteModels = true; // first run downloads from HF; cached after

    console.log(`[onnx-diarizer] loading processor (mel-fbank) for ${MODEL_ID}...`);
    const processor = await AutoProcessor.from_pretrained(MODEL_ID);
    // fp32 model (~25 MB) — cleaner embeddings than the q8 quantized variant.
    // We pay ~18 MB more disk and ~3× inference time vs q8, but cluster
    // assignment quality improves noticeably on real meeting audio. Tunable
    // back to 'q8' for the cheaper path once accuracy is good enough.
    console.log(`[onnx-diarizer] loading model (fp32 ONNX, ~25 MB)...`);
    const model = await AutoModel.from_pretrained(MODEL_ID, { dtype: 'fp32' });
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
        this.samplesAtLastCpCheck = 0;
      }
      this.utteranceChunks.push(audio);
      this.utteranceSamples += audio.length;

      // Change-point check: every changePointCheckIntervalSamples of new audio,
      // see if the speaker shifted mid-utterance (interruption with no silence).
      // Only check once we have enough audio for two distinct head/tail windows
      // plus a middle gap to absorb the transition itself.
      if (
        this.changePointCheckIntervalSamples > 0 &&
        this.utteranceSamples - this.samplesAtLastCpCheck >= this.changePointCheckIntervalSamples &&
        this.utteranceSamples >= 2 * this.changePointHeadTailSamples + this.changePointHeadTailSamples / 2
      ) {
        this.samplesAtLastCpCheck = this.utteranceSamples;
        await this.checkAndSplitChangePoint();
      }

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

  /** Extract a contiguous slice of samples [startSample, endSample) from the
   *  utteranceChunks ring as a single Float32Array. */
  private extractSlice(startSample: number, endSample: number): Float32Array {
    const out = new Float32Array(Math.max(0, endSample - startSample));
    let dstOffset = 0;
    let walked = 0;
    for (const chunk of this.utteranceChunks) {
      const chunkStart = walked;
      const chunkEnd = walked + chunk.length;
      const sliceFrom = Math.max(startSample, chunkStart);
      const sliceTo = Math.min(endSample, chunkEnd);
      if (sliceFrom < sliceTo) {
        const localFrom = sliceFrom - chunkStart;
        const localTo = sliceTo - chunkStart;
        out.set(chunk.subarray(localFrom, localTo), dstOffset);
        dstOffset += sliceTo - sliceFrom;
      }
      walked = chunkEnd;
      if (walked >= endSample) break;
    }
    return out;
  }

  /** Run a change-point check on the current accumulated buffer.
   *  If the embeddings of the first `changePointHeadTailSamples` (head) and
   *  the last `changePointHeadTailSamples` (tail) are > `changePointDistThreshold`
   *  apart, the buffer contains a speaker change. Split it: commit head+middle
   *  as one utterance, restart accumulation with the tail. */
  private async checkAndSplitChangePoint(): Promise<void> {
    const totalSamples = this.utteranceSamples;
    const headWin = this.changePointHeadTailSamples;
    if (totalSamples < 2 * headWin) return;

    const headSlice = this.extractSlice(0, headWin);
    const tailSlice = this.extractSlice(totalSamples - headWin, totalSamples);

    let headEmb: Float32Array | null = null;
    let tailEmb: Float32Array | null = null;
    try {
      headEmb = await this.embedUtterance(headSlice);
      tailEmb = await this.embedUtterance(tailSlice);
    } catch (err: any) {
      console.error(`[onnx-diarizer] change-point embed failed: ${err.message}`);
      return;
    }
    if (!headEmb || !tailEmb) return;

    let dot = 0;
    for (let i = 0; i < headEmb.length; i++) dot += headEmb[i] * tailEmb[i];
    const dist = 1 - dot;

    if (dist < this.changePointDistThreshold) {
      // Same speaker continuing — no split.
      return;
    }

    // Change point detected. Split such that:
    //   - utterance N = samples [0, totalSamples - headWin)  ← head + middle
    //   - utterance N+1 starts with tail = samples [totalSamples - headWin, totalSamples)
    const splitSample = totalSamples - headWin;
    const firstPartChunks = this.chunksUpTo(splitSample);
    const tailChunks = this.chunksFrom(splitSample);
    const tailStartMs = (this.utteranceStartTs ?? 0) + Math.round((splitSample / SAMPLE_RATE) * 1000);

    console.log(
      `[onnx-diarizer] change-point detected at ${(splitSample / SAMPLE_RATE).toFixed(2)}s ` +
        `(head/tail dist=${dist.toFixed(3)} > ${this.changePointDistThreshold}); splitting utterance`,
    );

    // Commit utterance N (head+middle). Temporarily swap state into the first part.
    const savedFullChunks = this.utteranceChunks;
    const savedFullSamples = this.utteranceSamples;
    const savedFullStartTs = this.utteranceStartTs;

    this.utteranceChunks = firstPartChunks;
    this.utteranceSamples = firstPartChunks.reduce((s, c) => s + c.length, 0);
    // utteranceStartTs unchanged for the first part
    await this.commitUtterance();

    // Restart accumulation with the tail as utterance N+1.
    this.utteranceChunks = tailChunks;
    this.utteranceSamples = tailChunks.reduce((s, c) => s + c.length, 0);
    this.utteranceStartTs = tailStartMs;
    this.samplesAtLastCpCheck = this.utteranceSamples;
  }

  /** Return chunks covering samples [0, splitSample). Splits at-most one chunk. */
  private chunksUpTo(splitSample: number): Float32Array[] {
    const out: Float32Array[] = [];
    let walked = 0;
    for (const chunk of this.utteranceChunks) {
      if (walked >= splitSample) break;
      const chunkEnd = walked + chunk.length;
      if (chunkEnd <= splitSample) {
        out.push(chunk);
      } else {
        out.push(chunk.subarray(0, splitSample - walked));
      }
      walked = chunkEnd;
    }
    return out;
  }

  /** Return chunks covering samples [splitSample, totalSamples). */
  private chunksFrom(splitSample: number): Float32Array[] {
    const out: Float32Array[] = [];
    let walked = 0;
    for (const chunk of this.utteranceChunks) {
      const chunkEnd = walked + chunk.length;
      if (chunkEnd <= splitSample) {
        walked = chunkEnd;
        continue;
      }
      if (walked >= splitSample) {
        out.push(chunk);
      } else {
        out.push(chunk.subarray(splitSample - walked));
      }
      walked = chunkEnd;
    }
    return out;
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
    const utteranceStartMs = this.utteranceStartTs ?? 0;
    const utteranceEndMs = utteranceStartMs + Math.round((combined.length / SAMPLE_RATE) * 1000);
    this.utteranceChunks = [];
    this.utteranceSamples = 0;
    const utteranceTs = this.utteranceStartTs;
    this.utteranceStartTs = null;

    // Seed rule: only utterances ≥ minSeedUtteranceMs are allowed to *seed*
    // a brand-new centroid. Shorter utterances can be matched against
    // existing centroids but can't allocate new ones — protects the centroid
    // pool from being contaminated by intros, breaths, music, brief noise.
    const canSeedNew = combined.length >= this.minSeedUtteranceSamples;

    try {
      const emb = await this.embedUtterance(combined);
      if (!emb) return;

      // Turn-distance diagnostic: how far is this utterance from the LAST
      // committed utterance's embedding? Big distance ≈ new speaker turn,
      // small distance ≈ continuation of the same voice. This signal is
      // independent of cluster assignment and tells us whether the
      // conversation is actually changing voices — even when the clusterer
      // assigns both to the same speaker_N. Useful for tuning threshold.
      let turnDist = NaN;
      if (this.lastUtteranceEmb) {
        let dot = 0;
        for (let i = 0; i < emb.length; i++) dot += emb[i] * this.lastUtteranceEmb[i];
        turnDist = 1 - dot;
      }
      this.lastUtteranceEmb = emb;

      const assignment = this.clustering.assignWithSeedGate(emb, canSeedNew);
      // Post-assignment: try to merge close clusters. If a brand-new cluster
      // was just allocated but it's actually within mergeThreshold of an
      // existing cluster, merge happens transparently here.
      const merges = this.clustering.mergeClose(0.20);
      for (const [oldId, keptId] of merges) {
        // Compose with existing rewrites: if keptId is itself remapped, walk through.
        let target = keptId;
        while (this.labelRewrites.has(target)) target = this.labelRewrites.get(target)!;
        this.labelRewrites.set(oldId, target);
      }
      // Resolve final speaker id through the rewrite chain
      let finalSpeakerId = assignment.speakerId;
      while (this.labelRewrites.has(finalSpeakerId)) {
        finalSpeakerId = this.labelRewrites.get(finalSpeakerId)!;
      }
      this.lastLabel = {
        speakerId: finalSpeakerId,
        speakerName: finalSpeakerId,
      };
      console.log(
        `[onnx-diarizer] commit utterance ` +
          `dur=${(combined.length / SAMPLE_RATE).toFixed(2)}s ` +
          `→ ${assignment.speakerId} ` +
          `(centroid_dist=${assignment.distance.toFixed(3)}, ` +
          `turn_dist=${Number.isNaN(turnDist) ? '---' : turnDist.toFixed(3)}, ` +
          `new=${assignment.isNew}, total=${this.clustering.size()}, ` +
          `seed_allowed=${canSeedNew})`,
      );
      this.onCommit?.({
        speakerId: finalSpeakerId,
        tStartMs: utteranceStartMs,
        tEndMs: utteranceEndMs,
        centroidDist: assignment.distance,
        turnDist,
        isNew: assignment.isNew && finalSpeakerId === assignment.speakerId,
        dbSize: this.clustering.size(),
        seedAllowed: canSeedNew,
      });
    } catch (err: any) {
      console.error(`[onnx-diarizer] embed/cluster failed: ${err.message}`);
    }
  }

  reset(): void {
    this.utteranceChunks = [];
    this.utteranceSamples = 0;
    this.utteranceStartTs = null;
    this.samplesAtLastCpCheck = 0;
    this.inSpeech = false;
    this.silenceSampleAccumulator = 0;
    this.lastLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };
    this.lastUtteranceEmb = null;
    this.labelRewrites.clear();
    this.clustering.reset();
  }

  /** Returns the transitive label-rewrite map accumulated over the session.
   *  Consumers (eval pipeline, dashboard) use this to fix up previously
   *  emitted commits when later evidence proves two clusters were the same
   *  speaker. Resolves transitive rewrites: A→B then B→C → looking up A returns C. */
  getLabelRewrites(): Map<string, string> {
    return new Map(this.labelRewrites);
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
