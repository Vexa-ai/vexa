/**
 * Google Meet speaker detection — THE shared HINT emitter.
 *
 * Pure browser code (no Node, no Playwright imports). Consumed by BOTH:
 *  - the bot: bundled into browser-utils.global.js, instantiated in-page by
 *    googlemeet/recording.ts; its onSpeaking hints feed recordMixedHint().
 *  - the extension: imported directly by vexa-extension/src/inpage.ts; its
 *    onSpeaking hints become `speaker_activity` (dom-active) WS messages.
 *
 * SoC: this module extracts RAW SIGNALS only — it reads who Meet is visibly
 * rendering as speaking, emits debounced start/stop HINTS per name, and exposes
 * litNames() (who is lit now). It NEVER itself attaches a name to audio. Two
 * consumers use the signal:
 *  - gmeet (capture.v1): gmeet-capture-v1 stamps the lit name onto each audio
 *    chunk AT THE SOURCE — binding identity to audio, not to a segment.
 *  - mixed (zoom/teams): the downstream ClusterNameBinder resolves clusters to
 *    these `dom-active` hints (cluster-vote, hysteresis), cross-validated vs audio.
 *
 * Speaking detection — NO auto-learn:
 *  The previous self-healing "learn a CSS class after the known ones go silent
 *  10s" heuristic was REMOVED. It mislearned a busy non-speaking class and stuck
 *  every channel to one name (the all-one-speaker collapse). Obfuscated class
 *  matching is a known-bad foundation we're replacing. For now detection uses the
 *  known classes ONLY (no learning ⇒ worst case a tile reads as not-speaking and
 *  stays provisional `ch-N`, never *wrongly* named). `probeDom()` dumps the live
 *  DOM structure so the robust, non-obfuscated signal can be designed from real
 *  data (audio-element ↔ participant-id linkage, aria/role speaking markers).
 */

export interface GmeetSpeakersOptions {
  /** Local participant's display name (bot name / data-self-name). Excluded from candidates. */
  selfName?: string;
  /** Debounced speaking state change for a NON-self named tile.
   *  isEnd=false → started speaking; isEnd=true → stopped. */
  onSpeaking?: (name: string, isEnd: boolean) => void;
  /** The LOCAL participant's display name, reported once it is observed on a self tile
   *  (the data-self-name marker can render late). Lets the channel binder pin a sticky
   *  self name it refuses to bind to any remote channel — the leak-proof backstop. */
  onSelf?: (name: string) => void;
  /** Log sink (defaults to console.log). */
  log?: (msg: string) => void;
  /** Poll interval (ms). Default 500. */
  pollMs?: number;
}

export interface GmeetTileInfo {
  id: string;
  name: string | null;
  self: boolean;
  speaking: boolean;
}

export interface GmeetSpeakersState {
  tiles: GmeetTileInfo[];
  speakingNow: string[];
  participantCount: number;
  selectorStats: {
    knownClassHits: Record<string, number>;
    lastKnownHitMs: number;
  };
}

export interface GmeetSpeakers {
  getState(): GmeetSpeakersState;
  /** Names lit RIGHT NOW (non-self, named), from the poll-maintained set — O(1),
   *  no DOM re-scan. The capture.v1 binder reads this per audio chunk to stamp the
   *  glow name at the chunk's capture time. Still RAW signal extraction: it reports
   *  who Meet renders as speaking; it does not itself attach a name to audio. */
  litNames(): string[];
  /** One-shot structural dump of the live Meet DOM — for designing a robust,
   *  non-obfuscated speaking/naming signal. Read-only; no side effects. */
  probeDom(): GmeetDomProbe;
  destroy(): void;
}

/** What probeDom() returns: enough of the live structure to decide whether
 *  channel↔name can be STRUCTURAL (audio element carries a participant id) or
 *  needs a SEMANTIC (aria/role) speaking signal instead of obfuscated classes. */
export interface GmeetDomProbe {
  audioCount: number;
  /** Per media element carrying audio: the co-location test — does it sit inside a
   *  tile that carries a participant id + name, and is that tile currently glowing
   *  (speaking)? If participantId/tileName are populated AND `speaking` tracks who
   *  is actually talking, audio→name is a DIRECT structural read (no timing). */
  audio: { tag: string; hasAudioTrack: boolean; trackId: string | null; participantId: string | null; tileName: string | null; speaking: boolean }[];
  tileCount: number;
  tiles: { id: string; name: string | null; speaking: boolean; aria: string | null }[];
  /** Names the DOM currently shows as speaking (glow), for cross-check. */
  speakingNow: string[];
}

