/**
 * TurnGate — pack-msteams-diarization-cutover (#394), segmentation-cut design.
 *
 * Replaces "route every commit by its cluster id" with:
 *   1. Segmentation (the diarizer's commits) defines turn boundaries.
 *   2. A new turn is opened when a commit's voiceprint diverges from the
 *      current turn's running centroid (different speaker).
 *   3. The turn's audio is HELD — not transcribed, not published — until its
 *      centroid STABILIZES (enough voiced audio + converged embedding). Only
 *      then is a speaker NAME assigned (matched against the global speaker set)
 *      and the held audio flushed into stage ③ (SpeakerStreamManager → Whisper).
 *   4. Backstops so nothing is lost / latency is bounded:
 *        • max-hold timeout  → force best-effort name, flush, DON'T learn.
 *        • turn ends early    → flush short turn best-effort, DON'T learn.
 *   5. Global speaker centroids are updated ONLY from stabilized turns
 *      (hygiene — a backchannel can't corrupt a speaker model or spawn one).
 *
 * Stage ③ is untouched: the gate calls `flush(name, audio, tStartMs)` which the
 * caller wires to speakerManager.feedAudio. A wrong name is now cosmetic (clean
 * turn, wrong label) — never cross-speaker audio contamination.
 */

export interface TurnGateConfig {
  /** cosine dist above which a commit belongs to a NEW speaker (open new turn). */
  switchDist: number;
  /** voiced samples required before a turn may stabilize (≈1.5s @16k). */
  minStableSamples: number;
  /** last-embedding-vs-centroid cosine delta below which the turn is "converged". */
  convergeEps: number;
  /** cosine dist under which a stabilized turn matches an EXISTING global speaker. */
  matchThreshold: number;
  /** required gap to the 2nd-nearest speaker before minting a NEW speaker. */
  newSpeakerMargin: number;
  /** force-flush a still-PENDING turn after this many ms (bounds latency). */
  maxHoldMs: number;
  /** EMA weight for updating a global centroid from a stabilized turn. */
  emaAlpha: number;
}

export const DEFAULT_TURN_GATE: TurnGateConfig = {
  switchDist: 0.55,
  minStableSamples: 1.5 * 16000,
  convergeEps: 0.10,
  matchThreshold: 0.55,
  newSpeakerMargin: 0.10,
  maxHoldMs: 2500,
  emaAlpha: 0.7,
};

type FlushFn = (name: string, audio: Float32Array, tStartMs: number) => void;

function normalize(v: Float32Array): Float32Array {
  let n = 0;
  for (let i = 0; i < v.length; i++) n += v[i] * v[i];
  n = Math.sqrt(n);
  if (n < 1e-8) return v;
  const out = new Float32Array(v.length);
  for (let i = 0; i < v.length; i++) out[i] = v[i] / n;
  return out;
}
function dot(a: Float32Array, b: Float32Array): number {
  let d = 0;
  for (let i = 0; i < a.length; i++) d += a[i] * b[i];
  return d;
}
function meanNormalize(embs: Float32Array[]): Float32Array {
  const out = new Float32Array(embs[0].length);
  for (const e of embs) for (let i = 0; i < e.length; i++) out[i] += e[i];
  for (let i = 0; i < out.length; i++) out[i] /= embs.length;
  return normalize(out);
}
function concat(parts: Float32Array[]): Float32Array {
  const n = parts.reduce((s, p) => s + p.length, 0);
  const out = new Float32Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

interface HeldTurn {
  chunks: Float32Array[];   // not-yet-flushed audio (only while PENDING)
  embs: Float32Array[];
  centroid: Float32Array;
  voiced: number;           // samples
  name: string | null;      // null = PENDING (held); set = STREAMING (flushed)
  startMs: number;
}

export class TurnGate {
  private centroids = new Map<string, Float32Array>();
  private counter = 0;
  private held: HeldTurn | null = null;

  constructor(private cfg: TurnGateConfig, private flush: FlushFn) {}

  /** Feed one diarizer commit (segmentation-bounded segment + its embedding). */
  onCommit(embRaw: Float32Array, audio: Float32Array, tStartMs: number, tEndMs: number): void {
    const e = normalize(embRaw);
    // 1) continuation vs new turn — voiceprint divergence from the held turn
    if (this.held && 1 - dot(e, this.held.centroid) > this.cfg.switchDist) {
      this.closeTurn();
    }
    // 2) open or extend
    if (!this.held) {
      this.held = { chunks: [audio], embs: [e], centroid: e, voiced: audio.length, name: null, startMs: tStartMs };
    } else {
      this.held.embs.push(e);
      this.held.centroid = meanNormalize(this.held.embs);
      this.held.voiced += audio.length;
      if (this.held.name == null) this.held.chunks.push(audio);
    }
    // 3) STREAMING turn → flush immediately; PENDING turn → try to stabilize
    if (this.held.name != null) {
      this.flush(this.held.name, audio, tStartMs);
    } else {
      this.maybeStabilize(tEndMs);
    }
  }

  /** Flush the final held turn (call on meeting end / cleanup). */
  finish(): void { this.closeTurn(); }

  private maybeStabilize(tEndMs: number): void {
    const h = this.held!;
    const enoughVoiced = h.voiced >= this.cfg.minStableSamples;
    const converged = h.embs.length < 2 || 1 - dot(h.embs[h.embs.length - 1], h.centroid) < this.cfg.convergeEps;
    const timedOut = tEndMs - h.startMs >= this.cfg.maxHoldMs;
    const stabilized = enoughVoiced && converged;
    if (!stabilized && !timedOut) return;
    const name = this.assignName(h.centroid, stabilized);
    h.name = name;
    const heldAudio = concat(h.chunks);
    h.chunks = [];
    this.flush(name, heldAudio, h.startMs);
    if (stabilized) this.updateCentroid(name, h.centroid);
  }

  private closeTurn(): void {
    const h = this.held;
    this.held = null;
    if (!h) return;
    if (h.name == null) {
      // short turn ended before stabilizing — best-effort name, never learn
      const name = this.assignName(h.centroid, false);
      this.flush(name, concat(h.chunks), h.startMs);
    }
  }

  /** Match a turn centroid to the global speaker set (mint new only if allowed). */
  private assignName(centroid: Float32Array, allowNew: boolean): string {
    if (this.centroids.size === 0) return this.mint(centroid);
    let nearestId = '', nearest = Infinity, second = Infinity;
    for (const [id, c] of this.centroids) {
      const d = 1 - dot(centroid, c);
      if (d < nearest) { second = nearest; nearest = d; nearestId = id; }
      else if (d < second) { second = d; }
    }
    if (nearest < this.cfg.matchThreshold) return nearestId;
    if (allowNew && (second - nearest >= this.cfg.newSpeakerMargin || this.centroids.size < 1)) return this.mint(centroid);
    return nearestId; // best-effort, no new speaker
  }
  private mint(centroid: Float32Array): string {
    const id = `speaker_${this.counter++}`;
    this.centroids.set(id, new Float32Array(centroid));
    return id;
  }
  private updateCentroid(name: string, c: Float32Array): void {
    const old = this.centroids.get(name);
    if (!old) { this.centroids.set(name, new Float32Array(c)); return; }
    const a = this.cfg.emaAlpha, out = new Float32Array(old.length);
    for (let i = 0; i < old.length; i++) out[i] = a * old[i] + (1 - a) * c[i];
    this.centroids.set(name, normalize(out));
  }
}
