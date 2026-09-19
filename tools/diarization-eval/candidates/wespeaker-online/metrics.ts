/**
 * Harness metrics — a single in-memory snapshot of everything we want to
 * observe live. Diarizer, server, and transcription wrappers push events
 * here; the HTTP /metrics endpoint and the dashboard WS broadcast read from
 * the same snapshot.
 *
 * Design goals:
 *   - One global instance per process; no DI ceremony for a tool.
 *   - Cheap O(1) updates on every event.
 *   - Histograms are fixed-bucket so the snapshot stays small.
 *   - Time-windowed rates (per-minute) so the dashboard shows live churn
 *     rather than session totals dominated by ancient activity.
 */

const NOW = () => Date.now();

/** Fixed-bucket histogram. Buckets are upper-edges. */
class FixedBucketHistogram {
  readonly buckets: number[];
  private readonly counts: number[];
  private samples = 0;
  private sum = 0;

  constructor(buckets: number[]) {
    this.buckets = buckets;
    this.counts = new Array(buckets.length + 1).fill(0); // +1 for overflow
  }

  observe(v: number): void {
    if (!Number.isFinite(v)) return;
    this.samples++;
    this.sum += v;
    for (let i = 0; i < this.buckets.length; i++) {
      if (v < this.buckets[i]) {
        this.counts[i]++;
        return;
      }
    }
    this.counts[this.buckets.length]++; // overflow
  }

  snapshot() {
    return {
      buckets: this.buckets,
      counts: [...this.counts],
      samples: this.samples,
      mean: this.samples > 0 ? this.sum / this.samples : 0,
    };
  }

  reset(): void {
    this.counts.fill(0);
    this.samples = 0;
    this.sum = 0;
  }
}

/** Rolling rate over the last `windowMs` of timestamps. */
class RollingRate {
  private readonly windowMs: number;
  private readonly events: number[] = [];

  constructor(windowMs: number) {
    this.windowMs = windowMs;
  }

  observe(ts = NOW()): void {
    this.events.push(ts);
    this.trim(ts);
  }

  /** Events per minute over the window. */
  perMinute(now = NOW()): number {
    this.trim(now);
    return (this.events.length / this.windowMs) * 60_000;
  }

  count(): number {
    return this.events.length;
  }

  private trim(now: number): void {
    const cutoff = now - this.windowMs;
    while (this.events.length > 0 && this.events[0] < cutoff) this.events.shift();
  }
}

/** Streaming p50/p95 via a simple ring buffer. Fine for small N. */
class LatencyTracker {
  private readonly capacity: number;
  private readonly ring: number[];
  private idx = 0;
  private filled = false;
  private count = 0;

  constructor(capacity = 256) {
    this.capacity = capacity;
    this.ring = new Array(capacity).fill(0);
  }

  observe(v: number): void {
    if (!Number.isFinite(v)) return;
    this.ring[this.idx] = v;
    this.idx = (this.idx + 1) % this.capacity;
    if (this.idx === 0) this.filled = true;
    this.count++;
  }

  snapshot() {
    const n = this.filled ? this.capacity : this.idx;
    if (n === 0) return { count: 0, p50: 0, p95: 0, mean: 0, last: 0 };
    const slice = this.ring.slice(0, n).sort((a, b) => a - b);
    const p = (q: number) => slice[Math.min(slice.length - 1, Math.floor(q * slice.length))];
    const sum = slice.reduce((a, b) => a + b, 0);
    return {
      count: this.count,
      p50: p(0.5),
      p95: p(0.95),
      mean: sum / slice.length,
      last: this.ring[(this.idx - 1 + this.capacity) % this.capacity],
    };
  }
}

