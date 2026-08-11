/**
 * TrackNamer — which HUMAN a transport track is.
 *
 * The transport spine (turn-source.ts) gives every turn a `trackId` that is stable for the whole
 * meeting but meaningless: `1266` is a number an RTP mixer chose. The DOM tiles and the closed
 * captions know names but are flickery, laggy, and — on Teams — light on NOISE, which is exactly
 * how a participant who was TYPING came to own 65 of 66 labels on a balanced two-speaker tape.
 *
 * The two signals fail in opposite directions, and that is the whole design here:
 *
 *   the transport is right about WHEN and never says WHO
 *   the UI is right about WHO and often wrong about WHEN
 *
 * So the UI is asked its question exactly ONCE per speaker per meeting, under the only condition
 * where it cannot be wrong: a track earns a name from the spans where EXACTLY ONE track was
 * audible AND EXACTLY ONE name was lit. Anything else — two tiles lit, two sources audible, a
 * name lit while nobody is audible — contributes NOTHING. Not a weaker vote: nothing.
 *
 * Three further rules, each of which a real fixture required:
 *
 *   • **Corroboration.** One coincidence is a coincidence. A name is accepted after N (default 2)
 *     separate episodes of at least `minEpisodeMs`, so a single lag artefact cannot name a track.
 *   • **Exclusivity, both ways.** A name belongs to the track holding the clear majority of that
 *     NAME's evidence, and a name already earned is never re-let. On the m30 fixture the other
 *     participant's track accrues 7.5s of "leo" evidence purely from hint lag against leo's 61s —
 *     without this rule both tracks would end up called leo, which is two wrong labels produced
 *     from correct data.
 *   • **A settle delay.** Evidence is integrated only up to `newest - settleMs`, so a hint that
 *     arrives late still lands on the span it describes. Names therefore arrive a few seconds
 *     after the speech does — which is what the retroactive rename path is FOR.
 *
 * A track with no evidence is NOT guessed. It publishes as a stable "Speaker A/B/C" by
 * first-heard order and stays that way. Unknown stays unknown.
 */

const envNumber = (name: string, fallback: number): number => {
  const raw = typeof process !== 'undefined' ? process.env?.[name] : undefined;
  const n = raw !== undefined && raw !== '' ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : fallback;
};

/** Separate coincidences a name must survive before it is believed. */
export const TRACK_NAME_CORROBORATIONS = envNumber('VEXA_TRACK_NAME_CORROBORATIONS', 2);
/** Shorter than this, a coincidence is not an episode — it is the tail of a lag correction. */
export const TRACK_NAME_MIN_EPISODE_MS = envNumber('VEXA_TRACK_NAME_MIN_EPISODE_MS', 600);
/** The winner's share of THIS TRACK's evidence. Below it the tiles disagree about who the track is. */
export const TRACK_NAME_MIN_SHARE = envNumber('VEXA_TRACK_NAME_MIN_SHARE', 0.7);
/** The winner's share of THIS NAME's evidence across all tracks. Below it two tracks are both
 *  claiming one person and neither may have them. */
export const TRACK_NAME_MIN_OWNER_SHARE = envNumber('VEXA_TRACK_NAME_OWNER_SHARE', 0.7);
/** How far behind the newest event evidence is integrated, so late signals land on the right span. */
export const TRACK_NAME_SETTLE_MS = envNumber('VEXA_TRACK_NAME_SETTLE_MS', 3000);

/** Per-kind UI lag: how far each naming signal trails the audio it describes.
 *
 *  MEASURED, not assumed. Sweeping the DOM lag against the transport's own account on the m30
 *  fixture, csrc∩DOM agreement runs 86.5% at zero and peaks at **94.4% at +1000 ms** (75.2% if
 *  shifted the wrong way) — the Teams voice outline trails RTP by about a second. The binder's
 *  200 ms figure for 'dom-outline' is a different measurement against a different reference and is
 *  left alone; this one is calibrated against the transport, which is the clock this file matches
 *  names to. [2026-08-11 S2 signal verdict §2] */
