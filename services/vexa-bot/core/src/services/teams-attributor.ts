/**
 * TeamsAttributor — correlates diarizer commits with Teams DOM caption
 * events to produce final speaker labels.
 *
 * Architecture (per pack epic #394):
 *
 *   audio frames ─→ diarizer.process(...) ─→ commit events with boundaries + cluster IDs
 *                                                  │
 *                                                  ▼
 *   Teams DOM captions ──→ attributor.recordCaption(...)
 *                                                  │
 *                                                  ▼
 *                       attributor.resolve(commit) → { speakerName, source }
 *                                                  │
 *                                                  ▼
 *                       speakerManager.feedAudio(speakerName, audio)
 *
 * Two-signal model:
 *
 *   1. Diarizer says WHEN the speaker changed (boundary + cluster ID).
 *      Cluster IDs are PROVISIONAL — they're stable per-session but
 *      meaningless (`speaker_0`, `speaker_1`, …).
 *
 *   2. Teams DOM captions say WHO is speaking at any wall-clock time.
 *      Caption stream is noisy: ~500-1500 ms lag behind actual audio,
 *      occasional flickers (someone else's open mic), sometimes absent
 *      (captions disabled, language unsupported).
 *
 * Resolution strategy per commit:
 *
 *   a. WINDOW MATCH: find captions whose [tStart, tEnd] (lag-shifted)
 *      overlap the commit's [tStart, tEnd]. The speaker with the most
 *      overlap-ms wins. This is the "best candidate for speaker
 *      attribution" the operator specified.
 *
 *   b. CLUSTER VOTE: if (a) returns null (no caption overlap in the
 *      window), look at ALL prior commits with the same cluster ID;
 *      take the majority caption name. This handles caption gaps:
 *      once a cluster has accumulated some caption evidence, all its
 *      commits inherit the resolved name.
 *
 *   c. PROVISIONAL: if (b) also returns null (caption gap on the first
 *      commit of a cluster), publish with the cluster ID as the
 *      speaker name. Later, when a caption resolves that cluster, fire
 *      the `onLateResolve` callback so the caller can run
 *      `speakerManager.updateSpeakerName(cluster_id, real_name)`
 *      on the production buffer.
 *
 * No fallback to the legacy caption-driven flush. Cluster IDs are
 * legitimate published speaker names until captions arrive.
 *
 * Pack: #394 (pack-msteams-diarization-cutover).
 */

const DEFAULT_CAPTION_LAG_MS = 1000;
/** How long after a caption event we still consider it a candidate for
 *  matching a commit's time range. Longer windows accommodate the lag
 *  between speech and Teams' caption ASR. */
const DEFAULT_MATCH_TOLERANCE_MS = 2500;
/** Cap the in-memory caption log so a long meeting doesn't grow it
 *  unboundedly. The cluster-vote rule only needs ~last N captions to
 *  resolve a cluster's identity. */
const DEFAULT_CAPTION_LOG_LIMIT = 2000;

export interface TeamsAttributorConfig {
  /** Estimated caption-stream lag in ms. Used to shift caption
   *  timestamps before window-matching against commit ranges. */
  captionLagMs?: number;
  /** ± slack added to commit window when scanning captions. */
  matchToleranceMs?: number;
  /** Max captions to retain in memory. */
  captionLogLimit?: number;
  /** Fired when (a) a commit was published with a cluster ID because no
   *  caption was available, then (b) a later caption resolves that
   *  cluster's identity. Caller is expected to call
   *  speakerManager.updateSpeakerName(clusterId, resolvedName). */
  onLateResolve?: (clusterId: string, resolvedName: string) => void;
}

export interface CaptionEvent {
  /** The display name shown in the Teams caption tile. */
  speakerName: string;
  /** Wall-clock ms when this caption event arrived. */
  tMs: number;
  /** Optional caption text — kept for telemetry / debugging only.
   *  Attribution doesn't use the text. */
  text?: string;
}

export interface CommitInfo {
  /** Diarizer's provisional cluster ID — stable per-session, not a
   *  display name. */
  clusterId: string;
  /** Audio-time start of the commit (wall-clock ms in the same timebase
   *  as caption events). */
  tStartMs: number;
  /** Audio-time end of the commit. */
  tEndMs: number;
}

