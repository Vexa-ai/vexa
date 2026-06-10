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
  /** Fired when the active speaker changes (name or null when nobody is active). */
  onSpeakerChange?: (name: string | null) => void;
  log?: (msg: string) => void;
  /** Poll interval (ms). Default 250 — matches the bot. */
  pollMs?: number;
}

export interface ZoomSpeakers {
  /** Current active speaker name, or null. */
  getActiveSpeaker(): string | null;
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
  let active: string | null = null;

  function nameFromContainer(container: Element | null): string | null {
    if (!container) return null;
    const footer = container.querySelector(NAME_FOOTER_SELECTOR);
    if (!footer) return null;
    const span = footer.querySelector('span');
    const t = (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || '';
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

  const timer = setInterval(() => {
    let name: string | null = null;
    try { name = readActiveSpeaker(); } catch { /* DOM in flux */ return; }
    if (name !== active) {
      if (active) log(`SPEAKER_END: ${active}`);
      active = name;
      if (name) log(`SPEAKER_START: ${name}`);
      try { opts.onSpeakerChange?.(active); } catch { /* consumer error */ }
    }
  }, pollMs);

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
    return { active, matchedSelector: matched, probe, tiles, nameFooterSelector: NAME_FOOTER_SELECTOR };
  }

  return {
    getActiveSpeaker(): string | null { return active; },
    getState,
    destroy(): void { clearInterval(timer); },
  };
}
