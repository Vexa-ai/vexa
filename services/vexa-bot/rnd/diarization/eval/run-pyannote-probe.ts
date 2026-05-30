/**
 * pyannote/segmentation-3.0 probe — A/B against the current wespeaker
 * change-point baseline.
 *
 * For each corpus WAV in eval/corpus/:
 *   1. Run the audio through onnx-community/pyannote-segmentation-3.0 in
 *      10s sliding windows (16kHz mono, 160000 samples per call).
 *   2. The model returns per-frame logits [1, 767, 7] = 13.04 ms per frame
 *      over 7 powerset classes:
 *         0=∅ (silence), 1=spk1, 2=spk2, 3=spk3,
 *         4=spk1+2 (overlap), 5=spk1+3 (overlap), 6=spk2+3 (overlap)
 *      The {1,2,3} speaker indices are LOCAL TO THE WINDOW — they don't
 *      persist across windows, which is fine for boundary detection (we
 *      just need WHERE the change happens, identity is from blue boxes).
 *   3. Detect boundaries as frames where argmax-class transitions
 *      between two SPEAKER classes (i.e. ignore silence↔speaker and
 *      overlap-onset transitions; treat them as candidate boundaries but
 *      weight by confidence).
 *   4. Map frame indices back to absolute audio time and emit a sorted
 *      list of detected change points.
 *   5. Score against GT speaker-change points using the same boundary
 *      recall metric as run-suite.ts (±500ms tolerance) so the numbers
 *      are directly comparable with the wespeaker baseline (88.7%).
 *
 * Usage:
 *   npx tsx eval/run-pyannote-probe.ts
 *
 * No new deps needed — @huggingface/transformers ^4.2.0 ships
 * AutoModelForAudioFrameClassification + a dedicated
 * PyAnnoteForAudioFrameClassification class.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import {
  AutoModel,
  AutoProcessor,
  type PreTrainedModel,
  type Processor,
  type Tensor,
} from '@huggingface/transformers';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CORPUS_DIR = path.join(__dirname, 'corpus');
const SAMPLE_RATE = 16_000;
const PYANNOTE_MODEL = 'onnx-community/pyannote-segmentation-3.0';
const WIN_SAMPLES = 10 * SAMPLE_RATE;        // 160_000 — model's required window
const HOP_SAMPLES = 5 * SAMPLE_RATE;          //  80_000 — 50% overlap; take middle 5s of each window for stability
const TOLERANCE_MS = 500;
const STRICT_TOLERANCE_MS = 200;

interface GroundTruth {
  id: string;
  turns: Array<{ speaker: string; text: string; start_ms: number; end_ms: number; duration_ms: number }>;
  total_duration_ms: number;
}

async function readWav16kMono(wavPath: string): Promise<Float32Array> {
  const buf = await fs.readFile(wavPath);
  let offset = 12;
  let dataOffset = -1;
  let dataLength = -1;
  let sampleRate = 0;
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === 'fmt ') sampleRate = buf.readUInt32LE(offset + 12);
    else if (id === 'data') {
      dataOffset = offset + 8;
      dataLength = size;
    }
    offset += 8 + size + (size % 2);
  }
  if (sampleRate !== SAMPLE_RATE) throw new Error(`${wavPath}: ${sampleRate} Hz, expected ${SAMPLE_RATE}`);
  const numSamples = dataLength / 2;
  const samples = new Float32Array(numSamples);
  for (let i = 0; i < numSamples; i++) samples[i] = buf.readInt16LE(dataOffset + i * 2) / 32768.0;
  return samples;
}

/** Speaker SET per powerset class. pyannote 3.0 powerset(3,2):
 *   0=∅, 1={1}, 2={2}, 3={3}, 4={1,2}, 5={1,3}, 6={2,3}.
 *  Boundary detection rule: a boundary fires whenever the set GAINS a
 *  speaker that wasn't present in the prior frame. So:
 *    - silence → {1}   ⇒ boundary (speaker 1 starts)
 *    - {1}     → {1,2} ⇒ boundary (speaker 2 joined — INTERRUPTION case)
 *    - {1}     → {2}   ⇒ boundary (clean handoff)
 *    - {1,2}   → {2}   ⇒ NOT a boundary (speaker 1 left; speaker 2 was already there)
 *    - {1}     → ∅     ⇒ NOT a boundary (a pause; the next speaker frame may signal a change)
 *  This captures the overlap-onset signal that the previous "primary
 *  speaker" rule missed and which the research called out as exactly the
 *  case wespeaker can't see. */