export interface MetricsSnapshot {
  ts: number;
  session: {
    startedAt: number;
    elapsedMs: number;
  };
  diarizer: {
    commits: number;
    commitsPerMin: number;
    meanCommitDurMs: number;
    /** Number of times the change-point detector split an in-progress utterance. */
    changePoints: number;
    changePointsPerMin: number;
    /** Number of `peek refresh` events the diarizer issued. */
    peekRefreshes: number;
    peekRefreshesPerMin: number;
    /** Total cluster allocations + merges over the session. */
    clusterAllocations: number;
    clusterMerges: number;
    clusterAllocsPerMin: number;
    /** Current centroid count in the clusterer. */
    clusterCount: number;
    /** ONNX embedding inference latency, ms. */
    embedLatency: ReturnType<LatencyTracker['snapshot']>;
    /** Histogram of centroid_dist (cosine distance) at commit. */
    centroidDistHistogram: ReturnType<FixedBucketHistogram['snapshot']>;
    /** Histogram of turn_dist (utterance-to-utterance) at commit. */
    turnDistHistogram: ReturnType<FixedBucketHistogram['snapshot']>;
    /** Map speaker_id → committed audio time (ms). */
    perSpeakerCommittedMs: Record<string, number>;
  };
  routing: {
    /** Current depth of the harness's pendingFrames queue. */
    pendingFramesDepth: number;
    /** Largest pendingFrames depth seen this session. */
    pendingFramesMax: number;
    /** Frames added to pendingFrames. */
    framesIn: number;
    /** Frames routed into speakerManager via onCommit drain. */
    framesRouted: number;
    /** Frames dropped during deferred routing (older than committed range). */
    framesDropped: number;
    /** Frames flushed because the soft cap (overflow) hit. */
    framesOverflowed: number;
  };
  transcription: {
    requests: number;
    successes: number;
    retries: number;
    serviceBusy503: number;
    fatalErrors: number;
    /** Round-trip request latency, ms. */
    requestLatency: ReturnType<LatencyTracker['snapshot']>;
  };
  /** Time from speech-start (utterance.startTs) to first dashboard label
   *  bound to that utterance's speakerId. The number that pinpoints the
   *  "speaker switch lags" user complaint. */
  labelEmitLatency: ReturnType<LatencyTracker['snapshot']>;
}

class Metrics {
  private startedAt = NOW();

  // Diarizer
  private commits = 0;
  private commitsRate = new RollingRate(60_000);
  private commitDurSumMs = 0;
  private changePoints = 0;
  private changePointsRate = new RollingRate(60_000);
  private peekRefreshes = 0;
  private peekRefreshesRate = new RollingRate(60_000);
  private clusterAllocations = 0;
  private clusterMerges = 0;
  private clusterAllocsRate = new RollingRate(60_000);
  private clusterCount = 0;
  private embedLatency = new LatencyTracker(512);
  // Buckets tuned to typical cosine-distance ranges.
  private centroidDistHist = new FixedBucketHistogram([0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]);
  private turnDistHist = new FixedBucketHistogram([0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]);
  private perSpeakerCommittedMs = new Map<string, number>();

  // Routing
  private pendingFramesDepth = 0;
  private pendingFramesMax = 0;
  private framesIn = 0;
  private framesRouted = 0;
  private framesDropped = 0;
  private framesOverflowed = 0;

  // Transcription
  private txRequests = 0;
  private txSuccesses = 0;
  private txRetries = 0;
  private tx503 = 0;
  private txFatals = 0;
  private txLatency = new LatencyTracker(256);

  // Label-emit latency: utterance speech-start ts → onCommit fire ts.
  private labelEmitLatency = new LatencyTracker(256);

  reset(): void {
    this.startedAt = NOW();
    this.commits = 0;
    this.commitsRate = new RollingRate(60_000);
    this.commitDurSumMs = 0;
    this.changePoints = 0;
    this.changePointsRate = new RollingRate(60_000);
    this.peekRefreshes = 0;
    this.peekRefreshesRate = new RollingRate(60_000);
    this.clusterAllocations = 0;
    this.clusterMerges = 0;
    this.clusterAllocsRate = new RollingRate(60_000);
    this.clusterCount = 0;
    this.embedLatency = new LatencyTracker(512);
    this.centroidDistHist.reset();
    this.turnDistHist.reset();
    this.perSpeakerCommittedMs.clear();
    this.pendingFramesDepth = 0;
    this.pendingFramesMax = 0;
    this.framesIn = 0;
    this.framesRouted = 0;
    this.framesDropped = 0;
    this.framesOverflowed = 0;
    this.txRequests = 0;
    this.txSuccesses = 0;
    this.txRetries = 0;
    this.tx503 = 0;
    this.txFatals = 0;
    this.txLatency = new LatencyTracker(256);
    this.labelEmitLatency = new LatencyTracker(256);
  }

  // ---- Diarizer events ----------------------------------------------------

