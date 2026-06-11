/**
 * ClusterNameBinder — converges two unreliable signals into speaker names:
 *
 *   1. The DIARIZER says WHEN the speaker changed (turn boundaries + stable
 *      per-session cluster ids). Cluster ids are PROVISIONAL — stable but
 *      meaningless ("speaker_0", "speaker_1", …).
 *   2. The platform UI says WHO is speaking around some wall-clock time, via
 *      one or more HINT streams, each with its own latency and failure modes:
 *        - 'dom-active'  : Zoom active-speaker DOM poll   (~250 ms lag, null
 *                          gaps between speakers, selector rot)
 *        - 'caption'     : Teams caption events           (~500–1500 ms lag,
 *                          flicker, absent when captions are off)
 *        - 'dom-outline' : Teams voice-outline transitions (~200 ms lag,
 *                          flicker, vanishes when video tiles change)
 *
 * Resolution per diarizer commit (generalized verbatim from the pack's
 * TeamsAttributor — pack-msteams-diarization-cutover #394):
 *
 *   a. WINDOW MATCH: find hint turns whose lag-shifted [tStart, tEnd] overlap
 *      the commit's [tStart, tEnd]; the name with the most overlap-ms wins.
 *   b. CLUSTER VOTE: no overlap → majority of names previously resolved for
 *      this cluster id (a cluster keeps its identity through hint gaps).
 *   c. PROVISIONAL: neither → publish the cluster id itself; when a later
 *      commit resolves the cluster, `onLateResolve` fires so the caller runs
 *      speakerManager.updateSpeakerName(clusterId, realName) and the already-
 *      published segments self-correct (stable segment_id + collector UPSERT).
 *
 * Hint turn model: a hint event marks the START of that name's turn; the turn
 * ends at the next hint of the same kind (any name), at an explicit end event
 * (`isEnd`), or after MAX_TURN_MS. This is exactly the caption-log model from
 * the pack, applied uniformly to every hint kind.
 */

export type HintKind = 'dom-active' | 'caption' | 'dom-outline';

/** Per-kind lag: how far the UI signal trails the actual audio (ms). Hint
 *  timestamps are shifted back by this amount before matching. */
const KIND_LAG_MS: Record<HintKind, number> = {
  'dom-active': 250,
  'caption': 1000,
  'dom-outline': 200,
};

/** Open turns (no successor, no explicit end) are STILL ACTIVE — the lit
 *  signal sends an explicit end (isEnd) when the speaker stops, so an open
 *  turn extends until then. Used only as the open-turn horizon. */
const OPEN_TURN_HORIZON_MS = Number.MAX_SAFE_INTEGER;
const DEFAULT_MATCH_TOLERANCE_MS = 2500;
const DEFAULT_HINT_LOG_LIMIT = 2000;

export interface HintEvent {
  /** Display name from the platform UI. */
  name: string;
  /** Wall-clock ms when the signal was observed (same timebase as commits). */
  tMs: number;
  kind: HintKind;
  /** Explicit turn end (e.g. Teams SPEAKER_END). Ends the name's open turn
   *  instead of starting a new one. */
  isEnd?: boolean;
}

export interface CommitInfo {
  /** Diarizer's provisional cluster id — stable per-session. */
  clusterId: string;
  /** Audio-time start/end of the commit, wall-clock ms. */
  tStartMs: number;
  tEndMs: number;
}

export interface ResolvedAttribution {
  /** Real display name, or the cluster id itself (provisional). */
  speakerName: string;
  source: 'window-match' | 'cluster-vote' | 'provisional-cluster-id';
  /** 1.0-ish for unambiguous window matches; lower for vote majorities; 0 provisional. */
  confidence: number;
}

export interface ClusterNameBinderConfig {
  /** Override per-kind lag (ms). Merged over defaults. */
  kindLagMs?: Partial<Record<HintKind, number>>;
  /** ± slack added to the commit window when scanning hints. */
  matchToleranceMs?: number;
  /** Max hint turns retained per kind. */
  hintLogLimit?: number;
  /** Fired when a cluster that published provisionally gains a majority name.
   *  Caller runs speakerManager.updateSpeakerName(clusterId, name) — idempotent. */
  onLateResolve?: (clusterId: string, resolvedName: string) => void;
}

interface HintTurn {
  name: string;
  /** Lag-corrected start (ms). */
  tStartMs: number;
  /** Lag-corrected end; undefined while the turn is open. */
  tEndMs?: number;
}

export class ClusterNameBinder {
  private readonly lag: Record<HintKind, number>;
  private readonly matchToleranceMs: number;
  private readonly hintLogLimit: number;
  private readonly onLateResolve?: (clusterId: string, resolvedName: string) => void;

  /** Per-kind turn logs (append-only, trimmed to hintLogLimit). */
  private turns = new Map<HintKind, HintTurn[]>();

  private clusterLastResolvedName = new Map<string, string>();
  private clusterVoteHistory = new Map<string, Map<string, number>>();

  constructor(cfg: ClusterNameBinderConfig = {}) {
    this.lag = { ...KIND_LAG_MS, ...(cfg.kindLagMs || {}) };
    this.matchToleranceMs = cfg.matchToleranceMs ?? DEFAULT_MATCH_TOLERANCE_MS;
    this.hintLogLimit = cfg.hintLogLimit ?? DEFAULT_HINT_LOG_LIMIT;
    this.onLateResolve = cfg.onLateResolve;
  }