const SPEAKERS_BY_CLASS: ReadonlyArray<ReadonlyArray<number>> = [
  [],         // 0: silence
  [1],        // 1: {1}
  [2],        // 2: {2}
  [3],        // 3: {3}
  [1, 2],     // 4: {1,2}
  [1, 3],     // 5: {1,3}
  [2, 3],     // 6: {2,3}
];

/** Returns true iff `cur` contains any speaker not in `prev`. */
function gainsSpeaker(prev: ReadonlyArray<number>, cur: ReadonlyArray<number>): boolean {
  for (const s of cur) if (!prev.includes(s)) return true;
  return false;
}

interface ChunkOutput {
  /** Per-frame argmax class (0-6) within the chunk. */
  frameClasses: number[];
  /** Per-frame max-logit confidence (softmaxed). */
  frameConfidence: number[];
  /** Frames per chunk. From the model card: 767 frames per 10s. */
  frameCount: number;
  /** Per-frame duration in ms. */
  frameMs: number;
  /** Absolute audio time where this chunk starts. */
  chunkStartMs: number;
}

async function runChunk(
  model: PreTrainedModel,
  processor: Processor,
  chunkSamples: Float32Array,
  chunkStartMs: number,
): Promise<ChunkOutput> {
  // The processor expects a Float32Array and outputs the model input tensor.
  const inputs = await processor(chunkSamples, { sampling_rate: SAMPLE_RATE });
  const outputs = (await model(inputs)) as { [k: string]: Tensor };
  const logits = outputs.logits ?? outputs[Object.keys(outputs)[0]];
  if (!logits) throw new Error('pyannote returned no logits');
  // logits shape: [1, frames, 7]
  const dims = logits.dims as number[];
  const numFrames = dims[1];
  const numClasses = dims[2];
  const data = logits.data as Float32Array;
  const frameClasses: number[] = new Array(numFrames);
  const frameConfidence: number[] = new Array(numFrames);
  for (let f = 0; f < numFrames; f++) {
    // Frame f spans logits indices [f*numClasses, f*numClasses+numClasses)
    let best = 0;
    let bestVal = -Infinity;
    let sumExp = 0;
    for (let c = 0; c < numClasses; c++) {
      const v = data[f * numClasses + c];
      if (v > bestVal) {
        bestVal = v;
        best = c;
      }
    }
    // Softmax max
    for (let c = 0; c < numClasses; c++) {
      sumExp += Math.exp(data[f * numClasses + c] - bestVal);
    }
    frameClasses[f] = best;
    frameConfidence[f] = 1 / sumExp; // = exp(bestVal-bestVal) / sumExp
  }
  const chunkDurationMs = (chunkSamples.length / SAMPLE_RATE) * 1000;
  return {
    frameClasses,
    frameConfidence,
    frameCount: numFrames,
    frameMs: chunkDurationMs / numFrames,
    chunkStartMs,
  };
}

interface DetectedBoundary {
  tMs: number;
  /** "silence→speaker" | "speaker→speaker" | "overlap-onset" */
  kind: string;
  confidence: number;
}

function extractBoundariesFromChunk(chunk: ChunkOutput): DetectedBoundary[] {
  const boundaries: DetectedBoundary[] = [];
  // Median-filter the class sequence to suppress 1-frame spikes (≈13ms
  // glitches that are almost always model noise, not real speaker events).
  const smoothed = medianFilter(chunk.frameClasses, 3);
  for (let f = 1; f < chunk.frameCount; f++) {
    const prevCls = smoothed[f - 1];
    const curCls = smoothed[f];
    if (prevCls === curCls) continue;
    const prevSpeakers = SPEAKERS_BY_CLASS[prevCls];
    const curSpeakers = SPEAKERS_BY_CLASS[curCls];
    if (!gainsSpeaker(prevSpeakers, curSpeakers)) continue;
    // Classify for logging only:
    const kind = prevSpeakers.length === 0
      ? 'silence→speaker'
      : (curSpeakers.length > prevSpeakers.length ? 'overlap-onset' : 'speaker→speaker');
    const tMs = chunk.chunkStartMs + f * chunk.frameMs;
    boundaries.push({ tMs, kind, confidence: chunk.frameConfidence[f] });
  }
  return boundaries;
}