const HINT_LAG_MS = envNumber('VEXA_TRACK_NAME_HINT_LAG_MS', 1000);
const CAPTION_LAG_MS = envNumber('VEXA_TRACK_NAME_CAPTION_LAG_MS', 1000);
/** A lit tile with no successor and no explicit end stays lit this long. */
const HINT_GRACE_MS = envNumber('VEXA_TRACK_NAME_HINT_GRACE_MS', 2500);
/** A caption describes roughly this much speech before its own timestamp. */
const CAPTION_WINDOW_MS = envNumber('VEXA_TRACK_NAME_CAPTION_WINDOW_MS', 1500);
/** Intervals older than this behind the integrator are dropped (a long meeting must not grow). */
const RETENTION_MS = 120_000;

export type NameEvidenceKind = 'dom' | 'caption';

export interface TrackEvidence {
  name: string;
  /** Exclusive-coincidence time, ms. */
  supportMs: number;
  /** Separate episodes contributing that time. */
  episodes: number;
  kinds: NameEvidenceKind[];
}

export interface TrackNaming {
  name: string;
  /** The winner's share of the track's own evidence at acceptance. */
  confidence: number;
  evidence: TrackEvidence[];
  /** When the name was accepted (integrator time, epoch ms). */
  atMs: number;
}

export interface TrackNamerOptions {
  corroborations?: number;
  minEpisodeMs?: number;
  minShare?: number;
  minOwnerShare?: number;
  settleMs?: number;
  /** A track earned a name. The host repaints everything that track already published. */
  onNamed?: (trackId: string, name: string) => void;
  log?: (m: string) => void;
}

interface Interval { start: number; end: number; kind?: NameEvidenceKind }

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
/** A, B, … Z, AA, AB, … — stable, and never a name. */
export function speakerLabel(index: number): string {
  let n = index, out = '';
  do { out = LETTERS[n % 26] + out; n = Math.floor(n / 26) - 1; } while (n >= 0);
  return `Speaker ${out}`;
}

export class TrackNamer {
  private readonly corroborations: number;
  private readonly minEpisodeMs: number;
  private readonly minShare: number;
  private readonly minOwnerShare: number;
  private readonly settleMs: number;
  private readonly log: (m: string) => void;
  onNamed?: (trackId: string, name: string) => void;

  /** Audible spans per track (the transport's own account). */
  private trackSpans = new Map<string, Interval[]>();
  /** Lit spans per name, from any naming signal. */
  private nameSpans = new Map<string, Interval[]>();

  /** (trackId → name → evidence). */
  private evidence = new Map<string, Map<string, TrackEvidence>>();
  private named = new Map<string, TrackNaming>();
  /** name → the track that earned it. A name is never re-let. */
  private owner = new Map<string, string>();
  /** First-heard order → the stable Speaker A/B/C fallback label. */
  private order: string[] = [];

  private upTo = 0;
  private newest = 0;
  /** The earliest instant any signal described. The integrator starts HERE, not at whenever it was
   *  first ticked — a namer first ticked late would otherwise silently discard the meeting's
   *  opening evidence, which is exactly the evidence that names a speaker fastest. */
  private origin: number | null = null;
  private episode: { trackId: string; name: string; start: number } | null = null;

  constructor(opts: TrackNamerOptions = {}) {
    this.corroborations = opts.corroborations ?? TRACK_NAME_CORROBORATIONS;
    this.minEpisodeMs = opts.minEpisodeMs ?? TRACK_NAME_MIN_EPISODE_MS;
    this.minShare = opts.minShare ?? TRACK_NAME_MIN_SHARE;
    this.minOwnerShare = opts.minOwnerShare ?? TRACK_NAME_MIN_OWNER_SHARE;
    this.settleMs = opts.settleMs ?? TRACK_NAME_SETTLE_MS;
    this.onNamed = opts.onNamed;
    this.log = opts.log ?? (() => { /* silent */ });
  }

  // ── inputs ────────────────────────────────────────────────────────────────────────────────────

  /** The transport says a source became (in)audible. */
  setTrackActive(trackId: string, active: boolean, tMs: number): void {
    this.newest = Math.max(this.newest, tMs);
    this.noteOrigin(tMs);
    const spans = this.span(this.trackSpans, trackId);
    const last = spans[spans.length - 1];
    if (active) {
      if (last && last.end >= tMs) { last.end = Math.max(last.end, tMs); return; }   // already open
      spans.push({ start: tMs, end: tMs });
      return;
    }
    if (last && last.end <= tMs) last.end = tMs;
  }

