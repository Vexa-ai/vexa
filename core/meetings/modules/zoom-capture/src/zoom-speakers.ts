/**
 * Zoom Web speaker attribution — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright). Consumed by BOTH:
 *  - the bot: bundled into browser-utils.global.js; Node's startSpeakerPolling
 *    reads window.__vexaZoomSpeakers.getActiveSpeaker() instead of inlining the
 *    DOM read.
 *  - the extension: imported by inpage.ts; labels the mixed tabCapture track
 *    with the current active speaker.
 *
 * Unlike Google Meet (per-participant <audio> elements → per-track vote/lock),
 * Zoom Web exposes only mixed audio (the bot uses PulseAudio; the extension
 * uses chrome.tabCapture). Attribution is therefore TEMPORAL: read who Zoom is
 * currently rendering as the active speaker from the DOM, and label the mixed
 * audio with that name. Selectors mirror vexa-bot zoom/web/selectors.ts +
 * recording.ts startSpeakerPolling.
 */

export interface ZoomSpeakersOptions {
  /** Local participant display name — never reported as the remote speaker. */
  selfName?: string;
  /** Fired when the active speaker changes (name or null when nobody is active).
   *  Legacy single-track mode (used when remote audio is one mixed track). */
  onSpeakerChange?: (name: string | null) => void;
  /** How often to report that no speaker has EVER been seen (ms, default 30s). A watcher whose
   *  selectors have gone stale is otherwise completely silent — see the report in `tick`. */
  blindReportMs?: number;
  /** Per-track naming (multi-channel mode): fired when a track index is mapped/
   *  locked to a participant name via active-speaker voting. */
  onName?: (index: number, name: string) => void;
  log?: (msg: string) => void;
  /** Poll interval (ms). Default 250 — matches the bot. */
  pollMs?: number;
  /** Votes needed to lock a track→name mapping. Default 2 (matches the bot). */
  lockVotes?: number;
}

export interface ZoomSpeakers {
  /** Current active speaker name, or null. */
  getActiveSpeaker(): string | null;
  /** Multi-channel mode: report that WebRTC track `index` just had audio. The
   *  module correlates this with the active speaker to vote/lock track→name. */
  reportTrackAudio(index: number): void;
  /** Locked name for a track, if any. */
  getTrackName(index: number): string | null;
  /** Live forensics — call window.__vexaZoomSpeakers.getState() on a real call
   *  to confirm the selectors match the current Zoom DOM (or find what does). */
  getState(): ZoomSpeakersState;
  destroy(): void;
}

export interface ZoomSpeakersState {
  active: string | null;
  /** Which known container selector currently matches, if any. */
  matchedSelector: string | null;
  /** Per known selector: present in DOM? name read? — to spot stale selectors. */
  probe: Array<{ selector: string; present: boolean; name: string | null }>;
  /** Every name-bearing participant tile: the name we read, the tile's own
   *  class chain (self + nearest ancestors), and whether any class on the tile
   *  or its descendants hints "speaking/active/talking/audio". This is the raw
   *  material to write a robust speaking-tile selector that works in any view. */
  tiles: Array<{ name: string; tileClasses: string[]; speakingHints: string[] }>;
  /** Footer selector currently used to read names. */
  nameFooterSelector: string;
  /** When tiles is empty (stale selectors), a generic DOM survey: elements that
   *  plausibly carry participant names (short text / aria-label on name-ish,
   *  avatar-ish, or video-adjacent nodes) with their classes — the raw material
   *  to derive the current Zoom version's selectors. */
  survey?: Array<{ cls: string; aria: string; text: string }>;
}

// Active-speaker containers (normal view + screen-share view), and the avatar
// footer that holds the name. Verbatim from vexa-bot zoom/web/selectors.ts.
const ACTIVE_CONTAINER_SELECTORS = [
  '.speaker-active-container__video-frame',
  '.speaker-bar-container__video-frame--active',
];
const NAME_FOOTER_SELECTOR = '.video-avatar__avatar-footer';

