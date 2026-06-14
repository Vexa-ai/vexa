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
 * rendering as speaking and emits debounced start/stop HINTS per name. It NEVER
 * binds a name to an audio track/segment. Naming happens DOWNSTREAM: the
 * ClusterNameBinder resolves channels/clusters to these `dom-active` hints
 * (cluster-vote, hysteresis, live re-resolve), cross-validated against audio.
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
  /** Per audio element: does it (or an ancestor) carry a participant id? */
  audio: { hasStream: boolean; trackId: string | null; participantId: string | null }[];
  tileCount: number;
  tiles: { id: string; name: string | null; aria: string | null; ariaInside: string[] }[];
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
    probeDom(): GmeetDomProbe {
      const audios = Array.from(document.querySelectorAll('audio')) as HTMLAudioElement[];
      const tiles = Array.from(document.querySelectorAll('[data-participant-id]')) as HTMLElement[];
      return {
        audioCount: audios.length,
        audio: audios.slice(0, 8).map(a => {
          let track: string | null = null;
          try { track = (a.srcObject as MediaStream | null)?.getAudioTracks?.()[0]?.id?.slice(0, 12) || null; } catch { /* */ }
          const pid = a.getAttribute('data-participant-id')
            || a.closest('[data-participant-id]')?.getAttribute('data-participant-id')
            || a.parentElement?.getAttribute('data-participant-id') || null;
          return { hasStream: !!a.srcObject, trackId: track, participantId: pid };
        }),
        tileCount: tiles.length,
        tiles: tiles.slice(0, 8).map(t => ({
          id: (t.getAttribute('data-participant-id') || '').slice(0, 16),
          name: tileName(t),
          aria: t.getAttribute('aria-label') || null,
          ariaInside: (Array.from(t.querySelectorAll('[aria-label],[aria-pressed],[aria-live],[role]')) as HTMLElement[])
            .slice(0, 5)
            .map(e => e.getAttribute('aria-label') || e.getAttribute('aria-pressed') || e.getAttribute('aria-live') || e.getAttribute('role') || '')
            .filter(Boolean),
        })),
      };
    },
    destroy(): void {
      clearInterval(timer);
    },
  };
}
