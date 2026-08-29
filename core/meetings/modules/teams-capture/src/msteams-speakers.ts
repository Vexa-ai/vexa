/**
 * MS Teams speaker attribution ("blue squares") — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright, no cross-file imports — the bot
 * bundles this file standalone). Consumed by BOTH:
 *  - the bot: bundled into browser-utils.global.js; msteams/recording.ts's
 *    page.evaluate instantiates it instead of inlining the detector.
 *  - the extension: imported by inpage.ts on Teams hosts; hints label the
 *    mixed tabCapture track.
 *
 * Signal: `[data-tid="voice-level-stream-outline"]` presence (the tile emits a
 * voice-level signal) is REQUIRED — a tile without it never produces a hint.
 * What counts as "speaking" on that outline is an ORDERED list of candidate
 * indicators (vdi-occlusion · inline-style-motion · aria-state ·
 * class-token-delta); the first to fire wins and the module reports WHICH one,
 * because the previous single hardcoded `vdi-frame-occlusion` test produced
 * zero transitions across a live 13-minute meeting. NO caption dependency —
 * captions may be disabled. Debounced speaking start/stop events per
 * participant feed the ChunkedTranscriber's name binder as 'dom-outline' hints.
 *
 * The module also fails LOUD rather than silent: tiles found without the outline
 * emit `signal-absent`, an observable-but-never-speaking window emits
 * `indicator-silent`, and `health()` exposes found/observable/named/
 * nameUnresolved/transitions. Those are diagnostics — a consumer must never turn
 * one into a speaker name. Unknown stays unknown.
 *
 * This module OWNS the Teams speaker-detection selectors (single source —
 * platforms/msteams/selectors.ts re-exports from here).
 */

export const teamsParticipantSelectors: string[] = [
  // Start from the exact speaking atom. Its enclosing stream wrapper joins the
  // active signal to the display name without depending on a gallery layout.
  '[data-tid="voice-level-stream-outline"]',
  // The stream wrapper is the canonical participant surface: Teams carries the
  // display name in data-tid and nests the voice-level outline below it.  Broad
  // tile/roster selectors remain fallbacks for layouts without this wrapper.
  '[data-stream-type][data-tid]',
  '[data-tid*="participant"]',
  '[aria-label*="participant"]',
  '[data-tid*="roster"]',
  '[data-tid*="roster-item"]',
  '[data-tid*="video-tile"]',
  '[data-tid*="videoTile"]',
  '[data-tid*="participant-tile"]',
  '[data-tid*="participantTile"]',
  '[role="listitem"]',
  '.participant-tile',
  '.video-tile',
  '.roster-item',
];

/**
 * The ROSTER PANEL — a DOM surface separate from the video tiles.
 *
 * Every name path in this file rode the participant TILES, and on meeting 37 that turned out to be
 * a single point of failure: the page degraded to one tile with no voice outline, the tile-based
 * roster walk resolved nothing, and a meeting with a named participant sitting in the roster
 * published as "Speaker A". The panel is a different subtree with a different lifecycle — it
 * survives gallery layouts that drop tiles entirely.
 *
 * NOTE ON WHAT THIS DELIBERATELY DOES NOT DO: it never OPENS the panel. Clicking the roster button
 * changes what the humans in the meeting see, and the same ruling that is retiring the captions
 * lane applies here — the bot reads the page, it does not operate it. A closed panel yields nothing
 * and says so, which is the honest outcome and is what `name-sources-absent` exists to report.
 */
export const teamsRosterPanelSelectors: string[] = [
  '[data-tid="roster"]',
  '[data-tid="people-pane"]',
  '[data-tid="roster-section"]',
  '[data-tid*="participant-list"]',
  '[aria-label*="Participants"]',
  '[aria-label*="People"]',
  '[role="tree"][aria-label*="articipant"]',
  '#roster-container',
  '.ts-calling-roster',
];

/** One entry inside that panel. Kept separate from the tile selectors: a roster row has no video,
 *  no voice outline and a different shape, and conflating the two is what made the tile surface
 *  look like the only surface. */
export const teamsRosterEntrySelectors: string[] = [
  '[data-tid="roster-participant"]',
  '[data-tid*="roster-item"]',
  '[data-tid*="participantRosterListItem"]',
  '[role="treeitem"]',
  '[role="listitem"]',
  '.roster-list-item',
  '.ts-calling-roster-item',
];

export const teamsNameSelectors: string[] = [
  // Look for the actual name div structure
  'div[class*="___2u340f0"]', // The actual name div class pattern
  '[data-tid*="display-name"]',
  '[data-tid*="participant-name"]',
  '[data-tid*="user-name"]',
  '[aria-label*="name"]',
  '.participant-name',
  '.display-name',
  '.user-name',
  '.roster-item-name',
  '.video-tile-name',
  'span[title]',
];

export const teamsParticipantIdSelectors: string[] = [
  '[data-tid]',
  '[data-participant-id]',
  '[data-user-id]',
];

export const teamsMeetingContainerSelectors: string[] = [
  '[role="main"]',
  'body',
];

