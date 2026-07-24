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
 *        - 'jitsi-dominant': Jitsi's exclusive, smoothed global dominant-speaker
 *                          state. Same-name repeats are liveness heartbeats; only
 *                          an ordered state transition is identity testimony.
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
 * Zoom/caption/Teams hints use the overlap model below. Jitsi is deliberately
 * separate: its global dominant-speaker signal testifies to ORDER, not acoustic
 * onset time, so it is bound by ordered transition tokens and never enters the
 * overlap log.
 */

export type HintKind = 'dom-active' | 'jitsi-dominant' | 'caption' | 'dom-outline';

/** Per-kind lag: how far the UI signal trails the actual audio (ms). Hint
 *  timestamps are shifted back by this amount before matching. */
const KIND_LAG_MS: Record<HintKind, number> = {
  'dom-active': 250,
  // Exclusive Jitsi transitions are ordinal testimony. This value is not used
  // for matching, but keeping it explicit prevents accidental fallback to the
  // Zoom envelope if a caller overrides kind lags.
  'jitsi-dominant': 0,
  'caption': 1000,
  'dom-outline': 200,
};

/** How long an OPEN hint turn (no successor / no explicit end) stays valid past
 *  its start. The capture layer re-asserts the active speaker every ~2s (the Zoom/
 *  Teams heartbeat), so a speaker who is STILL talking keeps refreshing this window;
 *  one who STOPPED decays after this grace and no longer out-votes the new speaker.
 *  Must exceed the heartbeat interval (tolerate a missed beat) — 2× = 4s. This is
 *  what gives a NEW active speaker priority over the previous one's lingering hint. */
const OPEN_TURN_GRACE_MS = 4000;
const DEFAULT_MATCH_TOLERANCE_MS = 2500;
/** Overlap difference (ms) within which two candidate names count as a "tie" and
 *  recency decides — prevents a previous speaker's still-open hint turn from
 *  out-voting the current speaker before the latter's own hint has accumulated. */
const RECENCY_TIE_MS = 1000;
const DEFAULT_HINT_LOG_LIMIT = 2000;
/** Vote-count lead a challenger name needs over the cluster's current name to
 *  flip it — hysteresis against hint flicker (continuous re-resolve). */
const NAME_SWITCH_MARGIN = 2;
const envNumber = (name: string, fallback: number): number => {
  const raw = typeof process !== 'undefined' ? process.env?.[name] : undefined;
  const n = raw !== undefined ? Number(raw) : NaN;
  return Number.isFinite(n) ? n : fallback;
};
/** A hint must cover enough of the actual commit span before it is allowed to
 *  name a turn. The ±match tolerance decides which hint turns are candidates,
 *  but support is measured mostly inside the speech span so a stale/incorrect UI
 *  switch near the edge cannot win solely because it was the only hint present. */
const MIN_MATCH_COVERAGE = envNumber('VEXA_HINT_MIN_COVERAGE', 0.35);
const MIN_MATCH_SUPPORT_MS = envNumber('VEXA_HINT_MIN_SUPPORT_MS', 450);
const MIN_MATCH_CONFIDENCE = envNumber('VEXA_HINT_MIN_CONFIDENCE', 0.6);
const HINT_SUPPORT_SLACK_MS = envNumber('VEXA_HINT_SUPPORT_SLACK_MS', 500);
/** Active-speaker FLICKER debounce. A hint turn that opened AND closed in less than
 *  this window is a transient — a noise burst lighting a tile, a UI blip — never a real
 *  speaking turn. Counting its overlap lets it hijack attribution mid-utterance (the
 *  reproduced spk-Dmitry steal). So a CLOSED turn shorter than this contributes no
 *  overlap and no vote. OPEN (still-lit) turns are exempt — a speaker who is still
 *  talking keeps the tile lit and the heartbeat re-asserts it every ~2s, so legit turns
 *  always close well above this floor. Tunable via VEXA_FLICKER_MIN_MS. */
const FLICKER_MIN_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_FLICKER_MIN_MS) || 1000);

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
  source: 'window-match' | 'exclusive-transition' | 'cluster-vote' | 'provisional-cluster-id';
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

export interface ResolveOptions {
  recordVote?: boolean;
  /** Whether the acoustic turn's end is final. Exclusive-trailing testimony
   *  never names an open turn, even when its current tEnd happens to equal tStart. */
  finalized?: boolean;
}

