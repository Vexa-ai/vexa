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
import { metrics } from './metrics';
import { PyannoteSegmenter, type BoundaryEvent } from './pyannote-segmenter';

// __filename / __dirname dropped vs the RnD pack original: bot-core is CJS
// (tsconfig: module=CommonJS) so those identifiers are already global; the
// import.meta.url-based reconstruction in the RnD source was only there
// for the harness's ESM context.

const MODEL_ID = 'onnx-community/wespeaker-voxceleb-resnet34-LM';

export interface OnnxLocalDiarizerConfig {
  /** Cosine distance threshold for online clustering. Default 0.50.
   *  Lower → more conservative (don't split same voice); higher → more
   *  aggressive splitting. Tuned for fp32 wespeaker-resnet34-LM on tab audio. */
  newSpeakerThreshold?: number;
  /** "Clearly different voice" override for short utterances that can't pass
   *  the seed gate. See OnlineSpeakerClusteringConfig.veryFarThreshold. */
  veryFarThreshold?: number;
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
  /** Periodic peek-based labeling cadence. Every `peekIntervalMs` of new
   *  accumulated speech, embed the last `peekWindowMs` and read-only peek
   *  the nearest cluster. If a confident match exists (cosine distance
   *  below `peekConfidenceThreshold`), update lastLabel immediately —
   *  giving sub-second live-label latency independent of the change-point
   *  cadence. Set interval to 0 to disable. */
  peekIntervalMs?: number;
  peekWindowMs?: number;
  peekConfidenceThreshold?: number;
  /** Cooldown after a new cluster is allocated: no further new clusters can
   *  be allocated until this much audio time has elapsed. Suppresses the
   *  "chaotic transition → 4 new clusters in 4 seconds" pattern observed
   *  on live YouTube where overlap/audio-glitches produce a short stretch
   *  of unreliable embeddings. Default 4000 ms; set to 0 to disable. */
  newClusterCooldownMs?: number;
  /** When TRUE, segmentation is driven by onnx-community/pyannote-segmentation-3.0
   *  (per-frame multi-speaker logits on a 10s rolling buffer) instead of
   *  the wespeaker head/tail change-point detector. Pyannote is
   *  architecturally correct for online speaker-change detection: it
   *  emits per-frame multi-speaker probabilities including explicit
   *  overlap classes, so the "interruption with no silence gap" case is
   *  detected at frame resolution (~13 ms) rather than at the
   *  "now − tail_length" anchor of the cosine-distance approach.
   *  Default true. Set false to fall back to the legacy head/tail
   *  detector. */
  usePyannoteSegmentation?: boolean;
  /** Pyannote inference cadence — how often to run the model on the
   *  rolling buffer. Default 500 ms. Lower = lower boundary-emission
   *  latency, more CPU. */
  pyannoteInferIntervalMs?: number;
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
  /** RnD capture hook (pack-msteams-diarization-cutover #394): fired in
   *  commitUtterance right after the wespeaker embedding is computed, BEFORE
   *  clustering assignment. Lets the offline eval cache the expensive
   *  embeddings + per-utterance gating inputs once, then replay clustering
   *  sweeps in milliseconds. Unused in production (callback unset). */
  onUtteranceEmbed?: (rec: {
    tStartMs: number;
    tEndMs: number;
    durSamples: number;
    canSeedNew: boolean;
    emb: number[];
  }) => void;
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
  private readonly peekIntervalSamples: number;
  private readonly peekWindowSamples: number;
  private readonly peekConfidenceThreshold: number;
  private samplesAtLastPeek = 0;
  private readonly newClusterCooldownMs: number;
  /** Pyannote segmenter — when set, drives mid-utterance splits. */
  private pyannoteSegmenter: PyannoteSegmenter | null = null;
  /** Boundary events queued by the pyannote segmenter's async callback,
   *  consumed during process() to decide whether to split the current
   *  in-progress utterance. */
  private pendingPyannoteBoundaries: BoundaryEvent[] = [];
  /** Wall-clock-style timestamp of the last new-cluster allocation, in the
   *  same timebase as utteranceStartTs. Initialized to -Infinity so the
   *  first allocation isn't blocked. */
  private lastNewClusterTs = -Infinity;

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
  /** Stored commit history for post-hoc refinement. Every commit pushes
   *  its embedding here; after each new commit, we re-evaluate past
   *  commits against the current centroids and update assignedId if the
   *  nearest cluster has changed meaningfully. Bounded to avoid unbounded
   *  growth on long sessions (oldest committed commits drop). */
  private commitHistory: Array<{ tStartMs: number; tEndMs: number; emb: Float32Array; assignedId: string }> = [];
  private static readonly COMMIT_HISTORY_LIMIT = 256;
  /** Per-commit rewrites discovered by post-hoc refinement. Keyed by
   *  composite `${tStartMs}-${tEndMs}` since CommitEvents don't carry a
   *  separate ID. Read by harness/eval through getCommitRewrites(). */
  private commitRewrites = new Map<string, string>();

