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
 * binds a name to an audio track/segment. Naming happens DOWNSTREAM: the mixed
 * remote channel (999) is diarized into clusters, and the ClusterNameBinder
 * resolves those clusters to these `dom-active` hints (cluster-vote, hysteresis,
 * live re-resolve). gmeet now follows the SAME mixed path as zoom/teams.
 *
 * Self-healing speaking detection:
 *  Meet's speaking-indicator CSS classes are obfuscated and rot with UI pushes
 *  (the historic failure mode: nothing ever reads as "speaking"). This module
 *  watches class mutations across tiles; if the known classes go silent for a
 *  while, the most-recently-added mutating class is adopted as a speaking
 *  indicator (logged loudly, capped). getState() exposes the full forensic dump.
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
  /** Adopt a learned indicator class only after known classes have been silent this long. Default 10s. */
  learnAfterSilentMs?: number;
  /** Mutation count required to adopt a class as a speaking indicator. Default 3. */
  learnMinScore?: number;
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
    learnedClasses: string[];
    /** class → mutation count (learning evidence) */
    candidateScores: Record<string, number>;
    lastKnownHitMs: number;
  };
}

export interface GmeetSpeakers {
  getState(): GmeetSpeakersState;
  destroy(): void;
}

const PARTICIPANT_SELECTORS = ['div[data-participant-id]', '[data-participant-id]'];
const KNOWN_SPEAKING_CLASSES = [
  'Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli',
  'speaking', 'active-speaker', 'speaker-active', 'speaking-indicator',
];
const JUNK_NAME = /^Google Participant \(|spaces\/|devices\//;
const JUNK_PHRASES = ['let participants', 'send messages', 'turn on captions'];
const MAX_LEARNED = 3;
/** Classes that mutate constantly for non-speaking reasons; never learn these. */
const LEARN_BLOCKLIST = /hover|focus|active-tab|tooltip/i;

export function createGmeetSpeakers(opts: GmeetSpeakersOptions = {}): GmeetSpeakers {
  const log = opts.log || ((m: string) => console.log(m));
  const pollMs = opts.pollMs ?? 500;
  const learnAfterSilentMs = opts.learnAfterSilentMs ?? 10_000;
  const learnMinScore = opts.learnMinScore ?? 3;

  // Self-healing state
  const knownClassHits: Record<string, number> = {};
  const learnedClasses: string[] = [];
  const candidateScores = new Map<string, number>();
  let lastKnownHitMs = Date.now();
  /** Recent class additions: class → last-added timestamp (rolling). */
  const recentClassAdds = new Map<string, number>();

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

  function isSelf(el: HTMLElement, name: string | null): boolean {
    if (el.hasAttribute('data-self-name') || el.querySelector('[data-self-name]')) {
      const selfAttr = (el.getAttribute('data-self-name')
        || (el.querySelector('[data-self-name]') as HTMLElement | null)?.getAttribute('data-self-name') || '').trim();
      if (selfAttr && name && (selfAttr === name)) return true;
      if (selfAttr && !name) return true;
    }
    if (opts.selfName && name) {
      const a = name.toLowerCase(), b = opts.selfName.toLowerCase();
      if (a.includes(b) || b.includes(a)) return true;
    }
    return false;
  }

  function activeSpeakingClasses(): string[] {
    return [...KNOWN_SPEAKING_CLASSES, ...learnedClasses];
  }

  function tileSpeaking(el: HTMLElement): boolean {
    for (const cls of activeSpeakingClasses()) {
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
    for (const sel of PARTICIPANT_SELECTORS) {
      document.querySelectorAll(sel).forEach(node => {
        const el = node as HTMLElement;
        const id = el.getAttribute('data-participant-id') || '';
        if (!id || seen.has(id)) return;
        seen.add(id);
        const name = tileName(el);
        out.push({ id, name, self: isSelf(el, name), speaking: tileSpeaking(el) });
      });
    }
    return out;
  }

  // ── Self-healing: learn speaking classes from mutation activity ──

  const observer = new MutationObserver(muts => {
    const now = Date.now();
    for (const m of muts) {
      if (m.type !== 'attributes' || m.attributeName !== 'class') continue;
      const el = m.target as HTMLElement;
      const old = new Set(String(m.oldValue || '').split(/\s+/).filter(Boolean));
      el.classList.forEach(c => {
        if (!old.has(c) && !LEARN_BLOCKLIST.test(c) && c.length <= 24) {
          recentClassAdds.set(c, now);
          if (!KNOWN_SPEAKING_CLASSES.includes(c) && !learnedClasses.includes(c)) {
            candidateScores.set(c, (candidateScores.get(c) || 0) + 1);
          }
        }
      });
    }
    if (recentClassAdds.size > 200) {
      for (const [c, t] of recentClassAdds) if (now - t > 5000) recentClassAdds.delete(c);
    }
  });
  try {
    observer.observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true, subtree: true });
  } catch { /* body not ready; poll loop still works with known classes */ }

  function maybeLearn(now: number): void {
    if (now - lastKnownHitMs < learnAfterSilentMs) return;       // known classes still work
    if (learnedClasses.length >= MAX_LEARNED) return;
    let best: [string, number] | null = null;
    for (const [cls, score] of candidateScores) {
      if (score >= learnMinScore && (!best || score > best[1])) best = [cls, score];
    }
    if (best) {
      learnedClasses.push(best[0]);
      candidateScores.delete(best[0]);
      log(`[GmeetSpeakers] ⚠ known speaking classes silent ${((now - lastKnownHitMs) / 1000).toFixed(0)}s — LEARNED indicator class "${best[0]}" (×${best[1]})`);
    }
  }

  // ── Main loop: emit start/stop HINTS on edge changes ─────────────

  const timer = setInterval(() => {
    const now = Date.now();
    const tiles = scanTiles();

    maybeLearn(now);

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
        selectorStats: {
          knownClassHits: { ...knownClassHits },
          learnedClasses: [...learnedClasses],
          candidateScores: Object.fromEntries(candidateScores),
          lastKnownHitMs,
        },
      };
    },
    destroy(): void {
      clearInterval(timer);
      try { observer.disconnect(); } catch { /* already gone */ }
    },
  };
}
