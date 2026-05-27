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
   *  further than this allocate a new speaker. Default 0.40 — tuned for
   *  wespeaker normalized embeddings; tune per model. */
  newSpeakerThreshold?: number;
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
  private readonly emaAlpha: number;
  private readonly maxSpeakers?: number;

  constructor(cfg: OnlineSpeakerClusteringConfig = {}) {
    this.threshold = cfg.newSpeakerThreshold ?? 0.40;
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
    if (this.centroids.size === 0) {
      const id = 'speaker_0';
      this.centroids.set(id, copyAndNormalize(embedding));
      return { speakerId: id, distance: 0, isNew: true };
    }

    // Find nearest centroid by cosine distance (= 1 - dot, both unit-normalized).
    let nearestId = '';
    let nearestDist = Infinity;
    for (const [id, centroid] of this.centroids) {
      const dot = dotProduct(embedding, centroid);
      const dist = 1 - dot;
      if (dist < nearestDist) {
        nearestDist = dist;
        nearestId = id;
      }
    }

    const canAllocateNew = this.maxSpeakers == null || this.centroids.size < this.maxSpeakers;
    if (nearestDist < this.threshold || !canAllocateNew) {
      // Assign to existing speaker; update centroid via EMA, re-normalize.
      const oldCentroid = this.centroids.get(nearestId)!;
      const updated = new Float32Array(oldCentroid.length);
      for (let i = 0; i < updated.length; i++) {
        updated[i] = this.emaAlpha * oldCentroid[i] + (1 - this.emaAlpha) * embedding[i];
      }
      normalizeInPlace(updated);
      this.centroids.set(nearestId, updated);
      return { speakerId: nearestId, distance: nearestDist, isNew: false };
    }

    // Allocate a fresh speaker.
    const newId = `speaker_${this.centroids.size}`;
    this.centroids.set(newId, copyAndNormalize(embedding));
    return { speakerId: newId, distance: nearestDist, isNew: true };
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