/** 1-D median filter with odd window. Preserves sharp transitions but
 *  removes single-frame outliers. */
function medianFilter(arr: number[], windowSize: number): number[] {
  if (windowSize % 2 === 0) windowSize++;
  const half = (windowSize - 1) / 2;
  const out = new Array<number>(arr.length);
  const buf = new Array<number>(windowSize);
  for (let i = 0; i < arr.length; i++) {
    let k = 0;
    for (let j = i - half; j <= i + half; j++) {
      const idx = Math.max(0, Math.min(arr.length - 1, j));
      buf[k++] = arr[idx];
    }
    const sorted = [...buf].sort((a, b) => a - b);
    out[i] = sorted[half];
  }
  return out;
}

/** Dedup boundaries that are within `mergeMs` of each other (artifacts of
 *  the 50% chunk overlap). Keep the higher-confidence boundary. */
function dedupBoundaries(boundaries: DetectedBoundary[], mergeMs = 100): DetectedBoundary[] {
  if (boundaries.length === 0) return [];
  const sorted = [...boundaries].sort((a, b) => a.tMs - b.tMs);
  const merged: DetectedBoundary[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const last = merged[merged.length - 1];
    if (sorted[i].tMs - last.tMs <= mergeMs) {
      if (sorted[i].confidence > last.confidence) merged[merged.length - 1] = sorted[i];
    } else {
      merged.push(sorted[i]);
    }
  }
  return merged;
}

async function probeCorpus(
  id: string,
  samples: Float32Array,
  gt: GroundTruth,
  model: PreTrainedModel,
  processor: Processor,
) {
  // Slide WIN_SAMPLES windows with HOP_SAMPLES stride. Only keep boundary
  // events that land in the central trust region of each window:
  //   - first window:  [0, HOP_SAMPLES + WIN_SAMPLES/4) absolute time
  //   - middle window: [HOP_SAMPLES/2, HOP_SAMPLES + WIN_SAMPLES/2)
  //   - last window:   [last.start + WIN_SAMPLES/4, last.start + WIN_SAMPLES)
  // Simpler approximation: take only the center 50% of each window. The
  // overlap then guarantees full coverage with cleaner near-the-edge
  // predictions.
  const allBoundaries: DetectedBoundary[] = [];
  let chunkStart = 0;
  const totalSamples = samples.length;
  const inferStart = Date.now();
  let nChunks = 0;
  while (chunkStart < totalSamples) {
    const chunkEnd = Math.min(chunkStart + WIN_SAMPLES, totalSamples);
    let chunk = samples.subarray(chunkStart, chunkEnd);
    // Right-pad short trailing chunk with zeros so the model gets its full
    // 10s window (avoid weird trailing artifacts).
    if (chunk.length < WIN_SAMPLES) {
      const padded = new Float32Array(WIN_SAMPLES);
      padded.set(chunk);
      chunk = padded;
    }
    const chunkResult = await runChunk(model, processor, chunk, chunkStart / SAMPLE_RATE * 1000);
    nChunks++;
    const boundaries = extractBoundariesFromChunk(chunkResult);
    // Trust region: center 50% of the window. For the FIRST and LAST chunk
    // we expand the trust region toward the start/end of audio respectively
    // so we don't miss boundaries at the edges of the corpus.
    const winMs = (WIN_SAMPLES / SAMPLE_RATE) * 1000;
    const trustStart = chunkStart === 0 ? 0 : (chunkResult.chunkStartMs + winMs * 0.25);
    const trustEnd = (chunkEnd >= totalSamples)
      ? (totalSamples / SAMPLE_RATE) * 1000
      : (chunkResult.chunkStartMs + winMs * 0.75);
    for (const b of boundaries) {
      if (b.tMs >= trustStart && b.tMs < trustEnd) allBoundaries.push(b);
    }
    chunkStart += HOP_SAMPLES;
  }
  const inferMs = Date.now() - inferStart;

  // Dedup overlap-induced near-duplicate boundaries.
  const finalBoundaries = dedupBoundaries(allBoundaries, 100);
  const boundaryTimes = finalBoundaries.map((b) => b.tMs).sort((a, b) => a - b);

  // Score against GT speaker-change events.
  const changes: number[] = [];
  for (let i = 1; i < gt.turns.length; i++) {
    if (gt.turns[i].speaker !== gt.turns[i - 1].speaker) changes.push(gt.turns[i].start_ms);
  }
  let hits500 = 0;
  let hits200 = 0;
  const offsets: number[] = [];
  for (const ch of changes) {
    let minAbs = Infinity;
    let nearestSigned = 0;
    for (const b of boundaryTimes) {
      const d = b - ch;
      if (Math.abs(d) < minAbs) {
        minAbs = Math.abs(d);
        nearestSigned = d;
      }
    }
    offsets.push(nearestSigned);
    if (minAbs <= TOLERANCE_MS) hits500++;
    if (minAbs <= STRICT_TOLERANCE_MS) hits200++;
  }
  const recall500 = changes.length > 0 ? hits500 / changes.length : 1;
  const recall200 = changes.length > 0 ? hits200 / changes.length : 1;
  return {
    id,
    nChunks,
    inferMs,
    boundaryCount: boundaryTimes.length,
    gtChangeCount: changes.length,
    recall500,
    recall200,
    offsets,
    finalBoundaries,
  };
}