  private readonly onCommit?: (ev: CommitEvent) => void;
  private readonly onUtteranceEmbed?: OnnxLocalDiarizerConfig['onUtteranceEmbed'];

  private constructor(
    model: PreTrainedModel,
    processor: Processor,
    cfg: OnnxLocalDiarizerConfig,
  ) {
    this.model = model;
    this.processor = processor;
    this.onCommit = cfg.onCommit;
    this.onUtteranceEmbed = cfg.onUtteranceEmbed;
    this.clustering = new OnlineSpeakerClustering({
      // 0.45 with the second-nearest-gap rule. Single threshold alone
      // can't separate "mixed-voice noise" (~0.55, multiple speakers
      // overlapping) from "same-gender new speaker" (~0.55 too). The
      // gap-rule resolves it: new-cluster allocation requires either
      // (a) clear gap between nearest and second-nearest centroids,
      // meaning the embedding is distinctively close to nothing, OR
      // (b) very-far-from-everything (≥ veryFarThreshold).
      newSpeakerThreshold: cfg.newSpeakerThreshold ?? 0.45,
      veryFarThreshold: cfg.veryFarThreshold,
      maxSpeakers: cfg.maxSpeakers,
    });
    // 500ms. Tried 300ms: too noisy — every brief speech burst got embedded,
    // producing flapping labels and (despite cooldown) too many short-window
    // clusters. The visible responsiveness win comes from the periodic
    // peek at 250 ms, which is read-only and doesn't allocate clusters.
    this.minUtteranceSamples = Math.floor(((cfg.minUtteranceMs ?? 300) / 1000) * SAMPLE_RATE);
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
    this.minSilenceSamples = Math.floor(((cfg.minSilenceMs ?? 100) / 1000) * SAMPLE_RATE);
    // Change-point detection (interruption-without-silence handler).
    // Defaults tuned for the eval suite's interruption corpora:
    //   - 2000ms check interval: balance compute (~2 embeds/check) vs latency
    //     to detecting a mid-utterance change
    //   - 1500ms head/tail: long enough for stable per-speaker embeddings,
    //     short enough to leave a "middle" buffer that can absorb the
    //     transition without contaminating either head or tail
    //   - 0.45 threshold: same as newSpeakerThreshold; below this the two
    //     halves are the same speaker continuing
    this.changePointCheckIntervalSamples = Math.floor(((cfg.changePointCheckIntervalMs ?? 1000) / 1000) * SAMPLE_RATE);
    this.changePointHeadTailSamples = Math.floor(((cfg.changePointHeadTailMs ?? 1500) / 1000) * SAMPLE_RATE);
    this.changePointDistThreshold = cfg.changePointDistThreshold ?? 0.40;
    // Periodic peek defaults: 250 ms cadence, 750 ms window, 0.40 threshold.
    // Tighter than the previous 500/1000 — label refresh visibly lagged on
    // YouTube when peek fired only twice a second. 250 ms gives 4 label
    // updates per second; 750 ms window is still enough for stable nearest-
    // cluster lookup. 0.40 is stricter than newSpeakerThreshold to avoid
    // flapping the label on borderline embeddings.
    this.peekIntervalSamples = Math.floor(((cfg.peekIntervalMs ?? 250) / 1000) * SAMPLE_RATE);
    this.peekWindowSamples = Math.floor(((cfg.peekWindowMs ?? 750) / 1000) * SAMPLE_RATE);
    this.peekConfidenceThreshold = cfg.peekConfidenceThreshold ?? 0.40;
    this.newClusterCooldownMs = cfg.newClusterCooldownMs ?? 4000;
  }