/** Why a window-match returned null (or that it didn't) — per-commit, for the
 *  attribution instruments (#849/#852). `no-hint-overlap` = no hint turn touched
 *  the tolerance window at all; the gate reasons mean a candidate existed and a
 *  specific threshold rejected it. */
export interface MatchDiagnostics {
  result: { name: string; confidence: number } | null;
  reject?: 'no-hint-overlap' | 'support' | 'coverage' | 'confidence';
  /** Distinct candidate names that overlapped the window. */
  candidates: number;
  /** Hint turns skipped by the flicker debounce while scanning this window. */
  flickerSkipped: number;
  best?: { name: string; supportMs: number; coverage: number; confidence: number };
}

interface HintTurn {
  name: string;
  /** Lag-corrected start (ms). */
  tStartMs: number;
  /** Lag-corrected end; undefined while the turn is open. */
  tEndMs?: number;
}

interface RegisteredAcousticTurn {
  clusterId: string;
  tStartMs: number;
  tEndMs?: number;
}

interface ExclusiveTransition {
  id: number;
  fromName: string;
  toName: string;
  /** Start of this signal epoch: the preceding distinct observation. */
  epochStartMs: number;
  /** Observation time of this distinct transition. */
  observedAtMs: number;
}

interface JitsiDominantState {
  currentName: string | null;
  lastName: string | null;
  ended: boolean;
  lastEndMs: number;
  lastTransitionMs: number;
  observedThroughMs: number;
  observations: number;
  nextTransitionId: number;
  transitions: ExclusiveTransition[];
  /** False once transition custody was compacted. Missing history can only
   *  reduce attribution to unknown; it may never manufacture uniqueness. */
  historyComplete: boolean;
  /** A late distinct observation contradicts the ordered producer contract.
   *  Stale same-state/outgoing heartbeats are harmless and do not set this. */
  orderAmbiguous: boolean;
}

export class ClusterNameBinder {
  private readonly lag: Record<HintKind, number>;
  private readonly matchToleranceMs: number;
  private readonly hintLogLimit: number;
  /** Fires on EVERY accepted cluster-attribution change, including a causal
   *  invalidation back to the provisional cluster id. The caller repaints that
   *  cluster's pending + published segments. Settable post-construction (the
   *  host wires it after creating the binder as a field). */
  onLateResolve?: (clusterId: string, resolvedName: string) => void;

  /** Per-kind turn logs (append-only, trimmed to hintLogLimit). */
  private turns = new Map<HintKind, HintTurn[]>();
  /** Acoustic turns are registered independently of hint arrival. Jitsi
   *  transition admission is computed over the whole retained turn set: one
   *  transition may claim exactly one ordinal turn, and one turn may be
   *  claimed by exactly one transition. */
  private acousticTurns = new Map<string, RegisteredAcousticTurn>();
  /** Acoustic registration watermark. A closed turn is not admissible until
   *  the boundary producer has sealed its complete interval. */
  private acousticSealedThroughMs = -Infinity;
  /** Compaction or a registration behind the sealed watermark destroys the
   *  proof of global uniqueness. The session then remains fail-closed. */
  private acousticHistoryComplete = true;
  private acousticOrderAmbiguous = false;
  private jitsiDominant: JitsiDominantState | null = null;

  private clusterLastResolvedName = new Map<string, string>();
  /** Names admitted specifically through exclusive-transition custody. These
   *  are the only attributions revoked if later custody proves incomplete. */
  private exclusiveAttributions = new Map<string, string>();
  private clusterVoteHistory = new Map<string, Map<string, number>>();

  constructor(cfg: ClusterNameBinderConfig = {}) {
    this.lag = { ...KIND_LAG_MS, ...(cfg.kindLagMs || {}) };
    this.matchToleranceMs = cfg.matchToleranceMs ?? DEFAULT_MATCH_TOLERANCE_MS;
    this.hintLogLimit = cfg.hintLogLimit ?? DEFAULT_HINT_LOG_LIMIT;
    this.onLateResolve = cfg.onLateResolve;
  }

