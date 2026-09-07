/**
 * track-name-resolver — bind each STABLE per-track channel to the right display name, and defend
 * that binding against a flaky active-speaker DOM.
 *
 * Zoom's per-track lane gives us the thing pyannote can never supply: audio that is already
 * physically separated, one WebRTC track per participant, stable for the meeting (witnessed live —
 * 5 streams for 5 speakers, 0 remaps). What Zoom does NOT give us is the name: its active-speaker
 * DOM is a sticky dominant-speaker spotlight that lags, holds the wrong person under screen-share,
 * and flickers. So the resolver's whole job is the correlation, and it is the same job
 * `GmeetChannelBinder` does for Meet's glow and `TrackNamer` does for the Teams CSRC spine.
 *
 * PURE LOGIC — no DOM, no audio, no timers, no globals. That is deliberate and it is the one design
 * decision every README in this subsystem repeats: the attribution algorithm is extracted so it can
 * be golden-tested offline instead of against a live meeting. This file was lifted verbatim out of
 * a `page.evaluate` string in the bot's `capture-bridge.ts`, where none of that was possible.
 *
 * The rules, in the order they matter:
 *   • VOTE — every clean moment (this channel is the loudest hot one AND exactly one speaker is lit)
 *     casts one channel↔name vote. The binding is the argmax, so a wrong early sample is a single
 *     vote the true speaker outweighs. SELF-CORRECTING: no bad first bind locks in for the meeting.
 *   • MARGIN hysteresis — a new leader must beat the incumbent by `margin` votes before it flips, so
 *     the sticky DOM's stray votes cannot churn an established name.
 *   • 1:1 BY IDENTITY — a name is another ACTIVE channel's identity; the DOM cannot paste "Justin"
 *     onto Scott's channel however often it over-votes it there. It frees up when its owner goes
 *     long-idle (`idleReleaseMs` — a leave/rejoin under a new stream). One exception: this channel's
 *     votes for the name are high-`purity`, which is what distinguishes two genuinely same-named
 *     people (pure votes on each → both named) from contamination (a mixed minority → stays blocked).
 *   • SELF-EXCLUSION — the local participant (our bot) is never a remote channel's identity. The
 *     upstream watcher already filters the self tile by `selfName`, but that marker vanishes
 *     transiently, which is exactly how the host's glow leaked onto a remote channel in the
 *     speaker-bots eval of 2026-06-20. This is the leak-proof backstop `GmeetChannelBinder` earned
 *     from that incident: a sticky self name the resolver refuses to assign, whose already-accrued
 *     votes and bindings are purged the moment the name becomes known.
 *   • SPEAKER A/B/C — an unbound channel is not anonymous, it is UNNAMED-BUT-SEPARATED. `labelFor`
 *     gives it a stable letter in first-heard order over the unnamed channels only, the way
 *     `TrackNamer.labelFor` does, so a reader can still follow two unnamed people apart. Letters run
 *     over the unnamed pool (not absolute channel order) because numbering by channel produced a
 *     transcript showing "Speaker B" with no Speaker A in it — the reader hunts for someone who does
 *     not exist, because A had meanwhile earned a real name.
 *
 * Floor: a channel still needs the DOM to name it correctly at least sometimes. A speaker Zoom never
 * lights (throughout a screen-share, say) stays unnamed — separated, and labelled, but unnamed.
 */

/** Defaults, kept identical to the page-side literal this replaced (behaviour is unchanged). */
export const TRACK_NAME_DEFAULTS = {
  /** A channel counts as "hot" for this long after its last energetic frame. */
  hotMs: 600,
  /** Votes a challenger must lead the incumbent by before the binding flips. */
  margin: 3,
  /** A name held by a channel idle this long is released (leave / rejoin under a new stream). */
  idleReleaseMs: 8_000,
  /** Share of a channel's votes a name needs to be co-held against an active owner. */
  purity: 0.7,
} as const;

export interface TrackNameResolverOptions {
  /**
   * `exclusive` — the watcher lights exactly one speaker at a time (Zoom's dominant-speaker
   * spotlight), so a new name clears the previous one. `additive` — several may be lit at once.
   */
  mode?: 'exclusive' | 'additive';
  hotMs?: number;
  margin?: number;
  idleReleaseMs?: number;
  purity?: number;
  /** The LOCAL participant (our bot). Never binds a remote channel — see SELF-EXCLUSION above. */
  selfName?: string;
  /** Fired on every committed bind or re-bind. The host reports it as a typed observation. */
  onBind?: (channel: number, name: string, votes: number) => void;
}

export interface TrackNameResolver {
  /** Live, because the host flips it per platform at construction time. */
  mode: 'exclusive' | 'additive';
  /** Declare (or update) the local participant. Sticky, and purges what the self already accrued. */
  setSelfName(name?: string): void;
  /** An active-speaker edge from the DOM watcher. `isEnd` closes the name's lit interval. */
  onSpeak(name: string | null, tMs: number, isEnd: boolean): void;
  /** This channel carried an energetic frame at `tsMs` with peak amplitude `energy` (0..1). */
  markHot(channel: number, tsMs: number, energy: number): void;
  /** Cast a vote if this moment is clean, then return the channel's committed name (if any). */
  resolve(channel: number, tsMs: number): string | undefined;
  /** The committed name, without voting. */
  nameFor(channel: number): string | undefined;
  /** What this channel publishes under right now: its earned name, else a stable "Speaker A/B/C". */
  labelFor(channel: number): string;
  /** Channels in first-heard order. */
  channels(): number[];
  /** channel → committed name, for diagnostics and typed observations. */
  bindings(): Array<{ channel: number; name: string }>;
}

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
/** A, B, … Z, AA, AB, … — stable, and never a real name. */
export function speakerLabel(index: number): string {
  let n = index, out = '';
  do { out = LETTERS[n % 26] + out; n = Math.floor(n / 26) - 1; } while (n >= 0);
  return `Speaker ${out}`;
}

