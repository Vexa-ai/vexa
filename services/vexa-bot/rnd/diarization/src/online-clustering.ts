/**
 * Online speaker clustering — incremental cosine-distance clustering with
 * stable cluster IDs across the session.
 *
 * Each new embedding is either assigned to an existing speaker (its
 * cosine distance to that speaker's centroid is below NEW_SPEAKER_THRESHOLD,
 * and the centroid is updated via EMA) or allocated a fresh `speaker_N`.
 *
 * The `speaker_db` dict's IDs are STABLE for the lifetime of the
 * OnlineSpeakerClustering instance — once `speaker_0` is bound to a
 * voice's centroid, it stays bound. No reshuffling between windows. This
 * is the property that the offline-Pipeline-in-rolling-window approach
 * couldn't deliver.
 */

export interface OnlineSpeakerClusteringConfig {
  /** Cosine distance threshold. Embeddings whose nearest existing centroid is
   *  further than this allocate a new speaker. Default 0.45 — tuned for
   *  wespeaker normalized embeddings; tune per model. */
  newSpeakerThreshold?: number;
  /** "Clearly-different-voice" override threshold. Even when `canSeedNew=false`
   *  (e.g. short utterance), if the embedding is THIS far from every existing
   *  centroid, treat it as a brand-new speaker anyway. The "very brief
   *  acknowledgment from a different voice" case (a host's quick 'sure'
   *  during a guest's monologue). Default 0.60. Must be > newSpeakerThreshold. */
  veryFarThreshold?: number;
  /** EMA decay: new centroid = α·old + (1-α)·new. Default 0.85 (slow adapt). */
  emaAlpha?: number;
  /** Optional upper bound on number of speakers — once reached, embeddings
   *  ALWAYS get assigned to nearest centroid, never a new one. */
  maxSpeakers?: number;
}

export interface ClusterAssignment {
  speakerId: string;
  /** Distance to centroid used for the match. NaN for fresh allocations. */
  distance: number;
  /** True iff this call allocated a brand new speaker. */
  isNew: boolean;
}

export class OnlineSpeakerClustering {
  private centroids = new Map<string, Float32Array>();
  private readonly threshold: number;
  private readonly veryFarThreshold: number;
  private readonly emaAlpha: number;
  private readonly maxSpeakers?: number;

  constructor(cfg: OnlineSpeakerClusteringConfig = {}) {
    this.threshold = cfg.newSpeakerThreshold ?? 0.45;
    // Calibrated empirically against the 4-corpus eval suite:
    //   - 0.60 was too loose: noisy overlap embeddings landed at dist 0.60+
    //     from the speaker's own centroid → spurious cluster allocations
    //     (5speakers-meeting bob split into 4).
    //   - 1.5 (disabled) was too tight: short genuinely-different-voice
    //     utterances (intro guest 2.37s commit @ dist 0.908) couldn't seed
    //     and got force-matched to the wrong cluster.
    //   - 0.85 keeps both: genuine new-voice short utts (dist ~0.9) bypass
    //     the seed gate; in-cluster noisy short utts (dist ~0.5-0.7) match.
    this.veryFarThreshold = cfg.veryFarThreshold ?? 0.85;
    this.emaAlpha = cfg.emaAlpha ?? 0.85;
    this.maxSpeakers = cfg.maxSpeakers;
  }

  size(): number {
    return this.centroids.size;
  }

  speakers(): string[] {
    return [...this.centroids.keys()];
  }

  reset(): void {
    this.centroids.clear();
  }

  /**
   * Assign an embedding to an existing speaker or allocate a new one.
   * `embedding` must be unit-normalized (we don't normalize internally
   * to avoid double-normalization when callers pre-normalize).
   */
  assign(embedding: Float32Array): ClusterAssignment {
    return this.assignWithSeedGate(embedding, /* canSeedNew */ true);
  }