  /** Record one platform hint event. MULTI-SPEAKER: hint turns are per-NAME and may
   *  overlap — several speakers can be active at once (Teams lights multiple blue
   *  squares; the heartbeat re-asserts each). A hint refreshes only ITS OWN name's
   *  open turn; other speakers' turns stay open so none is lost when several queue
   *  or overlap. Stopped speakers fall away via `isEnd` or OPEN_TURN_GRACE_MS. */
  recordHint(ev: HintEvent): void {
    if (!ev.name && !ev.isEnd) return;
    if (ev.kind === 'jitsi-dominant') {
      this.recordJitsiDominant(ev);
      return;
    }
    let log = this.turns.get(ev.kind);
    if (!log) { log = []; this.turns.set(ev.kind, log); }
    const t = ev.tMs - this.lag[ev.kind];

    if (ev.isEnd) {
      // Close this name's open turn (or the latest open one if no name was given).
      for (let i = log.length - 1; i >= 0; i--) {
        const turn = log[i];
        if (turn.tEndMs !== undefined) continue;
        if (!ev.name || turn.name === ev.name) { turn.tEndMs = t; if (ev.name) break; }
      }
      return;
    }
    // Refresh THIS name's open turn (close + reopen so its window stays fresh against
    // the grace), leaving OTHER names' open turns untouched → concurrent speakers.
    for (let i = log.length - 1; i >= 0; i--) {
      const turn = log[i];
      if (turn.name === ev.name && turn.tEndMs === undefined) { turn.tEndMs = t; break; }
    }
    log.push({ name: ev.name, tStartMs: t });
    if (log.length > this.hintLogLimit) log.splice(0, log.length - this.hintLogLimit);
  }

  /** Resolve a diarizer commit to its final speaker name. */
  resolve(commit: CommitInfo, opts: ResolveOptions = {}): ResolvedAttribution {
    const recordVote = opts.recordVote ?? true;
    const finalized = opts.finalized ?? true;
    if (this.jitsiDominant) {
      const exclusive = this.resolveJitsiDominant(commit, finalized);
      if (exclusive) {
        if (recordVote) this.confirmExclusiveAttribution(commit.clusterId, exclusive.name);
        return {
          speakerName: exclusive.name,
          source: 'exclusive-transition',
          confidence: 1,
        };
      }
      // Once this session carries Jitsi's exclusive signal, overlap and cluster
      // majority are causally inadmissible fallbacks. Unknown is the result.
      return { speakerName: commit.clusterId, source: 'provisional-cluster-id', confidence: 0 };
    }
    const winnerByOverlap = this.windowMatch(commit);
    if (winnerByOverlap) {
      if (recordVote) this.recordClusterVote(commit.clusterId, winnerByOverlap.name);
      return { speakerName: winnerByOverlap.name, source: 'window-match', confidence: winnerByOverlap.confidence };
    }
    const winnerByVote = this.clusterMajority(commit.clusterId);
    if (winnerByVote) {
      return { speakerName: winnerByVote.name, source: 'cluster-vote', confidence: winnerByVote.confidence };
    }
    return { speakerName: commit.clusterId, source: 'provisional-cluster-id', confidence: 0 };
  }

  /** True once the Jitsi-exclusive contract is active for this binder. */
  hasHintKind(kind: HintKind): boolean {
    return kind === 'jitsi-dominant'
      ? this.jitsiDominant !== null
      : (this.turns.get(kind)?.length ?? 0) > 0;
  }

  /** Register an acoustic turn independently of STT completion. Callers seal
   *  the boundary timeline with `sealAcousticThrough` only after every turn
   *  starting before that instant has been registered. */
  registerAcousticTurn(commit: CommitInfo, finalized = false): void {
    let turn = this.acousticTurns.get(commit.clusterId);
    if (!turn) {
      if (commit.tStartMs < this.acousticSealedThroughMs) {
        this.acousticOrderAmbiguous = true;
      }
      turn = {
        clusterId: commit.clusterId,
        tStartMs: commit.tStartMs,
      };
      this.acousticTurns.set(commit.clusterId, turn);
    } else {
      const earlierStart = Math.min(turn.tStartMs, commit.tStartMs);
      if (earlierStart < turn.tStartMs && earlierStart < this.acousticSealedThroughMs) {
        this.acousticOrderAmbiguous = true;
      }
      turn.tStartMs = earlierStart;
    }
    if (finalized) {
      if (
        turn.tEndMs !== undefined
        && turn.tEndMs !== commit.tEndMs
        && Math.min(turn.tEndMs, commit.tEndMs) < this.acousticSealedThroughMs
      ) {
        this.acousticOrderAmbiguous = true;
      }
      turn.tEndMs = turn.tEndMs === undefined
        ? commit.tEndMs
        : Math.max(turn.tEndMs, commit.tEndMs);
    }

    if (this.acousticTurns.size > this.hintLogLimit) {
      this.acousticHistoryComplete = false;
      for (const [clusterId] of this.acousticTurns) {
        this.acousticTurns.delete(clusterId);
        if (this.acousticTurns.size <= this.hintLogLimit) break;
      }
    }
    if (!this.acousticHistoryComplete || this.acousticOrderAmbiguous) {
      this.invalidateExclusiveAttributions();
    }
  }

