/**
 * TrackNamer — which HUMAN a transport track is.
 *
 * The transport spine (turn-source.ts) gives every turn a `trackId` that is stable for one
 * transport epoch but meaningless: `1266` is a number an RTP mixer chose. A reconnect may retire
 * that id and allocate a new one to the same human. The DOM tiles and the closed
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
 *     NAME's evidence. It can cross to a strictly later, sustained-evidence transport epoch, but
 *     never to a track that predates, interleaves with, or overlaps its owner. On the m30 fixture the other
 *     participant's track accrues 7.5s of "leo" evidence purely from hint lag against leo's 61s —
 *     without this rule both tracks would end up called leo, which is two wrong labels produced
 *     from correct data.
 *   • **A settle delay.** Evidence is integrated only up to `newest - settleMs`, so a hint that
 *     arrives late still lands on the span it describes. Names therefore arrive a few seconds
 *     after the speech does — which is what the retroactive rename path is FOR.
 *   • **Revisable until the roster settles.** A name earned while the roster is still discovering
 *     the room is published at once but stays open to revision, because the exclusivity that earned
 *     it was measured against a name universe that was incomplete. See `rosterSettled`.
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
/**
 * A SINGLE episode this long satisfies corroboration on its own.
 *
 * The count rule exists because one coincidence is a coincidence — but it can only count, and that
 * makes it blind to strength. On the first prod fixture two participants each accrued one unbroken
 * stretch of exclusive coincidence (3.1 s and 3.5 s, sole audible source, sole lit tile, share 1.0)
 * and were refused, because neither ever got a SECOND stretch: in a six-person room a quieter
 * speaker talks once while the tiles happen to be unambiguous and then never again. They spent the
 * meeting unnamed on evidence far stronger than the two 600 ms episodes the rule would have
 * accepted without hesitation.
 *
 * So duration is an alternative route to the same bar, not a lowering of it: share and the
 * cross-track owner share are still enforced exactly as before, and a long stretch that is
 * ambiguous still fails them.
 */
export const TRACK_NAME_SOLO_EPISODE_MS = envNumber('VEXA_TRACK_NAME_SOLO_EPISODE_MS', 2500);
/** The winner's share of THIS TRACK's evidence. Below it the tiles disagree about who the track is. */
export const TRACK_NAME_MIN_SHARE = envNumber('VEXA_TRACK_NAME_MIN_SHARE', 0.7);
/** The winner's share of THIS NAME's evidence across all tracks. Below it two tracks are both
 *  claiming one person and neither may have them. */
export const TRACK_NAME_MIN_OWNER_SHARE = envNumber('VEXA_TRACK_NAME_OWNER_SHARE', 0.7);
/** How far behind the newest event evidence is integrated, so late signals land on the right span. */
export const TRACK_NAME_SETTLE_MS = envNumber('VEXA_TRACK_NAME_SETTLE_MS', 3000);
/** A second track may carry the same human only when direct evidence is much stronger than the
 *  ordinary naming floor. These gates separate the two shapes that both look like a duplicate
 *  claim: UI-lag bleed onto the wrong human accumulates as MANY SHORT episodes of roughly a second
 *  each, while a genuine CSRC rotation accumulates as FEWER SUSTAINED ones. Total support, episode
 *  count and mean episode length must all clear their floors; the mean is the discriminator. */
export const TRACK_NAME_SUCCESSOR_MIN_SUPPORT_MS = envNumber('VEXA_TRACK_NAME_SUCCESSOR_MIN_SUPPORT_MS', 10_000);
export const TRACK_NAME_SUCCESSOR_EPISODES = envNumber('VEXA_TRACK_NAME_SUCCESSOR_EPISODES', 3);
export const TRACK_NAME_SUCCESSOR_AVG_EPISODE_MS = envNumber('VEXA_TRACK_NAME_SUCCESSOR_AVG_EPISODE_MS', 2000);
export const TRACK_NAME_SUCCESSOR_MIN_SHARE = envNumber('VEXA_TRACK_NAME_SUCCESSOR_MIN_SHARE', 0.9);

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
/** How long the roster must have shown NO NEW NAME before it counts as SETTLED.
 *  A roster fills one name at a time, so it passes through states that look decidable and are not.
 *  Elimination may not be drawn from an unsettled roster, and a name earned from evidence against
 *  one is published but stays revisable — see `rosterSettled`. */
export const TRACK_NAME_ROSTER_SETTLE_MS = envNumber('VEXA_TRACK_NAME_ROSTER_SETTLE_MS', 5000);

