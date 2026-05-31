/**
 * PyannoteSegmenter — streaming wrapper around
 * onnx-community/pyannote-segmentation-3.0.
 *
 * The model takes 10s of 16kHz mono audio per call and emits per-frame
 * (≈13ms) logits over a powerset of up to 3 local speakers:
 *
 *    0=∅, 1={1}, 2={2}, 3={3}, 4={1,2}, 5={1,3}, 6={2,3}
 *
 * For ONLINE boundary detection we keep a 10s ring buffer of recent audio
 * (regardless of utterance boundaries — pyannote benefits from context
 * across silences). Every `inferIntervalMs` we run inference on the
 * latest 10s and extract boundary events from the FRESHEST portion of
 * the per-frame predictions. A boundary fires when the speaker SET grows
 * (silence → {1}, {1} → {1,2} overlap-onset, {1} → {2} clean handoff).
 *
 * This module is a building block; the diarizer composes it with VAD
 * accumulation and wespeaker clustering. Architecture matches the
 * Coria 2021 / Diart pattern surfaced by the research workflow:
 *   - segmentation = per-frame multi-speaker (pyannote)
 *   - embedding    = utterance-level (wespeaker)
 *   - clustering   = online cosine-distance (our OnlineSpeakerClustering)
 */

import {
  AutoModel,
  AutoProcessor,
  type PreTrainedModel,
  type Processor,
  type Tensor,
} from '@huggingface/transformers';

const SAMPLE_RATE = 16_000;
const PYANNOTE_MODEL_ID = 'onnx-community/pyannote-segmentation-3.0';
const WINDOW_SAMPLES = 10 * SAMPLE_RATE;            // 160_000
const DEFAULT_INFER_INTERVAL_MS = 500;
/** Frames per 10s window — the model emits [1, 767, 7]. */
const EXPECTED_FRAMES_PER_WINDOW = 767;
const MS_PER_FRAME = (10 * 1000) / EXPECTED_FRAMES_PER_WINDOW; // ≈13.04
/** How long to retain detected overlap intervals (pack #394). Utterances
 *  cap at a few seconds, so 30s is ample for the diarizer to query. */
const OVERLAP_RETENTION_MS = 30_000;

/** Speaker set per powerset class. Order: silence, then single-speakers,
 *  then 2-speaker overlaps. */
const SPEAKERS_BY_CLASS: ReadonlyArray<ReadonlyArray<number>> = [
  [],         // 0: silence
  [1],        // 1: {1}
  [2],        // 2: {2}
  [3],        // 3: {3}
  [1, 2],     // 4: {1,2}
  [1, 3],     // 5: {1,3}
  [2, 3],     // 6: {2,3}
];

function gainsSpeaker(prev: ReadonlyArray<number>, cur: ReadonlyArray<number>): boolean {
  for (const s of cur) if (!prev.includes(s)) return true;
  return false;
}

/** pack-msteams-diarization-cutover (#394): emit boundary on ANY change in
 *  the active speaker set, not just "speaker added". The original
 *  `gainsSpeaker` filter only fired on silence→speaker and overlap-onset
 *  transitions; speaker_A→speaker_B with no silence gap (the common
 *  back-and-forth meeting case) was silently dropped, leaving the diarizer
 *  to wait for maxUtteranceMs and stuff both voices into one utterance.
 *  We want the split moment to match the actual speaker change so each
 *  speaker's audio routes cleanly to their own cluster buffer. */
function speakerSetChanges(prev: ReadonlyArray<number>, cur: ReadonlyArray<number>): boolean {
  if (prev.length !== cur.length) return true;
  for (const s of cur) if (!prev.includes(s)) return true;
  return false;
}

/** 3-tap median filter to suppress single-frame argmax spikes. */
function medianFilter3(arr: number[]): number[] {
  const out = new Array<number>(arr.length);
  for (let i = 0; i < arr.length; i++) {
    const a = arr[Math.max(0, i - 1)];
    const b = arr[i];
    const c = arr[Math.min(arr.length - 1, i + 1)];
    out[i] = a + b + c - Math.min(a, b, c) - Math.max(a, b, c);
  }
  return out;
}