  /** pack-msteams-diarization-cutover (#394) hot-tune support: mutate
   *  the runtime config fields without rebuilding the diarizer. Only
   *  the scalar tunables are exposed — model/processor/clustering
   *  topology stays put. Pass `{maxUtteranceMs: 2500}` etc.; unset
   *  fields stay unchanged. Returns the resolved current values for
   *  log readback. */
  updateConfig(partial: Partial<OnnxLocalDiarizerConfig>): Record<string, number> {
    if (partial.maxUtteranceMs != null) {
      this.maxUtteranceSamples = Math.floor((partial.maxUtteranceMs / 1000) * SAMPLE_RATE);
    }
    if (partial.minUtteranceMs != null) {
      this.minUtteranceSamples = Math.floor((partial.minUtteranceMs / 1000) * SAMPLE_RATE);
    }
    if (partial.changePointDistThreshold != null) {
      this.changePointDistThreshold = partial.changePointDistThreshold;
    }
    if (partial.peekConfidenceThreshold != null) {
      this.peekConfidenceThreshold = partial.peekConfidenceThreshold;
    }
    if (partial.newClusterCooldownMs != null) {
      this.newClusterCooldownMs = partial.newClusterCooldownMs;
    }
    if (partial.veryFarThreshold != null) {
      (this.clustering as any).veryFarThreshold = partial.veryFarThreshold;
    }
    if (partial.newSpeakerThreshold != null) {
      (this.clustering as any).newSpeakerThreshold = partial.newSpeakerThreshold;
    }
    return {
      maxUtteranceMs: (this.maxUtteranceSamples / SAMPLE_RATE) * 1000,
      minUtteranceMs: (this.minUtteranceSamples / SAMPLE_RATE) * 1000,
      changePointDistThreshold: this.changePointDistThreshold,
      peekConfidenceThreshold: this.peekConfidenceThreshold,
      newClusterCooldownMs: this.newClusterCooldownMs,
      veryFarThreshold: (this.clustering as any).veryFarThreshold,
      newSpeakerThreshold: (this.clustering as any).newSpeakerThreshold,
    };
  }

  static async create(cfg: OnnxLocalDiarizerConfig = {}): Promise<OnnxLocalDiarizer> {
    transformersEnv.allowLocalModels = true;
    transformersEnv.allowRemoteModels = true; // first run downloads from HF; cached after

    console.log(`[onnx-diarizer] loading processor (mel-fbank) for ${MODEL_ID}...`);
    const processor = await AutoProcessor.from_pretrained(MODEL_ID);
    console.log(`[onnx-diarizer] loading model (fp32 ONNX, ~25 MB)...`);
    const model = await AutoModel.from_pretrained(MODEL_ID, { dtype: 'fp32' });
    console.log(`[onnx-diarizer] wespeaker model ready (used for embedding + clustering)`);
    const inst = new OnnxLocalDiarizer(model, processor, cfg);
    // Pyannote/segmentation-3.0 is the segmentation source by default.
    // It runs in parallel to the wespeaker pipeline: pyannote emits
    // mid-utterance boundary events; wespeaker continues to provide
    // per-segment embeddings + online clustering.
    if (cfg.usePyannoteSegmentation !== false) {
      console.log(`[onnx-diarizer] loading pyannote/segmentation-3.0 for boundary detection...`);
      inst.pyannoteSegmenter = await PyannoteSegmenter.create({
        inferIntervalMs: cfg.pyannoteInferIntervalMs ?? 500,
        onBoundary: (ev) => inst.pendingPyannoteBoundaries.push(ev),
      });
      console.log(`[onnx-diarizer] pyannote ready — segmentation driven by per-frame logits`);
    }
    return inst;
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
    const t0 = Date.now();
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
    const norm = l2Normalize(emb);
    metrics.recordEmbedLatency(Date.now() - t0);
    return norm;
  }