/**
 * Names that must never become a speaker, checked HERE as well as at the platform guard.
 *
 * The producer's guard (teams-capture's isTeamsDisplayNameCandidate) is the door, and it is the
 * right place to close this. But this file binds a name to a human being on the strength of an
 * argument from absence, and the m34 meeting is what that costs when the input is polluted: the
 * roster contained our OWN BOT, elimination had exactly one track and one name left, and a bot's
 * name went onto a person's speech. A rule whose premise is 'nothing else it could be' must check
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

const IDENTITY_QUALIFIERS = new Set([
  'unverified', 'guest', 'bot', 'external', 'extern', 'invite', 'invité', 'gast', 'gość', 'гость', '外部',
]);

const stripParenthesizedIdentitySuffix = (
  value: string,
  accepts: (suffix: string) => boolean,
): string => {
  const trimmed = value.trimEnd();
  if (!trimmed.endsWith(')')) return trimmed;
  const open = trimmed.lastIndexOf('(');
  if (open <= 0 || !/\s/u.test(trimmed[open - 1])) return trimmed;
  const suffix = trimmed.slice(open + 1, -1);
  return accepts(suffix) ? trimmed.slice(0, open).trimEnd() : trimmed;
};

const isAsciiDigits = (value: string): boolean => {
  if (!value) return false;
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code < 48 || code > 57) return false;
  }
  return true;
};

const stripNumericIdentitySuffix = (value: string): string => {
  let start = value.length;
  while (start > 0) {
    const code = value.charCodeAt(start - 1);
    if (code < 48 || code > 57) break;
    start -= 1;
  }
  if (start === value.length || start === 0 || !/\s/u.test(value[start - 1])) return value;
  return value.slice(0, start).trimEnd();
};

/** Teams' qualifiers, stripped for identity comparison only — never for display. */
export function normalizeNameForIdentity(value: string): string {
  let normalized = String(value || '').trimEnd();
  for (;;) {
    const previous = normalized;
    normalized = stripParenthesizedIdentitySuffix(
      normalized,
      (suffix) => IDENTITY_QUALIFIERS.has(suffix.toLowerCase()),
    );
    normalized = stripParenthesizedIdentitySuffix(normalized, isAsciiDigits);
    normalized = stripNumericIdentitySuffix(normalized);
    if (normalized === previous) break;
  }
  return normalized.trim().toLowerCase();
}

// meeting-api's exact fallback when a caller supplies no bot name. This reserved machine namespace
// is safe to recognize; fuzzy role words ("bot", "assistant", "notetaker") are not.
const GENERATED_DEFAULT_BOT_ID = /^vexabot-[0-9a-f]{6}$/iu;

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
  /** Earlier transport epoch carrying this same human, when identity survived a CSRC rotation. */
  successorOf?: string;
}

export interface TrackNamerOptions {
  corroborations?: number;
  minEpisodeMs?: number;
  minShare?: number;
  minOwnerShare?: number;
  settleMs?: number;
  successorMinSupportMs?: number;
  successorEpisodes?: number;
  successorAvgEpisodeMs?: number;
  successorMinShare?: number;
  /** A track earned a name. The host repaints everything that track already published. */
  onNamed?: (trackId: string, name: string) => void;
  /** Roster sightings required before elimination may use a name. */
  rosterSightings?: number;
  /** A single episode of at least this much exclusive coincidence corroborates on its own. */
  soloEpisodeMs?: number;
  /** OUR OWN display name. A bot is in every meeting it records and appears in the roster like any
   *  participant; without this the lane cannot tell its own name from a person's. */
  selfName?: string;
  /** Teams-only fail-closed policy: refuse a bare all-lowercase handle/media label as a canonical
   *  human identity. Legacy callers keep their deployed behavior unless they opt in. */
  requireCanonicalDisplayName?: boolean;
  /** Quiet period the roster must show before an elimination is drawn from it. */
  rosterSettleMs?: number;
  /** A typed account of a naming decision worth seeing in a tape. Currently only the bot-family
   *  refusal, which is otherwise invisible: nothing is published, so the tape shows an absence. */
  onObservation?: (o: TrackNamerObservation) => void;
  log?: (m: string) => void;
}

/** Emitted when the roster carried a name from our own bot's family — the state that put a bot's
 *  name on a human's speech in meeting 25930. */