async function probeSingleWavNoGT(
  wavPath: string,
  model: PreTrainedModel,
  processor: Processor,
) {
  const samples = await readWav16kMono(wavPath);
  const durS = samples.length / SAMPLE_RATE;
  console.log(`[pyannote-probe] ${wavPath}  duration=${durS.toFixed(1)}s`);
  const allBoundaries: DetectedBoundary[] = [];
  let chunkStart = 0;
  const totalSamples = samples.length;
  const inferStart = Date.now();
  let nChunks = 0;
  while (chunkStart < totalSamples) {
    const chunkEnd = Math.min(chunkStart + WIN_SAMPLES, totalSamples);
    let chunk = samples.subarray(chunkStart, chunkEnd);
    if (chunk.length < WIN_SAMPLES) {
      const padded = new Float32Array(WIN_SAMPLES);
      padded.set(chunk);
      chunk = padded;
    }
    const chunkResult = await runChunk(model, processor, chunk, chunkStart / SAMPLE_RATE * 1000);
    nChunks++;
    const boundaries = extractBoundariesFromChunk(chunkResult);
    const winMs = (WIN_SAMPLES / SAMPLE_RATE) * 1000;
    const trustStart = chunkStart === 0 ? 0 : (chunkResult.chunkStartMs + winMs * 0.25);
    const trustEnd = (chunkEnd >= totalSamples)
      ? (totalSamples / SAMPLE_RATE) * 1000
      : (chunkResult.chunkStartMs + winMs * 0.75);
    for (const b of boundaries) {
      if (b.tMs >= trustStart && b.tMs < trustEnd) allBoundaries.push(b);
    }
    chunkStart += HOP_SAMPLES;
  }
  const inferMs = Date.now() - inferStart;
  const final = dedupBoundaries(allBoundaries, 100);
  console.log();
  console.log(`pyannote/segmentation-3.0 boundary detections — ${final.length} events from ${nChunks} chunks (${inferMs}ms total inference)`);
  console.log();
  for (const b of final) {
    const s = b.tMs / 1000;
    const mins = Math.floor(s / 60);
    const secs = (s % 60).toFixed(2);
    console.log(`  ${mins}:${secs.padStart(5, '0')}  (${b.tMs.toFixed(0).padStart(7)}ms)  ${b.kind.padEnd(16)}  conf=${b.confidence.toFixed(3)}`);
  }
  // Also print the current wespeaker change-point baseline's commits on
  // the same wav, for side-by-side comparison.
  console.log();
  console.log(`Tip: compare to current wespeaker baseline via:`);
  console.log(`  npx tsx eval/run-wav.ts ${wavPath}`);
  return 0;
}