  async process(audio: Float32Array, timestampMs: number): Promise<DiarizerLabel> {
    const wasInSpeech = this.inSpeech;
    const isInSpeech = this.updateVadAndAccumulate(audio);

    // Feed the pyannote segmenter (if enabled). The segmenter's
    // onBoundary callback (set at construction) pushes BoundaryEvents
    // into `pendingPyannoteBoundaries`; we drain that queue below to
    // decide whether to split the in-progress utterance. The segmenter
    // sees ALL audio (not just current-utterance) because pyannote
    // benefits from 10s of cross-silence context.
    if (this.pyannoteSegmenter) {
      // Fire-and-forget the append. The inference itself awaits inside
      // the segmenter, but we don't gate diarizer processing on it.
      await this.pyannoteSegmenter.appendFrame(audio, timestampMs);
    }
    // Drain pending boundaries. Pyannote boundaries that land INSIDE the
    // current in-progress utterance are committed as splits.
    await this.processPendingPyannoteBoundaries(timestampMs);

    if (isInSpeech) {
      if (!wasInSpeech) {
        // Speech start
        this.utteranceStartTs = timestampMs;
        this.utteranceChunks = [];
        this.utteranceSamples = 0;
        this.samplesAtLastCpCheck = 0;
        this.samplesAtLastPeek = 0;
      }
      this.utteranceChunks.push(audio);
      this.utteranceSamples += audio.length;

      // pack-msteams-diarization-cutover (#394): run wespeaker head/tail
      // change-point check ALONGSIDE pyannote. On live MS Teams audio,
      // pyannote's speaker→speaker transitions don't fire reliably
      // (recordings played back-to-back often classify as the same
      // pyannote class even though wespeaker embeddings are very
      // different). Both detectors run independently; if pyannote
      // catches the boundary first, it splits; if not, the wespeaker
      // head/tail check (every 1s, threshold 0.40) catches it ~1s
      // later. splitUtteranceAtSample is idempotent — only the first
      // split per utterance commits.
      if (
        this.changePointCheckIntervalSamples > 0 &&
        this.utteranceSamples - this.samplesAtLastCpCheck >= this.changePointCheckIntervalSamples &&
        this.utteranceSamples >= 2 * this.changePointHeadTailSamples + this.changePointHeadTailSamples / 2
      ) {
        this.samplesAtLastCpCheck = this.utteranceSamples;
        await this.checkAndSplitChangePoint();
      }

      // Periodic peek-based label refresh: cheaper than change-point detection
      // (only ONE embed call) and fires more often (every 500 ms). Read-only —
      // no centroid update, no commit, just lastLabel refresh against the
      // existing centroid set. Gives the live dashboard sub-second label
      // updates without waiting for the next commit or change-point split.
      if (
        this.peekIntervalSamples > 0 &&
        this.utteranceSamples - this.samplesAtLastPeek >= this.peekIntervalSamples &&
        this.utteranceSamples >= this.peekWindowSamples &&
        this.clustering.size() > 0
      ) {
        this.samplesAtLastPeek = this.utteranceSamples;
        await this.periodicPeek();
      }

      // Cap-and-commit on long single-speaker monologue so we keep emitting
      // updated labels and don't accumulate unbounded audio.
      if (this.utteranceSamples >= this.maxUtteranceSamples) {
        await this.commitUtterance();
        // pack-msteams-diarization-cutover (#394): speech continues after a
        // mid-speech cap-commit, but commitUtterance() reset utteranceStartTs
        // to null and the `!wasInSpeech` speech-start block won't re-run
        // (we're still in speech). Without re-seeding the start, the NEXT
        // cap-commit fell back to `utteranceStartMs = utteranceStartTs ?? 0`
        // → tStart=0, stacking every subsequent monologue commit at the
        // timeline origin. Re-seed to the current frame so timestamps stay
        // in the fed-audio timebase. (Insulated in prod because the FIFO
        // drain keys off duration, not absolute time — but it corrupts any
        // absolute-time consumer, including the offline eval's GT alignment.)
        this.utteranceStartTs = timestampMs;
      }
    } else if (wasInSpeech && !isInSpeech) {
      // Speech end — commit the just-finished utterance.
      await this.commitUtterance();
    }

    return this.lastLabel;
  }