export interface PyannoteSegmenterConfig {
  /** How often to run inference. Default 500ms. Lower = lower latency
   *  but more CPU. Forward pass is ~50ms per call on modern CPU. */
  inferIntervalMs?: number;
  /** Window of FRESH frames to scan for boundaries each inference.
   *  Looking only at the last ~1000ms means we don't re-emit boundaries
   *  that already fired in earlier inferences. Default 1200ms. */
  freshWindowMs?: number;
  /** Optional callback fired when a boundary is detected, in absolute
   *  audio time (the same timebase the caller fed via appendFrame). */
  onBoundary?: (ev: BoundaryEvent) => void;
}

export interface BoundaryEvent {
  /** Absolute audio time of the boundary, in ms. */
  tMs: number;
  /** pack #394: extended to cover all speaker-set changes, not just additions. */
  kind: 'silence→speaker' | 'speaker→speaker' | 'speaker→silence' | 'overlap-onset' | 'overlap-offset';
  /** Softmax confidence of the post-boundary frame's argmax. */
  confidence: number;
}

export class PyannoteSegmenter {
  private model!: PreTrainedModel;
  private processor!: Processor;

  // Audio ring buffer (10s).
  private ringBuffer = new Float32Array(WINDOW_SAMPLES);
  private ringWriteIdx = 0;
  /** Total samples ever fed to the ring (monotonic). */
  private totalSamplesFed = 0;
  /** Absolute audio time (ms) corresponding to ringBuffer[0]. */
  private ringBaseTsMs = 0;
  /** Counter of samples since last inference. */
  private samplesSinceLastInfer = 0;

  private readonly inferIntervalSamples: number;
  private readonly freshWindowSamples: number;
  private readonly onBoundary?: (ev: BoundaryEvent) => void;

  /** Absolute time (ms) of the most recently emitted boundary. Used to
   *  drop duplicates when overlapping inference windows re-detect the
   *  same boundary. */
  private lastEmittedBoundaryMs = -Infinity;

  /** pack-msteams-diarization-cutover (#394): rolling list of absolute
   *  time ranges [startMs, endMs] where pyannote classified 2+ speakers
   *  active simultaneously (overlap classes {1,2}/{1,3}/{2,3}). The
   *  diarizer queries this to (a) exclude overlap frames from wespeaker
   *  embeddings — they contain 2 voices and contaminate the centroid —
   *  and (b) hard-split utterances at overlap edges. Pruned to the last
   *  OVERLAP_RETENTION_MS so a long meeting doesn't grow it unbounded. */
  private overlapIntervals: Array<[number, number]> = [];

  private constructor(cfg: PyannoteSegmenterConfig) {
    this.inferIntervalSamples = Math.floor(((cfg.inferIntervalMs ?? DEFAULT_INFER_INTERVAL_MS) / 1000) * SAMPLE_RATE);
    this.freshWindowSamples = Math.floor(((cfg.freshWindowMs ?? 1200) / 1000) * SAMPLE_RATE);
    this.onBoundary = cfg.onBoundary;
  }

  static async create(cfg: PyannoteSegmenterConfig = {}): Promise<PyannoteSegmenter> {
    const inst = new PyannoteSegmenter(cfg);
    inst.model = await AutoModel.from_pretrained(PYANNOTE_MODEL_ID, { device: 'cpu' });
    inst.processor = await AutoProcessor.from_pretrained(PYANNOTE_MODEL_ID);
    return inst;
  }

  reset(): void {
    this.ringBuffer.fill(0);
    this.ringWriteIdx = 0;
    this.totalSamplesFed = 0;
    this.ringBaseTsMs = 0;
    this.samplesSinceLastInfer = 0;
    this.lastEmittedBoundaryMs = -Infinity;
    this.overlapIntervals = [];
  }

