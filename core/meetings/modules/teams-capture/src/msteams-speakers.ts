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
const TEAMS_CONTROL_LABELS = new Set([
  'more_vert', 'mic_off', 'mic', 'videocam', 'videocam_off',
  'present_to_all', 'devices', 'speaker', 'speakers', 'microphone',
  'camera', 'camera_off', 'share', 'chat', 'participant', 'user',
  'mute', 'unmute',
].map(value => value.replace(/[_\s-]+/g, ' ')));
const TEAMS_TIMER_LABEL = /^(?:\d{1,2}:)?\d{1,2}:\d{2}$/;
// Machine identifiers Teams also carries in `data-tid` / label attributes: hyphen- or
// underscore-joined token chains (`video-stream-2`, `voice-level-stream-outline`), camelCase
// single tokens (`videoTile`), and pure digit runs. A human display name is none of these.
// The attribute being STABLE is not evidence that its value is a person's name — accepting
// one would be a confident wrong answer, which is strictly worse than unknown.
// `Anne-Marie` and `Jean-Luc Picard` survive (capitalized / spaced); an all-lowercase
// hyphenated string is refused, which fails toward unknown rather than toward invention.
const TEAMS_MACHINE_TOKEN =
  /^(?:[a-z0-9]+(?:[-_][a-z0-9]+)+|[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+|\d+)$/;

function isTeamsDisplayNameCandidate(value: string): boolean {
  const candidate = value.trim();
  if (candidate.length <= 1 || candidate.length >= 50) return false;
  const normalized = candidate.toLowerCase().replace(/[_\s-]+/g, ' ');
  return !TEAMS_CONTROL_LABELS.has(normalized)
    && !TEAMS_TIMER_LABEL.test(normalized)
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
export function teamsNameFromStream(element: HTMLElement): string {
  const voiceOutline = element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
  const streamEl = (voiceOutline && (voiceOutline as any).closest?.('[data-stream-type][data-tid]'))
    || (element as any).closest?.('[data-stream-type][data-tid]')
    || element.querySelector('[data-stream-type][data-tid]');
  if (streamEl) {
    const name = ((streamEl as HTMLElement).getAttribute('data-tid') || '').trim();
    if (isTeamsDisplayNameCandidate(name)) return name;
  }
  return '';
}

/** Resolve the display name carried by one participant tile.
 *
 * Stable attributes are the preferred path. Teams may also render the visible
 * label as a text-only leaf whose classes are opaque atomic hashes, so the
 * fallback scans leaves only and applies the same exact-token/timer guard.
 */
export function extractTeamsSpeakerName(
  element: HTMLElement,
  opts?: { structuralFallback?: boolean },
): string {
  // Primary: Teams' stable `data-tid`-on-`[data-stream-type]` name (survives Fluent
  // class-hash churn). The hashed-class selectors below are legacy fallbacks that rot.
  const streamName = teamsNameFromStream(element);
  if (streamName) return streamName;
  for (const selector of teamsNameSelectors) {
    const nameElement = element.querySelector(selector) as HTMLElement | null;
    if (!nameElement) continue;
    let nameText = nameElement.textContent ||
      (nameElement as any).innerText ||
      nameElement.getAttribute('title') ||
      nameElement.getAttribute('aria-label');
    if (!nameText || !nameText.trim()) continue;
    nameText = nameText.trim();
    // Control labels are rejected as whole normalized tokens. Substring
    // matching would erase real names such as Michael and Micah.
    if (isTeamsDisplayNameCandidate(nameText)) return nameText;
  }
  const ariaLabel = element.getAttribute('aria-label');
  if (ariaLabel && ariaLabel.includes('name')) {
    const m = ariaLabel.match(/name[:\s]+([^,]+)/i);
    if (m && m[1]) {
      const nameText = m[1].trim();
      if (isTeamsDisplayNameCandidate(nameText)) return nameText;
    }
  }
  if (opts?.structuralFallback === false) return '';
  const leaves = element.querySelectorAll('*');
  for (let i = 0; i < leaves.length; i++) {
    const leaf = leaves[i] as HTMLElement;
    if (leaf.children.length !== 0) continue;
    const text = (leaf.textContent || '').trim();
    if (
      text.toLocaleLowerCase() !== text.toLocaleUpperCase()
      && isTeamsDisplayNameCandidate(text)
    ) return text;
  }
  return '';
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

export type TeamsProducerObservation =
  | TeamsNameUnresolvedObservation
  | TeamsSignalAbsentObservation
  | TeamsIndicatorFiredObservation
  | TeamsIndicatorSilentObservation;

/** Coverage + liveness of the WHO signal, for a caller that wants to surface it. */
export interface TeamsSpeakerHealth {
  /** Participant-shaped elements the selectors matched on the last scan. */
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
  const selfLower = (opts.selfName || '').toLowerCase();
  const debounceMs = opts.debounceMs ?? 300;
  const heartbeatMs = opts.heartbeatMs ?? 2000;
  const indicatorSilentMs = opts.indicatorSilentMs ?? 60_000;
  const indicatorHoldMs = opts.indicatorHoldMs ?? 400;
  const now = opts.now ?? (() => Date.now());

  // ── Coverage accounting (Gate A) ──
  // Every tile the selectors match is COUNTED, whether or not it is observable.
  // The old code returned silently on a missing outline, so 3 of 4 participants
  // were invisible: not observed, not named, and not reported as missing.
  const coverage = { found: 0, observable: 0 };
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
    let id = element.getAttribute('data-acc-element-id') ||
      element.getAttribute('data-tid') ||
      element.getAttribute('data-participant-id') ||
      element.getAttribute('data-user-id') ||
      element.getAttribute('data-object-id') ||
      element.getAttribute('id');
    if (!id) {
      const stableChild = element.querySelector(teamsParticipantIdSelectors.join(', '));
      if (stableChild) {
        id = stableChild.getAttribute('data-tid') ||
          stableChild.getAttribute('data-participant-id') ||
          stableChild.getAttribute('data-user-id');
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
    const voiceOutline = element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
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
    return element.querySelector(VOICE_LEVEL_SELECTOR) !== null;
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
    log('[TeamsSpeakers] signal-absent reason=outline-missing signal=dom-outline');
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
    if (selfLower && identity.name.toLowerCase().includes(selfLower)) return;
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
    const voiceOutline = element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
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

  function scanAndObserveAll(): void {
    const allSelectors = [...teamsParticipantSelectors, '[role="menuitem"]'];
    const seen = new WeakSet<HTMLElement>();
    let found = 0; let observable = 0;
    for (const selector of allSelectors) {
      document.querySelectorAll(selector).forEach(el => {
        if (el instanceof HTMLElement && !seen.has(el)) {
          seen.add(el);
          found++;
          if (hasRequiredSignal(el)) { observable++; observeParticipant(el); }
          else emitSignalAbsent(el);   // counted and reported, never hinted
        }
      });
    }
    coverage.found = found;
    coverage.observable = observable;
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
          if (el.matches(selector)) observeParticipant(el);
          el.querySelectorAll(selector).forEach(child => {
            if (child instanceof HTMLElement) observeParticipant(child);
          });
        }
      });
      mutation.removedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        for (const selector of teamsParticipantSelectors) {
          if (!el.matches(selector)) continue;
          const identity = cache.get(el);
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