export interface ResolvedAttribution {
  /** Final speaker name. Either a real display name (from caption
   *  match or cluster vote) or the cluster ID itself (provisional). */
  speakerName: string;
  /** Why we picked this name. Useful for telemetry + post-hoc
   *  attribution audits. */
  source: 'window-match' | 'cluster-vote' | 'provisional-cluster-id';
  /** Confidence in [0, 1]. 1.0 for unambiguous window matches; lower
   *  for cluster-vote majorities; ~0 for provisional. */
  confidence: number;
}

export class TeamsAttributor {
  private readonly captionLagMs: number;
  private readonly matchToleranceMs: number;
  private readonly captionLogLimit: number;
  private readonly onLateResolve?: (clusterId: string, resolvedName: string) => void;

  /** Append-only log of caption events. Cleaned to `captionLogLimit`. */
  private captionLog: CaptionEvent[] = [];

  /** clusterId → name we previously emitted for that cluster. Used to
   *  detect when a later caption resolves a cluster that previously
   *  published with its raw cluster ID. */
  private clusterLastResolvedName = new Map<string, string>();

  /** clusterId → list of caption names accumulated from commits we've
   *  resolved on that cluster. The mode of this list is the
   *  cluster-vote winner. */
  private clusterVoteHistory = new Map<string, Map<string, number>>();

  constructor(cfg: TeamsAttributorConfig = {}) {
    this.captionLagMs = cfg.captionLagMs ?? DEFAULT_CAPTION_LAG_MS;
    this.matchToleranceMs = cfg.matchToleranceMs ?? DEFAULT_MATCH_TOLERANCE_MS;
    this.captionLogLimit = cfg.captionLogLimit ?? DEFAULT_CAPTION_LOG_LIMIT;
    this.onLateResolve = cfg.onLateResolve;
  }

  /** Record a Teams caption event. Called from handleTeamsCaptionData. */
  recordCaption(speakerName: string, tMs: number, text?: string): void {
    if (!speakerName) return;
    this.captionLog.push({ speakerName, tMs, text });
    if (this.captionLog.length > this.captionLogLimit) {
      this.captionLog.splice(0, this.captionLog.length - this.captionLogLimit);
    }
    // A late caption may now resolve a cluster that previously emitted
    // with a provisional cluster_id. We don't fire onLateResolve here
    // — the right moment is when we OBSERVE that the cluster's vote
    // majority changed (see updateClusterVote). But we do scan recent
    // commits and update the vote tallies retroactively.
    this.maybeResolveRetroactively(speakerName, tMs);
  }

  /** Resolve a commit to its final speaker name. */
  resolve(commit: CommitInfo): ResolvedAttribution {
    // (a) Window match.
    const winnerByOverlap = this.windowMatch(commit);
    if (winnerByOverlap) {
      this.updateClusterVote(commit.clusterId, winnerByOverlap.name);
      return {
        speakerName: winnerByOverlap.name,
        source: 'window-match',
        confidence: winnerByOverlap.confidence,
      };
    }
    // (b) Cluster vote.
    const winnerByVote = this.clusterMajority(commit.clusterId);
    if (winnerByVote) {
      return {
        speakerName: winnerByVote.name,
        source: 'cluster-vote',
        confidence: winnerByVote.confidence,
      };
    }
    // (c) Provisional cluster ID.
    return {
      speakerName: commit.clusterId,
      source: 'provisional-cluster-id',
      confidence: 0,
    };
  }

