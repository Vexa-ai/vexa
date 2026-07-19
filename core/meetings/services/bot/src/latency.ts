/**
 * The CONFIRM-LAG oracle — how long after a word is audible does the transcript admit it?
 *
 * "Great latency" was an unchecked claim: nothing in the tree measured the delay between speech
 * and its confirmed segment, so a change that doubled it would ship green. Wall-clock is the wrong
 * instrument for that — it measures the machine, and an offline replay feeds instantly — so this
 * measures the delay in AUDIO time, which is the part the pipeline's own design controls:
 *
 *     lag = (audio ingested when the segment was published) − (audio-time the segment ends)
 *
 * That is "a word spoken at T is confirmed once the pipeline has heard through T + lag". It is
 * deterministic (same fixture ⇒ same number, on any machine), it is exactly the quantity the
 * LocalAgreement confirm threshold and the submit interval trade against accuracy, and it is
 * comparable across lanes and across commits. The STT provider's own wall-clock round-trip is a
 * SEPARATE budget, measured live by the recorder's stt.jsonl tap — the two must not be conflated:
 * this one is the structural floor, that one is the vendor's.
 */

/** One confirmed segment, timed. */
export interface LagSample {
  speaker: string;
  /** Audio-time ms at which the segment's speech ends. */
  endMs: number;
  /** Audio-time ms the pipeline had ingested when it published the segment. */
  ingestedMs: number;
  /** ingestedMs − endMs. Negative is impossible in a causal pipeline and is reported, not hidden. */
  lagMs: number;
}

export interface LagReport {
  samples: LagSample[];
  count: number;
  p50Ms: number;
  p95Ms: number;
  maxMs: number;
  /** True when at least one segment was confirmed before its own audio finished — a causality
   *  violation that means the harness (or the pipeline) is mis-timing, never a real speed-up. */
  impossible: boolean;
}

export interface LatencyOracle {
  /** Call as each frame is fed, with the frame's audio-time END (ts + duration). */
  ingested(audioMs: number): void;
  /** Call for each CONFIRMED segment, with the audio-time its speech ends. */
  confirmed(speaker: string, endMs: number): void;
  report(): LagReport;
}

const pct = (sorted: number[], p: number): number =>
  sorted.length === 0 ? 0 : sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))];

export function createLatencyOracle(): LatencyOracle {
  const samples: LagSample[] = [];
  let ingestedMs = 0;

  return {
    ingested(audioMs: number): void {
      if (audioMs > ingestedMs) ingestedMs = audioMs;
    },
    confirmed(speaker: string, endMs: number): void {
      samples.push({ speaker, endMs, ingestedMs, lagMs: Math.round(ingestedMs - endMs) });
    },
    report(): LagReport {
      const lags = samples.map((s) => s.lagMs).sort((a, b) => a - b);
      return {
        samples,
        count: samples.length,
        p50Ms: pct(lags, 0.5),
        p95Ms: pct(lags, 0.95),
        maxMs: lags.length ? lags[lags.length - 1] : 0,
        impossible: samples.some((s) => s.lagMs < 0),
      };
    },
  };
}

/** Render a report as one reviewable line. */
export function formatLag(label: string, r: LagReport): string {
  return `${label}: n=${r.count} p50=${r.p50Ms}ms p95=${r.p95Ms}ms max=${r.maxMs}ms`;
}