  /** Periodic peek: embed the last peekWindowSamples of buffered audio and
   *  read-only look up the nearest cluster. If confidence is high, update
   *  lastLabel. Cheap (1 embed call); fires every peekIntervalSamples. */
  private async periodicPeek(): Promise<void> {
    const totalSamples = this.utteranceSamples;
    const win = this.peekWindowSamples;
    if (totalSamples < win) return;
    const slice = this.extractSlice(totalSamples - win, totalSamples);
    let emb: Float32Array | null = null;
    try {
      emb = await this.embedUtterance(slice);
    } catch {
      return;
    }
    if (!emb) return;
    const peek = this.clustering.peek(emb);
    if (!peek) return;
    if (peek.distance < this.peekConfidenceThreshold) {
      // Resolve through the rewrite chain so we never show a stale merged label.
      let target = peek.speakerId;
      while (this.labelRewrites.has(target)) target = this.labelRewrites.get(target)!;
      if (this.lastLabel.speakerId !== target) {
        this.lastLabel = { speakerId: target, speakerName: target };
        // Quiet log — fires every 500 ms during speech, don't spam unless label changed.
        console.log(
          `[onnx-diarizer] peek refresh → ${target} (dist=${peek.distance.toFixed(3)})`,
        );
        metrics.recordPeekRefresh();
      }
    }
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

  /** Process any pyannote boundary events that landed during this frame's
   *  appendFrame call. For each event whose timestamp falls INSIDE the
   *  current in-progress utterance, split the utterance at the boundary
   *  (commit head, restart with tail). Events outside the current
   *  utterance (e.g. before utterance start, or in audio that already
   *  committed) are discarded.
   *
   *  The boundary timestamp is in the same wall-clock-style timebase
   *  passed to process(). We map it to a sample offset within the
   *  in-progress utterance via: samplesIntoUtterance = (boundaryMs -
   *  utteranceStartMs) * SAMPLE_RATE / 1000. */
  private async processPendingPyannoteBoundaries(nowMs: number): Promise<void> {
    if (this.pendingPyannoteBoundaries.length === 0) return;
    const events = this.pendingPyannoteBoundaries.splice(0, this.pendingPyannoteBoundaries.length);
    // Only handle events that fall INSIDE the in-progress utterance,
    // i.e. utteranceStartTs < ev.tMs < nowMs. Outside-utterance events
    // are speaker changes that already crossed an utterance boundary
    // (typically a clean VAD silence-end) and don't need a mid-utterance
    // split.
    if (this.utteranceStartTs == null || this.utteranceSamples === 0) return;
    for (const ev of events) {
      const utteranceStartMs = this.utteranceStartTs;
      const samplesIntoUtterance = Math.floor(((ev.tMs - utteranceStartMs) / 1000) * SAMPLE_RATE);
      // pack-msteams-diarization-cutover (#394) — Fix 1: honour
      // SKIP_TOO_LATE/EARLY boundaries instead of dropping them. The
      // old `continue` left the after-boundary tail glued to the
      // before-boundary cluster (the "Can you explain it? Sure." case
      // where a 1s interjection got attributed to the surrounding
      // speaker). Now we clamp the split sample to the legal
      // [minUtteranceSamples, utteranceSamples - minUtteranceSamples]
      // window — the clamp introduces a few-frames misalignment vs
      // the true boundary but preserves the cluster break, which is
      // what matters for downstream wespeaker assignment. When the
      // boundary is far from the legal window (>500ms), still skip
      // (very-near-edge boundaries are likely smoothing artefacts).
      const minSplit = this.minUtteranceSamples;
      const maxSplit = this.utteranceSamples - this.minUtteranceSamples;
      let splitSample = samplesIntoUtterance;
      const drift500ms = Math.floor(0.5 * SAMPLE_RATE);
      if (splitSample < minSplit) {
        if (minSplit - splitSample > drift500ms) continue;
        splitSample = minSplit;
      } else if (splitSample > maxSplit) {
        if (splitSample - maxSplit > drift500ms) continue;
        splitSample = maxSplit;
      }
      console.log(
        `[onnx-diarizer] pyannote boundary at ${(ev.tMs / 1000).toFixed(2)}s ` +
          `(${ev.kind}, conf=${ev.confidence.toFixed(3)}) ` +
          `→ splitting utterance at ${(splitSample / SAMPLE_RATE).toFixed(2)}s in` +
          (splitSample !== samplesIntoUtterance
            ? ` (clamped from ${(samplesIntoUtterance / SAMPLE_RATE).toFixed(2)}s)`
            : ''),
      );
      await this.splitUtteranceAtSample(splitSample);
      metrics.recordChangePoint();
      // After splitting, the remaining utterance is the tail; subsequent
      // boundary events from this frame might still apply, but their
      // timestamps would no longer make sense in the new utterance
      // timebase. Break and let the next process() round handle them.
      break;
    }
  }

  /** Split the in-progress utterance at the given sample offset. Commits
   *  the head as one utterance, leaves the tail in place as the start of
   *  the next utterance. Shared helper with the legacy head/tail
   *  change-point detector. */
  private async splitUtteranceAtSample(splitSample: number): Promise<void> {
    const utteranceStartMs = this.utteranceStartTs ?? 0;
    const firstPartChunks = this.chunksUpTo(splitSample);
    const tailChunks = this.chunksFrom(splitSample);
    const tailStartMs = utteranceStartMs + Math.round((splitSample / SAMPLE_RATE) * 1000);
    this.utteranceChunks = firstPartChunks;
    this.utteranceSamples = firstPartChunks.reduce((s, c) => s + c.length, 0);
    await this.commitUtterance();
    this.utteranceChunks = tailChunks;
    this.utteranceSamples = tailChunks.reduce((s, c) => s + c.length, 0);
    this.utteranceStartTs = tailStartMs;
    this.samplesAtLastCpCheck = this.utteranceSamples;
    this.samplesAtLastPeek = this.utteranceSamples;
  }

  /** Run a change-point check on the current accumulated buffer.
   *  If the embeddings of the first `changePointHeadTailSamples` (head) and
   *  the last `changePointHeadTailSamples` (tail) are > `changePointDistThreshold`
   *  apart, the buffer contains a speaker change. Split it: commit head+middle
   *  as one utterance, restart accumulation with the tail.
   *
   *  This is the LEGACY wespeaker-based detector. When pyannote
   *  segmentation is enabled (`usePyannoteSegmentation`), it provides
   *  earlier + more precise boundaries; we keep this path as a fallback
   *  for the no-pyannote configuration. */
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
    metrics.recordChangePoint();

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

    // PREEMPTIVE TAIL LABEL — fixes the live-label-lag bug. After splitting,
    // the tail buffer is the NEW speaker but its full commit won't fire
    // until silence (could be many seconds). Without this peek, every
    // per-frame process() call would keep returning the head's label until
    // the tail commits — that's the lag the live dashboard shows.
    // Lookup is read-only against the existing centroid set; we update
    // lastLabel optimistically. If the tail later commits to a different
    // cluster (e.g. it's actually a brand new speaker not yet allocated),
    // the lastLabel updates again at that final commit. If the tail
    // matches no existing cluster well enough (>= newSpeakerThreshold),
    // we keep showing the head's label until the final commit allocates.
    const peek = this.clustering.peek(tailEmb);
    const headSpeakerId = this.lastLabel.speakerId;
    if (peek && peek.speakerId !== headSpeakerId && peek.distance < 0.45) {
      let target = peek.speakerId;
      while (this.labelRewrites.has(target)) target = this.labelRewrites.get(target)!;
      this.lastLabel = { speakerId: target, speakerName: target };
      console.log(
        `[onnx-diarizer] preemptive tail label = ${target} (dist=${peek.distance.toFixed(3)}); ` +
          `lastLabel updated without waiting for tail commit`,
      );
    }
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

      // RnD capture (pack #394): dump the embedding + gating inputs so the
      // offline eval can replay clustering sweeps without re-running the
      // expensive pyannote+wespeaker stages. Fires before assignment.
      if (this.onUtteranceEmbed) {
        this.onUtteranceEmbed({
          tStartMs: utteranceStartMs,
          tEndMs: utteranceEndMs,
          durSamples: combined.length,
          canSeedNew,
          emb: Array.from(emb),
        });
      }

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

      // Cooldown gate: don't allocate a new cluster within
      // newClusterCooldownMs of the previous one. This is a temporal
      // rate-limit that suppresses the "chaotic transition → 4 spurious
      // clusters in 4 seconds" pattern. The utterance still gets matched
      // to the nearest existing cluster — we just don't mint a new ID for
      // it. Real new speakers settle in within a few seconds and their
      // next utterance (after cooldown) will properly seed.
      const utteranceEndTsForCooldown = utteranceTs! + Math.round((combined.length / SAMPLE_RATE) * 1000);
      const allowNewCluster = (utteranceEndTsForCooldown - this.lastNewClusterTs) >= this.newClusterCooldownMs;
      // Stickiness hook (bias matching toward the previous commit's cluster
      // when distances are close). Currently disabled (bias=0) — empirical
      // suite sweeps showed no positive effect because the previous
      // commit's cluster is wrong about half the time during heavy
      // interleaving. The API is kept for future tuning against richer
      // corpora that exhibit longer same-speaker continuations.
      const stickyHint = this.lastLabel?.speakerId ?? null;
      const assignment = this.clustering.assignWithSeedGate(emb, canSeedNew, allowNewCluster, stickyHint, 0);
      if (assignment.isNew) {
        this.lastNewClusterTs = utteranceEndTsForCooldown;
      }
      // Post-assignment: try to merge close clusters. If a brand-new cluster
      // was just allocated but it's actually within mergeThreshold of an
      // existing cluster, merge happens transparently here.
      const merges = this.clustering.mergeClose(0.30);
      for (const [oldId, keptId] of merges) {
        // Compose with existing rewrites: if keptId is itself remapped, walk through.
        let target = keptId;
        while (this.labelRewrites.has(target)) target = this.labelRewrites.get(target)!;
        this.labelRewrites.set(oldId, target);
      }
      if (merges.size > 0) metrics.recordClusterMerge(merges.size);
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
      // Push into bounded commit history for post-hoc refinement.
      this.commitHistory.push({
        tStartMs: utteranceStartMs,
        tEndMs: utteranceEndMs,
        emb,
        assignedId: finalSpeakerId,
      });
      if (this.commitHistory.length > OnnxLocalDiarizer.COMMIT_HISTORY_LIMIT) {
        this.commitHistory.shift();
      }
      // Post-hoc refinement: now that this commit may have refined a
      // centroid (or allocated a new cluster), re-evaluate every past
      // commit. If a past commit's nearest centroid (resolved through
      // rewrites) is now a different cluster AND the new nearest dist is
      // meaningfully smaller (>= refinementDeltaMin), rewrite the
      // commit's label. Caps the search to recent history (LIMIT above).
      this.refineCommitHistory();
      // Label-emit latency: the user-perceived "how long after this
      // utterance started did we publish a speaker label for it?". Measured
      // as commit-time minus utterance-start in audio-frame timebase. With
      // deferred routing this also equals the per-frame routing latency.
      metrics.recordCommit({
        speakerId: finalSpeakerId,
        durMs: utteranceEndMs - utteranceStartMs,
        centroidDist: assignment.distance,
        turnDist,
        isNew: assignment.isNew && finalSpeakerId === assignment.speakerId,
        clusterCount: this.clustering.size(),
        labelEmitLatencyMs: utteranceEndMs - utteranceStartMs,
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
    this.samplesAtLastPeek = 0;
    this.inSpeech = false;
    this.silenceSampleAccumulator = 0;
    this.lastLabel = { speakerId: 'speaker_0', speakerName: 'speaker_0' };
    this.lastUtteranceEmb = null;
    this.lastNewClusterTs = -Infinity;
    this.labelRewrites.clear();
    this.commitHistory.length = 0;
    this.commitRewrites.clear();
    this.clustering.reset();
    this.pyannoteSegmenter?.reset();
    this.pendingPyannoteBoundaries.length = 0;
  }

  /** Returns the transitive label-rewrite map accumulated over the session.
   *  Consumers (eval pipeline, dashboard) use this to fix up previously
   *  emitted commits when later evidence proves two clusters were the same
   *  speaker. Resolves transitive rewrites: A→B then B→C → looking up A returns C. */
  getLabelRewrites(): Map<string, string> {
    return new Map(this.labelRewrites);
  }

  /** Per-commit rewrites discovered by post-hoc refinement, keyed by
   *  `${tStartMs}-${tEndMs}`. Distinct from getLabelRewrites which maps
   *  clusters → clusters; this maps individual commits to (possibly
   *  different) cluster IDs after re-evaluation against later-stabilized
   *  centroids. The eval pipeline applies this to each commit during
   *  alignment so misrouted-then-refined commits get the corrected label. */
  getCommitRewrites(): Map<string, string> {
    return new Map(this.commitRewrites);
  }

  private commitKey(tStartMs: number, tEndMs: number): string {
    return `${tStartMs}-${tEndMs}`;
  }

  /** Re-evaluate every commit in history against the current centroid set,
   *  then iteratively recompute refinement centroids from the new
   *  assignments and re-evaluate again until convergence (k-means style).
   *
   *  Distinct from the clusterer's live EMA centroids: the live ones serve
   *  online streaming and are only nudged on confident matches
   *  (nearestTrueDist<0.25). The REFINEMENT centroids are a clean recompute
   *  from every committed embedding currently labeled as that cluster — they
   *  give a much sharper estimate of "what does this speaker actually sound
   *  like" once enough audio has accumulated, and we can use them to
   *  re-evaluate borderline early commits.
   *
   *  Caps iterations to avoid pathological loops. */
  private refineCommitHistory(): void {
    if (this.commitHistory.length === 0) return;
    const refinementDeltaMin = 0.05;
    const maxIters = 10;
    // Seed labels from live state (resolved through rewrites).
    const labels = this.commitHistory.map((h) => {
      let id = h.assignedId;
      while (this.labelRewrites.has(id)) id = this.labelRewrites.get(id)!;
      return id;
    });
    // Cluster IDs we'll iterate over: union of (a) live centroid IDs and
    // (b) any label currently in use. Use the live centroids' embedding
    // length as the dim.
    const liveCentroids = (this.clustering as any).centroids as Map<string, Float32Array>;
    const dim = this.commitHistory[0].emb.length;
    let prevLabels = labels.slice();
    for (let iter = 0; iter < maxIters; iter++) {
      // Recompute refinement centroids from current labels. Weight each
      // commit by its duration: long utterances are stronger signal than
      // short ones (which are noisier and more likely to contain overlap
      // or transitional audio). Embeddings ARE already unit-normalized,
      // so duration-weighted sum biases toward longer-duration commits;
      // we then renormalize the sum back to unit length.
      const sums = new Map<string, Float32Array>();
      const counts = new Map<string, number>();
      for (let i = 0; i < this.commitHistory.length; i++) {
        const lab = labels[i];
        if (!sums.has(lab)) sums.set(lab, new Float32Array(dim));
        const s = sums.get(lab)!;
        const e = this.commitHistory[i].emb;
        const h = this.commitHistory[i];
        const w = Math.max(1, h.tEndMs - h.tStartMs); // ms
        for (let j = 0; j < dim; j++) s[j] += e[j] * w;
        counts.set(lab, (counts.get(lab) ?? 0) + 1);
      }
      const refCentroids = new Map<string, Float32Array>();
      for (const [lab, s] of sums) {
        // Normalize the mean to unit length so cosine distance computes
        // correctly against the unit-normalized commit embeddings.
        let norm = 0;
        for (let j = 0; j < dim; j++) norm += s[j] * s[j];
        norm = Math.sqrt(norm);
        if (norm < 1e-8) continue;
        const c = new Float32Array(dim);
        for (let j = 0; j < dim; j++) c[j] = s[j] / norm;
        refCentroids.set(lab, c);
      }
      // Re-classify each commit against the refinement centroids. Only
      // commit a label change if (a) the new label exists in liveCentroids
      // (to avoid emitting unknown IDs) AND (b) it beats the current label
      // by at least refinementDeltaMin.
      let changed = false;
      for (let i = 0; i < this.commitHistory.length; i++) {
        const e = this.commitHistory[i].emb;
        let bestLab = labels[i];
        let bestDist = Infinity;
        let curDist = Infinity;
        for (const [lab, c] of refCentroids) {
          let dot = 0;
          for (let j = 0; j < dim; j++) dot += e[j] * c[j];
          const d = 1 - dot;
          if (d < bestDist) {
            bestDist = d;
            bestLab = lab;
          }
          if (lab === labels[i]) curDist = d;
        }
        // Flip rule: bestLab beats curLab by either an absolute delta
        // (refinementDeltaMin) OR a relative one (15% closer). The
        // relative gate catches cases where both distances are small
        // (e.g. 0.20 vs 0.22) — absolute delta wouldn't fire, but the
        // relative gain is real. The absolute gate catches cases where
        // both are large (e.g. 0.55 vs 0.45) — relative gain is small but
        // the absolute difference matters.
        const relGain = (curDist - bestDist) / Math.max(curDist, 1e-6);
        if (bestLab !== labels[i] && (bestDist + refinementDeltaMin <= curDist || relGain >= 0.15)) {
          labels[i] = bestLab;
          changed = true;
        }
      }
      if (!changed) break;
      prevLabels = labels.slice();
    }

    // Emit per-commit rewrites for any labels that differ from the
    // originally assigned (resolved) label. Only emit labels that exist
    // in the live centroid pool — refinement centroids are computed from
    // the same labels in use, so they should always match.
    for (let i = 0; i < this.commitHistory.length; i++) {
      const h = this.commitHistory[i];
      let originalResolved = h.assignedId;
      while (this.labelRewrites.has(originalResolved)) originalResolved = this.labelRewrites.get(originalResolved)!;
      if (labels[i] !== originalResolved && liveCentroids.has(labels[i])) {
        this.commitRewrites.set(this.commitKey(h.tStartMs, h.tEndMs), labels[i]);
        h.assignedId = labels[i];
      }
    }
    // Feed the converged refinement centroids back into the live clusterer.
    // The refinement centroids are a strictly better estimate of each
    // speaker's voice (recomputed from every assigned commit, not just
    // EMA-nudged on confident matches) — using them for the next commit's
    // nearest-cluster lookup makes future assignments sharper.
    //
    // Blend with the live centroid via a small mix factor so we don't
    // completely overwrite if refinement is wrong on one cluster:
    //   new_live = liveBlend * live + (1 - liveBlend) * refined  (then unit-normalize)
    const liveBlend = 0.5;
    const recompute = sumsForRecompute(this.commitHistory, labels, dim);
    for (const [lab, refined] of recompute) {
      const live = liveCentroids.get(lab);
      if (!live) continue;
      const mixed = new Float32Array(dim);
      for (let j = 0; j < dim; j++) mixed[j] = liveBlend * live[j] + (1 - liveBlend) * refined[j];
      let norm = 0;
      for (let j = 0; j < dim; j++) norm += mixed[j] * mixed[j];
      norm = Math.sqrt(norm);
      if (norm < 1e-8) continue;
      for (let j = 0; j < dim; j++) mixed[j] /= norm;
      liveCentroids.set(lab, mixed);
    }
    // Silence unused warning for prevLabels.
    void prevLabels;
  }
}

/** Helper: recompute refinement centroids (unit-normalized means) for each
 *  cluster from the given commit-history slice and per-commit labels. */
function sumsForRecompute(
  history: Array<{ tStartMs: number; tEndMs: number; emb: Float32Array; assignedId: string }>,
  labels: string[],
  dim: number,
): Map<string, Float32Array> {
  const sums = new Map<string, Float32Array>();
  const counts = new Map<string, number>();
  for (let i = 0; i < history.length; i++) {
    const lab = labels[i];
    if (!sums.has(lab)) sums.set(lab, new Float32Array(dim));
    const s = sums.get(lab)!;
    const e = history[i].emb;
    for (let j = 0; j < dim; j++) s[j] += e[j];
    counts.set(lab, (counts.get(lab) ?? 0) + 1);
  }
  const out = new Map<string, Float32Array>();
  for (const [lab, s] of sums) {
    let norm = 0;
    for (let j = 0; j < dim; j++) norm += s[j] * s[j];
    norm = Math.sqrt(norm);
    if (norm < 1e-8) continue;
    const c = new Float32Array(dim);
    for (let j = 0; j < dim; j++) c[j] = s[j] / norm;
    out.set(lab, c);
  }
  return out;
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