async function main(): Promise<number> {
  const arg = process.argv[2];
  console.log(`[pyannote-probe] loading model ${PYANNOTE_MODEL}...`);
  const t0 = Date.now();
  const model = await AutoModel.from_pretrained(PYANNOTE_MODEL, { device: 'cpu' });
  const processor = await AutoProcessor.from_pretrained(PYANNOTE_MODEL);
  console.log(`[pyannote-probe] model loaded in ${Date.now() - t0}ms`);

  // Single-file mode: just print the detected boundaries (no GT needed).
  // Use when probing a captured-from-YouTube wav.
  if (arg && arg.endsWith('.wav')) {
    const abs = path.isAbsolute(arg) ? arg : path.resolve(process.cwd(), arg);
    return probeSingleWavNoGT(abs, model, processor);
  }

  const entries = await fs.readdir(CORPUS_DIR);
  const wavs = entries.filter((e) => e.endsWith('.wav')).sort();
  console.log(`[pyannote-probe] ${wavs.length} corpora — scoring against ground-truth`);

  const allResults: Awaited<ReturnType<typeof probeCorpus>>[] = [];
  for (const wav of wavs) {
    const id = wav.replace(/\.wav$/, '');
    const gtPath = path.join(CORPUS_DIR, `${id}.ground-truth.json`);
    try { await fs.access(gtPath); } catch { console.log(`[pyannote-probe]   ${id}: SKIP (no ground-truth.json)`); continue; }
    const gt = JSON.parse(await fs.readFile(gtPath, 'utf-8')) as GroundTruth;
    const samples = await readWav16kMono(path.join(CORPUS_DIR, wav));
    const r = await probeCorpus(id, samples, gt, model, processor);
    allResults.push(r);
    console.log(
      `[pyannote-probe]   ${id}: ${(samples.length / SAMPLE_RATE).toFixed(1)}s, ` +
        `${r.nChunks} chunks (${r.inferMs}ms), ` +
        `${r.boundaryCount} detected vs ${r.gtChangeCount} GT  → ` +
        `recall@500ms=${(r.recall500 * 100).toFixed(1)}%  ` +
        `strict@200ms=${(r.recall200 * 100).toFixed(1)}%`,
    );
    // Per-corpus offset report
    const offsetSummary = r.offsets.map((o) => `${o >= 0 ? '+' : ''}${o.toFixed(0)}`).join(', ');
    console.log(`[pyannote-probe]     offsets vs GT: ${offsetSummary}`);
  }

  // Aggregate
  console.log();
  console.log('═══════════════════ PYANNOTE-SEGMENTATION-3.0 PROBE vs GROUND TRUTH ═══════════════════');
  let totalGt = 0;
  let totalHit500 = 0;
  let totalHit200 = 0;
  for (const r of allResults) {
    totalGt += r.gtChangeCount;
    totalHit500 += Math.round(r.recall500 * r.gtChangeCount);
    totalHit200 += Math.round(r.recall200 * r.gtChangeCount);
    console.log(
      `  ${r.id.padEnd(32)}  detected=${String(r.boundaryCount).padStart(3)}  GT=${String(r.gtChangeCount).padStart(2)}  ` +
        `recall@500=${(r.recall500 * 100).toFixed(1).padStart(5)}%  ` +
        `strict@200=${(r.recall200 * 100).toFixed(1).padStart(5)}%`,
    );
  }
  const aggR500 = totalGt > 0 ? totalHit500 / totalGt : 1;
  const aggR200 = totalGt > 0 ? totalHit200 / totalGt : 1;
  console.log();
  console.log(`OVERALL recall@500ms=${(aggR500 * 100).toFixed(1)}%  strict@200ms=${(aggR200 * 100).toFixed(1)}%`);
  console.log(`BASELINE (wespeaker change-point): recall@500ms=88.7%  strict@200ms=85.3%`);
  console.log();
  console.log('Interpretation:');
  console.log('  - If recall@500 jumps above 88.7%, pyannote wins on boundary detection.');
  console.log('  - If strict@200 jumps significantly above the baseline, pyannote is also');
  console.log('    more PRECISE in WHERE it places boundaries (sub-100ms vs ~1s coarseness).');
  console.log('  - If both numbers drop, our boundary-extraction heuristic from the powerset');
  console.log('    classes needs adjustment (e.g., use overlap-onset as a boundary signal).');
  return 0;
}

main().then((c) => process.exit(c)).catch((err) => { console.error('[pyannote-probe] fatal:', err); process.exit(1); });
