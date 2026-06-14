/**
 * bench/score — the reusable scorer for the mixed-pipeline benchmark (Lane 1).
 *
 * Pure functions, no I/O, no external deps (so it stays inside the brick's
 * isolation boundary and is unit-testable). Scores OUR separated-transcript.v1
 * segments against a Deepgram reference, in priority order:
 *
 *   ① segmentation (PRIMARY) — boundary precision/recall/F1 at a time tolerance,
 *      plus mean IoU of greedily time-matched segments. The headline metric.
 *   ② transcription          — word-level WER (Levenshtein) of normalized text.
 *   ③ cluster count (INFO)   — distinct speaker keys vs deepgram speakers, delta.
 */

/** A minimal time-bounded segment (seconds). Both ours and the reference reduce to this. */
export interface ScoredSegment {
  speaker: string;
  text: string;
  start: number; // seconds
  end: number;   // seconds
}

export interface SegmentationMetrics {
  toleranceMs: number;
  boundaryPrecision: number; // matched-ours / total-ours boundaries
  boundaryRecall: number;    // matched-ref  / total-ref  boundaries
  boundaryF1: number;
  matchedBoundaries: number;
  oursBoundaries: number;
  refBoundaries: number;
}

export interface TranscriptionMetrics {
  wer: number;            // 0..1+ (insertions can push >1)
  refWords: number;
  oursWords: number;
  editDistance: number;
}

export interface ClusterMetrics {
  oursClusters: number;
  refSpeakers: number;
  delta: number;          // ours - ref
}

export interface Scorecard {
  segmentation200: SegmentationMetrics;
  segmentation500: SegmentationMetrics;
  meanIoU: number;        // greedy time-matched segment IoU, headline companion
  transcription: TranscriptionMetrics;
  clusters: ClusterMetrics;
  oursSegments: number;
  refSegments: number;
}

// ───────────────────────── ① segmentation ─────────────────────────

/** Every segment contributes a start and an end boundary (seconds). */
function boundaries(segs: ScoredSegment[]): number[] {
  const b: number[] = [];
  for (const s of segs) { b.push(s.start); b.push(s.end); }
  return b.sort((a, z) => a - z);
}

/**
 * Greedy one-to-one boundary match within tolerance. Each ref boundary may match
 * at most one ours boundary and vice-versa; we walk sorted lists and pair the
 * closest available within tolerance.
 */
export function scoreSegmentation(ours: ScoredSegment[], ref: ScoredSegment[], toleranceMs: number): SegmentationMetrics {
  const tol = toleranceMs / 1000;
  const ob = boundaries(ours);
  const rb = boundaries(ref);
  const usedOurs = new Array(ob.length).fill(false);
  let matched = 0;
  // For each ref boundary, claim the nearest unused ours boundary within tol.
  for (const r of rb) {
    let best = -1, bestDist = Infinity;
    for (let i = 0; i < ob.length; i++) {
      if (usedOurs[i]) continue;
      const d = Math.abs(ob[i] - r);
      if (d <= tol && d < bestDist) { bestDist = d; best = i; }
    }
    if (best >= 0) { usedOurs[best] = true; matched++; }
  }
  const precision = ob.length ? matched / ob.length : 0;
  const recall = rb.length ? matched / rb.length : 0;
  const f1 = precision + recall > 0 ? (2 * precision * recall) / (precision + recall) : 0;
  return {
    toleranceMs,
    boundaryPrecision: precision,
    boundaryRecall: recall,
    boundaryF1: f1,
    matchedBoundaries: matched,
    oursBoundaries: ob.length,
    refBoundaries: rb.length,
  };
}

/** Intersection-over-union of two time intervals (seconds). */
function iou(a: ScoredSegment, b: ScoredSegment): number {
  const inter = Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));
  if (inter <= 0) return 0;
  const union = (a.end - a.start) + (b.end - b.start) - inter;
  return union > 0 ? inter / union : 0;
}

/**
 * Mean IoU of greedily time-matched segments: for each ours segment, claim the
 * unused ref segment with the highest IoU. Mean over ours segments (so missing
 * coverage is penalized via zeros). Direction-symmetric enough for a headline.
 */
export function meanIoU(ours: ScoredSegment[], ref: ScoredSegment[]): number {
  if (!ours.length) return 0;
  const usedRef = new Array(ref.length).fill(false);
  let sum = 0;
  for (const o of ours) {
    let best = -1, bestIoU = 0;
    for (let j = 0; j < ref.length; j++) {
      if (usedRef[j]) continue;
      const v = iou(o, ref[j]);
      if (v > bestIoU) { bestIoU = v; best = j; }
    }
    if (best >= 0) usedRef[best] = true;
    sum += bestIoU;
  }
  return sum / ours.length;
}

// ───────────────────────── ② transcription (WER) ─────────────────────────

/** Normalize: lowercase, strip punctuation, collapse whitespace → word list. */
export function normalizeWords(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s']/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);
}

/** Standard Levenshtein word-edit distance (insert/delete/substitute = 1). */
function wordEditDistance(ref: string[], hyp: string[]): number {
  const n = ref.length, m = hyp.length;
  if (n === 0) return m;
  if (m === 0) return n;
  let prev = new Array(m + 1);
  for (let j = 0; j <= m; j++) prev[j] = j;
  for (let i = 1; i <= n; i++) {
    const cur = new Array(m + 1);
    cur[0] = i;
    for (let j = 1; j <= m; j++) {
      const cost = ref[i - 1] === hyp[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    prev = cur;
  }
  return prev[m];
}

/** WER of ours vs reference: edit-distance / ref-word-count. */
export function scoreTranscription(oursText: string, refText: string): TranscriptionMetrics {
  const refW = normalizeWords(refText);
  const oursW = normalizeWords(oursText);
  const dist = wordEditDistance(refW, oursW);
  return {
    wer: refW.length ? dist / refW.length : (oursW.length ? 1 : 0),
    refWords: refW.length,
    oursWords: oursW.length,
    editDistance: dist,
  };
}

// ───────────────────────── ③ cluster count (info) ─────────────────────────

export function scoreClusters(ours: ScoredSegment[], ref: ScoredSegment[]): ClusterMetrics {
  const o = new Set(ours.map((s) => s.speaker)).size;
  const r = new Set(ref.map((s) => s.speaker)).size;
  return { oursClusters: o, refSpeakers: r, delta: o - r };
}

// ───────────────────────── full scorecard ─────────────────────────

export function score(ours: ScoredSegment[], ref: ScoredSegment[]): Scorecard {
  const oursText = ours.map((s) => s.text).join(' ');
  const refText = ref.map((s) => s.text).join(' ');
  return {
    segmentation200: scoreSegmentation(ours, ref, 200),
    segmentation500: scoreSegmentation(ours, ref, 500),
    meanIoU: meanIoU(ours, ref),
    transcription: scoreTranscription(oursText, refText),
    clusters: scoreClusters(ours, ref),
    oursSegments: ours.length,
    refSegments: ref.length,
  };
}