  /** Append a frame of audio to the ring buffer + advance inference cadence.
   *  Calls `onBoundary` synchronously (awaited) for each new boundary detected. */
  async appendFrame(frame: Float32Array, tsMs: number): Promise<BoundaryEvent[]> {
    // Update ring base timestamp ON the FIRST frame so the buffer's
    // absolute timebase is consistent. After that, ringBaseTsMs slides
    // forward whenever the ring wraps.
    if (this.totalSamplesFed === 0) {
      this.ringBaseTsMs = tsMs;
    } else {
      // ringBaseTsMs = ts of the OLDEST sample in the ring. If the ring
      // has wrapped, that's (current ts) - WINDOW_SAMPLES/SR*1000.
      if (this.totalSamplesFed >= WINDOW_SAMPLES) {
        this.ringBaseTsMs = tsMs - (WINDOW_SAMPLES / SAMPLE_RATE) * 1000;
      }
    }
    // Write frame into ring (linear; circular reads handled at infer time).
    for (let i = 0; i < frame.length; i++) {
      this.ringBuffer[this.ringWriteIdx] = frame[i];
      this.ringWriteIdx = (this.ringWriteIdx + 1) % WINDOW_SAMPLES;
    }
    this.totalSamplesFed += frame.length;
    this.samplesSinceLastInfer += frame.length;

    if (this.samplesSinceLastInfer < this.inferIntervalSamples) return [];
    // Need at least freshWindowSamples of audio before we can scan for
    // boundaries; less than that, pyannote's predictions in the recent
    // region are dominated by zero-pad and unreliable.
    if (this.totalSamplesFed < this.freshWindowSamples) {
      this.samplesSinceLastInfer = 0;
      return [];
    }
    this.samplesSinceLastInfer = 0;
    return await this.runInference(tsMs);
  }

  private readRingLinear(): Float32Array {
    // Read out the ring in its actual time order (oldest → newest).
    // ringWriteIdx points to the slot the NEXT write would go to, so the
    // oldest sample is at ringWriteIdx (when full) or at 0 (when not yet
    // wrapped).
    if (this.totalSamplesFed < WINDOW_SAMPLES) {
      // Not wrapped yet — first `totalSamplesFed` slots are the data,
      // pad the rest with zeros (model needs full 10s window).
      const out = new Float32Array(WINDOW_SAMPLES);
      out.set(this.ringBuffer.subarray(0, this.totalSamplesFed), 0);
      return out;
    }
    // Wrapped — re-order: [ringWriteIdx..end] ++ [0..ringWriteIdx]
    const out = new Float32Array(WINDOW_SAMPLES);
    const headLen = WINDOW_SAMPLES - this.ringWriteIdx;
    out.set(this.ringBuffer.subarray(this.ringWriteIdx, WINDOW_SAMPLES), 0);
    out.set(this.ringBuffer.subarray(0, this.ringWriteIdx), headLen);
    return out;
  }