  /** A DOM tile lit (or explicitly went dark). */
  recordHint(name: string, tMs: number, isEnd = false): void {
    if (!name) return;
    this.paint(name, tMs - HINT_LAG_MS, HINT_GRACE_MS, 'dom', isEnd);
  }

  /** The platform's own captions attributed speech to `name` at `tMs`. */
  recordCaption(name: string, tMs: number): void {
    if (!name) return;
    // A caption describes the speech BEFORE its timestamp, so it paints backwards.
    this.paintSpan(name, tMs - CAPTION_LAG_MS - CAPTION_WINDOW_MS, tMs - CAPTION_LAG_MS, 'caption');
    this.newest = Math.max(this.newest, tMs);
  }

  /** Move the integrator's clock (called on every audio frame — cheap when nothing moved). */
  tick(tMs: number): void {
    this.newest = Math.max(this.newest, tMs);
    this.advance(this.newest - this.settleMs);
  }

  /** Integrate everything, ignoring the settle delay. Teardown only. */
  finish(): void { this.advance(this.newest); }

  // ── outputs ───────────────────────────────────────────────────────────────────────────────────

  /** The name this track earned, or null. NEVER a guess. */
  nameFor(trackId: string): string | null { return this.named.get(trackId)?.name ?? null; }

  /** What a turn on this track publishes under right now: the earned name, else a stable
   *  "Speaker A/B/C" by first-heard order. */
  labelFor(trackId: string): string {
    const n = this.nameFor(trackId);
    if (n) return n;
    return speakerLabel(this.noteHeard(trackId));
  }

  /** Register a track in first-heard order (idempotent); returns its index. */
  noteHeard(trackId: string): number {
    const i = this.order.indexOf(trackId);
    if (i >= 0) return i;
    this.order.push(trackId);
    return this.order.length - 1;
  }

  naming(trackId: string): TrackNaming | null { return this.named.get(trackId) ?? null; }

  stats(): { tracks: number; named: number; evidence: Record<string, Record<string, number>> } {
    const ev: Record<string, Record<string, number>> = {};
    for (const [track, m] of this.evidence) {
      ev[track] = {};
      for (const [name, e] of m) ev[track][name] = Math.round(e.supportMs);
    }
    return { tracks: this.order.length, named: this.named.size, evidence: ev };
  }

  reset(): void {
    this.trackSpans.clear(); this.nameSpans.clear(); this.evidence.clear();
    this.named.clear(); this.owner.clear(); this.order = [];
    this.upTo = 0; this.newest = 0; this.origin = null; this.episode = null;
  }

  // ── the integrator ────────────────────────────────────────────────────────────────────────────

  /** Every interval start passes through here, so the origin cannot be missed. */
  private noteOrigin(t: number): void {
    if (this.origin === null || t < this.origin) this.origin = t;
  }

  private span(m: Map<string, Interval[]>, key: string): Interval[] {
    let a = m.get(key);
    if (!a) { a = []; m.set(key, a); }
    return a;
  }

  private paint(name: string, at: number, graceMs: number, kind: NameEvidenceKind, isEnd: boolean): void {
    this.newest = Math.max(this.newest, at + HINT_LAG_MS);
    this.noteOrigin(at);
    const spans = this.span(this.nameSpans, name);
    const last = spans[spans.length - 1];
    if (isEnd) { if (last && last.end > at) last.end = Math.max(at, last.start); return; }
    if (last && last.end >= at) { last.end = Math.max(last.end, at + graceMs); return; }
    spans.push({ start: at, end: at + graceMs, kind });
  }

  private paintSpan(name: string, from: number, to: number, kind: NameEvidenceKind): void {
    if (to <= from) return;
    this.noteOrigin(from);
    const spans = this.span(this.nameSpans, name);
    const last = spans[spans.length - 1];
    if (last && last.end >= from) { last.end = Math.max(last.end, to); return; }
    spans.push({ start: from, end: to, kind });
  }