export function createTrackNameResolver(options: TrackNameResolverOptions = {}): TrackNameResolver {
  const hotMs = options.hotMs ?? TRACK_NAME_DEFAULTS.hotMs;
  const margin = options.margin ?? TRACK_NAME_DEFAULTS.margin;
  const idleReleaseMs = options.idleReleaseMs ?? TRACK_NAME_DEFAULTS.idleReleaseMs;
  const purity = options.purity ?? TRACK_NAME_DEFAULTS.purity;
  const onBind = options.onBind;

  /** name → when it was lit (ms). */
  const speaking = new Map<string, number>();
  /** channel → last energetic frame {ts, peak}. */
  const hot = new Map<number, { ts: number; e: number }>();
  /** channel → name → co-occurrence tally. */
  const votes = new Map<number, Map<string, number>>();
  /** channel → committed name (the argmax of the available votes). */
  const names = new Map<number, string>();
  /** channels in first-heard order — the domain the Speaker A/B/C letters run over. */
  const order: number[] = [];

  let selfName: string | undefined = options.selfName;

  const noteHeard = (channel: number): void => {
    if (!order.includes(channel)) order.push(channel);
  };

  const rederive = (channel: number, tsMs: number): void => {
    const v = votes.get(channel);
    if (!v) return;
    let total = 0;
    for (const c of v.values()) total += c;
    // A name is AVAILABLE to this channel if no OTHER active channel holds it (1:1 BY IDENTITY —
    // raw-count 1:1 would let Scott's channel STEAL "Justin" whenever the sticky DOM over-votes it
    // there). The exception is a high-PURITY holding, which is what a genuine second same-named
    // speaker looks like and contamination does not.
    const available = (name: string): boolean => {
      if (selfName && name === selfName) return false;       // the self is never a remote identity
      let activeOwner = false;
      for (const [c, n] of names) {
        if (n !== name || c === channel) continue;
        const h = hot.get(c);
        if (h && tsMs - h.ts <= idleReleaseMs) { activeOwner = true; break; }
      }
      if (!activeOwner) return true;
      const vn = v.get(name) || 0;
      return vn >= purity * total && vn >= margin;
    };
    let best = '', bestN = -1;
    for (const [n, c] of v) if (c > bestN && available(n)) { bestN = c; best = n; }
    if (best === '') return;
    const cur = names.get(channel);
    if (best === cur) return;
    const curN = cur ? (v.get(cur) || 0) : -1;
    // Hysteresis: a challenger must beat the incumbent by `margin` before it flips, so a few stray
    // sticky-DOM votes cannot churn an established name — but real evidence still corrects it.
    if (bestN < curN + margin) return;
    // Release only IDLE owners of the name (a leave/reconnect); keep active co-holders (duplicates).
    for (const [c, n] of [...names]) {
      if (n !== best || c === channel) continue;
      const h = hot.get(c);
      if (!h || tsMs - h.ts > idleReleaseMs) names.delete(c);
    }
    names.set(channel, best);
    onBind?.(channel, best, bestN);
  };

  return {
    mode: options.mode ?? 'exclusive',

    setSelfName(name?: string): void {
      selfName = name;
      if (!name) return;
      // Purge what the self already accrued — the marker can render late, so a self bind may
      // already exist. Same treatment, and the same reason, as GmeetChannelBinder.setSelfName.
      speaking.delete(name);
      for (const v of votes.values()) v.delete(name);
      for (const [c, n] of [...names]) if (n === name) names.delete(c);
    },

    onSpeak(name: string | null, tMs: number, isEnd: boolean): void {
      if (!name) return;
      if (selfName && name === selfName) return;             // the self never votes for a channel
      if (isEnd) { speaking.delete(name); return; }
      if (this.mode === 'exclusive') { speaking.clear(); speaking.set(name, tMs); }
      else if (!speaking.has(name)) speaking.set(name, tMs);
    },

    markHot(channel: number, tsMs: number, energy: number): void {
      noteHeard(channel);
      hot.set(channel, { ts: tsMs, e: energy });
    },

    resolve(channel: number, tsMs: number): string | undefined {
      noteHeard(channel);
      // Vote whenever THIS channel is the loudest hot one while exactly one speaker is lit — a clean
      // channel↔name co-occurrence, and the only moment the DOM can be trusted about who is talking.
      let loud = -1, loudE = -1;
      for (const [c, h] of hot) { if (tsMs - h.ts < hotMs && h.e > loudE) { loudE = h.e; loud = c; } }
      const active = [...speaking.keys()];
      if (loud === channel && active.length === 1) {
        const n = active[0];
        let v = votes.get(channel);
        if (!v) { v = new Map(); votes.set(channel, v); }
        v.set(n, (v.get(n) || 0) + 1);
        rederive(channel, tsMs);
      }
      return names.get(channel);
    },

    nameFor(channel: number): string | undefined { return names.get(channel); },

    labelFor(channel: number): string {
      const n = names.get(channel);
      if (n) return n;
      noteHeard(channel);
      const unnamed = order.filter((c) => !names.has(c));
      const i = unnamed.indexOf(channel);
      return speakerLabel(i < 0 ? unnamed.length : i);
    },

    channels(): number[] { return [...order]; },

    bindings(): Array<{ channel: number; name: string }> {
      return [...names].map(([channel, name]) => ({ channel, name }));
    },
  };
}