export interface TrackNamerObservation {
  type: 'bot-family-in-roster';
  name: string;
  stem: string;
  tMs: number;
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
  private readonly successorMinSupportMs: number;
  private readonly successorEpisodes: number;
  private readonly successorAvgEpisodeMs: number;
  private readonly successorMinShare: number;
  private readonly rosterSightings: number;
  private readonly soloEpisodeMs: number;
  private readonly rosterSettleMs: number;
  private readonly selfName: string;
  private readonly selfStem: string;
  private readonly requireCanonicalDisplayName: boolean;
  private readonly log: (m: string) => void;
  onNamed?: (trackId: string, name: string) => void;
  onObservation?: (o: TrackNamerObservation) => void;
  /** Roster entries refused as bot-family. Tracked apart from the other pollution because it is
   *  the only kind the producer COUNTED AS NAMED — so it is the only kind coverage must discount. */
  private rosterBotFamily = new Set<string>();

  /** Audible spans per track (the transport's own account). */
  private trackSpans = new Map<string, Interval[]>();
  /** Epoch boundaries are retained for the whole meeting even after old spans are pruned. */
  private trackFirstSeen = new Map<string, number>();
  private trackLastEnded = new Map<string, number>();
  private activeTracks = new Set<string>();
  /** Lit spans per name, from any naming signal. */
  private nameSpans = new Map<string, Interval[]>();