  /**
   * Sweep [upTo, horizon] and credit every instant where exactly one track was audible while
   * exactly one name was lit. The edge list is rebuilt each call from the live intervals — there
   * are hundreds of them in an hour-long meeting, so the cost is noise beside one Whisper call.
   */
  private advance(horizon: number): void {
    if (this.upTo === 0) this.upTo = this.origin ?? horizon;
    if (!(horizon > this.upTo)) return;
    const edges = new Set<number>([horizon]);
    const consider = (iv: Interval): void => {
      if (iv.start > this.upTo && iv.start <= horizon) edges.add(iv.start);
      if (iv.end > this.upTo && iv.end <= horizon) edges.add(iv.end);
    };
    for (const list of this.trackSpans.values()) for (const iv of list) consider(iv);
    for (const list of this.nameSpans.values()) for (const iv of list) consider(iv);

    let cur = this.upTo;
    for (const e of [...edges].sort((a, b) => a - b)) {
      if (e <= cur) continue;
      this.integrate(cur, e);
      cur = e;
    }
    this.upTo = horizon;
    this.prune(horizon - RETENTION_MS);
  }

  private integrate(a: number, b: number): void {
    const mid = (a + b) / 2;
    let track: string | null = null; let tracks = 0;
    for (const [id, list] of this.trackSpans) {
      for (const iv of list) if (iv.start <= mid && mid < iv.end) { tracks++; track = id; break; }
      if (tracks > 1) break;
    }
    let name: string | null = null; let names = 0; let kind: NameEvidenceKind = 'dom';
    for (const [n, list] of this.nameSpans) {
      for (const iv of list) if (iv.start <= mid && mid < iv.end) { names++; name = n; kind = iv.kind ?? 'dom'; break; }
      if (names > 1) break;
    }
    const pair = tracks === 1 && names === 1 && track && name ? { trackId: track, name } : null;
    const cur = this.episode;
    if (cur && (!pair || cur.trackId !== pair.trackId || cur.name !== pair.name)) {
      this.commit(cur, a, kind);
      this.episode = null;
    }
    if (pair && !this.episode) this.episode = { trackId: pair.trackId, name: pair.name, start: a };
  }

  private commit(ep: { trackId: string; name: string; start: number }, end: number, kind: NameEvidenceKind): void {
    const ms = end - ep.start;
    if (ms < this.minEpisodeMs) return;   // too short to be anything but a lag artefact
    let byName = this.evidence.get(ep.trackId);
    if (!byName) { byName = new Map(); this.evidence.set(ep.trackId, byName); }
    const e = byName.get(ep.name) ?? { name: ep.name, supportMs: 0, episodes: 0, kinds: [] };
    e.supportMs += ms;
    e.episodes += 1;
    if (!e.kinds.includes(kind)) e.kinds.push(kind);
    byName.set(ep.name, e);
    this.evaluate(ep.trackId, end);
  }

  /** Can this track's leading candidate be believed yet? */
  private evaluate(trackId: string, atMs: number): void {
    if (this.named.has(trackId)) return;
    const byName = this.evidence.get(trackId);
    if (!byName) return;
    const ranked = [...byName.values()].sort((x, y) => y.supportMs - x.supportMs);
    const lead = ranked[0];
    if (!lead || lead.episodes < this.corroborations) return;
    const total = ranked.reduce((s, e) => s + e.supportMs, 0);
    const share = total > 0 ? lead.supportMs / total : 0;
    if (share < this.minShare) return;                        // this track's tiles disagree
    if (this.owner.has(lead.name)) return;                    // that person is already someone else
    // Exclusivity the other way: does this track hold the clear majority of that NAME's evidence?
    let nameTotal = 0;
    for (const [, m] of this.evidence) { const e = m.get(lead.name); if (e) nameTotal += e.supportMs; }
    if (nameTotal > 0 && lead.supportMs / nameTotal < this.minOwnerShare) return;
    this.named.set(trackId, { name: lead.name, confidence: share, evidence: ranked, atMs });
    this.owner.set(lead.name, trackId);
    this.noteHeard(trackId);
    this.log(`track ${trackId} → "${lead.name}" (${Math.round(lead.supportMs)}ms over ${lead.episodes} episode(s), share ${share.toFixed(2)})`);
    this.onNamed?.(trackId, lead.name);
  }

  private prune(before: number): void {
    if (before <= 0) return;
    for (const [k, list] of this.trackSpans) {
      const keep = list.filter((iv) => iv.end >= before);
      if (keep.length !== list.length) this.trackSpans.set(k, keep);
    }
    for (const [k, list] of this.nameSpans) {
      const keep = list.filter((iv) => iv.end >= before);
      if (keep.length !== list.length) this.nameSpans.set(k, keep);
    }
  }
}