  /**
   * Same as `assign`, but the caller controls whether this embedding is
   * allowed to *seed a brand new centroid*. When `canSeedNew=false`, the
   * embedding ALWAYS gets matched to the nearest existing centroid no
   * matter the distance (used for short utterances whose embeddings are
   * too noisy to safely allocate a new speaker from).
   */
  assignWithSeedGate(embedding: Float32Array, canSeedNew: boolean): ClusterAssignment {
    if (this.centroids.size === 0) {
      if (!canSeedNew) {
        // No centroids yet AND caller said "don't seed" — fall back to
        // speaker_0 without storing a centroid. The next eligible utterance
        // will properly seed speaker_0.
        return { speakerId: 'speaker_0', distance: NaN, isNew: false };
      }
      const id = 'speaker_0';
      this.centroids.set(id, copyAndNormalize(embedding));
      return { speakerId: id, distance: 0, isNew: true };
    }

    // Compute all distances, find nearest AND second-nearest.
    let nearestId = '';
    let nearestDist = Infinity;
    let secondNearestDist = Infinity;
    for (const [id, centroid] of this.centroids) {
      const dot = dotProduct(embedding, centroid);
      const dist = 1 - dot;
      if (dist < nearestDist) {
        secondNearestDist = nearestDist;
        nearestDist = dist;
        nearestId = id;
      } else if (dist < secondNearestDist) {
        secondNearestDist = dist;
      }
    }

    const underCap = this.maxSpeakers == null || this.centroids.size < this.maxSpeakers;
    // Very-far override: short utterance whose distance to nearest is very
    // high (clearly different voice).
    const veryFarOverride = !canSeedNew && nearestDist >= this.veryFarThreshold;
    // Second-nearest gap rule: an embedding qualifies as a brand-new speaker
    // only if it is **distinctly** closer to NOTHING in particular. Pure
    // mixed-voice utterances (multiple speakers overlapping) tend to sit
    // roughly equidistant from several existing centroids — nearestDist
    // might be 0.55-0.65 but secondNearestDist is similar (~0.60-0.70).
    // In that case it's not a new speaker, it's ambiguous mixed audio —
    // force into nearest. The gap rule requires nearestDist to be
    // meaningfully closer to the "next closest", i.e. there must be a
    // GAP of at least `gapMargin` between nearest and second-nearest,
    // OR nearest must be very far from everything (>= veryFarThreshold).
    const gapMargin = 0.10;
    const hasGap = (this.centroids.size < 2) ||
                   (secondNearestDist - nearestDist >= gapMargin) ||
                   (nearestDist >= this.veryFarThreshold);
    const canAllocateNew = (canSeedNew || veryFarOverride) && underCap && hasGap;
    if (nearestDist < this.threshold || !canAllocateNew) {
      // Assign to existing speaker; update centroid via EMA, re-normalize.
      // Only update the centroid if this was actually a CONFIDENT match
      // (well under threshold). Tighter than threshold * 0.75 — use an
      // absolute 0.25 cap. Reasoning: same-speaker dists cluster ~0.10-0.25
      // on natural speech. Anything 0.25-0.45 might be noisy embeddings
      // (overlap, short utterance, room change) and shouldn't drift the
      // centroid even if the assignment is correct.
      const oldCentroid = this.centroids.get(nearestId)!;
      if (nearestDist < 0.25) {
        const updated = new Float32Array(oldCentroid.length);
        for (let i = 0; i < updated.length; i++) {
          updated[i] = this.emaAlpha * oldCentroid[i] + (1 - this.emaAlpha) * embedding[i];
        }
        normalizeInPlace(updated);
        this.centroids.set(nearestId, updated);
      }
      return { speakerId: nearestId, distance: nearestDist, isNew: false };
    }

    // Allocate a fresh speaker.
    const newId = `speaker_${this.centroids.size}`;
    this.centroids.set(newId, copyAndNormalize(embedding));
    return { speakerId: newId, distance: nearestDist, isNew: true };
  }

  /** Returns a mapping {old_id → kept_id} describing clusters that were
   *  merged in this pass. Empty if nothing merged. Two clusters merge when
   *  their centroids are within `mergeThreshold` cosine distance — that
   *  typically means a noisy short-utterance embedding allocated a spurious
   *  cluster that later evidence showed was the same speaker.
   *
   *  Called by the diarizer periodically (e.g. after each commit). The
   *  returned mapping is informational; the clusterer itself updates its
   *  internal state. Callers should use it to rewrite any previously-stored
   *  speaker labels (the eval pipeline does this when computing alignment). */
  mergeClose(mergeThreshold = 0.20): Map<string, string> {
    const result = new Map<string, string>();
    const ids = [...this.centroids.keys()];
    // Conservative greedy merge: for each pair, if close, merge later into earlier.
    let didMerge = true;
    while (didMerge) {
      didMerge = false;
      const curIds = [...this.centroids.keys()];
      outer: for (let i = 0; i < curIds.length; i++) {
        for (let j = i + 1; j < curIds.length; j++) {
          const a = this.centroids.get(curIds[i])!;
          const b = this.centroids.get(curIds[j])!;
          const dist = 1 - dotProduct(a, b);
          if (dist < mergeThreshold) {
            // Merge curIds[j] (newer) into curIds[i] (older).
            const merged = new Float32Array(a.length);
            for (let k = 0; k < merged.length; k++) merged[k] = (a[k] + b[k]) / 2;
            normalizeInPlace(merged);
            this.centroids.set(curIds[i], merged);
            this.centroids.delete(curIds[j]);
            // Resolve transitive merges
            let target = curIds[i];
            while (result.has(target)) target = result.get(target)!;
            result.set(curIds[j], target);
            didMerge = true;
            break outer;
          }
        }
      }
    }
    return result;
  }
}

function dotProduct(a: Float32Array, b: Float32Array): number {
  if (a.length !== b.length) throw new Error(`embedding dim mismatch: ${a.length} vs ${b.length}`);
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += a[i] * b[i];
  return sum;
}

function copyAndNormalize(src: Float32Array): Float32Array {
  const out = new Float32Array(src.length);
  out.set(src);
  normalizeInPlace(out);
  return out;
}

function normalizeInPlace(v: Float32Array): void {
  let sumSq = 0;
  for (let i = 0; i < v.length; i++) sumSq += v[i] * v[i];
  const norm = Math.sqrt(sumSq);
  if (norm < 1e-8) return;
  for (let i = 0; i < v.length; i++) v[i] /= norm;
}