  /** (trackId → name → evidence). */
  private evidence = new Map<string, Map<string, TrackEvidence>>();
  private named = new Map<string, TrackNaming>();
  /** Tracks whose evidence-based name was earned while the roster was NOT settled. They are
   *  published like any other name — withholding one costs latency in every meeting to buy nothing
   *  — but they are re-derived on every evaluation and may change or be withdrawn until the roster
   *  settles. See `rosterSettled`. */
  private provisional = new Set<string>();
  /** name → the first track that earned it. Later transport epochs may join the same identity but
   *  never replace this anchor, so elimination and ordinary exclusivity still see it as claimed. */
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
  /** Raw, lag-corrected hint arrivals — the POSITIVE evidence, as opposed to the merged intervals
   *  above which are mostly the absence of an end event. Bounded like everything else here. */
  private hintEvents: Array<{ name: string; tMs: number }> = [];
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
  /** Transport succession is a whole-meeting claim. An old CSRC can always return later, so a
   *  successor is never published live; finalization is the first instant non-overlap is proved. */
  private finalizing = false;
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
    this.successorMinSupportMs = opts.successorMinSupportMs ?? TRACK_NAME_SUCCESSOR_MIN_SUPPORT_MS;
    this.successorEpisodes = opts.successorEpisodes ?? TRACK_NAME_SUCCESSOR_EPISODES;
    this.successorAvgEpisodeMs = opts.successorAvgEpisodeMs ?? TRACK_NAME_SUCCESSOR_AVG_EPISODE_MS;
    this.successorMinShare = opts.successorMinShare ?? TRACK_NAME_SUCCESSOR_MIN_SHARE;
    this.rosterSightings = opts.rosterSightings ?? TRACK_NAME_ROSTER_SIGHTINGS;
    this.soloEpisodeMs = opts.soloEpisodeMs ?? TRACK_NAME_SOLO_EPISODE_MS;
    this.rosterSettleMs = opts.rosterSettleMs ?? TRACK_NAME_ROSTER_SETTLE_MS;
    this.selfName = normalizeNameForIdentity(opts.selfName ?? '');
    this.requireCanonicalDisplayName = opts.requireCanonicalDisplayName ?? false;
    // Our bot's FAMILY, not its exact name. Every bot we spawn derives its display name from the
    // same configured stem ("Vexa test", "Vexa", "Vexa (Unverified)"), so the first token is what
    // identifies one — and identity-equality against our own full name, which is all we had, sees
    // a SECOND bot as a person. Self-hosters rebrand freely, so the stem is read from selfName
    // rather than hard-coded.
    this.selfStem = this.selfName.split(' ')[0] ?? '';
    this.onNamed = opts.onNamed;
    this.onObservation = opts.onObservation;
    this.log = opts.log ?? (() => { /* silent */ });
  }

  // ── inputs ────────────────────────────────────────────────────────────────────────────────────

  /** The transport says a source became (in)audible. */
  setTrackActive(trackId: string, active: boolean, tMs: number): void {
    this.newest = Math.max(this.newest, tMs);
    this.noteOrigin(tMs);
    if (!this.trackFirstSeen.has(trackId)) this.trackFirstSeen.set(trackId, tMs);
    if (active) this.activeTracks.add(trackId);
    else {
      this.activeTracks.delete(trackId);
      this.trackLastEnded.set(trackId, Math.max(this.trackLastEnded.get(trackId) ?? -Infinity, tMs));
    }
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
    if (!isEnd) {
      this.hintEvents.push({ name, tMs: tMs - HINT_LAG_MS });
      if (this.hintEvents.length > 20_000) this.hintEvents.splice(0, this.hintEvents.length - 20_000);
    }
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
    // A SECOND BOT IS NOT A PERSON. The check above asks "is this name OURS"; this one asks "is it
    // one of OUR KIND". A room can carry a second bot of our own family (e.g. "Vexa (Unverified)");
    // when the DOM yields nothing, a roster of exactly that one name reads as complete, and
    // elimination would bind a human's speech to a bot. Scoped deliberately to the roster: a real
    // person named Vexa can still be named by DIRECT evidence (recordHint/recordCaption do not consult this), so the
    // worst this can cost is an elimination we decline to draw, never a human who cannot be named.
    if (this.botFamily(trimmed)) {
      if (!this.rosterBotFamily.has(trimmed)) {
        this.rosterBotFamily.add(trimmed);
        this.rosterPolluted.add(trimmed);
        this.log(`roster carries "${trimmed}" from our own bot family (stem "${this.selfStem}") — elimination is off for this meeting`);
        this.onObservation?.({ type: 'bot-family-in-roster', name: trimmed, stem: this.selfStem, tMs: tMs ?? this.newest });
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
      const anchor = this.owner.get(naming.name) ?? this.owner.get(display) ?? trackId;
      this.owner.set(display, anchor);
      this.log(`track ${trackId} rendered as "${display}" (roster casing)`);
      this.onNamed?.(trackId, display);
    }
    this.eliminate(tMs ?? this.upTo);
  }

  /** Is this a name no human can be published under — a placeholder the platform uses when it does
   *  not know who someone is, or our own bot? Applied to EVERY naming path, not only elimination:
   *  a placeholder that reaches the transcript looks attributed, so nothing downstream ever asks
   *  again, which makes it worse than an honest blank. */
  /** Is this name one of OUR OWN KIND — a bot spawned from the same configured stem? Distinct from
   *  `unnameable`, which asks whether the name is ours exactly. Roster-only by design. */
  private botFamily(name: string): boolean {
    const id = normalizeNameForIdentity(name);
    if (!id) return false;
    if (GENERATED_DEFAULT_BOT_ID.test(id)) return true;
    if (!this.selfStem) return false;
    return id.split(' ')[0] === this.selfStem;
  }

  private unnameable(name: string): boolean {
    const id = normalizeNameForIdentity(name);
    if (!id) return true;
    if (this.requireCanonicalDisplayName && /^\p{Ll}[\p{Ll}\p{M}\p{Nd}]*$/u.test(name.trim())) return true;
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
    // A ROSTER OF ONLY BOTS IS NOT A COMPLETE ROSTER. The producer counts a bot as a participant it
    // could name, so a room holding one other bot reports named=1/participants=1 — the *most*
    // complete a roster can look, and the state elimination trusts most. Discounting the bot-family
    // entries turns 25930's roster into named=0/participants=1: incomplete, which is the truth.
    const effectiveNamed = Math.max(0, named - this.rosterBotFamily.size);
    this.rosterCoverage = { named: effectiveNamed, participants };
    if (tMs !== undefined) this.newest = Math.max(this.newest, tMs);
  }

  /**
   * IS THE ROSTER A SET WE MAY DECIDE AGAINST YET?
   *
   * Two conditions, and the second one is what meeting 26424 cost. There, track 201 was accepted as
   * "Matt" at +35 s on 3.8 s of evidence — correctly, by every rule this file had, because the
   * roster held exactly one name and had held it, quietly, for thirty-five seconds. The second
   * participant's first roster sighting was ten seconds away. Acceptance was permanent, so the 93 %
   * contrary evidence of the next 28 minutes could not revise it, exclusivity then refused "Matt"
   * to the man who is Matt, and one human's entire side of the meeting printed under the other's
   * name.
   *
   *   1. **Quiet.** No name the roster had never shown before, for `rosterSettleMs`.
   *   2. **Not provably incomplete.** The transport has not heard MORE distinct humans than the
   *      roster lists. A roster of one, while two sources are audible, is not a settled roster — it
   *      is a roster that has not caught up, however long it has been still. This is the only signal
   *      available at +35 s, and it is a hard contradiction rather than an inference.
   *
   * A meeting whose roster never reports at all is settled by definition: there is nothing to wait
   * for, and blocking on a signal that will never arrive would suppress every name in the meeting.
   */
  private rosterSettled(atMs: number): boolean {
    if (this.rosterChangedAt === -Infinity) return true;
    if (Math.max(atMs, this.newest) - this.rosterChangedAt < this.rosterSettleMs) return false;
    return this.roster.size >= this.trackFirstSeen.size;
  }

  /** Move the integrator's clock (called on every audio frame — cheap when nothing moved). */
  tick(tMs: number): void {
    this.newest = Math.max(this.newest, tMs);
    this.advance(this.newest - this.settleMs);
    // A provisional name is re-derived as time passes, not only when new evidence lands: the roster
    // settling is itself an event, and the instant it settles the name it leaves standing is frozen.
    if (this.provisional.size > 0) for (const trackId of [...this.provisional]) this.evaluate(trackId, this.newest);
    // Elimination is time-gated on a quiet roster, so it has to be re-tested as time passes — no
    // further roster sighting is coming once the room has settled.
    this.eliminate(this.newest);
  }

  /** Integrate everything, ignoring the settle delay. Teardown only. */
  finish(): void {
    this.finalizing = true;
    this.advance(this.newest);
    // Successor evidence may have crossed its threshold long before teardown. Re-evaluate every
    // unnamed track now that the complete transport history can prove no predecessor returned.
    for (const trackId of this.evidence.keys()) if (!this.named.has(trackId)) this.evaluate(trackId, this.newest);
    // Last word on anything still open: the roster will not change again after teardown.
    for (const trackId of [...this.provisional]) this.evaluate(trackId, this.newest);
    this.eliminate(this.newest);
    this.finalizing = false;
  }

  // ── outputs ───────────────────────────────────────────────────────────────────────────────────

  /** The name this track earned, or null. NEVER a guess. */
  nameFor(trackId: string): string | null { return this.named.get(trackId)?.name ?? null; }

  /**
   * The name whose tile was RE-ASSERTED most often around this instant, or null when nothing leads.
   *
   * `litNameAt` asks whether a tile was lit, and on the staging Jacob call that question could not
   * separate anybody: the mixer held both sources active for the whole of each contested span and
   * both tiles read lit throughout, so every one of eighteen spans in the middle of one man's
   * continuous speech published as "Speaker" — with correctly named rows on both sides of them.
   *
   * A lit INTERVAL is mostly the absence of an end event: it is extended by grace and it survives
   * silence. A hint EVENT is positive evidence, emitted when the watcher sees the outline move, and
   * a speaker who is actually talking re-emits one every couple of seconds. Counting the events
   * rather than measuring the interval separates the person speaking from the person the mixer is
   * merely still carrying — 1308 events for the man speaking against 97 for the man held open, on
   * that call.
   *
   * A strict majority is required, so two people genuinely talking at once still resolves to
   * nobody.
   */
  hintLeaderAt(tMs: number, tolMs: number, candidates?: string[]): string | null {
    const from = tMs - tolMs;
    const to = tMs + tolMs;
    // RANK AMONG THE PEOPLE WHO COULD ACTUALLY BE SPEAKING. In a six-person room the loudest tile
    // around an instant is often somebody who is not even in the mix at that moment, and returning
    // them meant the caller discarded the verdict and fell through to a weaker test — which then
    // picked the wrong one of the two who WERE audible. Measured on the first prod fixture: a span
    // where the transport had Shannon and Matt, the tiles said Shannon 2 / Matt 1, and the global
    // leader was Liam with 4 — so the answer went to Matt. Restricting the ballot to the audible
    // owners is not a weaker test, it is the question actually being asked.
    const allowed = candidates && candidates.length
      ? new Set(candidates.map((c) => this.canonicalCase(c).toLowerCase()))
      : null;
    const votes = new Map<string, number>();
    for (const ev of this.hintEvents) {
      if (ev.tMs < from) continue;
      if (ev.tMs > to) break;                    // the log is append-ordered
      const key = this.canonicalCase(ev.name);
      if (allowed && !allowed.has(key.toLowerCase())) continue;
      votes.set(key, (votes.get(key) ?? 0) + 1);
    }
    if (votes.size === 0) return null;
    const ranked = [...votes.entries()].sort((a, b) => b[1] - a[1]);
    if (ranked.length > 1 && ranked[0][1] === ranked[1][1]) return null;   // a tie decides nothing
    return ranked[0][0];
  }

  /** Has the UI ever testified about this TRACK — has it, at any point, been the sole audible
   *  source while exactly one tile was lit? That is the question the contested tie-break needs, and
   *  it is answerable for a track the namer has not yet been able to NAME. Requiring a name instead
   *  starved the rule in exactly the rooms it exists for: on the first prod fixture six sources
   *  produced six roster names, only three ever bound, and every contested span touching one of the
   *  other three was refused — 26 of 69 blank rows whose hints could decide them outright. */
  hasTileTestimony(trackId: string): boolean {
    const byName = this.evidence.get(trackId);
    if (!byName) return false;
    for (const e of byName.values()) if (e.supportMs > 0) return true;
    return false;
  }

  /** How many times this name's tile has been re-asserted all session. Zero means the UI has never
   *  once testified about this person — and a signal that has never named someone cannot be read as
   *  evidence that they are NOT the one speaking. */
  hintEvidenceFor(name: string): number {
    const want = this.canonicalCase(name).toLowerCase();
    let n = 0;
    for (const ev of this.hintEvents) if (this.canonicalCase(ev.name).toLowerCase() === want) n++;
    return n;
  }

  /** The ONE name the UI showed lit at this instant, or null when none or several were. Used only
   *  to break a tie the transport itself cannot: when two sources are audible, the tiles are the
   *  second, independent opinion about which of THOSE TWO was speaking. Constrained to names the
   *  caller already trusts, so this can never introduce a name of its own. */
  litNameAt(tMs: number): string | null {
    let found: string | null = null;
    for (const [name, list] of this.nameSpans) {
      for (const iv of list) {
        if (iv.start <= tMs && tMs < iv.end) {
          if (found && found !== name) return null;    // two tiles lit — the UI cannot decide either
          found = name;
          break;
        }
      }
    }
    return found;
  }

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
    roster: Record<string, number>; how: Record<string, string>; lineage: Record<string, string>;
    rosterPolluted: string[]; rosterCoverage: { named: number; participants: number } | null;
  } {
    const ev: Record<string, Record<string, number>> = {};
    for (const [track, m] of this.evidence) {
      ev[track] = {};
      for (const [name, e] of m) ev[track][name] = Math.round(e.supportMs);
    }
    const how: Record<string, string> = {};
    const lineage: Record<string, string> = {};
    for (const [t, n] of this.named) how[t] = `${n.name} [${n.source}]`;
    for (const [t, n] of this.named) if (n.successorOf) lineage[t] = n.successorOf;
    return {
      tracks: this.order.length, named: this.named.size, evidence: ev, roster: this.rosterNames(), how, lineage,
      rosterPolluted: [...this.rosterPolluted],
      rosterCoverage: this.rosterCoverage,
    };
  }

  reset(): void {
    this.trackSpans.clear(); this.nameSpans.clear(); this.evidence.clear();
    this.named.clear(); this.owner.clear(); this.roster.clear(); this.canonical.clear();
    this.provisional.clear(); this.rosterChangedAt = -Infinity;
    this.trackFirstSeen.clear(); this.trackLastEnded.clear(); this.activeTracks.clear();
    this.rosterPolluted.clear(); this.rosterCoverage = null; this.order = []; this.letters.clear();
    this.hintEvents = [];
    this.upTo = 0; this.newest = 0; this.finalizing = false; this.origin = null; this.episode = null;
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
    const existing = this.named.get(trackId);
    // A PROVISIONAL name is re-derived rather than defended: it was earned against a roster that
    // had not finished discovering the room, so the later evidence is entitled to overturn it.
    const revisable = existing !== undefined && this.provisional.has(trackId);
    // A track named by ELIMINATION may still be re-examined, for one purpose only: to upgrade the
    // record when direct evidence arrives for the same name. Elimination can beat the evidence path
    // to the punch — on the m36 tape a track was labelled [elimination] while holding 46.9 s of its
    // own unambiguous DOM evidence, so the audit trail understated its own confidence and read as
    // the weaker claim. The NAME never changes here; only the account of how it was reached.
    if (existing && existing.source === 'evidence' && !revisable) return;
    const byName = this.evidence.get(trackId);
    if (!byName) return;
    // A transport id can rotate while the human does not. This path is intentionally evaluated
    // before owned-name evidence is discounted below, and is much stricter than ordinary naming.
    // It does not "re-let" a name to an alternating source: the candidate must be a wholly later
    // epoch and carry sustained, independently leading evidence rather than one-second UI bleed.
    if (!existing && this.evaluateSuccessor(trackId, byName, atMs)) return;
    if (revisable) { this.reconsider(trackId, existing!, atMs); return; }
    const verdict = this.verdict(trackId, byName);
    if (!verdict) return;
    const { lead, ranked, share } = verdict;
    if (existing) {
      // Same name, better provenance: upgrade in place. A DIFFERENT name is never accepted — an
      // elimination that has already been published is not overturned by later evidence, it is a
      // contradiction worth seeing rather than silently resolving.
      if (existing.name !== this.canonicalCase(lead.name) && existing.name !== lead.name) {
        this.log(`track ${trackId} evidence names "${lead.name}" but it was already eliminated to "${existing.name}" — keeping the published name`);
        return;
      }
      existing.source = 'evidence';
      existing.confidence = share;
      existing.evidence = ranked;
      this.log(`track ${trackId} upgraded to [evidence] (${Math.round(lead.supportMs)}ms over ${lead.episodes} episode(s))`);
      return;
    }
    this.bind(trackId, lead.name, 'evidence', share, ranked, atMs,
      `${Math.round(lead.supportMs)}ms over ${lead.episodes} episode(s), share ${share.toFixed(2)}`);
  }

  /**
   * Re-derive a name that was earned against an unsettled roster, and freeze it once the roster is.
   *
   * Three outcomes: the evidence still says the same person (keep it, and freeze if the roster has
   * settled); it now says someone else (repaint to them); or it no longer clears the bar at all
   * (withdraw — the track goes back to Speaker A/B/C, which is the honest state, and the name is
   * released so the track that genuinely owns it can earn it).
   */
  private reconsider(trackId: string, existing: TrackNaming, atMs: number): void {
    const byName = this.evidence.get(trackId);
    const verdict = byName ? this.verdict(trackId, byName) : null;
    const settled = this.rosterSettled(atMs);
    if (verdict && (this.canonicalCase(verdict.lead.name) === existing.name || verdict.lead.name === existing.name)) {
      existing.confidence = verdict.share;
      existing.evidence = verdict.ranked;
      if (settled) {
        this.provisional.delete(trackId);
        this.log(`track ${trackId} → "${existing.name}" settled (roster stable, ${Math.round(verdict.lead.supportMs)}ms)`);
      }
      return;
    }
    this.withdraw(trackId, verdict
      ? `evidence now names "${verdict.lead.name}"`
      : `evidence no longer supports it (roster was still filling when it was accepted)`);
    if (verdict) {
      this.bind(trackId, verdict.lead.name, 'evidence', verdict.share, verdict.ranked, atMs,
        `${Math.round(verdict.lead.supportMs)}ms over ${verdict.lead.episodes} episode(s), share ${verdict.share.toFixed(2)}`);
      return;
    }
    // A released name may be exactly what another track has been refused all meeting.
    for (const other of this.order) if (other !== trackId && !this.named.has(other)) this.evaluate(other, atMs);
  }

  /** Take a provisional name back off a track and release it. The host repaints, as it does for
   *  every other change of label; a wrong name left standing is the alternative. */
  private withdraw(trackId: string, why: string): void {
    const naming = this.named.get(trackId);
    if (!naming) return;
    this.named.delete(trackId);
    this.provisional.delete(trackId);
    for (const [name, holder] of [...this.owner]) if (holder === trackId) this.owner.delete(name);
    this.log(`track ${trackId} released "${naming.name}" — ${why}`);
    this.letters.delete(trackId);
    this.noteHeard(trackId);
    this.relabelUnnamed();
  }

  /** The name this track's evidence currently supports, or null. Pure: consults state, changes none. */
  private verdict(
    trackId: string,
    byName: Map<string, TrackEvidence>,
  ): { lead: TrackEvidence; ranked: TrackEvidence[]; share: number } | null {
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
    // Corroborated by COUNT (several separate coincidences) or by WEIGHT (one long unambiguous
    // one). Either is corroboration; only the count was expressible before.
    if (!lead) return null;
    const corroborated = lead.episodes >= this.corroborations || lead.supportMs >= this.soloEpisodeMs;
    if (!corroborated) return null;
    const total = ranked.reduce((s, e) => s + e.supportMs, 0);
    const share = total > 0 ? lead.supportMs / total : 0;
    if (share < this.minShare) return null;                        // this track's tiles disagree
    const holder = this.owner.get(lead.name) ?? this.owner.get(this.canonicalCase(lead.name));
    if (holder !== undefined && holder !== trackId) return null;   // that person is already someone else
    // Exclusivity the other way: does this track hold the clear majority of that NAME's evidence?
    let nameTotal = 0;
    for (const [, m] of this.evidence) { const e = m.get(lead.name); if (e) nameTotal += e.supportMs; }
    if (nameTotal > 0 && lead.supportMs / nameTotal < this.minOwnerShare) return null;
    return { lead, ranked, share };
  }

  /** Join a strictly later transport epoch to an already proved human identity. */
  private evaluateSuccessor(trackId: string, byName: Map<string, TrackEvidence>, atMs: number): boolean {
    if (!this.finalizing) return false;  // future transport activity can still falsify succession
    const ranked = [...byName.values()].sort((a, b) => b.supportMs - a.supportMs);
    const lead = ranked[0];
    if (!lead || lead.episodes < this.successorEpisodes || lead.supportMs < this.successorMinSupportMs) return false;
    if (lead.supportMs / lead.episodes < this.successorAvgEpisodeMs) return false;
    const total = ranked.reduce((sum, e) => sum + e.supportMs, 0);
    const share = total > 0 ? lead.supportMs / total : 0;
    if (share < this.successorMinShare) return false;

    const identity = normalizeNameForIdentity(this.canonicalCase(lead.name));
    const lineage = [...this.named.entries()].filter(([, naming]) =>
      normalizeNameForIdentity(this.canonicalCase(naming.name)) === identity);
    if (lineage.length === 0) return false;

    const first = this.trackFirstSeen.get(trackId);
    if (first === undefined || this.activeTracks.has(trackId) === false && !this.trackLastEnded.has(trackId)) return false;
    // Every proved epoch must have ended before this id was ever heard. This rejects m30's false
    // track, which predates and alternates with the owner, and any old id that reappears while the
    // successor is accumulating evidence.
    for (const [prior] of lineage) {
      if (this.activeTracks.has(prior)) return false;
      const ended = this.trackLastEnded.get(prior);
      if (ended === undefined || ended >= first) return false;
    }

    // The normal owner-share rule is applied to the whole transport lineage. A rotation splits one
    // person's evidence across ids; unrelated tracks remain outside the numerator.
    let nameTotal = 0;
    let lineageSupport = 0;
    const lineageIds = new Set(lineage.map(([id]) => id));
    lineageIds.add(trackId);
    for (const [id, evidence] of this.evidence) {
      for (const item of evidence.values()) {
        if (normalizeNameForIdentity(this.canonicalCase(item.name)) !== identity) continue;
        nameTotal += item.supportMs;
        if (lineageIds.has(id)) lineageSupport += item.supportMs;
      }
    }
    if (nameTotal <= 0 || lineageSupport / nameTotal < this.minOwnerShare) return false;

    const predecessor = lineage.sort((a, b) =>
      (this.trackLastEnded.get(b[0]) ?? -Infinity) - (this.trackLastEnded.get(a[0]) ?? -Infinity))[0][0];
    this.bind(trackId, lead.name, 'evidence', share, ranked, atMs,
      `successor of track ${predecessor}; ${Math.round(lead.supportMs)}ms over ${lead.episodes} sustained episode(s), share ${share.toFixed(2)}`,
      predecessor);
    return true;
  }

  /** Accept a name for a track, once. The single place a name is ever attached. */
  private bind(
    trackId: string, name: string, source: 'evidence' | 'elimination',
    confidence: number, evidence: TrackEvidence[], atMs: number, why: string,
    successorOf?: string,
  ): void {
    const display = this.canonicalCase(name);
    this.named.set(trackId, { name: display, confidence, source, evidence, atMs, ...(successorOf ? { successorOf } : {}) });
    // Only the EVIDENCE path can be premature this way. Elimination already refuses to fire against
    // an unsettled roster, and a successor is only ever bound at finalization.
    if (source === 'evidence' && !successorOf && !this.rosterSettled(atMs)) this.provisional.add(trackId);
    else this.provisional.delete(trackId);
    if (!successorOf) {
      this.owner.set(name, trackId);
      if (display !== name) this.owner.set(display, trackId);
    }
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
    if (!this.rosterCoverage
      || this.rosterCoverage.participants <= 0
      || this.rosterCoverage.named !== this.rosterCoverage.participants
      || this.rosterCoverage.named !== this.roster.size) return;
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
    // …and NOT PROVABLY BEHIND. Quiet is not the same as complete: on meeting 26424 the roster sat
    // still on one name for 35 s while the transport was already carrying two humans, and coverage
    // called itself 1-of-1 the whole time. `rosterSettled` adds that one contradiction to the quiet
    // test; everything else about this rule is unchanged.
    if (!this.rosterSettled(atMs)) return;
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
