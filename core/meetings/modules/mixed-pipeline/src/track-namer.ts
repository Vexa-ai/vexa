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

/** How many separate scans a roster name must appear in before elimination may use it. A name that
 *  flickered once through a rotting selector is not a participant. */
export const TRACK_NAME_ROSTER_SIGHTINGS = envNumber('VEXA_TRACK_NAME_ROSTER_SIGHTINGS', 2);
/** How long the roster must have shown NO NEW NAME before an elimination may be drawn from it.
 *  Elimination is a last resort and must be taken LATE: a roster fills one name at a time, so it
 *  passes through states that look decidable and are not. */
export const TRACK_NAME_ROSTER_SETTLE_MS = envNumber('VEXA_TRACK_NAME_ROSTER_SETTLE_MS', 5000);

/**
 * Names that must never become a speaker, checked HERE as well as at the platform guard.
 *
 * The producer's guard (teams-capture's isTeamsDisplayNameCandidate) is the door, and it is the
 * right place to close this. But this file binds a name to a human being on the strength of an
 * argument from absence, and the m34 meeting is what that costs when the input is polluted: the
 * roster contained our OWN BOT, elimination had exactly one track and one name left, and a bot's
 * name went onto a person's speech. A rule that reasons from "nothing else it could be" must check
 * that what remains is a person, or its premise is doing no work at all.
 *
 * Platform-agnostic by construction: no Teams vocabulary, just the shapes that are never humans.
 */
const PLACEHOLDER_NAMES = new Set([
  'unknown user', 'unknown', 'unknown participant', 'guest', 'guest user', 'anonymous',
  'anonymous user', 'participant', 'unidentified', 'unbekannter benutzer', 'gast',
  'utilisateur inconnu', 'invite', 'invité', 'usuario desconocido', 'invitado',
  'utente sconosciuto', 'ospite', 'onbekende gebruiker', 'okänd användare',
  'nieznany użytkownik', 'неизвестный пользователь', 'гость', 'участник',
]);

