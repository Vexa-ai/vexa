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
  destroy(): void;
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

  return {
    getActiveSpeaker(): string | null { return active; },
    destroy(): void { clearInterval(timer); },
  };
}