  /** Advance the acoustic registration watermark. This is boundary custody,
   *  not a timing heuristic: all turns before `tMs` must already be present. */
  sealAcousticThrough(tMs: number): void {
    if (tMs < this.acousticSealedThroughMs) {
      this.acousticOrderAmbiguous = true;
      this.invalidateExclusiveAttributions();
      return;
    }
    this.acousticSealedThroughMs = tMs;
  }

  private recordJitsiDominant(ev: HintEvent): void {
    let state = this.jitsiDominant;
    if (!state) {
      state = {
        currentName: null,
        lastName: null,
        ended: false,
        lastEndMs: -Infinity,
        lastTransitionMs: -Infinity,
        observedThroughMs: -Infinity,
        observations: 0,
        nextTransitionId: 0,
        transitions: [],
        historyComplete: true,
        orderAmbiguous: false,
      };
      this.jitsiDominant = state;
    }

    // A delayed re-observation of the current or just-outgoing state is a stale
    // heartbeat and carries no new identity testimony. Any other late distinct
    // record contradicts the producer's ordered stream, so attribution fails
    // closed for the session.
    if (ev.tMs < state.observedThroughMs) {
      const latestTransition = state.transitions[state.transitions.length - 1];
      const harmlessHeartbeat = !ev.isEnd && (
        ev.name === state.currentName
        || (
          state.ended
          && ev.name === state.lastName
          && ev.tMs >= state.lastTransitionMs
          && ev.tMs < state.lastEndMs
        )
        || (
          latestTransition?.fromName === ev.name
          && ev.tMs >= latestTransition.epochStartMs
          && ev.tMs < latestTransition.observedAtMs
        )
      );
      if (!harmlessHeartbeat) {
        state.orderAmbiguous = true;
        this.invalidateExclusiveAttributions();
      }
      state.observations++;
      return;
    }
    state.observedThroughMs = Math.max(state.observedThroughMs, ev.tMs);
    state.observations++;

    if (ev.isEnd) {
      // An end token is valid only for the currently observed global state.
      // Silently accepting an unknown/duplicate end would let a corrupt record
      // advance the watermark and authorize an otherwise unfinalized name.
      if (!ev.name || state.currentName !== ev.name) {
        state.orderAmbiguous = true;
        this.invalidateExclusiveAttributions();
        return;
      }
      state.currentName = null;
      state.ended = true;
      state.lastEndMs = ev.tMs;
      return;
    }

    if (state.lastName === null) {
      // The first observation establishes the current baseline. It cannot name
      // an earlier acoustic turn because no preceding ordered transition exists.
      state.currentName = ev.name;
      state.lastName = ev.name;
      state.ended = false;
      state.lastTransitionMs = ev.tMs;
      return;
    }

    if (!state.ended && state.currentName === ev.name) {
      // Same global state, re-observed: liveness only. Crucially, this does not
      // mint a fresh start or alter the signal epoch.
      return;
    }

    state.transitions.push({
      id: state.nextTransitionId++,
      fromName: state.lastName,
      toName: ev.name,
      epochStartMs: state.lastTransitionMs,
      observedAtMs: ev.tMs,
    });
    if (state.transitions.length > this.hintLogLimit) {
      state.historyComplete = false;
      state.transitions.splice(0, state.transitions.length - this.hintLogLimit);
      this.invalidateExclusiveAttributions();
    }
    state.currentName = ev.name;
    state.lastName = ev.name;
    state.ended = false;
    state.lastTransitionMs = ev.tMs;
  }