const PARTICIPANT_SELECTORS = ['div[data-participant-id]', '[data-participant-id]'];
const KNOWN_SPEAKING_CLASSES = [
  'Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli',
  'speaking', 'active-speaker', 'speaker-active', 'speaking-indicator',
];
const JUNK_NAME = /^Google Participant \(|spaces\/|devices\//;
const JUNK_PHRASES = ['let participants', 'send messages', 'turn on captions'];

export function createGmeetSpeakers(opts: GmeetSpeakersOptions = {}): GmeetSpeakers {
  const pollMs = opts.pollMs ?? 250;  // responsive: track the visible active-speaker glow closely

  const knownClassHits: Record<string, number> = {};
  let lastKnownHitMs = Date.now();

  /** Names currently lit (non-self, named) — drives start/stop hint edges. */
  const speakingNow = new Set<string>();
  const reportedSelf = new Set<string>();   // self names already reported via onSelf (fire once each)

  // ── DOM reading ─────────────────────────────────────────────────

  function tileName(el: HTMLElement): string | null {
    const nt = el.querySelector('span.notranslate') as HTMLElement | null;
    let t = nt?.textContent?.trim() || '';
    if (!t) {
      const labeled = el.querySelector('[data-self-name]') as HTMLElement | null;
      t = labeled?.getAttribute('data-self-name')?.trim() || '';
    }
    if (!t || t.length < 2 || t.length > 50) return null;
    if (JUNK_NAME.test(t)) return null;
    const lower = t.toLowerCase();
    if (JUNK_PHRASES.some(p => lower.includes(p))) return null;
    return t;
  }

  // The local participant's tile, located via Meet's OWN structural marker
  // (data-self-name). Re-read every scan — the self tile can render late or move,
  // and the marker may sit on a child or sibling of the participant tile.
  function selfParticipantId(): string | null {
    const marker = document.querySelector('[data-self-name]');
    const tile = marker?.closest('[data-participant-id]') as HTMLElement | null;
    return tile?.getAttribute('data-participant-id') || null;
  }

  // Self/host detection is PURELY STRUCTURAL — Meet's data-self-name marker, never
  // name/aria text matching. The host tile is excluded so it can never emit a hint.
  function isSelf(el: HTMLElement, id: string, selfId: string | null): boolean {
    return el.hasAttribute('data-self-name')
      || !!el.querySelector('[data-self-name]')
      || (selfId !== null && id === selfId);
  }

  function tileSpeaking(el: HTMLElement): boolean {
    for (const cls of KNOWN_SPEAKING_CLASSES) {
      if (el.classList.contains(cls) || el.querySelector('.' + CSS.escape(cls))) {
        knownClassHits[cls] = (knownClassHits[cls] || 0) + 1;
        lastKnownHitMs = Date.now();
        return true;
      }
    }
    return false;
  }

  function scanTiles(): GmeetTileInfo[] {
    const out: GmeetTileInfo[] = [];
    const seen = new Set<string>();
    const selfId = selfParticipantId();
    for (const sel of PARTICIPANT_SELECTORS) {
      document.querySelectorAll(sel).forEach(node => {
        const el = node as HTMLElement;
        const id = el.getAttribute('data-participant-id') || '';
        if (!id || seen.has(id)) return;
        seen.add(id);
        const name = tileName(el);
        out.push({ id, name, self: isSelf(el, id, selfId), speaking: tileSpeaking(el) });
      });
    }
    return out;
  }

  // ── Main loop: emit start/stop HINTS on edge changes ─────────────

  const timer = setInterval(() => {
    const tiles = scanTiles();

    // Report the self/host name (once) so the binder can pin it as never-bindable —
    // the data-self-name marker can render late, so we watch every scan, not just start.
    for (const t of tiles) {
      if (t.self && t.name && !reportedSelf.has(t.name)) {
        reportedSelf.add(t.name);
        try { opts.onSelf?.(t.name); } catch { /* consumer error */ }
      }
    }

    // Currently-lit, non-self, named tiles.
    const litNow = new Set<string>(
      tiles.filter(t => !t.self && t.speaking && t.name).map(t => t.name as string),
    );

    // Newly lit → SPEAKER_START hint.
    for (const name of litNow) {
      if (!speakingNow.has(name)) {
        speakingNow.add(name);
        try { opts.onSpeaking?.(name, false); } catch { /* consumer error */ }
      }
    }
    // Went quiet → SPEAKER_END hint.
    for (const name of [...speakingNow]) {
      if (!litNow.has(name)) {
        speakingNow.delete(name);
        try { opts.onSpeaking?.(name, true); } catch { /* consumer error */ }
      }
    }
  }, pollMs);

  return {
    getState(): GmeetSpeakersState {
      const tiles = scanTiles();
      return {
        tiles,
        speakingNow: tiles.filter(t => !t.self && t.speaking && t.name).map(t => t.name as string),
        participantCount: tiles.length,
        selectorStats: { knownClassHits: { ...knownClassHits }, lastKnownHitMs },
      };
    },
    litNames(): string[] { return [...speakingNow]; },
    probeDom(): GmeetDomProbe {
      // The captured elements are <audio> AND <video> with audio tracks (same set
      // gmeet-capture taps). For each, run the co-location test against its tile.
      const media = (Array.from(document.querySelectorAll('audio, video')) as HTMLMediaElement[])
        .filter(el => el.srcObject instanceof MediaStream && (el.srcObject as MediaStream).getAudioTracks().length > 0);
      const tiles = Array.from(document.querySelectorAll('[data-participant-id]')) as HTMLElement[];
      return {
        audioCount: media.length,
        audio: media.slice(0, 16).map(a => {
          let track: string | null = null;
          try { track = (a.srcObject as MediaStream | null)?.getAudioTracks?.()[0]?.id?.slice(0, 12) || null; } catch { /* */ }
          const tile = (a.getAttribute('data-participant-id') ? a : a.closest('[data-participant-id]')) as HTMLElement | null;
          return {
            tag: a.tagName.toLowerCase(),
            hasAudioTrack: true,
            trackId: track,
            participantId: (tile?.getAttribute('data-participant-id') || a.parentElement?.getAttribute('data-participant-id') || null)?.slice(0, 16) || null,
            tileName: tile ? tileName(tile) : null,
            speaking: tile ? tileSpeaking(tile) : false,
          };
        }),
        tileCount: tiles.length,
        tiles: tiles.slice(0, 16).map(t => ({
          id: (t.getAttribute('data-participant-id') || '').slice(0, 16),
          name: tileName(t),
          speaking: tileSpeaking(t),
          aria: t.getAttribute('aria-label') || null,
        })),
        speakingNow: tiles.filter(t => !t.hasAttribute('data-self-name') && tileSpeaking(t) && tileName(t)).map(t => tileName(t) as string),
      };
    },
    destroy(): void {
      clearInterval(timer);
    },
  };
}