  recordCommit(args: {
    speakerId: string;
    durMs: number;
    centroidDist: number;
    turnDist: number;
    isNew: boolean;
    clusterCount: number;
    labelEmitLatencyMs: number;
  }): void {
    this.commits++;
    this.commitsRate.observe();
    this.commitDurSumMs += args.durMs;
    if (args.isNew) {
      this.clusterAllocations++;
      this.clusterAllocsRate.observe();
    }
    this.clusterCount = args.clusterCount;
    if (Number.isFinite(args.centroidDist)) this.centroidDistHist.observe(args.centroidDist);
    if (Number.isFinite(args.turnDist)) this.turnDistHist.observe(args.turnDist);
    this.perSpeakerCommittedMs.set(
      args.speakerId,
      (this.perSpeakerCommittedMs.get(args.speakerId) ?? 0) + args.durMs,
    );
    if (Number.isFinite(args.labelEmitLatencyMs) && args.labelEmitLatencyMs >= 0) {
      this.labelEmitLatency.observe(args.labelEmitLatencyMs);
    }
  }

  recordChangePoint(): void {
    this.changePoints++;
    this.changePointsRate.observe();
  }

  recordPeekRefresh(): void {
    this.peekRefreshes++;
    this.peekRefreshesRate.observe();
  }

  recordClusterMerge(n = 1): void {
    this.clusterMerges += n;
  }

  recordEmbedLatency(ms: number): void {
    this.embedLatency.observe(ms);
  }

  // ---- Routing events -----------------------------------------------------

  recordFrameIn(): void {
    this.framesIn++;
    this.pendingFramesDepth++;
    if (this.pendingFramesDepth > this.pendingFramesMax) this.pendingFramesMax = this.pendingFramesDepth;
  }

  recordFrameRouted(n = 1): void {
    this.framesRouted += n;
    this.pendingFramesDepth = Math.max(0, this.pendingFramesDepth - n);
  }

  recordFrameDropped(n = 1): void {
    this.framesDropped += n;
    this.pendingFramesDepth = Math.max(0, this.pendingFramesDepth - n);
  }

  recordFrameOverflow(n = 1): void {
    this.framesOverflowed += n;
    this.pendingFramesDepth = Math.max(0, this.pendingFramesDepth - n);
  }

  // ---- Transcription events ----------------------------------------------

  recordTranscription(args: { latencyMs: number; ok: boolean; busy503?: boolean; retries?: number; fatal?: boolean }): void {
    this.txRequests++;
    if (args.ok) this.txSuccesses++;
    if (args.fatal) this.txFatals++;
    if (args.busy503) this.tx503++;
    if (args.retries) this.txRetries += args.retries;
    if (Number.isFinite(args.latencyMs) && args.latencyMs >= 0) this.txLatency.observe(args.latencyMs);
  }

  // ---- Snapshot ----------------------------------------------------------

  snapshot(): MetricsSnapshot {
    const now = NOW();
    return {
      ts: now,
      session: {
        startedAt: this.startedAt,
        elapsedMs: now - this.startedAt,
      },
      diarizer: {
        commits: this.commits,
        commitsPerMin: this.commitsRate.perMinute(now),
        meanCommitDurMs: this.commits > 0 ? this.commitDurSumMs / this.commits : 0,
        changePoints: this.changePoints,
        changePointsPerMin: this.changePointsRate.perMinute(now),
        peekRefreshes: this.peekRefreshes,
        peekRefreshesPerMin: this.peekRefreshesRate.perMinute(now),
        clusterAllocations: this.clusterAllocations,
        clusterMerges: this.clusterMerges,
        clusterAllocsPerMin: this.clusterAllocsRate.perMinute(now),
        clusterCount: this.clusterCount,
        embedLatency: this.embedLatency.snapshot(),
        centroidDistHistogram: this.centroidDistHist.snapshot(),
        turnDistHistogram: this.turnDistHist.snapshot(),
        perSpeakerCommittedMs: Object.fromEntries(this.perSpeakerCommittedMs),
      },
      routing: {
        pendingFramesDepth: this.pendingFramesDepth,
        pendingFramesMax: this.pendingFramesMax,
        framesIn: this.framesIn,
        framesRouted: this.framesRouted,
        framesDropped: this.framesDropped,
        framesOverflowed: this.framesOverflowed,
      },
      transcription: {
        requests: this.txRequests,
        successes: this.txSuccesses,
        retries: this.txRetries,
        serviceBusy503: this.tx503,
        fatalErrors: this.txFatals,
        requestLatency: this.txLatency.snapshot(),
      },
      labelEmitLatency: this.labelEmitLatency.snapshot(),
    };
  }
}

/** Global singleton. */
export const metrics = new Metrics();