  /** Record one platform hint event. */
  recordHint(ev: HintEvent): void {
    if (!ev.name && !ev.isEnd) return;
    let log = this.turns.get(ev.kind);
    if (!log) { log = []; this.turns.set(ev.kind, log); }
    const t = ev.tMs - this.lag[ev.kind];

    const open = log.length > 0 ? log[log.length - 1] : null;
    if (ev.isEnd) {
      // Close the matching open turn (or the latest open one if names match loosely).
      if (open && open.tEndMs === undefined && (!ev.name || open.name === ev.name)) {
        open.tEndMs = t;
      }
      return;
    }
    // A new hint of this kind ends the previous open turn of the SAME kind.
    if (open && open.tEndMs === undefined) open.tEndMs = t;
    log.push({ name: ev.name, tStartMs: t });
    if (log.length > this.hintLogLimit) log.splice(0, log.length - this.hintLogLimit);
  }

  /** LIT-ONLY resolution (experiment, operator-decided): the name with the
   *  maximum hint-overlap for this time span — no cluster votes, no
   *  provisional ids. Returns null when no hint overlaps the window. */
  bestOverlapName(commit: { tStartMs: number; tEndMs: number }): { name: string; confidence: number } | null {
    return this.windowMatch({ clusterId: '', tStartMs: commit.tStartMs, tEndMs: commit.tEndMs });
  }

  /** Resolve a diarizer commit to its final speaker name. */
  resolve(commit: CommitInfo): ResolvedAttribution {
    const winnerByOverlap = this.windowMatch(commit);
    if (winnerByOverlap) {
      this.updateClusterVote(commit.clusterId, winnerByOverlap.name);
      return { speakerName: winnerByOverlap.name, source: 'window-match', confidence: winnerByOverlap.confidence };
    }
    const winnerByVote = this.clusterMajority(commit.clusterId);
    if (winnerByVote) {
      return { speakerName: winnerByVote.name, source: 'cluster-vote', confidence: winnerByVote.confidence };
    }
    return { speakerName: commit.clusterId, source: 'provisional-cluster-id', confidence: 0 };
  }

  private windowMatch(commit: CommitInfo): { name: string; confidence: number } | null {
    // Hints are already lag-corrected at insert, so the commit window only
    // needs tolerance slack.
    const windowStart = commit.tStartMs - this.matchToleranceMs;
    const windowEnd = commit.tEndMs + this.matchToleranceMs;
    const overlapMs = new Map<string, number>();

    for (const log of this.turns.values()) {
      for (const turn of log) {
        const turnEnd = turn.tEndMs ?? OPEN_TURN_HORIZON_MS;
        const o = Math.max(0, Math.min(turnEnd, windowEnd) - Math.max(turn.tStartMs, windowStart));
        if (o <= 0) continue;
        overlapMs.set(turn.name, (overlapMs.get(turn.name) ?? 0) + o);
      }
    }
    if (overlapMs.size === 0) return null;

    let bestName = '';
    let bestMs = 0;
    let totalMs = 0;
    for (const [name, ms] of overlapMs) {
      totalMs += ms;
      if (ms > bestMs) { bestMs = ms; bestName = name; }
    }
    if (!bestName) return null;
    return { name: bestName, confidence: totalMs > 0 ? bestMs / totalMs : 0 };
  }

  private clusterMajority(clusterId: string): { name: string; confidence: number } | null {
    const tally = this.clusterVoteHistory.get(clusterId);
    if (!tally || tally.size === 0) return null;
    let bestName = '';
    let bestCount = 0;
    let total = 0;
    for (const [name, count] of tally) {
      total += count;
      if (count > bestCount) { bestCount = count; bestName = name; }
    }
    if (!bestName) return null;
    return { name: bestName, confidence: total > 0 ? bestCount / total : 0 };
  }

  private updateClusterVote(clusterId: string, speakerName: string): void {
    if (!this.clusterVoteHistory.has(clusterId)) this.clusterVoteHistory.set(clusterId, new Map());
    const tally = this.clusterVoteHistory.get(clusterId)!;
    tally.set(speakerName, (tally.get(speakerName) ?? 0) + 1);

    const prevResolved = this.clusterLastResolvedName.get(clusterId);
    const majority = this.clusterMajority(clusterId);
    if (majority && majority.name !== prevResolved && majority.name !== clusterId) {
      // Fire late-resolve when the cluster previously had no real name (was
      // provisional). Caller's updateSpeakerName is idempotent.
      if (prevResolved === undefined || prevResolved === clusterId) {
        this.onLateResolve?.(clusterId, majority.name);
      }
      this.clusterLastResolvedName.set(clusterId, majority.name);
    }
  }

  /** Diagnostics for telemetry. */
  stats(): { hintTurns: Record<string, number>; clustersWithVotes: number; resolvedClusters: number } {
    const hintTurns: Record<string, number> = {};
    for (const [kind, log] of this.turns) hintTurns[kind] = log.length;
    return {
      hintTurns,
      clustersWithVotes: this.clusterVoteHistory.size,
      resolvedClusters: this.clusterLastResolvedName.size,
    };
  }

  reset(): void {
    this.turns.clear();
    this.clusterLastResolvedName.clear();
    this.clusterVoteHistory.clear();
  }
}