  /** Window-match: find captions overlapping [tStart-lag-tolerance,
   *  tEnd-lag+tolerance], tally overlap-ms per speaker, return winner. */
  private windowMatch(commit: CommitInfo): { name: string; confidence: number } | null {
    const windowStart = commit.tStartMs + this.captionLagMs - this.matchToleranceMs;
    const windowEnd = commit.tEndMs + this.captionLagMs + this.matchToleranceMs;
    const overlapMs = new Map<string, number>();
    // Each caption event is conceptually a START of a speaker turn.
    // Use the NEXT caption (for any speaker) as the end of this turn.
    const sorted = [...this.captionLog].sort((a, b) => a.tMs - b.tMs);
    for (let i = 0; i < sorted.length; i++) {
      const cap = sorted[i];
      const next = sorted[i + 1];
      // Caption is "active" from cap.tMs to (next.tMs ?? cap.tMs + 5000).
      const capActiveStart = cap.tMs;
      const capActiveEnd = next ? next.tMs : cap.tMs + 5000;
      const o = Math.max(0, Math.min(capActiveEnd, windowEnd) - Math.max(capActiveStart, windowStart));
      if (o <= 0) continue;
      overlapMs.set(cap.speakerName, (overlapMs.get(cap.speakerName) ?? 0) + o);
    }
    if (overlapMs.size === 0) return null;
    let bestName = '';
    let bestMs = 0;
    let totalMs = 0;
    for (const [name, ms] of overlapMs) {
      totalMs += ms;
      if (ms > bestMs) {
        bestMs = ms;
        bestName = name;
      }
    }
    if (!bestName) return null;
    const confidence = totalMs > 0 ? bestMs / totalMs : 0;
    return { name: bestName, confidence };
  }

  /** Cluster vote: among all caption resolutions previously seen for
   *  this cluster, which speaker name wins by count? */
  private clusterMajority(clusterId: string): { name: string; confidence: number } | null {
    const tally = this.clusterVoteHistory.get(clusterId);
    if (!tally || tally.size === 0) return null;
    let bestName = '';
    let bestCount = 0;
    let total = 0;
    for (const [name, count] of tally) {
      total += count;
      if (count > bestCount) {
        bestCount = count;
        bestName = name;
      }
    }
    if (!bestName) return null;
    return { name: bestName, confidence: total > 0 ? bestCount / total : 0 };
  }

  private updateClusterVote(clusterId: string, speakerName: string): void {
    if (!this.clusterVoteHistory.has(clusterId)) {
      this.clusterVoteHistory.set(clusterId, new Map());
    }
    const tally = this.clusterVoteHistory.get(clusterId)!;
    tally.set(speakerName, (tally.get(speakerName) ?? 0) + 1);
    // Check for late-resolve: if this cluster previously published with
    // its raw cluster_id but now has a majority caption name, fire the
    // callback so the caller can call updateSpeakerName.
    const prevResolved = this.clusterLastResolvedName.get(clusterId);
    const majority = this.clusterMajority(clusterId);
    if (majority && majority.name !== prevResolved && majority.name !== clusterId) {
      // Did we EVER publish a commit on this cluster with its raw ID? We
      // don't track every emission explicitly; instead, the heuristic is:
      // if prevResolved is undefined OR equals clusterId, fire the
      // resolve callback. The caller is idempotent (calling
      // updateSpeakerName with the same name twice is a no-op).
      if (prevResolved === undefined || prevResolved === clusterId) {
        this.onLateResolve?.(clusterId, majority.name);
      }
      this.clusterLastResolvedName.set(clusterId, majority.name);
    }
  }

  /** When a late caption arrives, retroactively credit it to nearby
   *  cluster votes. We don't know which cluster the new caption
   *  belongs to without a commit to map it through, but if a previous
   *  commit's window-match would now succeed with this new caption in
   *  the log, future calls to resolve() will pick it up. This is a no-op
   *  in the current implementation; left as a hook for richer
   *  retro-attribution. */
  private maybeResolveRetroactively(_speakerName: string, _tMs: number): void {
    // No-op for now. Retro-attribution of past commits requires the
    // caller to re-call resolve() on commits whose names should
    // potentially update — that's the late-rename flow. The
    // onLateResolve fires when a NEW commit reveals a cluster's
    // identity, which is the dominant case.
  }

  /** Diagnostic accessor — number of caption events currently buffered. */
  captionCount(): number {
    return this.captionLog.length;
  }

  /** Diagnostic accessor — how many clusters have at least one resolved
   *  caption vote. */
  clusterCount(): number {
    return this.clusterVoteHistory.size;
  }

  /** Reset state on new session. */
  reset(): void {
    this.captionLog.length = 0;
    this.clusterLastResolvedName.clear();
    this.clusterVoteHistory.clear();
  }
}