  private async runInference(latestTsMs: number): Promise<BoundaryEvent[]> {
    const window = this.readRingLinear();
    // Absolute time of the OLDEST sample in this window:
    const windowStartMs = latestTsMs - (Math.min(this.totalSamplesFed, WINDOW_SAMPLES) / SAMPLE_RATE) * 1000;
    let logits: Tensor | null = null;
    try {
      const inputs = await this.processor(window, { sampling_rate: SAMPLE_RATE });
      const outputs = (await this.model(inputs)) as { [k: string]: Tensor };
      logits = outputs.logits ?? outputs[Object.keys(outputs)[0]];
    } catch (err: any) {
      console.error(`[pyannote-segmenter] inference failed: ${err.message}`);
      return [];
    }
    if (!logits) return [];
    const dims = logits.dims as number[];
    const numFrames = dims[1];
    const numClasses = dims[2];
    const data = logits.data as Float32Array;
    const frameClasses: number[] = new Array(numFrames);
    const frameConfidence: number[] = new Array(numFrames);
    for (let f = 0; f < numFrames; f++) {
      let best = 0;
      let bestVal = -Infinity;
      for (let c = 0; c < numClasses; c++) {
        const v = data[f * numClasses + c];
        if (v > bestVal) {
          bestVal = v;
          best = c;
        }
      }
      let sumExp = 0;
      for (let c = 0; c < numClasses; c++) sumExp += Math.exp(data[f * numClasses + c] - bestVal);
      frameClasses[f] = best;
      frameConfidence[f] = 1 / sumExp;
    }
    const smoothed = medianFilter3(frameClasses);
    const frameMs = (window.length / SAMPLE_RATE) * 1000 / numFrames; // ≈13.04
    // pack-msteams-diarization-cutover (#394): scan the ENTIRE window
    // every time, not just the last freshWindowSamples worth. The fresh-
    // only scan missed boundaries when speech started >freshWindowMs ago
    // and continued steadily. lastEmittedBoundaryMs (200ms) dedups
    // re-emits.
    const scanStart = 1;
    // pack-msteams-diarization-cutover (#394): cap scan to frames that
    // map to REAL audio. When totalSamplesFed < WINDOW_SAMPLES (early in
    // the stream, or right after a session start), the ring buffer tail
    // is zeros — pyannote correctly classifies the padding as silence
    // and reports a spurious "speaker→silence" boundary at the
    // real-audio↔padding edge whose tMs lands AFTER latestTsMs (in the
    // future of the captured audio). Those boundaries break the
    // downstream split logic in onnx-local-diarizer.ts because their
    // samplesIntoUtterance is beyond the buffered samples.
    const realFrames = Math.min(
      numFrames,
      Math.ceil((Math.min(this.totalSamplesFed, WINDOW_SAMPLES) / SAMPLE_RATE) * 1000 / frameMs),
    );
    const scanEnd = realFrames;
    const events: BoundaryEvent[] = [];
    for (let f = scanStart; f < scanEnd; f++) {
      const prev = smoothed[f - 1];
      const cur = smoothed[f];
      if (prev === cur) continue;
      if (!speakerSetChanges(SPEAKERS_BY_CLASS[prev], SPEAKERS_BY_CLASS[cur])) continue;
      const tMs = windowStartMs + f * frameMs;
      // pack-msteams-diarization-cutover (#394) Fix 8: dedup windows
      // tightened (in-batch 100→50 ms, ever-emitted 200→100 ms) so
      // rapid turn-taking like "Okay." → "Let's be honest..." can fire
      // two boundaries within the same inference window. Previous 200 ms
      // floor swallowed close-spaced events in back-and-forth segments.
      const lastInBatch = events.length > 0 ? events[events.length - 1].tMs : -Infinity;
      const lastEverEmitted = this.lastEmittedBoundaryMs;
      if (tMs - lastInBatch <= 50) continue;
      if (tMs - lastEverEmitted <= 100) continue;
      const prevSet = SPEAKERS_BY_CLASS[prev];
      const curSet = SPEAKERS_BY_CLASS[cur];
      const kind: BoundaryEvent['kind'] = prevSet.length === 0
        ? 'silence→speaker'
        : curSet.length === 0
          ? 'speaker→silence'
          : (curSet.length > prevSet.length ? 'overlap-onset'
            : (curSet.length < prevSet.length ? 'overlap-offset' : 'speaker→speaker'));
      const ev: BoundaryEvent = { tMs, kind, confidence: frameConfidence[f] };
      events.push(ev);
      this.lastEmittedBoundaryMs = tMs;
      this.onBoundary?.(ev);
    }

    // pack-msteams-diarization-cutover (#394): record overlap intervals
    // (frames whose class has 2+ active speakers) in absolute time, so
    // the diarizer can mask them out of embeddings + hard-split at their
    // edges. Only scan the FRESH tail (last freshWindowMs) so overlapping
    // inference windows don't keep re-adding the same region.
    const freshStart = Math.max(scanStart, scanEnd - Math.ceil((this.freshWindowSamples / SAMPLE_RATE) * 1000 / frameMs));
    let runStart = -1;
    for (let f = freshStart; f < scanEnd; f++) {
      const isOv = SPEAKERS_BY_CLASS[smoothed[f]].length >= 2;
      if (isOv && runStart < 0) {
        runStart = windowStartMs + f * frameMs;
      } else if (!isOv && runStart >= 0) {
        this.overlapIntervals.push([runStart, windowStartMs + f * frameMs]);
        runStart = -1;
      }
    }
    if (runStart >= 0) this.overlapIntervals.push([runStart, windowStartMs + scanEnd * frameMs]);
    // prune old intervals
    const cutoff = latestTsMs - OVERLAP_RETENTION_MS;
    if (this.overlapIntervals.length > 0 && this.overlapIntervals[0][1] < cutoff) {
      this.overlapIntervals = this.overlapIntervals.filter((iv) => iv[1] >= cutoff);
    }
    return events;
  }

  /** pack-msteams-diarization-cutover (#394): true if absolute time `tMs`
   *  falls inside a detected 2+-speaker overlap region. */
  isOverlapMs(tMs: number): boolean {
    for (const [a, b] of this.overlapIntervals) {
      if (tMs >= a && tMs < b) return true;
    }
    return false;
  }
}