  private resolveJitsiDominant(
    commit: CommitInfo,
    finalized: boolean,
  ): { name: string } | null {
    if (!finalized) return null;
    const state = this.jitsiDominant;
    const turn = this.acousticTurns.get(commit.clusterId);
    if (
      !state
      || !turn
      || turn.tEndMs === undefined
      || !state.historyComplete
      || state.orderAmbiguous
      || !this.acousticHistoryComplete
      || this.acousticOrderAmbiguous
    ) return null;
    const turnEndMs = turn.tEndMs;
    if (this.acousticSealedThroughMs < turnEndMs) return null;
    if (state.observedThroughMs <= turnEndMs) return null;

    // A trailing exclusive transition testifies only to an ordering boundary,
    // not to an onset timestamp. Admission is globally one-to-one over complete
    // retained custody:
    //   - acoustic registration is sealed beyond the transition observation;
    //   - exactly one turn started after the prior signal epoch and no later
    //     than this transition;
    //   - exactly one transition can claim this turn.
    // Repeated heartbeats never enter `transitions`, so quantity cannot create
    // confidence. The rule is lag-independent: a transition after acoustic end
    // can repair the turn once the acoustic producer seals through that token.
    const associated = state.transitions.filter((transition) => {
      if (this.acousticSealedThroughMs <= transition.observedAtMs) return false;
      const candidateTurns = [...this.acousticTurns.values()].filter((candidate) =>
        candidate.tStartMs > transition.epochStartMs
        && candidate.tStartMs <= transition.observedAtMs);
      return candidateTurns.length === 1
        && candidateTurns[0].clusterId === turn.clusterId;
    });
    if (associated.length !== 1) return null;
    const transition = associated[0];

    // A signal that changes identity twice while one acoustic turn is open is
    // internally contradictory for a single-name turn, even if only the first
    // token has one ordinal candidate.
    const transitionsInsideTurn = state.transitions.filter((candidate) =>
      candidate.observedAtMs >= turn.tStartMs
      && candidate.observedAtMs < turnEndMs);
    if (
      transitionsInsideTurn.length > 1
      || (
        transitionsInsideTurn.length === 1
        && transitionsInsideTurn[0].id !== transition.id
      )
    ) return null;
    return { name: transition.toName };
  }

  recordClusterVote(clusterId: string, speakerName: string): void {
    this.updateClusterVote(clusterId, speakerName);
  }

  /** Commit one causally admitted exclusive transition. This bypasses quantity
   *  voting and hysteresis: globally one-to-one transition custody is the
   *  authority, not the number of heartbeats or prior window matches. */
  confirmExclusiveAttribution(clusterId: string, speakerName: string): void {
    this.exclusiveAttributions.set(clusterId, speakerName);
    const previous = this.clusterLastResolvedName.get(clusterId);
    if (previous === speakerName) return;
    this.clusterLastResolvedName.set(clusterId, speakerName);
    this.onLateResolve?.(clusterId, speakerName);
  }

  /** Once retained causal custody is contradicted or compacted, every name it
   *  authorized becomes unknown again. Repaint through the existing stable-id
   *  callback; deleting first makes repeated contradictions idempotent. */
  private invalidateExclusiveAttributions(): void {
    const invalidated = [...this.exclusiveAttributions.keys()];
    this.exclusiveAttributions.clear();
    for (const clusterId of invalidated) {
      this.clusterLastResolvedName.delete(clusterId);
      this.onLateResolve?.(clusterId, clusterId);
    }
  }

  /** Pure window-match — the name lit DURING the commit window (with confidence),
   *  or null. No cluster-vote, no vote accumulation, no provisional fallback. For
   *  per-segment "unknown until a confident hint matches" binding (rotating gmeet
   *  channels): callers treat a low/no-confidence result as UNKNOWN rather than
   *  inheriting a stale channel name. */
  matchWindow(commit: CommitInfo): { name: string; confidence: number } | null {
    return this.windowMatch(commit);
  }

  /** windowMatch with its working shown: the same scan, but reporting WHICH gate
   *  rejected the best candidate (or that none overlapped). Read-only. */
  explainMatch(commit: CommitInfo): MatchDiagnostics {
    return this.windowMatchDiag(commit);
  }

  private windowMatch(commit: CommitInfo): { name: string; confidence: number } | null {
    return this.windowMatchDiag(commit).result;
  }