/** Teams' qualifiers, stripped for identity comparison only — never for display. */
export function normalizeNameForIdentity(value: string): string {
  return (value || '')
    .replace(/\s*\((?:unverified|guest|bot|external|extern|invit[ée]|gast|гость)\)\s*$/giu, '')
    .replace(/\s+\(\d+\)\s*$/, '')
    .replace(/\s+\d+$/, '')
    .trim()
    .toLowerCase();
}

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
  /** The winner's share of the track's own evidence at acceptance. 1 for an elimination, which is
   *  not a measurement — it is the only remaining possibility. */
  confidence: number;
  /** HOW the name was reached. 'evidence' means this track was seen to be that person;
   *  'elimination' means nobody else could be, which is a weaker claim and is recorded as one. */
  source: 'evidence' | 'elimination';
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
  /** Roster sightings required before elimination may use a name. */
  rosterSightings?: number;
  /** OUR OWN display name. A bot is in every meeting it records and appears in the roster like any
   *  participant; without this the lane cannot tell its own name from a person's. */
  selfName?: string;
  /** Quiet period the roster must show before an elimination is drawn from it. */
  rosterSettleMs?: number;
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
  private readonly rosterSightings: number;
  private readonly rosterSettleMs: number;
  private readonly selfName: string;
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
  /** The ROSTER: display name → how many scans it has been sighted in. Who is in the room, which is
   *  a different question from who can be heard — and the only question an elimination can use. */
  private roster = new Map<string, number>();
  /** When the roster last showed a name it had never shown before. */
  private rosterChangedAt = -Infinity;
  /** Names the roster showed that no human can hold (our bot, a placeholder). Their PRESENCE is
   *  what matters: a set containing one is a set elimination cannot reason over. */
  private rosterPolluted = new Set<string>();
  /** The producer's own account of whether it could name everyone it could see. */
  private rosterCoverage: { named: number; participants: number } | null = null;
  /** lowercased name → the roster's own casing, so a tile that renders "leo" and a roster that
   *  renders "Leo" do not become two people, and the rendered form is the one Teams considers
   *  canonical. Never rewrites the name itself — a " (Unverified)" suffix is Teams' statement. */
  private canonical = new Map<string, string>();
  /** First-heard order → the stable Speaker A/B/C fallback label. */
  private order: string[] = [];
  /** The letter each unnamed track is currently rendered under, so a shift can be repainted. */
  private letters = new Map<string, string>();

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
    this.rosterSightings = opts.rosterSightings ?? TRACK_NAME_ROSTER_SIGHTINGS;
    this.rosterSettleMs = opts.rosterSettleMs ?? TRACK_NAME_ROSTER_SETTLE_MS;
    this.selfName = normalizeNameForIdentity(opts.selfName ?? '');
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
    if (!name || this.unnameable(name)) return;
    this.paint(name, tMs - HINT_LAG_MS, HINT_GRACE_MS, 'dom', isEnd);
  }

  /** The platform's own captions attributed speech to `name` at `tMs`. */
  recordCaption(name: string, tMs: number): void {
    if (!name || this.unnameable(name)) return;
    // A caption describes the speech BEFORE its timestamp, so it paints backwards.
    this.paintSpan(name, tMs - CAPTION_LAG_MS - CAPTION_WINDOW_MS, tMs - CAPTION_LAG_MS, 'caption');
    this.newest = Math.max(this.newest, tMs);
  }

  /**
   * The roster says this person is in the meeting.
   *
   * NOT a hint, and deliberately carries no time: a roster name says nothing about who is speaking,
   * and treating one as speaking evidence would attribute a turn to somebody for merely being
   * present. It does exactly two things — it supplies the canonical CASING for a name the tiles may
   * render differently, and it makes ELIMINATION possible.
   */
  recordRosterName(name: string, tMs?: number): void {
    const trimmed = (name || '').trim();
    if (!trimmed) return;
    // A bot or a placeholder in the roster does not merely fail to name its own track — it makes
    // the whole set unusable for elimination, because "the only name left" is only an argument if
    // every name in the set could have been a person. Recorded as POLLUTION rather than dropped, so
    // the refusal is visible instead of looking like an empty roster.
    if (this.unnameable(trimmed)) {
      if (!this.rosterPolluted.has(trimmed)) {
        this.rosterPolluted.add(trimmed);
        this.log(`roster carries a name no human can hold — elimination is off for this meeting`);
      }
      return;
    }
    if (!this.roster.has(trimmed)) this.rosterChangedAt = Math.max(tMs ?? this.newest, this.newest);
    this.roster.set(trimmed, (this.roster.get(trimmed) ?? 0) + 1);
    this.canonical.set(trimmed.toLowerCase(), trimmed);
    if (tMs !== undefined) this.newest = Math.max(this.newest, tMs);
    // A roster sighting can arrive after a track was already named from a tile that rendered the
    // same person in a different case. Re-render under the roster's form so one participant does
    // not read as two in the transcript — same person, same rows, one repaint.
    for (const [trackId, naming] of this.named) {
      const display = this.canonicalCase(naming.name);
      if (display === naming.name) continue;
      naming.name = display;
      this.owner.set(display, trackId);
      this.log(`track ${trackId} rendered as "${display}" (roster casing)`);
      this.onNamed?.(trackId, display);
    }
    this.eliminate(tMs ?? this.upTo);
  }

  /** Is this a name no human can be published under — a placeholder the platform uses when it does
   *  not know who someone is, or our own bot? Applied to EVERY naming path, not only elimination:
   *  a placeholder that reaches the transcript looks attributed, so nothing downstream ever asks
   *  again, which makes it worse than an honest blank. */
  private unnameable(name: string): boolean {
    const id = normalizeNameForIdentity(name);
    if (!id) return true;
    if (PLACEHOLDER_NAMES.has(id)) return true;
    if (PLACEHOLDER_NAMES.has(name.trim().toLowerCase())) return true;
    return !!this.selfName && id === this.selfName;
  }

  /** The roster's casing for a name, when the roster has seen it. Otherwise the name unchanged —
   *  inventing a capitalisation nobody rendered would be a different kind of guess.
   *
   *  PUBLIC because every naming path needs it, not only the track spine: the DOM tiles render
   *  "leo (Unverified)" and the roster renders "Leo (Unverified)", and a transcript that carries
   *  both is a transcript with one person in it twice. */
  canonicalCase(name: string): string {
    return this.canonical.get(name.toLowerCase()) ?? name;
  }

  /**
   * The producer's own account of how much of the roster it could actually read: how many
   * participants it saw, and how many of those it could put a name to.
   *
   * Elimination is an argument from a COMPLETE set, so it needs to know when the set is not. A scan
   * that sees four tiles and names two is not a roster of two people — it is a roster of four with
   * two missing, and the difference is invisible from the names alone.
   */
  recordRosterCoverage(named: number, participants: number, tMs?: number): void {
    this.rosterCoverage = { named, participants };
    if (tMs !== undefined) this.newest = Math.max(this.newest, tMs);
  }

  /** Move the integrator's clock (called on every audio frame — cheap when nothing moved). */
  tick(tMs: number): void {
    this.newest = Math.max(this.newest, tMs);
    this.advance(this.newest - this.settleMs);
    // Elimination is time-gated on a quiet roster, so it has to be re-tested as time passes — no
    // further roster sighting is coming once the room has settled.
    this.eliminate(this.newest);
  }

  /** Integrate everything, ignoring the settle delay. Teardown only. */
  finish(): void { this.advance(this.newest); this.eliminate(this.newest); }

  // ── outputs ───────────────────────────────────────────────────────────────────────────────────

  /** The name this track earned, or null. NEVER a guess. */
  nameFor(trackId: string): string | null { return this.named.get(trackId)?.name ?? null; }

  /** The roster as this namer knows it: name → sightings. */
  rosterNames(): Record<string, number> { return Object.fromEntries(this.roster); }

  /** What a turn on this track publishes under right now: the earned name, else a stable
   *  "Speaker A/B/C" by first-heard order. */
  labelFor(trackId: string): string {
    const n = this.nameFor(trackId);
    if (n) return n;
    this.noteHeard(trackId);
    // Letters run over the UNNAMED tracks only, in first-heard order. Numbering by absolute track
    // order instead produced a transcript showing "Speaker B" with no Speaker A anywhere in it —
    // the reader is left hunting for a speaker who does not exist, because A had meanwhile earned a
    // real name. When a track is named it releases its letter and the rest close up (relabel), and
    // those rows repaint through the same path a rename uses.
    const unnamed = this.order.filter((t) => !this.named.has(t));
    const i = unnamed.indexOf(trackId);
    return speakerLabel(i < 0 ? unnamed.length : i);
  }

  /** Re-render the letters after a track leaves the unnamed pool, repainting what shifted. */
  private relabelUnnamed(): void {
    const unnamed = this.order.filter((t) => !this.named.has(t));
    unnamed.forEach((trackId, i) => {
      const label = speakerLabel(i);
      if (this.letters.get(trackId) === label) return;
      this.letters.set(trackId, label);
      this.onNamed?.(trackId, label);
    });
  }

  /** Register a track in first-heard order (idempotent); returns its index. */
  noteHeard(trackId: string): number {
    const i = this.order.indexOf(trackId);
    if (i >= 0) return i;
    this.order.push(trackId);
    // A newly heard track changes how many tracks are unnamed, which is half of the elimination
    // test — including the case where hearing a SECOND track makes a pending elimination invalid.
    this.eliminate(this.upTo);
    return this.order.length - 1;
  }

  naming(trackId: string): TrackNaming | null { return this.named.get(trackId) ?? null; }

  stats(): {
    tracks: number; named: number; evidence: Record<string, Record<string, number>>;
    roster: Record<string, number>; how: Record<string, string>;
    rosterPolluted: string[]; rosterCoverage: { named: number; participants: number } | null;
  } {
    const ev: Record<string, Record<string, number>> = {};
    for (const [track, m] of this.evidence) {
      ev[track] = {};
      for (const [name, e] of m) ev[track][name] = Math.round(e.supportMs);
    }
    const how: Record<string, string> = {};
    for (const [t, n] of this.named) how[t] = `${n.name} [${n.source}]`;
    return {
      tracks: this.order.length, named: this.named.size, evidence: ev, roster: this.rosterNames(), how,
      rosterPolluted: [...this.rosterPolluted],
      rosterCoverage: this.rosterCoverage,
    };
  }

  reset(): void {
    this.trackSpans.clear(); this.nameSpans.clear(); this.evidence.clear();
    this.named.clear(); this.owner.clear(); this.roster.clear(); this.canonical.clear();
    this.rosterPolluted.clear(); this.rosterCoverage = null; this.order = []; this.letters.clear();
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
    // EVIDENCE FOR SOMEONE ELSE'S NAME IS NOT DISAGREEMENT. A track accrues time for a name that
    // another track has ALREADY EARNED purely because the tiles lag the audio by about a second —
    // the bleed this file's exclusivity rule exists to catch. Once that name is settled on another
    // track the bleed is a known contaminant, and leaving it in the denominator lets it veto a
    // name the track genuinely holds.
    //
    // Measured on the real m34 tape: track 201 carried 10.4 s for "Dmitry Grankin" and 7.1 s of
    // bleed for "leo (Unverified)", which track 414 owned outright with 82.8 s. Dmitry's share came
    // to 0.59, under the bar, and a participant with ten seconds of unambiguous evidence was
    // published as "Speaker A" for the whole meeting.
    //
    // This is not a loosened bar. The discount only removes names another track has already earned
    // under the full rules, the winner still has to clear corroboration and the same share, and it
    // still has to hold the clear majority of its OWN name across every track.
    const mine = [...byName.values()].filter((e) => {
      const holder = this.owner.get(e.name) ?? this.owner.get(this.canonicalCase(e.name));
      return holder === undefined || holder === trackId;
    });
    const ranked = mine.sort((x, y) => y.supportMs - x.supportMs);
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
    this.bind(trackId, lead.name, 'evidence', share, ranked, atMs,
      `${Math.round(lead.supportMs)}ms over ${lead.episodes} episode(s), share ${share.toFixed(2)}`);
  }

  /** Accept a name for a track, once. The single place a name is ever attached. */
  private bind(
    trackId: string, name: string, source: 'evidence' | 'elimination',
    confidence: number, evidence: TrackEvidence[], atMs: number, why: string,
  ): void {
    const display = this.canonicalCase(name);
    this.named.set(trackId, { name: display, confidence, source, evidence, atMs });
    this.owner.set(name, trackId);
    if (display !== name) this.owner.set(display, trackId);
    this.noteHeard(trackId);
    this.log(`track ${trackId} → "${display}" [${source}] (${why})`);
    this.letters.delete(trackId);
    this.onNamed?.(trackId, display);
    this.relabelUnnamed();
    // Settling THIS name can free another track's evidence from a contaminant — re-test the rest.
    for (const other of this.order) if (other !== trackId && !this.named.has(other)) this.evaluate(other, atMs);
    // A binding can complete the last pairing, so the elimination is re-tested after every one.
    this.eliminate(atMs);
  }

  /**
   * THE ELIMINATION RULE. When exactly ONE track is still unnamed and exactly ONE roster name is
   * still unclaimed, they are each other — there is no other pairing left.
   *
   * This is what the m30 fixture needs and nothing else can supply: the DOM outline named one of
   * two participants for the entire meeting, so the other could never be named from speaking
   * evidence however long anyone listened. He was in the roster the whole time.
   *
   * It is deliberately the STRICTEST possible form of the argument, because a loose one fabricates:
   *
   *   • two or more unnamed tracks  ⇒ nothing fires. Which of them is the unclaimed name is a
   *     coin toss, and a coin toss that prints a human's name is the defect this lane exists to end.
   *   • two or more unclaimed names ⇒ nothing fires, for the same reason in the other direction.
   *   • a track that already has a name is NEVER touched. Elimination is a last resort, never an
   *     override — direct evidence always wins, and a name reached this way is recorded as
   *     'elimination' so a reader can tell the two apart.
   *   • a roster name is only usable once it has been sighted in `rosterSightings` separate scans,
   *     so a name that flickered through a rotting selector cannot pair with anything.
   *
   * Silence in a 3-and-3 meeting is the correct answer, not a gap to close later.
   */
  private eliminate(atMs: number): void {
    // POLLUTION. If the roster showed even one name that no human can hold — our own bot, a
    // platform placeholder — then "the only name left" is not an argument about people. On the m34
    // meeting the roster held our bot, exactly one track and one name remained, and a BOT'S NAME
    // WAS PUT ON A HUMAN'S SPEECH. One polluted entry disables the rule for the meeting.
    if (this.rosterPolluted.size > 0) return;
    // INCOMPLETENESS. Elimination's premise is that the roster lists everyone; if the producer saw
    // participants it could not name, the unclaimed set is missing people and the "only one left"
    // is an artefact of what we failed to read. m34 again: four tiles, two names — and the two it
    // could not name included the human the rule then mislabelled.
    if (this.rosterCoverage && this.rosterCoverage.named < this.rosterCoverage.participants) return;
    const unnamed = this.order.filter((t) => !this.named.has(t));
    if (unnamed.length !== 1) return;
    // A ROSTER THAT IS STILL FILLING IS NOT A ROSTER YOU CAN ELIMINATE AGAINST. Sightings arrive one
    // name at a time, so between the moment the first name crosses the corroboration threshold and
    // the moment the second does, the set looks exactly like a decidable 1-and-1 — and eliminating
    // there is a race, not an argument. It produced the right answer on the m30 fixture for entirely
    // the wrong reason, which is worse than producing the wrong one: it would have gone unnoticed.
    // So every name the roster has shown must have finished corroborating before any pairing counts.
    for (const sightings of this.roster.values()) if (sightings < this.rosterSightings) return;
    // …and it must have been QUIET. A roster fills one name at a time, so between the first name
    // corroborating and the second appearing it looks exactly like a decidable 1-and-1. Eliminating
    // there is a race, not an argument — on the real m30 tape it produced the RIGHT answer for
    // entirely the WRONG reason, which is how it stayed invisible.
    if (Math.max(atMs, this.newest) - this.rosterChangedAt < this.rosterSettleMs) return;
    const unclaimed = [...this.roster.keys()]
      .filter((n) => !this.owner.has(n) && !this.owner.has(this.canonicalCase(n)));
    if (unclaimed.length !== 1) return;
    this.bind(unnamed[0], unclaimed[0], 'elimination', 1, [], atMs,
      `the only unnamed track and the only unclaimed roster name`);
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