export function createZoomSpeakers(opts: ZoomSpeakersOptions = {}): ZoomSpeakers {
  const log = opts.log || (() => { /* silent */ });
  const pollMs = opts.pollMs ?? 250;
  const lockVotes = opts.lockVotes ?? 2;
  let active: string | null = null;
  // Flicker debounce (the obvious attribution fix): Zoom can briefly light another
  // tile for a single ~250ms poll (a cough, a transient sound, a UI repaint).
  // Emitting that as a speaker change fires a wrong hint that can hijack the open
  // turn's name downstream (the binder attributes an unnamed turn to the first hint
  // and sticks). So require a change to HOLD for CONFIRM_POLLS reads before
  // committing it — a single flicker poll is dropped.
  const CONFIRM_POLLS = 2;          // ~2×pollMs ≈ 500ms of stability before a change counts
  let candidate: string | null = null;
  let candidateCount = 0;

  // ── Multi-channel track→name voting (same vote/lock idea as gmeet-speakers
  //    and the bot's speaker-identity.ts) ──────────────────────────────────
  const lastTrackAudio = new Map<number, number>();          // index → ts (ms)
  const votes = new Map<number, Map<string, number>>();      // index → name → count
  const lockedTrack = new Map<number, string>();             // index → locked name
  const nameToTrack = new Map<string, number>();             // name → index (1:1)
  const AUDIO_RECENT_MS = 400;

  function now(): number { return (globalThis.performance?.now?.() ?? 0) || +new Date(); }

  function voteOnce(): void {
    // Vote only when exactly one track has had recent audio AND there is a
    // single active speaker — that's an unambiguous track↔name correlation.
    if (!active) return;
    const t = now();
    const recent: number[] = [];
    for (const [idx, ts] of lastTrackAudio) if (t - ts <= AUDIO_RECENT_MS) recent.push(idx);
    if (recent.length !== 1) return;
    const index = recent[0];
    if (lockedTrack.has(index)) return;
    if (nameToTrack.has(active) && nameToTrack.get(active) !== index) return; // name already owned

    let m = votes.get(index); if (!m) { m = new Map(); votes.set(index, m); }
    const c = (m.get(active) || 0) + 1; m.set(active, c);
    if (c >= lockVotes) {
      lockedTrack.set(index, active);
      nameToTrack.set(active, index);
      log(`LOCK track ${index} → "${active}"`);
      try { opts.onName?.(index, active); } catch { /* consumer error */ }
    }
  }

  function nameFromContainer(container: Element | null): string | null {
    if (!container) return null;
    const footer = container.querySelector(NAME_FOOTER_SELECTOR);
    if (!footer) return null;
    const span = footer.querySelector('span');
    const raw = (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || '';
    // Collapse internal whitespace ("Lord  Mason" → "Lord Mason") — the binder keys
    // by name, so a whitespace variant would read as a different speaker (its own
    // kind of flicker) and split one person's turns.
    const t = raw.replace(/\s+/g, ' ').trim();
    return t || null;
  }

  function readActiveSpeaker(): string | null {
    for (const sel of ACTIVE_CONTAINER_SELECTORS) {
      const name = nameFromContainer(document.querySelector(sel));
      if (name) {
        if (opts.selfName && name.toLowerCase() === opts.selfName.toLowerCase()) return null;
        return name;
      }
    }
    return null;
  }

  // Re-assert the current speaker every ~2s even without a change, so a consumer
  // that started mid-turn (a reconnected WS, a restarted pipeline with an empty
  // name-binder) learns who's talking WITHOUT waiting for the next speaker change.
  // Without this, the opening monologue's turns publish provisionally (seg_N) for
  // the whole first turn. Repeated same-name hints are idempotent at the binder.
  const HEARTBEAT_POLLS = Math.max(1, Math.round(2000 / pollMs));
  let sinceEmit = 0;

  // Blindness reporting: how often to say "still nothing", and whether anything was EVER seen.
  const BLIND_REPORT_POLLS = Math.max(1, Math.round((opts.blindReportMs ?? 30_000) / pollMs));
  let polls = 0;
  let sawAnySpeaker = false;

  // One poll cycle: read the lit speaker; emit on change, or periodically re-assert.
  function tick(): void {
    let name: string | null = null;
    try { name = readActiveSpeaker(); } catch { /* DOM in flux */ return; }
    if (name !== active) {
      // A change must hold for CONFIRM_POLLS consecutive reads before we commit it,
      // so a single flicker poll can't emit a hint (and can't hijack attribution).
      if (name === candidate) candidateCount++; else { candidate = name; candidateCount = 1; }
      if (candidateCount >= CONFIRM_POLLS) {
        if (active) log(`SPEAKER_END: ${active}`);
        active = candidate;
        if (active) log(`SPEAKER_START: ${active}`);
        candidateCount = 0; sinceEmit = 0;
        try { opts.onSpeakerChange?.(active); } catch { /* consumer error */ }
      }
    } else {
      candidate = active; candidateCount = 0;   // back on the committed speaker → drop any pending flicker
      if (active && ++sinceEmit >= HEARTBEAT_POLLS) {
        sinceEmit = 0;
        try { opts.onSpeakerChange?.(active); } catch { /* consumer error */ }
      }
    }
    // SAY SO WHEN BLIND. This watcher's only output is a speaker transition, so selectors that stop
    // matching produce perfect silence — no error, no warning, a clean log and a full transcript
    // whose speaker column reads seg_0, seg_4, seg_7 (#852).
    //
    // A watcher that has never once seen a speaker is in a silent room, or looking at a DOM without
    // the chrome, or looking at renamed classes. It cannot tell which — so it reports the counts
    // that separate them and leaves the verdict to whoever reads them. The first version of this
    // block did draw the verdict ("selectors are stale"), and a second browser joined to the SAME
    // meeting at the SAME moment found both selectors present: the layout was the variable, not
    // the class names, and the confident message sent the investigation the wrong way.
    polls++;
    if (name) sawAnySpeaker = true;
    if (!sawAnySpeaker && polls % BLIND_REPORT_POLLS === 0) {
      const anchors = ACTIVE_CONTAINER_SELECTORS.filter((sel) => document.querySelector(sel));
      const speakerish = document.querySelectorAll('[class*="speaker"]').length;
      const frames = document.querySelectorAll('[class*="video-frame"]').length;
      const footers = document.querySelectorAll(NAME_FOOTER_SELECTOR).length;
      log(
        `NO ACTIVE SPEAKER seen in ${Math.round((polls * pollMs) / 1000)}s — ` +
        (anchors.length
          ? `containers present (${anchors.join(', ')}) but none named; the room may be silent`
          : `none of ${ACTIVE_CONTAINER_SELECTORS.join(', ')} exist here. This DOM: ` +
            `${speakerish} [class*=speaker], ${frames} [class*=video-frame], ${footers} name footers, ` +
            `${document.querySelectorAll('iframe').length} iframes, ` +
            `viewport ${window.innerWidth}x${window.innerHeight}, visibility=${document.visibilityState}. ` +
            `All zero means this client renders no speaker chrome at all; non-zero means the class ` +
            `names moved. Attribution is empty either way.`),
      );
    }

    // Multi-channel: correlate the active speaker with the track that's audible.
    if (opts.onName) { try { voteOnce(); } catch { /* ignore */ } }
  }
  tick();                              // read the current speaker NOW, don't wait a full pollMs
  const timer = setInterval(tick, pollMs);

  const HINT_RE = /speak|talk|active|audio|volume|voice/i;

  function getState(): ZoomSpeakersState {
    const probe = ACTIVE_CONTAINER_SELECTORS.map(selector => {
      const el = document.querySelector(selector);
      return { selector, present: !!el, name: nameFromContainer(el) };
    });
    const matched = probe.find(p => p.name)?.selector || null;

    // For every name footer in the DOM, walk up a few ancestors collecting class
    // names, and flag any class (self or descendant) hinting "speaking". This
    // reveals which tile class marks the active speaker — robust across views.
    const tiles: ZoomSpeakersState['tiles'] = [];
    const footers = document.querySelectorAll(NAME_FOOTER_SELECTOR);
    for (let i = 0; i < footers.length && tiles.length < 30; i++) {
      const footer = footers[i] as HTMLElement;
      const name = (footer.querySelector('span')?.textContent?.trim() || footer.innerText?.trim() || '');
      if (!name) continue;
      const tileClasses: string[] = [];
      let cur: HTMLElement | null = footer;
      for (let up = 0; up < 5 && cur; up++) { if (cur.className) tileClasses.push(String(cur.className)); cur = cur.parentElement; }
      const speakingHints: string[] = [];
      const scope = footer.closest('[class*="video"],[class*="participant"],[class*="tile"]') || footer.parentElement || footer;
      scope.querySelectorAll('[class]').forEach(el => {
        const c = String((el as HTMLElement).className);
        if (HINT_RE.test(c)) c.split(/\s+/).forEach(tok => { if (HINT_RE.test(tok) && !speakingHints.includes(tok)) speakingHints.push(tok); });
      });
      tiles.push({ name, tileClasses, speakingHints });
    }
    // Selector self-discovery material: when the known footer selector matches
    // nothing, sweep name-ish/avatar-ish/video-adjacent nodes carrying short
    // text or aria-labels. Short strings only (names), never paragraphs.
    let survey: ZoomSpeakersState['survey'];
    if (tiles.length === 0) {
      survey = [];
      const seen = new Set<string>();
      const sweep = document.querySelectorAll(
        '[class*="name"],[class*="avatar"],[class*="footer"],[class*="participant"],[class*="tile"],[aria-label]');
      for (let i = 0; i < sweep.length && survey.length < 25; i++) {
        const el = sweep[i] as HTMLElement;
        const aria = (el.getAttribute('aria-label') || '').slice(0, 60);
        // Own text only (not descendants') so we find the leaf carrying the name.
        let own = '';
        for (const n of Array.from(el.childNodes)) if (n.nodeType === 3) own += (n.textContent || '');
        own = own.trim();
        const text = own && own.length <= 40 ? own : '';
        if (!text && !aria) continue;
        const key = `${el.className}|${text}|${aria}`;
        if (seen.has(key)) continue;
        seen.add(key);
        survey.push({ cls: String(el.className).slice(0, 120), aria, text });
      }
    }
    return { active, matchedSelector: matched, probe, tiles, nameFooterSelector: NAME_FOOTER_SELECTOR, survey };
  }

  return {
    getActiveSpeaker(): string | null { return active; },
    reportTrackAudio(index: number): void { lastTrackAudio.set(index, now()); },
    getTrackName(index: number): string | null { return lockedTrack.get(index) ?? null; },
    getState,
    destroy(): void { clearInterval(timer); },
  };
}