  private windowMatchDiag(commit: CommitInfo): MatchDiagnostics {
    // Hints are already lag-corrected at insert, so the commit window only
    // needs tolerance slack.
    const windowStart = commit.tStartMs - this.matchToleranceMs;
    const windowEnd = commit.tEndMs + this.matchToleranceMs;
    const supportStart = commit.tStartMs - HINT_SUPPORT_SLACK_MS;
    const supportEnd = commit.tEndMs + HINT_SUPPORT_SLACK_MS;
    const commitDur = Math.max(1, commit.tEndMs - commit.tStartMs);
    const agg = new Map<string, { ms: number; supportMs: number; inSpanMs: number; lastStart: number }>();

    let flickerSkipped = 0;
    for (const log of this.turns.values()) {
      for (const turn of log) {
        // FLICKER DEBOUNCE: a closed turn shorter than the window is a transient (noise
        // burst / UI blip) — but only when it carries NO in-span evidence. A short slice
        // that intersects the commit span itself is testimony about who lit DURING the
        // commit, and evicting it handed the window to the lagging neighbour (the sole
        // remaining candidate) on the botsig9 red (#868). Out-of-span transients still
        // can't steal a segment: in-span ranking + in-span confidence outvote them.
        if (turn.tEndMs !== undefined && turn.tEndMs - turn.tStartMs < FLICKER_MIN_MS) {
          const fEnd = turn.tEndMs;
          const inSpanHere = Math.min(fEnd, commit.tEndMs) - Math.max(turn.tStartMs, commit.tStartMs);
          if (inSpanHere <= 0) {
            // Count only flickers that would otherwise have overlapped this window,
            // so the diagnostic names what the debounce actually cost here.
            if (Math.min(fEnd, windowEnd) - Math.max(turn.tStartMs, windowStart) > 0) flickerSkipped++;
            continue;
          }
        }
        const turnEnd = turn.tEndMs ?? (turn.tStartMs + OPEN_TURN_GRACE_MS);
        const o = Math.max(0, Math.min(turnEnd, windowEnd) - Math.max(turn.tStartMs, windowStart));
        if (o <= 0) continue;
        const support = Math.max(0, Math.min(turnEnd, supportEnd) - Math.max(turn.tStartMs, supportStart));
        if (support <= 0) continue;
        const inSpan = Math.max(0, Math.min(turnEnd, commit.tEndMs) - Math.max(turn.tStartMs, commit.tStartMs));
        const a = agg.get(turn.name) ?? { ms: 0, supportMs: 0, inSpanMs: 0, lastStart: -Infinity };
        a.ms += o; a.supportMs += support; a.inSpanMs += inSpan; a.lastStart = Math.max(a.lastStart, turn.tStartMs);
        agg.set(turn.name, a);
      }
    }
    if (agg.size === 0) return { result: null, reject: 'no-hint-overlap', candidates: 0, flickerSkipped };

    // Most overlap-ms wins; on a near-tie (within RECENCY_TIE_MS) the MORE RECENT
    // hint wins — so a previous speaker's still-open turn can't out-vote the speaker
    // who actually just started.
    let best: { name: string; ms: number; supportMs: number; inSpanMs: number; lastStart: number } | null = null;
    let totalSupportMs = 0;
    let totalInSpanMs = 0;
    for (const [name, a] of agg) { totalSupportMs += a.supportMs; totalInSpanMs += a.inSpanMs; }
    // Winner selection prefers IN-SPAN evidence: the name that actually lit during
    // the commit beats the name whose support piled up in the ±slack (the lagging
    // previous speaker's heartbeat slices — on the botsig9 red those were chosen as
    // best with inSpanMs=0 and then rejected at confidence 0.00, #868). Only when
    // NOBODY lit in-span (pure hint lag) does slack support choose, as before.
    // The tie window must live on the metric's own scale: in-span durations are
    // bounded by the commit (sub-second here), so the slack-scale RECENCY_TIE_MS
    // would call EVERY comparison a tie and let recency — the neighbour's latest
    // heartbeat — decide anyway (the pass-two regression). In-span ties are a
    // fifth of the larger claim, floored at 50ms; recency still breaks real ties.
    const inSpanRank = totalInSpanMs > 0;
    for (const [name, a] of agg) {
      if (!best) { best = { name, ...a }; continue; }
      const av = inSpanRank ? a.inSpanMs : a.supportMs;
      const bv = inSpanRank ? best.inSpanMs : best.supportMs;
      const tie = inSpanRank ? Math.max(50, 0.2 * Math.max(av, bv)) : RECENCY_TIE_MS;
      if (av > bv + tie) best = { name, ...a };
      else if (av >= bv - tie && a.lastStart > best.lastStart) best = { name, ...a };
    }
    if (!best) return { result: null, reject: 'no-hint-overlap', candidates: agg.size, flickerSkipped };
    const coverage = Math.min(1, best.supportMs / commitDur);
    // Confidence is CONTESTED-NESS INSIDE THE COMMIT SPAN. The ±support slack exists to
    // absorb hint-lag jitter, not to give the neighbouring speaker's adjacent speech a
    // vote: with sub-second diarizer turns, a handover commit fully covered by one name
    // was still scoring conf<0.6 because the previous speaker's closed heartbeat slices
    // sat inside the slack — 10 of 13 unnamed turns on the botsig9 red arm (#868). When
    // no hint time falls inside the span at all (pure lag), the slack-window share is
    // the only evidence there is, and judges as before.
    const confidence = totalInSpanMs > 0
      ? best.inSpanMs / totalInSpanMs
      : (totalSupportMs > 0 ? best.supportMs / totalSupportMs : 0);
    const bestDiag = { name: best.name, supportMs: best.supportMs, coverage, confidence };
    const requiredSupport = Math.min(MIN_MATCH_SUPPORT_MS, commitDur * 0.8);
    if (best.supportMs < requiredSupport) return { result: null, reject: 'support', candidates: agg.size, flickerSkipped, best: bestDiag };
    if (coverage < MIN_MATCH_COVERAGE) return { result: null, reject: 'coverage', candidates: agg.size, flickerSkipped, best: bestDiag };
    if (confidence < MIN_MATCH_CONFIDENCE) return { result: null, reject: 'confidence', candidates: agg.size, flickerSkipped, best: bestDiag };
    return { result: { name: best.name, confidence: Math.min(coverage, confidence) }, candidates: agg.size, flickerSkipped, best: bestDiag };
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

    const prev = this.clusterLastResolvedName.get(clusterId);
    const majority = this.clusterMajority(clusterId);
    if (!majority || majority.name === clusterId || majority.name === prev) return;

    // Continuous re-resolve with HYSTERESIS: the cluster's name may change as
    // evidence accumulates, but only flip when the new winner leads the current
    // name by a clear margin — otherwise noisy/lagging hints thrash A→B→A.
    // Fires onLateResolve on EVERY accepted change (first resolution included),
    // so the caller repaints the cluster's pending + published segments live.
    const prevVotes = prev ? (tally.get(prev) ?? 0) : 0;
    const winnerVotes = tally.get(majority.name) ?? 0;
    const firstResolution = prev === undefined || prev === clusterId;
    if (firstResolution || winnerVotes - prevVotes >= NAME_SWITCH_MARGIN) {
      this.clusterLastResolvedName.set(clusterId, majority.name);
      this.onLateResolve?.(clusterId, majority.name);
    }
  }