const VOICE_LEVEL_SELECTOR = '[data-tid="voice-level-stream-outline"]';
const STREAM_WRAPPER_SELECTOR = '[data-stream-type][data-tid]';
const STABLE_PARTICIPANT_ROOT_SELECTOR = [
  '[data-participant-id]',
  '[data-user-id]',
  '[data-object-id]',
  '[data-acc-element-id]',
  '[data-tid*="participant-tile"]',
  '[data-tid*="participantTile"]',
  '[data-tid*="video-tile"]',
  '[data-tid*="videoTile"]',
].join(', ');
function matchesSelector(element: HTMLElement, selector: string): boolean {
  try { return (element as any).matches?.(selector) === true; } catch { return false; }
}
const TEAMS_CONTROL_LABELS = new Set([
  'more_vert', 'mic_off', 'mic', 'videocam', 'videocam_off',
  'present_to_all', 'devices', 'speaker', 'speakers', 'microphone',
  'camera', 'camera_off', 'share', 'chat', 'participant', 'user',
  'mute', 'unmute',
].map(value => value.replace(/[_\s-]+/g, ' ')));
const TEAMS_TIMER_LABEL = /^(?:\d{1,2}:)?\d{1,2}:\d{2}$/;
// A clock reading that carries a SUFFIX — "10:42 AM", "05:14 elapsed" — is not matched by
// the anchored timer label above, so a roster tile's timestamp leaf could still become a
// confident wrong name. No human display name begins with h:mm.
// Adopted from Daniel Dormann's guard in Vexa-ai/vexa#1121, which is stricter than ours was.
const TEAMS_CLOCK_PREFIX = /^\d{1,2}:\d{2}/;
// Machine identifiers Teams also carries in `data-tid` / label attributes: hyphen- or
// underscore-joined token chains (`video-stream-2`, `voice-level-stream-outline`), camelCase
// single tokens (`videoTile`), and pure digit runs. A human display name is none of these.
// The attribute being STABLE is not evidence that its value is a person's name — accepting
// one would be a confident wrong answer, which is strictly worse than unknown.
// `Anne-Marie` and `Jean-Luc Picard` survive (capitalized / spaced); an all-lowercase
// hyphenated string is refused, which fails toward unknown rather than toward invention.
const TEAMS_MACHINE_TOKEN =
  /^(?:[a-z0-9]+(?:[-_][a-z0-9]+)+|[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+|\d+)$/;
// A bare all-lowercase token on a TILE is a handle or a role/topic label, not a canonical human
// identity: tiles carry whatever string Teams decided to render in the name slot, so accepting one
// can publish a label as though it were the person's name. Fail toward Speaker A/B instead.
// Qualified names such as `leo (Unverified)` pass because Teams supplied more than a bare handle.
//
// The refusal is lifted by CORROBORATION, never by shape: the roster panel is the name-authoritative
// surface — it lists participants, not media — so a bare token the panel also shows as a row IS that
// participant's chosen display name and passes. A bare token seen only on a tile stays refused.
const TEAMS_UNCANONICAL_BARE_HANDLE = /^\p{Ll}[\p{Ll}\p{M}\p{Nd}]*$/u;

/**
 * Teams' own PLACEHOLDERS for a participant it cannot identify. These are not names — they are the
 * platform saying it does not know — and they arrive shaped exactly like names: capitalised, spaced,
 * two words, through the same attributes a real name uses.
 *
 * On the m34 meeting Teams' captions attributed 50 entries to "Unknown user", and that string
 * became a speaker label on the founder's transcript. A placeholder that becomes a name is worse
 * than an unnamed row: the row LOOKS attributed, so nothing downstream ever asks again.
 *
 * Locale variants are listed because the bot joins meetings in the participant's language, not
 * ours; a German tenant's "Unbekannter Benutzer" is the same failure wearing different letters.
 */
const TEAMS_PLACEHOLDER_NAMES = new Set([
  'unknown user', 'unknown', 'unknown participant', 'guest', 'guest user',
  'anonymous', 'anonymous user', 'participant', 'unidentified',
  'unbekannter benutzer', 'unbekannter teilnehmer', 'gast',
  'utilisateur inconnu', 'invite', 'invité',
  'usuario desconocido', 'invitado',
  'utente sconosciuto', 'ospite',
  'onbekende gebruiker', 'gebruiker',
  'okand anvandare', 'okänd användare',
  'nieznany uzytkownik', 'nieznany użytkownik',
  'неизвестный пользователь', 'гость', 'участник',
  'usuario desconhecido', 'convidado',
  'bilinmeyen kullanıcı', 'misafir',
]);

/**
 * Strip the qualifiers Teams appends to a display name, for IDENTITY comparison only.
 *
 * Teams renders the same person as "Vexa", "Vexa (Unverified)", "Vexa (Guest)" or "Vexa 2"
 * depending on how they joined and whether the name collides. Comparing raw strings therefore let
 * our OWN bot's roster tile past the self-filter on the m34 meeting — and that bot's name was then
 * handed to a HUMAN by the elimination rule, which is the worst outcome this whole lane has.
 *
 * Used ONLY to decide "is this the same identity", never to rewrite what is displayed: the suffix
 * is Teams' statement about the participant and the transcript keeps it verbatim.
 */
export function normalizeDisplayNameForIdentity(value: string): string {
  let normalized = String(value || '').trimEnd();
  // Teams can stack a collision suffix after a qualifier ("Name (Guest) 2"). Peel until stable so
  // the order in which Teams appended the decorations cannot change identity equality.
  for (;;) {
    const previous = normalized;
    normalized = normalized
      .replace(/\s*\((?:unverified|guest|bot|external|extern|invit[ée]|gast|gość|гость|外部)\)\s*$/giu, '')
      .replace(/\s+\(\d+\)\s*$/, '')
      .replace(/\s+\d+$/, '')
      .trimEnd();
    if (normalized === previous) break;
  }
  return normalized.trim().toLowerCase();
}

/** The exact machine namespace meeting-api owns for an omitted bot name.
 *
 * `VexaBot-${uuid.hex[:6]}` is generated in meeting-api. Teams may append an identity qualifier
 * or collision suffix, so compare only after the same identity normalization used everywhere
 * else. This is deliberately exact: words such as "bot", "assistant" and "notetaker" also occur
 * in legitimate human display names and are not identity evidence.
 */
export function isGeneratedDefaultBotDisplayName(value: string): boolean {
  return /^vexabot-[0-9a-f]{6}$/iu.test(normalizeDisplayNameForIdentity(value));
}

/** Is `name` the local participant (our bot), whatever qualifier Teams hung off it? */
export function isSelfDisplayName(name: string, selfName: string | undefined): boolean {
  const self = normalizeDisplayNameForIdentity(selfName || '');
  if (!self) return false;
  return normalizeDisplayNameForIdentity(name) === self;
}

/** What the surrounding scan knows about the surface a candidate string came from.
 *
 * Only the bare-lowercase-handle rule consults this: every other refusal above is a property of the
 * string itself and no surface can vouch for it. A control label is a control label on the roster
 * too.
 */
export interface TeamsNameCandidateContext {
  /** The string is being read FROM the roster panel, which lists participants and nothing else. */
  rosterAuthoritative?: boolean;
  /** Display names the roster panel showed this scan; they corroborate the same string on a tile. */
  rosterNames?: Iterable<string>;
}

/** Is `value` the same identity as one of the roster panel's participant rows? */
function isRosterCorroborated(value: string, ctx?: TeamsNameCandidateContext): boolean {
  if (!ctx) return false;
  if (ctx.rosterAuthoritative) return true;
  if (!ctx.rosterNames) return false;
  const wanted = normalizeDisplayNameForIdentity(value);
  if (!wanted) return false;
  for (const name of ctx.rosterNames) {
    if (normalizeDisplayNameForIdentity(name) === wanted) return true;
  }
  return false;
}

/** Is this string plausibly a HUMAN display name (as opposed to a Teams control
 * label, a timer, a machine token, or Teams' own placeholder for someone it cannot
 * identify)? Exported so every name path in this package — tile resolution, the
 * roster walk AND the caption reader — applies the SAME guard: one place decides
 * what may become a name, so a rejection here can never be re-litigated by a
 * second implementation next door.
 *
 * `ctx` carries the one thing the string cannot carry about itself: which surface showed it. Callers
 * that have read the roster pass it so a genuinely bare-lowercase display name — `leo`, `марина` —
 * is admitted on the evidence of the participant list rather than refused for its shape. */
export function isTeamsDisplayNameCandidate(value: string, ctx?: TeamsNameCandidateContext): boolean {
  const candidate = value.trim();
  if (candidate.length <= 1 || candidate.length >= 50) return false;
  const normalized = candidate.toLowerCase().replace(/[_\s-]+/g, ' ');
  // A placeholder is rejected with its qualifier stripped too, so "Unknown user (Guest)" cannot
  // walk through the door its bare form is refused at.
  if (TEAMS_PLACEHOLDER_NAMES.has(normalized)) return false;
  if (TEAMS_PLACEHOLDER_NAMES.has(normalizeDisplayNameForIdentity(candidate))) return false;
  if (isGeneratedDefaultBotDisplayName(candidate)) return false;
  if (TEAMS_UNCANONICAL_BARE_HANDLE.test(candidate) && !isRosterCorroborated(candidate, ctx)) return false;
  return !TEAMS_CONTROL_LABELS.has(normalized)
    && !TEAMS_TIMER_LABEL.test(normalized)
    && !TEAMS_CLOCK_PREFIX.test(normalized)
    && !TEAMS_MACHINE_TOKEN.test(candidate);
}

/**
 * Resolve a participant's display name from Teams' STABLE signal: the `data-tid` on the
 * `[data-stream-type]` stream wrapper, e.g. `<div data-tid="Jane Doe" data-stream-type="Video">`.
 * Teams' Fluent-UI class hashes churn every release (so class-based name selectors rot), but this
 * attribute pair is durable. Anchor on the voice-level outline so we pick the stream nearest the
 * actual speaking signal; otherwise search up (`closest`) then down (`querySelector`) from the tile.
 * Returns `''` when no stream wrapper is found (caller then falls back to the legacy selectors).
 *
 * The value is passed through the SAME control-label/timer guard as every other name path: a
 * `data-tid="video-stream-2"`-shaped attribute must never become a confident wrong name. Stability
 * of the attribute is not evidence that its value is a human's name.
 *
 * Origin: Jacob Schooley, Vexa-ai/vexa#1024 (commit 6fab915e); the guard is added here.
 */
export function teamsNameFromStream(element: HTMLElement, ctx?: TeamsNameCandidateContext): string {
  const name = rawTeamsNameFromStream(element);
  return name && isTeamsDisplayNameCandidate(name, ctx) ? name : '';
}

/** The stream wrapper's `data-tid` exactly as Teams wrote it, before any guard. */
function rawTeamsNameFromStream(element: HTMLElement): string {
  const voiceOutline = matchesSelector(element, VOICE_LEVEL_SELECTOR)
    ? element
    : element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
  const streamEl = (voiceOutline && (voiceOutline as any).closest?.('[data-stream-type][data-tid]'))
    || (element as any).closest?.('[data-stream-type][data-tid]')
    || element.querySelector('[data-stream-type][data-tid]');
  if (!streamEl) return '';
  return ((streamEl as HTMLElement).getAttribute('data-tid') || '').trim();
}

/**
 * Read the participant names the ROSTER PANEL is showing, if it is open.
 *
 * Returns [] when the panel is absent — which is a fact worth having, not a failure to paper over:
 * it is precisely the m37 state, and the caller reports it rather than silently naming nobody.
 * Names go through `isTeamsDisplayNameCandidate` like every other path in this file, so the panel
 * cannot introduce a control label, a timer or a placeholder the tiles would have been refused. The
 * one rule read differently here is the bare-lowercase handle: this surface lists PARTICIPANTS, so a
 * row reading `leo` is a person named leo, and the resulting names then corroborate the same string
 * on a tile.
 */
export interface TeamsRosterPanelState {
  /** Distinct display names resolved from panel rows. */
  names: string[];
  /** One entry per distinct panel row; null means the row existed but no safe identity resolved. */
  entries: Array<string | null>;
}

export function readTeamsRosterPanelState(
  root: ParentNode = document,
  opts?: { selfName?: string },
): TeamsRosterPanelState {
  const names: string[] = [];
  const entries: Array<string | null> = [];
  const seenNames = new Set<string>();
  const seenEntries = new Set<Element>();
  for (const panelSel of teamsRosterPanelSelectors) {
    let panels: Element[] = [];
    try { panels = Array.from(root.querySelectorAll(panelSel)); } catch { continue; }
    for (const panel of panels) {
      for (const entrySel of teamsRosterEntrySelectors) {
        let matchedEntries: Element[] = [];
        try { matchedEntries = Array.from(panel.querySelectorAll(entrySel)); } catch { continue; }
        for (const entry of matchedEntries) {
          if (!(entry instanceof HTMLElement)) continue;
          if (seenEntries.has(entry)) continue;
          seenEntries.add(entry);
          // OUR OWN ROW IS NOT A PARTICIPANT — dropped before it can be named OR counted, and by
          // the raw display string rather than the resolved one, so a bot name the candidate guard
          // refuses (the generated `VexaBot-<hex>`) still leaves as self instead of as unresolved.
          if (isSelfParticipantSurface(entry, opts?.selfName)) continue;
          // Same resolution order as a tile, minus the stream wrapper a roster row does not have:
          // stable attributes first, then the structural leaf scan (#1121) as the rot-proof floor.
          // The panel is name-authoritative, so a bare-lowercase row is a person, not a label.
          const name = extractTeamsSpeakerName(entry, { nameContext: { rosterAuthoritative: true } });
          entries.push(name || null);
          if (!name || seenNames.has(name)) continue;
          seenNames.add(name);
          names.push(name);
        }
      }
    }
  }
  return { names, entries };
}

export function readTeamsRosterPanel(root: ParentNode = document, opts?: { selfName?: string }): string[] {
  return readTeamsRosterPanelState(root, opts).names;
}

/** Resolve the display name carried by one participant tile.
 *
 * Stable attributes are the preferred path. Teams may also render the visible
 * label as a text-only leaf whose classes are opaque atomic hashes, so the
 * fallback scans leaves only and applies the same exact-token/timer guard.
 */
export function extractTeamsSpeakerName(
  element: HTMLElement,
  opts?: { structuralFallback?: boolean; nameContext?: TeamsNameCandidateContext },
): string {
  for (const raw of teamsRawDisplayStrings(element, opts)) {
    // Control labels are rejected as whole normalized tokens. Substring
    // matching would erase real names such as Michael and Micah.
    if (isTeamsDisplayNameCandidate(raw, opts?.nameContext)) return raw;
  }
  return '';
}

/** Every string this surface offers as its display name, in resolution order, BEFORE any guard.
 *
 * Two questions read the same surfaces and must not disagree about what it says: "what name may this
 * become" (the guard picks the first string it accepts) and "is this our own bot" (identity, which
 * the guard has no say in). Sharing one collector is what keeps them in step — a bot name the guard
 * refuses is still the bot's name, and a surface whose only string is that name must classify as
 * self, not as an unnamed participant.
 */
function teamsRawDisplayStrings(
  element: HTMLElement,
  opts?: { structuralFallback?: boolean },
): string[] {
  const raw: string[] = [];
  const push = (value: string | null | undefined): void => {
    const text = (value || '').trim();
    if (text) raw.push(text);
  };
  // Primary: Teams' stable `data-tid`-on-`[data-stream-type]` name (survives Fluent
  // class-hash churn). The hashed-class selectors below are legacy fallbacks that rot.
  push(rawTeamsNameFromStream(element));
  for (const selector of teamsNameSelectors) {
    const nameElement = element.querySelector(selector) as HTMLElement | null;
    if (!nameElement) continue;
    push(nameElement.textContent ||
      (nameElement as any).innerText ||
      nameElement.getAttribute('title') ||
      nameElement.getAttribute('aria-label'));
  }
  const ariaLabel = element.getAttribute('aria-label');
  if (ariaLabel && ariaLabel.includes('name')) {
    const m = ariaLabel.match(/name[:\s]+([^,]+)/i);
    if (m && m[1]) push(m[1]);
  }
  if (opts?.structuralFallback === false) return raw;
  for (const text of teamsLeafTexts(element)) push(text);
  return raw;
}

/** Does this participant surface belong to the LOCAL bot, whatever Teams renders for it?
 *
 * Asked of the raw display strings rather than the resolved name, because the resolved name is
 * gated on the candidate guard and the bot's own generated `VexaBot-<hex>` identity is precisely
 * what that guard refuses. Resolving first would classify our own tile as an unnamed participant,
 * hold the roster permanently one short of complete, and so disable elimination for everybody else.
 */
export function isSelfParticipantSurface(
  element: HTMLElement,
  selfName: string | undefined,
  opts?: { structuralFallback?: boolean },
): boolean {
  if (!selfName) return false;
  for (const raw of teamsRawDisplayStrings(element, opts)) {
    if (isSelfDisplayName(raw, selfName)) return true;
  }
  return false;
}

/** First leaf-text fragment in `element` that can plausibly be a display name.
 *
 *  Teams renders the display name in a div whose only distinguishing attribute is a
 *  build-specific obfuscated class like `___2u340f0`, with no data-tid, title or
 *  aria-label to key on — so the durable key is the STRUCTURE (a text-only leaf), never
 *  the class hash. Leaves only, so a wrapper's concatenated text can never win. Returns
 *  `''` when nothing resolves: no hint beats a wrong hint.
 *
 *  Origin: Daniel Dormann, Vexa-ai/vexa#1121 (delivers #1119). This is the same leaf scan
 *  `extractTeamsSpeakerName` already ends in, exported under his name so that his
 *  rot-resistance fixtures (a deliberately invented future class) guard OUR resolver
 *  rather than a second copy of it — two implementations of "what may become a name"
 *  would let a rejection here be re-litigated next door.
 */
export function plausibleNameFromLeaves(element: HTMLElement, ctx?: TeamsNameCandidateContext): string {
  for (const text of teamsLeafTexts(element)) {
    if (isTeamsDisplayNameCandidate(text, ctx)) return text;
  }
  return '';   // name not resolvable yet — emit NO hint rather than a meaningless GUID
}

/** Text-only leaves carrying at least one cased letter, in document order, unguarded. */
function teamsLeafTexts(element: HTMLElement): string[] {
  const out: string[] = [];
  const leaves = element.querySelectorAll('*');
  for (let i = 0; i < leaves.length; i++) {
    const leaf = leaves[i] as HTMLElement;
    // Leaf test tolerates both shapes our fixtures use: the DOM's `childElementCount`
    // and a plain `children` array.
    const childCount = typeof (leaf as any).childElementCount === 'number'
      ? (leaf as any).childElementCount
      : ((leaf as any).children?.length ?? 0);
    if (childCount !== 0) continue;
    const text = (leaf.textContent || '').trim();
    if (text && text.toLocaleLowerCase() !== text.toLocaleUpperCase()) out.push(text);
  }
  return out;
}

export interface TeamsSpeakerIdentity {
  id: string;
  name: string;
}

/** A Teams voice-outline edge that was observed but could not safely become a
 * speaker hint because the tile's display name was unresolved. This is a
 * producer observation, not a hint: consumers must never infer a name from it. */
export interface TeamsNameUnresolvedObservation {
  type: 'name-unresolved';
  platform: 'teams';
  signal: 'dom-outline';
  reason: 'resolver-empty';
  edge: 'start' | 'end';
  tMs: number;
}

/** A participant tile that the selectors FOUND but which carries no voice-level
 * outline, so it can never be observed and can never produce a hint. Emitted so
 * coverage is measurable instead of a silent `return`. Carries no DOM text, no
 * display name and no tile identifier. */
export interface TeamsSignalAbsentObservation {
  type: 'signal-absent';
  platform: 'teams';
  signal: 'dom-outline';
  reason: 'outline-missing';
  tMs: number;
}

/** The ordered candidate speaking-indicators. `vdi-frame-occlusion` is a VDI
 * frame-occlusion marker, not a speech indicator, and a 13-minute live Teams
 * meeting produced ZERO transitions through it — so the detector now names
 * WHICH candidate fired and a live run reports the truth instead of us guessing. */
export type TeamsSpeakingIndicator =
  | 'vdi-occlusion'
  | 'inline-style-motion'
  | 'aria-state'
  | 'class-token-delta';

/** One admitted transition INTO the speaking state, naming the candidate that
 * produced it. Diagnostic only — never a speaker hint. */
export interface TeamsIndicatorFiredObservation {
  type: 'indicator-fired';
  platform: 'teams';
  signal: 'dom-outline';
  indicator: TeamsSpeakingIndicator;
  /** Bounded, sanitized class token — only for `class-token-delta`. */
  detail?: string;
  tMs: number;
}

/** The module reporting its OWN contract violation: tiles are observable and no
 * candidate indicator has fired for a whole window, i.e. speaker attribution is
 * producing nothing. Diagnostic only — never a speaker hint. */
export interface TeamsIndicatorSilentObservation {
  type: 'indicator-silent';
  platform: 'teams';
  signal: 'dom-outline';
  reason: 'no-speaking-transition-in-window';
  found: number;
  observable: number;
  windowMs: number;
  tMs: number;
}

/**
 * A display name the ROSTER shows — a participant who is in this meeting, whether or not they are
 * currently speaking and whether or not their tile carries a voice outline.
 *
 * Every other name in this module answers "who is speaking NOW", which is a question about the
 * voice-level signal and therefore silent about anyone the outline cannot see. The roster answers a
 * different question — "who is in the room" — and that is the one an elimination argument needs: on
 * the m30 fixture the outline named exactly ONE of two participants for the entire meeting, so the
 * other could never be named from speaking evidence no matter how long anyone listened.
 *
 * It is emitted as an OBSERVATION, not a hint. A roster name says nothing about time, so treating
 * one as a speaking hint would attribute a turn to somebody merely for being present — the exact
 * fabrication this package exists to refuse. What may consume it is stated at the consumer: the
 * track namer, and only under a rule that binds a name to a track when no other pairing is possible.
 *
 * The name goes through `isTeamsDisplayNameCandidate` like every other name path, the local
 * participant is excluded, and each name is emitted for the first few scans it appears in rather
 * than on every heartbeat — enough for a consumer to require corroboration, bounded so an hour-long
 * meeting does not write thousands of identical lines into the fixture.
 */
export interface TeamsRosterNameObservation {
  type: 'roster-name';
  platform: 'teams';
  /** The display name exactly as the roster renders it — including any " (Unverified)" suffix.
   *  That suffix is Teams' own statement about the participant, not noise for us to tidy away. */
  name: string;
  /** Which scan produced it (1-based), so a consumer can require N sightings across M rescans. */
  scan: number;
  tMs: number;
}

/**
 * How much of the roster this scan could actually READ: participants seen, and of those, how many
 * resolved a display name.
 *
 * A scan that sees four tiles and names two is not a roster of two people — it is a roster of four
 * with two missing, and nothing in the names themselves says so. Any consumer whose premise is
 * 'these are the people in the meeting' needs this, or that premise is silently false — precisely
 * how the m34 meeting put a bot's name on a human.
 */
export interface TeamsRosterCoverageObservation {
  type: 'roster-coverage';
  platform: 'teams';
  /** Participant-shaped tiles this scan matched, excluding our own. */
  participants: number;
  /** …of which a display name resolved. */
  named: number;
  tMs: number;
}

/**
 * The module saying it has NO WAY to name anybody.
 *
 * Meeting 37 shipped a whole conversation as "Speaker A" and nothing in the run said why. The tiles
 * had degraded to one element with no voice outline, the roster resolved nothing, and the caption
 * lane had failed on the Teams side — three independent name sources all dark at once, and the
 * transcript looked merely anonymous rather than broken. Refusing to guess is correct; refusing to
 * guess SILENTLY is not, because the two are indistinguishable to whoever reads the transcript.
 *
 * Emitted once per window, and only while the meeting is actually producing audio — a silent room
 * with nobody speaking has nothing to name and is not a fault.
 */
export interface TeamsNameSourcesAbsentObservation {
  type: 'name-sources-absent';
  platform: 'teams';
  /** Participant-shaped tiles the scan matched (m37: 1). */
  tiles: number;
  /** …of which carried the voice-level outline, the only ones that can ever hint (m37: 0). */
  observable: number;
  /** Distinct names the ROSTER PANEL yielded (m37: 0). */
  rosterPanelNames: number;
  /** Distinct names any surface has yielded all session (m37: 0). */
  namesKnown: number;
  windowMs: number;
  tMs: number;
}

export type TeamsProducerObservation =
  | TeamsNameUnresolvedObservation
  | TeamsSignalAbsentObservation
  | TeamsIndicatorFiredObservation
  | TeamsIndicatorSilentObservation
  | TeamsRosterNameObservation
  | TeamsRosterCoverageObservation
  | TeamsNameSourcesAbsentObservation;

/** Coverage + liveness of the WHO signal, for a caller that wants to surface it. */
export interface TeamsSpeakerHealth {
  /** Canonical participant surfaces discovered on the last scan. */
  found: number;
  /** …of which carry the voice-level outline (only these can ever be named). */
  observable: number;
  /** Observed tiles whose display name resolved. */
  named: number;
  /** Observed tiles whose display name is still unresolved. */
  nameUnresolved: number;
  /** How many times ANY tile entered the speaking state. The live failure this
   * accounting exists for reported zero here across a whole meeting. */
  transitions: number;
  /** Distinct display names the roster has shown — including participants the voice
   *  outline can never see, who are the whole point of collecting it. */
  roster: number;
}

export interface TeamsSpeakersOptions {
  /** Local participant / bot display name — its tiles are never reported. */
  selfName?: string;
  /** Debounced speaking state change: isEnd=false → started speaking,
   *  isEnd=true → stopped. tMs = wall-clock at emit. */
  onSpeaking: (name: string, id: string, isEnd: boolean, tMs: number) => void;
  /** Fail-loud producer observation emitted before an unresolved-name edge is
   * withheld from the hint stream. It contains no DOM text or display name. */
  onNameUnresolved?: (observation: TeamsNameUnresolvedObservation) => void;
  /** Every typed producer observation, name-unresolved included. These are
   * DIAGNOSTICS: a consumer must never turn one into a speaker hint. */
  onObservation?: (observation: TeamsProducerObservation) => void;
  log?: (msg: string) => void;
  /** Debounce for state-change emission (ms). Default 300 — matches the bot. */
  debounceMs?: number;
  /** Heartbeat interval (ms). Default 2000; exposed so deterministic producer
   * validators can advance this contract without wall-clock sleeps. */
  heartbeatMs?: number;
  /** Window after which observable-but-never-speaking is reported as a contract
   * violation (ms). Default 60000. */
  indicatorSilentMs?: number;
  /** How many scans a roster name is re-emitted for before it goes quiet (default 3). A consumer
   *  requiring N corroborations needs at least N; anything beyond that is repetition in a fixture. */
  rosterEmitScans?: number;
  /** How long an EDGE-shaped indicator (a style or class change) is held as a
   * speaking LEVEL (ms). Default 400 — comfortably longer than the gap between
   * two voice-bar updates, so a 60fps sampler does not read silence between
   * them, and short enough that the closing END lands promptly. */
  indicatorHoldMs?: number;
  /** Clock injection, so silence windows are testable without sleeping. */
  now?: () => number;
}

export interface TeamsSpeakers {
  /** Names currently in 'speaking' state. */
  getSpeaking(): string[];
  /** Coverage + liveness snapshot; callers surface it, never act on it. */
  health(): TeamsSpeakerHealth;
  destroy(): void;
}

type SpeakingState = 'speaking' | 'silent' | 'unknown';

export function createTeamsSpeakers(opts: TeamsSpeakersOptions): TeamsSpeakers {
  const log = opts.log || (() => { /* silent */ });
  const debounceMs = opts.debounceMs ?? 300;
  const heartbeatMs = opts.heartbeatMs ?? 2000;
  const indicatorSilentMs = opts.indicatorSilentMs ?? 60_000;
  const indicatorHoldMs = opts.indicatorHoldMs ?? 400;
  const rosterEmitScans = opts.rosterEmitScans ?? 3;
  const now = opts.now ?? (() => Date.now());

  // ── Coverage accounting (Gate A) ──
  // Every canonical participant surface is COUNTED, whether or not it is observable.
  // The old code returned silently on a missing outline, so 3 of 4 participants
  // were invisible: not observed, not named, and not reported as missing.
  const coverage = { found: 0, observable: 0, roster: 0 };
  let transitions = 0;          // entries into the speaking state, cumulative
  let lastSpeakingAt = now();   // anchor for the indicator-silent window
  let coverageWarned = false;

  function deliver(observation: TeamsProducerObservation): void {
    try {
      opts.onObservation?.(observation);
    } catch {
      log(`[TeamsSpeakers] observation-delivery-failed type=${observation.type}`);
    }
  }

  interface Identity { id: string; name: string; element: HTMLElement }

  // ── Participant identity cache ──
  const cache = new Map<HTMLElement, Identity>();

  function extractId(element: HTMLElement): string {
    const isStreamWrapper = matchesSelector(element, STREAM_WRAPPER_SELECTOR);
    let id = element.getAttribute('data-acc-element-id') ||
      element.getAttribute('data-participant-id') ||
      element.getAttribute('data-user-id') ||
      element.getAttribute('data-object-id') ||
      // On a stream wrapper data-tid is the display NAME, not an identity key.
      // Two people can share a display name, so keep them element-distinct.
      (!isStreamWrapper ? element.getAttribute('data-tid') : null) ||
      element.getAttribute('id');
    if (!id) {
      const stableChild = element.querySelector(
        '[data-participant-id], [data-user-id], [data-object-id], [data-acc-element-id]');
      if (stableChild) {
        id = stableChild.getAttribute('data-participant-id') ||
          stableChild.getAttribute('data-user-id') ||
          stableChild.getAttribute('data-object-id') ||
          stableChild.getAttribute('data-acc-element-id');
      }
    }
    if (!id) {
      if (!(element as any).dataset.vexaGeneratedId) {
        (element as any).dataset.vexaGeneratedId = 'teams-id-' + Math.random().toString(36).substr(2, 9);
      }
      id = (element as any).dataset.vexaGeneratedId as string;
    }
    return id!;
  }

  // Resolution chain lives in extractTeamsSpeakerName (stream data-tid → name
  // selectors → aria → structural leaf fallback), which already ends in the
  // structural leaf path this commit is about.
  const extractName = (element: HTMLElement): string => extractTeamsSpeakerName(element);

  function getIdentity(element: HTMLElement): Identity {
    let identity = cache.get(element);
    if (!identity) {
      identity = { id: extractId(element), name: extractName(element), element };
      cache.set(element, identity);
    } else if (!identity.name) {
      identity.name = extractName(element);   // the name div often renders after the tile
    }
    return identity;
  }

  /**
   * Collapse nested selector surfaces onto the element that actually joins
   * identity to the speaking signal.
   *
   * Teams may expose an outer participant tile, a list item and the nested
   * stream wrapper at the same time. Observing/counting each match independently
   * creates duplicate participants and can strand the name on one subtree while
   * the voice outline lives on another. The stream wrapper is therefore the
   * canonical floor. When it sits inside a root carrying a stable participant
   * identifier, retain that root so state survives name/layout changes.
   */
  function canonicalParticipantSurface(element: HTMLElement): HTMLElement {
    const outline = matchesSelector(element, VOICE_LEVEL_SELECTOR)
      ? element
      : element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
    const stream = (matchesSelector(element, STREAM_WRAPPER_SELECTOR) ? element : null)
      || (outline && (outline as any).closest?.(STREAM_WRAPPER_SELECTOR))
      || element.querySelector(STREAM_WRAPPER_SELECTOR);
    if (!(stream instanceof HTMLElement)) {
      const signalRoot = outline && (outline as any).closest?.(STABLE_PARTICIPANT_ROOT_SELECTOR);
      return signalRoot instanceof HTMLElement ? signalRoot : element;
    }
    const stableRoot = (stream as any).closest?.(STABLE_PARTICIPANT_ROOT_SELECTOR);
    return stableRoot instanceof HTMLElement ? stableRoot : stream;
  }

  function participantSurfaceShape(element: HTMLElement): string {
    const stream = matchesSelector(element, STREAM_WRAPPER_SELECTOR)
      ? 'self'
      : element.querySelector(STREAM_WRAPPER_SELECTOR) ? 'descendant' : 'absent';
    const stableRoot = matchesSelector(element, STABLE_PARTICIPANT_ROOT_SELECTOR) ? 'yes' : 'no';
    return `stream=${stream} stable-root=${stableRoot}`;
  }

  // ── State machine (200ms hysteresis, signal-required) ──
  const states = new Map<string, { state: SpeakingState; hasSignal: boolean; lastChangeTime: number }>();
  const MIN_STATE_CHANGE_MS = 200;

  function updateState(id: string, r: { isSpeaking: boolean; hasSignal: boolean }): boolean {
    const current = states.get(id);
    const at = now();
    if (!r.hasSignal) {
      if (current?.hasSignal) states.set(id, { state: 'unknown', hasSignal: false, lastChangeTime: at });
      return false;
    }
    const newState: SpeakingState = r.isSpeaking ? 'speaking' : 'silent';
    if (current?.state === newState && current?.hasSignal) return false;
    if (current && (at - current.lastChangeTime) < MIN_STATE_CHANGE_MS) return false;
    states.set(id, { state: newState, hasSignal: true, lastChangeTime: at });
    return true;
  }

  // ── Detection (Gate B): the voice-level outline is still REQUIRED, but what
  //    counts as "speaking" on it is now an ordered list of named candidates.
  //
  //    Measured on v0.12.18 in a live 13-minute Teams meeting (4-6 participants,
  //    two people conversing): SPEAKER_START 0, SPEAKER_END 2 (the bootstrap
  //    silent assertions only). The single hardcoded `vdi-frame-occlusion` test
  //    never fired once — it is a VDI frame-occlusion marker, not a speech
  //    indicator. Guessing its replacement is how we got here, so instead every
  //    candidate below is evaluated, the FIRST that fires wins, and the module
  //    reports WHICH one fired. A live run then tells us the truth.
  //
  //    Candidates come in two shapes and the difference is LOAD-BEARING.
  //    `vdi-occlusion` and `aria-state` are LEVELS — they answer "is this tile
  //    speaking right now" on any sample. `inline-style-motion` and
  //    `class-token-delta` are EDGES — they answer "did something just change",
  //    which is true on exactly the one sample that observes the change and
  //    false on every sample in between. Speaking is a level, so an edge used
  //    raw makes the state machine oscillate at the sampling rate; with rAF at
  //    ~60fps and a voice bar updating every ~150ms only 1 sample in 9 reads
  //    speaking. The 200ms hysteresis then admits an alternating transition
  //    roughly every 200ms, and because the 300ms debounce is LONGER than that,
  //    every pending emit is cancelled by the opposite transition before it can
  //    fire. Measured, twice — a real headless-Chromium run and a shim at a
  //    realistic frame cadence: transitions=3, indicator-fired x3, onSpeaking
  //    NEVER called, no SPEAKER_START line at all. Diagnostics alive, hint path
  //    dead. So an edge is HELD for `indicatorHoldMs` and read as a level.
  interface TileSignalMemory {
    primed: boolean;               // first sample only records; it never fires
    style: string;                 // last inline-style signature of the outline
    styleSignatures: number;       // distinct signatures this tile has ever shown
    classTokens: Set<string>;      // last class tokens on outline + ancestors
    edgeFiredAt: Map<TeamsSpeakingIndicator, number>;   // edge → level hold
    edgeDetail: Map<TeamsSpeakingIndicator, string>;
  }
  const signalMemory = new Map<HTMLElement, TileSignalMemory>();
  const CLASS_DELTA_ANCESTOR_LIMIT = 8;   // vdi-occlusion still walks to the root
  const CLASS_TOKEN_MAX_CHARS = 40;

  function boundedChain(outline: HTMLElement): HTMLElement[] {
    const chain: HTMLElement[] = [outline];
    let current: HTMLElement | null = outline.parentElement ?? null;
    for (let i = 0; current && i < CLASS_DELTA_ANCESTOR_LIMIT; i++) {
      chain.push(current);
      current = current.parentElement ?? null;
    }
    return chain;
  }

  function classTokensOf(chain: HTMLElement[]): Set<string> {
    const tokens = new Set<string>();
    for (const element of chain) {
      for (const token of (element.getAttribute('class') || '').split(/\s+/)) {
        if (token) tokens.add(token);
      }
    }
    return tokens;
  }

  /** Class tokens are markup, not user text — but bound and sanitize anyway so a
   *  diagnostic can never become an exfiltration channel. */
  function safeToken(token: string): string {
    return token.replace(/[^A-Za-z0-9_-]/g, '').slice(0, CLASS_TOKEN_MAX_CHARS) || 'unnamed';
  }

  const ARIA_SPEAKING = /\b(?:speaking|talking)\b/i;
  const ARIA_NOT_SPEAKING = /\bnot\s+(?:speaking|talking)\b|\bmuted\b/i;
  function ariaSaysSpeaking(element: HTMLElement | null, allowPressed: boolean): boolean {
    if (!element) return false;
    if (allowPressed && element.getAttribute('aria-pressed') === 'true') return true;
    for (const attr of ['aria-label', 'aria-live', 'aria-description']) {
      const value = element.getAttribute(attr);
      if (!value) continue;
      if (ARIA_SPEAKING.test(value) && !ARIA_NOT_SPEAKING.test(value)) return true;
    }
    return false;
  }

  interface IndicatorContext { tile: HTMLElement; outline: HTMLElement; memory: TileSignalMemory }
  interface IndicatorCandidate {
    name: TeamsSpeakingIndicator;
    /** 'level' answers "speaking now"; 'edge' answers "just changed" and is
     *  held for indicatorHoldMs so it can be read as a level. */
    kind: 'level' | 'edge';
    test: (ctx: IndicatorContext) => { fired: boolean; detail?: string };
  }

  const indicatorCandidates: IndicatorCandidate[] = [
    {
      // Kept verbatim, walk to the root included: it fired at least once
      // historically, and removing it would destroy the only evidence we have.
      name: 'vdi-occlusion',
      kind: 'level',
      test: ({ outline }) => {
        let current: HTMLElement | null = outline;
        while (current) {
          if (current.classList?.contains('vdi-frame-occlusion')) return { fired: true };
          current = current.parentElement ?? null;
        }
        return { fired: false };
      },
    },
    {
      // The observer already watched `style` on this element and the detector
      // only ever tested `class` — this closes that gap. Voice-level animation
      // rewrites the outline's inline style (transform/scale/opacity/height);
      // a CHANGE from the remembered signature is activity, silence is the
      // signature holding still. The 200ms hysteresis below shapes it into one
      // START and one END rather than per-frame chatter.
      //
      // The ≥3-signature floor is the guard against the failure mode that is
      // WORSE than silence: a voice bar rewrites its geometry many times a
      // second, but a layout reflow rewrites it ONCE and then holds. Without the
      // floor a single resize makes every tile read as speaking at once, which
      // attributes speech to the wrong person rather than to nobody. A tile that
      // has genuinely animated clears the floor within two frames.
      name: 'inline-style-motion',
      kind: 'edge',
      test: ({ outline, memory }) => {
        const signature = (outline.getAttribute('style') || '').replace(/\s+/g, ' ').trim();
        const changed = signature !== memory.style;
        memory.style = signature;
        if (!memory.primed) { memory.styleSignatures = 1; return { fired: false }; }
        if (changed) memory.styleSignatures++;
        return { fired: changed && memory.styleSignatures >= 3 };
      },
    },
    {
      name: 'aria-state',
      kind: 'level',
      test: ({ tile, outline }) => ({
        fired: ariaSaysSpeaking(outline, true) || ariaSaysSpeaking(tile, false),
      }),
    },
    {
      // Last, and deliberately broad: ANY class token appearing on the outline or
      // a near ancestor that was not there on the previous sample. It records
      // WHICH token, so if Teams' real indicator is a class we do not know about,
      // one live meeting names it for us.
      name: 'class-token-delta',
      kind: 'edge',
      test: ({ outline, memory }) => {
        const tokens = classTokensOf(boundedChain(outline));
        const previous = memory.classTokens;
        memory.classTokens = tokens;
        if (!memory.primed) return { fired: false };
        for (const token of tokens) {
          if (!previous.has(token)) return { fired: true, detail: safeToken(token) };
        }
        return { fired: false };
      },
    },
  ];

  interface Detection {
    isSpeaking: boolean;
    hasSignal: boolean;
    indicator?: TeamsSpeakingIndicator;
    detail?: string;
  }

  function detectSpeakingState(element: HTMLElement): Detection {
    const voiceOutline = matchesSelector(element, VOICE_LEVEL_SELECTOR)
      ? element
      : element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
    if (!voiceOutline) return { isSpeaking: false, hasSignal: false };
    let memory = signalMemory.get(element);
    if (!memory) {
      memory = {
        primed: false, style: '', styleSignatures: 0, classTokens: new Set<string>(),
        edgeFiredAt: new Map(), edgeDetail: new Map(),
      };
      signalMemory.set(element, memory);
    }
    const at = now();
    let indicator: TeamsSpeakingIndicator | undefined;
    let detail: string | undefined;
    // EVERY candidate runs even after one has fired: the stateful ones must
    // refresh their memory on every sample or they fire spuriously on the next.
    for (const candidate of indicatorCandidates) {
      const result = candidate.test({ tile: element, outline: voiceOutline, memory });
      let active = result.fired;
      let activeDetail = result.detail;
      if (candidate.kind === 'edge') {
        // Hold the edge so it reads as a level between two voice-bar updates.
        // Without this the level is true on 1 sample in 9 and the debounce
        // cancels every pending emit — the dead hint path, measured.
        if (result.fired) {
          memory.edgeFiredAt.set(candidate.name, at);
          if (result.detail) memory.edgeDetail.set(candidate.name, result.detail);
          else memory.edgeDetail.delete(candidate.name);
        }
        const firedAt = memory.edgeFiredAt.get(candidate.name);
        active = firedAt !== undefined && (at - firedAt) <= indicatorHoldMs;
        activeDetail = active ? memory.edgeDetail.get(candidate.name) : undefined;
      }
      if (active && !indicator) { indicator = candidate.name; detail = activeDetail; }
    }
    memory.primed = true;
    return { isSpeaking: Boolean(indicator), hasSignal: true, indicator, detail };
  }

  function hasRequiredSignal(element: HTMLElement): boolean {
    return matchesSelector(element, VOICE_LEVEL_SELECTOR)
      || element.querySelector(VOICE_LEVEL_SELECTOR) !== null;
  }

  // ── Gate A: a tile without the outline is ACCOUNTED FOR, never hinted ──
  const signalAbsentReported = new Set<HTMLElement>();
  function emitSignalAbsent(element: HTMLElement): void {
    if (signalAbsentReported.has(element)) return;   // one per tile, not per rescan
    signalAbsentReported.add(element);
    deliver({
      type: 'signal-absent',
      platform: 'teams',
      signal: 'dom-outline',
      reason: 'outline-missing',
      tMs: now(),
    });
    log('[TeamsSpeakers] signal-absent reason=outline-missing signal=dom-outline '
      + participantSurfaceShape(element));
  }

  /**
   * Report having no way to name anyone. Rate-limited to one per window: the condition persists for
   * as long as the page stays degraded, and a line per scan would bury the fact it is reporting.
   */
  function checkNameSources(): void {
    const at = now();
    if (at - nameSourcesAbsentAt < indicatorSilentMs) return;
    // Something must have been observable to name in the first place: a scan that found no tiles at
    // all is a page that has not rendered yet, not a naming failure.
    if (coverage.found === 0) return;
    if (rosterSeen.size > 0 || transitions > 0) return;   // some surface has produced a name
    nameSourcesAbsentAt = at;
    deliver({
      type: 'name-sources-absent', platform: 'teams',
      tiles: coverage.found, observable: coverage.observable,
      rosterPanelNames: lastPanelCount, namesKnown: rosterSeen.size,
      windowMs: indicatorSilentMs, tMs: at,
    });
    log(`[TeamsSpeakers] NAME-SOURCES-ABSENT tiles=${coverage.found} observable=${coverage.observable} `
      + `roster-panel=${lastPanelCount} — no tile can hint and no surface has yielded a name`);
  }

  function emitIndicatorFired(indicator: TeamsSpeakingIndicator, detail?: string): void {
    const observation: TeamsIndicatorFiredObservation = {
      type: 'indicator-fired',
      platform: 'teams',
      signal: 'dom-outline',
      indicator,
      tMs: now(),
    };
    if (detail) observation.detail = detail;
    deliver(observation);
    log(`[TeamsSpeakers] indicator-fired indicator=${indicator}${detail ? ` token=${detail}` : ''}`);
  }

  function checkIndicatorSilence(): void {
    if (coverage.observable <= 0) return;
    if ((now() - lastSpeakingAt) < indicatorSilentMs) return;
    lastSpeakingAt = now();   // one report per window, not one per heartbeat
    deliver({
      type: 'indicator-silent',
      platform: 'teams',
      signal: 'dom-outline',
      reason: 'no-speaking-transition-in-window',
      found: coverage.found,
      observable: coverage.observable,
      windowMs: indicatorSilentMs,
      tMs: now(),
    });
    log(
      `⚠️ [TeamsSpeakers] WARN indicator-silent observable=${coverage.observable} `
      + `found=${coverage.found} windowMs=${indicatorSilentMs} — no candidate indicator `
      + 'has fired; this producer is emitting no speaker attribution at all',
    );
  }

  // ── Debouncer ──
  const debounceTimers = new Map<string, number>();
  function debounce(key: string, fn: () => void): void {
    const t = debounceTimers.get(key);
    if (t !== undefined) clearTimeout(t);
    debounceTimers.set(key, setTimeout(() => { fn(); debounceTimers.delete(key); }, debounceMs) as unknown as number);
  }

  // ── Observer system ──
  const observers = new Map<HTMLElement, MutationObserver[]>();
  const rafHandles = new Map<string, number>();
  const speakingStates = new Map<string, SpeakingState>();
  // One diagnostic episode per participant: a bootstrap-silent tile is not an
  // identity failure, and a 2s heartbeat is not a fresh edge. A real unresolved
  // start opens the episode; its unresolved end closes it.
  const unresolvedEpisodes = new Set<string>();
  // A named END is admissible only after this producer actually emitted the
  // matching named START (either on the edge or as a speaking heartbeat).
  const namedEpisodes = new Set<string>();
  let destroyed = false;

  function emitUnresolved(edge: 'start' | 'end', identity: Identity): void {
    if (edge === 'start') {
      namedEpisodes.delete(identity.id);
      unresolvedEpisodes.add(identity.id);
    }
    else unresolvedEpisodes.delete(identity.id);
    const observation: TeamsNameUnresolvedObservation = {
      type: 'name-unresolved',
      platform: 'teams',
      signal: 'dom-outline',
      reason: 'resolver-empty',
      edge,
      tMs: now(),
    };
    try {
      opts.onNameUnresolved?.(observation);
    } catch {
      log(`[TeamsSpeakers] name-unresolved-delivery-failed edge=${edge}`);
    }
    deliver(observation);
    log(
      `[TeamsSpeakers] name-unresolved reason=${observation.reason} ` +
      `signal=${observation.signal} edge=${edge}`,
    );
  }

  function emitNamedStart(identity: Identity): void {
    unresolvedEpisodes.delete(identity.id);
    opts.onSpeaking(identity.name, identity.id, false, now());
    namedEpisodes.add(identity.id);
  }

  function emit(state: SpeakingState, identity: Identity): void {
    if (state === 'unknown' || destroyed) return;
    const edge = state === 'speaking' ? 'start' : 'end';
    // Identity painting after an unresolved START is not retrospective named
    // testimony. Unless a named START was emitted, close the typed episode.
    if (edge === 'end' && unresolvedEpisodes.has(identity.id)) {
      emitUnresolved('end', identity);
      return;
    }
    if (!identity.name) identity.name = extractName(identity.element);
    if (!identity.name) {
      if (edge === 'end') return;
      emitUnresolved('start', identity);
      return;   // unresolved name stays unknown; never emit a nameless/GUID hint
    }
    unresolvedEpisodes.delete(identity.id);
    if (isSelfDisplayName(identity.name, opts.selfName)) return;                 // our bot, any qualifier
    if (edge === 'end' && !namedEpisodes.delete(identity.id)) return;
    log(`${state === 'speaking' ? '🎤' : '🔇'} [TeamsSpeakers] ${state === 'speaking' ? 'SPEAKER_START' : 'SPEAKER_END'}: ${identity.name} (${identity.id})`);
    if (edge === 'start') emitNamedStart(identity);
    else opts.onSpeaking(identity.name, identity.id, true, now());
  }

  function checkAndEmit(identity: Identity): void {
    if (destroyed) return;
    if (!identity.element.isConnected) { removeParticipant(identity); return; }
    const r = detectSpeakingState(identity.element);
    if (updateState(identity.id, r) && r.hasSignal) {
      const newState: SpeakingState = r.isSpeaking ? 'speaking' : 'silent';
      speakingStates.set(identity.id, newState);
      if (newState === 'speaking') {
        // The transition is real HERE — before the debounce, the self-name filter
        // and the no-blank-name guard, any of which may legitimately swallow the
        // hint. The observation says the detector fired; the hint stream says it
        // also became a name. Conflating the two is how a dead detector hides.
        transitions++;
        lastSpeakingAt = now();
        if (r.indicator) emitIndicatorFired(r.indicator, r.detail);
      }
      debounce(identity.id, () => emit(newState, identity));
    }
  }

  function scheduleRAFCheck(identity: Identity): void {
    const check = () => {
      if (destroyed) return;
      if (!identity.element.isConnected) { removeParticipant(identity); return; }
      checkAndEmit(identity);
      rafHandles.set(identity.id, requestAnimationFrame(check));
    };
    rafHandles.set(identity.id, requestAnimationFrame(check));
  }

  function removeParticipant(identity: Identity): void {
    const t = debounceTimers.get(identity.id);
    if (t !== undefined) { clearTimeout(t); debounceTimers.delete(identity.id); }
    if (states.get(identity.id)?.state === 'speaking') emit('silent', identity);
    const obs = observers.get(identity.element);
    if (obs) { obs.forEach(o => o.disconnect()); observers.delete(identity.element); }
    const raf = rafHandles.get(identity.id);
    if (raf !== undefined) { cancelAnimationFrame(raf); rafHandles.delete(identity.id); }
    states.delete(identity.id);
    speakingStates.delete(identity.id);
    signalMemory.delete(identity.element);
    signalAbsentReported.delete(identity.element);
    cache.delete(identity.element);
    delete (identity.element as any).dataset.vexaObserverAttached;
    log(`🗑️ [TeamsSpeakers] Removed: ${identity.name} (${identity.id})`);
  }

  function observeParticipant(element: HTMLElement): void {
    if ((element as any).dataset.vexaObserverAttached) return;
    // UNCHANGED RULE: a tile with no voice-level signal is never observed and can
    // never produce a hint. What changed is that it is no longer INVISIBLE — the
    // scan accounts for it and emits a typed `signal-absent` observation.
    if (!hasRequiredSignal(element)) { emitSignalAbsent(element); return; }
    signalAbsentReported.delete(element);   // it grew an outline; re-report if it loses one
    const identity = getIdentity(element);
    (element as any).dataset.vexaObserverAttached = 'true';
    log(`👁️ [TeamsSpeakers] Observing: ${identity.name} (${identity.id})`);
    const voiceOutline = matchesSelector(element, VOICE_LEVEL_SELECTOR)
      ? element
      : element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
    if (!voiceOutline) return;
    const voiceObserver = new MutationObserver(() => checkAndEmit(identity));
    voiceObserver.observe(voiceOutline, { attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] });
    const containerObserver = new MutationObserver(() => {
      if (!hasRequiredSignal(element)) { removeParticipant(identity); return; }
      checkAndEmit(identity);
    });
    containerObserver.observe(element, { childList: true, subtree: true });
    observers.set(element, [voiceObserver, containerObserver]);
    scheduleRAFCheck(identity);
    checkAndEmit(identity);
  }

  // ── The ROSTER: who is in the room, regardless of whether they can be heard ──
  // Counted per name across scans. A tile with no voice outline still carries a display name, and
  // that name is precisely what the speaking path can never report — which is why this walks EVERY
  // matched tile rather than only the observable ones.
  const rosterSeen = new Map<string, number>();
  let scanCounter = 0;
  let lastCoverageKey = '';
  let lastPanelCount = 0;
  let nameSourcesAbsentAt = 0;
  function emitRosterName(name: string, nameContext?: TeamsNameCandidateContext): void {
    const trimmed = (name || '').trim();
    if (!trimmed) return;
    if (!isTeamsDisplayNameCandidate(trimmed, nameContext)) return;  // the one guard every name path uses
    if (isSelfDisplayName(trimmed, opts.selfName)) return;   // never report ourselves, suffix or not
    const seenCount = (rosterSeen.get(trimmed) ?? 0) + 1;
    rosterSeen.set(trimmed, seenCount);
    if (seenCount > rosterEmitScans) return;                    // corroborated enough; stop repeating
    deliver({ type: 'roster-name', platform: 'teams', name: trimmed, scan: seenCount, tMs: now() });
    log(`[TeamsSpeakers] roster-name sighting ${seenCount}/${rosterEmitScans}`);
  }

  function scanAndObserveAll(): void {
    const allSelectors = [...teamsParticipantSelectors, '[role="menuitem"]'];
    const seen = new WeakSet<HTMLElement>();
    let found = 0; let observable = 0;
    scanCounter++;
    const namesThisScan = new Set<string>();
    let unresolvedParticipantSurfaces = 0;
    // SECOND SURFACE: the roster panel, if the meeting has it open. Read FIRST and merged into the
    // same stream — a name is a name whichever surface showed it — but counted separately, because
    // "the tiles are gone and the panel saved us" is exactly the state worth being able to see in a
    // fixture afterwards. It leads because it is the name-authoritative one: its names are what
    // corroborate a bare-lowercase display name on a tile.
    let panelState: TeamsRosterPanelState = { names: [], entries: [] };
    try { panelState = readTeamsRosterPanelState(document, { selfName: opts.selfName }); }
    catch { panelState = { names: [], entries: [] }; }
    const panelNames = panelState.names;
    const nameContext: TeamsNameCandidateContext = { rosterNames: panelNames };
    for (const selector of allSelectors) {
      document.querySelectorAll(selector).forEach(el => {
        if (el instanceof HTMLElement) {
          const surface = canonicalParticipantSurface(el);
          if (seen.has(surface)) return;
          seen.add(surface);
          found++;
          // The roster walk is deliberately BEFORE the signal gate: a participant whose tile has no
          // voice outline is invisible to every other path in this file, and they are exactly the
          // person an elimination argument is for.
          //
          // OUR OWN TILE IS NEITHER NAMED NOR COUNTED, and self is decided on the raw display string
          // rather than the resolved name: the bot's generated `VexaBot-<hex>` identity is refused by
          // the candidate guard, so resolving first would file us as an unnamed participant and hold
          // the roster one short of complete forever — which is exactly the state that disables
          // elimination for everyone else in the room.
          if (isSelfParticipantSurface(surface, opts.selfName)) { /* local bot */ }
          else {
            const rosterName = extractTeamsSpeakerName(surface, { nameContext });
            if (rosterName) namesThisScan.add(rosterName);
            else unresolvedParticipantSurfaces++;
          }
          if (hasRequiredSignal(surface)) { observable++; observeParticipant(surface); }
          else emitSignalAbsent(surface);   // counted and reported, never hinted
        }
      });
    }
    for (const n of panelNames) if (!isSelfDisplayName(n, opts.selfName)) namesThisScan.add(n);
    lastPanelCount = panelNames.length;

    // Emit once per name per scan (a name matched by two selectors is one participant).
    for (const n of namesThisScan) emitRosterName(n, nameContext);
    // …and say how much of the roster this scan could read. Reported on CHANGE rather than every
    // heartbeat: it is a state, and an unchanged state repeated 1800 times is not information.
    const usableNames = new Set([...namesThisScan].filter(
      (n) => isTeamsDisplayNameCandidate(n, nameContext) && !isSelfDisplayName(n, opts.selfName)));
    // COMPLETENESS MUST FAIL CLOSED WITHOUT PRETENDING DOM ELEMENTS ARE PEOPLE. Teams may render the
    // same named participant on several selector surfaces, so resolved display names are deduped.
    // An unresolved surface cannot be deduped safely: each is therefore one additional lower-bound
    // participant. Meeting 26218 had zero usable names and four unresolved surfaces, so it is 0/4;
    // an ordinary two-person room rendered twice per person but named on every surface is 2/2.
    const nonSelfPanelEntries = panelState.entries.filter(
      (name) => !name || !isSelfDisplayName(name, opts.selfName));
    const distinctUsablePanelNames = new Set(nonSelfPanelEntries.filter(
      (name): name is string => !!name && isTeamsDisplayNameCandidate(name, nameContext)));
    const unresolvedPanelParticipants = Math.max(
      0, nonSelfPanelEntries.length - distinctUsablePanelNames.size);
    const participants = usableNames.size + unresolvedParticipantSurfaces + unresolvedPanelParticipants;
    const coverageKey = `${usableNames.size}/${participants}`;
    if (coverageKey !== lastCoverageKey) {
      lastCoverageKey = coverageKey;
      deliver({ type: 'roster-coverage', platform: 'teams', participants, named: usableNames.size, tMs: now() });
      log(`[TeamsSpeakers] roster-coverage named=${usableNames.size} participants=${participants}`);
    }
    coverage.found = found;
    coverage.observable = observable;
    coverage.roster = rosterSeen.size;
    checkNameSources();
    log(`🔍 [TeamsSpeakers] Scanned ${found} participants, observing ${observable} with signal (signal-absent ${found - observable})`);
    if (!coverageWarned && found > 0 && (observable / found) < 0.5) {
      coverageWarned = true;
      log(
        `⚠️ [TeamsSpeakers] WARN coverage-low found=${found} observable=${observable} `
        + `ratio=${(observable / found).toFixed(2)} — most matched tiles carry no `
        + 'voice-level outline, so they can never be observed or named',
      );
    }
  }

  scanAndObserveAll();

  // Monitor for new/removed participants.
  const bodyObserver = new MutationObserver((mutationsList) => {
    if (destroyed) return;
    const allSelectors = [...teamsParticipantSelectors, '[role="menuitem"]'];
    for (const mutation of mutationsList) {
      if (mutation.type !== 'childList') continue;
      mutation.addedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        for (const selector of allSelectors) {
          if (el.matches(selector)) observeParticipant(canonicalParticipantSurface(el));
          el.querySelectorAll(selector).forEach(child => {
            if (child instanceof HTMLElement) observeParticipant(canonicalParticipantSurface(child));
          });
        }
      });
      mutation.removedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        const removed = new Set<HTMLElement>();
        for (const selector of allSelectors) {
          if (el.matches(selector)) removed.add(canonicalParticipantSurface(el));
          el.querySelectorAll(selector).forEach(child => {
            if (child instanceof HTMLElement) removed.add(canonicalParticipantSurface(child));
          });
        }
        for (const surface of removed) {
          const identity = cache.get(surface);
          if (identity) removeParticipant(identity);
        }
      });
    }
  });
  const container = document.querySelector(teamsMeetingContainerSelectors[0]) || document.body;
  bodyObserver.observe(container, { childList: true, subtree: true });

  // Periodic rescan: tiles re-render without childList mutations sometimes.
  const rescanInterval = setInterval(() => { if (!destroyed) scanAndObserveAll(); }, 5000) as unknown as number;

  // Heartbeat: re-assert who is currently speaking every ~2s even without a state
  // change, so a consumer that started mid-turn (a reconnected WS / a restarted
  // pipeline with an empty name-binder) learns the active speaker WITHOUT waiting
  // for the next blue-square transition. Mirrors the Zoom active-speaker heartbeat;
  // repeated same-speaker hints are idempotent at the binder.
  const heartbeat = setInterval(() => {
    if (destroyed) return;
    checkIndicatorSilence();   // fail loud: observable tiles that never speak
    for (const [id, st] of speakingStates) {
      if (st !== 'speaking') continue;
      for (const ident of cache.values()) {
        if (ident.id !== id) continue;
        if (!ident.name) ident.name = extractName(ident.element);   // name may have rendered since
        if (ident.name) {
          emitNamedStart(ident);
        }
        break;
      }
    }
  }, heartbeatMs) as unknown as number;

  return {
    getSpeaking(): string[] {
      const names: string[] = [];
      for (const [id, st] of speakingStates) {
        if (st !== 'speaking') continue;
        for (const ident of cache.values()) if (ident.id === id) { names.push(ident.name); break; }
      }
      return names;
    },
    health(): TeamsSpeakerHealth {
      let named = 0; let nameUnresolved = 0;
      for (const ident of cache.values()) {
        if (ident.name) named++; else nameUnresolved++;
      }
      return {
        found: coverage.found,
        observable: coverage.observable,
        named,
        nameUnresolved,
        transitions,
        roster: coverage.roster,
      };
    },
    destroy(): void {
      destroyed = true;
      clearInterval(rescanInterval);
      clearInterval(heartbeat);
      bodyObserver.disconnect();
      for (const obs of observers.values()) obs.forEach(o => o.disconnect());
      observers.clear();
      for (const raf of rafHandles.values()) cancelAnimationFrame(raf);
      rafHandles.clear();
      for (const t of debounceTimers.values()) clearTimeout(t);
      debounceTimers.clear();
      states.clear();
      speakingStates.clear();
      unresolvedEpisodes.clear();
      namedEpisodes.clear();
      signalMemory.clear();
      signalAbsentReported.clear();
      cache.clear();
    },
  };
}