  /** Total lit-time (ms) of `name` across all hint kinds within [fromMs, toMs].
   *  Flicker-short slices count — this is raw testimony of the name's activity
   *  around a window, and the CALLER thresholds it (a stale tile flip is one
   *  short slice; a real speaker's heartbeats total seconds). */
  litMsAround(name: string, fromMs: number, toMs: number): number {
    let ms = 0;
    for (const log of this.turns.values()) {
      for (const turn of log) {
        if (turn.name !== name) continue;
        const end = turn.tEndMs ?? (turn.tStartMs + OPEN_TURN_GRACE_MS);
        ms += Math.max(0, Math.min(end, toMs) - Math.max(turn.tStartMs, fromMs));
      }
    }
    return ms;
  }

  /** Diagnostics for telemetry. */
  stats(): { hintTurns: Record<string, number>; clustersWithVotes: number; resolvedClusters: number } {
    const hintTurns: Record<string, number> = {};
    for (const [kind, log] of this.turns) hintTurns[kind] = log.length;
    if (this.jitsiDominant) hintTurns['jitsi-dominant'] = this.jitsiDominant.observations;
    return {
      hintTurns,
      clustersWithVotes: this.clusterVoteHistory.size,
      resolvedClusters: this.clusterLastResolvedName.size,
    };
  }

  reset(): void {
    this.turns.clear();
    this.acousticTurns.clear();
    this.acousticSealedThroughMs = -Infinity;
    this.acousticHistoryComplete = true;
    this.acousticOrderAmbiguous = false;
    this.jitsiDominant = null;
    this.clusterLastResolvedName.clear();
    this.exclusiveAttributions.clear();
    this.clusterVoteHistory.clear();
  }
}
